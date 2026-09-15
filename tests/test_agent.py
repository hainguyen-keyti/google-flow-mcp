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


@pytest.mark.parametrize("message", ["hi\nthere", "hi\r"])
def test_agent_send_refuses_a_multiline_message_before_the_ledger_or_the_page(tmp_path: Path, message):
    # Review 2026-09-15: keyboard.type presses Enter for a newline, and the message was typed into the agent box
    # before its `submitted` row, so a second line could send the first on its own.
    with pytest.raises(ValueError, match="one line"):
        asyncio.run(agent.send(_NoSession(), "p", message, 1.0, out_dir=tmp_path, job_id="job-2"))
    assert not (tmp_path / "ledger.jsonl").exists()
