"""Why does the character editor's Delete button do nothing? Dump the editor's visible buttons, click
Delete, report dialogs and text changes. $0 (the delete is only confirmed when --confirm is given).

uv run python -m video.probes.character_editor --project <id> --entity <entity_id> [--confirm]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re

from video.flow import characters
from video.flow.reader import capture
from video.session import FlowSession

_BUTTONS_JS = """() => [...document.querySelectorAll('button')].filter(e => e.offsetParent !== null)
  .map(e => ({label: (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 50),
              cls: e.className.toString().slice(0, 50), disabled: e.disabled}))"""
_DIALOGS_JS = """() => [...document.querySelectorAll('[role=dialog], mat-dialog-container, .cdk-overlay-pane')]
  .filter(e => e.offsetParent !== null)
  .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 300))"""
_TEXT_JS = "() => (document.body.innerText || '').trim().replace(/\\s+/g, ' ')"


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
        print("url:", page.url)
        print("buttons:", json.dumps(await page.evaluate(_BUTTONS_JS), ensure_ascii=False)[:2500])
        deletes = page.get_by_role("button", name=re.compile("^Delete$", re.IGNORECASE))
        print("delete buttons by role:", await deletes.count())
        text_before = await page.evaluate(_TEXT_JS)
        print("text:", text_before[:400])
        if await deletes.count() == 0:
            await page.screenshot(path="out/t7_character_editor.png")
            return
        frames = await capture(session, lambda: deletes.first.click(timeout=8_000), settle=3.0)
        print("after click rpcids:", sorted(frames))
        print("dialogs:", json.dumps(await page.evaluate(_DIALOGS_JS), ensure_ascii=False)[:1200])
        text_after = await page.evaluate(_TEXT_JS)
        print(
            "text delta:",
            text_after[len(text_before) - 50 if len(text_after) > len(text_before) else 0 :][:500],
        )
        await page.screenshot(path="out/t7_character_delete_click.png")
        if args.confirm:
            dialog = page.locator("[role=dialog], mat-dialog-container").first
            confirm = dialog.get_by_role(
                "button", name=re.compile("delete|remove|confirm", re.IGNORECASE)
            ).first
            print("confirm buttons:", await confirm.count())
            if await confirm.count():
                frames = await capture(session, lambda: confirm.click(timeout=8_000), settle=5.0)
                print("confirm rpcids:", sorted(frames))
            remaining = await characters.list_characters(session, args.project)
            print("remaining:", [c["entity_id"] for c in remaining])


if __name__ == "__main__":
    asyncio.run(main())
