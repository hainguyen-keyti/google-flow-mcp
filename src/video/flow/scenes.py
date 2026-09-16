"""Scenes (Scenebuilder) on the migrated host (measured 2026-09-12): 'Add media' > 'New scene' creates a
scene (rpc rqZuUc) and opens /project/<id>/scene/<scene_id>. Scenes are listed in Zzl0ze[4]; the grid tile's
'More options' > 'Move to trash' (rpc BpMsoe) keeps the entry listed with the trashed flag set and shows it
under the project's Trash view. The scene editor's own 'Move to trash' button did nothing in two runs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import parsers, reader
from video.flow.reader import capture, one
from video.session import PROJECT_READY, FlowSession

_SCENE_RE = re.compile(r"/scene/([A-Za-z0-9-]+)")
BUILDER = "flow-scene-builder"
TILE_WAIT_MS = 15_000
TOOLBAR_MIN = 20
TOOLBAR_WAIT_MS = 15_000
THUMBS = "flow-scene-timeline video, flow-scene-timeline img"
OVERLAY = ".cdk-overlay-pane button, [role=dialog] button"
# Add clip's menu offers Extend (Veo 3.1 - Lite), 10 credits, right next to the item this driver wants.
PAID_WORDS = ("extend", "generate", "upscale")
# U+200B zero width space and U+00AD soft hyphen, built with chr() so this file carries no escape sequence.
_ZERO_WIDTH = str.maketrans("", "", chr(0x200B) + chr(0xAD))


def _tile_text(title: str | None) -> str:
    """What an exact text selector is compared against: Playwright drops U+200B and U+00AD, collapses runs of
    whitespace and trims, on the element's text and on the selector alike. A title that comes out empty here
    would match every tile element that shows no text of its own."""
    return " ".join((title or "").translate(_ZERO_WIDTH).split())


async def _wait_for_tiles(page: Any, tiles: Any, expected: int, where: str, state: str) -> None:
    """Tiles render over time, so judge titles only once the view holds one tile per listed scene: a late tile is
    missed otherwise, and a tile the listing does not know can be the only title match before the right one renders."""
    waited_ms = 0
    while (shown := await tiles.count()) < expected:
        if waited_ms >= TILE_WAIT_MS:
            break
        await page.wait_for_timeout(1_000)
        waited_ms += 1_000
    if shown != expected:
        raise LookupError(
            f"the {where} shows {shown} scene tiles for {expected} {state} scenes; not guessing"
        )


def _moved_scenes(before: list[dict[str, Any]], after: list[dict[str, Any]], scene_id: str) -> str:
    """Tiles carry no scene id, so acting on the wrong one can only be caught afterwards, in the listing."""
    was = {s["scene_id"]: s["trashed"] for s in before}
    moved = [
        s for s in after if s["scene_id"] != scene_id and was.get(s["scene_id"], s["trashed"]) != s["trashed"]
    ]
    return ", ".join(f"{s['scene_id']} ({s['title']!r})" for s in moved)


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


async def _open_scene(session: FlowSession, project_id: str, scene_id: str) -> None:
    """Measured 2026-09-16: a scene page builds its toolbar in stages, 2 to 4 s, and a page read too early showed
    7 of its 27 buttons, so anything matched against it before then is a guess."""
    page = session.page
    await session.goto(f"{session.project_url(project_id)}/scene/{scene_id}", ready=BUILDER)
    waited = 0
    while (shown := await page.locator("button").count()) < TOOLBAR_MIN:
        if waited >= TOOLBAR_WAIT_MS:
            raise LookupError(
                f"the scene page showed {shown} buttons after {waited // 1000}s; it never finished building"
            )
        await page.wait_for_timeout(1_000)
        waited += 1_000


async def _click_one(page: Any, found: Any, what: str) -> str:
    """Click the single control that matches, and never one that spends: labels sit next to paid actions here."""
    matching = await found.count()
    if matching != 1:
        raise LookupError(f"{matching} controls match {what}; not guessing")
    label = await found.first.inner_text()
    if any(word in (label or "").lower() for word in PAID_WORDS):
        raise RuntimeError(f"refusing to click {label!r} for {what}: that control spends credits")
    await found.first.click(timeout=8_000)
    return label


async def rename(session: FlowSession, project_id: str, scene_id: str, title: str) -> dict[str, Any]:
    """Rename a scene through the header's editable text, confirmed by the listing (rpc BpMsoe)."""
    if not _tile_text(title):
        raise ValueError(f"a scene title must hold visible text, got {title!r}")
    page = session.page
    await _open_scene(session, project_id, scene_id)
    box = page.locator("flow-editor-header flow-editable-text, flow-editable-text").first

    async def edit() -> None:
        await box.click(timeout=8_000)
        await page.keyboard.press("Meta+A")
        await page.keyboard.type(title)
        await page.keyboard.press("Enter")

    frames = await capture(session, edit, settle=4.0)
    after = await list_scenes(session, project_id, include_trashed=True)
    state = next((s for s in after if s["scene_id"] == scene_id), None)
    if state is None or state.get("title") != title:
        raise RuntimeError(
            f"rename: the listing still reads {(state or {}).get('title')!r} for scene {scene_id}"
        )
    return {"scene_id": scene_id, "title": title, "rpcids": sorted(frames)}


