"""What does ONE click on Start generation do after entering Extend? Clicks exactly once and then watches
rpcids, the credit balance and new records for a few minutes. Spends whatever that one click costs.

uv run python -m video.probes.extend_submit --project <id> --media <media_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time

from video.flow import clips, reader
from video.session import FlowSession

_MODE_JS = """() => ({
  timeline: [...document.querySelectorAll('flow-scene-timeline')].map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 160)),
  placeholder: [...document.querySelectorAll('flow-base-prompt-box, flow-edit-video-prompt-box')]
    .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 120)),
  boxes: [...document.querySelectorAll('[contenteditable=true]')].map(e => ({
    host: (() => { let n = e.parentElement; while (n && !n.tagName.toLowerCase().startsWith('flow-')) n = n.parentElement;
                   return n ? n.tagName.toLowerCase() : '?'; })(),
    text: (e.innerText || '').trim().slice(0, 60)})),
  prompt_components: [...new Set([...document.querySelectorAll('*')].map(e => e.tagName.toLowerCase())
    .filter(t => t.includes('prompt-box') || t.includes('extend')))],
})"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--media", required=True)
    ap.add_argument("--prompt", default="the sailboat keeps rocking gently, camera holds still")
    ap.add_argument("--watch", type=float, default=240.0)
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        page = session.page
        rows_before, scenes_before = await clips._snapshot(session, args.project)
        before_ids = {r["workflow_id"] for r in rows_before}
        credits_before = (await reader.credits(session))["balance"]
        print("credits before:", credits_before, "records:", len(rows_before))
        await clips._open(session, args.project, args.media)
        item = await clips._menu_item(session, "Add clip", "Extend")
        await item.click(timeout=8_000)
        await page.wait_for_timeout(2_000)
        box = page.locator(f"{clips.EDITOR} [contenteditable='true']").first
        start = page.get_by_role("button", name=re.compile("Start generation", re.IGNORECASE)).first
        ready = await clips._prompt_ready(page, box, start, args.prompt)
        print("prompt ready:", ready)
        print("mode at click:", json.dumps(await page.evaluate(_MODE_JS), ensure_ascii=False)[:1200])
        frames = await reader.capture(session, lambda: start.click(timeout=8_000), settle=30.0)
        print("ONE click, rpcids within 30s:", sorted(frames))
        print("mode after click:", json.dumps(await page.evaluate(_MODE_JS), ensure_ascii=False)[:1200])
        await page.screenshot(path="out/t9_extend_after_click.png")
        deadline = time.time() + args.watch
        while time.time() < deadline:
            await asyncio.sleep(30)
            rows, scenes = await clips._snapshot(session, args.project)
            fresh = clips.new_records(before_ids, rows)
            credits_now = (await reader.credits(session))["balance"]
            print(
                f"t+{int(time.time() - (deadline - args.watch))}s credits={credits_now} "
                f"spent={credits_before - credits_now} new_scenes={sorted(scenes - scenes_before)}"
            )
            for row in fresh:
                print(
                    f"   {row['workflow_id'][:8]} media={row['id'][:8]} status={row['status']} "
                    f"role={clips.role_of(row, args.prompt)} prompt={(row['prompt'] or '')[:50]!r}"
                )
            if fresh and any(clips.role_of(r, args.prompt) == "generated" for r in fresh):
                print("generated record appeared")
                break


if __name__ == "__main__":
    asyncio.run(main())
