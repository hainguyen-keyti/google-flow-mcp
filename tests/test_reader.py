import asyncio
import json
from pathlib import Path

from video.flow import reader

FIXTURES = Path(__file__).parent / "fixtures" / "rpc"


def test_every_balance_read_leaves_one_timestamped_line(monkeypatch, tmp_path):
    # Measured 2026-09-14: the balance read 255, 240, 255 and then 195 while the spend ledger had no row
    # for the day, and nothing recorded when each reading was taken. Every reader of the balance goes
    # through reader.credits (CLI, MCP, gflow jobs, clip editor), so that is where the line is written.
    payload = json.loads((FIXTURES / "nzlxg.json").read_text(encoding="utf-8"))["payload"]

    async def fake_grid(session, settle=6.0, **kwargs):
        return {"nzlxg": [payload]}

    log = tmp_path / "credits.jsonl"
    monkeypatch.setattr(reader, "grid", fake_grid)
    monkeypatch.setattr(reader, "CREDITS_LOG", log, raising=False)

    info = asyncio.run(reader.credits(object()))

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    assert len(rows) == 1
    assert rows[0]["balance"] == info["balance"] == 840
    assert isinstance(rows[0]["ts"], float)


# Plan AX: a read ends once the reply it needs has arrived and the page has been quiet, the old settle its bound.
# Measured 2026-11-12: the balance read took 7.3 s, 6 s of it a fixed wait, on a page that loads in 1.2 s.


class _Reply:
    def __init__(self, rpcids, body):
        self.request = type(
            "Q", (), {"url": f"https://flow.google.com/_/x/data/batchexecute?rpcids={rpcids}&x=1"}
        )()
        self._body = body

    async def text(self):
        return self._body


class _TimedPage:
    """Replies land at given milliseconds of the page's own waiting."""

    def __init__(self, replies):
        self.replies = sorted(replies, key=lambda r: r[0])
        self.waited = 0
        self.handlers = []

    def on(self, event, handler):
        self.handlers.append(handler)

    def remove_listener(self, event, handler):
        self.handlers.remove(handler)

    async def wait_for_timeout(self, ms):
        self.waited += ms
        while self.replies and self.replies[0][0] <= self.waited:
            _, rpcids, body = self.replies.pop(0)
            for handler in list(self.handlers):
                handler(_Reply(rpcids, body))
        await asyncio.sleep(0)


def _frames_of(monkeypatch):
    from video.flow import reader

    monkeypatch.setattr(reader, "parse_frames", lambda body: [(body, {"from": body})])
    return reader


async def _no_action():
    return None


def test_a_read_ends_soon_after_the_reply_it_needs_and_not_at_the_full_settle(monkeypatch):
    reader = _frames_of(monkeypatch)
    page = _TimedPage([(300, "nzlxg", "nzlxg"), (500, "UpteDb", "UpteDb")])
    session = type("S", (), {"page": page})()

    frames = asyncio.run(reader.capture(session, _no_action, settle=6.0, wants=("nzlxg",)))

    assert "nzlxg" in frames and "UpteDb" in frames, "a reply inside the quiet period is kept too"
    assert 1_000 <= page.waited <= 2_500, page.waited
    assert page.handlers == []


def test_a_read_waits_the_whole_settle_for_a_reply_that_never_comes(monkeypatch):
    reader = _frames_of(monkeypatch)
    page = _TimedPage([(300, "UpteDb", "UpteDb")])
    session = type("S", (), {"page": page})()

    frames = asyncio.run(reader.capture(session, _no_action, settle=6.0, wants=("nzlxg",)))

    assert "nzlxg" not in frames and 6_000 <= page.waited <= 6_200, page.waited


def test_a_read_keeps_waiting_while_replies_keep_arriving(monkeypatch):
    """I4: quiet means quiet. A page still answering is not cut short just because the wanted reply came first."""
    reader = _frames_of(monkeypatch)
    page = _TimedPage([(200, "Zzl0ze", "Zzl0ze"), (900, "ngNC2", "ngNC2"), (1_700, "yBhWQ", "yBhWQ")])
    session = type("S", (), {"page": page})()

    frames = asyncio.run(reader.capture(session, _no_action, settle=10.0, wants=("Zzl0ze", "ngNC2", "yBhWQ")))

    assert set(frames) == {"Zzl0ze", "ngNC2", "yBhWQ"}
    assert 2_600 <= page.waited <= 3_300, page.waited


def test_a_capture_that_names_no_reply_waits_its_settle_as_before(monkeypatch):
    reader = _frames_of(monkeypatch)
    page = _TimedPage([(300, "nzlxg", "nzlxg")])
    session = type("S", (), {"page": page})()

    asyncio.run(reader.capture(session, _no_action, settle=6.0))

    assert page.waited == 6_000
