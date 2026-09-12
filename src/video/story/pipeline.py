"""Run the five shots in order, resumable, never spending twice for the same shot (I7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from video import gen
from video.story import composer, persona, shots

PRICE_PER_SHOT = 10


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
