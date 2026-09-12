"""Deleting a character right after creating it fails (dialog_seen=False, no rpc) while old characters
delete fine: create one, then watch the editor's Delete button and the confirm overlay over time. $0.

uv run python -m video.probes.character_fresh_delete --project <id>
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
  .map(e => ({label: (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40),
              disabled: e.disabled, pressed: e.getAttribute('aria-pressed')}))
  .filter(b => /delete|done|history|portrait|body|generation/i.test(b.label))"""
_OVERLAYS_JS = """() => [...document.querySelectorAll('[role=dialog], mat-dialog-container, .cdk-overlay-pane')]
  .filter(e => e.offsetParent !== null)
  .map(e => ({tag: e.tagName.toLowerCase(), text: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 200)}))"""
_TEXT_JS = "() => (document.body.innerText || '').trim().replace(/\\s+/g, ' ')"


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--entity", default=None, help="Reuse an existing character instead of creating one.")
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        page = session.page
        entity = args.entity
        if not entity:
            created = await characters.create(
                session,
                args.project,
                "young woman with short black hair and a warm smile, studio portrait, soft light",
                name="Fresh Delete Probe",
                personality="Calm.",
            )
            entity = created["entity_id"]
            print("created:", entity, "rpcids", created["rpcids"], "done_rpcids", created.get("done_rpcids"))
        listed = await characters.list_characters(session, args.project)
        print("listed:", [c["entity_id"] for c in listed])
        await characters._open_editor(session, args.project, entity)
        print("url:", page.url)
        for attempt in range(4):
            print(
                f"attempt {attempt} buttons:",
                json.dumps(await page.evaluate(_BUTTONS_JS), ensure_ascii=False),
            )
            trash = page.get_by_role("button", name=re.compile("^Delete$", re.IGNORECASE))
            print("  delete count:", await trash.count())
            text_before = await page.evaluate(_TEXT_JS)
            frames = await capture(session, lambda t=trash: t.first.click(timeout=8_000), settle=3.0)
            overlays = await page.evaluate(_OVERLAYS_JS)
            text_after = await page.evaluate(_TEXT_JS)
            print(
                "  after click rpcids:", sorted(frames), "overlays:", json.dumps(overlays, ensure_ascii=False)
            )
            print(
                "  text delta:",
                text_after[len(text_before) - 20 if len(text_after) > len(text_before) else 0 :][:200],
            )
            await page.screenshot(path=f"out/t7_fresh_delete_{attempt}.png")
            if overlays:
                confirm = (
                    page.locator("[role=dialog], mat-dialog-container, .cdk-overlay-pane")
                    .filter(
                        has=page.get_by_role(
                            "button", name=re.compile("delete|remove|confirm", re.IGNORECASE)
                        )
                    )
                    .last.get_by_role("button", name=re.compile("delete|remove|confirm", re.IGNORECASE))
                    .first
                )
                print("  confirm buttons:", await confirm.count())
                frames = await capture(session, lambda c=confirm: c.click(timeout=8_000), settle=5.0)
                print("  confirm rpcids:", sorted(frames))
                break
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(10_000)
        remaining = await characters.list_characters(session, args.project)
        print("remaining:", [c["entity_id"] for c in remaining])


if __name__ == "__main__":
    asyncio.run(main())
