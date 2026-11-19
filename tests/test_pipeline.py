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


def test_a_failure_that_cost_nothing_may_be_retried(tmp_path):
    # Flow refusing a prompt fires a request, creates no media and charges nothing: retrying is safe.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("tryon-01", "submitted", kind="story")
    ledger.append("tryon-01", "failed", spent=0, notice="This content may not be allowed")
    assert pipeline.job_state(ledger, "tryon-01") == "retryable"
    assert pipeline.check_resumable(ledger, ["tryon-01"]) == ["tryon-01"]


def test_a_failure_that_cost_credits_still_blocks(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("tryon-02", "submitted", kind="story")
    ledger.append("tryon-02", "failed", spent=10)
    assert pipeline.job_state(ledger, "tryon-02") == "blocked"


def test_blocked_job_stops_the_run_instead_of_spending_again(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("tryon-02", "submitted", kind="story")
    with pytest.raises(RuntimeError, match="tryon-02"):
        pipeline.check_resumable(ledger, ["tryon-01", "tryon-02", "tryon-03"])


def test_check_resumable_returns_only_the_jobs_left_to_run(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("tryon-01", "done", path="a.mp4")
    assert pipeline.check_resumable(ledger, ["tryon-01", "tryon-02"]) == ["tryon-02"]


@pytest.mark.parametrize("after, spent", [(830, 10), (None, None)])
def test_a_product_shot_says_what_it_spent_and_none_when_the_balance_after_is_unknown(
    monkeypatch, tmp_path, after, spent
):
    """Plan BC: run_job answers a made and paid job with credits_after None when the read after it fails; the
    product shot keeps its clip and says the bill is unknown instead of subtracting None."""
    import asyncio
    from pathlib import Path

    async def fake_run_job(job, out_dir, **kwargs):
        return {
            "job_id": job.job_id,
            "outputs": [{"media_id": "m-1", "path": "out/p.mp4"}],
            "credits_before": 840,
            "credits_after": after,
        }

    monkeypatch.setattr(pipeline.gen, "run_job", fake_run_job)
    monkeypatch.setattr(pipeline.product, "check_image", lambda: Path("product.png"))

    shot = asyncio.run(
        pipeline._generate_with_product(
            None, "P", prompt="the dress", job_id="prod-1", out_dir=tmp_path, wait=0
        )
    )

    assert (shot["media_id"], shot["path"], shot["spent"]) == ("m-1", "out/p.mp4", spent)
