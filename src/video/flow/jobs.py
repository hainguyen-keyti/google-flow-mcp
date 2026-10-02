"""A generation submitted in one call and looked at or fetched in a later one (plan AN, G7).

`composer._submit(detach=True)` leaves a `submitted` row and, once its request has left, a `started` row holding the
workflow Flow's own reply named. Everything here works from those two rows, the rows of the other jobs, the project
listing and the balance. It never clicks Start generation and never types in the prompt box, so nothing here can
start or pay for a job. A download asks Flow's rendition links first and, when every one of them fails, goes through
the editor's own Download menu (`clips.download_rendition`), which is clicked.

Measured 2026-10-02 (`out/an/t1_probe.py`, three jobs of three): the workflow id of the submit reply is the
`workflow_id` the listing gives the clip. Flow's reason for a refusal is heard only by the page that submitted
(plan E, 2026-09-17), so a job that never shows is settled as nothing generated, not as a typed refusal.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from video import gen, outcome
from video.flow import clips, composer, ingredients, reader
from video.session import FlowSession

# How long after its submit a job with no record is still only "not listed". Not measured: the listing has always
# shown a rendering job within a minute; ten leaves room for a slow day before anything is written.
NOT_LISTED_S = 600.0
# How long after its submit another job that is not closed can still move the balance. Not measured: the longest run
# seen took 8 minutes; an hour keeps a job that crashed last week from marking every later bracket as shared.
IN_FLIGHT_S = 3600.0
# Rows of another job that can stand for a balance on the move.
_MOVES = (*outcome.INTENT, "started", *outcome.SETTLED)
# What another job came to when nothing more of it can reach the balance: it finished, or Flow itself failed it.
# Every other outcome says, in its own advice, that the job may still finish and bill.
_CLOSED = ("DONE", "AUDIO_FILTERED", "NO_REASON", "UNSAFE_GENERATION", "PROMINENT_PEOPLE", "REFUSED")
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
    keep = (
        "media_id",
        "path",
        "spent",
        "spent_from",
        "credits_before",
        "credits_after",
        "workflow_id",
        "recipe_check",
    )
    return {
        "job_id": job_id,
        "state": "settled",
        "status": settled.get("status"),
        **{key: settled[key] for key in keep if key in settled},
    }


def standing(ledger: gen.Ledger, job_id: str) -> dict[str, Any] | None:
    """What the ledger alone says of a job: the answer of one that has its settled row, None for a started one that
    Flow must be asked about, and a refusal for one with no started row. Callers ask this first, so neither a settled
    job nor one with no clip to look for opens a browser."""
    intent, started, settled = _run(ledger.rows(job_id))
    if settled is not None:
        return _settled(job_id, settled)
    if intent is None or started is None:
        raise _not_started(job_id, ledger)
    return None


def rendering(rows: list[dict[str, Any]], project: str, prompt: str) -> bool:
    """Whether these rows are a job that job_submit started for this project and prompt and nobody has collected."""
    intent, started, settled = _run(rows)
    if intent is None or started is None or settled is not None:
        return False
    same_prompt = composer._flat(started.get("prompt")) == composer._flat(prompt)
    return same_prompt and intent.get("project") == project


def _not_started(job_id: str, ledger: gen.Ledger) -> LookupError:
    return LookupError(
        f"job {job_id!r} has no started row in {ledger.path}: either a blocking tool ran it, or its job_submit ended "
        "before the request was seen leaving, so job_status and job_collect have no clip of it to look for; what it "
        "came to is its outcome, and flow_media shows whether a clip exists"
    )


def _theirs(job_id: str, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The rows of every other job, by job id, oldest first."""
    theirs: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("job_id") != job_id:
            theirs.setdefault(str(row.get("job_id")), []).append(row)
    return theirs


def _named(theirs: dict[str, list[dict[str, Any]]]) -> set[str]:
    """Every id another job's rows name as its own: the workflow Flow named for it, the media it settled on, its
    outputs. gflow's rows give a workflow id under `media_id`, so a record is held against both of its ids."""
    named: set[str] = set()
    for rows in theirs.values():
        for row in rows:
            ids = [row.get("workflow_id"), _said_by_flow(row)]
            if row.get("status") in ("done", "pending"):
                outputs = row.get("outputs") if isinstance(row.get("outputs"), list) else []
                ids += [row.get("media_id"), *(o.get("media_id") for o in outputs if isinstance(o, dict))]
            named.update(str(each) for each in ids if each)
    return named


def _said_by_flow(row: dict[str, Any]) -> str | None:
    """The workflow Flow's replies named for a job, which a blocking run keeps only inside the `flow` of its row."""
    flow = row.get("flow")
    return flow.get("workflow_id") if isinstance(flow, dict) else None


