"""Generate one base clip whose stance is deliberately held, so Omni edits of it keep sharing a pose.

    uv run python -m video.probes.still_base --project <id> [--out out/still]

SPENDS 10 CREDITS (veo-lite, 8s, count 1). Unlike the other probes in this package it is not free.

Why it exists: two Omni edits of one base clip share a body pose only inside a window, measured
2026-09-13 as 0.88s to 3.58s on the old base, 2.70s wide. That window sits exactly where the base
clip moves least. This asks whether a base that holds its stance on purpose widens it, which decides
whether a reference-style reel with many outfits is reachable. scripts/acceptance/pose_window.py
judges the answer against 4.00s.

It stays thin on purpose. composer._submit already owns the price guard, the prompt read-back, the
single click, `_await_submit`, the screenshot and the ledger rows; a second copy of any of that here
would be a second place for it to drift.

The outfit is her everyday clothes, never the garment being sold: Flow silently refuses to generate
the character wearing it, taking the click, creating no job and charging nothing (measured four times
across three wordings on 2026-09-13). The garment goes on afterwards, through an Omni edit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from video import gen
from video.flow import composer
from video.session import FlowSession
from video.story import bible, persona

PRICE = 10
ASPECT = "9:16"

STILL = bible.Scene(
    camera=(
        "full-length mirror shot, phone resting on a tripod and locked off, the framing never changes, "
        "the camera does not pan, tilt, zoom or follow her, vertical framing"
    ),
    pose=(
        "standing still in front of the mirror, both feet planted flat on the floor shoulder-width "
        "apart, weight even on both feet, she does not step, shift her weight, sway or turn; both arms "
        "hang still at her sides with hands relaxed and motionless; only her head turns slightly and "
        "she blinks"
    ),
    outfit="a plain oversized white cotton t-shirt and soft shorts, her own everyday clothes",
    expression="pleased, a soft smile, small natural blinks",
)


async def run(project: str, out_dir: Path, job_id: str, profile: str, wait: float) -> dict:
    prompt = bible.compose(STILL)
    print(f"[probe] prompt is {len(prompt)} characters, job {job_id}, expecting {PRICE} credits")
    async with FlowSession(profile) as session:
        who = await persona.ensure(session, project)
        print(f"[probe] character {who['name']} (created={who['created']})")
        return await composer.generate_with_character(
            session,
            project,
            character=who["name"],
            prompt=prompt,
            job_id=job_id,
            expected_credits=PRICE,
            out_dir=out_dir,
            aspect=ASPECT,
            wait=wait,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--out", default="out/still")
    parser.add_argument("--job", default="still-base-1")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--wait", type=float, default=300.0)
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(run(args.project, out_dir, args.job, args.profile, args.wait))
    except gen.AlreadySubmitted as done:
        print(f"[probe] refused: {done}")
        return 2
    except RuntimeError as failed:
        # Flow can take the click, create nothing and charge nothing. The ledger row and the
        # screenshot next to it are the only evidence left once the page is gone.
        print(f"[probe] nothing generated: {failed}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
