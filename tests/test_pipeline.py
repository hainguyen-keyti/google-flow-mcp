import pytest

from video import gen
from video.story import persona, pipeline


def test_persona_is_described_from_the_owner_bible():
    assert persona.NAME.strip()
    low = persona.PERSONALITY.lower()
    assert "calm" in low
    assert "romantic feminine bedroom wardrobe" in low, "personality should quote the bible's aesthetic"


def test_job_state_tells_new_done_and_blocked_apart(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    assert pipeline.job_state(ledger, "tryon-01") == "new"

    ledger.append("tryon-01", "submitted", kind="story")
    assert pipeline.job_state(ledger, "tryon-01") == "blocked", "money may already be in flight"

    ledger.append("tryon-01", "done", path="out/story/tryon-01.mp4")
    assert pipeline.job_state(ledger, "tryon-01") == "done"


def test_blocked_job_stops_the_run_instead_of_spending_again(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("tryon-02", "submitted", kind="story")
    with pytest.raises(RuntimeError, match="tryon-02"):
        pipeline.check_resumable(ledger, ["tryon-01", "tryon-02", "tryon-03"])


def test_check_resumable_returns_only_the_jobs_left_to_run(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("tryon-01", "done", path="a.mp4")
    assert pipeline.check_resumable(ledger, ["tryon-01", "tryon-02"]) == ["tryon-02"]
