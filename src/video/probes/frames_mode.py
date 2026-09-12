"""Can the composer's Frames mode pin a first frame from an existing image? $0.

Plan 2 measured that the settings panel offers Image / Video / Frames / Ingredients. Frames shows Start,
"Swap first and last frames" and End. This probe switches to Frames and opens the Start slot, dumping
whether it is a file chooser or the same picker (Images / Uploads / Videos tabs), then tries to pin the
first available image and reports whether a thumbnail actually lands in the slot.

Nothing is generated, so this costs nothing.

uv run python -m video.probes.frames_mode --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re

from video.flow.reader import capture
from video.session import PROJECT_READY, FlowSession
from video.story import composer

_SLOTS_JS = """() => [...document.querySelectorAll('flow-prompt-box button, flow-base-prompt-box button')]
  .map(e => ({label: (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40),
              visible: e.offsetParent !== null,
              thumb: e.querySelectorAll('img').length}))"""
_PICKER_JS = """() => [...document.querySelectorAll('.cdk-overlay-pane, [role=dialog], [role=menu]')]
  .filter(e => e.offsetParent !== null)
  .map(e => ({text: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 300),
              tabs: [...e.querySelectorAll('button, [role=tab], [role=menuitem]')]
                .map(b => (b.getAttribute('aria-label') || b.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 34))
                .filter(Boolean).slice(0, 20),
              images: e.querySelectorAll('img').length}))"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        page = session.page
        await session.goto(session.project_url(args.project), ready=PROJECT_READY)
        await page.wait_for_timeout(3_000)

        settings = await composer.configure(session, mode="Frames", aspect="9:16", count="x1", label="frames")
        print("settings applied:", json.dumps(settings["applied"]), "price:", settings["price"])
        print("composer slots:", json.dumps(await page.evaluate(_SLOTS_JS), ensure_ascii=False)[:900])

        start = (
            page.locator("flow-prompt-box button, flow-base-prompt-box button")
            .filter(has_text=re.compile(r"^\s*Start\s*$", re.IGNORECASE))
            .first
        )
        by_label = page.locator(
            "flow-prompt-box button[aria-label='Start'], flow-base-prompt-box button[aria-label='Start']"
        ).first
        slot = by_label if await by_label.count() else start
        print("Start slot count:", await slot.count())
        if await slot.count() == 0:
            await page.screenshot(path="out/t1_frames_no_start.png")
            print("no Start slot; Frames mode may not be available on this account")
            return

        chooser_seen = False

        def on_chooser(_: object) -> None:
            nonlocal chooser_seen
            chooser_seen = True

        page.on("filechooser", on_chooser)
        frames = await capture(session, lambda: slot.click(timeout=8_000), settle=6.0)
        print("rpcids after opening Start:", sorted(frames), "file chooser opened:", chooser_seen)
        picker = await page.evaluate(_PICKER_JS)
        print("picker:", json.dumps(picker, ensure_ascii=False)[:1600])
        await page.screenshot(path="out/t1_frames_start.png")

        tile = page.locator(".cdk-overlay-pane img, [role=dialog] img").first
        print("pickable images:", await page.locator(".cdk-overlay-pane img, [role=dialog] img").count())
        if await tile.count():
            await tile.click(timeout=8_000)
            await page.wait_for_timeout(3_000)
            print(
                "slots after picking:", json.dumps(await page.evaluate(_SLOTS_JS), ensure_ascii=False)[:900]
            )
            await page.screenshot(path="out/t1_frames_pinned.png")
        else:
            print("no image tiles inside the Start picker")
        await page.keyboard.press("Escape")
        print("NOT clicking Start generation (probe is $0)")


if __name__ == "__main__":
    asyncio.run(main())
