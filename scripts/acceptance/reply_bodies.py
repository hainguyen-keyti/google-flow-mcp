"""Are Flow's replies read whole? A grid read and N project reads through the real FlowSession and reader, the way
every tool reads, with a listener of its own that reads every batchexecute reply as the tools do (plan AT, 2026-10-22).

    uv run python scripts/acceptance/reply_bodies.py --profile harleychina0 --project <id> [--loads 4]
    VIDEO_REPLY_BODIES=0 uv run python scripts/acceptance/reply_bodies.py ...   # the read without the route: the red

A read PASSes when every rpc the wire baseline holds for its view was heard and no reply failed to be read; the script
exits 1 on any FAIL. Measured 2026-10-21 under Chrome 155 without the route: 11 of 41 replies of one project load
could not be read, and `Zzl0ze`, the listing every tool depends on, arrived on some loads and not on others.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import os
import sys
from pathlib import Path

os.environ.setdefault("GFLOW_CLI_LEASE_WAIT_SECONDS", "900")
os.environ.setdefault("GFLOW_CLI_LOG_LEVEL", "WARNING")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from video import bodies
from video.flow import reader, version, wire
from video.flow.reader import capture
from video.session import PROJECT_READY, FlowSession


def expected(view: str) -> set[str]:
    return {rpcid for rpcid, entry in wire.baseline()["rpcs"].items() if entry["view"] == view}


class Reads:
    """Every batchexecute reply read with response.text(), as the tools' listeners read, with the failures counted."""

    def __init__(self, page):
        self.page, self.ok, self.failed, self.pending = page, 0, 0, []
        page.on("response", self.on_response)

    def on_response(self, response):
        if bodies.covers(response.request.url):
            self.pending.append(asyncio.ensure_future(self.read(response)))

    async def read(self, response):
        try:
            await response.text()
            self.ok += 1
        except Exception:  # noqa: BLE001
            self.failed += 1

    async def settle(self) -> tuple[int, int]:
        await asyncio.gather(*self.pending, return_exceptions=True)
        ok, failed, self.ok, self.failed, self.pending = self.ok, self.failed, 0, 0, []
        return ok, failed


async def run(profile: str, project: str, loads: int) -> list[dict]:
    rows = []
    async with FlowSession(profile) as session:
        reads = Reads(session.page)
        frames = await reader.grid(session)
        build = await version.read(session.page)
        rows.append(await judge("grid", 1, frames, reads, build))
        opening = functools.partial(session.goto, session.project_url(project), ready=PROJECT_READY)
        for index in range(1, loads + 1):
            frames = await capture(session, opening, settle=8.0)
            rows.append(await judge("project", index, frames, reads, build))
    return rows


async def judge(view: str, index: int, frames, reads: Reads, build) -> dict:
    ok, failed = await reads.settle()
    served, fallen = bodies.counts["served"], bodies.counts["fallen_back"]
    bodies.counts.update(served=0, fallen_back=0)
    missing = sorted(expected(view) - set(frames))
    # With the route on, a read answered by Chrome instead of the route is a call the route did not see (review D9).
    on = os.environ.get("VIDEO_REPLY_BODIES", "1") != "0"
    escaped = fallen if on else 0
    return {
        "name": f"{view} read {index}",
        "status": "PASS" if not missing and not failed and not escaped else "FAIL",
        "detail": f"heard {len(frames)} rpcs, read {ok} replies ({served} from the route, {fallen} from Chrome), "
        f"{failed} unreadable, missing {missing or 'none'}" + (f", build {build}" if index == 1 else ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--loads", type=int, default=4)
    args = parser.parse_args()
    switch = os.environ.get("VIDEO_REPLY_BODIES", "1")
    print(f"route {'off' if switch == '0' else 'on'} (VIDEO_REPLY_BODIES={switch}), profile {args.profile}")
    rows = asyncio.run(run(args.profile, args.project, args.loads))
    for row in rows:
        print(f"{row['status']:5} {row['name']:16} {row['detail']}")
    counts = {status: sum(1 for row in rows if row["status"] == status) for status in ("PASS", "FAIL")}
    print(f"rows={len(rows)} pass={counts['PASS']} fail={counts['FAIL']}")
    return 0 if counts["FAIL"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
