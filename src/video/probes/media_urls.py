"""Which URL on a listing record is the real asset? GET every https value on the record (plus lh3
suffix variants) through the page context and report status, content-type, size and magic bytes.
$0: reads only.

uv run python -m video.probes.media_urls --project <id> --media <media_id> [--variants =dv,=m18,=s0]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from video.flow.reader import capture, one
from video.session import PROJECT_READY, FlowSession


def urls_in(node: Any, path: tuple[int, ...] = ()) -> list[tuple[tuple[int, ...], str]]:
    found: list[tuple[tuple[int, ...], str]] = []
    if isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(urls_in(value, (*path, i)))
    elif isinstance(node, str) and node.startswith("https://"):
        found.append((path, node))
    return found


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--media", required=True)
    ap.add_argument("--variants", default="=dv,=m18,=s0,=d")
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        frames = await capture(
            session, lambda: session.goto(session.project_url(args.project), ready=PROJECT_READY), settle=10.0
        )
        listing = one(frames, "Zzl0ze")
        record = next(
            (r for r in listing[2] if isinstance(r, list) and len(r) > 2 and r[2] == args.media), None
        )
        if record is None:
            print("record not found for", args.media)
            return
        print(f"record len={len(record)}")
        candidates: list[tuple[str, str]] = [(str(p), u) for p, u in urls_in(record)]
        base_urls = [u for _, u in candidates]
        for suffix in [s for s in args.variants.split(",") if s]:
            candidates.extend((f"variant{suffix} of {i}", u + suffix) for i, u in enumerate(base_urls))
        results = []
        for label, url in candidates:
            try:
                resp = await session.page.request.get(url, max_redirects=5, timeout=60_000)
                body = await resp.body()
                results.append(
                    {
                        "label": label,
                        "status": resp.status,
                        "content_type": resp.headers.get("content-type"),
                        "bytes": len(body),
                        "magic": body[:12].hex(),
                        "is_mp4": body[4:8] == b"ftyp",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append({"label": label, "error": str(exc)[:120]})
    for r in results:
        print(json.dumps(r))


if __name__ == "__main__":
    asyncio.run(main())
