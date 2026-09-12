"""Spike for T9: which per-tile actions exist on a video tile (extend, upscale, download, reuse).
Hovers the first video tile, dumps its hotbar, opens its 'More options' menu, dumps the items. $0.

uv run python -m video.probes.tile_actions --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from video.session import PROJECT_READY, FlowSession

_BUTTONS_JS = """
(sel) => [...document.querySelectorAll(sel)]
  .filter(e => e.offsetParent !== null)
  .map(e => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60))
  .filter(Boolean)
"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    report: dict[str, Any] = {}
    Path("out").mkdir(exist_ok=True)
    async with FlowSession(args.profile) as session:
        page = session.page
        await session.goto(session.project_url(args.project), ready=PROJECT_READY)
        await page.wait_for_timeout(4_000)
        tile = page.locator("flow-video-tile").first
        report["video_tiles"] = await page.locator("flow-video-tile").count()
        await tile.hover(timeout=8_000)
        await page.wait_for_timeout(800)
        report["hotbar"] = await page.evaluate(
            _BUTTONS_JS, "flow-video-tile button, flow-video-hotbar button"
        )
        await page.screenshot(path="out/t9_tile_hover.png")
        more = tile.get_by_role("button", name=re.compile("more|options", re.IGNORECASE)).first
        if await more.count() == 0:
            more = page.locator("flow-video-hotbar button").filter(has_text=re.compile("more_vert")).first
        report["more_found"] = await more.count()
        if await more.count():
            await more.click(timeout=8_000)
            await page.wait_for_timeout(1_000)
            report["menu"] = await page.evaluate(
                _BUTTONS_JS, "[role=menuitem], [role=menu] button, .cdk-overlay-pane button"
            )
            await page.screenshot(path="out/t9_tile_menu.png")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(600)
            backdrop = page.locator(".cdk-overlay-backdrop-showing").first
            if await backdrop.count():
                await backdrop.click(timeout=5_000, force=True)
                await page.wait_for_timeout(600)
        await tile.click(timeout=8_000, force=True)
        await page.wait_for_timeout(2_500)
        report["after_click_url"] = page.url
        report["after_click_buttons"] = await page.evaluate(_BUTTONS_JS, "button")
        await page.screenshot(path="out/t9_tile_open.png")
    Path("out/t9_tile_report.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    for key, value in report.items():
        print(f"{key}: {json.dumps(value, ensure_ascii=False)[:600]}")


if __name__ == "__main__":
    asyncio.run(main())
