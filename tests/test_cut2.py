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


def test_build2_refuses_to_stitch_a_chain_with_a_missing_shot(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append(shots2.job_id(1), "done", path=str(tmp_path / "tryon2-01.mp4"))
    with pytest.raises(RuntimeError, match="wardrobe"):
        pipeline.build2(out_dir=tmp_path)


def test_load_trims_reads_a_missing_file_as_no_trims(tmp_path):
    assert pipeline.load_trims(tmp_path) == {}
    (tmp_path / "trims.json").write_text(json.dumps({"pose": [1, 2.5]}))
    assert pipeline.load_trims(tmp_path) == {"pose": (1.0, 2.5)}


def test_probe_helper_reads_a_real_file(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", seconds=1)
    assert subprocess.run(["ffprobe", "-v", "error", str(clip)], check=False).returncode == 0
