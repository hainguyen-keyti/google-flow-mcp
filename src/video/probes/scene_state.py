"""Where does a scene live in the project listing, and how does a trashed one look? $0.
Prints every listing path holding the scene id (with the surrounding entry), then opens the Trash view
and reports whether the scene is there.

uv run python -m video.probes.scene_state --project <id> --scene <scene_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from video.flow.reader import capture, one
from video.session import PROJECT_READY, FlowSession


def paths_to(node: Any, needle: str, path: tuple[int, ...] = ()) -> list[tuple[int, ...]]:
    found: list[tuple[int, ...]] = []
    if isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(paths_to(value, needle, (*path, i)))
    elif isinstance(node, str) and node == needle:
        found.append(path)
    return found


def entry_at(node: Any, path: tuple[int, ...], up: int = 1) -> Any:
    for index in path[: max(len(path) - up, 0)]:
        node = node[index]
    return node


def shape(x: Any, depth: int = 0) -> str:
    if isinstance(x, list):
        inner = ", ".join(shape(i, depth + 1) for i in x[:10]) if depth < 3 else ".."
        return f"L{len(x)}[{inner}]"
    return repr(x)[:36]


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--scene", required=True)
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        page = session.page
        frames = await capture(
            session, lambda: session.goto(session.project_url(args.project), ready=PROJECT_READY), settle=10.0
        )
        listing = one(frames, "Zzl0ze")
        hits = paths_to(listing, args.scene)
        print("paths holding the scene id:", hits)
        for path in hits[:4]:
            entry = entry_at(listing, path, up=1)
            print(f"entry at {path[:-1]}: {shape(entry)}")
            print("   raw:", json.dumps(entry)[:600])
        print(
            "top sections:",
            [f"{i}:{type(v).__name__}{len(v) if isinstance(v, list) else ''}" for i, v in enumerate(listing)],
        )
        tiles = await page.evaluate(
            "() => [...document.querySelectorAll('flow-tile-container')].map(t => (t.innerText||'').trim().replace(/\\s+/g,' ').slice(0,50))"
        )
        print("all-media tiles:", tiles[:6], "count", len(tiles))
        trash_nav = page.locator("mat-list-item, a.mat-mdc-list-item", has_text="Trash").first
        trash_frames = await capture(session, lambda: trash_nav.click(timeout=8_000), settle=6.0)
        print("trash view rpcids:", {k: len(v) for k, v in sorted(trash_frames.items())}, "url:", page.url)
        trash_tiles = await page.evaluate(
            "() => [...document.querySelectorAll('flow-tile-container')].map(t => (t.innerText||'').trim().replace(/\\s+/g,' ').slice(0,50))"
        )
        print("trash tiles:", trash_tiles[:8], "count", len(trash_tiles))
        for rpcid, payloads in trash_frames.items():
            if args.scene in json.dumps(payloads):
                print(f"trash rpc {rpcid} holds the scene id")
        await page.screenshot(path="out/t10_trash.png")


if __name__ == "__main__":
    asyncio.run(main())
