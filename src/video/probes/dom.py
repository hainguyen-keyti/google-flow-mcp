"""Dump what a Flow URL renders: custom elements, buttons, inputs, visible text, batchexecute rpcids.
$0: one navigation, no clicks.

uv run python -m video.probes.dom --url https://flow.google.com/project/<id>/character [--settle 10]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from video.flow.reader import capture
from video.session import FlowSession

_JS = """
() => {
  const txt = (e) => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
  const uniq = (arr) => [...new Set(arr.filter(Boolean))];
  return {
    url: location.href,
    title: document.title,
    elements: uniq([...document.querySelectorAll('*')].map(e => e.tagName.toLowerCase()).filter(t => t.includes('-'))).slice(0, 120),
    buttons: uniq([...document.querySelectorAll('button')].map(txt)).slice(0, 80),
    inputs: uniq([...document.querySelectorAll('input,textarea,[contenteditable="true"]')].map(e => (e.getAttribute('placeholder') || e.getAttribute('aria-label') || e.className || '').toString().slice(0, 60))).slice(0, 30),
    links: uniq([...document.querySelectorAll('a')].map(a => txt(a) + ' -> ' + (a.getAttribute('href') || ''))).slice(0, 40),
    text: (document.body.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 1200),
  };
}
"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--url", required=True)
    ap.add_argument("--settle", type=float, default=10.0)
    ap.add_argument("--screenshot", default="out/dom.png")
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        frames = await capture(session, lambda: session.goto(args.url), settle=args.settle)
        dom = await session.page.evaluate(_JS)
        Path("out").mkdir(exist_ok=True)
        await session.page.screenshot(path=args.screenshot)
    dom["rpcids"] = {rpcid: len(payloads) for rpcid, payloads in sorted(frames.items())}
    Path("out/dom.json").write_text(json.dumps(dom, indent=1, ensure_ascii=False), encoding="utf-8")
    for key in ("url", "title", "rpcids", "elements", "buttons", "inputs", "links"):
        print(f"--- {key}: {json.dumps(dom[key], ensure_ascii=False)[:900]}")
    print(f"--- text: {dom['text'][:700]}")
    print(f"(screenshot {args.screenshot}, out/dom.json)")


if __name__ == "__main__":
    asyncio.run(main())
