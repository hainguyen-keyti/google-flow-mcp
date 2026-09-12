"""Spike for T7: what happens on the New-character page when a face prompt is generated.
Types a prompt, clicks Start generation, records rpcids and the editor DOM afterwards. Does NOT save.
Image generation only (0 credits measured 2026-09-12).

uv run python -m video.probes.character_create --project <id> --prompt "..."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from video.flow.reader import capture
from video.session import FlowSession

_DOM_JS = """
() => {
  const txt = (e) => (e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
  const uniq = (arr) => [...new Set(arr.filter(Boolean))];
  return {
    url: location.href,
    elements: uniq([...document.querySelectorAll('*')].map(e => e.tagName.toLowerCase()).filter(t => t.startsWith('flow-') || t.startsWith('mat-'))).slice(0, 80),
    buttons: uniq([...document.querySelectorAll('button')].filter(b => b.offsetParent !== null).map(txt)).slice(0, 60),
    inputs: uniq([...document.querySelectorAll('input,textarea,select,[contenteditable="true"]')].map(e => e.tagName.toLowerCase() + ':' + (e.className || '').toString().slice(0, 40) + ':' + txt(e))).slice(0, 30),
    images: [...document.querySelectorAll('img')].filter(i => i.offsetParent !== null).map(i => (i.getAttribute('src') || '').split('?')[0].slice(0, 80)).slice(0, 12),
    text: (document.body.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 900),
  };
}
"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument(
        "--prompt", default="young woman with short black hair, warm smile, studio portrait, soft light"
    )
    ap.add_argument("--wait", type=float, default=75.0)
    args = ap.parse_args()
    report: dict[str, Any] = {}
    Path("out").mkdir(exist_ok=True)
    async with FlowSession(args.profile) as session:
        page = session.page
        await session.goto(f"{session.project_url(args.project)}/character", ready="flow-character-page")
        await page.wait_for_timeout(3_000)
        report["before"] = await page.evaluate(_DOM_JS)
        box = page.locator(
            "flow-character-prompt-box [contenteditable='true'], flow-character-page .ProseMirror"
        ).first
        await box.click(timeout=8_000)
        await page.keyboard.type(args.prompt)
        await page.wait_for_timeout(800)
        await page.screenshot(path="out/t7_typed.png")
        start = page.get_by_role("button", name=re.compile("Start generation", re.IGNORECASE)).first
        frames = await capture(session, lambda: start.click(timeout=8_000), settle=args.wait)
        report["generation_rpcids"] = {k: len(v) for k, v in sorted(frames.items())}
        report["after"] = await page.evaluate(_DOM_JS)
        await page.screenshot(path="out/t7_after_generate.png")
        for rpcid in ("ogiZ0b", "WuwhI"):
            if rpcid in frames:
                report[f"{rpcid}_head"] = json.dumps(frames[rpcid][0])[:600]
    Path("out/t7_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print("rpcids:", report["generation_rpcids"])
    print("url after:", report["after"]["url"])
    print("elements after:", report["after"]["elements"][:40])
    print("buttons after:", report["after"]["buttons"][:30])
    print("inputs after:", report["after"]["inputs"][:15])
    print("images after:", report["after"]["images"][:6])
    print("text after:", report["after"]["text"][:500])


if __name__ == "__main__":
    asyncio.run(main())
