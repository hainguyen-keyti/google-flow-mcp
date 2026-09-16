"""MCP smoke: start the server in-memory and actually USE it, the way an agent would.

    uv run python scripts/acceptance/mcp_smoke.py

Exit code is 1 when any row is FAIL. Costs nothing, but it does drive a real browser: every read-only
tool is called against the live account, so a run takes roughly 2 to 3 minutes (`Backend._with` opens one
FlowSession per call, measured 7 to 14 seconds each on 2026-09-13).

Why it calls tools for real instead of counting them. Until 2026-09-13 this script called exactly ONE of
26 tools and accepted `len(names) >= 15`, so two capabilities (`flow clip reconcile` and `flow uploads`)
were missing from the MCP surface for weeks with every gate green. A tool nobody calls is a tool nobody
knows is broken.

Why a plausible payload is checked and not just `is_error`. Also measured 2026-09-13: `flow_media` used
to accept an `all_versions` argument it did not implement, drop it silently, and answer as if nothing had
been asked. That returns `is_error=False` and looks perfect, so `is_error` alone proves very little.

MUTATION TOOLS ARE NEVER CALLED (invariant I4). `agent_mode`, `scene_create`, `scene_delete`, `scene_restore`,
`project_create`, `project_delete` and `flow_upload` cost nothing but change the owner's real project;
a gate that ran them would quietly litter it on every run. The roster check below asserts they exist and
the run asserts they stayed untouched.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys

from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from video import mcp_server

SECRET = re.compile(r"SAPISID=|__Secure-|Authorization:")

# Free AND side-effect free: safe to call on the owner's live account on every run.
READ_ONLY_NO_ARGS = ("flow_lane", "flow_projects", "flow_credits", "flow_tools")
READ_ONLY_PER_PROJECT = ("flow_media", "flow_characters", "flow_uploads", "scene_list")

# Free but they CHANGE things. Never called here; see I4 in the plan.
MUTATING = (
    "agent_mode",
    "scene_create",
    "scene_delete",
    "scene_restore",
    "scene_add_clip",
    "scene_rename",
    "project_create",
    "project_rename",
    "project_delete",
    "character_create",
    "character_delete",
    "flow_upload",
    "clip_reconcile",
    "gen_t2i",
    "gen_i2i",
)

# Free for Flow but they write a file on this machine, so they are not called here either.
DOWNLOADING = ("flow_download", "scene_download")

# clip_download at 1080p was measured free, but its 4k rendition is a Flow upscale whose price has never been
# measured, and its own description says so. Filing it with the plainly free ones was a claim the tool contradicts
# (review 2026-09-16); a test in tests/test_mcp_server.py keeps the two from drifting apart again.
MAYBE_SPENDING = ("clip_download",)

# Real money. Never called here, and the roster row insists they stay declared rather than quietly vanish.
SPENDING = ("gen_t2v", "gen_i2v", "gen_r2v", "gen_character", "clip_extend", "clip_edit", "agent_send")

# One source for the roster contract: a tool that exists must be classified above, and a name declared above must
# still be served. The count used to be typed here as well, so adding three tools left this gate red while the unit
# test was green (review of plan scene-timeline-tools, 2026-09-16).
CLASSIFIED = (
    *READ_ONLY_NO_ARGS,
    *READ_ONLY_PER_PROJECT,
    *MUTATING,
    *DOWNLOADING,
    *MAYBE_SPENDING,
    *SPENDING,
)


def payload_of(result):
    text = "".join(getattr(chunk, "text", "") for chunk in result.content)
    try:
        return text, json.loads(text)
    except ValueError:
        return text, None


async def call(session, name, arguments, expect):
    """Call one tool and judge the answer, not merely the absence of an error.

    Returns the parsed payload too, so a caller never has to spend a second browser session re-asking
    for something it has already been told.
    """
    result = await session.call_tool(name, arguments)
    text, payload = payload_of(result)
    if result.is_error:
        return "FAIL", f"{name}: is_error, {text[:90]}", payload
    if SECRET.search(text):
        return "FAIL", f"{name}: session material leaked into the reply", payload
    if payload is None:
        return "FAIL", f"{name}: reply is not JSON, {text[:90]}", payload
    detail = expect(payload)
    if detail is not None:
        return "FAIL", f"{name}: {detail}", payload
    return "PASS", f"{name}: {text[:70].replace(chr(10), ' ')}", payload


def _each_has(items, *keys):
    return all(isinstance(item, dict) and all(item.get(key) for key in keys) for item in items)


def a_lane(payload):
    # Every other row depends on this account being on the migrated host (CLAUDE.md rule 1).
    if not isinstance(payload, dict):
        return f"expected an object, got {type(payload).__name__}"
    if payload.get("verdict") != "MIGRATED":
        return f"verdict is {payload.get('verdict')!r}, expected 'MIGRATED'"
    return None


def a_project_list(payload):
    # Ids are NOT all UUIDs: measured 2026-09-14, one project is `8822142b-ca75-46b7-aac8-03d2831_backfill`.
    if not isinstance(payload, list) or not payload:
        return "expected a non-empty list of projects"
    ids = [project.get("id") if isinstance(project, dict) else None for project in payload]
    if not all(isinstance(project_id, str) and project_id for project_id in ids):
        return "a project has no id"
    if len(set(ids)) != len(ids):
        return f"project ids repeat: {len(ids)} projects, {len(set(ids))} distinct ids"
    return None


def a_balance(payload):
    balance = payload.get("balance") if isinstance(payload, dict) else None
    if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
        return f"balance is {balance!r}, expected an int of 0 or more"
    return None


def media_of(project_id):
    def check(payload):
        if not isinstance(payload, dict):
            return f"expected an object, got {type(payload).__name__}"
        meta = payload.get("meta")
        meta_id = meta.get("id") if isinstance(meta, dict) else None
        if meta_id != project_id:
            return f"meta.id is {meta_id!r}, but the smoke asked for {project_id}"
        media = payload.get("media")
        if not isinstance(media, list):
            return f"media is {type(media).__name__}, expected a list"
        if any(not isinstance(item, dict) or item.get("project_id") != project_id for item in media):
            return "a media record belongs to another project"
        models = payload.get("models")
        if not models or not isinstance(models, list) or not all(isinstance(name, str) for name in models):
            return f"models is {models!r}, expected a non-empty list of names"
        return None

    return check


def versions_of(project_id):
    # all_versions keeps the object and adds `versions` (2026-09-15); a bare list is the old, broken shape.
    # Grid rows carry no `type` and version records always do, so a swallowed all_versions shows up here.
    def check(payload):
        if not isinstance(payload, dict):
            return f"expected an object, got {type(payload).__name__}"
        versions = payload.get("versions")
        if not isinstance(versions, list):
            return f"versions is {type(versions).__name__}, expected a list"
        for record in versions:
            if not isinstance(record, dict) or record.get("project_id") != project_id:
                return "a version record belongs to another project"
            if not isinstance(record.get("type"), str) or not record.get("workflow_id"):
                return "a record has no type or workflow_id: grid rows came back instead of versions"
        return None

    return check


def a_character_list(payload):
    if not isinstance(payload, list):
        return f"expected a list, got {type(payload).__name__}"
    return None if _each_has(payload, "entity_id", "name") else "a character has no entity_id or name"


def a_tool_list(payload):
    if not isinstance(payload, list) or not payload:
        return "expected a non-empty list of tools"
    return None if _each_has(payload, "id", "name") else "a tool has no id or name"


def a_scene_list(payload):
    # Called without include_trashed. Measured on c5d1301b: 10 scenes, none trashed; the flag adds 6 more.
    if not isinstance(payload, list):
        return f"expected a list, got {type(payload).__name__}"
    if not _each_has(payload, "scene_id"):
        return "a scene has no scene_id"
    trashed = sum(1 for scene in payload if scene.get("trashed") is True)
    return f"{trashed} trashed scene(s) in a listing that did not ask for them" if trashed else None


def an_upload_count(payload):
    """Why `a_dict` was not enough here. Measured 2026-09-14: flow_uploads was answering `count: null`,
    a doubled tile figure and an empty rpcid list on a project holding 4 uploads, and this gate stayed
    green through all of it, because every one of those replies is still a dict.
    """
    if not isinstance(payload, dict):
        return f"expected an object, got {type(payload).__name__}"
    count = payload.get("count")
    if isinstance(count, bool) or not isinstance(count, int):
        return f"count is {count!r}, expected an int"
    if count < 0:
        return f"count is {count}, expected 0 or more"
    return None


async def run(findings):
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
                names = sorted(tool.name for tool in (await session.list_tools()).tools)
                unclassified = sorted(set(names) - set(CLASSIFIED))
                vanished = sorted(set(CLASSIFIED) - set(names))
                findings.append(
                    {
                        "name": "roster",
                        "status": "PASS" if not unclassified and not vanished else "FAIL",
                        "detail": (
                            f"{len(names)} tools served, every one classified"
                            if not unclassified and not vanished
                            else f"unclassified {unclassified}; declared but not served {vanished}"
                        ),
                    }
                )
                missing = [tool for tool in MUTATING if tool not in names]
                findings.append(
                    {
                        "name": "mutating present",
                        "status": "PASS" if not missing else "FAIL",
                        "detail": f"declared but deliberately not called: {len(MUTATING)}"
                        if not missing
                        else f"missing from the roster: {missing}",
                    }
                )

                expectations = {
                    "flow_lane": a_lane,
                    "flow_projects": a_project_list,
                    "flow_credits": a_balance,
                    # Called with no project on purpose: that is the path that opens the first project itself.
                    "flow_tools": a_tool_list,
                }
                projects = None
                for name in READ_ONLY_NO_ARGS:
                    status, detail, payload = await call(session, name, {}, expectations[name])
                    findings.append({"name": name, "status": status, "detail": detail})
                    if name == "flow_projects":
                        projects = payload

                project_id = None
                if isinstance(projects, list) and projects and isinstance(projects[0], dict):
                    project_id = projects[0].get("id")
                if not project_id:
                    findings.append(
                        {
                            "name": "project id",
                            "status": "FAIL",
                            "detail": "flow_projects gave no id, cannot exercise the per-project tools",
                        }
                    )
                    return
                findings.append(
                    {"name": "project id", "status": "PASS", "detail": f"exercising against {project_id}"}
                )

                per_project = {
                    "flow_media": media_of(project_id),
                    "flow_characters": a_character_list,
                    "flow_uploads": an_upload_count,
                    "scene_list": a_scene_list,
                }
                for name in READ_ONLY_PER_PROJECT:
                    status, detail, _ = await call(
                        session, name, {"project_id": project_id}, per_project[name]
                    )
                    findings.append({"name": name, "status": status, "detail": detail})

                status, detail, _ = await call(
                    session,
                    "flow_media",
                    {"project_id": project_id, "all_versions": True},
                    versions_of(project_id),
                )
                findings.append({"name": "flow_media all", "status": status, "detail": detail})
        finally:
            serve.cancel()


async def main() -> int:
    findings: list[dict[str, str]] = []
    try:
        await run(findings)
    except Exception as exc:  # noqa: BLE001
        findings.append({"name": "run", "status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"})
    print(f"{'STATUS':7s} {'ROW':18s} DETAIL")
    for finding in findings:
        print(f"{finding['status']:7s} {finding['name']:18s} {finding['detail']}")
    failed = [f for f in findings if f["status"] == "FAIL"]
    print(f"\nrows={len(findings)} pass={len(findings) - len(failed)} fail={len(failed)}")
    return 1 if failed or not findings else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
