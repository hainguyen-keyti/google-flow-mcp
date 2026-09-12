"""Read Flow state by letting the page make its own batchexecute calls and decoding the replies."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from gflow_cli.api.transports.batchexecute import parse_frames

from video.flow import parsers
from video.session import GRID_READY, MIGRATED_ROOT, PROJECT_READY, FlowSession

Frames = dict[str, list[Any]]


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
    return parsers.credits(one(await grid(session), "nzlxg"))


async def project(session: FlowSession, project_id: str, settle: float = 10.0) -> dict[str, Any]:
    frames = await capture(
        session, lambda: session.goto(session.project_url(project_id), ready=PROJECT_READY), settle=settle
    )
    return {
        "meta": parsers.project_meta(one(frames, "ngNC2")),
        "models": parsers.video_models(one(frames, "yBhWQ")),
        "media": parsers.media(one(frames, "Zzl0ze")),
    }


async def characters(session: FlowSession, project_id: str) -> list[dict[str, Any]]:
    url = f"{session.project_url(project_id)}/character"
    frames = await capture(session, lambda: session.goto(url), settle=8.0)
    rendered = await session.page.locator("flow-character-page").count() > 0
    return parsers.characters_or_empty(frames, character_page_rendered=rendered)


async def tools(session: FlowSession, project_id: str) -> list[dict[str, Any]]:
    frames = await capture(
        session, lambda: session.goto(session.project_url(project_id), ready=PROJECT_READY), settle=8.0
    )
    return parsers.tools(one(frames, "tRARke"))
