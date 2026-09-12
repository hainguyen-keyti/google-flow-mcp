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

from video import gen
from video.flow import download as download_mod
from video.flow import parsers, reader
from video.flow.reader import capture, one
from video.session import PROJECT_READY, FlowSession

EDITOR = "flow-scene-builder"
RENDITIONS = {"gif": "270p", "720p": "720p", "1080p": "1080p", "4k": "4K"}
DONE_STATUS = 3


def new_records(before: set[str], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((r for r in rows if r["id"] not in before), key=lambda r: (r["created"] or 0, r["id"]))


def is_done(row: dict[str, Any]) -> bool:
    return row.get("status") == DONE_STATUS and bool(row.get("url"))


async def _open(session: FlowSession, project_id: str, media_id: str) -> None:
    await session.goto(f"{session.project_url(project_id)}/edit/{media_id}", ready=EDITOR)
    await session.page.wait_for_timeout(3_000)


async def _menu_item(session: FlowSession, button: str, item: str) -> Any:
    page = session.page
    await page.get_by_role("button", name=re.compile(button, re.IGNORECASE)).first.click(timeout=8_000)
    await page.wait_for_timeout(800)
    return (
        page.locator("[role=menuitem], .cdk-overlay-pane button")
        .filter(has_text=re.compile(item, re.IGNORECASE))
        .first
    )


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
    before = {r["id"] for r in rows}
    credits_before = (await reader.credits(session))["balance"]
    await _open(session, project_id, media_id)
    page = session.page
    if kind == "extend":
        item = await _menu_item(session, "Add clip", "Extend")
        await item.click(timeout=8_000)
        await page.wait_for_timeout(1_500)
    box = page.locator(f"{EDITOR} [contenteditable='true']").first
    await box.click(timeout=8_000)
    await page.keyboard.type(prompt)
    await page.wait_for_timeout(500)
    start = page.get_by_role("button", name=re.compile("Start generation", re.IGNORECASE)).first
    ledger.append(
        job_id, "submitted", kind=kind, source_media_id=media_id, prompt=prompt, credits_before=credits_before
    )
    frames = await capture(session, lambda: start.click(timeout=8_000), settle=15.0)
    fresh: list[dict[str, Any]] = []
    scenes_after: set[str] = set()
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        rows, scenes_after = await _snapshot(session, project_id)
        fresh = new_records(before, rows)
        if fresh and all(is_done(r) for r in fresh):
            break
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(10)
    credits_after = (await reader.credits(session))["balance"]
    outputs = []
    for row in fresh:
        entry: dict[str, Any] = {"media_id": row["id"], "status": row["status"], "path": None}
        if is_done(row):
            try:
                path = await download_mod.fetch_asset(
                    session.page.request, row["kind"], row["url"], out_dir / row["id"]
                )
                entry["path"] = str(path)
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)[:200]
        outputs.append(entry)
    new_scenes = sorted(scenes_after - scenes_before)
    status = "done" if fresh and all(o["path"] for o in outputs) else "pending" if fresh else "failed"
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
