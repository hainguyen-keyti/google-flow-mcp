"""Does the Characters sidebar item change the URL, and does a fresh load of that URL fetch the
list (WuwhI)? $0: one click, one navigation.

uv run python -m video.probes.characters_route --profile default --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json

from video.flow.reader import capture
from video.session import PROJECT_READY, FlowSession

_JS = """
() => ({
  url: location.href,
  elements: [...new Set([...document.querySelectorAll('mat-sidenav-content *')]
    .map(e => e.tagName.toLowerCase()).filter(t => t.includes('-')))].slice(0, 40),
  text: (document.querySelector('mat-sidenav-content')?.innerText || '').trim().slice(0, 300),
})
"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        await session.goto(session.project_url(args.project), ready=PROJECT_READY)
        await session.page.wait_for_timeout(4_000)
        nav = session.page.locator("mat-list-item", has_text="Characters").first
        frames = await capture(session, lambda: nav.click(timeout=8_000), settle=5.0)
        after_click = await session.page.evaluate(_JS)
        print("after click: rpcids=", sorted(frames), json.dumps(after_click, ensure_ascii=False)[:600])
        target = after_click["url"]
        frames2 = await capture(session, lambda: session.goto(target), settle=8.0)
        after_goto = await session.page.evaluate(_JS)
        print("after goto : rpcids=", sorted(frames2), json.dumps(after_goto, ensure_ascii=False)[:600])
        if "WuwhI" in frames2:
            print("WuwhI payload:", json.dumps(frames2["WuwhI"][0])[:300])


if __name__ == "__main__":
    asyncio.run(main())
