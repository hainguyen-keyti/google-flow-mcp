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


class _AgentPage:
    """Enough page for one send, including the listeners a real one carries."""

    def __init__(self):
        self.listeners: list[tuple[str, object]] = []
        self.attached: list[str] = []
        self.keyboard = self

    def on(self, event, handler):
        self.listeners.append((event, handler))
        self.attached.append(event)

    def remove_listener(self, event, handler):
        self.listeners = [row for row in self.listeners if row != (event, handler)]

    async def type(self, text):
        return None

    async def evaluate(self, script):
        return "the agent said something"

    async def wait_for_timeout(self, ms):
        return None

    def locator(self, selector):
        return self

    def get_by_role(self, role, name=None):
        return self

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    async def click(self, timeout=None):
        return None


class _AgentSession:
    def __init__(self):
        self.page = _AgentPage()


class _Said:
    """Stands in for FlowReplies; its interface is pinned against the real class below."""

    def __init__(self):
        self.seen = 0

    def on_response(self, response):
        self.seen += 1

    def reported_failed(self):
        return False

    async def report(self):
        return {"workflow_id": "wf-agent", "statuses": [6, 3]}


def _agent_world(monkeypatch, balances=None):
    balances = list(balances or [])

    async def fake_capture(session, action, *, settle):
        await action()
        return {"uwAyfb": [[]]}

    async def fake_set_mode(session, project_id, enabled):
        return {"enabled": enabled, "was": False}

    async def fake_credits(session):
        return {"balance": balances.pop(0) if balances else 100}

    monkeypatch.setattr(agent, "capture", fake_capture)
    monkeypatch.setattr(agent, "set_mode", fake_set_mode)
    monkeypatch.setattr(agent.reader, "credits", fake_credits)
    monkeypatch.setattr(agent.composer_mod, "FlowReplies", _Said)


def test_the_agent_row_carries_what_flow_said_and_the_listener_is_removed(monkeypatch, tmp_path: Path):
    """agent_send can spend, and when Flow drops the job its replies are the only place a reason appears."""
    _agent_world(monkeypatch)
    session = _AgentSession()

    result = asyncio.run(agent.send(session, "p", "hello", 1.0, out_dir=tmp_path, job_id="job-3"))

    last = gen.Ledger(tmp_path / "ledger.jsonl").rows("job-3")[-1]
    assert last["flow"] == {"workflow_id": "wf-agent", "statuses": [6, 3]}, last
    assert result["flow"]["workflow_id"] == "wf-agent", result
    assert session.page.attached == ["response"], "it never listened to Flow at all"
    assert session.page.listeners == [], session.page.listeners


def test_the_agent_path_hears_flow_through_the_same_reader_as_the_gen_path():
    from video.flow import composer

    assert agent.composer_mod is composer, "it must use the reader measured on the gen path"


def test_a_send_that_moves_the_balance_says_so(monkeypatch, tmp_path: Path):
    """Four sends measured on this account (2026-09-12 to 2026-09-16) each cost 0 credits, and a send that makes
    the agent generate costs that generation's price. Nothing in the answer said which of the two had just
    happened: the balance moved and the row kept the number to itself."""
    _agent_world(monkeypatch, balances=[100, 88])

    result = asyncio.run(
        agent.send(_AgentSession(), "p", "make me a clip", 1.0, out_dir=tmp_path, job_id="job-4")
    )

    last = gen.Ledger(tmp_path / "ledger.jsonl").rows("job-4")[-1]
    assert last["spent"] == 12, last
    said = last["balance_moved"]
    assert (said["kind"], said["measured"], said["moved"]) == ("agent", 0, 12), last
    assert "generated nothing" in said["note"], said
    assert result["balance_moved"] == said, result


def test_a_send_that_costs_nothing_says_nothing_about_the_price(monkeypatch, tmp_path: Path):
    """The measured case has to stay quiet, or the alarm is noise by the time it matters."""
    _agent_world(monkeypatch, balances=[100, 100])

    result = asyncio.run(agent.send(_AgentSession(), "p", "hello", 1.0, out_dir=tmp_path, job_id="job-5"))

    last = gen.Ledger(tmp_path / "ledger.jsonl").rows("job-5")[-1]
    assert last["spent"] == 0 and "balance_moved" not in last, last
    assert "balance_moved" not in result, result


def test_the_agent_and_the_editor_read_one_price_table(monkeypatch, tmp_path: Path):
    """Two copies of a measured price drift, and the one that drifts is the one nobody is looking at. Proven by
    behaviour, not by the import (review of plan P: an inlined copy kept the import and every test green): move the
    table, and the agent's verdict has to move with it."""
    from video.flow import clips

    _agent_world(monkeypatch, balances=[100, 88])
    monkeypatch.setitem(clips.MEASURED_PRICE, "agent", 12)

    result = asyncio.run(
        agent.send(_AgentSession(), "p", "make me a clip", 1.0, out_dir=tmp_path, job_id="job-6")
    )

    assert "balance_moved" not in result, result
