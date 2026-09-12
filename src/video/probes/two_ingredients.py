"""Upload the product image and check the picker can hold the character AND the product at once. $0.

The reveal shot needs both: the character entity for the face and the product image for the garment.
Plan 2 only ever attached one ingredient, so this measures whether a second one sticks.

uv run python -m video.probes.two_ingredients --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json

from video.session import PROJECT_READY, FlowSession
from video.story import composer, persona, product

_TILES_JS = """() => [...document.querySelectorAll('.cdk-overlay-pane img, [role=dialog] img')]
  .slice(0, 12)
  .map(e => ({alt: (e.getAttribute('alt') || '').slice(0, 50),
              title: (e.getAttribute('title') || '').slice(0, 50),
              label: (e.closest('button, [role=option], [role=menuitem]') || e).getAttribute('aria-label') || '',
              src: (e.getAttribute('src') || '').slice(0, 40)}))"""
_CHIPS_JS = """() => ({
  chips: [...document.querySelectorAll('flow-prompt-box [class*=chip], flow-base-prompt-box [class*=chip]')]
    .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40)).filter(Boolean),
  thumbs: document.querySelectorAll('flow-prompt-box img, flow-base-prompt-box img').length,
})"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        page = session.page
        uploaded = await product.ensure(session, args.project)
        print("product:", json.dumps(uploaded, ensure_ascii=False))

        await session.goto(session.project_url(args.project), ready=PROJECT_READY)
        await page.wait_for_timeout(2_500)
        settings = await composer.configure(session, mode="Ingredients", aspect="9:16", label="two")
        print("settings:", json.dumps(settings["applied"]), "price:", settings["price"])

        # Clicking a tile adds the ingredient AND closes the picker (measured), so the picker is reopened
        # for the second one. The open question this answers: does the first chip survive the reopen?
        for tab, wanted in (("Characters", persona.NAME), ("Uploads", product.IMAGE.name)):
            add = page.locator(composer.INGREDIENTS).first
            await add.wait_for(state="visible", timeout=20_000)
            await add.click(timeout=8_000)
            await page.wait_for_timeout(4_000)
            print(f"picker reopened for {tab}: panes={await page.locator('.cdk-overlay-pane').count()}")
            switch = composer._in_picker(page, tab)
            print(f"tab {tab!r} count:", await switch.count())
            if await switch.count():
                await switch.click(timeout=8_000)
                await page.wait_for_timeout(3_000)
            print(f"tiles on {tab}:", json.dumps(await page.evaluate(_TILES_JS), ensure_ascii=False)[:900])
            named = composer._in_picker(page, wanted)
            tile = (
                named
                if await named.count()
                else page.locator(".cdk-overlay-pane img, [role=dialog] img").first
            )
            print(f"picking {wanted!r}, by-name={await named.count()}, any-image={await tile.count()}")
            if await tile.count():
                await tile.click(timeout=8_000)
                await page.wait_for_timeout(2_500)
            print(f"chips after {tab}:", json.dumps(await page.evaluate(_CHIPS_JS), ensure_ascii=False))

        close = composer._in_picker(page, "Close")
        print("picker close button:", await close.count())
        if await close.count():
            await close.click(timeout=8_000)
        else:
            await page.keyboard.press("Escape")
        await page.wait_for_timeout(2_000)
        print("after product:", json.dumps(await page.evaluate(_CHIPS_JS), ensure_ascii=False))
        await page.screenshot(path="out/t2_two_ingredients.png")
        print("NOT clicking Start generation (probe is $0)")


if __name__ == "__main__":
    asyncio.run(main())
