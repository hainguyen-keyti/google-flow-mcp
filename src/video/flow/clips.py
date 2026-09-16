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


async def _await_submit(session: FlowSession, click: Any, *, settle: float = 30.0, patience: float = 90.0):
    """Click once, then stay on the editor until a request has actually gone out.

    The submit is sent well after the click (measured 2026-09-13: ~20s). Navigating away to poll 15s
    later cancelled it, costing 0 credits and generating nothing, which looked exactly like a dead click.
    """
    frames = await capture(session, click, settle=settle)
    waited = settle
    while not frames and waited < patience:
        frames = await capture(session, lambda: session.page.wait_for_timeout(15_000), settle=0.0)
        waited += 15.0
    return frames


async def _open(session: FlowSession, project_id: str, media_id: str) -> None:
    await session.goto(f"{session.project_url(project_id)}/edit/{media_id}", ready=EDITOR)
    await session.page.wait_for_timeout(3_000)


CONTROL_WAIT_MS = 15_000


async def _control(page: Any, found: Any, what: str) -> Any:
    """Wait for a control to exist before clicking it.

    Playwright's click waits for its element, but only for its own timeout. Measured 2026-09-16 by the first real
    clip_extend through MCP: this editor builds its toolbar in stages, the Add clip button arrived later than the
    8 s the click allows, and the call died there, after the ledger already held the job's intent row. The scene
    driver carried the same bug and was fixed the same day.
    """
    waited = 0
    while await found.count() == 0:
        if waited >= CONTROL_WAIT_MS:
            raise LookupError(f"{what} never appeared on the editor after {waited // 1000}s")
        await page.wait_for_timeout(1_000)
        waited += 1_000
    return found.first


async def _menu_item(session: FlowSession, button: str, item: str) -> Any:
    """Open a toolbar menu and return its item, waiting for the overlay to render.

    The wait matters: "Add clip" both opens a menu and, clicked again, appends a copy of the clip, so a
    second click costs a stray clip instead of a retry (measured 2026-09-13 00:39, two copies, 0 credits).
    """
    page = session.page
    for attempt in range(2):
        opener = await _control(
            page, page.get_by_role("button", name=re.compile(button, re.IGNORECASE)), f"the {button} button"
        )
        await opener.click(timeout=8_000)
        found = (
            page.locator("[role=menuitem], .cdk-overlay-pane button")
            .filter(has_text=re.compile(item, re.IGNORECASE))
            .first
        )
        try:
            await found.wait_for(state="visible", timeout=6_000)
            # A greyed out item is not a slow one: clicking it only spends 8 s waiting for Playwright to give up, and
            # the caller reads a bare timeout. Measured 2026-09-16: Flow renders `Extend (Veo 3.1 - Lite)` disabled on
            # a clip made by omni-flash, and that is what stopped the first two real clip_extend calls.
            if not await found.is_enabled():
                raise LookupError(
                    f"menu {button!r} shows {item!r} greyed out, so Flow does not offer it for this clip; "
                    "extend is offered on Veo clips, not on every model"
                )
            return found
        except PlaywrightTimeoutError:
            if attempt == 0:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(1_500)
    raise LookupError(f"menu {button!r} has no item matching {item!r}")


async def _select_version(
    session: FlowSession, project_id: str, media_id: str, workflow_id: str | None
) -> dict[str, Any]:
    """Point the editor at one version, because "Download media" takes whatever is on screen.

    `_open` lands on the pre-edit version and MUST keep doing so: `_generate_from_editor` relies on every
    Omni edit starting from the same base, so fixing this in `_open` would make each edit stack on the
    previous one. The fix belongs here instead, after the page is already open.

    Measured 2026-09-13: the history panel renders one `.container` per version, and the tail of a
    record's listing url appears inside that container's thumbnail src. Matching is not guaranteed for
    every version, so a miss raises rather than downloading whatever happens to be showing: returning the
    wrong clip while looking successful is the exact failure this function exists to prevent.
    """
    records, _ = await _snapshot(session, project_id)
    if workflow_id:
        wanted = next(
            (r for r in records if r.get("id") == media_id and r.get("workflow_id") == workflow_id), None
        )
        if wanted is None:
            raise LookupError(f"media {media_id} has no version with workflow {workflow_id}")
        if not wanted.get("url"):
            raise ValueError(f"version {workflow_id} of media {media_id} has no url in the listing")
    else:
        wanted = download_mod.latest_version(records, media_id)
    tail = str(wanted["url"])[-24:]
    entry = session.page.locator(f'.container:has(img[src*="{tail}"])')
    if await entry.count() == 0:
        raise LookupError(
            f"the editor history has no entry matching version {wanted.get('workflow_id')} of {media_id}"
        )
    await entry.first.click(timeout=8_000)
    await session.page.wait_for_timeout(3_000)
    return wanted


