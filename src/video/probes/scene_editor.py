"""Plan E T9 (widened 2026-09-17): the scene editor as it is today, measured on a probe scene. $0.

The dancer test (out/dancer_test/report_kt1.md, step 4) found the picker changed: clicking a row only selects it and
'Add media' adds it, a new clip lands right after the selected clip rather than at the end, 'Toggle aspect ratio'
flips 16:9 and 9:16, and a 40 s film failed to download twice with TargetClosedError. This probe measures each of
those, plus how a clip leaves a scene and how clips are reordered, so the driver is written against numbers.

Actions run in the order given, in ONE browser session, and the report is rewritten after every action, so a browser
that dies mid-run keeps what was measured before it. No action clicks a control whose label names a paid word, and no
action repeats a click it has not measured: each one clicks its deciding control exactly once.

    uv run python -m video.probes.scene_editor --project <id> (--scene <scene_id> | --create <title>) --do <action> ...

Actions: open, picker, add=<media_id> (reopens the scene first), addhere=<media_id> (adds on the page as it is),
timeline, select=<index>, skip=<next|previous>, zoom=<out|in>, aspect, menu=<index>, remove=<index>:<menu item>,
move=<from>:<to>, download, trash, grid (read only: scene tiles on the project grid). Indexes count clips on the
timeline from 0.

Measured with this probe on 2026-09-17, five passes on scene pe-scene-1624 (376cd4f9), balance 149 before:
- The listing holds every scene's clips, in order: Zzl0ze[6] entries read [[clip_id, _, _, [title, created, _, _,
  clip_workflow_id, _, updated], project_id], scene_id, [index or null for 0, [length], [start], [end], [1]]]. The
  order matched the downloaded film frame by frame, and the dancer scenes (8c6c91da three clips, cae71b29 five).
  Zzl0ze[4] entry [5] is the scene's aspect ratio, 1 for 9:16 and 2 for 16:9, not a status.
- 'Add clip' on a scene that already holds a clip opens a menu first, Add clip and Extend (Veo 3.1 - Lite), as on
  2026-09-16; an empty scene opens the picker at once.
- The picker is a dialog of rows (button[role=option], newest video first) and the first row is already selected
  (aria-selected, asset-item-active). Clicking a row only selects it; its detail pane holds one 'Add media' button.
- 'Add media' fires oWTRd [project, scene, [media_id], index] within 0.4 s, index being the selected clip's plus one
  (0 on an empty scene). The Total duration label moves within 1.3 s, before Flow has stored anything; the oWTRd
  reply, carrying the new clip id, came 11 to 14 s later, then GoMJte sent the whole ordered clip list.
- A loaded page selects clip 0, and the 'Add clip' button sits inside the selected clip only. A locator click
  scrolls a clip into view and selects it, after which the add landed at index 4 of 4, the end; a mouse click at an
  off screen coordinate selected nothing.
- 'Toggle aspect ratio' fires BpMsoe [project, scene, [scene_id, _, _, _, _, 1], [["aspect_ratio"]]] and the label,
  the listing and a reloaded page all read 9:16.
- Right-clicking a clip selects it and opens Copy, Paste, Save to Project, Download, Delete; an item's text runs the
  icon ligature into the label ("deleteDelete"). Delete asks nothing and sends GoMJte with the clips that remain.
- Dragging a clip (15 mouse steps, after two zoom-outs: 816 to 204 px per 8 s clip) sends GoMJte in the new order.
  The zoom survives a reload.
- 'Download scene' builds the film inside the page: the button turns to a spinner, a snackbar reads 'Exporting your
  scene…', then 'Your scene has been downloaded!', and the file comes from a blob URL. A 40 s film took 33.5 s and
  41.5 s to the download event, 36.5 MB, 40.000 s, no page or context closed. Playwright's Python save_as streams
  the file in 1 MiB chunks (playwright/_impl/_artifact.py save_as uses saveAsStream), which fits the film of exactly
  18 MiB the dancer test's failed run left under its final name. What closed that browser was not reproduced.

Plan H T1 measured with gridscan and media on 2026-09-17, project 118aece2 (8 active scenes, 37 trashed), $0:
- The project grid and the trash share one scroller, div.cdk-virtual-scrollable.page-container (the trash adds
  has-trash-action-bar), 720 px tall over a 4419 to 6250 px page, and it VIRTUALIZES: at the top the grid rendered
  7 scene tiles of 8 and the trash 16 of 37, which is why delete and restore, waiting for one tile per listed
  scene, refuse every project with more scenes than one screen holds.
- A tile's offset inside the scroller (scrollTop + its box, rounded) is stable across scrolls and unique per tile:
  the grid collected 8 distinct offsets for 8 active scenes and the trash 37 for 37 trashed, with the grid's 8th
  tile appearing only from scrollTop 1728. Rows sit 293 px apart, the trash two tiles per row (left 16 and 524).
- Scrolling in steps of 80 % of the viewport, 1.5 s apart, swept the whole grid in 9 steps (26 s) and the trash in
  11 (29 s). scrollHeight GREW during the sweep (4419 to 4991, 5678 to 6250) as media tiles loaded, so the end has
  to be re-read every step, and a far tile is unrendered again (step 4 held 2 scene tiles of 8).
- A tile's text before its thumbnail loads reads 'movie_edit <title> movie', after it '<title> play_circle <n>
  add <m> movie', so a title must be matched against an element's own text, never the tile's whole text: the
  latter also makes 'dancer-test' match the tile of 'dancer-test-2'.
- flow_media on the same project: default 28.378 characters over 45 media rows, all_versions=true 122.919 over 135
  version rows (media 28.210, versions 94.527). Inside the version rows url weighs 28.205 and prompt 24.607, id,
  project_id and workflow_id 15.410 together. Cutting to the newest 20 rows alone leaves 14.804 (default) and
  40.234 (all_versions), so rows alone do not fit a client limit: the heavy FIELDS have to go too.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
import subprocess
import tempfile
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from gflow_cli.api.transports.batchexecute import parse_frames

from video.flow import reader, scenes
from video.probes._common import OUT_DIR
from video.session import FlowSession

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PROJECT_ACTIONS = ("grid", "gridscan", "media")
ROW = ".cdk-overlay-pane button[role=option]"
LABEL_POLL_S = 20.0

_TIMELINE_JS = """
() => {
  const uuids = (s) => (String(s || '').match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g) || []);
  const safe = (v) => {
    const s = String(v || '');
    if (/^(https?:|blob:|data:)/.test(s)) {
      let host = s.slice(0, 5);
      try { host = new URL(s).host || s.split(':')[0]; } catch (e) {}
      return {url_host: host, uuids: uuids(s), length: s.length};
    }
    return s.length > 160 ? s.slice(0, 160) + '...' : s;
  };
  const attrs = (e) => Object.fromEntries([...e.attributes].map(a => [a.name, safe(a.value)]));
  const rect = (e) => { const r = e.getBoundingClientRect();
    return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
  const text = (e) => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 160);
  const builder = document.querySelector('flow-scene-builder');
  const all = builder ? (builder.innerText || '') : '';
  const after = (key) => { const at = all.indexOf(key); if (at < 0) return null;
    const m = all.slice(at + key.length, at + key.length + 24).match(/[0-9:]+/); return m ? m[0] : null; };
  const list = document.querySelector('.timeline-contents');
  const items = list ? [...list.children].map((c, i) => ({
    index: i,
    tag: c.tagName.toLowerCase(),
    attrs: attrs(c),
    rect: rect(c),
    text: text(c),
    media: [...c.querySelectorAll('img, video, source, canvas')].map(m => ({
      tag: m.tagName.toLowerCase(), src: safe(m.currentSrc || m.src || ''), poster: safe(m.poster || ''),
      rect: rect(m)})),
    marked: [...c.querySelectorAll('*')].filter(d => {
      const cls = typeof d.className === 'string' ? d.className : '';
      return /select|active|current|focus|playhead/i.test(cls) || d.hasAttribute('aria-selected')
        || d.hasAttribute('aria-pressed') || d.tagName.toLowerCase().startsWith('flow-');
    }).slice(0, 20).map(d => ({tag: d.tagName.toLowerCase(), attrs: attrs(d), rect: rect(d)})),
    buttons: [...c.querySelectorAll('button')].map(b => ({aria: b.getAttribute('aria-label'), text: text(b),
      rect: rect(b)})),
  })) : null;
  const marked = [...document.querySelectorAll('flow-scene-builder *')].filter(d => {
    const cls = typeof d.className === 'string' ? d.className : '';
    return /playhead|scrubber|cursor|selected|is-active/i.test(cls);
  }).slice(0, 20).map(d => ({tag: d.tagName.toLowerCase(), cls: String(d.className).slice(0, 120), rect: rect(d)}));
  const aspect = [...document.querySelectorAll('button')].filter(b =>
    /toggle aspect ratio/i.test(b.getAttribute('aria-label') || ''));
  return {
    total_duration: after('Total duration:'),
    current_time: after('Current time:'),
    aspect: aspect.map(b => text(b)),
    select_hint: /Select a clip to edit/.test(all),
    list_attrs: list ? attrs(list) : null,
    list_rect: list ? rect(list) : null,
    items,
    marked,
  };
}
"""

_OVERLAY_JS = """
() => {
  const text = (e) => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 160);
  const rect = (e) => { const r = e.getBoundingClientRect();
    return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
  return [...document.querySelectorAll('.cdk-overlay-pane, [role=dialog], [role=menu]')].map(p => ({
    tags: [...new Set([...p.querySelectorAll('*')].map(e => e.tagName.toLowerCase())
      .filter(t => t.startsWith('flow-') || t.startsWith('mat-')))].slice(0, 30),
    rows: [...p.querySelectorAll('button[role=option]')].map(b => ({
      text: text(b), selected: b.getAttribute('aria-selected'), cls: String(b.className).slice(0, 120),
      title: text(b.querySelector('.asset-title') || b), subtitle: text(b.querySelector('.type-subtitle') || b)})),
    buttons: [...p.querySelectorAll('button, [role=menuitem]')].filter(b => b.getAttribute('role') !== 'option')
      .map(b => ({aria: b.getAttribute('aria-label'), text: text(b), role: b.getAttribute('role'),
        disabled: b.disabled || b.getAttribute('aria-disabled') === 'true', visible: !!(b.offsetWidth || b.offsetHeight),
        rect: rect(b)})),
    detail: text(p.querySelector('flow-add-menu-detail-pane') || document.createElement('i')),
    text: text(p).slice(0, 400),
  }));
}
"""


_BUSY_JS = """
() => {
  const text = (e) => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 200);
  const busy = [...document.querySelectorAll(
    '[role=progressbar], [role=status], [role=alert], mat-progress-bar, mat-spinner, mat-progress-spinner, '
    + 'mat-snack-bar-container, .cdk-overlay-pane')]
    .filter(e => e.offsetWidth || e.offsetHeight)
    .map(e => [e.tagName.toLowerCase(), e.getAttribute('role'), e.getAttribute('aria-valuenow'), text(e)]);
  const button = [...document.querySelectorAll('button')].find(b =>
    /download scene/i.test(b.getAttribute('aria-label') || ''));
  return [busy, button ? [button.disabled, text(button)] : null];
}
"""


def _shape(value: Any, depth: int = 0) -> Any:
    """A payload's structure without anything that could be a credential: URLs shrink to their host and ids."""
    if isinstance(value, str):
        if value.startswith(("http:", "https:")):
            return {"url_host": urlparse(value).netloc, "uuids": UUID.findall(value)}
        return value if len(value) <= 120 else value[:60] + f"...({len(value)})"
    if isinstance(value, list):
        return [_shape(item, depth + 1) for item in value[:40]] if depth < 12 else f"list({len(value)})"
    if isinstance(value, dict):
        return {key: _shape(item, depth + 1) for key, item in list(value.items())[:40]}
    return value