def _open(intent: dict[str, Any], theirs: dict[str, list[dict[str, Any]]]) -> dict[str, list[Any]]:
    """The other jobs that are not over as far as this job can tell (I-read-2): one with a row after this job's
    submit, or one whose last row is from the hour before it and whose outcome is not closed. Its charge, the refund
    of its failure, or its clip may still be on the way."""
    mine = intent.get("ts") or 0
    still: dict[str, list[Any]] = {}
    for job, rows in theirs.items():
        moves = [row for row in rows if row.get("status") in _MOVES]
        if not moves:
            continue
        later = any((row.get("ts") or 0) > mine for row in moves)
        recent = mine - (moves[-1].get("ts") or 0) < IN_FLIGHT_S
        if (later or recent) and outcome.classify(moves)["code"] not in _CLOSED:
            still[job] = moves
        elif later:
            # Closed, and yet it moved the balance after this job's submit.
            still[job] = []
    return still


def _rivals(
    intent: dict[str, Any], record: dict[str, Any], still: dict[str, list[dict[str, Any]]]
) -> list[str]:
    """The open jobs of this project that no row names a workflow for and whose prompt is the record's: with no
    workflow named for this job either, the one new clip of the prompt could be any of theirs."""
    held = composer._flat(record.get("prompt"))
    rivals = []
    for job, moves in still.items():
        began, begun, _ = _run(moves)
        if began is None or began.get("project") != intent.get("project"):
            continue
        if any(row.get("workflow_id") or _said_by_flow(row) for row in moves):
            continue
        typed = began.get("prompt") or ""
        texts = {
            composer._flat(row.get(key)) for row in (began, begun or {}) for key in ("prompt", "prompt_text")
        }
        # The intent row keeps the first `PROMPT_KEPT` characters of what was typed.
        cut = len(typed) >= outcome.PROMPT_KEPT and held.startswith(composer._flat(typed))
        if held in texts - {""} or cut:
            rivals.append(job)
    return sorted(rivals)


