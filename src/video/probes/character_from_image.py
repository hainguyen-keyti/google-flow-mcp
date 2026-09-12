"""Can a Flow character take the owner's OWN photo as its portrait, instead of a generated face? $0.

Walks the new-character page and the character editor, dumping which of "Upload", "Add from project" and
"Create portrait" exist at each step, then tries the image path and reports the resulting entity.

uv run python -m video.probes.character_from_image --project <id> [--image assets/character/01_portrait_closeup.png]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from video.flow import characters
from video.flow.reader import capture
from video.session import FlowSession

_BUTTONS_JS = """() => [...document.querySelectorAll('button')].filter(e => e.offsetParent !== null)
  .map(e => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 44))
  .filter(Boolean)"""
_DIALOG_JS = """() => [...document.querySelectorAll('[role=dialog], mat-dialog-container, .cdk-overlay-pane')]
  .filter(e => e.offsetParent !== null)
  .map(e => ({tag: e.tagName.toLowerCase(),
              text: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 260),
              tiles: e.querySelectorAll('flow-tile-container, img').length}))"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--image", default="assets/character/01_portrait_closeup.png")
    ap.add_argument("--route", choices=["upload", "project"], default="upload")
    args = ap.parse_args()
    image = Path(args.image).resolve()
    if not image.is_file():
        raise SystemExit(f"image not found: {image}")
    async with FlowSession(args.profile) as session:
        page = session.page
        await session.goto(f"{session.project_url(args.project)}/character", ready=characters.NEW_PAGE)
        await page.wait_for_timeout(3_000)
        print("new-character url:", page.url)
        print(
            "new-character buttons:", json.dumps(await page.evaluate(_BUTTONS_JS), ensure_ascii=False)[:1200]
        )

        label = "Upload" if args.route == "upload" else "Add from project"
        button = page.get_by_role("button", name=re.compile(label, re.IGNORECASE)).first
        print(f"{label!r} present on new-character page:", await button.count())
        if await button.count() == 0:
            print("route unavailable here; the entity may have to exist first")
            return

        if args.route == "upload":

            async def act() -> None:
                async with page.expect_file_chooser(timeout=15_000) as chooser:
                    await button.click(timeout=8_000)
                picked = await chooser.value
                await picked.set_files(str(image))
        else:

            async def act() -> None:
                await button.click(timeout=8_000)

        frames = await capture(session, act, settle=25.0)
        print("rpcids after the image step:", sorted(frames))
        print("dialogs:", json.dumps(await page.evaluate(_DIALOG_JS), ensure_ascii=False)[:1200])
        print("url now:", page.url)
        print("buttons now:", json.dumps(await page.evaluate(_BUTTONS_JS), ensure_ascii=False)[:1200])
        await page.screenshot(path="out/t2_character_from_image.png")

        entity = None
        try:
            entity = characters.entity_id_from_url(page.url)
        except ValueError:
            print("no entity id in the url yet")
        print("entity:", entity)
        listed = await characters.list_characters(session, args.project)
        print("characters now:", json.dumps(listed, ensure_ascii=False)[:600])


if __name__ == "__main__":
    asyncio.run(main())
