"""Generate one shot of the film, once, and file it under the name the shot list uses.

    uv run python scripts/leelyly/shoot.py p1 p2 p3        # the pilot, 12 credits each

Each shot is its own job id, so a call that dies after Flow took the money cannot be repeated under a new id
(CLAUDE.md rule 9). A shot whose file already exists is skipped rather than bought again.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from shots import CLIPS, PROMPTS

from video import mcp_server

PROJECT = "a0b250bd-056b-4bf6-ba02-d8caacaba8c0"
ENTITY = "b6b47da6-d949-43d0-957f-b787ee3fcf44"
MODEL = "omni-flash"
PRICE = 12
CAP = 36  # the owner approved the pilot only; anything past this needs a new answer, not a new argument


async def shoot(keys: list[str], cap: int) -> int:
    backend = mcp_server.backend
    CLIPS.mkdir(parents=True, exist_ok=True)
    todo = [key for key in keys if not (CLIPS / f"{key}.mp4").exists()]
    if len(todo) * PRICE > cap:
        print(f"FAIL {len(todo)} shots at {PRICE} credits is over the {cap} credit cap for this run")
        return 1
    if not todo:
        print("every shot asked for is already on disk")
        return 0
    before = (await backend.credits()).get("balance")
    print(f"balance {before}, shooting {todo}", flush=True)
    for key in todo:
        started = time.monotonic()
        result = await backend.gen_character(
            PROJECT,
            PROMPTS[key],
            [ENTITY],
            model=MODEL,
            aspect="9:16",
            job_id=f"leelyly-{key}",
            out_dir=str(CLIPS),
        )
        path = Path(result["path"]) if result.get("path") else None
        if path and path.exists():
            path.replace(CLIPS / f"{key}.mp4")
        print(
            f"{key}: spent {result.get('spent')} in {time.monotonic() - started:.0f} s -> "
            f"{CLIPS / f'{key}.mp4'} (media {result.get('media_id')})",
            flush=True,
        )
    after = (await backend.credits()).get("balance")
    print(json.dumps({"balance_before": before, "balance_after": after, "shots": todo}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="+", help="shot keys from scripts/leelyly/shots.py PROMPTS")
    ap.add_argument("--cap", type=int, default=CAP, help="credit ceiling for this run")
    args = ap.parse_args()
    unknown = [key for key in args.keys if key not in PROMPTS]
    if unknown:
        print("FAIL no prompt for", unknown, "; known:", sorted(PROMPTS))
        return 1
    return asyncio.run(shoot(args.keys, args.cap))


if __name__ == "__main__":
    raise SystemExit(main())
