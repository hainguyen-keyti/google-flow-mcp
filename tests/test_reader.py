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

    async def fake_grid(session, settle=6.0):
        return {"nzlxg": [payload]}

    log = tmp_path / "credits.jsonl"
    monkeypatch.setattr(reader, "grid", fake_grid)
    monkeypatch.setattr(reader, "CREDITS_LOG", log, raising=False)

    info = asyncio.run(reader.credits(object()))

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    assert len(rows) == 1
    assert rows[0]["balance"] == info["balance"] == 840
    assert isinstance(rows[0]["ts"], float)
