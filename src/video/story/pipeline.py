"""Run the five shots in order, resumable, never spending twice for the same shot (I7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video import gen, post
from video.flow import clips
from video.flow import uploads as uploads_mod
from video.story import composer, persona, product, shots, shots2

PRICE_PER_SHOT = 10
FINAL_NAME = "tryon2_final.mp4"
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


def _done_media_id(ledger: gen.Ledger, job_id: str) -> str | None:
    for row in reversed(ledger.rows(job_id)):
        if row.get("status") != "done":
            continue
        if row.get("media_id"):
            return row["media_id"]
        outputs = row.get("outputs") or []
        if outputs and outputs[0].get("media_id"):
            return outputs[0]["media_id"]
    return None


async def _refetch_720(session, project_id: str, media_id: str, stem: Path) -> Path:
    """Re-download at 720p: the clip-edit path accepts the 360p rendition when 720p is still 404."""
    from video.flow import download as download_mod
    from video.flow import reader

    record = download_mod.latest_version(await reader.records(session, project_id), media_id)
    return await composer.fetch_720(session, record, stem)


def take_ids(job_id: str, hands_risk: bool) -> list[str]:
    """A shot whose hands touch the clothes is shot twice: Veo mangled a hand in two of five v1 shots,
    and a spare take costs one shot instead of a whole rerun."""
    return [job_id, f"{job_id}b"] if hands_risk else [job_id]


def _shot_row(key: str) -> dict[str, Any]:
    for row in shots2.plan():
        if row["key"] == key:
            return row
    raise KeyError(key)


def _done_path(ledger: gen.Ledger, job_id: str) -> Path | None:
    done = [r for r in ledger.rows(job_id) if r.get("status") == "done" and r.get("path")]
    return Path(done[-1]["path"]) if done else None


def clip_file(out_dir: Path, ledger: gen.Ledger, job_id: str, edited: bool = False) -> Path | None:
    """The file that IS this shot, in the order that survives every route.

    The edited reveal wins over the clip it was made from; an extend is written by the editor under its
    own name and its ledger row carries no path at all, so the file named after the job comes next.
    """
    if not any(r.get("status") == "done" for r in ledger.rows(job_id)):
        return None
    if edited:
        worn = worn_path(out_dir, job_id)
        if worn is not None:
            return worn
    named = Path(out_dir) / f"{job_id}.mp4"
    return named if named.is_file() else _done_path(ledger, job_id)


def worn_path(out_dir: Path, job_id: str) -> Path | None:
    """The clip after the Omni edit put the garment on her.

    The edit is billed under its own job id and leaves the base row untouched, so whoever reads the
    ledger would otherwise hand the pre-edit clip to the cut and to the shots that continue from it.
    """
    candidate = Path(out_dir) / f"{job_id}_worn.mp4"
    return candidate if candidate.is_file() else None


def picked(ledger: gen.Ledger, row: dict[str, Any]) -> str | None:
    """The take a human chose for this shot, if one was chosen."""
    picks = [r for r in ledger.rows(row["job_id"]) if r.get("status") == "selected"]
    return picks[-1]["take"] if picks else None


def inconsistent_chain(out_dir: Path) -> list[str]:
    """Shots that were continued from a take nobody picked, so their room and outfit no longer match."""
    ledger = gen.Ledger(Path(out_dir) / "ledger.jsonl")
    by_key = {row["key"]: row for row in shots2.plan()}
    orphaned = []
    for row in shots2.plan():
        parent = by_key.get(row["start_from"] or "")
        if parent is None:
            continue
        pick = picked(ledger, parent)
        if pick and pick != parent["job_id"]:
            orphaned.append(row["key"])
    return orphaned


def select(out_dir: Path, key: str, take: str, reason: str) -> dict[str, Any]:
    """Record which take of a shot goes into the cut and why the other was dropped (I9).

    No machine here can tell a broken hand from a good one, so the choice is a human's and the reason is
    written down next to the money.
    """
    row = _shot_row(key)
    ids = take_ids(row["job_id"], row["hands_risk"])
    if take not in ids:
        raise ValueError(f"{take} is not a take of {key}; its takes are {ids}")
    if not (reason or "").strip():
        raise ValueError("a pick needs the reason the other take was dropped")
    rejected = [job for job in ids if job != take]
    gen.Ledger(Path(out_dir) / "ledger.jsonl").append(
        row["job_id"], "selected", take=take, rejected=rejected, reason=reason
    )
    return {
        "key": key,
        "take": take,
        "rejected": rejected,
        "reason": reason,
        "needs_regen": inconsistent_chain(out_dir),
    }


def load_trims(out_dir: Path) -> dict[str, tuple[float, float]]:
    """Optional `trims.json`: the seconds of each shot worth keeping, keyed by shot key."""
    path = Path(out_dir) / "trims.json"
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text())
    return {key: (float(window[0]), float(window[1])) for key, window in raw.items()}


def clip_paths(out_dir: Path) -> list[tuple[str, Path]]:
    """The clip of every shot, in story order; a gap or an unjudged pair of takes stops the cut."""
    ledger = gen.Ledger(Path(out_dir) / "ledger.jsonl")
    found, missing = [], []
    for row in shots2.plan():
        takes = {
            job: clip_file(out_dir, ledger, job, edited=row["edit_to_product"])
            for job in take_ids(row["job_id"], row["hands_risk"])
        }
        done = {job: path for job, path in takes.items() if path is not None}
        pick = picked(ledger, row)
        if pick and pick in done:
            found.append((row["key"], done[pick]))
            continue
        if pick:
            missing.append(f"{row['key']} (picked {pick}, which has no clip)")
            continue
        if len(done) > 1:
            raise RuntimeError(
                f"{row['key']} has {len(done)} takes and nobody chose one: look at the strips in "
                f"{Path(out_dir) / 'review'} then run 'video story pick'"
            )
        if not done:
            missing.append(f"{row['key']} ({row['job_id']})")
            continue
        found.append((row["key"], next(iter(done.values()))))
    if missing:
        raise RuntimeError(f"no clip for {', '.join(missing)}; run 'video story run2' first")
    return found


def build2(*, out_dir: Path, fade: float = post.FADE) -> dict[str, Any]:
    """Cut the v2 chain: even the audio, dissolve the joins, burn the captions, sheet every clip.

    Loudness and transitions are fixed here because both are free: the clips themselves cost credits.
    """
    out_dir = Path(out_dir)
    trims = load_trims(out_dir)
    review = out_dir / "review"
    sources = clip_paths(out_dir)
    rows: list[dict[str, Any]] = []
    normalised: list[Path] = []
    for key, source in sources:
        window = trims.get(key)
        target = post.normalise(
            source,
            out_dir / "norm" / f"{key}.mp4",
            start=window[0] if window else None,
            end=window[1] if window else None,
        )
        normalised.append(target)
        rows.append(
            {
                "key": key,
                "source": str(source),
                "path": str(target),
                "seconds": round(post.duration(target), 3),
                "lufs": post.lufs(target),
                "trim": list(window) if window else None,
            }
        )
    captions = [shots2.by_key(key).caption for key, _ in sources]
    final = post.crossfade_with_captions(normalised, captions, out_dir / FINAL_NAME, fade)
    strips = [
        str(post.strip(clip, review / f"strip_{row['key']}.jpg"))
        for clip, row in zip(normalised, rows, strict=True)
    ]
    sheet = post.contact_sheet(normalised, review / "contact_sheet.jpg")
    result = {
        "final": str(final),
        "seconds": round(post.duration(final), 3),
        "expected_seconds": round(post.xfade_total([r["seconds"] for r in rows], fade), 3),
        "fade": fade,
        "clips": rows,
        "strips": strips,
        "contact_sheet": str(sheet),
    }
    (out_dir / "cut.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


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
    jobs = [job for row in rows for job in take_ids(row["job_id"], row["hands_risk"])]
    todo = check_resumable(ledger, jobs)
    who = await persona.ensure(session, project_id)
    await product.ensure(session, project_id)

    results: list[dict[str, Any]] = []
    # Seed from the ledger so `--only <shot>` still knows what the shot before it produced.
    paths: dict[str, str] = {}
    media: dict[str, str] = {}
    bases: dict[str, str] = {}
    for done_row in shots2.plan():
        clip = clip_file(out_dir, ledger, done_row["job_id"], edited=done_row["edit_to_product"])
        if clip is not None:
            paths[done_row["key"]] = str(clip)
            media[done_row["key"]] = _done_media_id(ledger, done_row["job_id"])
        base = clip_file(out_dir, ledger, done_row["job_id"])
        if base is not None:
            bases[done_row["key"]] = str(base)
    for row in rows:
        key = row["key"]
        for index, job in enumerate(take_ids(row["job_id"], row["hands_risk"])):
            if job not in todo:
                done = [r for r in ledger.rows(job) if r.get("status") == "done"]
                path = done[-1].get("path") if done else None
                base = path
                if row["edit_to_product"]:
                    edited = worn_path(out_dir, job)
                    path = str(edited) if edited is not None else path
                if index == 0:
                    paths[key] = path
                    bases[key] = base
                    media[key] = _done_media_id(ledger, job)
                results.append({"job_id": job, "key": key, "status": "already done", "path": path})
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
                # The frame comes from the clip BEFORE its edit: a still of her in the set is refused.
                previous = bases.get(row["start_from"]) or paths.get(row["start_from"])
                if not previous:
                    raise RuntimeError(f"{job}: {row['start_from']} has no clip to continue from")
                still = post.last_frame(Path(previous), out_dir / f"{job}_start.png")
                await uploads_mod.upload(session, project_id, still)
                result = await composer.generate_from_frame(
                    session, project_id, start_name=still.name, **common
                )

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
                    result = {
                        **result,
                        "base_path": result.get("path"),
                        "path": str(fresh),
                        "edited": True,
                        "edit_spent": edited.get("spent"),
                    }

            # The next shot continues from the first take: nobody has judged the spare yet.
            if index == 0:
                paths[key] = result.get("path")
                media[key] = result.get("media_id")
                bases[key] = result.get("base_path") or result.get("path")
            results.append({**result, "key": key, "status": "done"})
    return {"character": who, "shots": results}
