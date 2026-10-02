"""A generation submitted in one call and looked at or fetched in a later one (plan AN, G7).

`composer._submit(detach=True)` leaves a `submitted` row and, once its request has left, a `started` row holding the
workflow Flow's own reply named. Everything here works from those two rows, the project listing and the balance: it
never clicks and never types, so nothing here can start or pay for a job.

Measured 2026-10-02 (`out/an/t1_probe.py`, three jobs of three): the workflow id of the submit reply is the
`workflow_id` the listing gives the clip. Flow's reason for a refusal is heard only by the page that submitted
(plan E, 2026-09-17), so a job that never shows is settled as nothing generated, not as a typed refusal.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from video import gen, outcome
from video.flow import clips, composer, reader
from video.session import FlowSession

# How long after its submit a job with no record is still only "not listed". Not measured: the listing has always
# shown a rendering job within a minute; ten leaves room for a slow day before anything is written.
NOT_LISTED_S = 600.0
# How long after its submit another job with no settled row can still move the balance. Not measured: the longest run
# seen took 8 minutes; an hour keeps a job that crashed last week from marking every later bracket as shared.
IN_FLIGHT_S = 3600.0
# Rows of another job that can stand for a balance on the move.
_MOVES = (*outcome.INTENT, "started", *outcome.SETTLED)
_NEVER = "never run this job again under a new job_id"


def _run(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """The intent row of the job's last run, the `started` row after it, and the settled row after that."""
    intent, settled = outcome.last_run(rows)
    if intent is None:
        return None, None, settled
    after = rows[max(i for i, row in enumerate(rows) if row is intent) :]
    started = next((row for row in reversed(after) if row.get("status") == "started"), None)
    return intent, started, settled


def _settled(job_id: str, settled: dict[str, Any]) -> dict[str, Any]:
    """A job that already has its settled row is answered from it: one settled row per job (I-money-6)."""
    keep = ("media_id", "path", "spent", "spent_from", "credits_before", "credits_after", "workflow_id")
    return {
        "job_id": job_id,
        "state": "settled",
        "status": settled.get("status"),
        **{key: settled[key] for key in keep if key in settled},
    }


def standing(ledger: gen.Ledger, job_id: str) -> dict[str, Any] | None:
    """What the ledger alone says of a job: the answer of one that has its settled row, None for a started one that
    Flow must be asked about, and a refusal for one job_submit did not start. Callers ask this first, so neither a
    settled job nor one with no clip to look for opens a browser."""
    intent, started, settled = _run(ledger.rows(job_id))
    if settled is not None:
        return _settled(job_id, settled)
    if intent is None or started is None:
        raise _not_started(job_id, ledger)
    return None


def _not_started(job_id: str, ledger: gen.Ledger) -> LookupError:
    return LookupError(
        f"job {job_id!r} has no started row in {ledger.path}: job_submit did not start it, so job_status and "
        "job_collect have no clip to look for; a blocking tool settles its own job, and what this one came to is "
        "its outcome"
    )


