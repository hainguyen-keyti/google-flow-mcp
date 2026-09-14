"""Upload local media into a project (measured 2026-09-12: 'Add media' menu > Upload > file chooser,
the upload answers on rpc maseQ). Counting uploads reads the DOM instead: measured 2026-09-14, opening
the Uploads view fires no batchexecute at all, it is a client-side filter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import parsers
from video.flow.reader import capture
from video.session import PROJECT_READY, FlowSession

# Count the wrapper only. The union `flow-tile-container, flow-image-tile, flow-video-tile` matched the
# wrapper AND the tile nested inside it, so every figure came out doubled: measured 2026-09-14 in the
# Uploads view of 604b2de7, container 4, image-tile 4, video-tile 0, union 8, against a screenshot of 4.
_TILES_JS = "() => document.querySelectorAll('flow-tile-container').length"


async def upload(session: FlowSession, project_id: str, path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    page = session.page
    await session.goto(session.project_url(project_id), ready=PROJECT_READY)
    await page.wait_for_timeout(2_000)
    await page.get_by_role("button", name=re.compile("Add media", re.IGNORECASE)).first.click(timeout=8_000)
    await page.wait_for_timeout(1_000)
    item = (
        page.locator("[role=menuitem], .cdk-overlay-pane button")
        .filter(has_text=re.compile("upload", re.IGNORECASE))
        .first
    )

    async def pick() -> None:
        async with page.expect_file_chooser(timeout=10_000) as chooser_info:
            await item.click(timeout=8_000)
        chooser = await chooser_info.value
        await chooser.set_files(str(path.resolve()))

    frames = await capture(session, pick, settle=20.0)
    if "maseQ" not in frames:
        raise RuntimeError(f"upload: rpc maseQ not observed; saw {sorted(frames)}")
    record = parsers.upload_record(frames["maseQ"][0])
    return {
        "file": str(path),
        "bytes": path.stat().st_size,
        "rpcids": sorted(frames),
        "tiles": await page.evaluate(_TILES_JS),
        **record,
    }


async def list_uploads(session: FlowSession, project_id: str) -> dict[str, Any]:
    page = session.page
    await session.goto(session.project_url(project_id), ready=PROJECT_READY)
    # The sidebar lands a beat after PROJECT_READY and WHEN is not fixed: measured 2026-09-14, about
    # 2000ms on a project holding uploads and about 3000ms on an upload-free one. Reading it after a flat
    # 2s sleep answered `count: 0` for a project holding 4 uploads, so wait for the sidebar to exist.
    try:
        await page.locator("mat-list-item").first.wait_for(state="attached", timeout=30_000)
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(f"list_uploads: the sidebar of {project_id} never rendered") from exc
    # Only now does absence carry information: a project that has never had an upload grows no "Uploads"
    # item. Asked any earlier, absence just means the page has not painted yet.
    nav = page.locator("mat-list-item", has_text="Uploads").first
    if await nav.count() == 0:
        return {"count": 0}
    await nav.click(timeout=8_000)
    # Nothing to await: the click fires no request and leaves the URL alone, and the tiles of the view we
    # came from stay in the DOM, so there is no anchor that says "the filter has run". 4s is what was
    # measured to settle; a project with enough uploads to need scrolling is not handled.
    await page.wait_for_timeout(4_000)
    return {"count": await page.evaluate(_TILES_JS)}
