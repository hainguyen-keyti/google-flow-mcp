"""Can a character entity be attached to a video prompt as an ingredient? $0.

Opens the project composer, clicks "Add ingredients to the prompt box" and dumps what the picker offers
(tabs, characters, uploads), then tries to pick the given entity and reports whether a chip lands in the
prompt box. Nothing is generated, so this costs nothing.

uv run python -m video.probes.ingredients --project <id> [--entity <entity_id>]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re

from video.flow.reader import capture
from video.session import PROJECT_READY, FlowSession

_OVERLAY_JS = """() => [...document.querySelectorAll('[role=dialog], mat-dialog-container, .cdk-overlay-pane, [role=menu]')]
  .filter(e => e.offsetParent !== null)
  .map(e => ({tag: e.tagName.toLowerCase(),
              text: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 400),
              buttons: [...e.querySelectorAll('button, [role=menuitem], [role=tab]')]
                .map(b => (b.getAttribute('aria-label') || b.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40))
                .filter(Boolean).slice(0, 25),
              images: e.querySelectorAll('img').length}))"""
_ALL_BUTTONS_JS = """() => [...document.querySelectorAll('flow-prompt-box button, flow-base-prompt-box button, flow-creative-agent-prompt-box button')]
  .map(e => ({label: (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 44),
              visible: e.offsetParent !== null, disabled: e.disabled}))"""
_PROMPT_JS = """() => ({
  box: [...document.querySelectorAll('flow-prompt-box, flow-base-prompt-box')]
    .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 200)),
  chips: [...document.querySelectorAll('flow-prompt-box [class*=chip], flow-base-prompt-box [class*=chip], [class*=ingredient]')]
    .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60)).filter(Boolean),
})"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--entity", default=None, help="Character entity to try to attach.")
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        page = session.page
        await session.goto(session.project_url(args.project), ready=PROJECT_READY)
        await page.wait_for_timeout(3_000)
        for selector in ("flow-prompt-box", "flow-base-prompt-box", "flow-creative-agent-prompt-box"):
            box = page.locator(selector).first
            if await box.count():
                print("composer component:", selector)
                break
        print(
            "composer buttons:", json.dumps(await page.evaluate(_ALL_BUTTONS_JS), ensure_ascii=False)[:1200]
        )
        selector = "flow-prompt-box button[aria-label*='ngredient'], flow-base-prompt-box button[aria-label*='ngredient']"
        add = page.locator(selector).first
        if await add.count() == 0:
            # The composer keeps whichever mode was used last; a failed i2v leaves it on Frames, which has
            # no ingredients button at all. Open the settings trigger and dump what modes exist.
            trigger = page.locator("button[aria-label*='Settings trigger'], .settings-trigger-button").first
            print("settings trigger count:", await trigger.count())
            if await trigger.count():
                await trigger.click(timeout=8_000)
                await page.wait_for_timeout(2_000)
                print(
                    "settings overlay:",
                    json.dumps(await page.evaluate(_OVERLAY_JS), ensure_ascii=False)[:2000],
                )
                await page.screenshot(path="out/t2_composer_settings.png")
                for wanted in ("Ingredients to video", "Ingredients", "Text to video"):
                    option = (
                        page.locator("[role=menuitem], [role=option], .cdk-overlay-pane button")
                        .filter(has_text=re.compile(wanted, re.IGNORECASE))
                        .first
                    )
                    if await option.count():
                        print("switching composer mode to:", wanted)
                        await option.click(timeout=8_000)
                        await page.wait_for_timeout(3_000)
                        break
                else:
                    print("no mode option matched")
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(1_500)
            print(
                "composer buttons after mode switch:",
                json.dumps(await page.evaluate(_ALL_BUTTONS_JS), ensure_ascii=False)[:1200],
            )
            add = page.locator(selector).first
        print("add-ingredients button count:", await add.count())
        if await add.count() == 0:
            await page.screenshot(path="out/t2_no_ingredients.png")
            print("composer has no ingredients button on this page")
            return
        frames = await capture(session, lambda: add.click(timeout=8_000), settle=6.0)
        print("rpcids after opening the picker:", sorted(frames))
        overlays = await page.evaluate(_OVERLAY_JS)
        print("picker:", json.dumps(overlays, ensure_ascii=False)[:2200])
        await page.screenshot(path="out/t2_ingredients_picker.png")

        target = None
        for label in ("Characters", "Character"):
            tab = page.get_by_role("tab", name=re.compile(f"^{label}", re.IGNORECASE)).first
            if await tab.count():
                target = tab
                break
        if target is not None:
            await target.click(timeout=8_000)
            await page.wait_for_timeout(2_000)
            print(
                "after Characters tab:",
                json.dumps(await page.evaluate(_OVERLAY_JS), ensure_ascii=False)[:1600],
            )
            await page.screenshot(path="out/t2_ingredients_characters.png")
        else:
            print("no Characters tab found by role=tab")

        print(
            "prompt state before pick:", json.dumps(await page.evaluate(_PROMPT_JS), ensure_ascii=False)[:600]
        )
        tile = page.locator(".cdk-overlay-pane img, [role=dialog] img").first
        print("pickable tiles:", await page.locator(".cdk-overlay-pane img, [role=dialog] img").count())
        if await tile.count():
            pick_frames = await capture(session, lambda: tile.click(timeout=8_000), settle=5.0)
            print("rpcids after picking:", sorted(pick_frames))
            await page.wait_for_timeout(2_000)
            print(
                "prompt state after pick:",
                json.dumps(await page.evaluate(_PROMPT_JS), ensure_ascii=False)[:800],
            )
            await page.screenshot(path="out/t2_ingredients_picked.png")


if __name__ == "__main__":
    asyncio.run(main())
