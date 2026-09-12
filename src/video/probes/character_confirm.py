"""What exactly is the "Delete" confirm that appears after the character editor's trash icon? Dump the
overlay's HTML and the clickable candidates, optionally click the best one. $0 unless --confirm.

uv run python -m video.probes.character_confirm --project <id> --entity <entity_id> [--confirm]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re

from video.flow import characters
from video.flow.reader import capture
from video.session import FlowSession

_OVERLAY_JS = """() => [...document.querySelectorAll('.cdk-overlay-container .cdk-overlay-pane, [role=dialog], mat-dialog-container')]
  .filter(e => e.offsetParent !== null)
  .map(e => ({tag: e.tagName.toLowerCase(), cls: e.className.toString().slice(0, 80),
              html: e.innerHTML.replace(/\\s+/g, ' ').slice(0, 1500),
              candidates: [...e.querySelectorAll('button, [role=menuitem], [role=button], a, mat-option, li, span, div')]
                .filter(c => /delete/i.test((c.innerText || '').trim()) && c.children.length <= 2)
                .map(c => ({tag: c.tagName.toLowerCase(), role: c.getAttribute('role'), cls: c.className.toString().slice(0, 60),
                            text: (c.innerText || '').trim().slice(0, 40)}))}))"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--entity", required=True)
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        page = session.page
        await characters._open_editor(session, args.project, args.entity)
        trash = page.get_by_role("button", name=re.compile("^Delete$", re.IGNORECASE)).first
        frames = await capture(session, lambda: trash.click(timeout=8_000), settle=3.0)
        print("after trash click rpcids:", sorted(frames))
        overlays = await page.evaluate(_OVERLAY_JS)
        print("overlays:", json.dumps(overlays, ensure_ascii=False)[:4000])
        await page.screenshot(path="out/t7_character_confirm.png")
        if args.confirm and overlays:
            target = (
                page.locator(".cdk-overlay-container .cdk-overlay-pane, [role=dialog], mat-dialog-container")
                .locator("button, [role=menuitem], [role=button], a, mat-option, li, span, div")
                .filter(has_text=re.compile("^\\s*delete\\s*$", re.IGNORECASE))
                .last
            )
            print("target count:", await target.count())
            frames = await capture(session, lambda: target.click(timeout=8_000), settle=5.0)
            print("confirm rpcids:", sorted(frames))
            remaining = await characters.list_characters(session, args.project)
            print("remaining:", [c["entity_id"] for c in remaining])


if __name__ == "__main__":
    asyncio.run(main())
