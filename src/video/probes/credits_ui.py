"""Find where the credit balance is shown in the UI so nzlxg can be cross-checked. $0: clicks on
chips and menus only, nothing typed or submitted.

uv run python -m video.probes.credits_ui --profile default --project <id> --expect 840
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from video.probes._common import build_client, resolve_profile_dir, step

_TEXT_JS = "() => (document.body.innerText || '')"
_OVERLAY_JS = (
    "() => [...document.querySelectorAll('.cdk-overlay-pane')].map(e => e.innerText).join('\\n---\\n')"
)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--expect", default="840")
    args = ap.parse_args()
    findings: dict[str, str] = {}
    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = await client._context.new_page()
        step("nav", "project")
        await page.goto(
            f"https://flow.google.com/project/{args.project}", wait_until="domcontentloaded", timeout=60_000
        )
        await page.wait_for_timeout(12_000)
        findings["page_text"] = await page.evaluate(_TEXT_JS)
        for label, selector in (
            ("settings_chip", ".settings-trigger-button"),
            ("tier_chip", "flow-user-tier-chip"),
            ("avatar", "flow-header-user-icon"),
            ("more_options", "button[aria-label='More options']"),
        ):
            step("click", label)
            try:
                await page.locator(selector).first.click(timeout=6_000)
                await page.wait_for_timeout(2_500)
                findings[label] = await page.evaluate(_OVERLAY_JS)
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(800)
            except Exception as exc:  # noqa: BLE001
                findings[label] = f"(failed: {str(exc)[:100]})"
    Path("out").mkdir(exist_ok=True)
    Path("out/credits_ui.json").write_text(
        json.dumps(findings, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    for label, text in findings.items():
        hits = [line.strip() for line in text.splitlines() if args.expect in line or "credit" in line.lower()]
        print(f"{label:14s} chars={len(text):6d} hits={hits[:6]}")


if __name__ == "__main__":
    asyncio.run(main())