async def add_clip(session: FlowSession, project_id: str, scene_id: str, media_id: str) -> dict[str, Any]:
    """Put one of the project's clips on a scene's timeline.

    Measured 2026-09-16: a picker row carries NO media id, only the media's title, and titles do collide in this
    account, so the media is resolved to its title from the listing and a title more than one row shows is refused
    instead of guessed. An empty scene answers 'Add clip' with the picker, a scene that already holds a clip answers
    with a menu first. Clicking the row IS the add: the picker closes itself and no confirm button follows.
    """
    page = session.page
    media = (await reader.project(session, project_id))["media"]
    item = next((m for m in media if m.get("id") == media_id), None)
    if item is None:
        raise LookupError(f"media {media_id} is not in the project listing")
    title = item.get("title")
    if not _tile_text(title):
        raise LookupError(
            f"media {media_id} has no title to match ({title!r}), and picker rows carry no media id, so adding "
            "it would guess"
        )
    await _open_scene(session, project_id, scene_id)
    before = await page.locator(THUMBS).count()
    # By accessible name, not by text: measured 2026-09-16, this button's text is only the ligature "add_2" while
    # "Add clip" lives in its aria-label, so a text match finds nothing at all (live run of scene_build).
    await _click_one(
        page,
        page.get_by_role("button", name=re.compile("Add clip", re.IGNORECASE)),
        "the Add clip button",
    )
    await page.wait_for_timeout(1_500)
    menu = page.locator(OVERLAY).filter(has_text=re.compile("Add clip", re.IGNORECASE))
    if await menu.count():
        await _click_one(page, menu, "the Add clip menu item")
        await page.wait_for_timeout(2_000)
    rows = page.locator(OVERLAY).filter(has=page.get_by_text(title, exact=True))
    matching = await rows.count()
    if matching == 0:
        raise LookupError(f"media {media_id} titled {title!r} is not offered by the picker")
    if matching > 1:
        raise RuntimeError(
            f"{matching} picker rows show {title!r}; rows carry no media id, so adding would guess"
        )
    frames = await capture(session, lambda: _click_one(page, rows, "the picker row"), settle=6.0)
    after = await page.locator(THUMBS).count()
    if after <= before:
        raise RuntimeError(f"add_clip: the timeline still shows {after} thumbnails; nothing was added")
    return {
        "scene_id": scene_id,
        "media_id": media_id,
        "title": title,
        "clips_before": before,
        "clips_after": after,
        "rpcids": sorted(frames),
    }


