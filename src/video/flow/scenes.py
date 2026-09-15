"""Scenes (Scenebuilder) on the migrated host (measured 2026-09-12): 'Add media' > 'New scene' creates a
scene (rpc rqZuUc) and opens /project/<id>/scene/<scene_id>. Scenes are listed in Zzl0ze[4]; the grid tile's
'More options' > 'Move to trash' (rpc BpMsoe) keeps the entry listed with the trashed flag set and shows it
under the project's Trash view. The scene editor's own 'Move to trash' button did nothing in two runs."""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import parsers
from video.flow.reader import capture, one
from video.session import PROJECT_READY, FlowSession

_SCENE_RE = re.compile(r"/scene/([A-Za-z0-9-]+)")
BUILDER = "flow-scene-builder"


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


async def list_scenes(
    session: FlowSession, project_id: str, *, include_trashed: bool = False
) -> list[dict[str, Any]]:
    scenes = parsers.scenes_from_listing(await _listing(session, project_id))
    return scenes if include_trashed else [s for s in scenes if not s["trashed"]]


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
    """Move a scene to the project's trash; verified by the trashed flag in the listing."""
    page = session.page
    scenes = await list_scenes(session, project_id, include_trashed=True)
    scene = next((s for s in scenes if s["scene_id"] == scene_id), None)
    if scene is None:
        raise LookupError(f"scene {scene_id} is not in the project listing")
    if scene["trashed"]:
        raise LookupError(f"scene {scene_id} is already in the trash")
    title = scene["title"]
    if not title:
        raise LookupError(
            f"scene {scene_id} has no title, and grid tiles carry no scene id, so trashing it would guess"
        )
    # Measured 2026-09-16 (plan D T1): the title sits in an element of its own, between two icon ligatures.
    tiles = page.locator("flow-tile-container", has=page.locator("flow-scene-tile")).filter(
        has=page.get_by_text(title, exact=True)
    )
    matching = await tiles.count()
    if matching == 0:
        raise LookupError(f"scene tile titled {title!r} not found on the grid")
    if matching > 1:
        raise RuntimeError(
            f"{matching} scene tiles show {title!r}; grid tiles carry no ids, so trashing would guess"
        )
    tile = tiles.first
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
    after = await list_scenes(session, project_id, include_trashed=True)
    state = next((s for s in after if s["scene_id"] == scene_id), None)
    if state is not None and not state["trashed"]:
        raise RuntimeError(f"delete: scene {scene_id} is still active; rpcids {sorted(frames)}")
    return {
        "scene_id": scene_id,
        "trashed": state is not None,
        "rpcids": sorted(frames),
        "remaining": sum(1 for s in after if not s["trashed"]),
    }


async def restore(session: FlowSession, project_id: str, scene_id: str) -> dict[str, Any]:
    """Bring a scene back from the project's Trash; verified by the trashed flag in the listing.

    Measured 2026-09-15: /project/<id>/trash opens directly, a trashed tile carries no scene id, hovering it shows
    Restore and Delete permanently, and Restore fires BpMsoe with no confirm dialog. The tile can only be found
    by its exact title, so a scene with no title, or a title more than one trash tile shows, is refused instead of
    guessed."""
    page = session.page
    scenes = await list_scenes(session, project_id, include_trashed=True)
    scene = next((s for s in scenes if s["scene_id"] == scene_id), None)
    if scene is None:
        raise LookupError(f"scene {scene_id} is not in the project listing")
    if not scene["trashed"]:
        raise LookupError(f"scene {scene_id} is not in the trash")
    title = scene["title"]
    if not title:
        raise LookupError(
            f"scene {scene_id} has no title, and the trash shows no ids, so restoring it would guess"
        )
    trashed = sum(1 for s in scenes if s["trashed"])
    await session.goto(f"{session.project_url(project_id)}/trash", ready=PROJECT_READY)
    scene_tiles = page.locator("flow-tile-container", has=page.locator("flow-scene-tile"))
    # Tiles render over time: match the title only once every trashed scene has one, or a late namesake hides.
    waited_ms = 0
    while (shown := await scene_tiles.count()) < trashed:
        if waited_ms >= 15_000:
            raise LookupError(
                f"the trash shows {shown} scene tiles for {trashed} trashed scenes; not guessing"
            )
        await page.wait_for_timeout(1_000)
        waited_ms += 1_000
    if shown > trashed:
        # A tile the listing does not know ends the wait early and can be the only title match before the right one renders.
        raise LookupError(f"the trash shows {shown} scene tiles for {trashed} trashed scenes; not guessing")
    tiles = scene_tiles.filter(has=page.get_by_text(title, exact=True))
    matching = await tiles.count()
    if matching != 1:
        raise RuntimeError(
            f"{matching} trash tiles match {title!r}; the trash shows no ids, so restoring would guess"
        )
    tile = tiles.first
    await tile.hover(timeout=8_000)
    await page.wait_for_timeout(600)
    button = tile.get_by_role("button", name=re.compile("^Restore$", re.IGNORECASE)).first
    frames = await capture(session, lambda: button.click(timeout=8_000), settle=5.0)
    after = await list_scenes(session, project_id, include_trashed=True)
    was = {s["scene_id"]: s["trashed"] for s in scenes}
    moved = [
        s for s in after if s["scene_id"] != scene_id and was.get(s["scene_id"], s["trashed"]) != s["trashed"]
    ]
    if moved:
        names = ", ".join(f"{s['scene_id']} ({s['title']!r})" for s in moved)
        raise RuntimeError(f"restore changed another scene: {names}; check scene_list with include_trashed")
    state = next((s for s in after if s["scene_id"] == scene_id), None)
    if state is None or state["trashed"]:
        raise RuntimeError(f"restore: scene {scene_id} is still in the trash; rpcids {sorted(frames)}")
    return {
        "scene_id": scene_id,
        "trashed": False,
        "rpcids": sorted(frames),
        "active": sum(1 for s in after if not s["trashed"]),
    }
