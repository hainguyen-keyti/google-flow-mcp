"""What a month's credits went on and what the balance still buys (plan AY, 2026-11-12).

Pure: it reads ledger rows and the surveyed price table, opens nothing and spends nothing. The owner works inside
Google AI Pro's monthly Flow credits, and the ledgers since 2026-09 showed nearly every clip made at 720p, three times
the 360p price, so the tool that answers this says where the month went before the next shot is paid for.
"""

from __future__ import annotations

import calendar
import time
from collections import defaultdict
from typing import Any

from video.flow import video

# The rows a job ends on; `spent` on the last of them is the job's bill.
OUTCOMES = ("done", "collected", "failed", "unknown", "pending")
MADE = ("done", "collected")
# One clip of these cells is what the balance is said in: the draft, the final and the cheap fixed-price model.
BUYS = (("omni-1.2-flash", "360p", 8), ("omni-1.2-flash", "720p", 8), ("veo-lite", None, 8))
FLOW_TIME_NOTE = (
    "the time Flow sends beside the balance; what it marks (the next refresh, an expiry) is unmeasured, so it is "
    "passed on as it came"
)


def _month(ts: float) -> str:
    return time.strftime("%Y-%m", time.localtime(ts))


def _jobs(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each job's bill (its last outcome row carrying a number), when it landed, and what it asked for."""
    jobs: dict[str, dict[str, Any]] = {}
    for row in rows:
        job_id = row.get("job_id")
        if not isinstance(job_id, str):
            continue
        job = jobs.setdefault(job_id, {"spent": 0, "ts": 0.0, "status": None, "settled": False})
        if row.get("status") == "started":
            job.update(started=float(row.get("ts") or 0))
        if isinstance(row.get("quoted_credits"), int) and not isinstance(row.get("quoted_credits"), bool):
            job["quoted"] = row["quoted_credits"]
        if row.get("status") in OUTCOMES:
            job["settled"] = True
        for key in ("kind", "model", "resolution"):
            if row.get(key) is not None and key not in job:
                job[key] = row[key]
        spent = row.get("spent")
        if row.get("status") in OUTCOMES and isinstance(spent, int) and not isinstance(spent, bool):
            job.update(spent=spent, ts=float(row.get("ts") or 0), status=row["status"])
    return jobs


def summary(
    *,
    rows: list[dict[str, Any]],
    shots: list[dict[str, Any]],
    balance: int,
    month: str | None = None,
    now: float | None = None,
    balance_raw: Any = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    month = month or _month(now)
    charged = {
        job_id: job for job_id, job in _jobs(rows).items() if job["spent"] > 0 and _month(job["ts"]) == month
    }
    by: dict[tuple[Any, Any, Any], list[int]] = defaultdict(lambda: [0, 0])
    for job in charged.values():
        cell = by[(job.get("kind"), job.get("model"), job.get("resolution"))]
        cell[0] += job["spent"]
        cell[1] += 1
    lost = [job for job in charged.values() if job["status"] not in MADE]
    kinds = {row.get("job_id"): row.get("status") for row in shots if row.get("status") in ("draft", "final")}

    def of(kind: str) -> dict[str, int]:
        mine = [job for job_id, job in charged.items() if kinds.get(job_id) == kind]
        return {"credits": sum(job["spent"] for job in mine), "jobs": len(mine)}

    # job_submit is charged at the click and its bill is written only at job_collect: named apart until then.
    waiting = [
        job
        for job in _jobs(rows).values()
        if not job["settled"] and "started" in job and _month(job["started"]) == month
    ]
    spent = sum(job["spent"] for job in charged.values())
    year, number = (int(part) for part in month.split("-"))
    days = (
        int(time.strftime("%d", time.localtime(now)))
        if _month(now) == month
        else calendar.monthrange(year, number)[1]
    )
    if _month(now) < month:
        days = 0
    out: dict[str, Any] = {
        "month": month,
        "balance": balance,
        "spent": spent,
        "jobs_charged": len(charged),
        "by": [
            {"kind": kind, "model": model, "resolution": resolution, "credits": credits, "jobs": jobs}
            for (kind, model, resolution), (credits, jobs) in sorted(by.items(), key=lambda item: -item[1][0])
        ],
        "charged_with_no_clip": {"credits": sum(job["spent"] for job in lost), "jobs": len(lost)},
        "submitted_not_collected": {
            "quoted_credits": sum(job.get("quoted", 0) for job in waiting),
            "jobs": len(waiting),
        },
        "drafts": of("draft"),
        "finals": of("final"),
        "days_so_far": days,
        "per_day": round(spent / days, 1) if days else 0,
        "balance_buys": [
            {
                "model": model,
                "resolution": resolution,
                "seconds": seconds,
                "credits_each": price,
                "clips": balance // price,
            }
            for model, resolution, seconds in BUYS
            if (price := video.price(model, resolution, seconds if resolution else None, 1))
        ],
    }
    stamp = balance_raw[6][0] if _has_time(balance_raw) else None
    if stamp is not None:
        out["flow_time"] = {
            "epoch": stamp,
            "local": time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp)),
            "note": FLOW_TIME_NOTE,
        }
    return out


def _has_time(raw: Any) -> bool:
    return (
        isinstance(raw, list)
        and len(raw) > 6
        and isinstance(raw[6], list)
        and bool(raw[6])
        and isinstance(raw[6][0], int)
    )
