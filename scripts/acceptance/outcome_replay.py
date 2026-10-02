"""Acceptance: every job this machine ever paid for or tried is read back as a typed outcome (plan AM, G5).

    uv run python scripts/acceptance/outcome_replay.py [out_dir]

Exit code is 1 when any row is FAIL. Offline: no browser, no Flow, no credits. It reads every `ledger.jsonl` under the
out folder (machine-local, so the counts are this machine's), classifies each job with `video.outcome.classify`, and
holds the result against what the rows themselves say, counted here without the classifier:

- a job is called uncharged only when its own balance bracket says 0;
- a job is offered a retry only when it is a composer job that Flow failed (status 4), uncharged, with the audio
  filter or with no reason;
- every refusal Flow gave a reason for carries that reason's code.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from video import outcome

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("out")
RETRY_REASONS = ({"PUBLIC_ERROR_AUDIO_FILTERED"}, set())


def jobs_under(folder: Path) -> dict[tuple[str, str], list[dict]]:
    jobs: dict[tuple[str, str], list[dict]] = collections.OrderedDict()
    for ledger in sorted(folder.rglob("ledger.jsonl")):
        for line in ledger.read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            row = json.loads(line)
            jobs.setdefault((str(ledger), str(row.get("job_id"))), []).append(row)
    return jobs


def bracket(row: dict) -> int | None:
    if isinstance(row.get("spent"), int):
        return row["spent"]
    if isinstance(row.get("credits_before"), int) and isinstance(row.get("credits_after"), int):
        return row["credits_before"] - row["credits_after"]
    return None


def main() -> int:
    jobs = jobs_under(OUT)
    table: list[tuple[str, str, str]] = []

    def row(name: str, ok: bool, detail: str) -> None:
        table.append(("PASS" if ok else "FAIL", name, detail))

    ledgers = len({ledger for ledger, _ in jobs})
    row("ledgers found", bool(jobs), f"{ledgers} ledgers, {len(jobs)} jobs under {OUT}")
    said = {key: outcome.classify(rows) for key, rows in jobs.items()}
    counts = collections.Counter(each["code"] for each in said.values())
    row(
        "every job has a code",
        all(each["code"] in outcome.CODES for each in said.values()),
        ", ".join(f"{code} {n}" for code, n in sorted(counts.items())),
    )

    wrongly_free, wrongly_retry, reasons_lost, retryable = [], [], [], []
    by_reason: collections.Counter[str] = collections.Counter()
    for key, rows in jobs.items():
        settled = [r for r in rows if r.get("status") in outcome.SETTLED]
        last = settled[-1] if settled else {}
        flow = last.get("flow") if isinstance(last.get("flow"), dict) else {}
        reasons, statuses = set(flow.get("reasons") or []), flow.get("statuses") or []
        kinds = [r.get("kind") for r in rows if r.get("status") in outcome.INTENT]
        if said[key]["charged"] == 0 and bracket(last) != 0 and rows:
            wrongly_free.append(key[1])
        may_retry = (
            last.get("status") == "failed"
            and rows[-1].get("status") not in outcome.INTENT
            and bracket(last) == 0
            and statuses[-1:] == [4]
            and reasons in RETRY_REASONS
            and "exit_code" not in last
            and kinds[-1:] in (["video"], ["character"])
        )
        if said[key]["retryable"]:
            retryable.append(key[1])
        if said[key]["retryable"] != may_retry:
            wrongly_retry.append(key[1])
        if last.get("status") == "failed" and bracket(last) == 0 and statuses[-1:] == [4]:
            for reason in reasons:
                by_reason[reason] += 1
                want = outcome.REASONS.get(reason, "REFUSED")
                if want not in outcome.RETRYABLE and said[key]["code"] != want:
                    reasons_lost.append(key[1])
                if reasons == {reason} and said[key]["code"] != want:
                    reasons_lost.append(key[1])

    free = f"{len(wrongly_free)} wrong {wrongly_free[:5]}"
    row("uncharged only when the bracket says 0", not wrongly_free, free)
    row(
        "a retry is offered exactly where the rows allow one",
        not wrongly_retry,
        f"{len(wrongly_retry)} wrong {wrongly_retry[:5]}; offered to {sorted(retryable)}",
    )
    row(
        "every reason Flow gave is the job's code",
        not reasons_lost,
        f"{len(reasons_lost)} lost {reasons_lost[:5]}; heard {dict(sorted(by_reason.items()))}",
    )
    for status, name, detail in table:
        print(f"{status}  {name:42s} {detail}")
    return 1 if any(status == "FAIL" for status, _, _ in table) else 0


if __name__ == "__main__":
    sys.exit(main())
