"""Spike for T9: enumerate the composer settings on this account: mode, submode, aspect, resolution,
duration, count, model menu, and the Start/End frame chips. $0: opens the chip, reads, closes.

uv run python -m video.probes.composer_options --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from video.session import PROJECT_READY, FlowSession

_PANE_JS = """
() => {
  const pane = document.querySelector('.cdk-overlay-pane');
  if (!pane) return null;
  const groups = [...pane.querySelectorAll('[role=radiogroup]')].map(g => ({
    label: (g.getAttribute('aria-label') || '').slice(0, 40),
    radios: [...g.querySelectorAll('[role=radio]')].map(r => ({
      text: (r.innerText || r.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 40),
      checked: r.getAttribute('aria-checked'),
      disabled: r.getAttribute('aria-disabled'),
    })),
  }));
  return {
    text: (pane.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 600),
    groups,
    buttons: [...pane.querySelectorAll('button')].map(b => (b.getAttribute('aria-label') || b.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 50)),
  };
}
"""
_MENU_JS = """
() => [...document.querySelectorAll('[role=menu] [role=menuitem], [role=menu] button')]
  .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60)).filter(Boolean)
"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    report: dict[str, Any] = {}
    Path("out").mkdir(exist_ok=True)
    async with FlowSession(args.profile) as session:
        page = session.page
        await session.goto(session.project_url(args.project), ready=PROJECT_READY)
        await page.wait_for_timeout(3_000)
        chip = page.locator(".settings-trigger-button").first
        report["chip_text"] = (await chip.inner_text()).strip()
        await chip.click(timeout=8_000)
        await page.wait_for_timeout(1_500)
        report["pane"] = await page.evaluate(_PANE_JS)
        await page.screenshot(path="out/t9_settings.png")
        model_button = (
            page.locator(".cdk-overlay-pane button").filter(has_text=re.compile("arrow_drop_down")).first
        )
        if await model_button.count():
            await model_button.click(timeout=8_000)
            await page.wait_for_timeout(1_200)
            report["model_menu"] = await page.evaluate(_MENU_JS)
            await page.screenshot(path="out/t9_models.png")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
        chips = page.locator("flow-prompt-box button").filter(has_text=re.compile("^(Start|End)$"))
        report["frame_chips"] = await chips.count()
        agent = page.locator("button.agent-mode-chip, flow-agent-mode-toggle-chip button").first
        report["agent_chip"] = {
            "found": await agent.count(),
            "pressed": await agent.get_attribute("aria-pressed") if await agent.count() else None,
        }
    Path("out/t9_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print("chip:", report["chip_text"])
    pane = report.get("pane") or {}
    for group in pane.get("groups", []):
        print("group", group["label"] or "-", ":", [(r["text"], r["checked"]) for r in group["radios"]])
    print("pane buttons:", pane.get("buttons"))
    print("pane text:", pane.get("text", "")[:300])
    print("model menu:", report.get("model_menu"))
    print("frame chips:", report["frame_chips"], "| agent chip:", report["agent_chip"])


if __name__ == "__main__":
    asyncio.run(main())
