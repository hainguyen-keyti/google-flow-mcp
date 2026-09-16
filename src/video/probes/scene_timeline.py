"""Plan scene-timeline-tools T1: what the scene page's timeline offers, and what a scene download really is. $0.

Creates one scene, waits until the toolbar is really built, then adds clips through the 'Add clip' picker and measures
what the page says: the rpcids each step fires, the page's own total duration, and how many clips the timeline holds.
Then it downloads the 720p 'Original size' rendition (the one that is not an upscale) through 'Download scene' and
measures it with ffprobe, container and video stream alike. 'Download project' is only opened far enough to read the
file it would hand over; that download is cancelled, never saved. The probe scene is left in the trash.

Everything here is a read or a $0 state change on a draft project, so it costs no credits.

Measured while writing this probe (2026-09-16, three runs):
- The scene page and the clip editor share `flow-scene-builder` but NOT their button names: a scene has
  'Download scene' and 'Done editing scene', the editor has 'Download media'.
- 'Add clip' opens a modal picker (tabs 'Videos', 'Uploads', 'Upload media', one row per media with its own
  'Add media' button). While it is open it covers the page, so every later click times out until it is closed.
- Button labels carry icon ligatures, so an anchored name match finds nothing; match a substring instead.
- A scene page can render its toolbar in stages: a dump taken too early saw 7 buttons where a built page has 27.

    uv run python -m video.probes.scene_timeline --project <id> [--inventory]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import scenes
from video.flow.reader import capture
from video.probes._common import OUT_DIR, out_path
from video.session import FlowSession

BUILDER = "flow-scene-builder"
TOOLBAR_MIN = 20

_PAGE_JS = """
() => {
  const el = document.querySelector('flow-scene-builder');
  const text = (el ? el.innerText : document.body.innerText) || '';
  const clip = (s) => (s || '').trim().replace(/\\s+/g, ' ').slice(0, 400);
  const duration = () => {
    const key = 'Total duration:';
    const at = text.indexOf(key);
    if (at < 0) return null;
    const m = text.slice(at + key.length, at + key.length + 24).match(/[0-9:]+/);
    return m ? m[0] : null;
  };
  const timeline = document.querySelector('flow-scene-timeline');
  return {
    url: location.href,
    button_count: document.querySelectorAll('button').length,
    buttons: [...document.querySelectorAll('button')]
      .map(b => clip(b.getAttribute('aria-label') || b.innerText)).filter(Boolean).slice(0, 40),
    total_duration: duration(),
    timeline_tags: timeline
      ? [...new Set([...timeline.querySelectorAll('*')].map(e => e.tagName.toLowerCase()))].slice(0, 25)
      : [],
    timeline_media: timeline ? timeline.querySelectorAll('video, img').length : 0,
    timeline_text: timeline ? clip(timeline.innerText) : null,
    text: clip(text),
  };
}
"""

_OVERLAY_JS = """
() => {
  const clip = (s) => (s || '').trim().replace(/\\s+/g, ' ').slice(0, 160);
  return [...document.querySelectorAll('.cdk-overlay-pane, [role=dialog]')].map(p => ({
    tags: [...new Set([...p.querySelectorAll('*')].map(e => e.tagName.toLowerCase())
      .filter(t => t.startsWith('flow-')))].slice(0, 20),
    buttons: [...p.querySelectorAll('button')].map(b => ({
      aria: clip(b.getAttribute('aria-label')),
      text: clip(b.innerText),
      visible: !!(b.offsetWidth || b.offsetHeight),
      rect: (r => [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)])(
        b.getBoundingClientRect()),
    })).slice(0, 40),
    text: clip(p.innerText),
  }));
}
"""

# PAID is a hard stop, not a reminder: this probe claims to cost nothing, so it must be unable to click a control
# that spends. Measured 2026-09-16: a scene already holding a clip answers 'Add clip' with a menu whose second item
# is 'Extend (Veo 3.1 - Lite)', 10 credits, and an earlier pass of this probe aimed a click straight at it.
_CLICK_JS = """
(args) => {
  const [scope, needle] = args;
  const PAID = ['extend', 'generate', 'upscale', '4k'];
  const panes = [...document.querySelectorAll('.cdk-overlay-pane, [role=dialog]')];
  const root = scope === 'pane' ? panes[panes.length - 1] : document;
  if (!root) return null;
  const want = needle.toLowerCase();
  const label = (b) => ((b.getAttribute('aria-label') || '') + ' ' + (b.innerText || '')).trim();
  const hit = [...root.querySelectorAll('button')].find(b => {
    const text = label(b).toLowerCase();
    return text.includes(want) && !PAID.some(word => text.includes(word)) && (b.offsetWidth || b.offsetHeight);
  });
  if (!hit) return null;
  hit.click();
  return label(hit).replace(/\\s+/g, ' ').slice(0, 120);
}
"""


def ffprobe(path: Path) -> dict[str, Any]:
    """Container duration AND video stream duration: the repo has been bitten by trusting the container alone."""
    out: dict[str, Any] = {"file": str(path), "bytes": path.stat().st_size if path.exists() else None}
    for key, args in (
        ("container_s", ["-show_entries", "format=duration"]),
        ("stream_s", ["-select_streams", "v:0", "-show_entries", "stream=duration"]),
    ):
        run = subprocess.run(
            ["ffprobe", "-v", "error", *args, "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        value = run.stdout.strip().splitlines()
        out[key] = float(value[0]) if value and value[0] not in ("", "N/A") else None
    return out


async def _ready(session: FlowSession, timeout_s: float = 25.0) -> dict[str, Any]:
    """Wait for the toolbar to be built, and report how long it took: a page dumped too early lies about its buttons."""
    page = session.page
    waited = 0.0
    while waited < timeout_s:
        count = await page.locator("button").count()
        if count >= TOOLBAR_MIN:
            return {"waited_s": round(waited, 1), "buttons": count}
        await page.wait_for_timeout(1_000)
        waited += 1.0
    return {"waited_s": round(waited, 1), "buttons": await page.locator("button").count(), "short": True}


async def _page(session: FlowSession) -> dict[str, Any]:
    return await session.page.evaluate(_PAGE_JS)


async def _close_overlays(page: Any) -> int:
    """The picker is a modal covering the whole page: leave it open and every later click waits forever."""
    panes = page.locator(".cdk-overlay-pane, [role=dialog]")
    for _ in range(3):
        if await panes.count() == 0:
            return 0
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
    return await panes.count()


async def _click(session: FlowSession, scope: str, needle: str, *, settle: float) -> dict[str, Any]:
    """Click the first visible button whose aria-label or text CONTAINS the needle, and report what was clicked.

    Matching in the page instead of through Playwright's accessible-name engine: the labels carry icon ligatures
    ('add Add media'), so an anchored name match finds nothing.
    """
    clicked: str | None = None

    async def do() -> None:
        nonlocal clicked
        clicked = await session.page.evaluate(_CLICK_JS, [scope, needle])

    frames = await capture(session, do, settle=settle)
    return {"needle": needle, "clicked": clicked, "rpcids": sorted(frames)}


async def _add_clip(session: FlowSession, step: int, shot: Path | None) -> dict[str, Any]:
    """Open 'Add clip' once, record what it offers, add a media, and report what the page did.

    Once only: on the clip editor the same label appends a copy of the clip when clicked twice.
    """
    page = session.page
    result: dict[str, Any] = {"step": step}
    result["open"] = await _click(session, "page", "add clip", settle=4.0)
    await page.wait_for_timeout(1_500)
    first = await page.evaluate(_OVERLAY_JS)
    result["first_overlay"] = first
    # Measured 2026-09-16: an empty scene answers with the media picker, but a scene that already holds a clip
    # answers with a menu (Add clip, Extend), so the picker is one click further in.
    if any("add clip" in (b["text"] + b["aria"]).lower() for pane in first for b in pane["buttons"]):
        result["menu_step"] = await _click(session, "pane", "add clip", settle=3.0)
        await page.wait_for_timeout(2_000)
    result["picker"] = await page.evaluate(_OVERLAY_JS)
    if shot is not None:
        await page.screenshot(path=str(shot))
        result["screenshot"] = str(shot)
    chrome = (
        "videos",
        "uploads",
        "upload media",
        "add media",
        "add clip",
        "extend",
        "play",
        "pause",
        "unmute",
        "mute",
        "close",
    )
    rows = [
        button
        for pane in result["picker"]
        for button in pane["buttons"]
        if button["visible"] and not any(word in (button["aria"] + button["text"]).lower() for word in chrome)
    ]
    result["media_rows"] = [row["aria"] or row["text"] for row in rows]
    if rows:
        wanted = rows[min(step, len(rows) - 1)]
        needle = (wanted["aria"] or wanted["text"])[:40]
        # Measured 2026-09-16: clicking the row IS the add. The picker closes itself and the clip lands on the
        # timeline, so there is no confirm button to press; the fallback below only runs if it stays open.
        result["select"] = await _click(session, "pane", needle.lower(), settle=6.0)
        await page.wait_for_timeout(1_500)
        result["panes_after_select"] = await page.locator(".cdk-overlay-pane, [role=dialog]").count()
        if result["panes_after_select"]:
            result["add"] = await _click(session, "pane", "add media", settle=6.0)
    result["overlays_left"] = await _close_overlays(page)
    await page.wait_for_timeout(3_000)
    result["page"] = await _page(session)
    return result


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--inventory", action="store_true", help="stop after dumping the Add clip picker")
    ap.add_argument("--clips", type=int, default=2)
    args = ap.parse_args()
    title = f"pT-probe {int(time.time())}"
    report: dict[str, Any] = {"project": args.project, "title": title, "inventory": args.inventory}
    stamp = time.strftime("%Y%m%d_%H%M%S")
    created: dict[str, Any] = {}
    async with FlowSession(args.profile) as session:
        page = session.page
        try:
            created = await scenes.create(session, args.project, title)
            report["created"] = created
            scene_url = f"{session.project_url(args.project)}/scene/{created['scene_id']}"
            await session.goto(scene_url, ready=BUILDER)
            report["ready"] = await _ready(session)
            report["empty_scene"] = await _page(session)

            report["adds"] = []
            for step in range(1 if args.inventory else args.clips):
                shot = OUT_DIR / f"scene_picker_{stamp}_{step}.png" if step == 0 else None
                report["adds"].append(await _add_clip(session, step, shot))

            if not args.inventory:
                try:
                    await _close_overlays(page)
                    # Measured 2026-09-16: unlike the clip editor, a scene has no quality menu. 'Download scene'
                    # downloads straight away and only a snackbar follows, so the click itself must be awaited.
                    async with page.expect_download(timeout=600_000) as info:
                        report["download_open"] = await _click(session, "page", "download scene", settle=2.0)
                    download = await info.value
                    # Save before touching the page again: the browser has died right here twice, and the file is
                    # thrown away with the context, so a dump taken first costs the measurement.
                    suffix = Path(download.suggested_filename).suffix or ".mp4"
                    target = OUT_DIR / f"scene_{created['scene_id'][:8]}{suffix}"
                    await download.save_as(str(target))
                    report["scene_download"] = {"suggested": download.suggested_filename, **ffprobe(target)}
                    await page.wait_for_timeout(1_000)
                    report["download_menu"] = await page.evaluate(_OVERLAY_JS)
                except Exception as exc:  # noqa: BLE001
                    report["scene_download"] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}

                try:
                    await _close_overlays(page)
                    report["more_open"] = await _click(session, "page", "more options", settle=2.0)
                    await page.wait_for_timeout(1_500)
                    report["more_menu"] = await page.evaluate(_OVERLAY_JS)
                    project_item = (
                        page.locator("[role=menuitem], .cdk-overlay-pane button")
                        .filter(has_text=re.compile("Download project", re.IGNORECASE))
                        .first
                    )
                    async with page.expect_download(timeout=120_000) as info:
                        await project_item.click(timeout=8_000)
                    download = await info.value
                    # Read what it hands over, then throw it away: a whole project could be gigabytes.
                    report["download_project"] = {"suggested": download.suggested_filename, "saved": False}
                    await download.cancel()
                except PlaywrightTimeoutError as exc:
                    report["download_project"] = {"error": f"TimeoutError: {str(exc)[:200]}"}
                except Exception as exc:  # noqa: BLE001
                    report["download_project"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        except Exception as exc:  # noqa: BLE001
            report["run_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        finally:
            if created:
                try:
                    report["cleanup"] = await scenes.delete(session, args.project, created["scene_id"])
                except Exception as exc:  # noqa: BLE001
                    report["cleanup"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    if created and "error" in (report.get("cleanup") or {}):
        # The browser has died mid-run twice: a second session is the only way the probe scene still gets trashed.
        try:
            async with FlowSession(args.profile) as rescue:
                report["cleanup_retry"] = await scenes.delete(rescue, args.project, created["scene_id"])
        except Exception as exc:  # noqa: BLE001
            report["cleanup_retry"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    path = out_path("scene_timeline")
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    empty = report.get("empty_scene", {})
    print(f"scene={created.get('scene_id')} title={title!r} ready={report.get('ready')}")
    print(f"empty: total_duration={empty.get('total_duration')} timeline_media={empty.get('timeline_media')}")
    for add in report.get("adds", []):
        print(
            f"add {add['step']}: open={add['open']['clicked']!r} rpc={add['open']['rpcids']} "
            f"rows={add['media_rows']}"
        )
        select = add.get("select", {})
        print(
            f"  select={select.get('clicked')!r} rpc={select.get('rpcids')} "
            f"panes_after={add.get('panes_after_select')} add={add.get('add', {}).get('clicked')!r} "
            f"overlays_left={add['overlays_left']}"
        )
        page_after = add["page"]
        print(
            f"  -> total_duration={page_after['total_duration']} timeline_media={page_after['timeline_media']} "
            f"timeline_text={page_after['timeline_text']!r}"
        )
    print(f"download_open={report.get('download_open', {}).get('clicked')!r}")
    print(f"scene_download={report.get('scene_download')}")
    print(f"download_project={report.get('download_project')}")
    print(f"run_error={report.get('run_error')} cleanup={report.get('cleanup')}")
    if "cleanup_retry" in report:
        print(f"cleanup_retry={report['cleanup_retry']}")
    print(f"report={path}")


if __name__ == "__main__":
    asyncio.run(main())
