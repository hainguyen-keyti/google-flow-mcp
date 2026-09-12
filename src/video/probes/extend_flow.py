"""Step through the clip editor's Extend flow and dump the state after each step, WITHOUT clicking
Start generation, so it costs nothing. Answers: does the prompt land in a box, and is the generate
button enabled?

uv run python -m video.probes.extend_flow --project <id> --media <media_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re

from video.flow import clips
from video.session import FlowSession

_STATE_JS = """() => ({
  url: location.href,
  editable: [...document.querySelectorAll('[contenteditable=true]')].map(e => ({
    label: (e.getAttribute('aria-label') || e.getAttribute('data-placeholder') || '').slice(0, 60),
    cls: e.className.toString().slice(0, 60),
    host: (() => { let n = e.parentElement; while (n && !n.tagName.toLowerCase().startsWith('flow-')) n = n.parentElement;
                   return n ? n.tagName.toLowerCase() : '?'; })(),
    visible: e.offsetParent !== null,
    text: (e.innerText || '').trim().slice(0, 80),
  })),
  generate: [...document.querySelectorAll('button')].filter(e => /start generation|generate/i.test(
      (e.getAttribute('aria-label') || e.innerText || ''))).map(e => ({
    label: (e.getAttribute('aria-label') || e.innerText || '').trim().slice(0, 40),
    disabled: e.disabled, visible: e.offsetParent !== null,
    host: (() => { let n = e.parentElement; while (n && !n.tagName.toLowerCase().startsWith('flow-')) n = n.parentElement;
                   return n ? n.tagName.toLowerCase() : '?'; })(),
  })),
  flow_components: [...new Set([...document.querySelectorAll('*')].map(e => e.tagName.toLowerCase())
    .filter(t => t.startsWith('flow-') && /prompt|extend|scene|clip|compos/.test(t)))],
  timeline: [...document.querySelectorAll('flow-scene-timeline')].map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 200)),
})"""


async def dump(page, label: str) -> None:
    print(f"--- {label}:", json.dumps(await page.evaluate(_STATE_JS), ensure_ascii=False)[:2500])


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--media", required=True)
    ap.add_argument("--prompt", default="the sailboat keeps rocking gently, camera holds still")
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        page = session.page
        await clips._open(session, args.project, args.media)
        await dump(page, "opened")
        item = await clips._menu_item(session, "Add clip", "Extend")
        print("extend item text:", (await item.inner_text()).strip()[:60])
        await item.click(timeout=8_000)
        await page.wait_for_timeout(3_000)
        await dump(page, "after Extend")
        box = page.locator(f"{clips.EDITOR} [contenteditable='true']").first
        print("driver box count:", await box.count())
        await box.click(timeout=8_000)
        await page.keyboard.type(args.prompt)
        await page.wait_for_timeout(1_500)
        await dump(page, "after typing")
        start = page.get_by_role("button", name=re.compile("Start generation", re.IGNORECASE)).first
        print("driver start button count:", await start.count(), "disabled:", await start.is_disabled())
        await page.screenshot(path="out/t9_extend_flow.png")
        print("NOT clicking Start generation (probe is $0)")


if __name__ == "__main__":
    asyncio.run(main())