class Traffic:
    """batchexecute requests and replies around one action, with offsets from its start."""

    def __init__(self, session: FlowSession, scene_id: str | None) -> None:
        self.session = session
        self.scene_id = scene_id
        self.t0 = time.monotonic()
        self.requests: list[dict[str, Any]] = []
        self._bodies: list[asyncio.Task[tuple[float, str, int, str]]] = []

    def _offset(self) -> float:
        return round(time.monotonic() - self.t0, 2)

    def on_request(self, request: Any) -> None:
        if "batchexecute" not in request.url:
            return
        rpcids = parse_qs(urlparse(request.url).query).get("rpcids", [""])[0]
        freq = parse_qs(request.post_data or "").get("f.req", [""])[0]
        calls = []
        try:
            entries = json.loads(freq)[0]
        except (ValueError, IndexError, TypeError):
            entries = []
        for entry in entries if isinstance(entries, list) else []:
            inner = entry[1] if isinstance(entry, list) and len(entry) > 1 else None
            try:
                payload = json.loads(inner) if isinstance(inner, str) else inner
            except ValueError:
                payload = inner
            calls.append(
                {
                    "rpcid": entry[0] if isinstance(entry, list) and entry else None,
                    "uuids": UUID.findall(json.dumps(payload))[:40],
                    "payload": _shape(payload),
                }
            )
        self.requests.append({"t": self._offset(), "rpcids": rpcids, "calls": calls})

    def on_response(self, response: Any) -> None:
        if "batchexecute" not in response.url:
            return

        async def body() -> tuple[float, str, int, str]:
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001
                text = ""
            rpcids = parse_qs(urlparse(response.url).query).get("rpcids", [""])[0]
            return self._offset(), rpcids, response.status, text

        self._bodies.append(asyncio.ensure_future(body()))

    def start(self) -> Traffic:
        self.t0 = time.monotonic()
        self.session.page.on("request", self.on_request)
        self.session.page.on("response", self.on_response)
        return self

    async def stop(self) -> dict[str, Any]:
        self.session.page.remove_listener("request", self.on_request)
        self.session.page.remove_listener("response", self.on_response)
        replies = []
        for offset, rpcids, status, text in await asyncio.gather(*self._bodies):
            frames = []
            for rpcid, payload in parse_frames(text):
                dumped = json.dumps(payload)
                frames.append(
                    {
                        "rpcid": rpcid,
                        "length": len(dumped),
                        "names_scene": bool(self.scene_id and self.scene_id in dumped),
                        "uuids": UUID.findall(dumped)[:40],
                        "shape": _shape(payload) if self.scene_id and self.scene_id in dumped else None,
                    }
                )
            replies.append({"t": offset, "rpcids": rpcids, "status": status, "frames": frames})
        return {"requests": self.requests, "replies": sorted(replies, key=lambda r: r["t"])}


