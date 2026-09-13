import json
import subprocess
from pathlib import Path

import pytest
from test_post import make_clip, probe

from video import gen, post
from video.story import pipeline, shots2


def ledger_with_clips(out_dir: Path, seconds: int = 2, volumes: dict[str, float] | None = None) -> None:
    ledger = gen.Ledger(out_dir / "ledger.jsonl")
    volumes = volumes or {}
    for index, shot in enumerate(shots2.SHOTS, start=1):
        job = shots2.job_id(index)
        clip = make_clip(out_dir / f"{job}.mp4", seconds=seconds, volume=volumes.get(shot.key, 0.5))
        ledger.append(job, "done", path=str(clip), spent=shots2.PRICE)


@pytest.mark.slow
def test_build2_evens_the_audio_dissolves_the_joins_and_sheets_every_clip(tmp_path):
    # The clips come out of Veo at different loudness and the old hard cut jumped; both are fixed here.
    ledger_with_clips(tmp_path, volumes={"hook": 0.9, "fabric": 0.05})
    result = pipeline.build2(out_dir=tmp_path)

    final = Path(result["final"])
    info = probe(final)
    assert abs(float(info["format"]["duration"]) - result["expected_seconds"]) < 0.15, info
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (720, 1280)

    levels = [row["lufs"] for row in result["clips"]]
    assert all(-17.5 <= level <= -14.5 for level in levels), levels
    assert max(levels) - min(levels) <= 1.5, levels

    assert len(result["strips"]) == len(shots2.SHOTS)
    assert all(Path(p).is_file() for p in result["strips"])
    assert Path(result["contact_sheet"]).is_file()
    assert json.loads((tmp_path / "cut.json").read_text())["final"] == str(final)


@pytest.mark.slow
def test_build2_applies_a_trim_so_a_broken_hand_costs_no_credits(tmp_path):
    ledger_with_clips(tmp_path, seconds=3)
    (tmp_path / "trims.json").write_text(json.dumps({"pose": [0.0, 1.5]}))
    result = pipeline.build2(out_dir=tmp_path)

    pose = next(row for row in result["clips"] if row["key"] == "pose")
    assert abs(pose["seconds"] - 1.5) < 0.2, pose
    assert pose["trim"] == [0.0, 1.5]
    expected = post.xfade_total([row["seconds"] for row in result["clips"]], post.FADE)
    assert abs(result["expected_seconds"] - expected) < 0.01
    assert abs(post.duration(Path(result["final"])) - expected) < 0.15


