"""Spike for T8: upload a local file through the project page 'Add media menu', record the rpcids,
then look at the Uploads view. $0 (uploads are not generations).

uv run python -m video.probes.upload --project <id> --file out/t5/<image>.jpg
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

_MENU_JS = """
() => [...document.querySelectorAll('[role=menuitem], [role=menu] button, .cdk-overlay-pane button')]
  .filter(e => e.offsetParent !== null)
  .map(e => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60))
  .filter(Boolean)
"""
_TILES_JS = """
() => ({
  url: location.href,
  tiles: document.querySelectorAll('flow-tile-container, flow-image-tile, flow-video-tile').length,
  text: (document.querySelector('mat-sidenav-content')?.innerText || document.body.innerText || '')
    .trim().replace(/\\s+/g, ' ').slice(0, 300),
})
"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--file", required=True)
    args = ap.parse_args()
    path = Path(args.file).resolve()
    report: dict[str, Any] = {"file": str(path), "bytes": path.stat().st_size}
    Path("out").mkdir(exist_ok=True)
    async with FlowSession(args.profile) as session:
        page = session.page
        await session.goto(session.project_url(args.project), ready=PROJECT_READY)
        await page.wait_for_timeout(3_000)
        add = page.get_by_role("button", name=re.compile("Add media", re.IGNORECASE)).first
        await add.click(timeout=8_000)
        await page.wait_for_timeout(1_200)
        report["menu"] = await page.evaluate(_MENU_JS)
        await page.screenshot(path="out/t8_menu.png")
        upload_item = (
            page.locator("[role=menuitem], .cdk-overlay-pane button")
            .filter(has_text=re.compile("upload", re.IGNORECASE))
            .first
        )
        report["upload_item_found"] = await upload_item.count()

        async def pick() -> None:
            async with page.expect_file_chooser(timeout=10_000) as chooser_info:
                await upload_item.click(timeout=8_000)
            chooser = await chooser_info.value
            await chooser.set_files(str(path))

        frames = await capture(session, pick, settle=20.0)
        report["upload_rpcids"] = {k: len(v) for k, v in sorted(frames.items())}
        await page.screenshot(path="out/t8_after_upload.png")
        report["after_upload"] = await page.evaluate(_TILES_JS)
        uploads_nav = page.locator("mat-list-item", has_text="Uploads").first
        frames2 = await capture(session, lambda: uploads_nav.click(timeout=8_000), settle=6.0)
        report["uploads_view_rpcids"] = {k: len(v) for k, v in sorted(frames2.items())}
        report["uploads_view"] = await page.evaluate(_TILES_JS)
        await page.screenshot(path="out/t8_uploads_view.png")
    Path("out/t8_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    for key in (
        "menu",
        "upload_item_found",
        "upload_rpcids",
        "after_upload",
        "uploads_view_rpcids",
        "uploads_view",
    ):
        print(f"{key}: {json.dumps(report[key], ensure_ascii=False)[:500]}")


if __name__ == "__main__":
    asyncio.run(main())
