"""How does the composer settings panel behave? $0.

Opens the settings trigger and clicks mode, aspect and count one at a time, dumping the overlay text
after each click, so we learn whether the panel stays open and where the live price line lives.

uv run python -m video.probes.composer_settings --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json

from video.flow import composer
from video.session import PROJECT_READY, FlowSession

_OVERLAY_JS = """() => [...document.querySelectorAll('.cdk-overlay-pane, [role=menu], [role=dialog]')]
  .filter(e => e.offsetParent !== null)
  .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 300))"""


async def show(page, label: str) -> None:
    panes = await page.evaluate(_OVERLAY_JS)
    joined = " | ".join(panes)
    print(f"{label}: panes={len(panes)} price={composer.price_from(joined)} text={json.dumps(joined)[:400]}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        page = session.page
        await session.goto(session.project_url(args.project), ready=PROJECT_READY)
        await page.wait_for_timeout(3_000)
        trigger = page.locator(composer.SETTINGS).first
        print("settings trigger:", await trigger.count())
        await trigger.click(timeout=8_000)
        await page.wait_for_timeout(2_000)
        await show(page, "opened")
        for label in ("Ingredients", "9:16", "x1"):
            clicked = await composer._click_option(page, label)
            print(f"clicked {label!r}: {clicked}")
            await page.wait_for_timeout(1_500)
            await show(page, f"after {label}")
            if not await page.locator(".cdk-overlay-pane").count():
                print("  panel closed, reopening")
                await trigger.click(timeout=8_000)
                await page.wait_for_timeout(2_000)
        await page.screenshot(path="out/t4_composer_settings.png")
        await page.keyboard.press("Escape")


if __name__ == "__main__":
    asyncio.run(main())
