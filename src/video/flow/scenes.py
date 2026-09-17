"""Scenes (Scenebuilder) on the migrated host (measured 2026-09-12): 'Add media' > 'New scene' creates a
scene (rpc rqZuUc) and opens /project/<id>/scene/<scene_id>. Scenes are listed in Zzl0ze[4]; the grid tile's
'More options' > 'Move to trash' (rpc BpMsoe) keeps the entry listed with the trashed flag set and shows it
under the project's Trash view. The scene editor's own 'Move to trash' button did nothing in two runs."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qs

from gflow_cli.api.transports.batchexecute import parse_frames
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import parsers
from video.flow.reader import capture, one
from video.session import PROJECT_READY, FlowSession

_SCENE_RE = re.compile(r"/scene/([A-Za-z0-9-]+)")
BUILDER = "flow-scene-builder"
TILE_WAIT_MS = 15_000
TOOLBAR_MIN = 20
TOOLBAR_WAIT_MS = 15_000
CONTROL_WAIT_MS = 10_000
SAVE_WAIT_MS = 20_000
# Measured 2026-09-16: a scene film lands within seconds of the click. The editor's 600 s, copied at first, turned one
# click that fired no download at all into a ten minute hang.
DOWNLOAD_WAIT_MS = 180_000
OVERLAY = ".cdk-overlay-pane button, [role=dialog] button"
ROW = ".cdk-overlay-pane button[role=option]"
CLIPS = ".timeline-contents > .clip"
# Measured 2026-09-17 (probes/scene_editor.py) on a heavily loaded machine: every request a scene click fires left the
# page within 0.4 s, while Flow's reply to an add came 11 to 14 s later and nothing is stored until it does.
SEND_WAIT_MS = 15_000
REPLY_WAIT_MS = 90_000
# Add clip's menu offers Extend (Veo 3.1 - Lite), 10 credits, right next to the item this driver wants, and the same
# page carries Start generation. "generation" is listed on its own because "generate" is not a substring of it, which
# left the one certain spender on that page unguarded (review 2026-09-16).
PAID_WORDS = ("extend", "generate", "generation", "upscale")
TITLE_BOX = "flow-editor-header flow-editable-text"
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


def _seconds(value: Any) -> float:
    """A duration as the listing holds it: [] for none, [seconds] or [seconds, nanos]."""
    whole, nanos = parsers._at(value, 0), parsers._at(value, 1)
    return (whole if type(whole) is int else 0) + (nanos if type(nanos) is int else 0) / 1e9


def clips_from_listing(payload: Any, scene_id: str) -> list[dict[str, Any]]:
    """One scene's clips, in the order its film plays them.

    Measured 2026-09-17 (probes/scene_editor.py): Zzl0ze[6] lists every scene's clips in no particular order, each
    [[clip_id, _, _, [title, ...], project_id], scene_id, [index or null for 0, [length], [start], [end], [1]]], and
    the index order matched the downloaded film. A clip plays from its start to its end.
    """
    clips = []
    for entry in parsers._at(payload, 6) or []:
        if parsers._at(entry, 1) != scene_id:
            continue
        timing = parsers._at(entry, 2)
        index = parsers._at(timing, 0)
        clips.append(
            {
                "position": index if type(index) is int else 0,
                "clip_id": parsers._at(entry, 0, 0),
                "title": parsers._at(entry, 0, 3, 0),
                "seconds": round(_seconds(parsers._at(timing, 3)) - _seconds(parsers._at(timing, 2)), 3),
            }
        )
    clips.sort(key=lambda clip: clip["position"])
    positions = [clip["position"] for clip in clips]
    if positions != list(range(len(clips))):
        raise ValueError(f"scene {scene_id} lists its clips at positions {positions}; not guessing the order")
    return clips


ASPECTS = {1: "9:16", 2: "16:9"}


def timeline_from_listing(payload: Any, scene_id: str) -> dict[str, Any]:
    """A scene as the listing keeps it: its clips in order, its length and its aspect ratio (Zzl0ze[4] entry [5],
    1 for 9:16 and 2 for 16:9, measured 2026-09-17 by toggling it)."""
    scene = next((s for s in parsers._at(payload, 4) or [] if parsers._at(s, 0) == scene_id), None)
    if scene is None:
        raise LookupError(f"scene {scene_id} is not in the project listing")
    clips = clips_from_listing(payload, scene_id)
    aspect, flags = parsers._at(scene, 5), parsers._at(scene, 7)
    return {
        "scene_id": scene_id,
        "title": parsers._at(scene, 1),
        "trashed": isinstance(flags, list) and bool(flags) and flags[0] is True,
        "aspect": ASPECTS.get(aspect) if type(aspect) is int else None,
        "seconds": round(sum(clip["seconds"] for clip in clips), 3),
        "clips": clips,
    }


async def timeline(session: FlowSession, project_id: str, scene_id: str) -> dict[str, Any]:
    return timeline_from_listing(await _listing(session, project_id), scene_id)


class _Calls:
    """What the page sent and heard for the named rpcs while one control was clicked.

    A scene click is judged by the request it fires, not by the page: measured 2026-09-17, the Total duration label
    moved within 1.3 s of an add that Flow only stored 11 to 14 s later, when its reply came back.
    """

    def __init__(self, page: Any, rpcids: tuple[str, ...]) -> None:
        self.page = page
        self.rpcids = rpcids
        self.sent: dict[str, list[Any]] = {}
        self.heard: dict[str, list[Any]] = {}
        self._reads: list[asyncio.Future[None]] = []

    def _on_request(self, request: Any) -> None:
        if "batchexecute" not in request.url:
            return
        try:
            entries = json.loads(parse_qs(request.post_data or "").get("f.req", ["[]"])[0])[0]
        except (ValueError, TypeError, IndexError):
            return
        for entry in entries if isinstance(entries, list) else []:
            rpcid, inner = parsers._at(entry, 0), parsers._at(entry, 1)
            if rpcid not in self.rpcids or not isinstance(inner, str):
                continue
            try:
                self.sent.setdefault(rpcid, []).append(json.loads(inner))
            except ValueError:
                continue

    def _on_response(self, response: Any) -> None:
        if "batchexecute" in response.url:
            self._reads.append(asyncio.ensure_future(self._read(response)))

    async def _read(self, response: Any) -> None:
        try:
            body = await response.text()
        except Exception:  # noqa: BLE001
            return
        for rpcid, payload in parse_frames(body):
            if rpcid in self.rpcids:
                self.heard.setdefault(rpcid, []).append(payload)

    def __enter__(self) -> Self:
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        return self

    def __exit__(self, *exc: object) -> None:
        self.page.remove_listener("request", self._on_request)
        self.page.remove_listener("response", self._on_response)

    async def wait(self, seen: dict[str, list[Any]], rpcid: str, limit_ms: int) -> list[Any] | None:
        waited = 0
        while rpcid not in seen:
            if waited >= limit_ms:
                return None
            await self.page.wait_for_timeout(500)
            waited += 500
        return seen[rpcid]


async def set_aspect(session: FlowSession, project_id: str, scene_id: str, aspect: str) -> dict[str, Any]:
    """Set a scene to 9:16 or 16:9 through 'Toggle aspect ratio', confirmed by the listing.

    Measured 2026-09-17: the button flips whatever the page shows and fires BpMsoe with the new ratio under the field
    mask aspect_ratio; the listing and a reloaded page kept it. A scene already at the ratio is left alone, since a
    click there would set the other one.
    """
    wanted = next((code for code, name in ASPECTS.items() if name == aspect), None)
    if wanted is None:
        raise ValueError(f"aspect must be 9:16 or 16:9, got {aspect!r}")
    before = await timeline(session, project_id, scene_id)
    if before["aspect"] == aspect:
        return {"scene_id": scene_id, "aspect": aspect, "changed": False, "rpcids": []}
    if before["aspect"] is None:
        raise LookupError(f"the listing names no aspect ratio for scene {scene_id}; not toggling blind")
    page = session.page
    await _open_scene(session, project_id, scene_id)
    toggle = page.get_by_role("button", name=re.compile("Toggle aspect ratio", re.IGNORECASE))
    button = await _one_of(
        page, toggle, "{count} Toggle aspect ratio buttons on the scene page; not guessing"
    )
    shown = " ".join((await button.inner_text() or "").split())
    if aspect in shown or before["aspect"] not in shown:
        raise RuntimeError(
            f"the editor shows {shown!r} while the listing reads {before['aspect']} for scene {scene_id}; not toggling"
        )
    with _Calls(page, ("BpMsoe",)) as calls:
        await _click_one(page, toggle, "the Toggle aspect ratio button")
        sent = await calls.wait(calls.sent, "BpMsoe", SEND_WAIT_MS)
        if not sent:
            raise RuntimeError(
                f"clicked Toggle aspect ratio once and no aspect ratio change left the page for scene {scene_id}; "
                "read scene_clips before trying again"
            )
        if parsers._at(sent[0], 2, 0) != scene_id or parsers._at(sent[0], 2, 5) != wanted:
            raise RuntimeError(
                f"clicked Toggle aspect ratio once and the page asked Flow for {sent[0]!r} instead"
            )
        heard = await calls.wait(calls.heard, "BpMsoe", REPLY_WAIT_MS)
    if not heard:
        raise RuntimeError(
            f"Flow never answered the aspect ratio change for scene {scene_id} within {REPLY_WAIT_MS // 1000}s; "
            "read scene_clips before trying again"
        )
    after = await timeline(session, project_id, scene_id)
    if after["aspect"] != aspect:
        raise RuntimeError(
            f"clicked Toggle aspect ratio once; the listing still reads {after['aspect']} for scene {scene_id}"
        )
    return {"scene_id": scene_id, "aspect": aspect, "changed": True, "rpcids": ["BpMsoe"]}


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
        box = await _title_box(page)

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


async def _holds_a_clip(page: Any) -> bool:
    """Flow's own judgment about whether the editor has a scene to work with.

    Measured 2026-09-16: on a freshly loaded page the timeline thumbnails are not restored, so counting them says
    nothing, while 'Download scene' stays disabled until the editor really holds a clip.
    """
    button = page.get_by_role("button", name=re.compile("Download scene", re.IGNORECASE))
    matching = await button.count()
    if matching != 1:
        raise LookupError(f"{matching} Download scene buttons on the scene page; not guessing")
    return not await button.first.is_disabled()


async def _wait_until_it_holds_a_clip(page: Any, scene_id: str, complaint: str) -> int:
    waited = 0
    while not await _holds_a_clip(page):
        if waited >= SAVE_WAIT_MS:
            raise LookupError(f"{complaint} (scene {scene_id}, waited {waited // 1000}s)")
        await page.wait_for_timeout(2_000)
        waited += 2_000
    return waited


async def _label_of(element: Any) -> str:
    """A control's accessible name AND its text: these buttons carry the name in aria-label and only a ligature as
    text, so reading one of the two leaves the guard below blind exactly where the match was made on the other."""
    aria = await element.get_attribute("aria-label")
    text = await element.inner_text()
    return " ".join(part for part in (aria, text) if part)


async def _one_of(page: Any, found: Any, complaint: str) -> Any:
    """Wait for exactly one match, then hand it over, or refuse rather than guess between several.

    Playwright's click waits for its element; count() does not, it is one query with no retry. These controls arrive
    in stages: measured 2026-09-16, a scene that already held a clip crossed the toolbar count while the timeline's
    own Add clip button was still missing, and a driver that only counted read 0 and gave up.
    """
    waited = 0
    while (matching := await found.count()) != 1:
        if waited >= CONTROL_WAIT_MS:
            raise LookupError(complaint.replace("{count}", str(matching)))
        await page.wait_for_timeout(1_000)
        waited += 1_000
    return found.first


async def _click_one(page: Any, found: Any, what: str, *, guard_paid: bool = True) -> str:
    """Click the single control that matches, and never one that spends: paid actions sit right beside these.

    `guard_paid` is off for the media rows of the picker: a row is a clip, not a control, and its label is a title
    the owner chose, so refusing "robot generates a sandwich" as a spender was both wrong and unavoidable.
    """
    element = await _one_of(page, found, "{count} controls match " + what + "; not guessing")
    label = await _label_of(element)
    if guard_paid and any(word in label.lower() for word in PAID_WORDS):
        raise RuntimeError(f"refusing to click {label!r} for {what}: that control spends credits")
    await element.click(timeout=8_000)
    return label


async def _title_box(page: Any) -> Any:
    """The scene's own title, and only it.

    Measured 2026-09-16: a scene page holds exactly one `flow-editable-text`, inside `flow-editor-header`, and the
    composer's prompt box is not one. Typing into `.first` of a wider selector would be a guess on a page whose
    buttons include Start generation.
    """
    return await _one_of(
        page,
        page.locator(TITLE_BOX),
        "{count} editable titles on the page; not guessing which one to type into",
    )


async def rename(session: FlowSession, project_id: str, scene_id: str, title: str) -> dict[str, Any]:
    """Rename a scene through the header's editable text, confirmed by the listing (rpc BpMsoe)."""
    if not _tile_text(title):
        raise ValueError(f"a scene title must hold visible text, got {title!r}")
    page = session.page
    await _open_scene(session, project_id, scene_id)
    box = await _title_box(page)

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


