"""Upload local media into a project (measured 2026-09-12: 'Add media' menu > Upload > file chooser,
the upload answers on rpc maseQ; the Uploads view fires WuwhI)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from video.flow import parsers
from video.flow.reader import capture
from video.session import PROJECT_READY, FlowSession

_TILES_JS = "() => document.querySelectorAll('flow-tile-container, flow-image-tile, flow-video-tile').length"


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
    await page.wait_for_timeout(2_000)
    nav = page.locator("mat-list-item", has_text="Uploads").first
    # A project that has never had an upload grows no "Uploads" nav item, and clicking a locator that
    # matches nothing just waits out its timeout. Measured 2026-09-13 against a fresh project:
    # `TimeoutError: Locator.click: Timeout 8000ms exceeded` on a perfectly healthy project. Absent means
    # empty, so answer empty. Note this checks for ABSENCE only: a nav item that exists but fails to open
    # still raises, because that is a broken Uploads view and not an empty one.
    if await nav.count() == 0:
        return {"rpcids": [], "count": 0, "tiles": await page.evaluate(_TILES_JS), "head": None}
    frames = await capture(session, lambda: nav.click(timeout=8_000), settle=6.0)
    listing = frames.get("WuwhI", [None])[0]
    return {
        "rpcids": sorted(frames),
        "count": len(listing) if isinstance(listing, list) else None,
        "tiles": await page.evaluate(_TILES_JS),
        "head": json.dumps(listing)[:300] if listing is not None else None,
    }
