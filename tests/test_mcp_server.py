import asyncio
import json

from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from video import mcp_server

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


def test_tools_list_covers_the_cli_surface():
    async def fn(session):
        return {t.name for t in (await session.list_tools()).tools}

    names = with_client(fn)
    assert EXPECTED_TOOLS <= names
    assert len(names) >= 15


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
