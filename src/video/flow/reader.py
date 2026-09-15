"""Read Flow state by letting the page make its own batchexecute calls and decoding the replies."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from gflow_cli.api.transports.batchexecute import parse_frames

from video.flow import parsers
from video.session import GRID_READY, MIGRATED_ROOT, PROJECT_READY, FlowSession

Frames = dict[str, list[Any]]

# Every balance read lands here with its time: measured 2026-09-14, the balance moved 255, 240, 255, 195 with no spend.
CREDITS_LOG = Path("out") / "credits.jsonl"


async def capture(session: FlowSession, action: Callable[[], Awaitable[Any]], *, settle: float) -> Frames:
    tasks: list[asyncio.Task[str]] = []

    async def body_of(resp: Any) -> str:
        try:
            return await resp.text()
        except Exception:  # noqa: BLE001
            return ""

    def on_response(resp: Any) -> None:
        if "batchexecute" in resp.request.url:
            tasks.append(asyncio.ensure_future(body_of(resp)))

    session.page.on("response", on_response)
    try:
        await action()
        await session.page.wait_for_timeout(int(settle * 1000))
    finally:
        session.page.remove_listener("response", on_response)
    frames: Frames = {}
    for body in await asyncio.gather(*tasks):
        for rpcid, payload in parse_frames(body):
            frames.setdefault(rpcid, []).append(payload)
    return frames


def one(frames: Frames, rpcid: str) -> Any:
    if rpcid not in frames:
        raise LookupError(f"rpc {rpcid} not observed; saw {sorted(frames)}")
    return frames[rpcid][0]


async def grid(session: FlowSession, settle: float = 6.0) -> Frames:
    return await capture(session, lambda: session.goto(MIGRATED_ROOT, ready=GRID_READY), settle=settle)


async def projects(session: FlowSession) -> list[dict[str, Any]]:
    return parsers.projects(one(await grid(session), "UpteDb"))


async def credits(session: FlowSession) -> dict[str, Any]:
    info = parsers.credits(one(await grid(session), "nzlxg"))
    CREDITS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with CREDITS_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": time.time(), "balance": info["balance"]}) + "\n")
    return info


async def project(
    session: FlowSession, project_id: str, settle: float = 10.0, *, versions: bool = False
) -> dict[str, Any]:
    frames = await capture(
        session, lambda: session.goto(session.project_url(project_id), ready=PROJECT_READY), settle=settle
    )
    listing = one(frames, "Zzl0ze")
    out = {
        "meta": parsers.project_meta(one(frames, "ngNC2")),
        "models": parsers.video_models(one(frames, "yBhWQ")),
        "media": parsers.media(listing),
    }
    if versions:
        out["versions"] = parsers.records(listing)
    return out


async def records(session: FlowSession, project_id: str, settle: float = 8.0) -> list[dict[str, Any]]:
    frames = await capture(
        session, lambda: session.goto(session.project_url(project_id), ready=PROJECT_READY), settle=settle
    )
    return parsers.records(one(frames, "Zzl0ze"))


async def characters(session: FlowSession, project_id: str) -> list[dict[str, Any]]:
    frames = await capture(
        session, lambda: session.goto(session.project_url(project_id), ready=PROJECT_READY), settle=8.0
    )
    return parsers.characters_from_listing(one(frames, "Zzl0ze"))


async def tools(session: FlowSession, project_id: str | None = None) -> list[dict[str, Any]]:
    # Only an open project loads the gallery, the same one everywhere (2026-09-15: not the grid, /tools is a 404).
    if project_id is None:
        listed = await projects(session)
        if not listed:
            raise LookupError("no project to load the Tools gallery from; create one with project_create")
        project_id = listed[0]["id"]
    frames = await capture(
        session, lambda: session.goto(session.project_url(project_id), ready=PROJECT_READY), settle=8.0
    )
    return parsers.tools(one(frames, "tRARke"))
