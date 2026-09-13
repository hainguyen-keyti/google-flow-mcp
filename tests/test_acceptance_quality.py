import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "acceptance" / "story_quality.py"


def load():
    spec = importlib.util.spec_from_file_location("story_quality", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_review_gate_fails_loudly_when_no_human_has_looked(tmp_path):
    quality = load()
    status, detail = quality.review_gate(tmp_path / "review.json")
    assert status == "FAIL"
    assert quality.REVIEW_NOTE in detail

    (tmp_path / "review.json").write_text(json.dumps({"verdict": "fail", "notes": "hand is broken"}))
    status, detail = quality.review_gate(tmp_path / "review.json")
    assert status == "FAIL"
    assert "hand is broken" in detail or "fail" in detail

    (tmp_path / "review.json").write_text(json.dumps({"verdict": "pass", "reviewer": "owner"}))
    status, detail = quality.review_gate(tmp_path / "review.json")
    assert status == "PASS"
    assert "owner" in detail


def test_a_hands_risk_shot_without_its_rejected_take_written_down_fails(tmp_path):
    quality = load()
    rows = [
        {"job_id": "tryon2-02", "status": "done", "spent": 10},
        {"job_id": "tryon2-05", "status": "done", "spent": 10},
    ]
    status, detail = quality.takes_check(rows)
    assert status == "FAIL"
    assert "wardrobe" in detail and "pose" in detail

    rows += [
        {"job_id": "tryon2-02b", "status": "done", "spent": 10},
        {"job_id": "tryon2-05b", "status": "done", "spent": 10},
    ]
    status, detail = quality.takes_check(rows)
    assert status == "FAIL", "two takes and no written choice is still a fail"

    rows += [
        {"job_id": "tryon2-02", "status": "selected", "take": "tryon2-02", "reason": ""},
        {"job_id": "tryon2-05", "status": "selected", "take": "tryon2-05b", "reason": "fused fingers"},
    ]
    status, detail = quality.takes_check(rows)
    assert status == "FAIL" and "wardrobe" in detail, "a pick with no reason does not close I9"

    rows.append({"job_id": "tryon2-02", "status": "selected", "take": "tryon2-02", "reason": "cleaner hand"})
    status, detail = quality.takes_check(rows)
    assert status == "PASS", detail


def test_the_ledger_row_counts_a_reused_clip_at_the_price_it_really_cost():
    quality = load()
    rows = [{"job_id": "old-probe", "status": "done", "spent": 10}]
    for job in ("tryon2-01", "tryon2-02", "tryon2-02b", "tryon2-04", "tryon2-05", "tryon2-05b", "tryon2-06"):
        rows.append({"job_id": job, "status": "done", "spent": 10})
    rows.append({"job_id": "tryon2-04-edit", "status": "done", "spent": 20})
    # pose and closing are edits themselves, so their own rows carry the 20.
    for job in ("tryon2-05", "tryon2-05b", "tryon2-06"):
        for row in rows:
            if row["job_id"] == job:
                row["spent"] = 20
    rows.append({"job_id": "tryon2-03", "status": "done", "spent": 0, "reused_from": "old-probe"})

    status, detail = quality.ledger_check(rows)
    assert status == "PASS", detail
    assert "130" in detail

    status, _ = quality.ledger_check([*rows, {"job_id": "tryon2-06", "status": "failed", "spent": 10}])
    assert status == "FAIL", "a failure that moved money is never acceptable"


def test_every_clip_in_the_cut_must_trace_back_to_a_ledger_row():
    # The gate used to name the take the ledger settled on while the cut quietly used another file.
    # Passing a row that names the wrong file is how an acceptance goes green on something nobody checked.
    quality = load()
    ledger = [
        {"job_id": "tryon2-05", "status": "done", "path": "out/x/tryon2-05.mp4"},
        {"job_id": "pose-reroll-1", "status": "done", "outputs": [{"path": "out/x/reroll.mp4"}]},
    ]
    clips = [{"key": "pose", "source": "out/x/tryon2-05.mp4"}]
    status, detail = quality.provenance_check(clips, ledger)
    assert status == "PASS" and "tryon2-05" in detail

    # An override is legitimate, but the row has to SAY the cut is using a different file.
    swapped = [{"key": "pose", "source": "out/x/reroll.mp4"}]
    status, detail = quality.provenance_check(swapped, ledger)
    assert status == "PASS"
    assert "pose-reroll-1" in detail and "pose" in detail

    # A file nobody paid for has no business in the cut.
    orphan = [{"key": "pose", "source": "out/x/mystery.mp4"}]
    status, detail = quality.provenance_check(orphan, ledger)
    assert status == "FAIL"
    assert "mystery.mp4" in detail

    assert quality.provenance_check([], ledger)[0] == "FAIL"


def test_audio_is_judged_on_both_the_level_and_the_spread():
    quality = load()
    assert quality.audio_check([-16.8, -15.6, -16.0])[0] == "PASS"
    assert quality.audio_check([-14.5, -17.3])[0] == "FAIL", "2.8 dB apart is what the owner heard jump"
    assert quality.audio_check([-20.1, -19.8])[0] == "FAIL", "even and quiet is still wrong"
    assert quality.audio_check([])[0] == "FAIL"
    assert quality.audio_check([None, -16.0])[0] == "FAIL"


def test_the_final_duration_is_judged_against_the_join_lengths_actually_used():
    quality = load()
    assert quality.duration_check([8.01, 8.01], 15.54, 0.5)[0] == "PASS"
    assert quality.duration_check([8.01, 8.01], 16.02, 0.5)[0] == "FAIL", "that is a hard concat"
    # Mixed joins: one cut of a frame and one half-second dissolve.
    assert quality.duration_check([8.0, 8.0, 8.0], 23.458, [0.042, 0.5])[0] == "PASS"
    assert quality.duration_check([8.0, 8.0, 8.0], 23.0, [0.042, 0.5])[0] == "FAIL"
    assert quality.duration_check([], 0.0, 0.5)[0] == "FAIL"


def test_every_shot_is_checked_against_its_own_declared_length():
    quality = load()
    declared = {"hook": 8, "fabric": 6}
    good = [
        {"key": "hook", "seconds": 8.01, "size": (720, 1280)},
        {"key": "fabric", "seconds": 6.02, "size": (720, 1280)},
    ]
    assert quality.shots_check(good, declared)[0] == "PASS"
    # The same 8s clip is right for hook and wrong for fabric: one shared number would miss that.
    swapped = [{"key": "fabric", "seconds": 8.01, "size": (720, 1280)}]
    status, detail = quality.shots_check(swapped, declared)
    assert status == "FAIL" and "fabric" in detail
    assert quality.shots_check([{"key": "hook", "seconds": 8.01, "size": (360, 640)}], declared)[0] == "FAIL"
    assert quality.shots_check([], declared)[0] == "FAIL"


def test_the_script_reports_a_failure_when_nothing_has_been_produced(tmp_path, capsys):
    quality = load()
    assert quality.main(["--out", str(tmp_path)]) == 1
    printed = capsys.readouterr().out
    assert quality.REVIEW_NOTE in printed
    assert "fail=" in printed
