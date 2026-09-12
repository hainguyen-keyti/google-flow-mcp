"""Spike for T10: three $0 looks at the surfaces gflow never drove on the migrated host.
1. Tools page: /project/<id>/tools (DOM, rpcids).
2. Scenes: 'Add media' > 'New scene' (URL, DOM, rpcids).
3. Agent mode: toggle the agent chip and dump the agent prompt box; nothing is sent unless --send.

uv run python -m video.probes.agent_tools_scenes --project <id> [--send "question"]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from video.flow.reader import capture
from video.session import PROJECT_READY, FlowSession

_DOM_JS = """
() => {
  const txt = (e) => (e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 70);
  const uniq = (arr) => [...new Set(arr.filter(Boolean))];
  return {
    url: location.href,
    elements: uniq([...document.querySelectorAll('*')].map(e => e.tagName.toLowerCase()).filter(t => t.startsWith('flow-'))).slice(0, 60),
    buttons: uniq([...document.querySelectorAll('button')].filter(b => b.offsetParent !== null).map(txt)).slice(0, 50),
    inputs: uniq([...document.querySelectorAll('input,textarea,[contenteditable="true"]')].map(e => e.tagName.toLowerCase() + ':' + txt(e))).slice(0, 15),
    text: (document.body.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 700),
  };
}
"""


def rpc_counts(frames: dict[str, list[Any]]) -> dict[str, int]:
    return {k: len(v) for k, v in sorted(frames.items())}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--send", default=None, help="Send this message in agent mode (may spend credits).")
    args = ap.parse_args()
    report: dict[str, Any] = {}
    Path("out").mkdir(exist_ok=True)
    async with FlowSession(args.profile) as session:
        page = session.page
        frames = await capture(
            session, lambda: session.goto(f"{session.project_url(args.project)}/tools"), settle=10.0
        )
        report["tools"] = {"rpcids": rpc_counts(frames), "dom": await page.evaluate(_DOM_JS)}
        await page.screenshot(path="out/t10_tools.png")

        await session.goto(session.project_url(args.project), ready=PROJECT_READY)
        await page.wait_for_timeout(2_500)
        await page.get_by_role("button", name=re.compile("Add media", re.IGNORECASE)).first.click(
            timeout=8_000
        )
        await page.wait_for_timeout(1_000)
        scene_item = (
            page.locator("[role=menuitem], .cdk-overlay-pane button")
            .filter(has_text=re.compile("New scene", re.IGNORECASE))
            .first
        )
        frames = await capture(session, lambda: scene_item.click(timeout=8_000), settle=8.0)
        report["scene"] = {"rpcids": rpc_counts(frames), "dom": await page.evaluate(_DOM_JS)}
        await page.screenshot(path="out/t10_scene.png")

        await session.goto(session.project_url(args.project), ready=PROJECT_READY)
        await page.wait_for_timeout(2_500)
        chip = page.locator("button.agent-mode-chip, flow-agent-mode-toggle-chip button").first
        frames = await capture(session, lambda: chip.click(timeout=8_000), settle=4.0)
        report["agent"] = {
            "rpcids": rpc_counts(frames),
            "pressed": await chip.get_attribute("aria-pressed"),
            "dom": await page.evaluate(_DOM_JS),
        }
        await page.screenshot(path="out/t10_agent.png")
        if args.send:
            box = page.locator(
                "flow-prompt-box [contenteditable='true'], flow-creative-agent-prompt-box [contenteditable='true']"
            ).first
            await box.click(timeout=8_000)
            await page.keyboard.type(args.send)
            send = (
                page.locator("flow-prompt-box button, flow-creative-agent-prompt-box button")
                .filter(has_text=re.compile("arrow_forward|send", re.IGNORECASE))
                .last
            )
            frames = await capture(session, lambda: send.click(timeout=8_000), settle=45.0)
            report["agent_send"] = {"rpcids": rpc_counts(frames), "dom": await page.evaluate(_DOM_JS)}
            await page.screenshot(path="out/t10_agent_reply.png")
    Path("out/t10_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    for key, value in report.items():
        print(f"== {key}: rpcids={value.get('rpcids')} pressed={value.get('pressed')}")
        dom = value.get("dom") or {}
        print("   url:", dom.get("url"))
        print("   elements:", dom.get("elements", [])[:25])
        print("   buttons:", dom.get("buttons", [])[:25])
        print("   inputs:", dom.get("inputs", [])[:8])
        print("   text:", (dom.get("text") or "")[:350])


if __name__ == "__main__":
    asyncio.run(main())
