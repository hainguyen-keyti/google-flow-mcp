"""Scenebuilder through MCP on a draft project: an agent can put a film together clip by clip and get it back whole.

    uv run python scripts/acceptance/scene_build.py --project <draft project id>

Costs nothing, but it changes the project: it creates one scene, adds five of the project's videos, moves and removes
clips, downloads the films into `out/scene_build_<stamp>/`, and leaves the scene in the trash. Run it on a draft project
only. Exit code is 1 when any row is FAIL or missing.

B1  scene_create then scene_rename: the listing shows the new title.
B2  a new scene reads 16:9 with no clips; scene_set_aspect 9:16 changes it, and asking again leaves it alone.
B3  five scene_add_clip calls put five clips on in the order asked, each answering its own position at the end.
B4  scene_clips reads those five clips in that order, with seconds equal to the sum of the clips.
B5  scene_move_clip moves the first clip to position 2 and scene_clips reads the new order.
B6  scene_remove_clip takes the clip at position 1 off and scene_clips reads the four left, in order.
B7  scene_add_clip puts the removed media back at the end, five clips again.
B8  scene_download hands over a film whose length ffprobe measures within 0.2 s of the listing's seconds, 40 s or more.
B9  every segment of that film is the source clip the listing places there: its middle frame is nearest the middle
    frame of that source (at most 8 apart, and 20 closer than any other source), after the measure proves itself
    (CLAUDE.md rule 8). The frames are also rendered side by side into the run's folder: look at them.
B10 the scene ends in the trash.
B11 the credit balance is the same before and after.

What this gate does NOT cover, so nobody reads more into a green run than it earned:
- It moves one clip later, never earlier, so a drop to the left is proven only by unit tests.
- It runs on whatever five videos with unique titles the draft project holds; two near-identical clips would weaken B9,
  which is why B9 demands a 20 point margin rather than trusting the nearest match alone.
- `out/scene_build_<stamp>/` is left behind on purpose, films included; nothing here prunes it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from video import framediff, mcp_server
from video.flow.scenes import _tile_text
from video.probes.scene_timeline import ffprobe

ROWS = 11
CLIPS = 5
LENGTH_TOLERANCE_S = 0.2
SAME_FRAME = 8.0
MARGIN = 20.0


async def call(session: ClientSession, name: str, arguments: dict[str, Any]) -> tuple[Any, str | None, float]:
    started = time.monotonic()
    result = await session.call_tool(name, arguments)
    took = time.monotonic() - started
    text = "".join(getattr(chunk, "text", "") for chunk in result.content)
    if result.is_error:
        return None, text[:300], took
    return json.loads(text), None, took


def _seconds_of(path: Path) -> float | None:
    measured = ffprobe(path)
    return measured.get("stream_s") or measured.get("container_s")


def _titles(clips: Any) -> list[str]:
    return [clip.get("title") for clip in clips or []]


def order_check(
    film: Path, clips: list[dict[str, Any]], sources: dict[str, Path], room: Path
) -> tuple[bool, str]:
    """Match each segment of the film to its nearest source by middle frame, after the measure proves itself."""
    framediff.self_test(film, [(180, 320, 0, 0), (180, 320, 360, 640)], at=clips[0]["seconds"] / 2)
    source_frames = {title: framediff.frame(path, _seconds_of(path) / 2) for title, path in sources.items()}
    start, verdicts, ok = 0.0, [], True
    sheet_inputs = []
    for index, clip in enumerate(clips):
        middle = start + clip["seconds"] / 2
        segment = framediff.frame(film, middle)
        ranked = sorted((framediff.diff(segment, frame), title) for title, frame in source_frames.items())
        (best, nearest), (runner_up, _) = ranked[0], ranked[1]
        fits = nearest == clip["title"] and best <= SAME_FRAME and runner_up - best >= MARGIN
        ok = ok and fits
        verdicts.append(f"{index}:{'ok' if fits else 'WRONG'}({best:.1f}/{runner_up:.1f})")
        sheet_inputs.append((film, middle, sources[clip["title"]], _seconds_of(sources[clip["title"]]) / 2))
        start += clip["seconds"]
    sheet = room / "order_sheet.png"
    _render_sheet(sheet_inputs, sheet)
    return ok, " ".join(verdicts) + f" sheet={sheet}"


def _render_sheet(pairs: list[tuple[Path, float, Path, float]], sheet: Path) -> None:
    """Film middle frames on top, the sources the listing names below, 180x320 each."""
    frames = []
    for film, at_film, source, at_source in pairs:
        for path, at in ((film, at_film), (source, at_source)):
            out = sheet.with_name(f"frame_{len(frames)}.png")
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    f"{at:.3f}",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=180:320",
                    str(out),
                ],
                check=True,
            )
            frames.append(out)
    top, bottom = frames[0::2], frames[1::2]
    inputs = [arg for frame in [*top, *bottom] for arg in ("-i", str(frame))]
    count = len(top)
    graph = (
        "".join(f"[{i}]" for i in range(count))
        + f"hstack={count}[top];"
        + "".join(f"[{count + i}]" for i in range(count))
        + f"hstack={count}[bottom];[top][bottom]vstack=2"
    )
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, str(sheet)], check=True)


async def run(project: str, rows: list[dict[str, Any]], state: dict[str, Any]) -> None:
    stamp = int(time.time())
    title = f"pB{stamp}"
    room = Path(f"out/scene_build_{stamp}")
    room.mkdir(parents=True, exist_ok=False)

    def row(name: str, ok: bool, detail: str) -> None:
        status = "PASS" if ok else "FAIL"
        rows.append({"name": name, "status": status, "detail": detail})
        print(f"{status}  {name:46} {detail}", flush=True)

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

                balance, error, _ = await call(session, "flow_credits", {})
                state["balance_before"] = (balance or {}).get("balance")
                listed, error, _ = await call(session, "flow_media", {"project_id": project})
                if error:
                    row("B1 create and rename a scene", False, f"flow_media failed: {error}")
                    return
                names = [_tile_text(m.get("title")) for m in listed["media"]]
                videos = [
                    m
                    for m, name in zip(listed["media"], names, strict=True)
                    if m.get("kind") == "video" and name and names.count(name) == 1
                ][:CLIPS]
                if len(videos) < CLIPS:
                    row(
                        "B1 create and rename a scene",
                        False,
                        f"the project holds {len(videos)} uniquely titled videos",
                    )
                    return
                sources: dict[str, Path] = {}
                for video in videos:
                    arguments = {"project_id": project, "media_id": video["id"], "out_dir": str(room)}
                    fetched, error, _ = await call(session, "flow_download", arguments)
                    if error:
                        row("B1 create and rename a scene", False, f"flow_download failed: {error}")
                        return
                    sources[video["title"]] = Path(fetched["path"])

                created, error, took = await call(session, "scene_create", {"project_id": project})
                if error:
                    row("B1 create and rename a scene", False, f"scene_create failed: {error}")
                    return
                # Handed out at once, so the caller can bin the scene even if a later row raises.
                scene_id = state["scene_id"] = created["scene_id"]
                scene = {"project_id": project, "scene_id": scene_id}
                _, rename_error, rename_took = await call(session, "scene_rename", {**scene, "title": title})
                listing, _, _ = await call(
                    session, "scene_list", {"project_id": project, "include_trashed": True}
                )
                titles = {s["scene_id"]: s["title"] for s in listing or []}
                row(
                    "B1 create and rename a scene",
                    rename_error is None and titles.get(scene_id) == title,
                    f"{took + rename_took:.0f}s scene={scene_id} title={titles.get(scene_id)!r} error={rename_error}",
                )

                fresh, error, _ = await call(session, "scene_clips", scene)
                set_first, first_error, _ = await call(
                    session, "scene_set_aspect", {**scene, "aspect": "9:16"}
                )
                set_again, again_error, _ = await call(
                    session, "scene_set_aspect", {**scene, "aspect": "9:16"}
                )
                after, _, _ = await call(session, "scene_clips", scene)
                row(
                    "B2 set the scene to 9:16",
                    error is None
                    and (fresh or {}).get("aspect") == "16:9"
                    and (fresh or {}).get("clips") == []
                    and (set_first or {}).get("changed") is True
                    and (set_again or {}).get("changed") is False
                    and (after or {}).get("aspect") == "9:16",
                    f"new={(fresh or {}).get('aspect')} first={(set_first or {}).get('changed')} "
                    f"again={(set_again or {}).get('changed')} now={(after or {}).get('aspect')} "
                    f"errors={error or first_error or again_error}",
                )

                wanted = [video["title"] for video in videos]
                positions, add_errors, add_took = [], [], 0.0
                for video in videos:
                    added, error, took = await call(
                        session, "scene_add_clip", {**scene, "media_id": video["id"]}
                    )
                    add_took += took
                    positions.append((added or {}).get("position"))
                    if error:
                        add_errors.append(error)
                row(
                    "B3 add five clips, each at the end",
                    not add_errors and positions == list(range(CLIPS)),
                    f"{add_took:.0f}s positions={positions} errors={add_errors[:1]}",
                )

                read, error, _ = await call(session, "scene_clips", scene)
                clips = (read or {}).get("clips") or []
                total = sum(clip.get("seconds") or 0 for clip in clips)
                row(
                    "B4 scene_clips reads them in order",
                    error is None
                    and _titles(clips) == wanted
                    and abs((read or {}).get("seconds", -1) - total) < 0.01,
                    f"titles_match={_titles(clips) == wanted} seconds={(read or {}).get('seconds')} error={error}",
                )

                expected = [wanted[1], wanted[2], wanted[0], wanted[3], wanted[4]]
                moving = clips[0]["clip_id"] if clips else "none"
                moved, error, took = await call(
                    session, "scene_move_clip", {**scene, "clip_id": moving, "position": 2}
                )
                read, read_error, _ = await call(session, "scene_clips", scene)
                clips = (read or {}).get("clips") or []
                row(
                    "B5 move the first clip to position 2",
                    error is None and read_error is None and _titles(clips) == expected,
                    f"{took:.0f}s moved={(moved or {}).get('moved')} order_ok={_titles(clips) == expected} "
                    f"error={error or read_error}",
                )

                removed_title = clips[1]["title"] if len(clips) > 1 else None
                removing = clips[1]["clip_id"] if len(clips) > 1 else "none"
                expected = [t for i, t in enumerate(_titles(clips)) if i != 1]
                _, error, took = await call(session, "scene_remove_clip", {**scene, "clip_id": removing})
                read, read_error, _ = await call(session, "scene_clips", scene)
                clips = (read or {}).get("clips") or []
                row(
                    "B6 remove the clip at position 1",
                    error is None and read_error is None and _titles(clips) == expected,
                    f"{took:.0f}s left={len(clips)} order_ok={_titles(clips) == expected} error={error or read_error}",
                )

                media_back = next((v["id"] for v in videos if v["title"] == removed_title), "none")
                back, error, took = await call(session, "scene_add_clip", {**scene, "media_id": media_back})
                read, read_error, _ = await call(session, "scene_clips", scene)
                clips = (read or {}).get("clips") or []
                row(
                    "B7 put the removed media back at the end",
                    error is None and len(clips) == CLIPS and _titles(clips)[-1] == removed_title,
                    f"{took:.0f}s position={(back or {}).get('position')} clips={len(clips)} error={error or read_error}",
                )

                film_arguments = {**scene, "out_dir": str(room)}
                film, error, took = await call(session, "scene_download", film_arguments)
                path = Path((film or {}).get("path") or room / "missing.mp4")
                measured = _seconds_of(path) if path.exists() else None
                listed_seconds = (read or {}).get("seconds") or 0
                row(
                    "B8 download the whole film",
                    error is None
                    and measured is not None
                    and listed_seconds >= 40
                    and abs(measured - listed_seconds) <= LENGTH_TOLERANCE_S
                    and (film or {}).get("seconds") == listed_seconds,
                    f"{took:.0f}s measured={measured} listing={listed_seconds} attempts={(film or {}).get('attempts')} "
                    f"error={error}",
                )

                if measured is None:
                    row("B9 the film plays the clips in that order", False, "no film to check")
                else:
                    try:
                        ok, detail = order_check(path, clips, sources, room)
                    except Exception as exc:  # noqa: BLE001
                        ok, detail = False, f"{type(exc).__name__}: {str(exc)[:200]}"
                    row("B9 the film plays the clips in that order", ok, detail)
        finally:
            serve.cancel()


async def finish(project: str, rows: list[dict[str, Any]], state: dict[str, Any]) -> None:
    """Bin the scene whatever happened above, then compare the balance: the draft project must not collect scenes."""
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
                if state.get("scene_id"):
                    arguments = {"project_id": project, "scene_id": state["scene_id"]}
                    _, error, took = await call(session, "scene_delete", arguments)
                    listing, _, _ = await call(
                        session, "scene_list", {"project_id": project, "include_trashed": True}
                    )
                    trashed = {s["scene_id"]: s["trashed"] for s in listing or []}
                    ok = bool(trashed.get(state["scene_id"]))
                    detail = f"{took:.0f}s trashed={trashed.get(state['scene_id'])} error={error}"
                    rows.append(
                        {
                            "name": "B10 the scene ends in the trash",
                            "status": "PASS" if ok else "FAIL",
                            "detail": detail,
                        }
                    )
                    print(f"{rows[-1]['status']}  {rows[-1]['name']:46} {detail}", flush=True)
                balance, error, _ = await call(session, "flow_credits", {})
                after = (balance or {}).get("balance")
                before = state.get("balance_before")
                ok = before is not None and before == after
                detail = f"before={before} after={after} error={error}"
                rows.append(
                    {
                        "name": "B11 the balance did not move",
                        "status": "PASS" if ok else "FAIL",
                        "detail": detail,
                    }
                )
                print(f"{rows[-1]['status']}  {rows[-1]['name']:46} {detail}", flush=True)
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
        asyncio.run(finish(args.project, rows, state))
    failed = sum(1 for each in rows if each["status"] == "FAIL") + ROWS - len(rows)
    print(f"\nrows={ROWS} pass={ROWS - failed} fail={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