def test_pick_writes_down_which_take_won_and_why_the_other_was_dropped(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    for job in ("tryon2-02", "tryon2-02b"):
        ledger.append(job, "done", path=str(tmp_path / f"{job}.mp4"), spent=10)

    chosen = pipeline.select(tmp_path, "wardrobe", "tryon2-02b", "take a lost the left hand on the rail")
    assert chosen["take"] == "tryon2-02b"
    assert chosen["rejected"] == ["tryon2-02"]
    rows = [r for r in ledger.rows("tryon2-02") if r.get("status") == "selected"]
    assert rows[-1]["reason"].startswith("take a lost")

    with pytest.raises(ValueError, match="reason"):
        pipeline.select(tmp_path, "wardrobe", "tryon2-02", "")
    with pytest.raises(ValueError, match="tryon2-99"):
        pipeline.select(tmp_path, "wardrobe", "tryon2-99", "nope")


def test_a_shot_with_two_takes_and_no_pick_stops_the_cut(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    for index, shot in enumerate(shots2.SHOTS, start=1):
        ledger.append(shots2.job_id(index), "done", path=str(tmp_path / "x.mp4"), spent=10)
    ledger.append("tryon2-02b", "done", path=str(tmp_path / "b.mp4"), spent=10)

    with pytest.raises(RuntimeError, match="wardrobe"):
        pipeline.clip_paths(tmp_path)

    pipeline.select(tmp_path, "wardrobe", "tryon2-02b", "cleaner hand")
    assert dict(pipeline.clip_paths(tmp_path))["wardrobe"] == Path(tmp_path / "b.mp4")


def test_picking_a_take_the_next_shot_did_not_continue_from_is_reported(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    for index, shot in enumerate(shots2.SHOTS, start=1):
        ledger.append(shots2.job_id(index), "done", path=str(tmp_path / "x.mp4"), spent=10)
    ledger.append("tryon2-05b", "done", path=str(tmp_path / "b.mp4"), spent=10)

    # closing started from pose's last frame, so swapping pose's take orphans it.
    chosen = pipeline.select(tmp_path, "pose", "tryon2-05b", "take a fused two fingers")
    assert chosen["needs_regen"] == ["closing"]
    assert pipeline.inconsistent_chain(tmp_path) == ["closing"]

    pipeline.select(tmp_path, "pose", "tryon2-05", "take b drifted off the mirror")
    assert pipeline.inconsistent_chain(tmp_path) == []


def test_the_edited_reveal_is_what_goes_into_the_cut_not_the_clip_before_the_edit(tmp_path):
    # The Omni edit is the shot that has the garment on her; the ledger row still names the base clip,
    # so both the cut and the shots that continue from it must take the edited file.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    for index, shot in enumerate(shots2.SHOTS, start=1):
        for job in pipeline.take_ids(shots2.job_id(index), shot.hands_risk):
            ledger.append(job, "done", path=str(tmp_path / f"{job}.mp4"), spent=10)
    pipeline.select(tmp_path, "wardrobe", "tryon2-02", "cleaner hand")
    pipeline.select(tmp_path, "pose", "tryon2-05", "cleaner hand")

    paths = dict(pipeline.clip_paths(tmp_path))
    assert paths["reveal"] == tmp_path / "tryon2-04.mp4", "no edited file yet, so the base clip stands"

    worn = tmp_path / "tryon2-04_worn.mp4"
    worn.write_bytes(b"mp4")
    assert dict(pipeline.clip_paths(tmp_path))["reveal"] == worn


def test_an_extended_shot_is_found_by_the_file_named_after_its_job(tmp_path):
    # The editor writes its own ledger row: outputs, no top-level path. The clip that counts is the 720p
    # one the pipeline re-downloaded next to it.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    for index, shot in enumerate(shots2.SHOTS, start=1):
        for job in pipeline.take_ids(shots2.job_id(index), shot.hands_risk):
            clip = tmp_path / f"{job}.mp4"
            clip.write_bytes(b"mp4")
            if shot.mode == "extend":
                ledger.append(job, "done", outputs=[{"media_id": "x", "path": "/tmp/editor-copy.mp4"}])
            else:
                ledger.append(job, "done", path=str(clip), spent=10)
    pipeline.select(tmp_path, "wardrobe", "tryon2-02", "cleaner hand")
    pipeline.select(tmp_path, "pose", "tryon2-05", "cleaner hand")

    paths = dict(pipeline.clip_paths(tmp_path))
    assert paths["pose"] == tmp_path / "tryon2-05.mp4"
    assert paths["closing"] == tmp_path / "tryon2-06.mp4"


def test_build2_refuses_to_stitch_a_chain_with_a_missing_shot(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append(shots2.job_id(1), "done", path=str(tmp_path / "tryon2-01.mp4"))
    with pytest.raises(RuntimeError, match="wardrobe"):
        pipeline.build2(out_dir=tmp_path)


def test_the_pick_command_writes_the_choice_and_refuses_a_silent_one(tmp_path):
    from click.testing import CliRunner

    from video import cli

    gen.Ledger(tmp_path / "ledger.jsonl").append("tryon2-02b", "done", path=str(tmp_path / "b.mp4"))
    runner = CliRunner()
    args = ["story", "pick", "--out", str(tmp_path), "--key", "wardrobe", "--take", "tryon2-02b"]

    bad = runner.invoke(cli.main, [*args, "--reason", "  "])
    assert bad.exit_code != 0 and "reason" in bad.output

    good = runner.invoke(cli.main, [*args, "--reason", "take a lost a finger on the rail"])
    assert good.exit_code == 0, good.output
    assert json.loads(good.output)["take"] == "tryon2-02b"
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows("tryon2-02")
    assert rows[-1]["status"] == "selected" and rows[-1]["reason"].startswith("take a lost")


@pytest.mark.slow
def test_build2_applies_a_colour_grade_and_records_it(tmp_path):
    ledger_with_clips(tmp_path)
    (tmp_path / "grade.json").write_text(json.dumps({"fabric": [1.3, 1.0, 0.8]}))
    before = post.mean_rgb(tmp_path / "tryon2-03.mp4", at=1.0)

    result = pipeline.build2(out_dir=tmp_path)

    fabric = next(row for row in result["clips"] if row["key"] == "fabric")
    assert fabric["gains"] == [1.3, 1.0, 0.8]
    after = post.mean_rgb(Path(fabric["path"]), at=1.0)
    assert after[0] > before[0] and after[2] < before[2], (before, after)
    # Nothing else is touched.
    assert all(row["gains"] is None for row in result["clips"] if row["key"] != "fabric")


def test_load_grades_reads_a_missing_file_as_no_grade(tmp_path):
    assert pipeline.load_grades(tmp_path) == {}
    (tmp_path / "grade.json").write_text(json.dumps({"fabric": [1.14, 1.2, 1.09]}))
    assert pipeline.load_grades(tmp_path) == {"fabric": (1.14, 1.2, 1.09)}


def test_load_trims_reads_a_missing_file_as_no_trims(tmp_path):
    assert pipeline.load_trims(tmp_path) == {}
    (tmp_path / "trims.json").write_text(json.dumps({"pose": [1, 2.5]}))
    assert pipeline.load_trims(tmp_path) == {"pose": (1.0, 2.5)}


def test_probe_helper_reads_a_real_file(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", seconds=1)
    assert subprocess.run(["ffprobe", "-v", "error", str(clip)], check=False).returncode == 0
