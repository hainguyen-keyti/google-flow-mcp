"""Scene titles through MCP on a draft project: scene_delete and scene_restore act on the scene asked for, never on a
namesake whose title merely contains its title (plan D, L2).

    uv run python scripts/acceptance/scene_titles.py --project <draft project id>

Costs nothing, but it changes the project: it creates three scenes titled `pD<stamp> Scene 1`, `pD<stamp> Scene 1 draft`
and `pD<stamp> Scene 10`, trashes and restores them, and tries to leave all three in the trash. Run it on a draft project
only; nothing here deletes permanently. Exit code is 1 when any row is FAIL or missing.

S1 the three scenes are created and listed active.
S2 scene_delete on the draft trashes it and leaves the other two active.
S3 scene_delete on `Scene 1` trashes it while `Scene 10` stays active.
S4 scene_restore on `Scene 1` brings it back while the draft stays in the trash.
S5 every probe scene ends in the trash.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from video import mcp_server

ROWS = 5
LABELS = ("Scene 1", "Scene 1 draft", "Scene 10")


async def call(session: ClientSession, name: str, arguments: dict[str, Any]) -> tuple[Any, str | None, float]:
    started = time.monotonic()
    result = await session.call_tool(name, arguments)
    took = time.monotonic() - started
    text = "".join(getattr(chunk, "text", "") for chunk in result.content)
    if result.is_error:
        return None, text[:300], took
    return json.loads(text), None, took


async def run(project: str, rows: list[dict[str, Any]]) -> None:
    stamp = int(time.time())
    titles = {label: f"pD{stamp} {label}" for label in LABELS}
    ids: dict[str, str] = {}

    def row(name: str, ok: bool, detail: str) -> None:
        status = "PASS" if ok else "FAIL"
        rows.append({"name": name, "status": status, "detail": detail})
        print(f"{status}  {name:40} {detail}", flush=True)

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

                async def states() -> dict[str, bool | None]:
                    arguments = {"project_id": project, "include_trashed": True}
                    listed, error, _ = await call(session, "scene_list", arguments)
                    if error:
                        raise RuntimeError(f"scene_list failed: {error}")
                    trashed = {scene["scene_id"]: scene["trashed"] for scene in listed}
                    return {label: trashed.get(scene_id) for label, scene_id in ids.items()}

                errors = []
                seconds = 0.0
                for label in LABELS:
                    arguments = {"project_id": project, "title": titles[label]}
                    created, error, took = await call(session, "scene_create", arguments)
                    seconds += took
                    if error:
                        errors.append(f"{label}: {error}")
                    else:
                        ids[label] = created["scene_id"]
                now = await states() if ids else {}
                expected = {label: False for label in LABELS}
                detail = (
                    f"{seconds:.1f}s titles={list(titles.values())} ids={ids} states={now} errors={errors}"
                )
                row("S1 create three namesakes", not errors and now == expected, detail)
                if len(ids) < len(LABELS):
                    # Leave nothing active behind when one creation failed halfway.
                    for label, scene_id in ids.items():
                        arguments = {"project_id": project, "scene_id": scene_id}
                        _, error, _ = await call(session, "scene_delete", arguments)
                        print(f"      cleanup {label}: {error or 'trashed'}", flush=True)
                    return

                steps = (
                    ("S2 delete the draft", "scene_delete", "Scene 1 draft", {"Scene 1 draft"}),
                    ("S3 delete Scene 1", "scene_delete", "Scene 1", {"Scene 1 draft", "Scene 1"}),
                    ("S4 restore Scene 1", "scene_restore", "Scene 1", {"Scene 1 draft"}),
                )
                for name, tool, label, in_trash in steps:
                    arguments = {"project_id": project, "scene_id": ids[label]}
                    _, error, took = await call(session, tool, arguments)
                    now = await states()
                    expected = {each: each in in_trash for each in LABELS}
                    row(name, error is None and now == expected, f"{took:.1f}s states={now} error={error}")

                cleanup = []
                for label, trashed in (await states()).items():
                    if trashed is False:
                        arguments = {"project_id": project, "scene_id": ids[label]}
                        _, error, took = await call(session, "scene_delete", arguments)
                        cleanup.append((label, f"{took:.1f}s", error))
                now = await states()
                row("S5 every probe scene in the trash", all(now.values()), f"states={now} cleanup={cleanup}")
        finally:
            serve.cancel()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--project", required=True, help="A draft project: the run creates and trashes scenes in it."
    )
    args = parser.parse_args(argv)
    rows: list[dict[str, Any]] = []
    asyncio.run(run(args.project, rows))
    failed = sum(1 for each in rows if each["status"] == "FAIL") + ROWS - len(rows)
    print(f"\nrows={ROWS} pass={ROWS - failed} fail={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
