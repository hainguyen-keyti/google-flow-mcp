import asyncio

import pytest

from video import gen
from video.story import composer, pipeline


def test_reconcile_decision_reads_the_ground_truth():
    row = {"job_id": "tryon-02", "status": "submitted", "credits_before": 515}
    # a record exists: the shot really was generated
    assert pipeline.reconcile_decision(row, 505, {"id": "m"}) == "done"
    # no record and the balance never moved: nothing was spent, safe to retry
    assert pipeline.reconcile_decision(row, 515, None) == "failed"
    # no record but credits moved: a human has to look, never guess
    assert pipeline.reconcile_decision(row, 505, None) == "unknown"


def test_pending_jobs_lists_only_the_ones_stuck_on_submitted(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("tryon-01", "submitted", credits_before=525)
    ledger.append("tryon-01", "done", path="a.mp4")
    ledger.append("tryon-02", "submitted", credits_before=515)
    assert [r["job_id"] for r in pipeline.pending_jobs(ledger)] == ["tryon-02"]


def test_snapshot_retry_survives_a_listing_that_did_not_fire(monkeypatch):
    calls = []

    async def flaky(session, project_id):
        calls.append(1)
        if len(calls) < 3:
            raise LookupError("rpc Zzl0ze not observed; saw ['ngNC2']")
        return ["rows"], {"scene"}

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(composer.clips, "_snapshot", flaky)
    monkeypatch.setattr(composer.asyncio, "sleep", no_sleep)
    rows, _scenes = asyncio.run(composer.snapshot(None, "p"))
    assert rows == ["rows"] and len(calls) == 3


def test_snapshot_retry_gives_up_with_the_real_error(monkeypatch):
    async def always_missing(session, project_id):
        raise LookupError("rpc Zzl0ze not observed; saw []")

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(composer.clips, "_snapshot", always_missing)
    monkeypatch.setattr(composer.asyncio, "sleep", no_sleep)
    with pytest.raises(LookupError, match="Zzl0ze"):
        asyncio.run(composer.snapshot(None, "p", attempts=2))