async def _page_agrees(page: Any, expected: int, scene_id: str) -> Any:
    """The timeline's clips, once the page shows as many as the listing holds: clips carry no id on the page, so a
    position means the same clip on both only when the counts agree."""
    clips = page.locator(CLIPS)
    waited = 0
    while (shown := await clips.count()) != expected:
        if waited >= CONTROL_WAIT_MS:
            raise LookupError(
                f"the timeline shows {shown} clips while Flow lists {expected} for scene {scene_id}; not guessing "
                "which clip is which"
            )
        await page.wait_for_timeout(1_000)
        waited += 1_000
    return clips


async def _select_clip(page: Any, clips: Any, index: int, scene_id: str) -> None:
    """Select one clip by clicking it; a locator click scrolls it into view first (measured 2026-09-17: a click at an
    off screen coordinate selected nothing, a locator click on the last of five selected it)."""
    clip = clips.nth(index)
    await clip.click(timeout=8_000)
    await page.wait_for_timeout(1_000)
    marks = (await clip.get_attribute("class") or "").split()
    if "selected" not in marks or await page.locator(f"{CLIPS}.selected").count() != 1:
        raise LookupError(
            f"clicking clip {index} of scene {scene_id} did not select it; not guessing where Flow would put the change"
        )


async def add_clip(session: FlowSession, project_id: str, scene_id: str, media_id: str) -> dict[str, Any]:
    """Put one of the project's clips at the END of a scene's timeline, confirmed by the listing.

    Picker rows carry no media id, only the media's title, and titles do collide in this account, so the media is
    resolved to a title no other media shares. Measured 2026-09-17 (probes/scene_editor.py): a new clip goes right
    after the selected clip and a loaded page selects clip 0, so the last clip is selected first; clicking a row only
    selects it, clicking the row already selected adds it, and 'Add media' adds the selected row. The page shows the
    clip at once, yet Flow stores it only when its reply to oWTRd comes back 11 to 14 s later, so the add is judged by
    that request, that reply and the listing read afterwards, never by the page.
    """
    page = session.page
    listing = await _listing(session, project_id)
    media = parsers.media(listing)
    item = next((m for m in media if m.get("id") == media_id), None)
    if item is None:
        raise LookupError(f"media {media_id} is not in the project listing")
    title = item.get("title")
    if not _tile_text(title):
        raise LookupError(
            f"media {media_id} has no title to match ({title!r}), and picker rows carry no media id, so adding "
            "it would guess"
        )
    # Judge a name clash on the listing, not on the rows one tab happens to show: the picker is tabbed (Videos,
    # Uploads, Upload media), so two media sharing a title can appear as a single row and the click would be a coin
    # flip that still reports the media_id that was asked for (review 2026-09-16).
    namesakes = sorted(m["id"] for m in media if _tile_text(m.get("title")) == _tile_text(title))
    if len(namesakes) > 1:
        raise RuntimeError(
            f"{len(namesakes)} media in this project are titled {title!r} ({', '.join(namesakes)}); picker rows "
            "carry no media id, so adding would guess which one"
        )
    before = timeline_from_listing(listing, scene_id)["clips"]
    await _open_scene(session, project_id, scene_id)
    clips = await _page_agrees(page, len(before), scene_id)
    if before:
        await _select_clip(page, clips, len(before) - 1, scene_id)
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
    rows = page.locator(ROW).filter(has=page.get_by_text(title, exact=True))
    waited = 0
    while (matching := await rows.count()) == 0 and waited < CONTROL_WAIT_MS:
        await page.wait_for_timeout(1_000)
        waited += 1_000
    if matching == 0:
        raise LookupError(f"media {media_id} titled {title!r} is not offered by the picker")
    if matching > 1:
        raise RuntimeError(
            f"{matching} picker rows show {title!r}; rows carry no media id, so adding would guess"
        )
    if await rows.first.get_attribute("aria-selected") != "true":
        await rows.first.click(timeout=8_000)
        await page.wait_for_timeout(1_000)
    chosen = page.locator(f"{ROW}[aria-selected=true]")
    if await chosen.count() != 1 or await chosen.filter(has=page.get_by_text(title, exact=True)).count() != 1:
        raise RuntimeError(f"the picker did not select the one row titled {title!r}; not adding")
    add = page.locator(OVERLAY).filter(has_text=re.compile(r"^\s*Add media\s*$", re.IGNORECASE))
    with _Calls(page, ("oWTRd", "GoMJte")) as calls:
        await _click_one(page, add, "the Add media button")
        sent = await calls.wait(calls.sent, "oWTRd", SEND_WAIT_MS)
        if not sent:
            raise RuntimeError(
                f"clicked Add media once and no add request left the page within {SEND_WAIT_MS // 1000}s; read "
                f"scene_clips before adding again (scene {scene_id})"
            )
        heard = await calls.wait(calls.heard, "oWTRd", REPLY_WAIT_MS)
        if heard:
            # The page saves the whole ordered list right after the reply; leaving before it lands is not measured.
            await calls.wait(calls.heard, "GoMJte", SEND_WAIT_MS)
    if not heard:
        raise RuntimeError(
            f"Flow never answered the add of media {media_id} within {REPLY_WAIT_MS // 1000}s: the clip may still "
            f"land, so read scene_clips before adding again (scene {scene_id})"
        )
    after = timeline_from_listing(await _listing(session, project_id), scene_id)
    titles = [clip["title"] for clip in after["clips"]]
    if parsers._at(sent[0], 2) != [media_id]:
        raise RuntimeError(
            f"the add Flow received names media {parsers._at(sent[0], 2)} instead of {media_id}; the listing reads "
            f"{titles}: remove the wrong clip with scene_remove_clip, do not add it again"
        )
    added = parsers._at(heard[0], 0, 0, 0, 0)
    position = next((clip["position"] for clip in after["clips"] if clip["clip_id"] == added), None)
    if position is None:
        raise RuntimeError(
            f"Flow answered the add with clip {added} but the listing reads {titles} without it; read scene_clips "
            f"before adding again (scene {scene_id})"
        )
    others = [clip["clip_id"] for clip in after["clips"] if clip["clip_id"] != added]
    if position != len(before) or others != [clip["clip_id"] for clip in before]:
        raise RuntimeError(
            f"clip {added} landed at position {position} instead of {len(before)}; the listing reads {titles}: use "
            "scene_move_clip to move it, do not add it again"
        )
    return {
        "scene_id": scene_id,
        "media_id": media_id,
        "title": title,
        "position": position,
        "clip_id": added,
        "seconds": after["seconds"],
        "clips": after["clips"],
        "rpcids": sorted({*calls.sent, *calls.heard}),
    }


