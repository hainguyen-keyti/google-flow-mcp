"""Per-clip actions on the clip editor /project/<id>/edit/<media> (measured 2026-09-12): 'Download media'
offers 270p GIF, 720p Original, 1080p Upscaled and 4K Upscaled; 'Add clip' offers Add clip and
Extend (Veo 3.1 - Lite); the prompt box 'Describe how to edit this video' runs Omni 1.1 Flash edits.
Extend creates a scene (rpc rqZuUc) and the new clip is a generation record in Zzl0ze[2] WITHOUT a grid
descriptor, so outputs are tracked through parsers.records. Extend and edit spend credits and are
ledgered like gen jobs (I1, I5)."""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video import gen
from video.flow import download as download_mod
from video.flow import parsers, reader
from video.flow.reader import capture, one
from video.session import PROJECT_READY, FlowSession

EDITOR = "flow-scene-builder"
RENDITIONS = {"gif": "270p", "720p": "720p", "1080p": "1080p", "4k": "4K"}
DONE_STATUS = 3


def new_records(before: set[str], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records whose workflow id was not seen before: an edit is a new record on the SAME media id."""
    return sorted(
        (r for r in rows if r["workflow_id"] not in before),
        key=lambda r: (r["created"] or 0, r["workflow_id"]),
    )


def is_done(row: dict[str, Any]) -> bool:
    return row.get("status") == DONE_STATUS and bool(row.get("url"))


def role_of(row: dict[str, Any], prompt: str) -> str:
    """Extend also copies the source clip into the new scene: only the record carrying our prompt is
    the generated clip (measured 2026-09-12: copy has the source prompt and no size)."""
    return "generated" if (row.get("prompt") or "").strip() == prompt.strip() else "copy"


async def _fetch_with_retry(request: Any, row: dict[str, Any], stem: Path, attempts: int = 6) -> Path:
    """lh3 renditions of a fresh clip can 404 for a while after the record says done."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await download_mod.fetch_asset(request, row["kind"], row["url"], stem)
        except RuntimeError as exc:
            last = exc
            if "404" not in str(exc) or attempt == attempts - 1:
                raise
            await asyncio.sleep(15)
    raise RuntimeError(str(last))


async def _prompt_ready(page: Any, box: Any, start: Any, prompt: str, timeout: float = 25.0) -> bool:
    """Type the prompt if the box is empty and wait until it is really there and the button is enabled.

    Entering Extend creates the scene and re-renders the editor, so a click 500ms after typing could land
    on a cleared box and submit nothing at all (measured 2026-09-13: rpcids [], 0 credits).
    """
    needle = prompt[:40]
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        text = (await box.inner_text()).strip()
        if needle in text and not await start.is_disabled():
            return True
        if needle not in text:
            await box.click(timeout=8_000)
            await page.keyboard.type(prompt)
        await asyncio.sleep(1)
    return False


async def _open(session: FlowSession, project_id: str, media_id: str) -> None:
    await session.goto(f"{session.project_url(project_id)}/edit/{media_id}", ready=EDITOR)
    await session.page.wait_for_timeout(3_000)


async def _menu_item(session: FlowSession, button: str, item: str) -> Any:
    """Open a toolbar menu and return its item, waiting for the overlay to render.

    The wait matters: "Add clip" both opens a menu and, clicked again, appends a copy of the clip, so a
    second click costs a stray clip instead of a retry (measured 2026-09-13 00:39, two copies, 0 credits).
    """
    page = session.page
    for attempt in range(2):
        await page.get_by_role("button", name=re.compile(button, re.IGNORECASE)).first.click(timeout=8_000)
        found = (
            page.locator("[role=menuitem], .cdk-overlay-pane button")
            .filter(has_text=re.compile(item, re.IGNORECASE))
            .first
        )
        try:
            await found.wait_for(state="visible", timeout=6_000)
            return found
        except PlaywrightTimeoutError:
            if attempt == 0:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(1_500)
    raise LookupError(f"menu {button!r} has no item matching {item!r}")


async def download_rendition(
    session: FlowSession, project_id: str, media_id: str, quality: str, out_dir: Path
) -> Path:
    label = RENDITIONS[quality.lower()]
    page = session.page
    await _open(session, project_id, media_id)
    item = await _menu_item(session, "Download media", label)
    async with page.expect_download(timeout=600_000) as download_info:
        await item.click(timeout=8_000)
    download = await download_info.value
    suffix = Path(download.suggested_filename).suffix or ".mp4"
    target = out_dir / f"{media_id}_{quality.lower()}{suffix}"
    if target.exists():
        raise FileExistsError(target)
    out_dir.mkdir(parents=True, exist_ok=True)
    await download.save_as(str(target))
    return target


async def _snapshot(session: FlowSession, project_id: str) -> tuple[list[dict[str, Any]], set[str]]:
    frames = await capture(
        session, lambda: session.goto(session.project_url(project_id), ready=PROJECT_READY), settle=8.0
    )
    listing = one(frames, "Zzl0ze")
    return parsers.records(listing), {s["scene_id"] for s in parsers.scenes_from_listing(listing)}


async def _generate_from_editor(
    session: FlowSession,
    project_id: str,
    media_id: str,
    prompt: str,
    *,
    kind: str,
    out_dir: Path,
    job_id: str | None,
    wait: float,
) -> dict[str, Any]:
    ledger = gen.Ledger(out_dir / "ledger.jsonl")
    job_id = job_id or str(uuid.uuid4())
    if ledger.has_submitted(job_id):
        raise gen.AlreadySubmitted(f"job {job_id} already has a submitted row; use a new job id")
    rows, scenes_before = await _snapshot(session, project_id)
    before = {r["workflow_id"] for r in rows}
    credits_before = (await reader.credits(session))["balance"]
    await _open(session, project_id, media_id)
    page = session.page
    if kind == "extend":
        item = await _menu_item(session, "Add clip", "Extend")
        await item.click(timeout=8_000)
        await page.wait_for_timeout(1_500)
    box = page.locator(f"{EDITOR} [contenteditable='true']").first
    start = page.get_by_role("button", name=re.compile("Start generation", re.IGNORECASE)).first
    if not await _prompt_ready(page, box, start, prompt):
        raise RuntimeError(f"{kind}: the prompt never reached the editor box, nothing was submitted")
    ledger.append(
        job_id, "submitted", kind=kind, source_media_id=media_id, prompt=prompt, credits_before=credits_before
    )
    frames = await capture(session, lambda: start.click(timeout=8_000), settle=15.0)
    if not frames and await _prompt_ready(page, box, start, prompt):
        # The click hit a stale editor (entering Extend re-renders it) and submitted nothing: click again.
        frames = await capture(session, lambda: start.click(timeout=8_000), settle=15.0)
    if not frames:
        ledger.append(job_id, "failed", credits_before=credits_before, credits_after=credits_before, spent=0)
        raise RuntimeError(f"{kind}: Start generation fired no request, so nothing was submitted (0 credits)")
    fresh: list[dict[str, Any]] = []
    scenes_after: set[str] = set()
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        rows, scenes_after = await _snapshot(session, project_id)
        fresh = new_records(before, rows)
        # The scene copy of the source shows up first and is already "done": wait for our own record.
        ours = [r for r in fresh if role_of(r, prompt) == "generated"]
        if ours and all(is_done(r) for r in ours):
            break
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(10)
    credits_after = (await reader.credits(session))["balance"]
    outputs = []
    for row in fresh:
        role = role_of(row, prompt)
        entry: dict[str, Any] = {
            "media_id": row["id"],
            "workflow_id": row["workflow_id"],
            "role": role,
            "status": row["status"],
            "path": None,
        }
        if role == "generated" and is_done(row):
            stem = out_dir / (row["id"] if row["id"] != media_id else f"{row['id']}_{row['workflow_id'][:8]}")
            try:
                path = await _fetch_with_retry(session.page.request, row, stem)
                entry["path"] = str(path)
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)[:200]
        outputs.append(entry)
    new_scenes = sorted(scenes_after - scenes_before)
    generated = [o for o in outputs if o["role"] == "generated"]
    status = "done" if generated and all(o["path"] for o in generated) else "pending" if fresh else "failed"
    ledger.append(
        job_id,
        status,
        outputs=outputs,
        scenes=new_scenes,
        credits_before=credits_before,
        credits_after=credits_after,
        spent=credits_before - credits_after,
        rpcids=sorted(frames),
    )
    if not fresh:
        raise RuntimeError(f"{kind}: no new generation record within {wait:.0f}s; rpcids {sorted(frames)}")
    return {
        "job_id": job_id,
        "kind": kind,
        "status": status,
        "source_media_id": media_id,
        "scene_id": new_scenes[0] if new_scenes else None,
        "outputs": outputs,
        "credits_before": credits_before,
        "credits_after": credits_after,
    }


async def extend(
    session: FlowSession,
    project_id: str,
    media_id: str,
    prompt: str,
    *,
    out_dir: Path,
    job_id: str | None = None,
    wait: float = 240.0,
) -> dict[str, Any]:
    return await _generate_from_editor(
        session, project_id, media_id, prompt, kind="extend", out_dir=out_dir, job_id=job_id, wait=wait
    )


async def edit(
    session: FlowSession,
    project_id: str,
    media_id: str,
    prompt: str,
    *,
    out_dir: Path,
    job_id: str | None = None,
    wait: float = 240.0,
) -> dict[str, Any]:
    return await _generate_from_editor(
        session, project_id, media_id, prompt, kind="edit", out_dir=out_dir, job_id=job_id, wait=wait
    )
