"""Acceptance for Plan 3: judge the try-on video that was actually produced, with numbers.

    uv run python scripts/acceptance/story_quality.py --out out/story2

Exit code is 1 when any row is FAIL. Nothing here generates anything, so it costs nothing and can be
rerun freely.

Plan 2's acceptance measured the plumbing and passed on a video the owner rejected on sight. This one
keeps the plumbing rows and adds the two things that were missing: the parts a machine CAN measure about
the complaint (loudness that jumps, hard cuts, a garment that was never picked between takes), and a
gate that refuses to pass at all until a human has looked at the strips and written down a verdict.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from video import post
from video.story import pipeline, shots2

SECRET = re.compile(r"SAPISID=|__Secure-|Authorization:")
REVIEW_NOTE = "máy không chấm được mặt, tay và món đồ, phải soi strip"
LUFS_WINDOW = (-17.5, -14.5)
LUFS_SPREAD = 1.5
SIZE = (720, 1280)


def probe(path: Path) -> dict:
    done = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(done.stdout or "{}")


def video_stream(info: dict) -> dict:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})


def seconds(info: dict) -> float:
    return float(info.get("format", {}).get("duration", 0) or 0)


def review_gate(path: Path) -> tuple[str, str]:
    """The one row no machine can fill in: a human has to look at the face, the hands and the garment."""
    path = Path(path)
    if not path.is_file():
        return "FAIL", f"{path} is missing; {REVIEW_NOTE}"
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        return "FAIL", f"{path} is not readable JSON ({exc}); {REVIEW_NOTE}"
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict != "pass":
        notes = data.get("notes") or "(no notes)"
        return "FAIL", f"verdict={verdict or 'missing'!r}, notes: {notes}; {REVIEW_NOTE}"
    return "PASS", f"{path} verdict=pass, reviewed by {data.get('reviewer', '?')}"


def takes_check(rows: list[dict]) -> tuple[str, str]:
    """I9: a shot that risks the hands is shot twice and the loser is written down with its reason."""
    problems, good = [], []
    for shot in shots2.plan():
        if not shot["hands_risk"]:
            continue
        ids = pipeline.take_ids(shot["job_id"], True)
        done = [job for job in ids if any(r["job_id"] == job and r.get("status") == "done" for r in rows)]
        picks = [r for r in rows if r["job_id"] == shot["job_id"] and r.get("status") == "selected"]
        if len(done) < len(ids):
            problems.append(f"{shot['key']}: {len(done)} take(s) of {len(ids)}")
        elif not picks:
            problems.append(f"{shot['key']}: two takes and no pick")
        elif not str(picks[-1].get("reason") or "").strip():
            problems.append(f"{shot['key']}: pick has no reason")
        else:
            good.append(f"{shot['key']}={picks[-1]['take']}")
    if problems:
        return "FAIL", "; ".join(problems)
    return "PASS", f"two takes judged and written down: {', '.join(good)}"


def chain_jobs() -> list[str]:
    """Every job the chain is supposed to pay for: each take, plus an edit for each dressed take."""
    jobs = []
    for shot in shots2.plan():
        ids = pipeline.take_ids(shot["job_id"], shot["hands_risk"])
        jobs += ids
        # An edit-mode shot IS the edit: it has no separate edit job to pay for.
        if shot["edit_to_product"] and shot["mode"] != "edit":
            jobs += [f"{job}-edit" for job in ids]
    return jobs


def ledger_check(rows: list[dict]) -> tuple[str, str]:
    """One paid outcome per take, no failure that moved money, and the bill the plan quoted."""
    jobs = chain_jobs()
    mine = [r for r in rows if r["job_id"] in jobs]
    done = {job: sum(1 for r in mine if r["job_id"] == job and r.get("status") == "done") for job in jobs}
    free_failures = [r["job_id"] for r in mine if r.get("status") == "failed" and (r.get("spent") or 0)]
    spent = sum(r.get("spent") or 0 for r in mine)
    # A clip reused from an earlier probe was still paid for, just under another job id in this ledger.
    for row in mine:
        source = row.get("reused_from")
        if source:
            spent += sum(r.get("spent") or 0 for r in rows if r["job_id"] == source)
    expected = shots2.expected_credits()
    missing = [job for job, count in done.items() if count != 1]
    ok = not missing and not free_failures and spent == expected
    detail = (
        f"spent {spent} of {expected} expected; "
        f"{len(jobs) - len(missing)}/{len(jobs)} jobs with exactly one done row"
    )
    if missing:
        detail += f"; wrong done count for {missing}"
    if free_failures:
        detail += f"; failures that cost money: {free_failures}"
    return ("PASS" if ok else "FAIL"), detail


def audio_check(levels: list[float | None]) -> tuple[str, str]:
    """The owner heard the volume jump at every cut: level alone is not enough, the spread is the defect."""
    if not levels or any(level is None for level in levels):
        return "FAIL", f"no loudness reading for {sum(1 for x in levels if x is None)} of {len(levels)} clips"
    low, high = min(levels), max(levels)
    spread = high - low
    in_window = all(LUFS_WINDOW[0] <= level <= LUFS_WINDOW[1] for level in levels)
    detail = (
        f"{[round(level, 1) for level in levels]} LUFS, spread {spread:.1f} dB "
        f"(want each in {LUFS_WINDOW}, spread <= {LUFS_SPREAD})"
    )
    return ("PASS" if in_window and spread <= LUFS_SPREAD else "FAIL"), detail


def duration_check(
    clip_seconds: list[float], final_seconds: float, fade: float | list[float]
) -> tuple[str, str]:
    if not clip_seconds:
        return "FAIL", "no clips to add up"
    expected = post.xfade_total(clip_seconds, fade)
    ok = abs(final_seconds - expected) <= 0.1
    detail = (
        f"final {final_seconds:.2f}s, formula {expected:.2f}s "
        f"(sum {sum(clip_seconds):.2f} minus joins {fade})"
    )
    return ("PASS" if ok else "FAIL"), detail


def shots_check(clips: list[dict], declared: dict[str, int]) -> tuple[str, str]:
    """Each shot against its OWN declared length: an extend runs 7s where a fresh shot runs 8s."""
    if not clips:
        return "FAIL", "no clips found"
    wrong_length = [c["key"] for c in clips if abs(c["seconds"] - declared.get(c["key"], 0)) > 0.2]
    wrong_size = [c["key"] for c in clips if tuple(c["size"]) != SIZE]
    detail = (
        f"{len(clips)} clips, {[round(c['seconds'], 2) for c in clips]}s, "
        f"sizes {sorted({tuple(c['size']) for c in clips})}"
    )
    if wrong_length:
        detail += f"; wrong length: {wrong_length} (want {[declared.get(k) for k in wrong_length]}s +-0.2)"
    if wrong_size:
        detail += f"; wrong size: {wrong_size} (want {SIZE[0]}x{SIZE[1]})"
    return ("PASS" if not wrong_length and not wrong_size else "FAIL"), detail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acceptance for the v2 try-on video.")
    parser.add_argument("--out", default="out/story2")
    args = parser.parse_args(argv)
    out = Path(args.out)
    rows: list[tuple[str, str, str]] = []

    def row(name: str, status: str, detail: str) -> None:
        rows.append((name, status, detail))
        print(f"{status:7s} {name:14s} {detail}", flush=True)

    cut_path = out / "cut.json"
    cut = json.loads(cut_path.read_text()) if cut_path.is_file() else {}
    clips = cut.get("clips") or []
    for clip in clips:
        info = probe(Path(clip["path"])) if Path(clip["path"]).is_file() else {}
        stream = video_stream(info)
        clip["size"] = (stream.get("width"), stream.get("height"))

    if not cut:
        row("cut", "FAIL", f"{cut_path} is missing; run 'video story cut2 --out {out}' first")
    else:
        row("cut", "PASS", f"{cut_path} describes {len(clips)} clips")

    declared = {shot.key: shot.duration for shot in shots2.SHOTS}
    trimmed = [c["key"] for c in clips if c.get("trim")]
    row("shots", *shots_check([c for c in clips if c["key"] not in trimmed], declared))
    if trimmed:
        row("trims", "PASS", f"trimmed on purpose, length not checked: {trimmed}")

    row("audio", *audio_check([c.get("lufs") for c in clips]))

    final = Path(cut.get("final") or (out / pipeline.FINAL_NAME))
    info = probe(final) if final.is_file() else {}
    stream = video_stream(info)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    shape_ok = final.is_file() and (stream.get("width"), stream.get("height")) == SIZE and has_audio
    shape = (
        f"{stream.get('width')}x{stream.get('height')} audio={has_audio}"
        if final.is_file()
        else "does not exist"
    )
    row("final", "PASS" if shape_ok else "FAIL", f"{final} {shape}")
    row(
        "duration",
        *duration_check(
            [c["seconds"] for c in clips],
            post.video_duration(final) if final.is_file() else 0.0,
            cut.get("fades") or cut.get("fade", post.FADE),
        ),
    )

    strips = [Path(p) for p in cut.get("strips") or []]
    sheet = Path(cut.get("contact_sheet") or (out / "review" / "contact_sheet.jpg"))
    sheets_ok = len(strips) == len(clips) and all(p.is_file() for p in strips) and sheet.is_file()
    row(
        "review sheet",
        "PASS" if sheets_ok and clips else "FAIL",
        f"{len(strips)} strips at 2 frames/s for {len(clips)} clips, contact sheet {sheet.name} "
        f"exists={sheet.is_file()}",
    )

    ledger_path = out / "ledger.jsonl"
    ledger_rows = (
        [json.loads(line) for line in ledger_path.read_text().splitlines() if line.strip()]
        if ledger_path.is_file()
        else []
    )
    row("takes", *takes_check(ledger_rows))
    row("ledger", *ledger_check(ledger_rows))

    orphaned = pipeline.inconsistent_chain(out) if ledger_rows else []
    if not ledger_rows:
        chain_detail = f"{ledger_path} is missing, nothing to check"
    elif orphaned:
        chain_detail = f"{orphaned} continue from a take nobody picked"
    else:
        chain_detail = "every shot follows a chosen take"
    row("chain", "PASS" if ledger_rows and not orphaned else "FAIL", chain_detail)

    row("human gate", *review_gate(out / "review.json"))

    leaked = [
        p.name
        for p in out.glob("*")
        if p.suffix in {".jsonl", ".log", ".json"} and SECRET.search(p.read_text(errors="ignore"))
    ]
    row(
        "I2 no leak", "PASS" if not leaked else "FAIL", f"session material in {leaked}" if leaked else "clean"
    )

    print()
    print(f"{'STATUS':7s} {'ROW':14s} DETAIL")
    for name, status, detail in rows:
        print(f"{status:7s} {name:14s} {detail}")
    failed = [r for r in rows if r[1] == "FAIL"]
    print(f"\nrows={len(rows)} pass={sum(r[1] == 'PASS' for r in rows)} fail={len(failed)}")
    if failed:
        print(f"\n{REVIEW_NOTE}: open {out / 'review'} and judge the strips by eye.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
