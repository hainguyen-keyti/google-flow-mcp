"""Judge the delivered film by what a viewer and an accountant would each notice.

    uv run python scripts/acceptance/leelyly_story.py --out out/leelyly

The last row is a HUMAN gate. On 2026-09-13 this repo passed 7/7 on a video that was unusable: the checks
measured resolution, duration, credits and the ledger while the picture lost hands and changed the dress every
shot. A machine cannot judge a face, so a missing review.json is a FAIL, not a skip.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "leelyly"))

from shots import SEGMENTS
from timeline import AVERAGE_MAX_S, SEGMENT_MAX_S, fit, plan, problems, voice_problems

from video import gen

TARGET_S = 120.0
SLACK_S = 5.0
CREDIT_CAP = 160
PRICE = 12


def ffprobe(path: Path, entries: str, stream: bool = False) -> str:
    args = ["ffprobe", "-v", "error"]
    if stream:
        args += ["-select_streams", "v:0", "-show_entries", f"stream={entries}"]
    else:
        args += ["-show_entries", f"format={entries}"]
    args += ["-of", "default=nw=1:nk=1", str(path)]
    return subprocess.run(args, capture_output=True, text=True, check=False).stdout.strip()


def rows(out_dir: Path) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []

    def row(name: str, ok: bool, detail: str) -> None:
        found.append((name, "PASS" if ok else "FAIL", detail))

    film = out_dir / "leelyly_story.mp4"
    if not film.exists():
        row("film exists", False, f"no {film}")
        return found
    seconds = float(ffprobe(film, "duration") or 0.0)
    row("length", abs(seconds - TARGET_S) <= SLACK_S, f"{seconds:.1f} s against {TARGET_S} +/- {SLACK_S}")
    size = ffprobe(film, "width,height", stream=True).splitlines()
    row("vertical 1080x1920", size == ["1080", "1920"], "x".join(size) or "unknown")

    voice_dir = out_dir / "voice"
    durations = {
        segment.line: float(ffprobe(voice_dir / f"{segment.line}.mp3", "duration") or 0.0)
        for segment in SEGMENTS
        if segment.line
    }
    row(
        "every line spoken",
        all(durations.values()),
        f"{sum(1 for v in durations.values() if v)} of {len(durations)}",
    )
    if all(durations.values()):
        fitted = fit(SEGMENTS, durations)
        laid = plan(fitted, hook_seconds=0.0)
        shots = len(fitted)
        average = laid.seconds / shots
        row(
            "cut rhythm",
            average <= AVERAGE_MAX_S,
            f"{shots} shots, average {average:.2f} s, ceiling {AVERAGE_MAX_S}",
        )
        longest = max(segment.seconds for segment in fitted)
        row("no shot held too long", longest <= SEGMENT_MAX_S, f"longest {longest:.2f} s")
        row(
            "voice never overlaps",
            not voice_problems(fitted, durations),
            "; ".join(voice_problems(fitted, durations))[:120] or "clean",
        )
        row(
            "edit rules",
            not problems(fitted, narration_total=sum(durations.values()), target=TARGET_S),
            "; ".join(problems(fitted, narration_total=sum(durations.values()), target=TARGET_S))[:160]
            or "clean",
        )

    ledger = out_dir / "clips" / "ledger.jsonl"
    # Through the shared reader: it splits on the newline alone, because splitlines() also breaks on U+2028,
    # U+2029 and U+0085, which json keeps inside a prompt.
    jobs: dict[str, int] = {
        entry["job_id"]: int(entry["spent"]) for entry in gen.Ledger(ledger).rows() if entry.get("spent")
    }
    spent = sum(jobs.values())
    wanted = sorted({Path(segment.source).stem for segment in SEGMENTS if segment.kind != "still"})
    paid = sorted(job.replace("leelyly-", "") for job in jobs)
    row(
        "every clip paid for once",
        all(f"leelyly-{shot}" in jobs for shot in wanted),
        f"{len(paid)} of {len(wanted)} jobs in the ledger",
    )
    row("spend inside the cap", spent <= CREDIT_CAP, f"{spent} of {CREDIT_CAP} credits")

    review = out_dir / "review.json"
    verdict = ""
    if review.exists():
        try:
            verdict = json.loads(review.read_text(encoding="utf-8")).get("verdict", "")
        except ValueError:
            verdict = "unreadable"
    row("human gate", verdict == "pass", f"review.json says {verdict or 'nothing'}")
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/leelyly")
    args = ap.parse_args()
    found = rows(Path(args.out))
    width = max(len(name) for name, _, _ in found)
    print(f"{'STATUS':6}  {'ROW':{width}}  DETAIL")
    for name, status, detail in found:
        print(f"{status:6}  {name:{width}}  {detail}")
    failed = sum(1 for _, status, _ in found if status != "PASS")
    print(f"\nrows={len(found)} pass={len(found) - failed} fail={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