def _claim(
    records: list[dict[str, Any]],
    started: dict[str, Any],
    named: set[str] | frozenset[str] = frozenset(),
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    """Where the job's clip stands in a listing: (state, the record, the candidates when there are several).

    I-money-5: with the workflow Flow named, only the record of that workflow is the clip, whatever else carries the
    same prompt. With none named, the one NEW record whose prompt is the job's and which no other job's rows name is
    the clip, and two are not chosen between."""
    workflow = started.get("workflow_id")
    if workflow:
        mine = [record for record in records if record.get("workflow_id") == workflow]
    else:
        fresh = [
            record
            for record in clips.new_records(set(started.get("workflows_before") or []), records)
            if record.get("id") not in named and record.get("workflow_id") not in named
        ]
        mine = composer.matching_outputs(fresh, started.get("prompt") or "", started.get("prompt_text"))
        if len(mine) > 1:
            return "ambiguous", None, mine
    if not mine:
        return "not_listed", None, []
    return ("ready" if clips.is_done(mine[0]) else "rendering"), mine[0], []


async def _look(
    session: FlowSession,
    job_id: str,
    intent: dict[str, Any],
    started: dict[str, Any],
    others: Callable[[], list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    """The listing, the balance, and then the other jobs' rows: read last, so a job submitted while this call waited
    for the browser is among them."""
    records, _ = await composer.snapshot(session, str(intent.get("project")))
    credits_now = (await reader.credits(session))["balance"]
    try:
        rows: list[dict[str, Any]] | None = others() if others else []
        unread = "a line that is no row" if any(not isinstance(row, dict) for row in rows) else None
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        # Another process mid-append leaves a torn last line for a moment; a line that is JSON and no row fails on
        # the row itself.
        unread = f"{type(exc).__name__}: {str(exc)[:120]}"
    if unread:
        rows = None
    if rows is None and not started.get("workflow_id"):
        raise RuntimeError(
            f"a ledger under the out folder cannot be read right now ({unread}), and Flow named no workflow for this "
            "job, so whether another job owns the one new clip of its prompt is what those rows would say: call "
            f"again; nothing was written; job {job_id}"
        )
    theirs = _theirs(job_id, rows or [])
    still = _open(intent, theirs)
    state, record, candidates = _claim(records, started, _named(theirs))
    rivals = _rivals(intent, record, still) if record is not None and not started.get("workflow_id") else []
    if rivals:
        # I-money-5: one clip that another unnamed job of the prompt could own is taken by neither.
        state, record, candidates = "ambiguous", None, [record]
    return {
        "state": state,
        "record": record,
        "candidates": candidates,
        "rivals": rivals,
        "credits_now": credits_now,
        # Ledgers that cannot be read may hold a job that moved the balance, so they count as one.
        "shared": True if rows is None else bool(still),
    }


async def status(
    session: FlowSession,
    ledger: gen.Ledger,
    job_id: str,
    *,
    others: Callable[[], list[dict[str, Any]]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Where a job stands: `settled`, `rendering`, `ready`, `not_listed` or `ambiguous`. It reads the listing, the
    balance and the ledgers and writes no ledger row."""
    intent, started, settled = _run(ledger.rows(job_id))
    if settled is not None:
        return _settled(job_id, settled)
    if intent is None or started is None:
        raise _not_started(job_id, ledger)
    seen = await _look(session, job_id, intent, started, others)
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


def _free_stem(stem: Path) -> Path:
    """A name no file holds yet. A collect that died after its download left a file no row names; a download never
    overwrites (CLAUDE.md rule 5), so under the same name every later collect would fail on that file for good."""

    def held(candidate: Path) -> bool:
        return candidate.parent.is_dir() and any(
            each.stem == candidate.name for each in candidate.parent.iterdir()
        )

    if not held(stem):
        return stem
    take = 2
    while held(stem.with_name(f"{stem.name}_{take}")):
        take += 1
    return stem.with_name(f"{stem.name}_{take}")


async def _recipe(
    session: FlowSession, project_id: str, record: dict[str, Any], asked: list[dict[str, Any]]
) -> dict[str, Any]:
    """What `ingredients.generate` checks after a blocking run, for a clip fetched by a later call: whether it kept
    every reference the started row says was asked for."""
    try:
        kept = await reader.recipe(session, project_id, record["id"], workflow_id=record.get("workflow_id"))
        return ingredients.recipe_check(kept, [ingredients.reference_from_row(each) for each in asked])
    except Exception as exc:  # noqa: BLE001
        # The clip is paid for and the request carried every reference; a listing that cannot be read now is no
        # reason to call it failed.
        return {"ok": None, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


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
    the row says so (`balance_shared`) and a fetched job's `spent` is the price Flow quoted before the click, said
    as such (I-read-2)."""
    intent, started, settled = _run(ledger.rows(job_id))
    if settled is not None:
        return _settled(job_id, settled)
    if intent is None or started is None:
        raise _not_started(job_id, ledger)
    project_id = str(intent.get("project"))
    seen = await _look(session, job_id, intent, started, others)
    state, record, credits_now, shared = seen["state"], seen["record"], seen["credits_now"], seen["shared"]
    age = int((time.time() if now is None else now) - (started.get("ts") or 0))
    before, quoted = intent.get("credits_before"), intent.get("quoted_credits")
    bracket = before - credits_now if isinstance(before, int) and isinstance(credits_now, int) else None
    common = {
        "credits_before": before,
        "credits_after": credits_now,
        "workflow_id": started.get("workflow_id"),
        **({"balance_shared": True} if shared else {}),
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
        whose = (
            f" or the clip of {', '.join(seen['rivals'])}, started with the same prompt and no workflow named"
            if seen["rivals"]
            else ""
        )
        raise RuntimeError(
            f"check flow_media and {_NEVER}: {len(ids)} new record{'' if len(ids) == 1 else 's'} could be this "
            f"job's clip{whose} ({ids}), so none is taken; job {job_id}"
        )
    if state == "not_listed":
        if age < NOT_LISTED_S:
            return waiting
        if not shared and bracket == 0:
            ledger.append(job_id, "failed", spent=0, flow=started.get("flow"), **common)
            raise RuntimeError(
                f"nothing was generated: no record of the job showed within {age}s of its submit and the balance did "
                f"not move ({before}). Flow's reason is heard only by the call that submitted, and no call heard it "
                "fail the job, so it may still finish and bill: check flow_media in a few minutes, and do not start "
                f"it again under a new job_id before then; job {job_id}"
            )
        ledger.append(
            job_id, "unknown", spent=None if shared else bracket, flow=started.get("flow"), **common
        )
        why = (
            "other jobs could have moved the balance meanwhile, so whether it was charged cannot be told"
            if shared
            else f"the balance moved by {bracket} credits"
        )
        raise RuntimeError(
            f"check flow_media and flow_credits and {_NEVER}: no record of the job showed within {age}s of its "
            f"submit, and {why}; job {job_id}"
        )

    asked = started.get("references") or []
    # Read before the download, so nothing is awaited between the file and the row that names it.
    recipe = {"recipe_check": await _recipe(session, project_id, record, asked)} if asked else {}
    stem = _free_stem(ledger.path.parent / f"{record['id']}_{composer._job_digest(job_id)[:8]}")
    try:
        path = str(await composer.fetch_720(session, record, stem, project_id=project_id))
    except Exception as exc:
        # Nothing is written: the clip exists and is paid for, and a row here would close the job with no file.
        raise RuntimeError(
            f"the clip {record['id']} is finished but no file came back ({type(exc).__name__}: {str(exc)[:160]}): "
            f"call job_collect again, or fetch it with flow_download; nothing was written; job {job_id}"
        ) from exc
    own = not shared and bracket is not None
    spent = bracket if own else quoted
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
        **recipe,
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
    kept = recipe.get("recipe_check") or {}
    if kept.get("ok") is False:
        raise RuntimeError(
            f"{spent} credits were spent and the request carried every reference, but the clip {record['id']} "
            f"({path}) kept another set: missing {kept['missing']}, not asked for {kept['unexpected']} (clip_recipe "
            f"reads it). Do not run this job again under a new job_id. job {job_id}"
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
        **recipe,
        **moved,
    }
