"""Where did an Omni edit of a clip go? Open /edit/<media>, capture the page's rpcs, click 'Show history'
and dump what it lists. $0.

uv run python -m video.probes.edit_history --project <id> --media <media_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re

from video.flow.reader import capture
from video.session import FlowSession

_TEXT_JS = "() => (document.body.innerText || '').trim().replace(/\\s+/g, ' ')"
_HISTORY_JS = """() => [...document.querySelectorAll('[class*=history], [class*=version]')]
  .filter(e => e.offsetParent !== null)
  .map(e => ({tag: e.tagName.toLowerCase(), cls: e.className.toString().slice(0, 80),
              text: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 300)}))
  .slice(0, 20)"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--media", required=True)
    ap.add_argument("--needle", default="night time")
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        page = session.page
        frames = await capture(
            session,
            lambda: session.goto(
                f"{session.project_url(args.project)}/edit/{args.media}", ready="flow-scene-builder"
            ),
            settle=10.0,
        )
        print("edit page rpcids:", {k: len(v) for k, v in sorted(frames.items())})
        for rpcid, payloads in frames.items():
            blob = json.dumps(payloads)
            if args.needle in blob:
                index = blob.find(args.needle)
                print(
                    f"rpc {rpcid} mentions {args.needle!r}: ...{blob[max(0, index - 700) : index + 300]}..."
                )
        text = await page.evaluate(_TEXT_JS)
        print("page text:", text[:500])
        history = page.get_by_role("button", name=re.compile("Show history", re.IGNORECASE)).first
        print("history button:", await history.count())
        if await history.count():
            history_frames = await capture(session, lambda: history.click(timeout=8_000), settle=6.0)
            print("history rpcids:", {k: len(v) for k, v in sorted(history_frames.items())})
            for rpcid, payloads in history_frames.items():
                blob = json.dumps(payloads)
                print(f"  {rpcid}: {blob[:600]}")
            print("history panel:", json.dumps(await page.evaluate(_HISTORY_JS), ensure_ascii=False)[:2500])
            after = await page.evaluate(_TEXT_JS)
            print("text delta:", after[len(text) - 100 if len(after) > len(text) else 0 :][:1200])
            await page.screenshot(path="out/t9_edit_history.png")


if __name__ == "__main__":
    asyncio.run(main())
