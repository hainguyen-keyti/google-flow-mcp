"""Scenes (Scenebuilder) on the migrated host (measured 2026-09-12): 'Add media' > 'New scene' creates a
scene (rpc rqZuUc) and opens /project/<id>/scene/<scene_id>. Scenes are listed in Zzl0ze[4]; the grid tile's
'More options' > 'Move to trash' (rpc BpMsoe) keeps the entry listed with the trashed flag set and shows it
under the project's Trash view. The scene editor's own 'Move to trash' button did nothing in two runs."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qs

from gflow_cli.api.transports.batchexecute import parse_frames
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import clips as clips_mod
from video.flow import parsers
from video.flow.reader import capture, one
from video.session import PROJECT_READY, FlowSession

_SCENE_RE = re.compile(r"/scene/([A-Za-z0-9-]+)")
BUILDER = "flow-scene-builder"
TILE_WAIT_MS = 15_000
TILE_STEP_MS = 1_000
# The trash of the draft project swept in 11 windows; this is the backstop that keeps a stuck view from hanging a call.
TILE_SWEEP_STEPS = 60
TOOLBAR_MIN = 20
TOOLBAR_WAIT_MS = 15_000
CONTROL_WAIT_MS = 10_000
SAVE_WAIT_MS = 20_000
# Measured 2026-09-17 (probes/scene_editor.py): 'Download scene' builds the film inside the page. Within 2.5 s the
# button turns into a spinner and a snackbar reads 'Exporting your scene…'; a 40 s film took 33.5 s and 41.5 s on a
# heavily loaded machine, then 'Your scene has been downloaded!' and the file 2 s later. So a click that starts no
# export is failed fast, and a running export is waited for as long as its length warrants.
SNACKBAR = "mat-snack-bar-container"
EXPORT_START_MS = 20_000
EXPORT_MIN_MS = 180_000
EXPORT_MS_PER_SECOND = 10_000
HANDOFF_WAIT_MS = 30_000
OVERLAY = ".cdk-overlay-pane button, [role=dialog] button"
ROW = ".cdk-overlay-pane button[role=option]"
CLIPS = ".timeline-contents > .clip"
MENU_ITEM = "[role=menuitem]"
# Measured 2026-09-17: five 8 s clips at 816 px each ran far past a 1280 px wide page; two zoom-outs fit all five.
ZOOM_OUT_MAX = 4
# Measured 2026-09-17 (probes/scene_editor.py) on a heavily loaded machine: every request a scene click fires left the
# page within 0.4 s, while Flow's reply to an add came 11 to 14 s later and nothing is stored until it does. The wait is
# far longer than that because two adds of the same run passed 90 s with no reply at all, one of them having landed.
SEND_WAIT_MS = 15_000
REPLY_WAIT_MS = 180_000
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


def _only_scene_titled(scenes: list[dict[str, Any]], title: str, trashed: bool, where: str) -> None:
    """Which scene a tile belongs to is decided by the LISTING, which knows every scene, not by the handful of tiles
    the view has rendered: measured 2026-09-17, the grid renders 7 tiles of 8 and the trash 16 of 37."""
    same = [s for s in scenes if s["trashed"] is trashed and _tile_text(s["title"]) == _tile_text(title)]
    if len(same) > 1:
        state = "trashed" if trashed else "active"
        raise RuntimeError(
            f"{len(same)} {state} scenes are titled {title!r}; {where} tiles carry no scene id, so this would guess"
        )


async def _scroll_to_tile(page: Any, tiles: Any, match: Any, title: str, where: str) -> Any:
    """The one rendered tile with that exact title, scrolling the virtual view until it renders.

    Measured 2026-09-17 (Plan H T1): the grid and the trash share one div.cdk-virtual-scrollable, tiles render a
    window at a time and a far one is dropped again, so a view is swept by bringing its last rendered tile into
    view until nothing new arrives. That works because the rendered window runs well past the visible area (31 to
    50 containers over a 720 px viewport), which is what makes the last rendered tile a step forward.

    Two tiles showing the title is still a refusal, but only among the tiles rendered together: a namesake the
    listing does not know, sitting in another window, is caught after the click instead, by the listing read that
    would name the scene wrongly moved."""
    windows: set[tuple[str, ...]] = set()
    waited_ms = 0
    for _ in range(TILE_SWEEP_STEPS):
        found = await match.count()
        if found > 1:
            raise RuntimeError(
                f"{found} {where} tiles show {title!r}; tiles carry no scene id, so this would guess"
            )
        if found == 1:
            return match.first
        rendered = tuple(await tiles.all_text_contents())
        if rendered and rendered not in windows:
            windows.add(rendered)
            await tiles.last.scroll_into_view_if_needed(timeout=8_000)
        elif waited_ms >= TILE_WAIT_MS:
            raise LookupError(f"scene tile titled {title!r} not found on the {where}")
        else:
            waited_ms += TILE_STEP_MS
        await page.wait_for_timeout(TILE_STEP_MS)
    # A view that keeps answering with new windows must still end somewhere, or a call hangs for good.
    raise LookupError(f"scene tile titled {title!r} not found in {TILE_SWEEP_STEPS} steps of the {where}")


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
    # Listening from before the first click: an add that leaves before the one deliberate 'Add media' click, like a click
    # on a row the picker had already selected, must not pass unnoticed.
    with _Calls(page, ("oWTRd", "GoMJte")) as calls:

        def no_add_yet(after: str) -> None:
            if "oWTRd" in calls.sent:
                raise RuntimeError(
                    f"an add request left the page after {after}, before Add media was clicked, naming media "
                    f"{parsers._at(calls.sent['oWTRd'][0], 2)}; read scene_clips before adding again (scene {scene_id})"
                )

        # By accessible name, not by text: measured 2026-09-16, this button's text is only the ligature "add_2" while
        # "Add clip" lives in its aria-label, so a text match finds nothing at all (live run of scene_build).
        await _click_one(
            page,
            page.get_by_role("button", name=re.compile("Add clip", re.IGNORECASE)),
            "the Add clip button",
        )
        await page.wait_for_timeout(1_500)
        # A scene holding clips answers with a menu first (measured 2026-09-17). Its items are menuitems; an empty scene
        # opens the picker straight away, whose rows are buttons in the same pane and can carry "add clip" in a title.
        menu = page.locator(MENU_ITEM).filter(has_text=re.compile("Add clip", re.IGNORECASE))
        if await menu.count():
            await _click_one(page, menu, "the Add clip menu item")
            await page.wait_for_timeout(2_000)
        no_add_yet("the Add clip button")
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
            no_add_yet(f"the picker row titled {title!r}")
        chosen = page.locator(f"{ROW}[aria-selected=true]")
        if (
            await chosen.count() != 1
            or await chosen.filter(has=page.get_by_text(title, exact=True)).count() != 1
        ):
            raise RuntimeError(f"the picker did not select the one row titled {title!r}; not adding")
        no_add_yet("the picker opened")
        add = page.locator(OVERLAY).filter(has_text=re.compile(r"^\s*Add media\s*$", re.IGNORECASE))
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
    after = timeline_from_listing(await _listing(session, project_id), scene_id)
    titles = [clip["title"] for clip in after["clips"]]
    if parsers._at(sent[0], 2) != [media_id]:
        raise RuntimeError(
            f"the add Flow received names media {parsers._at(sent[0], 2)} instead of {media_id}; the listing reads "
            f"{titles}: remove the wrong clip with scene_remove_clip, do not add it again"
        )
    added = parsers._at(heard[0], 0, 0, 0, 0) if heard else None
    if added is None:
        # Measured 2026-09-17 (acceptance run 3): two adds passed the reply wait with nothing heard and one of them had
        # landed all the same, so the listing decides, not the silence.
        fresh = [
            clip for clip in after["clips"] if clip["clip_id"] not in {each["clip_id"] for each in before}
        ]
        if len(fresh) != 1 or fresh[0]["title"] != title:
            raise RuntimeError(
                f"Flow never answered the add of media {media_id} within {REPLY_WAIT_MS // 1000}s and the listing "
                f"reads {titles}; read scene_clips before adding again (scene {scene_id})"
            )
        added = fresh[0]["clip_id"]
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
        "answered": heard is not None,
        "seconds": after["seconds"],
        "clips": after["clips"],
        "rpcids": sorted({*calls.sent, *calls.heard}),
    }


def _saved_order(payload: Any) -> list[Any]:
    """The clip ids a GoMJte request stores, in the order of their index (null for 0)."""
    entries = parsers._at(payload, 2) or []
    indexed = [(parsers._at(entry, 2, 0) or 0, parsers._at(entry, 0, 0)) for entry in entries]
    return [clip_id for _, clip_id in sorted(indexed, key=lambda pair: pair[0])]


async def _store_order(
    session: FlowSession, project_id: str, scene_id: str, act: Any, what: str, expected: list[str]
) -> dict[str, Any]:
    """Do the one action that reorders a timeline, then judge it by the GoMJte it sent, Flow's answer and the listing.

    Measured 2026-09-17: deleting a clip and dragging one both make the page send GoMJte with every clip that stays,
    in its new order, and Flow keeps that list once it answers.
    """
    page = session.page
    with _Calls(page, ("GoMJte",)) as calls:
        await act()
        sent = await calls.wait(calls.sent, "GoMJte", SEND_WAIT_MS)
        if not sent:
            raise RuntimeError(
                f"{what} once and no timeline change left the page within {SEND_WAIT_MS // 1000}s; read scene_clips "
                f"before trying again (scene {scene_id})"
            )
        if _saved_order(sent[-1]) != expected:
            raise RuntimeError(
                f"{what} once and the timeline sent to Flow reads {_saved_order(sent[-1])} instead of {expected}; "
                f"read scene_clips before changing it again (scene {scene_id})"
            )
        heard = await calls.wait(calls.heard, "GoMJte", REPLY_WAIT_MS)
    if not heard:
        raise RuntimeError(
            f"Flow never answered the timeline change within {REPLY_WAIT_MS // 1000}s: it may still land, so read "
            f"scene_clips before trying again (scene {scene_id})"
        )
    after = await timeline(session, project_id, scene_id)
    if [clip["clip_id"] for clip in after["clips"]] != expected:
        titles = [clip["title"] for clip in after["clips"]]
        raise RuntimeError(
            f"Flow answered, but the listing reads {titles} instead of the order sent; read scene_clips before "
            f"changing it again (scene {scene_id})"
        )
    return after


async def remove_clip(session: FlowSession, project_id: str, scene_id: str, clip_id: str) -> dict[str, Any]:
    """Take one clip, named by its clip_id from the listing, off a scene's timeline.

    Measured 2026-09-17: right-clicking a clip selects it and opens Copy, Paste, Save to Project, Download and Delete,
    and Delete asks nothing. A clip carries no id on the page, so it is found at the position the listing gives it,
    once the page shows as many clips as the listing holds.
    """
    page = session.page
    before = await timeline(session, project_id, scene_id)
    ids = [clip["clip_id"] for clip in before["clips"]]
    if clip_id not in ids:
        raise LookupError(
            f"clip {clip_id} is not on scene {scene_id}'s timeline; scene_clips lists its clip_ids"
        )
    index = ids.index(clip_id)
    await _open_scene(session, project_id, scene_id)
    clips = await _page_agrees(page, len(ids), scene_id)
    clip = clips.nth(index)
    await clip.click(button="right", timeout=8_000)
    await page.wait_for_timeout(1_000)
    marks = (await clip.get_attribute("class") or "").split()
    if "selected" not in marks or await page.locator(f"{CLIPS}.selected").count() != 1:
        raise LookupError(f"right-clicking clip {index} of scene {scene_id} did not select it; not deleting")
    # An item's text runs its icon ligature into its label ("deleteDelete", measured 2026-09-17).
    delete = page.locator(MENU_ITEM).filter(has_text=re.compile(r"Delete\s*$"))
    expected = [each for each in ids if each != clip_id]
    after = await _store_order(
        session,
        project_id,
        scene_id,
        lambda: _click_one(page, delete, "the Delete item of the clip menu"),
        "clicked Delete",
        expected,
    )
    return {
        "scene_id": scene_id,
        "clip_id": clip_id,
        "removed_position": index,
        "seconds": after["seconds"],
        "clips": after["clips"],
        "rpcids": ["GoMJte"],
    }


async def move_clip(
    session: FlowSession, project_id: str, scene_id: str, clip_id: str, position: int
) -> dict[str, Any]:
    """Move one clip, named by its clip_id from the listing, to a position counted from 0.

    Measured 2026-09-17: Flow reorders a timeline only by dragging a clip; a drag of clip 1 onto clip 3, in 15 mouse
    steps with both clips on screen, sent GoMJte with clip 1 at index 3. Zooming out halves a clip's width and
    survives a reload.
    """
    page = session.page
    before = await timeline(session, project_id, scene_id)
    ids = [clip["clip_id"] for clip in before["clips"]]
    if clip_id not in ids:
        raise LookupError(
            f"clip {clip_id} is not on scene {scene_id}'s timeline; scene_clips lists its clip_ids"
        )
    if type(position) is not int or not 0 <= position < len(ids):
        raise ValueError(f"position must be 0 to {len(ids) - 1} on scene {scene_id}, got {position!r}")
    source = ids.index(clip_id)
    if source == position:
        return {
            "scene_id": scene_id,
            "clip_id": clip_id,
            "position": position,
            "moved": False,
            "seconds": before["seconds"],
            "clips": before["clips"],
            "rpcids": [],
        }
    expected = [each for each in ids if each != clip_id]
    expected.insert(position, clip_id)
    await _open_scene(session, project_id, scene_id)
    clips = await _page_agrees(page, len(ids), scene_id)
    width = (page.viewport_size or {}).get("width") or 0
    for zoomed in range(ZOOM_OUT_MAX + 1):
        boxes = [await clips.nth(index).bounding_box() for index in (source, position)]
        if all(box and box["x"] >= 0 and box["x"] + box["width"] <= width for box in boxes):
            break
        if zoomed == ZOOM_OUT_MAX:
            raise LookupError(
                f"clips {source} and {position} of scene {scene_id} are still off screen after {ZOOM_OUT_MAX} "
                "zoom-outs; not dragging blind"
            )
        await _click_one(
            page, page.get_by_role("button", name=re.compile("Zoom out", re.IGNORECASE)), "Zoom out"
        )
        await page.wait_for_timeout(800)
    # Pressed in the clip's right quarter: the selected clip's Add clip button covers 5 to 29 px of the next clip's left
    # edge (measured 2026-09-17), which is the middle of a clip once four zoom-outs make it 51 px wide.
    start = (boxes[0]["x"] + boxes[0]["width"] * 0.75, boxes[0]["y"] + boxes[0]["height"] / 2)
    # Past the middle of the clip whose place it takes, on the side it comes from, as the measured drag did.
    nudge = 12 if position > source else -12
    end = (boxes[1]["x"] + boxes[1]["width"] / 2 + nudge, boxes[1]["y"] + boxes[1]["height"] / 2)

    async def drag() -> None:
        await page.mouse.move(*start)
        await page.mouse.down()
        await page.wait_for_timeout(300)
        for step in range(1, 16):
            await page.mouse.move(
                start[0] + (end[0] - start[0]) * step / 15, start[1] + (end[1] - start[1]) * step / 15
            )
            await page.wait_for_timeout(60)
        await page.wait_for_timeout(500)
        await page.mouse.up()

    after = await _store_order(
        session, project_id, scene_id, drag, f"dragged clip {source} to {position}", expected
    )
    return {
        "scene_id": scene_id,
        "clip_id": clip_id,
        "position": position,
        "moved": True,
        "seconds": after["seconds"],
        "clips": after["clips"],
        "rpcids": ["GoMJte"],
    }


async def _export_state(page: Any) -> str:
    """What the page says about a scene export: exported, exporting or idle (measured 2026-09-17)."""
    if await page.locator(SNACKBAR).filter(has_text=re.compile("has been downloaded", re.IGNORECASE)).count():
        return "exported"
    button = page.get_by_role("button", name=re.compile("Download scene", re.IGNORECASE))
    if await button.count() == 1 and await button.first.is_disabled():
        return "exporting"
    if (
        await page.locator(SNACKBAR)
        .filter(has_text=re.compile("Exporting your scene", re.IGNORECASE))
        .count()
    ):
        return "exporting"
    return "idle"


async def download(session: FlowSession, project_id: str, scene_id: str, *, out_dir: Path) -> dict[str, Any]:
    """Hand back the scene as one film, the whole timeline assembled by Flow.

    Measured 2026-09-16: a scene has no quality menu, and two 8 s clips came back as one 16.0 s mp4. Measured
    2026-09-17: the film is built inside the page and handed over from a blob URL. The finished file is copied off
    disk under a temporary name and renamed once whole: Playwright's Python save_as streams it in 1 MiB chunks through
    the browser connection, and the dancer test's failed run left a film of exactly 18 MiB under its final name.
    """
    page = session.page
    scene = await timeline(session, project_id, scene_id)
    if not scene["clips"]:
        raise LookupError(
            f"there is no clip on scene {scene_id}'s timeline to download; scene_add_clip puts one there"
        )
    # Stamped, because downloading the same scene again after adding a clip is the normal way to work and a name
    # made of the scene id alone collides with the film taken a minute earlier (CLAUDE.md rule 5: never overwrite).
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = Path(out_dir) / f"{scene_id}_{stamp}.mp4"
    if target.exists():
        raise FileExistsError(target)
    await _open_scene(session, project_id, scene_id)
    await _wait_until_it_holds_a_clip(
        page, scene_id, "the editor never offered Download scene for a scene with clips"
    )
    # Same as Add clip: the label is the aria-label, the text is just the ligature "download".
    button = page.get_by_role("button", name=re.compile("Download scene", re.IGNORECASE))
    limit = max(EXPORT_MIN_MS, int(scene["seconds"] * EXPORT_MS_PER_SECOND))
    # Twice the loop's own limits, which count only its sleeps: on a slow page Playwright's timeout would otherwise fire
    # first and turn a stuck export into a retried "waiting for event" (review 2026-09-17).
    async with page.expect_download(timeout=2 * (EXPORT_START_MS + limit + HANDOFF_WAIT_MS)) as info:
        await _click_one(page, button, "the Download scene button")
        waited, started, exported = 0, False, None
        while not info.is_done():
            state = await _export_state(page)
            started = started or state != "idle"
            if state == "exported" and exported is None:
                exported = waited
            if not started and waited >= EXPORT_START_MS:
                raise RuntimeError(
                    f"clicked Download scene once and the page started no export within {EXPORT_START_MS // 1000}s "
                    f"(scene {scene_id})"
                )
            if exported is not None and waited - exported >= HANDOFF_WAIT_MS:
                raise RuntimeError(
                    f"the page says scene {scene_id} was downloaded but no file reached this browser within "
                    f"{HANDOFF_WAIT_MS // 1000}s"
                )
            if waited >= EXPORT_START_MS + limit:
                raise RuntimeError(
                    f"the export of scene {scene_id} ({scene['seconds']} s) did not finish within "
                    f"{(EXPORT_START_MS + limit) // 1000}s"
                )
            await page.wait_for_timeout(2_000)
            waited += 2_000
    handed = await info.value
    try:
        local = Path(await handed.path())
    except Exception as exc:
        raise RuntimeError(
            f"the film of scene {scene_id} failed in the browser ({type(exc).__name__}: {str(exc)[:120]})"
        ) from exc
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    try:
        await asyncio.to_thread(shutil.copyfile, local, partial)
        if partial.stat().st_size != local.stat().st_size:
            raise OSError(f"copied {partial.stat().st_size} of {local.stat().st_size} bytes of {local}")
        if target.exists():
            raise FileExistsError(target)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return {
        "scene_id": scene_id,
        "path": str(target),
        "suggested": handed.suggested_filename,
        "bytes": target.stat().st_size,
        "seconds": scene["seconds"],
        "clips": scene["clips"],
    }


async def delete(session: FlowSession, project_id: str, scene_id: str) -> dict[str, Any]:
    """Move a scene to the project's trash; verified by the trashed flag in the listing.

    A grid tile carries no scene id (2026-09-16) and the grid renders only a window of tiles (2026-09-17), so the
    listing decides that one active scene wears this title and the view is swept until its tile renders."""
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
    _only_scene_titled(scenes, title, False, "grid")
    tiles = page.locator("flow-tile-container")
    scene_tiles = tiles.filter(has=page.locator("flow-scene-tile"))
    match = scene_tiles.filter(has=page.get_by_text(title, exact=True))
    tile = await _scroll_to_tile(page, tiles, match, title, "grid")
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
    Restore and Delete permanently, and Restore fires BpMsoe with no confirm dialog. The tile can only be found by
    its exact title, so a scene with no title, or a title more than one trashed scene wears, is refused instead of
    guessed; the trash renders a window at a time (16 tiles of 37 on 2026-09-17), so it is swept, not counted."""
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
    _only_scene_titled(scenes, title, True, "trash")
    await session.goto(f"{session.project_url(project_id)}/trash", ready=PROJECT_READY)
    tiles = page.locator("flow-tile-container")
    scene_tiles = tiles.filter(has=page.locator("flow-scene-tile"))
    match = scene_tiles.filter(has=page.get_by_text(title, exact=True))
    tile = await _scroll_to_tile(page, tiles, match, title, "trash")
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


