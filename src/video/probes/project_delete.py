"""Spike for T6 delete: click the card's own 'Delete project' button, dump the confirm dialog, confirm,
verify the grid count. $0.

uv run python -m video.probes.project_delete --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from video.flow import parsers
from video.flow.reader import capture, one
from video.session import GRID_READY, MIGRATED_ROOT, FlowSession

_DIALOG_JS = """
() => [...document.querySelectorAll('[role=dialog], mat-dialog-container, .cdk-overlay-pane')]
  .map(e => ({ text: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 300),
               buttons: [...e.querySelectorAll('button')].map(b => (b.getAttribute('aria-label') || b.innerText || '').trim()) }))
"""


async def grid_ids(session: FlowSession) -> list[str]:
    frames = await capture(session, lambda: session.goto(MIGRATED_ROOT, ready=GRID_READY), settle=6.0)
    return [p["id"] for p in parsers.projects(one(frames, "UpteDb"))]


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    report: dict[str, Any] = {}
    async with FlowSession(args.profile) as session:
        page = session.page
        ids = await grid_ids(session)
        report["before"] = {"count": len(ids), "present": args.project in ids}
        card = page.locator(
            "flow-project-card", has=page.locator(f"a[href*='/project/{args.project}']")
        ).first
        report["card_found"] = await card.count()
        await card.hover(timeout=8_000)
        await page.wait_for_timeout(600)
        delete_button = card.get_by_role("button", name=re.compile("Delete project", re.IGNORECASE)).first
        report["delete_button_visible"] = await delete_button.is_visible()
        await delete_button.click(timeout=8_000, force=True)
        await page.wait_for_timeout(1_500)
        report["dialog"] = await page.evaluate(_DIALOG_JS)
        Path("out").mkdir(exist_ok=True)
        await page.screenshot(path="out/t6_delete_dialog.png")
        print(json.dumps(report, ensure_ascii=False)[:1200])
        confirm = (
            page.locator("[role=dialog], mat-dialog-container")
            .get_by_role("button", name=re.compile("delete|remove|confirm|ok", re.IGNORECASE))
            .first
        )
        if await confirm.count():
            frames = await capture(session, lambda: confirm.click(timeout=8_000), settle=6.0)
            report["confirm_rpcids"] = sorted(frames)
        else:
            report["confirm_rpcids"] = "no confirm button found"
        ids = await grid_ids(session)
        report["after"] = {"count": len(ids), "present": args.project in ids}
    Path("out/t6_delete_report.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in ("before", "confirm_rpcids", "after")}))


if __name__ == "__main__":
    asyncio.run(main())
