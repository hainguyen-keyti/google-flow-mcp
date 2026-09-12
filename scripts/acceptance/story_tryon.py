"""Acceptance for Plan 2: check the try-on video that was actually produced, with numbers.

    uv run python scripts/acceptance/story_tryon.py --out out/story

Exit code is 1 when any row is FAIL. This script never generates anything, so it costs nothing and can
be rerun freely. It deliberately does NOT score "does the face match": no machine here can, so it builds
the contact sheet path for a human to look at and says so.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROWS: list[tuple[str, str, str]] = []
SECRET = re.compile(r"SAPISID=|__Secure-|Authorization:")
SHOTS = 5
PRICE = 10
CELL = 240


def row(name: str, status: str, detail: str) -> None:
    ROWS.append((name, status, detail))
    print(f"{status:7s} {name:18s} {detail}", flush=True)


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="out/story")
    args = ap.parse_args()
    out = Path(args.out)

    clips = sorted(out.glob("tryon-0*.mp4"))
    row("shots", "PASS" if len(clips) == SHOTS else "FAIL", f"{len(clips)}/{SHOTS} clips in {out}")

    durations, sizes = [], []
    for clip in clips:
        info = probe(clip)
        durations.append(seconds(info))
        stream = video_stream(info)
        sizes.append((stream.get("width"), stream.get("height")))
    total = sum(durations)
    ok = clips and all(abs(d - 8.0) < 0.2 for d in durations) and 38.0 <= total <= 42.0
    row("duration", "PASS" if ok else "FAIL", f"each {[round(d, 2) for d in durations]}, total {total:.2f}s")
    row(
        "aspect",
        "PASS" if sizes and all(s == (720, 1280) for s in sizes) else "FAIL",
        f"{sorted(set(sizes))} (want 720x1280)",
    )

    final = out / "tryon_final.mp4"
    info = probe(final) if final.is_file() else {}
    stream = video_stream(info)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    final_ok = (
        final.is_file()
        and (stream.get("width"), stream.get("height")) == (720, 1280)
        and 38.0 <= seconds(info) <= 42.0
        and has_audio
    )
    row(
        "final",
        "PASS" if final_ok else "FAIL",
        f"{final} {stream.get('width')}x{stream.get('height')} {seconds(info):.2f}s audio={has_audio}",
    )

    sheet = out / "contact_sheet.jpg"
    sheet_info = probe(sheet) if sheet.is_file() else {}
    width = video_stream(sheet_info).get("width", 0)
    row(
        "contact sheet",
        "PASS" if width == CELL * SHOTS else "FAIL",
        f"{sheet} width={width} (want {CELL * SHOTS}, one {CELL}px cell per shot)",
    )

    ledger_path = out / "ledger.jsonl"
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines() if line.strip()]
    jobs = {f"tryon-{i:02d}" for i in range(1, SHOTS + 1)}
    done = {j: sum(1 for r in rows if r["job_id"] == j and r["status"] == "done") for j in sorted(jobs)}
    free_failures = all(r.get("spent") == 0 for r in rows if r["status"] == "failed")
    spent = sum(r.get("spent") or 0 for r in rows)
    ledger_ok = all(count == 1 for count in done.values()) and free_failures and spent == SHOTS * PRICE
    row(
        "ledger",
        "PASS" if ledger_ok else "FAIL",
        f"done per job {done}, every failure cost 0 = {free_failures}, total spent {spent} "
        f"(want {SHOTS * PRICE})",
    )

    leaked = [
        p.name
        for p in out.glob("*")
        if p.suffix in {".jsonl", ".log", ".json"} and SECRET.search(p.read_text(errors="ignore"))
    ]
    row(
        "I2 no leak", "PASS" if not leaked else "FAIL", f"session material in {leaked}" if leaked else "clean"
    )

    print()
    print(f"{'STATUS':7s} {'ROW':18s} DETAIL")
    for name, status, detail in ROWS:
        print(f"{status:7s} {name:18s} {detail}")
    failed = [r for r in ROWS if r[1] == "FAIL"]
    print(f"\nrows={len(ROWS)} pass={sum(r[1] == 'PASS' for r in ROWS)} fail={len(failed)}")
    print(f"\nFace and outfit consistency is NOT scored here: open {sheet} and judge it by eye.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