async def download_rendition(
    session: FlowSession,
    project_id: str,
    media_id: str,
    quality: str,
    out_dir: Path,
    *,
    workflow_id: str | None = None,
) -> Path:
    label = RENDITIONS[quality.lower()]
    page = session.page
    await _open(session, project_id, media_id)
    await _select_version(session, project_id, media_id, workflow_id)
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
    # _prompt_ready types with keyboard.type, which presses Enter for a newline before any `submitted` row exists.
    if "\n" in prompt or "\r" in prompt:
        raise ValueError(f"{kind}: the prompt must be one line; the editor box takes a newline as Enter")
    ledger = gen.Ledger(out_dir / "ledger.jsonl")
    job_id = job_id or str(uuid.uuid4())
    if ledger.has_submitted(job_id):
        raise gen.AlreadySubmitted(f"job {job_id} already has a submitted row; use a new job id")
    rows, scenes_before = await _snapshot(session, project_id)
    before = {r["workflow_id"] for r in rows}
    credits_before = (await reader.credits(session))["balance"]
    # Written before anything that can spend. Opening the editor and choosing "Extend" are enough on
    # their own to create a paid job (measured 2026-09-13: 20 credits left the account while the driver
    # died before its `submitted` row, so nothing in the ledger pointed at them). The two calls above are
    # reads that cannot spend, and this row needs their numbers. The status stays outside the set
    # `has_submitted` blocks on, so an orphaned row never stops a legitimate retry. The project and the
    # workflows already listed let reconcile tell what this job added without comparing two clocks, and the
    # prompt tells this job's own record from an upscale or another job's output.
    ledger.append(
        job_id,
        "opening",
        kind=kind,
        source_media_id=media_id,
        credits_before=credits_before,
        project=project_id,
        workflows_before=sorted(before),
        prompt=prompt,
    )
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
    # Exactly one click, ever. A second click lands in the plain edit box once the editor re-renders and
    # submits a differently priced job on top (measured 2026-09-13: an extra Omni edit, 20 credits).
    # Whether the submit worked is decided by the listing and the credit balance below, not by a retry.
    frames = await _await_submit(session, lambda: start.click(timeout=8_000))
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
    status = (
        "done" if generated and all(o["path"] for o in generated) else "pending" if generated else "failed"
    )
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
    if not generated:
        raise RuntimeError(
            f"{kind}: nothing was generated within {wait:.0f}s, spent {credits_before - credits_after} "
            f"credits; rpcids {sorted(frames)}"
        )
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


STUCK_STATUSES = ("opening", "submitted")