async def _timeline(page: Any) -> dict[str, Any]:
    return await page.evaluate(_TIMELINE_JS)


async def _overlay(page: Any) -> list[dict[str, Any]]:
    return await page.evaluate(_OVERLAY_JS)


async def _shot(page: Any, stamp: str, name: str) -> str:
    path = OUT_DIR / f"scene_editor_{stamp}_{name}.png"
    await page.screenshot(path=str(path))
    return str(path)


async def _labels_over(page: Any, seconds: float, step_s: float = 0.5) -> list[list[Any]]:
    """Every distinct Total duration and aspect reading, with the offset it first appeared at."""
    seen: list[list[Any]] = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        view = await _timeline(page)
        reading = [view["total_duration"], view["aspect"], len(view["items"] or [])]
        if not seen or seen[-1][1:] != reading:
            seen.append([round(time.monotonic() - t0, 2), *reading])
        await page.wait_for_timeout(int(step_s * 1000))
    return seen


async def _close_overlays(page: Any) -> int:
    panes = page.locator(".cdk-overlay-pane, [role=dialog]")
    for _ in range(3):
        if await panes.count() == 0:
            return 0
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
    return await panes.count()


async def _paid_free(element: Any, what: str) -> str:
    label = await scenes._label_of(element)
    if any(word in label.lower() for word in scenes.PAID_WORDS):
        raise RuntimeError(f"refusing to click {label!r} for {what}: that control spends credits")
    return label


