"""Records that share a media id (edit versions): dump them raw so the parser can tell versions apart. $0.

uv run python -m video.probes.versions --project <id> [--media <media_id>]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict

from video.flow.reader import capture, one
from video.session import PROJECT_READY, FlowSession


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--media", default=None)
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        frames = await capture(
            session, lambda: session.goto(session.project_url(args.project), ready=PROJECT_READY), settle=10.0
        )
        listing = one(frames, "Zzl0ze")
        by_media = defaultdict(list)
        for record in listing[2] or []:
            if isinstance(record, list) and len(record) > 3:
                by_media[record[2]].append(record)
        types = defaultdict(int)
        for record in listing[2] or []:
            if isinstance(record, list) and len(record) > 3:
                types[record[3]] += 1
        print("record types:", dict(types))
        for media_id, records in by_media.items():
            if len(records) > 1 or media_id == args.media:
                print(f"media {media_id}: {len(records)} records")
                for record in records:
                    details = record[5] if len(record) > 5 else None
                    status = details[8] if isinstance(details, list) and len(details) > 8 else None
                    print(
                        f"  workflow={record[0]} type={record[3]} len={len(record)} status={status}"
                        f" created={details[0] if isinstance(details, list) else None}"
                    )
                    print("   raw:", json.dumps(record)[:1600])


if __name__ == "__main__":
    asyncio.run(main())
