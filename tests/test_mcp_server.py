import asyncio
import json

from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from video import cli, mcp_server

EXPECTED_TOOLS = {
    "flow_lane",
    "flow_projects",
    "flow_credits",
    "flow_media",
    "flow_characters",
    "flow_tools",
    "flow_download",
    "flow_upload",
    "project_create",
    "project_rename",
    "project_delete",
    "character_create",
    "character_delete",
    "gen_t2v",
    "gen_i2v",
    "gen_r2v",
    "gen_t2i",
    "gen_i2i",
    "scene_list",
    "scene_create",
    "scene_delete",
    "agent_mode",
    "agent_send",
    "clip_download",
    "clip_extend",
    "clip_edit",
    "clip_reconcile",
    "flow_uploads",
}


def with_client(fn):
    async def main():
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            client_read, client_write = client_streams
            server_read, server_write = server_streams
            low = mcp_server.server._lowlevel_server
            serve = asyncio.create_task(
                low.run(server_read, server_write, low.create_initialization_options(), raise_exceptions=True)
            )
            try:
                async with ClientSession(client_read, client_write) as session:
                    await session.initialize()
                    return await fn(session)
            finally:
                serve.cancel()

    return asyncio.run(main())


# A CLI command with no MCP tool is unreachable for an agent, which is the whole product. Every entry
# here is a claim that an agent never needs that command, so each one carries the reason it is safe.
EXCLUDED_COMMANDS = {
    "mcp run": "is the MCP server itself; it does not expose itself as a tool",
    "flow character list": "flow_characters already serves it",
}
EXCLUDED_GROUPS = {
    "story": "the video-building layer, out of scope since 2026-09-13 (DECISIONS section 1)",
}


def leaf_commands(group, prefix=""):
    """Every runnable CLI command, read from click itself so this list cannot drift by hand."""
    out = []
    for name, command in sorted(getattr(group, "commands", {}).items()):
        full = f"{prefix}{name}"
        if getattr(command, "commands", None):
            out += leaf_commands(command, full + " ")
        else:
            out.append(full)
    return out


def tool_for(command, tools):
    """`flow scene list` -> `scene_list`; `flow lane` -> `flow_lane`; `gen t2v` -> `gen_t2v`."""
    parts = command.split()
    for candidate in ("_".join(parts[1:]), "_".join(parts)):
        if candidate in tools:
            return candidate
    return None


def served_tools():
    async def fn(session):
        return {t.name for t in (await session.list_tools()).tools}

    return with_client(fn)


def test_the_served_tools_are_exactly_the_declared_roster():
    # Equality, not a subset: a subset check passes a tool that was added without being declared, and
    # the old `len(names) >= 15` floor let 11 of 26 tools vanish unnoticed.
    assert served_tools() == EXPECTED_TOOLS


def test_every_cli_command_an_agent_needs_has_an_mcp_tool():
    """The MCP surface is the product: a command reachable only from a shell is unreachable for an agent.

    This reads the CLI out of click rather than comparing against a second hand-written constant. The
    test that used to carry the name `covers_the_cli_surface` compared against a constant and never
    looked at the CLI at all, which is exactly how two commands went missing with every test green.
    """
    tools = served_tools()
    unreachable = [
        command
        for command in leaf_commands(cli.main)
        if command not in EXCLUDED_COMMANDS
        and command.split()[0] not in EXCLUDED_GROUPS
        and tool_for(command, tools) is None
    ]
    assert unreachable == [], f"no MCP tool for: {unreachable}"


def test_the_exclusion_list_names_only_commands_that_still_exist():
    # An exclusion for a command that no longer exists is a licence nobody remembered to revoke.
    leaves = set(leaf_commands(cli.main))
    assert set(EXCLUDED_COMMANDS) <= leaves
    groups = {command.split()[0] for command in leaves}
    assert set(EXCLUDED_GROUPS) <= groups


def test_flow_credits_tool_returns_the_balance(monkeypatch):
    async def fake_credits(profile="default"):
        return {"balance": 840, "raw": [840, 1, 2, 2, None, 840]}

    monkeypatch.setattr(mcp_server.backend, "credits", fake_credits)

    async def fn(session):
        result = await session.call_tool("flow_credits", {})
        return result

    result = with_client(fn)
    assert not result.is_error
    text = "".join(getattr(c, "text", "") for c in result.content)
    assert "840" in text
    assert "SAPISID" not in text


def test_flow_media_can_ask_for_every_version(monkeypatch):
    # An Omni edit stacks a new version onto the SAME media id, so an agent that cannot list versions
    # cannot find the clip it just paid for. `flow media --all` has always reached reader.records; the
    # tool reached only reader.project, which is how this had to be done in raw Python on 2026-09-13.
    called = []

    async def fake_media(project_id, all_versions=False):
        called.append((project_id, all_versions))
        return [{"workflow_id": "w1"}, {"workflow_id": "w2"}] if all_versions else {"media": []}

    monkeypatch.setattr(mcp_server.backend, "media", fake_media)

    async def fn(session):
        return await session.call_tool("flow_media", {"project_id": "P", "all_versions": True})

    result = with_client(fn)
    assert not result.is_error
    assert called == [("P", True)]
    payload = json.loads("".join(getattr(c, "text", "") for c in result.content))
    assert [row["workflow_id"] for row in payload] == ["w1", "w2"]


def test_gen_tool_refuses_a_missing_project(monkeypatch):
    called = []

    async def fake_generate(**kwargs):
        called.append(kwargs)
        return {"job_id": "x", "outputs": []}

    monkeypatch.setattr(mcp_server.backend, "generate", fake_generate)

    async def fn(session):
        return await session.call_tool("gen_t2v", {"prompt": "a boat", "project": ""})

    result = with_client(fn)
    assert result.is_error
    assert called == []


def test_clip_download_rejects_an_unknown_quality_before_opening_a_browser(monkeypatch):
    called = []

    async def fake_clip_download(*args):
        called.append(args)
        return "out/x.mp4"

    monkeypatch.setattr(mcp_server.backend, "clip_download", fake_clip_download)

    async def fn(session):
        return await session.call_tool("clip_download", {"project_id": "P", "media_id": "M", "quality": "8k"})

    result = with_client(fn)
    assert result.is_error
    assert called == []


def test_gen_tool_forwards_job_parameters(monkeypatch):
    called = []

    async def fake_generate(**kwargs):
        called.append(kwargs)
        return {"job_id": "job-1", "outputs": [{"media_id": "m", "path": "out/m.mp4"}]}

    monkeypatch.setattr(mcp_server.backend, "generate", fake_generate)

    async def fn(session):
        return await session.call_tool(
            "gen_t2v", {"prompt": "a boat", "project": "P", "model": "veo-lite", "aspect": "9:16"}
        )

    result = with_client(fn)
    assert not result.is_error
    payload = json.loads("".join(getattr(c, "text", "") for c in result.content))
    assert payload["job_id"] == "job-1"
    assert called[0]["kind"] == "t2v" and called[0]["project"] == "P" and called[0]["aspect"] == "9:16"
