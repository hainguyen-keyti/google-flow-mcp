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


def recipe_from(
    listing: Any,
    media_id: str,
    *,
    workflow_id: str | None = None,
    characters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """What one clip was made from, with each input named: the newest version unless one is asked for."""
    rows = [row for row in parsers.records(listing) if row["id"] == media_id]
    if not rows:
        raise LookupError(f"media {media_id} has no generation record in this project's listing")
    recipes = parsers.recipes(listing)
    if workflow_id is not None:
        chosen = next((row for row in rows if row["workflow_id"] == workflow_id), None)
        if chosen is None:
            raise LookupError(
                f"media {media_id} has no version {workflow_id}; its versions are "
                f"{[row['workflow_id'] for row in rows]}"
            )
    else:
        # An upscale is a rendition of a version, not a version (the rule of download.is_upsampled).
        versions = [row for row in rows if "upsampler" not in str(row.get("model") or "")] or rows
        chosen = max(versions, key=lambda row: row.get("created") or 0)
    recipe = recipes.get(chosen["workflow_id"])
    if recipe is None:
        raise LookupError(
            f"media {media_id} is an image or keeps no recipe in the listing; only a generated clip has one"
        )
    media_of = {row["workflow_id"]: row["id"] for row in parsers.records(listing)}
    title_of = {item["id"]: item["title"] for item in parsers.media(listing)}
    voice_media = {
        item["workflow_id"]: item["title"] for item in parsers.media(listing) if item["workflow_id"]
    }
    presets = {voice["id"]: voice["name"] for voice in parsers.voices_from_listing(listing)}
    people = {each["entity_id"]: each.get("name") for each in characters or []}

    def image(workflow: str) -> dict[str, Any]:
        found = media_of.get(workflow)
        return {"workflow_id": workflow, "media_id": found, "title": title_of.get(found)}

    source = recipe["source_workflow_id"]
    return {
        "media_id": media_id,
        "workflow_id": chosen["workflow_id"],
        "model_key": recipe["model_key"],
        "kind": recipe["kind"],
        "frames": [{"slot": frame["slot"], **image(frame["workflow_id"])} for frame in recipe["frames"]],
        "reference_images": [image(workflow) for workflow in recipe["reference_images"]],
        # Flow records a voice of your own by its workflow id and a preset by its lowercase name (measured
        # 2026-10-01), so the id itself tells which it is, whatever this listing happens to name.
        "voices": [
            {
                "voice": voice,
                "name": presets.get(voice) or voice_media.get(voice),
                "custom": parsers._uuid(voice),
            }
            for voice in recipe["voices"]
        ],
        "characters": [{"entity_id": entity, "name": people.get(entity)} for entity in recipe["characters"]],
        "source": {"workflow_id": source, "media_id": media_of.get(source)} if source else None,
    }


async def recipe(
    session: FlowSession, project_id: str, media_id: str, *, workflow_id: str | None = None
) -> dict[str, Any]:
    """Read back from the listing what a clip carried when it was made. Free."""
    frames = await capture(
        session, lambda: session.goto(session.project_url(project_id), ready=PROJECT_READY), settle=8.0
    )
    listing = one(frames, "Zzl0ze")
    return recipe_from(
        listing, media_id, workflow_id=workflow_id, characters=parsers.characters_from_listing(listing)
    )


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