def _stuck_editor_jobs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Editor jobs whose money state is unknown: opened or submitted, never settled.

    One row per job, the earliest one, because that is the row carrying the balance from before the
    spend. A job can hold both an `opening` and a `submitted` row and is still only one job.
    """
    settled = {r["job_id"] for r in rows if r.get("status") in ("done", "failed")}
    first: dict[str, dict[str, Any]] = {}
    for row in rows:
        job_id = row.get("job_id")
        if row.get("status") not in STUCK_STATUSES or job_id in settled or job_id in first:
            continue
        first[job_id] = row
    return list(first.values())


def _taken_elsewhere(rows: list[dict[str, Any]], job_id: str) -> set[str]:
    """Workflows another job's outputs already hold: one record is never two jobs' output (DECISIONS 2026-09-15)."""
    return {
        output.get("workflow_id")
        for row in rows
        if row.get("job_id") != job_id
        for output in row.get("outputs") or []
        if output.get("role") == "generated"
    }


def _editor_verdict(
    row: dict[str, Any], records: list[dict[str, Any]], credits_now: int, taken: set[str], contested: bool
) -> tuple[str, dict[str, Any] | None]:
    """What really happened, judged by ground truth only, never by the editor's own say-so.

    Stricter than `story.pipeline.reconcile_decision` on `failed` (DECISIONS 2026-09-15): the balance has been
    measured moving with no spend (195 to 245 overnight), so an equal one proves nothing alone, while a job that
    spent has always left a record in the listing. A record is new when its workflow is missing from the
    `workflows_before` the job wrote as it opened, whatever its time: Flow's clock and this Mac's disagree, and a
    clip's `created` is stamped at submit. `done` is for an edit only: exactly one new version on its own clip
    carries its prompt (compared as the driver does) and is held by no other job's outputs, counting one still
    rendering; that version is finished; and no rival is left, another job that named this clip and prompt and is
    still open, or closed holding no version on the clip, since a driver closes a job `failed` when its version shows
    up after the deadline, unless clip_reconcile itself closed it `failed` (DECISIONS 2026-09-15, 2026-09-16). Every
    measured edit put its version on its clip, while an extend's clip lands on
    a new media id beside copies of each source version that keep their prompts, and a free upscale adds a promptless
    version, so an extend is never `done`. `failed` needs an equal balance and no new record at all, taken or not. A
    listing without the job's own clip holds no evidence about the job, and a clip the row never saw would make every
    one of its records look new: both are `unknown`.
    """
    media_id = row.get("source_media_id")
    before = set(row["workflows_before"])
    if not any(r.get("id") == media_id and r.get("workflow_id") in before for r in records):
        return "unknown", None
    fresh = new_records(before, records)
    prompt = row["prompt"].strip()
    candidates = [
        r
        for r in fresh
        if r.get("id") == media_id
        and r["workflow_id"] not in taken
        and (r.get("prompt") or "").strip() == prompt
    ]
    if row.get("kind") == "edit" and len(candidates) == 1 and is_done(candidates[0]) and not contested:
        return "done", candidates[0]
    if credits_now == row.get("credits_before") and not fresh:
        return "failed", None
    return "unknown", None


def _judgeable(row: dict[str, Any], project_id: str) -> bool:
    """An editor job of this project whose opening row lists the workflows it saw and its prompt: the only kind
    reconcile judges. An empty list never shows the source clip as seen, so it counts as none."""
    return (
        bool(row.get("source_media_id"))
        and row.get("project") == project_id
        and bool(row.get("workflows_before"))
        and bool((row.get("prompt") or "").strip())
    )


def reconcile_needs_flow(project_id: str, *, out_dir: Path) -> bool:
    """Whether the ledger holds a job reconcile can judge for this project, the only reason to open a browser."""
    rows = gen.Ledger(Path(out_dir) / "ledger.jsonl").rows()
    return any(_judgeable(row, project_id) for row in _stuck_editor_jobs(rows))


async def reconcile_editor(
    session: FlowSession | None, project_id: str, *, out_dir: Path
) -> list[dict[str, Any]]:
    """Close out editor jobs that spent credits without ever recording an outcome.

    A job left on `unknown` is left alone: money moved but nothing on that media proves what it bought,
    and writing a guess into the ledger is worse than leaving the question open for a human.
    """
    ledger = gen.Ledger(Path(out_dir) / "ledger.jsonl")
    rows = ledger.rows()
    stuck = _stuck_editor_jobs(rows)
    if not stuck:
        return []
    records: list[dict[str, Any]] = []
    credits_now = None
    judgeable = [row for row in stuck if _judgeable(row, project_id)]
    if judgeable:
        records, _ = await _snapshot(session, project_id)
        credits_now = (await reader.credits(session))["balance"]
    held_on_clip = {
        (other.get("job_id"), output.get("media_id"))
        for other in rows
        for output in other.get("outputs") or []
        if output.get("role") == "generated"
    }
    open_jobs = {r["job_id"] for r in stuck}
    last_rows = {other.get("job_id"): other for other in rows}
    # Only clip_reconcile writes failed with a source_media_id, and only on an equal balance and no new record.
    made_nothing = {
        job_id
        for job_id, last in last_rows.items()
        if last.get("status") == "failed" and last.get("reconciled") and last.get("source_media_id")
    }
    out: list[dict[str, Any]] = []
    for row in stuck:
        if not row.get("source_media_id"):
            # A gen or agent job names no clip, so nothing here can judge it: report it, never write it.
            out.append({"job_id": row["job_id"], "kind": row.get("kind"), "verdict": "skipped"})
            continue
        if row.get("project") not in (None, project_id):
            # Its clip and workflows live in that project's listing: say where to reconcile it, never judge it here.
            out.append(
                {
                    "job_id": row["job_id"],
                    "kind": row.get("kind"),
                    "verdict": "skipped",
                    "project": row["project"],
                }
            )
            continue
        if not _judgeable(row, project_id):
            # Nothing to judge by: no workflows listed, or no prompt (DECISIONS 2026-09-15). Left for a person.
            out.append({"job_id": row["job_id"], "verdict": "unknown", "credits_now": credits_now})
            continue
        # Another job that named this clip and prompt may own the new version: always while it is open, and once closed
        # unless it holds a version on the clip or clip_reconcile found it made nothing (DECISIONS 2026-09-16).
        contested = any(
            other.get("job_id") != row["job_id"]
            and other.get("source_media_id") == row["source_media_id"]
            and (other.get("prompt") or "").strip() == row["prompt"].strip()
            and (
                other.get("job_id") in open_jobs
                or (
                    (other.get("job_id"), row["source_media_id"]) not in held_on_clip
                    and other.get("job_id") not in made_nothing
                )
            )
            for other in rows
        )
        taken = _taken_elsewhere(rows, row["job_id"])
        verdict, record = _editor_verdict(row, records, credits_now, taken, contested)
        before = row.get("credits_before")
        if verdict != "unknown":
            outputs = []
            if record is not None:
                outputs = [
                    {
                        "media_id": record["id"],
                        "workflow_id": record["workflow_id"],
                        "role": "generated",
                        "status": record["status"],
                    }
                ]
            ledger.append(
                row["job_id"],
                verdict,
                kind=row.get("kind"),
                source_media_id=row.get("source_media_id"),
                credits_before=before,
                credits_after=credits_now,
                spent=(before - credits_now) if before is not None else None,
                outputs=outputs,
                reconciled="checked the listing and the credit balance",
            )
        out.append({"job_id": row["job_id"], "verdict": verdict, "credits_now": credits_now})
    return out


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