async def download(session: FlowSession, project_id: str, scene_id: str, *, out_dir: Path) -> dict[str, Any]:
    """Hand back the scene as one film.

    Measured 2026-09-16: unlike the clip editor, a scene has no quality menu. 'Download scene' downloads straight
    away, and the file is the whole assembled timeline (two 8 s clips came back as one 16.0 s mp4).
    """
    page = session.page
    await _open_scene(session, project_id, scene_id)
    await _wait_until_it_holds_a_clip(page, scene_id, "there is no clip on this scene's timeline to download")
    # Same as Add clip: the label is the aria-label, the text is just the ligature "download".
    button = page.get_by_role("button", name=re.compile("Download scene", re.IGNORECASE))
    async with page.expect_download(timeout=DOWNLOAD_WAIT_MS) as info:
        await _click_one(page, button, "the Download scene button")
    handed = await info.value
    # Stamped, because downloading the same scene again after adding a clip is the normal way to work and a name
    # made of the scene id alone collides with the film taken a minute earlier (CLAUDE.md rule 5: never overwrite).
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = Path(out_dir) / f"{scene_id}_{stamp}{Path(handed.suggested_filename).suffix or '.mp4'}"
    if target.exists():
        raise FileExistsError(target)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    await handed.save_as(str(target))
    return {
        "scene_id": scene_id,
        "path": str(target),
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