def _claim(
    records: list[dict[str, Any]], started: dict[str, Any]
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    """Where the job's clip stands in a listing: (state, the record, the candidates when there are several).

    I-money-5: with the workflow Flow named, only the record of that workflow is the clip, whatever else carries the
    same prompt. With none named, the one NEW record whose prompt is the job's is the clip, and two are not chosen
    between."""
    named = started.get("workflow_id")
    if named:
        mine = [record for record in records if record.get("workflow_id") == named]
    else:
        fresh = clips.new_records(set(started.get("workflows_before") or []), records)
        mine = composer.matching_outputs(fresh, started.get("prompt") or "", started.get("prompt_text"))
        if len(mine) > 1:
            return "ambiguous", None, mine
    if not mine:
        return "not_listed", None, []
    return ("ready" if clips.is_done(mine[0]) else "rendering"), mine[0], []


def _shared(job_id: str, intent: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    """Whether another job could have moved the balance inside this job's bracket (I-read-2): one with a row after
    this job's submit, or one submitted within the hour before it and not settled yet, whose charge, or the refund
    of its failure, is still to land."""
    mine = intent.get("ts") or 0
    theirs: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("job_id") != job_id and row.get("status") in _MOVES:
            theirs.setdefault(str(row.get("job_id")), []).append(row)
    for moves in theirs.values():
        if any((row.get("ts") or 0) > mine for row in moves):
            return True
        began, settled = outcome.last_run(moves)
        if began is not None and settled is None and mine - (began.get("ts") or 0) < IN_FLIGHT_S:
            return True
    return False


async def _look(session: FlowSession, intent: dict[str, Any], started: dict[str, Any]) -> dict[str, Any]:
    records, _ = await composer.snapshot(session, str(intent.get("project")))
    state, record, candidates = _claim(records, started)
    return {
        "state": state,
        "record": record,
        "candidates": candidates,
        "credits_now": (await reader.credits(session))["balance"],
    }


async def status(
    session: FlowSession, ledger: gen.Ledger, job_id: str, *, now: float | None = None
) -> dict[str, Any]:
    """Where a job stands: `settled`, `rendering`, `ready`, `not_listed` or `ambiguous`. Reads only, writes nothing."""
    intent, started, settled = _run(ledger.rows(job_id))
    if settled is not None:
        return _settled(job_id, settled)
    if intent is None or started is None:
        raise _not_started(job_id, ledger)
    seen = await _look(session, intent, started)
    return {
        "job_id": job_id,
        "state": seen["state"],
        "workflow_id": started.get("workflow_id"),
        "media_id": seen["record"]["id"] if seen["record"] else None,
        **({"candidates": [c["id"] for c in seen["candidates"]]} if seen["candidates"] else {}),
        "project": intent.get("project"),
        "quoted_credits": intent.get("quoted_credits"),
        "credits_before": intent.get("credits_before"),
        "credits_now": seen["credits_now"],
        "age_s": int((time.time() if now is None else now) - (started.get("ts") or 0)),
    }


async def collect(
    session: FlowSession,
    ledger: gen.Ledger,
    job_id: str,
    *,
    others: Callable[[], list[dict[str, Any]]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Fetch a started job's finished clip and write its one settled row; a job still rendering, or not listed yet,
    is answered as such with nothing written. `others` reads the rows of every ledger under the out folder: another
    job that could have moved the balance meanwhile (`_shared`) means the bracket is not this job's own, and then
    the job's `spent` is the price Flow quoted before the click, said as such (I-read-2)."""
    intent, started, settled = _run(ledger.rows(job_id))
    if settled is not None:
        return _settled(job_id, settled)
    if intent is None or started is None:
        raise _not_started(job_id, ledger)
    seen = await _look(session, intent, started)
    state, record, credits_now = seen["state"], seen["record"], seen["credits_now"]
    age = int((time.time() if now is None else now) - (started.get("ts") or 0))
    before, quoted = intent.get("credits_before"), intent.get("quoted_credits")
    # Read after the balance, so a job submitted while this call waited for the browser is among them. A ledger that
    # cannot be read (another process mid-append) may hold such a job, so it counts as one.
    try:
        shared = _shared(job_id, intent, others() if others else [])
    except (OSError, ValueError):
        shared = True
    bracket = before - credits_now if isinstance(before, int) and isinstance(credits_now, int) else None
    common = {
        "credits_before": before,
        "credits_after": credits_now,
        "workflow_id": started.get("workflow_id"),
    }
    waiting = {"job_id": job_id, "state": state, "age_s": age, **common}

    if state == "rendering":
        return {**waiting, "media_id": record["id"]}
    if state == "ambiguous":
        ids = [candidate["id"] for candidate in seen["candidates"]]
        ledger.append(
            job_id,
            "unknown",
            candidates=ids,
            spent=None if shared else bracket,
            flow=started.get("flow"),
            **common,
        )
        raise RuntimeError(
            f"check flow_media and {_NEVER}: {len(ids)} new records could be this job's clip ({ids}), so none is "
            f"taken; job {job_id}"
        )
    if state == "not_listed":
        if age < NOT_LISTED_S:
            return waiting
        if not shared and bracket == 0:
            ledger.append(job_id, "failed", spent=0, flow=started.get("flow"), **common)
            raise RuntimeError(
                f"nothing was generated: no record of the job showed within {age}s of its submit and the balance did "
                f"not move ({before}). Flow's reason is heard only by the call that submitted, so it is not known; "
                f"a new attempt is a new job; job {job_id}"
            )
        ledger.append(
            job_id, "unknown", spent=None if shared else bracket, flow=started.get("flow"), **common
        )
        why = (
            "other jobs moved the balance meanwhile, so whether it was charged cannot be told"
            if shared
            else f"the balance moved by {bracket} credits"
        )
        raise RuntimeError(
            f"check flow_media and flow_credits and {_NEVER}: no record of the job showed within {age}s of its "
            f"submit, and {why}; job {job_id}"
        )

    stem = ledger.path.parent / f"{record['id']}_{composer._job_digest(job_id)[:8]}"
    try:
        path = str(await composer.fetch_720(session, record, stem, project_id=str(intent.get("project"))))
    except Exception as exc:
        # Nothing is written: the clip exists and is paid for, and a row here would close the job with no file.
        raise RuntimeError(
            f"the clip {record['id']} is finished but no file came back ({type(exc).__name__}: {str(exc)[:160]}): "
            f"call job_collect again, or fetch it with flow_download; nothing was written; job {job_id}"
        ) from exc
    own = not shared and bracket is not None
    spent = bracket if own or quoted is None else quoted
    moved = {"balance_moved": {"quoted": quoted, "moved": bracket}} if own and bracket != quoted else {}
    check = {"body_check": started["body_check"]} if started.get("body_check") else {}
    ledger.append(
        job_id,
        "done",
        media_id=record["id"],
        path=path,
        spent=spent,
        spent_from="bracket" if own else "quoted",
        flow=started.get("flow"),
        **common,
        **check,
        **moved,
    )
    body = started.get("body_check") or {}
    if body and not body.get("ok"):
        raise RuntimeError(
            f"{spent} credits were spent, but Flow's submit request did not match what was asked: model keys "
            f"{body.get('model_keys')}, missing {body.get('missing')}, rpc {body.get('rpcid')}. The clip ({path}) may "
            f"not be what was asked. Do not run this job again under a new job_id; its ledger row holds the body "
            f"check. job {job_id}"
        )
    return {
        "job_id": job_id,
        "kind": intent.get("kind"),
        "state": "collected",
        "media_id": record["id"],
        "path": path,
        "quoted_credits": quoted,
        "spent": spent,
        "spent_from": "bracket" if own else "quoted",
        **common,
        **moved,
    }
