"""Run the five shots in order, resumable, never spending twice for the same shot (I7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from video import gen, post
from video.flow import clips
from video.flow import uploads as uploads_mod
from video.story import composer, persona, product, shots, shots2

PRICE_PER_SHOT = 10
EDIT_PROMPT = (
    "Change only her clothing. She is now wearing " + product.DESCRIPTION + ". Keep her face, her hair, "
    "her tattoos, her necklace, the room, the lighting and the camera framing exactly as they are."
)


def job_state(ledger: gen.Ledger, job_id: str) -> str:
    """new, done, retryable (a failure that provably cost nothing) or blocked (money may have moved)."""
    rows = ledger.rows(job_id)
    if any(r.get("status") == "done" for r in rows):
        return "done"
    if not rows:
        return "new"
    last = rows[-1]
    if last.get("status") == "failed" and last.get("spent") == 0:
        return "retryable"
    return "blocked"


def check_resumable(ledger: gen.Ledger, job_ids: list[str]) -> list[str]:
    """Jobs still to run. A job that was submitted but never finished stops the run: the credits may
    already be spent, so a human decides whether to retry it under a new id."""
    todo = []
    for job_id in job_ids:
        state = job_state(ledger, job_id)
        if state == "blocked":
            raise RuntimeError(
                f"{job_id} has a submitted row but no done row: credits may already be spent. "
                f"Check out/story/ledger.jsonl and Flow before rerunning."
            )
        if state in ("new", "retryable"):
            todo.append(job_id)
    return todo


def pending_jobs(ledger: gen.Ledger) -> list[dict[str, Any]]:
    """Submitted rows with no outcome: the shots whose money state is unknown."""
    rows = ledger.rows()
    settled = {r["job_id"] for r in rows if r.get("status") in ("done", "failed")}
    return [r for r in rows if r.get("status") == "submitted" and r["job_id"] not in settled]


def reconcile_decision(row: dict[str, Any], credits_now: int, record: dict[str, Any] | None) -> str:
    """What really happened to a shot stuck on 'submitted', judged by ground truth only."""
    if record is not None:
        return "done"
    if credits_now == row.get("credits_before"):
        return "failed"
    return "unknown"


async def reconcile(session, project_id: str, *, out_dir: Path) -> list[dict[str, Any]]:
    """Close out shots stuck on 'submitted' using the listing and the credit balance, never a guess."""
    from video.flow import download as download_mod
    from video.flow import reader
    from video.story import composer

    ledger = gen.Ledger(out_dir / "ledger.jsonl")
    stuck = pending_jobs(ledger)
    if not stuck:
        return []
    rows, _ = await composer.snapshot(session, project_id)
    credits_now = (await reader.credits(session))["balance"]
    plan_by_job = {r["job_id"]: r for r in shots.plan()}
    out = []
    for row in stuck:
        planned = plan_by_job.get(row["job_id"], {})
        record = composer.pick_output(
            [r for r in rows if (planned.get("prompt", "")[:40].lower() in (r.get("prompt") or "").lower())],
            planned.get("prompt", ""),
        )
        verdict = reconcile_decision(row, credits_now, record)
        path = None
        if verdict == "done" and record is not None:
            path = str(
                await download_mod.fetch_asset(
                    session.page.request, record["kind"], record["url"], out_dir / row["job_id"]
                )
            )
        if verdict != "unknown":
            ledger.append(
                row["job_id"],
                verdict,
                media_id=record["id"] if record else None,
                path=path,
                credits_before=row.get("credits_before"),
                credits_after=credits_now,
                spent=(row.get("credits_before") or credits_now) - credits_now,
                reconciled="checked the listing and the credit balance",
            )
        out.append({"job_id": row["job_id"], "verdict": verdict, "path": path, "credits_now": credits_now})
    return out


async def run(
    session,
    project_id: str,
    *,
    out_dir: Path,
    only: list[str] | None = None,
    wait: float = 300.0,
    price: int = PRICE_PER_SHOT,
) -> dict[str, Any]:
    rows = [r for r in shots.plan() if not only or r["key"] in only]
    ledger = gen.Ledger(out_dir / "ledger.jsonl")
    todo = check_resumable(ledger, [r["job_id"] for r in rows])
    who = await persona.ensure(session, project_id)
    results: list[dict[str, Any]] = []
    for row in rows:
        if row["job_id"] not in todo:
            results.append({"job_id": row["job_id"], "key": row["key"], "status": "already done"})
            continue
        result = await composer.generate(
            session,
            project_id,
            prompt=row["prompt"],
            character=who["name"],
            job_id=row["job_id"],
            expected_credits=price,
            out_dir=out_dir,
            aspect=shots.ASPECT,
            wait=wait,
        )
        results.append({**result, "key": row["key"], "status": "done"})
    return {"character": who, "shots": results}


async def _generate_with_product(
    session, project_id: str, *, prompt: str, job_id: str, out_dir: Path, wait: float
):
    """The product close-up goes through gflow r2v: the product photo is the only reference, and no face
    is needed, so the garment wins (measured 2026-09-13)."""
    job = gen.Job(
        job_id=job_id,
        kind="r2v",
        prompt=prompt,
        project=project_id,
        model=shots2.MODEL,
        aspect=shots2.ASPECT,
        refs=[product.check_image()],
    )
    result = await gen.run_job(job, out_dir, read_credits=lambda: gen.read_credits_live("default"))
    outputs = result.get("outputs") or []
    return {
        "job_id": job_id,
        "kind": "product",
        "media_id": outputs[0]["media_id"] if outputs else None,
        "path": outputs[0]["path"] if outputs else None,
        "spent": result["credits_before"] - result["credits_after"],
    }


async def _refetch_720(session, project_id: str, media_id: str, stem: Path) -> Path:
    """Re-download at 720p: the clip-edit path accepts the 360p rendition when 720p is still 404."""
    from video.flow import download as download_mod
    from video.flow import reader

    record = download_mod.latest_version(await reader.records(session, project_id), media_id)
    return await composer.fetch_720(session, record, stem)


async def run2(
    session,
    project_id: str,
    *,
    out_dir: Path,
    only: list[str] | None = None,
    wait: float = 300.0,
) -> dict[str, Any]:
    """Generate the v2 chain: each shot takes the route measured for it, and the shots after the reveal
    inherit the garment through their start frame instead of paying for another edit."""
    rows = [r for r in shots2.plan() if not only or r["key"] in only]
    ledger = gen.Ledger(out_dir / "ledger.jsonl")
    todo = check_resumable(ledger, [r["job_id"] for r in rows])
    who = await persona.ensure(session, project_id)
    await product.ensure(session, project_id)

    results: list[dict[str, Any]] = []
    paths: dict[str, str] = {}
    for row in rows:
        job, key = row["job_id"], row["key"]
        if job not in todo:
            done = [r for r in ledger.rows(job) if r.get("status") == "done"]
            paths[key] = done[-1].get("path") if done else None
            results.append({"job_id": job, "key": key, "status": "already done", "path": paths[key]})
            continue

        common = {
            "prompt": row["prompt"],
            "job_id": job,
            "expected_credits": shots2.PRICE,
            "out_dir": out_dir,
            "aspect": shots2.ASPECT,
            "wait": wait,
        }
        if row["mode"] == "character":
            result = await composer.generate_with_character(
                session, project_id, character=who["name"], **common
            )
        elif row["mode"] == "product":
            result = await _generate_with_product(
                session, project_id, prompt=row["prompt"], job_id=job, out_dir=out_dir, wait=wait
            )
        else:
            previous = paths.get(row["start_from"])
            if not previous:
                raise RuntimeError(f"{job}: {row['start_from']} has no clip to continue from")
            still = post.last_frame(Path(previous), out_dir / f"{job}_start.png")
            await uploads_mod.upload(session, project_id, still)
            result = await composer.generate_from_frame(session, project_id, start_name=still.name, **common)

        if row["edit_to_product"]:
            edited = await clips.edit(
                session,
                project_id,
                result["media_id"],
                EDIT_PROMPT,
                out_dir=out_dir,
                job_id=f"{job}-edit",
                wait=wait,
            )
            produced = [o for o in edited.get("outputs", []) if o.get("path")]
            if produced:
                fresh = await _refetch_720(
                    session, project_id, produced[0]["media_id"], out_dir / f"{job}_worn"
                )
                result = {**result, "path": str(fresh), "edited": True, "edit_spent": edited.get("spent")}

        paths[key] = result.get("path")
        results.append({**result, "key": key, "status": "done"})
    return {"character": who, "shots": results}
