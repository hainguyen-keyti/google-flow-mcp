"""Scenes (Scenebuilder) on the migrated host (measured 2026-09-12): 'Add media' > 'New scene' creates a
scene (rpc rqZuUc) and opens /project/<id>/scene/<scene_id>. Scenes are listed in Zzl0ze[4]. The editor's
'Move to trash' button did nothing in two runs; deletion goes through the grid tile's More options menu."""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import parsers
from video.flow.reader import capture, one
from video.session import PROJECT_READY, FlowSession

_SCENE_RE = re.compile(r"/scene/([A-Za-z0-9-]+)")
BUILDER = "flow-scene-builder"
_TILES_JS = (
    "() => [...document.querySelectorAll('flow-tile-container')]"
    ".map(t => (t.innerText || '').trim().replace(/\\s+/g, ' '))"
)


def scene_id_from_url(url: str) -> str:
    match = _SCENE_RE.search(url)
    if not match:
        raise ValueError(f"not a scene url: {url}")
    return match.group(1)


async def _listing(session: FlowSession, project_id: str) -> Any:
    frames = await capture(
        session, lambda: session.goto(session.project_url(project_id), ready=PROJECT_READY), settle=8.0
    )
    return one(frames, "Zzl0ze")


async def list_scenes(session: FlowSession, project_id: str) -> list[dict[str, Any]]:
    return parsers.scenes_from_listing(await _listing(session, project_id))


async def create(session: FlowSession, project_id: str, title: str | None = None) -> dict[str, Any]:
    page = session.page
    await session.goto(session.project_url(project_id), ready=PROJECT_READY)
    await page.wait_for_timeout(2_000)
    await page.get_by_role("button", name=re.compile("Add media", re.IGNORECASE)).first.click(timeout=8_000)
    await page.wait_for_timeout(1_000)
    item = (
        page.locator("[role=menuitem], .cdk-overlay-pane button")
        .filter(has_text=re.compile("New scene", re.IGNORECASE))
        .first
    )
    frames = await capture(session, lambda: item.click(timeout=8_000), settle=8.0)
    scene_id = scene_id_from_url(page.url)
    result: dict[str, Any] = {
        "scene_id": scene_id,
        "project_id": project_id,
        "rpcids": sorted(frames),
        "title": None,
    }
    if title:
        box = page.locator("flow-editor-header flow-editable-text, flow-editable-text").first

        async def edit() -> None:
            await box.click(timeout=8_000)
            await page.keyboard.press("Meta+A")
            await page.keyboard.type(title)
            await page.keyboard.press("Enter")

        rename_frames = await capture(session, edit, settle=4.0)
        result["title"] = title
        result["rename_rpcids"] = sorted(rename_frames)
    return result


async def delete(session: FlowSession, project_id: str, scene_id: str) -> dict[str, Any]:
    page = session.page
    scenes = await list_scenes(session, project_id)
    scene = next((s for s in scenes if s["scene_id"] == scene_id), None)
    if scene is None:
        raise LookupError(f"scene {scene_id} is not in the project listing")
    title = scene["title"] or ""
    tile = (
        page.locator("flow-tile-container", has=page.locator("flow-scene-tile"))
        .filter(has_text=re.compile(re.escape(title)))
        .first
    )
    if await tile.count() == 0:
        raise LookupError(f"scene tile titled {title!r} not found on the grid")
    await tile.hover(timeout=8_000)
    await page.wait_for_timeout(600)
    await tile.get_by_role("button", name=re.compile("More options", re.IGNORECASE)).first.click(
        timeout=8_000
    )
    await page.wait_for_timeout(800)
    item = (
        page.locator("[role=menuitem], .cdk-overlay-pane button")
        .filter(has_text=re.compile("Move to trash", re.IGNORECASE))
        .first
    )
    frames = await capture(session, lambda: item.click(timeout=8_000), settle=3.0)
    dialog = page.locator("[role=dialog], mat-dialog-container").first
    try:
        await dialog.wait_for(state="visible", timeout=6_000)
        confirm = dialog.get_by_role(
            "button", name=re.compile("trash|delete|remove|confirm", re.IGNORECASE)
        ).first
        confirm_frames = await capture(session, lambda: confirm.click(timeout=8_000), settle=5.0)
        frames = {**frames, **confirm_frames}
    except PlaywrightTimeoutError:
        await page.wait_for_timeout(2_000)
    remaining = await list_scenes(session, project_id)
    if any(s["scene_id"] == scene_id for s in remaining):
        raise RuntimeError(f"delete: scene {scene_id} is still listed; rpcids {sorted(frames)}")
    return {"scene_id": scene_id, "rpcids": sorted(frames), "remaining": len(remaining)}
