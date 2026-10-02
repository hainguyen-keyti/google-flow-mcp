"""Upload local media into a project (measured 2026-09-12: 'Add media' menu > Upload > file chooser,
the upload answers on rpc maseQ; on 2026-10-03, three uploads, 7.8 to 9.1 s after the file was chosen). Counting
uploads reads the DOM instead: measured 2026-09-14, opening the Uploads view fires no batchexecute at all, it is a
client-side filter.

On 2026-10-02 two uploads raised "rpc maseQ not observed; saw []" while each image had reached the project, and the
second, made on that error, left two images of one name. It did not happen again the next day and the cause is not
known, so a reply that is not seen is settled by the listing: the project's images are read before the file is
chosen and again after, and the upload is the one new image carrying the file's name."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import parsers, reader
from video.flow.reader import capture
from video.session import PROJECT_READY, FlowSession

# Count the wrapper only. The union `flow-tile-container, flow-image-tile, flow-video-tile` matched the
# wrapper AND the tile nested inside it, so every figure came out doubled: measured 2026-09-14 in the
# Uploads view of 604b2de7, container 4, image-tile 4, video-tile 0, union 8, against a screenshot of 4.
_TILES_JS = "() => document.querySelectorAll('flow-tile-container').length"
# The files whose upload the listing can vouch for: an uploaded image is listed under its file name (measured
# 2026-10-02 and 2026-10-03, five uploads). A video's title in the listing was never measured.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


async def upload(session: FlowSession, project_id: str, path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    page = session.page
    # Reading the listing opens the project page, which is where the upload starts.
    before = _images(await reader.project(session, project_id))
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
    if "maseQ" in frames:
        record = parsers.upload_record(frames["maseQ"][0])
    else:
        record = {**_found(before, await reader.project(session, project_id), project_id, path, frames)}
    return {
        "file": str(path),
        "bytes": path.stat().st_size,
        "rpcids": sorted(frames),
        "tiles": await page.evaluate(_TILES_JS),
        **record,
    }


def _images(listing: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("id")): row for row in listing.get("media") or [] if row.get("kind") == "image"}


def _found(
    before: dict[str, dict[str, Any]],
    listing: dict[str, Any],
    project_id: str,
    path: Path,
    frames: dict[str, Any],
) -> dict[str, Any]:
    """The upload whose reply was not seen, told by the listing: the one image that is new since the file was
    chosen and carries its name. None means nothing was uploaded; two are not chosen between."""
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise RuntimeError(
            f"upload: Flow's reply (rpc maseQ) was not seen for {path.name!r}; how an uploaded video shows in the "
            "listing was never measured, so look at flow_media before uploading it again"
        )
    new = [
        row for key, row in _images(listing).items() if key not in before and row.get("title") == path.name
    ]
    if not new:
        raise RuntimeError(
            f"upload: Flow's reply (rpc maseQ) was not seen and the listing holds no new image named {path.name!r}, "
            f"so nothing was uploaded and uploading again is safe; saw {sorted(frames)}"
        )
    if len(new) > 1:
        raise RuntimeError(
            f"upload: Flow's reply (rpc maseQ) was not seen and the listing holds {len(new)} new images named "
            f"{path.name!r} ({[row.get('id') for row in new]}): look at flow_media before uploading anything again"
        )
    return {
        "media_id": new[0].get("id"),
        "project_id": project_id,
        "workflow_id": new[0].get("workflow_id"),
        "size_bytes": new[0].get("size_bytes"),
        "found_by": "listing",
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