async def _open_picker(page: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    add_clip = page.get_by_role("button", name=re.compile("Add clip", re.IGNORECASE))
    result["add_clip_buttons"] = await add_clip.count()
    result["clicked"] = await scenes._click_one(page, add_clip, "the Add clip button")
    await page.wait_for_timeout(1_500)
    result["first_overlay"] = await _overlay(page)
    menu = page.locator(scenes.OVERLAY).filter(has_text=re.compile("Add clip", re.IGNORECASE))
    result["menu_items"] = await menu.count()
    if result["menu_items"]:
        result["menu_clicked"] = await scenes._click_one(page, menu, "the Add clip menu item")
        await page.wait_for_timeout(2_000)
    waited = 0
    while await page.locator(ROW).count() == 0 and waited < 10_000:
        await page.wait_for_timeout(1_000)
        waited += 1_000
    result["rows_waited_ms"] = waited
    result["picker"] = await _overlay(page)
    return result


async def act_open(session: FlowSession, project: str, scene_id: str, stamp: str) -> dict[str, Any]:
    page = session.page
    traffic = Traffic(session, scene_id).start()
    await scenes._open_scene(session, project, scene_id)
    labels = await _labels_over(page, 15.0, 1.0)
    result: dict[str, Any] = {"labels_after_load": labels}
    result["timeline"] = await _timeline(page)
    result["traffic"] = await traffic.stop()
    result["shot"] = await _shot(page, stamp, "open")
    return result


async def act_picker(session: FlowSession, stamp: str) -> dict[str, Any]:
    page = session.page
    result = await _open_picker(page)
    result["shot"] = await _shot(page, stamp, "picker")
    result["panes_left"] = await _close_overlays(page)
    result["timeline_after"] = await _timeline(page)
    return result


async def act_add(
    session: FlowSession,
    project: str,
    scene_id: str,
    media_id: str,
    titles: dict[str, str],
    stamp: str,
    *,
    reopen: bool,
) -> dict[str, Any]:
    page = session.page
    if media_id not in titles:
        raise LookupError(f"{media_id} is not a video with a title no other media shares")
    title = titles[media_id]
    result: dict[str, Any] = {"media_id": media_id, "title": title, "reopen": reopen}
    if reopen:
        await scenes._open_scene(session, project, scene_id)
        result["labels_after_load"] = await _labels_over(page, 8.0, 1.0)
    result["before"] = await _timeline(page)
    result["open"] = await _open_picker(page)
    rows = page.locator(ROW).filter(has=page.get_by_text(title, exact=True))
    result["row_matches"] = await rows.count()
    if result["row_matches"] != 1:
        raise LookupError(f"{result['row_matches']} picker rows show {title!r}")
    result["selected_on_open"] = await rows.first.get_attribute("aria-selected")
    if result["selected_on_open"] != "true":
        await rows.first.click(timeout=8_000)
        await page.wait_for_timeout(2_000)
        result["after_row_click"] = await _overlay(page)
        result["label_after_row_click"] = (await _timeline(page))["total_duration"]
        result["selected_after_click"] = await rows.first.get_attribute("aria-selected")
        if result["selected_after_click"] != "true":
            raise RuntimeError("the row did not become selected; not clicking Add media")
    add = page.locator(".cdk-overlay-pane button").filter(has_text=re.compile("Add media", re.IGNORECASE))
    result["add_media_buttons"] = await add.count()
    if result["add_media_buttons"] != 1:
        raise LookupError(f"{result['add_media_buttons']} Add media buttons in the picker")
    result["add_media_label"] = await _paid_free(add.first, "Add media")
    result["shot_before_add"] = await _shot(page, stamp, f"add_{media_id[:8]}_before")
    traffic = Traffic(session, scene_id).start()
    await add.first.click(timeout=8_000)
    result["labels_after_add"] = await _labels_over(page, LABEL_POLL_S)
    result["panes_after_add"] = await page.locator(".cdk-overlay-pane, [role=dialog]").count()
    result["traffic"] = await traffic.stop()
    result["after"] = await _timeline(page)
    result["shot_after_add"] = await _shot(page, stamp, f"add_{media_id[:8]}_after")
    return result


async def act_select(session: FlowSession, index: int, stamp: str) -> dict[str, Any]:
    page = session.page
    before = await _timeline(page)
    clip = await _clip(page, index)
    traffic = Traffic(session, None).start()
    # A locator click scrolls the clip into view first: pass 2 clicked the centre of a clip lying off screen, at
    # x 2061 on a 1272 px wide timeline, and hit nothing.
    await clip.click(timeout=8_000)
    await page.wait_for_timeout(1_500)
    after = await _timeline(page)
    return {
        "index": index,
        "before": before,
        "after": after,
        "overlay": await _overlay(page),
        "traffic": await traffic.stop(),
        "shot": await _shot(page, stamp, f"select_{index}"),
    }


async def act_skip(session: FlowSession, direction: str, stamp: str) -> dict[str, Any]:
    page = session.page
    name = "Skip to next clip" if direction == "next" else "Skip to previous clip"
    before = await _timeline(page)
    label = await scenes._click_one(
        page, page.get_by_role("button", name=re.compile(name, re.IGNORECASE)), name
    )
    await page.wait_for_timeout(1_500)
    return {
        "clicked": label,
        "before": before,
        "after": await _timeline(page),
        "shot": await _shot(page, stamp, f"skip_{direction}"),
    }


async def act_aspect(session: FlowSession, scene_id: str, stamp: str) -> dict[str, Any]:
    page = session.page
    toggle = page.get_by_role("button", name=re.compile("Toggle aspect ratio", re.IGNORECASE))
    result: dict[str, Any] = {"buttons": await toggle.count(), "before": await _timeline(page)}
    traffic = Traffic(session, scene_id).start()
    result["clicked"] = await scenes._click_one(page, toggle, "the Toggle aspect ratio button")
    result["labels_after_click"] = await _labels_over(page, 10.0)
    result["overlay"] = await _overlay(page)
    result["traffic"] = await traffic.stop()
    result["shot"] = await _shot(page, stamp, "aspect")
    return result


async def _clip(page: Any, index: int) -> Any:
    clips = page.locator(".timeline-contents > .clip")
    count = await clips.count()
    if not 0 <= index < count:
        raise LookupError(f"the timeline holds {count} clips, no index {index}")
    return clips.nth(index)


async def act_zoom(session: FlowSession, direction: str, stamp: str) -> dict[str, Any]:
    page = session.page
    name = "Zoom out" if direction == "out" else "Zoom in"
    before = await _timeline(page)
    label = await scenes._click_one(
        page, page.get_by_role("button", name=re.compile(name, re.IGNORECASE)), name
    )
    await page.wait_for_timeout(1_000)
    return {
        "clicked": label,
        "before_rects": [it["rect"] for it in before["items"] or []],
        "after": await _timeline(page),
        "viewport": page.viewport_size,
        "shot": await _shot(page, stamp, f"zoom_{direction}"),
    }


async def act_menu(session: FlowSession, index: int, stamp: str) -> dict[str, Any]:
    page = session.page
    clip = await _clip(page, index)
    result: dict[str, Any] = {"index": index, "before": await _timeline(page)}
    await clip.hover(timeout=8_000)
    await page.wait_for_timeout(1_000)
    result["hovered"] = await _timeline(page)
    await clip.click(button="right", timeout=8_000)
    await page.wait_for_timeout(1_500)
    result["right_click_overlay"] = await _overlay(page)
    result["shot"] = await _shot(page, stamp, f"menu_{index}")
    result["panes_left"] = await _close_overlays(page)
    return result


async def act_remove(
    session: FlowSession, scene_id: str, index: int, item: str, stamp: str
) -> dict[str, Any]:
    page = session.page
    clip = await _clip(page, index)
    result: dict[str, Any] = {"index": index, "item": item, "before": await _timeline(page)}
    await clip.click(button="right", timeout=8_000)
    await page.wait_for_timeout(1_500)
    result["selected_after_right_click"] = await _timeline(page)
    result["overlay"] = await _overlay(page)
    # Pass 4: an item's text is its icon ligature run straight into its label ("deleteDelete"), no space between,
    # so the label is matched at the end of the text.
    entries = page.locator("[role=menuitem]").filter(
        has_text=re.compile(rf"{re.escape(item)}\s*$", re.IGNORECASE)
    )
    result["matches"] = await entries.count()
    result["menuitem_texts"] = await page.locator("[role=menuitem]").all_text_contents()
    if result["matches"] != 1:
        result["panes_left"] = await _close_overlays(page)
        record_error = f"{result['matches']} menu entries read {item!r}: {result['menuitem_texts']}"
        raise LookupError(record_error)
    result["clicked"] = await _paid_free(entries.first, item)
    traffic = Traffic(session, scene_id).start()
    await entries.first.click(timeout=8_000)
    result["labels_after_click"] = await _labels_over(page, 12.0)
    result["dialogs"] = await _overlay(page)
    result["traffic"] = await traffic.stop()
    result["after"] = await _timeline(page)
    result["shot"] = await _shot(page, stamp, f"remove_{index}")
    return result


async def act_move(
    session: FlowSession, scene_id: str, source: int, target: int, stamp: str
) -> dict[str, Any]:
    page = session.page
    view = await _timeline(page)
    boxes = [await (await _clip(page, index)).bounding_box() for index in (source, target)]
    width = (page.viewport_size or {}).get("width") or 0
    result: dict[str, Any] = {"from": source, "to": target, "before": view, "boxes": boxes, "viewport": width}
    if any(box is None or box["x"] < 0 or box["x"] + box["width"] > width for box in boxes):
        raise LookupError(
            f"clip {source} or {target} lies off screen ({boxes}, viewport {width}); zoom out first"
        )
    sx, sy = boxes[0]["x"] + boxes[0]["width"] / 2, boxes[0]["y"] + boxes[0]["height"] / 2
    tx, ty = boxes[1]["x"] + boxes[1]["width"] / 2, boxes[1]["y"] + boxes[1]["height"] / 2
    traffic = Traffic(session, scene_id).start()
    await page.mouse.move(sx, sy)
    await page.mouse.down()
    await page.wait_for_timeout(300)
    nudge = 12 if tx > sx else -12
    for step in range(1, 16):
        await page.mouse.move(sx + (tx + nudge - sx) * step / 15, sy + (ty - sy) * step / 15)
        await page.wait_for_timeout(60)
    await page.wait_for_timeout(500)
    result["during"] = await _timeline(page)
    await page.mouse.up()
    result["labels_after_drop"] = await _labels_over(page, 12.0)
    result["traffic"] = await traffic.stop()
    result["after"] = await _timeline(page)
    result["shot"] = await _shot(page, stamp, f"move_{source}_{target}")
    return result


def _chrome_processes() -> int:
    run = subprocess.run(["pgrep", "-f", "Google Chrome"], capture_output=True, text=True, check=False)
    return len(run.stdout.split())


def _free_pages() -> int | None:
    run = subprocess.run(["vm_stat"], capture_output=True, text=True, check=False)
    match = re.search(r"Pages free:\s+(\d+)", run.stdout)
    return int(match.group(1)) if match else None


def _duration(path: Any) -> str:
    run = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return run.stdout.strip() or run.stderr.strip()[:200]


def _artifact_files() -> dict[str, int]:
    found: dict[str, int] = {}
    for folder in glob.glob(os.path.join(tempfile.gettempdir(), "playwright-artifacts-*")):
        for path in glob.glob(os.path.join(folder, "*")):
            try:
                found[path] = os.path.getsize(path)
            except OSError:
                continue
    return found


_GRID_JS = """
() => {
  const containers = [...document.querySelectorAll('flow-tile-container')];
  const scenes = containers.filter(c => c.querySelector('flow-scene-tile'));
  const text = (e) => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
  const box = (e) => { const r = e.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y),
    Math.round(r.width), Math.round(r.height)]; };
  const scrollers = [...document.querySelectorAll('*')].filter(e => e.scrollHeight > e.clientHeight + 20
    && ['auto', 'scroll'].includes(getComputedStyle(e).overflowY)).map(e => ({
      tag: e.tagName.toLowerCase(), cls: String(e.className).slice(0, 80), top: e.scrollTop,
      height: e.scrollHeight, client: e.clientHeight}));
  return {tiles: containers.length, scene_tiles: scenes.map(c => ({text: text(c), box: box(c)})),
    page_height: document.documentElement.scrollHeight, viewport: window.innerHeight, scrollers};
}
"""


_SCROLLER_FIND = """
  const scroller = document.querySelector('.cdk-virtual-scrollable')
    || [...document.querySelectorAll('*')].filter(e => e.scrollHeight > e.clientHeight + 20
      && ['auto', 'scroll'].includes(getComputedStyle(e).overflowY))
      .sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
"""

_GRID_SCAN_JS = (
    "() => {"
    + _SCROLLER_FIND
    + """
  if (!scroller) return {scroller: null, tiles: []};
  const host = scroller.getBoundingClientRect();
  const text = (e) => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
  const all = [...document.querySelectorAll('flow-tile-container')];
  const tiles = all.filter(c => c.querySelector('flow-scene-tile')).map(c => {
    const r = c.getBoundingClientRect();
    return {text: text(c), top: Math.round(r.y - host.y + scroller.scrollTop),
      left: Math.round(r.x - host.x), height: Math.round(r.height), visible: r.bottom > host.top && r.top < host.bottom};
  });
  return {scroller: String(scroller.className).slice(0, 80), top: Math.round(scroller.scrollTop),
    height: scroller.scrollHeight, client: scroller.clientHeight, containers: all.length, tiles};
}"""
)

_GRID_SCROLL_JS = (
    "(top) => {"
    + _SCROLLER_FIND
    + """
  if (!scroller) return null;
  scroller.scrollTop = top;
  return Math.round(scroller.scrollTop);
}"""
)


async def act_gridscan(session: FlowSession, project: str, stamp: str, where: str) -> dict[str, Any]:
    """Every scene tile the virtual grid renders while it is scrolled to the end, each with its offset inside the
    scroller, so a driver can tell a recycled tile from a new one and know when it has seen the whole list. Read only."""
    page = session.page
    url = session.project_url(project) + ("/trash" if where == "trash" else "")
    await session.goto(url, ready="flow-project-page")
    await page.wait_for_timeout(4_000)
    steps: list[dict[str, Any]] = []
    seen: dict[tuple[int, int], str] = {}
    for step in range(20):
        snap = await page.evaluate(_GRID_SCAN_JS)
        for tile in snap["tiles"]:
            seen.setdefault((tile["top"], tile["left"]), tile["text"])
        steps.append(snap)
        if step == 0:
            snap["shot"] = await _shot(page, stamp, f"{where}scan_top")
        if not snap.get("scroller") or snap["top"] + snap["client"] >= snap["height"] - 2:
            break
        moved = await page.evaluate(_GRID_SCROLL_JS, snap["top"] + int(snap["client"] * 0.8))
        await page.wait_for_timeout(1_500)
        if moved == snap["top"]:
            break
    steps[-1]["shot"] = await _shot(page, stamp, f"{where}scan_end")
    listed = await scenes.list_scenes(session, project, include_trashed=True)
    return {
        "where": where,
        "steps": steps,
        "collected": [{"top": top, "left": left, "text": text} for (top, left), text in sorted(seen.items())],
        "distinct_tiles": len(seen),
        "listing_active": sum(1 for s in listed if not s["trashed"]),
        "listing_trashed": sum(1 for s in listed if s["trashed"]),
        "listing_titles": [s["title"] for s in listed if s["trashed"] == (where == "trash")],
    }


def _chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


async def act_media(session: FlowSession, project: str) -> dict[str, Any]:
    """How big flow_media's answer is and where its characters sit: what an agent's client has to swallow today,
    and what the same answer would weigh cut to the newest 20 rows."""
    out: dict[str, Any] = {}
    for versions in (False, True):
        data = await reader.project(session, project, versions=versions)
        entry: dict[str, Any] = {
            "chars": _chars(data),
            "media_rows": len(data["media"]),
            "sections": {key: _chars(value) for key, value in data.items()},
        }
        rows = data.get("versions") or []
        if rows:
            entry["version_rows"] = len(rows)
            entry["version_fields"] = {
                field: sum(_chars(row.get(field)) for row in rows) for field in rows[0]
            }
        newest = sorted(data["media"], key=lambda r: r.get("created") or 0, reverse=True)[:20]
        cut = {**data, "media": newest}
        if rows:
            cut["versions"] = sorted(rows, key=lambda r: r.get("created") or 0, reverse=True)[:20]
        entry["chars_newest_20"] = _chars(cut)
        entry["kinds"] = sorted({str(row.get("kind")) for row in data["media"]})
        out["all_versions" if versions else "default"] = entry
    return out


async def act_grid(session: FlowSession, project: str, stamp: str) -> dict[str, Any]:
    """How many scene tiles the project grid renders, before and after scrolling every scroller to its end. Read only."""
    page = session.page
    await session.goto(session.project_url(project), ready="flow-project-page")
    result: dict[str, Any] = {"loaded": []}
    for _ in range(4):
        await page.wait_for_timeout(3_000)
        result["loaded"].append(await page.evaluate(_GRID_JS))
    result["shot_top"] = await _shot(page, stamp, "grid_top")
    await page.evaluate(
        "() => [...document.querySelectorAll('*')].filter(e => e.scrollHeight > e.clientHeight + 20)"
        ".forEach(e => e.scrollTop = e.scrollHeight)"
    )
    await page.wait_for_timeout(3_000)
    result["scrolled"] = await page.evaluate(_GRID_JS)
    result["shot_scrolled"] = await _shot(page, stamp, "grid_scrolled")
    return result


async def act_download(session: FlowSession, scene_id: str, stamp: str) -> dict[str, Any]:
    """Every signal around one scene download, to learn what closes the target under a long film."""
    page = session.page
    events: list[list[Any]] = []
    t0 = time.monotonic()

    def mark(name: str) -> Any:
        return lambda *_: events.append([round(time.monotonic() - t0, 2), name])

    page.on("close", mark("page_close"))
    page.on("crash", mark("page_crash"))
    page.on("popup", mark("popup"))
    page.on(
        "framenavigated",
        lambda frame: events.append([round(time.monotonic() - t0, 2), "navigated", frame == page.main_frame]),
    )
    page.context.on("close", mark("context_close"))
    page.context.on("page", mark("context_page"))
    result: dict[str, Any] = {
        "before": await _timeline(page),
        "chrome_processes": _chrome_processes(),
        "free_pages": _free_pages(),
        "artifacts_before": len(_artifact_files()),
        "events": events,
    }
    button = page.get_by_role("button", name=re.compile("Download scene", re.IGNORECASE))
    ui: list[list[Any]] = []
    result["ui_while_rendering"] = ui
    try:
        async with page.expect_download(timeout=300_000) as info:
            result["clicked"] = await scenes._click_one(page, button, "the Download scene button")
            # The film is built inside the page (a blob URL, pass 3): record what the page shows meanwhile, so a
            # driver can tell a render still running from a click that started nothing.
            while not info.is_done() and time.monotonic() - t0 < 300:
                shown = await page.evaluate(_BUSY_JS)
                if not ui or ui[-1][1:] != shown:
                    ui.append([round(time.monotonic() - t0, 1), *shown])
                await asyncio.sleep(2.0)
        download = await info.value
        result["download_event_s"] = round(time.monotonic() - t0, 2)
        parsed = urlparse(download.url)
        result["download_url"] = {
            "scheme": parsed.scheme,
            "host": parsed.netloc,
            "uuids": UUID.findall(download.url),
        }
        result["suggested"] = download.suggested_filename
        known = set(_artifact_files())
        growth: list[list[Any]] = []
        finished = asyncio.ensure_future(download.path())
        while not finished.done():
            fresh = {p: s for p, s in _artifact_files().items() if p not in known}
            growth.append([round(time.monotonic() - t0, 1), sum(fresh.values()), _free_pages()])
            await asyncio.sleep(1.0)
        result["growth"] = growth[-60:]
        try:
            local = await finished
            result["finished_s"] = round(time.monotonic() - t0, 2)
            result["local_bytes"] = os.path.getsize(local) if local else None
        except Exception as exc:  # noqa: BLE001
            result["path_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            result["path_error_s"] = round(time.monotonic() - t0, 2)
        result["failure"] = await download.failure()
        target = OUT_DIR / f"scene_editor_{stamp}_{scene_id[:8]}.mp4"
        await download.save_as(str(target))
        result["saved"] = str(target)
        result["saved_bytes"] = target.stat().st_size
        result["saved_duration_s"] = await asyncio.to_thread(_duration, target)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
        result["error_s"] = round(time.monotonic() - t0, 2)
    result["chrome_processes_after"] = _chrome_processes()
    result["free_pages_after"] = _free_pages()
    return result


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--scene")
    ap.add_argument("--create")
    ap.add_argument("--do", action="append", default=[], dest="actions")
    args = ap.parse_args()
    project_only = all(action.partition("=")[0] in PROJECT_ACTIONS for action in args.actions)
    if bool(args.scene) == bool(args.create) and not (project_only and not args.scene and not args.create):
        ap.error("give exactly one of --scene and --create, or neither for project-wide actions")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    report_path = OUT_DIR / f"scene_editor_{stamp}.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"project": args.project, "scene": args.scene, "actions": []}

    def save() -> None:
        report_path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    async with FlowSession(args.profile) as session:
        scene_id = args.scene
        titles: dict[str, str] = {}
        if any(action.startswith("add") for action in args.actions):
            # One listing read up front: a read later would navigate away from the timeline an action set up.
            media = (await reader.project(session, args.project))["media"]
            names = [scenes._tile_text(m.get("title")) for m in media]
            titles = {
                m["id"]: name
                for m, name in zip(media, names, strict=True)
                if m.get("kind") == "video" and name and names.count(name) == 1
            }
            report["titles"] = titles
        if args.create:
            report["created"] = await scenes.create(session, args.project, args.create)
            scene_id = report["scene"] = report["created"]["scene_id"]
            save()
        if scene_id:
            await scenes._open_scene(session, args.project, scene_id)
        for action in args.actions:
            name, _, value = action.partition("=")
            record: dict[str, Any] = {"action": action, "started": time.strftime("%H:%M:%S")}
            report["actions"].append(record)
            t0 = time.monotonic()
            try:
                if name == "open":
                    record["result"] = await act_open(session, args.project, scene_id, stamp)
                elif name == "picker":
                    record["result"] = await act_picker(session, stamp)
                elif name in ("add", "addhere"):
                    record["result"] = await act_add(
                        session, args.project, scene_id, value, titles, stamp, reopen=name == "add"
                    )
                elif name == "timeline":
                    record["result"] = await _timeline(session.page)
                elif name == "select":
                    record["result"] = await act_select(session, int(value), stamp)
                elif name == "skip":
                    record["result"] = await act_skip(session, value, stamp)
                elif name == "zoom":
                    record["result"] = await act_zoom(session, value, stamp)
                elif name == "aspect":
                    record["result"] = await act_aspect(session, scene_id, stamp)
                elif name == "menu":
                    record["result"] = await act_menu(session, int(value), stamp)
                elif name == "remove":
                    index, _, item = value.partition(":")
                    record["result"] = await act_remove(session, scene_id, int(index), item, stamp)
                elif name == "move":
                    source, _, target = value.partition(":")
                    record["result"] = await act_move(session, scene_id, int(source), int(target), stamp)
                elif name == "download":
                    record["result"] = await act_download(session, scene_id, stamp)
                elif name == "trash":
                    record["result"] = await scenes.delete(session, args.project, scene_id)
                elif name == "grid":
                    record["result"] = await act_grid(session, args.project, stamp)
                elif name == "gridscan":
                    record["result"] = await act_gridscan(session, args.project, stamp, value or "grid")
                elif name == "media":
                    record["result"] = await act_media(session, args.project)
                else:
                    raise ValueError(f"unknown action {name!r}")
            except Exception as exc:  # noqa: BLE001
                record["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
                try:
                    record["error_shot"] = await _shot(session.page, stamp, f"error_{len(report['actions'])}")
                except Exception as shot_exc:  # noqa: BLE001
                    record["error_shot"] = f"{type(shot_exc).__name__}: {str(shot_exc)[:200]}"
            record["seconds"] = round(time.monotonic() - t0, 1)
            save()
            print(f"{action}: {record.get('error') or 'ok'} ({record['seconds']} s)", flush=True)
            if "error" in record:
                break
    print(f"scene={report.get('scene')} report={report_path}")


if __name__ == "__main__":
    asyncio.run(main())
