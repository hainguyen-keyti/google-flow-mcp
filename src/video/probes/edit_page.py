"""Spike for T9 on the clip editor (/project/<id>/edit/<media>): what the extend menu, the More options
menu and the Download media control offer (extend, upscale, resolutions). $0: hovers and menu opens only.

uv run python -m video.probes.edit_page --project <id> --media <media_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from video.session import FlowSession

_VISIBLE_JS = """
(sel) => [...document.querySelectorAll(sel)]
  .filter(e => e.offsetParent !== null)
  .map(e => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 70))
  .filter(Boolean)
"""
_EXTEND_JS = """
() => [...document.querySelectorAll('flow-extend-menu')].map(e => ({
  text: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 200),
  buttons: [...e.querySelectorAll('button')].map(b => (b.getAttribute('aria-label') || b.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 50)),
  visible: e.offsetParent !== null,
}))
"""


async def open_menu(page: Any, name: str) -> list[str]:
    button = page.get_by_role("button", name=re.compile(name, re.IGNORECASE)).first
    if await button.count() == 0:
        return [f"(no button {name})"]
    await button.click(timeout=8_000)
    await page.wait_for_timeout(1_000)
    items = await page.evaluate(_VISIBLE_JS, "[role=menuitem], [role=menu] button, .cdk-overlay-pane button")
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(500)
    backdrop = page.locator(".cdk-overlay-backdrop-showing").first
    if await backdrop.count():
        await backdrop.click(timeout=3_000, force=True)
        await page.wait_for_timeout(400)
    return items


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--media", required=True)
    args = ap.parse_args()
    report: dict[str, Any] = {}
    Path("out").mkdir(exist_ok=True)
    async with FlowSession(args.profile) as session:
        page = session.page
        await session.goto(
            f"{session.project_url(args.project)}/edit/{args.media}", ready="flow-scene-builder"
        )
        await page.wait_for_timeout(5_000)
        report["extend_menu_initial"] = await page.evaluate(_EXTEND_JS)
        clip = page.locator(
            "flow-scene-timeline flow-thumbnail-strip, flow-scene-timeline [class*=clip]"
        ).first
        if await clip.count():
            await clip.hover(timeout=8_000)
            await page.wait_for_timeout(800)
        report["extend_menu_after_hover"] = await page.evaluate(_EXTEND_JS)
        report["timeline_buttons"] = await page.evaluate(_VISIBLE_JS, "flow-scene-timeline button")
        await page.screenshot(path="out/t9_edit_hover.png")
        for label in ("More options", "Download media", "Add clip"):
            report[label] = await open_menu(page, label)
            await page.screenshot(path=f"out/t9_edit_{label.split()[0].lower()}.png")
        report["all_buttons"] = await page.evaluate(_VISIBLE_JS, "button")
    Path("out/t9_edit_report.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    for key, value in report.items():
        print(f"{key}: {json.dumps(value, ensure_ascii=False)[:700]}")


if __name__ == "__main__":
    asyncio.run(main())
