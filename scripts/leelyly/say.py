"""Speak the story in LeeLyLy's own voice, one line at a time, for nothing.

Measured 2026-09-18: Flow synthesises a voice preview through rpc no0P6 and answers with a signed url on
flow-content.google/audio, which this fetches. Editing a SAVED voice does not re-synthesise, so every line
starts from the preset and is never saved: the saved voice `LyMienTay18` is only the one attached to the
character for on-camera speech.

    uv run python scripts/leelyly/say.py --project <id> --entity <id>      # synthesise what is missing
    uv run python scripts/leelyly/say.py --check                           # measure what is on disk

Costs nothing: the balance was 125 before and after the first full run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lines import LINES, SAMPLE_MAX, VOICE_PRESET, performance_for, too_long

from video.flow import characters
from video.flow.reader import capture
from video.session import FlowSession

PROJECT = "a0b250bd-056b-4bf6-ba02-d8caacaba8c0"
ENTITY = "b6b47da6-d949-43d0-957f-b787ee3fcf44"
OUT = Path("out/leelyly/voice")
AUDIO_URL = re.compile(r"https://flow-content\.google/audio/[^\"\s\\]+")
# The synthesis took 24 s in the runs measured on 2026-09-18; this is the ceiling before a line is given up on.
SYNTH_WAIT_S = 90.0
SYNTH_STEP_S = 2.0
# A line of ~60 characters came back as 5.2 s, so the whole narration should land near this window.
TOTAL_MIN_S = 85.0
TOTAL_MAX_S = 125.0


def duration_of(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return float(out.stdout.strip() or 0.0)


async def say(session: FlowSession, project: str, entity: str, text: str, delivery: str) -> bytes:
    """One line, spoken in the custom voice, as bytes. Nothing is saved and nothing is spent."""
    page = session.page
    await characters._open_voice_selector(session, project, entity)
    await characters._pick_voice(session, page, project, entity, VOICE_PRESET)
    boxes = page.locator(characters.VOICE_BOXES)
    if await boxes.count() < 2:
        raise LookupError("the voice dialog shows no sample and performance boxes")
    await boxes.nth(0).fill(text)
    await boxes.nth(1).fill(performance_for(delivery))
    await page.wait_for_timeout(800)
    preview = page.get_by_role("button", name=characters.VOICE_PREVIEW).first
    if not await preview.count():
        raise LookupError("the voice dialog offers no Preview, so nothing can be synthesised")
    frames = await capture(session, lambda: preview.click(timeout=10_000), settle=SYNTH_WAIT_S / 2)
    urls = AUDIO_URL.findall(json.dumps(frames, ensure_ascii=False))
    if not urls:
        raise RuntimeError(
            f"Flow answered {sorted(frames)} with no audio url; the line was not synthesised and clicking "
            "again is free, so try the same line again rather than changing it"
        )
    response = await page.request.get(urls[0])
    if response.status != 200:
        raise RuntimeError(f"the audio url answered {response.status}")
    return await response.body()


async def run(project: str, entity: str, only: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = [row for row in LINES if not only or row[0] in only]
    missing = [row for row in wanted if not (OUT / f"{row[0]}.mp3").exists()]
    print(f"{len(wanted)} lines, {len(missing)} to synthesise", flush=True)
    if not missing:
        return
    async with FlowSession("default") as session:
        for line_id, text, delivery in missing:
            started = time.monotonic()
            body = await say(session, project, entity, text, delivery)
            path = OUT / f"{line_id}.mp3"
            path.write_bytes(body)
            print(
                f"{line_id}: {duration_of(path):4.1f} s  {len(body):>7} bytes  "
                f"({time.monotonic() - started:.0f} s)  {text[:40]}",
                flush=True,
            )


def check() -> int:
    rows: list[dict[str, Any]] = []
    for line_id, text, _ in LINES:
        path = OUT / f"{line_id}.mp3"
        rows.append(
            {
                "id": line_id,
                "chars": len(text),
                "file": path.exists(),
                "seconds": round(duration_of(path), 2) if path.exists() else 0.0,
            }
        )
    total = round(sum(row["seconds"] for row in rows), 1)
    missing = [row["id"] for row in rows if not row["file"]]
    long_lines = too_long()
    silent = [row["id"] for row in rows if row["file"] and row["seconds"] < 1.0]
    print(json.dumps({"lines": len(rows), "total_seconds": total, "missing": missing}, ensure_ascii=False))
    problems = []
    if missing:
        problems.append(f"{len(missing)} lines have no audio: {missing}")
    if long_lines:
        problems.append(f"lines over {SAMPLE_MAX} characters would be cut by Flow: {long_lines}")
    if silent:
        problems.append(f"lines that came back under a second: {silent}")
    if rows and not missing and not (TOTAL_MIN_S <= total <= TOTAL_MAX_S):
        problems.append(f"the narration is {total} s, outside {TOTAL_MIN_S}-{TOTAL_MAX_S} s")
    for problem in problems:
        print("FAIL", problem)
    if not problems:
        print(f"PASS narration {total} s over {len(rows)} lines")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=PROJECT)
    ap.add_argument("--entity", default=ENTITY)
    ap.add_argument("--only", default="", help="comma separated line ids")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check()
    asyncio.run(run(args.project, args.entity, [x for x in args.only.split(",") if x]))
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