async def download(session: FlowSession, project_id: str, scene_id: str, *, out_dir: Path) -> dict[str, Any]:
    """Hand back the scene as one film.

    Measured 2026-09-16: unlike the clip editor, a scene has no quality menu. 'Download scene' downloads straight
    away, and the file is the whole assembled timeline (two 8 s clips came back as one 16.0 s mp4).
    """
    page = session.page
    await _open_scene(session, project_id, scene_id)
    clips = await page.locator(THUMBS).count()
    if clips == 0:
        raise LookupError(f"scene {scene_id} has no clip on its timeline to download")
    # Same as Add clip: the label is the aria-label, the text is just the ligature "download".
    button = page.get_by_role("button", name=re.compile("Download scene", re.IGNORECASE))
    async with page.expect_download(timeout=600_000) as info:
        await _click_one(page, button, "the Download scene button")
    handed = await info.value
    target = Path(out_dir) / f"{scene_id}{Path(handed.suggested_filename).suffix or '.mp4'}"
    if target.exists():
        raise FileExistsError(target)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    await handed.save_as(str(target))
    return {
        "scene_id": scene_id,
        "path": str(target),
        "clips": clips,
        "suggested": handed.suggested_filename,
        "bytes": target.stat().st_size,
    }


async def delete(session: FlowSession, project_id: str, scene_id: str) -> dict[str, Any]:
    """Move a scene to the project's trash; verified by the trashed flag in the listing.

    Measured 2026-09-16: the grid holds one tile per active scene and none for a trashed one, and a tile carries no
    scene id, so wait for that many tiles before matching the exact title, as restore does for the trash."""
    page = session.page
    scenes = await list_scenes(session, project_id, include_trashed=True)
    scene = next((s for s in scenes if s["scene_id"] == scene_id), None)
    if scene is None:
        raise LookupError(f"scene {scene_id} is not in the project listing")
    if scene["trashed"]:
        raise LookupError(f"scene {scene_id} is already in the trash")
    title = scene["title"]
    if not _tile_text(title):
        raise LookupError(
            f"scene {scene_id} has no title to match ({title!r}), and grid tiles carry no scene id, so trashing "
            "it would guess"
        )
    # Measured 2026-09-16 (plan D T1): the title sits in an element of its own, between two icon ligatures.
    active = sum(1 for s in scenes if not s["trashed"])
    scene_tiles = page.locator("flow-tile-container", has=page.locator("flow-scene-tile"))
    await _wait_for_tiles(page, scene_tiles, active, "grid", "active")
    tiles = scene_tiles.filter(has=page.get_by_text(title, exact=True))
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
    moved = _moved_scenes(scenes, after, scene_id)
    if moved:
        raise RuntimeError(f"delete changed another scene: {moved}; check scene_list with include_trashed")
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
    if not _tile_text(title):
        raise LookupError(
            f"scene {scene_id} has no title to match ({title!r}), and the trash shows no ids, so restoring it "
            "would guess"
        )
    trashed = sum(1 for s in scenes if s["trashed"])
    await session.goto(f"{session.project_url(project_id)}/trash", ready=PROJECT_READY)
    scene_tiles = page.locator("flow-tile-container", has=page.locator("flow-scene-tile"))
    await _wait_for_tiles(page, scene_tiles, trashed, "trash", "trashed")
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
    moved = _moved_scenes(scenes, after, scene_id)
    if moved:
        raise RuntimeError(f"restore changed another scene: {moved}; check scene_list with include_trashed")
    state = next((s for s in after if s["scene_id"] == scene_id), None)
    if state is None or state["trashed"]:
        raise RuntimeError(f"restore: scene {scene_id} is still in the trash; rpcids {sorted(frames)}")
    return {
        "scene_id": scene_id,
        "trashed": False,
        "rpcids": sorted(frames),
        "active": sum(1 for s in after if not s["trashed"]),
    }
