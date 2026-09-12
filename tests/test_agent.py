import asyncio
from pathlib import Path

import pytest

from video import gen
from video.flow import agent


class _NoSession:
    def __getattr__(self, name):
        raise AssertionError(f"session.{name} touched after a duplicate job id")


def test_agent_send_refuses_a_job_id_that_already_has_a_submitted_row(tmp_path: Path):
    gen.Ledger(tmp_path / "ledger.jsonl").append("job-1", "submitted", kind="agent")
    with pytest.raises(gen.AlreadySubmitted):
        asyncio.run(agent.send(_NoSession(), "p", "hello", 1.0, out_dir=tmp_path, job_id="job-1"))