async def save_clip_to_project(
    session: FlowSession, project_id: str, scene_id: str, clip_id: str
) -> dict[str, Any]:
    """Copy one clip off a scene's timeline onto the project grid, so a later scene can use it as media.

    Measured 2026-09-18: the clip's right-click menu holds Save to Project, the click fires rpc Sc7aEb with
    [clip_id, None, None, project_id], and the grid gains a media of its own. The timeline is not touched. Free.
    The listing lags behind the click, which is why the new media is waited for rather than read once.
    """
    page = session.page
    before = await timeline(session, project_id, scene_id)
    ids = [clip["clip_id"] for clip in before["clips"]]
    if clip_id not in ids:
        raise LookupError(
            f"clip {clip_id} is not on scene {scene_id}'s timeline; scene_clips lists its clip_ids"
        )
    index = ids.index(clip_id)
    known = await clips_mod.media_ids(session, project_id)
    await _open_scene(session, project_id, scene_id)
    clips = await _page_agrees(page, len(ids), scene_id)
    clip = clips.nth(index)
    await clip.click(button="right", timeout=8_000)
    await page.wait_for_timeout(1_000)
    marks = (await clip.get_attribute("class") or "").split()
    if "selected" not in marks or await page.locator(f"{CLIPS}.selected").count() != 1:
        raise LookupError(f"right-clicking clip {index} of scene {scene_id} did not select it; not saving")
    # The item's text runs its icon ligature into the label, as Delete's does ("saveSave to Project").
    item = page.locator(MENU_ITEM).filter(has_text=re.compile(r"Save to Project\s*$"))
    await _click_one(page, item, "the Save to Project item of the clip menu")
    await page.wait_for_timeout(2_000)
    saved = await clips_mod.wait_for_new_media(
        session,
        project_id,
        known,
        what="scene_save_clip",
        wants=lambda row: row.get("kind") == "video",
    )
    return {
        "scene_id": scene_id,
        "clip_id": clip_id,
        "position": index,
        "media_id": saved["id"],
        "kind": saved.get("kind"),
        "title": saved.get("title"),
        "rpcids": ["Sc7aEb"],
    }
