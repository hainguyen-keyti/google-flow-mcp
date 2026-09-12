"""How to leave Flow's agent session: dump the agent panel and agent composer controls, click Close, dump
again. $0.

uv run python -m video.probes.agent_panel --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re

from video.session import PROJECT_READY, FlowSession

_STATE_JS = """() => ({
  chips: [...document.querySelectorAll('button.agent-mode-chip, flow-agent-mode-toggle-chip button')]
    .map(b => ({pressed: b.getAttribute('aria-pressed'), visible: b.offsetParent !== null})),
  settings_hidden: [...document.querySelectorAll('.settings-trigger-button')].map(b => b.hasAttribute('hidden')),
  agent_tags: [...new Set([...document.querySelectorAll('*')].map(e => e.tagName.toLowerCase())
    .filter(t => t.startsWith('flow-') && (t.includes('agent') || t.includes('prompt-box'))))],
  pressed: [...document.querySelectorAll('[aria-pressed]')].filter(e => e.offsetParent !== null)
    .map(e => ({tag: e.tagName.toLowerCase(), cls: e.className.toString().slice(0, 60),
               label: (e.getAttribute('aria-label') || e.innerText || '').trim().slice(0, 40),
               pressed: e.getAttribute('aria-pressed')})),
  panel_buttons: [...document.querySelectorAll('flow-agent-panel button')].filter(e => e.offsetParent !== null)
    .map(e => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40)),
  composer_buttons: [...document.querySelectorAll('flow-creative-agent-prompt-box button, flow-prompt-box button')]
    .filter(e => e.offsetParent !== null)
    .map(e => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40)),
  close_buttons: [...document.querySelectorAll('button')].filter(e => e.offsetParent !== null)
    .filter(e => /^close$/i.test((e.getAttribute('aria-label') || e.innerText || '').trim()))
    .map(e => ({parent_chain: (() => { const c = []; let n = e.parentElement; while (n && c.length < 6) { c.push(n.tagName.toLowerCase()); n = n.parentElement; } return c.join('>'); })()})),
})"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        page = session.page
        await session.goto(session.project_url(args.project), ready=PROJECT_READY)
        await page.wait_for_timeout(4_000)
        print("before:", json.dumps(await page.evaluate(_STATE_JS), ensure_ascii=False))
        close = page.get_by_role("button", name=re.compile("^Close$", re.IGNORECASE)).last
        print("close buttons:", await close.count())
        if await close.count():
            await close.click(timeout=8_000)
            await page.wait_for_timeout(3_000)
        print("after close:", json.dumps(await page.evaluate(_STATE_JS), ensure_ascii=False))
        await page.screenshot(path="out/t10_agent_after_close.png")
        chip = page.locator("button.agent-mode-chip, flow-agent-mode-toggle-chip button").first
        if await chip.count() and (await chip.get_attribute("aria-pressed")) == "true":
            await chip.click(timeout=8_000)
            await page.wait_for_timeout(3_000)
            print("after chip off:", json.dumps(await page.evaluate(_STATE_JS), ensure_ascii=False))
        await page.reload()
        await page.wait_for_selector(PROJECT_READY, timeout=60_000)
        await page.wait_for_timeout(4_000)
        print("after reload:", json.dumps(await page.evaluate(_STATE_JS), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
