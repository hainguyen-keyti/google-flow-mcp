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
