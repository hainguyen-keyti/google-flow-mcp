"""Scenebuilder through MCP on a draft project: a scene can be renamed, given clips, and handed back as one film.

    uv run python scripts/acceptance/scene_build.py --project <draft project id>

Costs nothing, but it changes the project: it creates one scene, adds the same project clip to it twice, downloads the
assembled film into `out/`, and leaves the scene in the trash. Run it on a draft project only. Exit code is 1 when any
row is FAIL or missing.

B1 scene_create then scene_rename: the listing shows the new title.
B2 scene_add_clip puts a clip on the timeline, and scene_download hands over a film as long as the source clip.
B3 scene_add_clip puts the same clip on a second time.
B4 scene_download now hands over a film exactly two source clips long.
B5 the probe scene ends in the trash.

B2 and B4 measure the film instead of reading the page: the page's own 'Total duration' label was measured lagging
behind the timeline on 2026-09-16, while the downloaded file never was. Both compare against the SOURCE clip, which
this script downloads once for free: a check of the shape "the second film is twice the first" is scale free, so a
driver that puts every clip on twice, or adds the wrong clip every time, would pass it (review 2026-09-16).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from video import mcp_server
from video.probes.scene_timeline import ffprobe

ROWS = 5
TOLERANCE_S = 1.0


async def call(session: ClientSession, name: str, arguments: dict[str, Any]) -> tuple[Any, str | None, float]:
    started = time.monotonic()
    result = await session.call_tool(name, arguments)
    took = time.monotonic() - started
    text = "".join(getattr(chunk, "text", "") for chunk in result.content)
    if result.is_error:
        return None, text[:300], took
    return json.loads(text), None, took


def _duration(payload: Any) -> float | None:
    path = Path((payload or {}).get("path", ""))
    if not path.name or not path.exists():
        return None
    measured = ffprobe(path)
    return measured.get("stream_s") or measured.get("container_s")


async def run(project: str, rows: list[dict[str, Any]], state: dict[str, Any]) -> None:
    stamp = int(time.time())
    title = f"pB{stamp}"
    # A room of its own: the source clip and the films land here, so a second run never trips the no-overwrite rule
    # on a file an earlier run left behind (CLAUDE.md rule 5).
    room = f"out/scene_build_{stamp}"
    scene_id: str | None = None
    first: float | None = None

    def row(name: str, ok: bool, detail: str) -> None:
        status = "PASS" if ok else "FAIL"
        rows.append({"name": name, "status": status, "detail": detail})
        print(f"{status}  {name:44} {detail}", flush=True)

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        low = mcp_server.server._lowlevel_server
        serve = asyncio.create_task(
            low.run(
                server_streams[0],
                server_streams[1],
                low.create_initialization_options(),
                raise_exceptions=True,
            )
        )
        try:
            async with ClientSession(client_streams[0], client_streams[1]) as session:
                await session.initialize()

                listed, error, _ = await call(session, "flow_media", {"project_id": project})
                if error:
                    row("B1 create and rename a scene", False, f"flow_media failed: {error}")
                    return
                videos = [m for m in listed["media"] if m.get("kind") == "video" and m.get("id")]
                if not videos:
                    row("B1 create and rename a scene", False, "the project has no video to add")
                    return
                media_id = videos[0]["id"]
                # Free, and it gives the yardstick the film rows compare against.
                arguments = {"project_id": project, "media_id": media_id, "out_dir": room}
                source, error, _ = await call(session, "flow_download", arguments)
                source_s = _duration(source)
                if error or not source_s:
                    row("B1 create and rename a scene", False, f"flow_download failed: {error}")
                    return

                created, error, took = await call(session, "scene_create", {"project_id": project})
                if error:
                    row("B1 create and rename a scene", False, f"scene_create failed: {error}")
                    return
                # Handed out at once, so the caller can bin the scene even if a later row raises.
                scene_id = state["scene_id"] = created["scene_id"]
                arguments = {"project_id": project, "scene_id": scene_id, "title": title}
                _, rename_error, rename_took = await call(session, "scene_rename", arguments)
                names = {}
                if not rename_error:
                    scenes, list_error, _ = await call(
                        session, "scene_list", {"project_id": project, "include_trashed": True}
                    )
                    names = {s["scene_id"]: s["title"] for s in scenes} if not list_error else {}
                row(
                    "B1 create and rename a scene",
                    rename_error is None and names.get(scene_id) == title,
                    f"{took + rename_took:.1f}s scene={scene_id} title={names.get(scene_id)!r} error={rename_error}",
                )

                add_arguments = {"project_id": project, "scene_id": scene_id, "media_id": media_id}
                film_arguments = {"project_id": project, "scene_id": scene_id, "out_dir": room}

                _, add_error, add_took = await call(session, "scene_add_clip", add_arguments)
                film, film_error, film_took = await call(session, "scene_download", film_arguments)
                first = _duration(film)
                row(
                    "B2 add a clip and download the film",
                    add_error is None
                    and film_error is None
                    and bool(first)
                    and abs(first - source_s) <= TOLERANCE_S,
                    f"{add_took + film_took:.1f}s seconds={first} source={source_s} add_error={add_error} "
                    f"download_error={film_error}",
                )

                _, add_error, add_took = await call(session, "scene_add_clip", add_arguments)
                row(
                    "B3 add the same clip a second time",
                    add_error is None,
                    f"{add_took:.1f}s error={add_error}",
                )

                film, film_error, film_took = await call(session, "scene_download", film_arguments)
                second = _duration(film)
                grew = bool(second and abs(second - 2 * source_s) <= TOLERANCE_S)
                row(
                    "B4 the film is two source clips long",
                    film_error is None and grew,
                    f"{film_took:.1f}s one_clip={first} two_clips={second} source={source_s} "
                    f"error={film_error}",
                )
        finally:
            serve.cancel()


async def cleanup(project: str, scene_id: str, rows: list[dict[str, Any]]) -> None:
    """Bin the probe scene whatever happened above: the draft project must not collect scenes on a failed run."""
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        low = mcp_server.server._lowlevel_server
        serve = asyncio.create_task(
            low.run(
                server_streams[0],
                server_streams[1],
                low.create_initialization_options(),
                raise_exceptions=True,
            )
        )
        try:
            async with ClientSession(client_streams[0], client_streams[1]) as session:
                await session.initialize()
                arguments = {"project_id": project, "scene_id": scene_id}
                _, error, took = await call(session, "scene_delete", arguments)
                scenes, list_error, _ = await call(
                    session, "scene_list", {"project_id": project, "include_trashed": True}
                )
                trashed = {s["scene_id"]: s["trashed"] for s in scenes} if not list_error else {}
                rows.append(
                    {
                        "name": "B5 the probe scene ends in the trash",
                        "status": "PASS" if trashed.get(scene_id) else "FAIL",
                        "detail": f"{took:.1f}s trashed={trashed.get(scene_id)} error={error}",
                    }
                )
                print(f"{rows[-1]['status']}  {rows[-1]['name']:44} {rows[-1]['detail']}", flush=True)
        finally:
            serve.cancel()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--project", required=True, help="A draft project: the run creates and trashes a scene in it."
    )
    args = parser.parse_args(argv)
    rows: list[dict[str, Any]] = []
    state: dict[str, Any] = {}
    try:
        asyncio.run(run(args.project, rows, state))
    finally:
        if state.get("scene_id"):
            asyncio.run(cleanup(args.project, state["scene_id"], rows))
    failed = sum(1 for each in rows if each["status"] == "FAIL") + ROWS - len(rows)
    print(f"\nrows={ROWS} pass={ROWS - failed} fail={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
