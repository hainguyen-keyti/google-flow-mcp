"""Upload local media into a project (measured 2026-09-12: 'Add media' menu > Upload > file chooser,
the upload answers on rpc maseQ; on 2026-10-03 five uploads answered there, the two that were timed 7.8 and 9.1 s
after the file was chosen). Counting uploads reads the DOM instead: measured 2026-09-14, opening the Uploads view
fires no batchexecute at all, it is a client-side filter.

On 2026-10-02 two uploads raised "rpc maseQ not observed; saw []" while each image had reached the project, and the
second, made on that error, left two images of one name. It did not happen again the next day and the cause is not
known, so a reply that is not seen is settled by the listing: the project's images are read before the file is
chosen and several times after, and the upload is the one new image carrying the file's name."""

from __future__ import annotations

import asyncio
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
# 2026-10-02 and 2026-10-03, on PNG files only). How anything else is titled in the listing was never measured.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
# How long Flow's reply is waited for: it came 7.8 and 9.1 s after the file was chosen (2026-10-03).
REPLY_WAIT_S = 20.0
# With no reply, how often the listing is read before "nothing was uploaded" is said, and the pause between reads.
# One read is not enough: a saved frame reached the listing about 40 s after its click (2026-09-18), and the uploads
# of 2026-10-02 landed with no reply at all. Each read takes over 10 s itself, so four reads span about a minute,
# the delay at which the probe of 2026-10-03 (out/ao/t1_upload.py) read the listing and found its upload.
LISTING_READS = 4
LISTING_STEP_S = 5.0


async def upload(session: FlowSession, project_id: str, path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        # Refused before the file chooser: how any other kind of file shows in the listing was never measured, so an
        # upload of one could not be settled when Flow's reply did not come (review 2026-10-08, D11).
        raise ValueError(
            f"upload takes {', '.join(IMAGE_SUFFIXES)}, got {path.suffix or 'no suffix'!r} ({path.name}); nothing "
            "was sent"
        )
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

    frames = await capture(session, pick, settle=REPLY_WAIT_S)
    if "maseQ" in frames:
        record = parsers.upload_record(frames["maseQ"][0])
    else:
        record = await _settled_by_listing(session, project_id, path, before, frames)
    return {
        "file": str(path),
        "bytes": path.stat().st_size,
        "rpcids": sorted(frames),
        "tiles": await page.evaluate(_TILES_JS),
        **record,
    }


def _images(listing: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("id")): row for row in listing.get("media") or [] if row.get("kind") == "image"}


async def _settled_by_listing(
    session: FlowSession,
    project_id: str,
    path: Path,
    before: dict[str, dict[str, Any]],
    frames: dict[str, Any],
) -> dict[str, Any]:
    """The upload whose reply was not seen, told by the listing: the one image that is new since the file was
    chosen and carries its name. Two are not chosen between. "Nothing was uploaded" is said only when the listing,
    read `LISTING_READS` times, holds no new image at all: a new image under another title may be this upload."""
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise RuntimeError(
            f"upload: Flow's reply (rpc maseQ) was not seen for {path.name!r}; how a file that is not one of "
            f"{', '.join(IMAGE_SUFFIXES)} shows in the listing was never measured, so look at flow_media before "
            "uploading it again"
        )
    new: list[dict[str, Any]] = []
    named: list[dict[str, Any]] = []
    for read in range(LISTING_READS):
        if read:
            await asyncio.sleep(LISTING_STEP_S)
        listed = _images(await reader.project(session, project_id))
        new = [row for key, row in listed.items() if key not in before]
        named = [row for row in new if row.get("title") == path.name]
        if named:
            break
    if len(named) > 1:
        raise RuntimeError(
            f"upload: Flow's reply (rpc maseQ) was not seen and the listing holds {len(named)} new images named "
            f"{path.name!r} ({[row.get('id') for row in named]}): look at flow_media before uploading anything again"
        )
    if named:
        return {
            "media_id": named[0].get("id"),
            "project_id": project_id,
            "workflow_id": named[0].get("workflow_id"),
            "size_bytes": named[0].get("size_bytes"),
            "found_by": "listing",
        }
    if new:
        raise RuntimeError(
            f"upload: Flow's reply (rpc maseQ) was not seen and the listing holds no new image named {path.name!r}, "
            f"but it holds new images under other titles ({[(row.get('id'), row.get('title')) for row in new]}): "
            "one of them may be this upload, so look at flow_media before uploading anything again"
        )
    raise RuntimeError(
        f"upload: Flow's reply (rpc maseQ) was not seen in {REPLY_WAIT_S:.0f} s and the listing, read {LISTING_READS} "
        f"times after that, holds no new image at all, so nothing was uploaded and uploading again is safe; saw "
        f"{sorted(frames)}"
    )


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
