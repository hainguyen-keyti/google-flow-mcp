import asyncio
import json
import re
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from video import cli, gen, mcp_server
from video.flow import parsers, reader
from video.session import MIGRATED_ROOT, FlowSession

FIXTURES = Path(__file__).parent / "fixtures" / "rpc"

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


# Every tool with the smallest argument set it accepts. This table is what turns coverage into a property
# of the suite rather than something re-counted by hand: measured 2026-09-14 by wrapping
# ClientSession.call_tool at runtime, only 4 of 28 tools were ever really invoked, while two separate
# grep-based counts of the same thing disagreed in opposite directions.
TOOL_CALLS: dict[str, dict] = {
    "flow_lane": {},
    "flow_projects": {},
    "flow_credits": {},
    "flow_media": {"project_id": "P"},
    "flow_characters": {"project_id": "P"},
    "flow_tools": {},
    "flow_download": {"project_id": "P", "media_id": "M"},
    "flow_upload": {"project_id": "P", "path": "/tmp/a.png"},
    "flow_uploads": {"project_id": "P"},
    "project_create": {},
    "project_rename": {"project_id": "P", "title": "new title"},
    "project_delete": {"project_id": "P"},
    "character_create": {"project_id": "P", "prompt": "a calm face"},
    "character_delete": {"project_id": "P", "entity_id": "E"},
    "scene_list": {"project_id": "P"},
    "scene_create": {"project_id": "P"},
    "scene_delete": {"project_id": "P", "scene_id": "S"},
    "agent_mode": {"project_id": "P", "enabled": True},
    "agent_send": {"project_id": "P", "message": "hello", "job_id": "job-agent"},
    "clip_download": {"project_id": "P", "media_id": "M"},
    "clip_extend": {"project_id": "P", "media_id": "M", "prompt": "keep going", "job_id": "job-extend"},
    "clip_edit": {"project_id": "P", "media_id": "M", "prompt": "change the shirt", "job_id": "job-edit"},
    "clip_reconcile": {"project_id": "P"},
    "gen_t2v": {"prompt": "a boat", "project": "P", "job_id": "job-t2v"},
    "gen_i2v": {"initial_frame": "/tmp/a.png", "prompt": "a boat", "project": "P", "job_id": "job-i2v"},
    "gen_r2v": {"refs": ["/tmp/a.png"], "prompt": "a boat", "project": "P", "job_id": "job-r2v"},
    "gen_t2i": {"prompt": "a boat", "project": "P"},
    "gen_i2i": {"refs": ["/tmp/a.png"], "prompt": "a boat", "project": "P"},
}


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


class _Recorder:
    """Stands in for the whole Backend, recording what each tool forwarded.

    It replaces `mcp_server.backend` outright rather than one method at a time, because the tool bodies
    look `backend` up in module globals at call time (verified 2026-09-14). Nothing here opens a
    FlowSession, so no browser starts, no Flow request goes out, and no credit can move: that matters
    because this file calls gen_*, clip_extend, clip_edit and agent_send, which all spend real money.
    """

    def __init__(self):
        self.calls = []

    def __getattr__(self, method):
        async def capture(*args, **kwargs):
            self.calls.append((method, args, kwargs))
            return {"recorded": method}

        return capture

    def methods(self):
        return [method for method, _, _ in self.calls]


def drive_every_tool(monkeypatch):
    """Call every tool in the table once against a recording backend, and report what happened."""
    recorder = _Recorder()
    monkeypatch.setattr(mcp_server, "backend", recorder)

    async def fn(session):
        outcomes = {}
        for name, arguments in TOOL_CALLS.items():
            result = await session.call_tool(name, dict(arguments))
            outcomes[name] = result
        return outcomes

    return recorder, with_client(fn)


def test_every_tool_can_actually_be_called(monkeypatch):
    # Measured 2026-09-14 by wrapping ClientSession.call_tool at runtime: before this test the suite
    # really invoked only 4 of 28 tools (clip_download, flow_credits, flow_media, gen_t2v). A tool that
    # nobody ever calls is a tool nobody knows is broken.
    recorder, outcomes = drive_every_tool(monkeypatch)

    errored = sorted(name for name, result in outcomes.items() if result.is_error)
    assert errored == [], f"tools returned is_error: {errored}"
    assert sorted(outcomes) == sorted(TOOL_CALLS)
    assert len(recorder.calls) == len(TOOL_CALLS), (
        f"{len(TOOL_CALLS)} tools called but the backend only saw {len(recorder.calls)}: {recorder.methods()}"
    )


def test_every_tool_forwards_its_arguments_to_the_backend(monkeypatch):
    """An argument a tool accepts and then drops is invisible: the call still answers is_error=False.

    Measured 2026-09-13: `flow_media` took an `all_versions` argument it did not implement, discarded it,
    and replied as though nothing had been asked. That was found only because someone went looking. This
    checks all 28 at once.

    The assertion is deliberately loose about HOW a value arrives, because the mapping is not one to one:
    `flow_media("P")` reaches the backend as `media("P", False)` positionally with the default filled in,
    while `gen_t2v` arrives as `generate(kind="t2v", prompt=..., project=...)` in keyword form. Pinning 28
    exact signatures would mostly test my transcription of them. What matters is that no value vanishes.
    """
    recorder, _ = drive_every_tool(monkeypatch)
    # Pairing tool to call relies on both being in table order, which holds only while each tool calls the
    # backend exactly once. Check that here rather than trusting another test, because a double call would
    # shift every later pair and turn this into nonsense.
    assert len(recorder.calls) == len(TOOL_CALLS)

    lost = []
    for tool, (method, args, kwargs) in zip(TOOL_CALLS, recorder.calls, strict=True):
        received = list(args) + list(kwargs.values())
        for name, value in TOOL_CALLS[tool].items():
            if value not in received:
                lost.append(f"{tool}({name}={value!r}) never reached backend.{method}")
    assert lost == [], "arguments swallowed between the tool and the backend:\n" + "\n".join(lost)


def test_the_call_table_covers_every_served_tool():
    # Adding a tool to the server without adding it here fails immediately, so no tool can arrive
    # unexercised. This is the guard that "4 of 28" could exist unnoticed for weeks without.
    served = served_tools()
    assert set(TOOL_CALLS) == served, (
        f"table misses {sorted(served - set(TOOL_CALLS))}; table invents {sorted(set(TOOL_CALLS) - served)}"
    )


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
        versions = {"versions": [{"workflow_id": "w1"}, {"workflow_id": "w2"}]} if all_versions else {}
        return {"meta": {}, "models": [], "media": [], **versions}

    monkeypatch.setattr(mcp_server.backend, "media", fake_media)

    async def fn(session):
        return await session.call_tool("flow_media", {"project_id": "P", "all_versions": True})

    result = with_client(fn)
    assert not result.is_error
    assert called == [("P", True)]
    payload = json.loads("".join(getattr(c, "text", "") for c in result.content))
    assert [row["workflow_id"] for row in payload["versions"]] == ["w1", "w2"]


def test_gen_tool_refuses_a_missing_project(monkeypatch):
    called = []

    async def fake_generate(**kwargs):
        called.append(kwargs)
        return {"job_id": "x", "outputs": []}

    monkeypatch.setattr(mcp_server.backend, "generate", fake_generate)

    async def fn(session):
        return await session.call_tool("gen_t2v", {"prompt": "a boat", "project": "", "job_id": "job-1"})

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
            "gen_t2v",
            {"prompt": "a boat", "project": "P", "model": "veo-lite", "aspect": "9:16", "job_id": "job-1"},
        )

    result = with_client(fn)
    assert not result.is_error
    payload = json.loads("".join(getattr(c, "text", "") for c in result.content))
    assert payload["job_id"] == "job-1"
    assert called[0]["kind"] == "t2v" and called[0]["project"] == "P" and called[0]["aspect"] == "9:16"


def test_the_instructions_name_every_tool_whose_description_says_it_spends_credits():
    # Measured 2026-09-14: the instructions an agent reads at initialize said only "gen_* tools spend Flow
    # credits". That named no tool, left out clip_extend (10), clip_edit (20) and agent_send, and swept in
    # gen_t2i and gen_i2i, which are credit-free. Each tool's own description is the source here.
    # Match the phrases, not the word: clip_reconcile says "spent credits" and "the spend" and is free.
    async def fn(session):
        return {tool.name: tool.description or "" for tool in (await session.list_tools()).tools}

    descriptions = with_client(fn)
    spenders = sorted(
        name for name, text in descriptions.items() if "spends credits" in text or "may spend credits" in text
    )

    assert {"gen_t2v", "clip_extend", "clip_edit", "agent_send"} <= set(spenders), spenders
    assert [name for name in spenders if name not in mcp_server.server.instructions] == []


def test_the_instructions_do_not_tell_an_agent_it_cannot_create_projects():
    # project_create is served and free; "cannot create projects through gflow" dates from the dead labs
    # lane and would steer an agent away from a tool that works.
    assert "project_create" in EXPECTED_TOOLS
    assert "cannot create projects" not in mcp_server.server.instructions


# The owner's roster of tools that move money (DECISIONS 2026-09-15). gen_t2i and gen_i2i are credit-free.
SPENDING_TOOLS = {"gen_t2v", "gen_i2v", "gen_r2v", "clip_extend", "clip_edit", "agent_send"}


def served_tool_objects():
    async def fn(session):
        return {tool.name: tool for tool in (await session.list_tools()).tools}

    return with_client(fn)


def test_every_tool_description_states_its_cost():
    # Measured 2026-09-15: an agent holding nothing but this MCP could not tell what a call costs. Only gen_t2v
    # carried a figure; clip_extend, clip_edit, gen_r2v and agent_send said "spends credits" with no number, and
    # character_create was the one free write tool whose description did not end in "Free.".
    unpriced = []
    for name, tool in served_tool_objects().items():
        text = (tool.description or "").strip()
        if not (text.endswith("Free.") or re.search(r"\b\d+ credits?\b", text) or "unmeasured" in text):
            unpriced.append(name)
        elif text.endswith("Free.") and "spends credits" in text:
            unpriced.append(f"{name} ends in Free. but says it spends credits")
    assert unpriced == []


def test_every_credit_spending_tool_requires_a_job_id():
    # The ledger refuses a job id it already holds (gen.py), but the server minted a fresh uuid whenever the
    # agent left job_id out, so a retry after a slow call (61 to 119 s per generation) paid a second time.
    tools = served_tool_objects()
    required = {name for name, tool in tools.items() if "job_id" in tool.input_schema.get("required", [])}
    optional_on_a_paid_tool = sorted(
        name
        for name, tool in tools.items()
        if "job_id" in tool.input_schema.get("properties", {})
        and name not in required
        and "0 credits" not in (tool.description or "")
    )
    assert sorted(SPENDING_TOOLS - required) == []
    assert optional_on_a_paid_tool == []


def _flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


def _argv_sent_by(monkeypatch, tool, arguments):
    """Run a generate tool through the real Backend up to the gflow argv, with run_job stubbed out."""
    jobs = []

    async def fake_run_job(job, out_dir, **kwargs):
        jobs.append(job)
        return {"job_id": job.job_id, "outputs": []}

    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend())
    monkeypatch.setattr(mcp_server.gen_mod, "run_job", fake_run_job)

    async def fn(session):
        return await session.call_tool(tool, arguments)

    result = with_client(fn)
    assert not result.is_error, [getattr(c, "text", "") for c in result.content]
    return mcp_server.gen_mod.build_argv(jobs[0], Path("out"))


def test_a_video_generation_without_a_model_goes_out_as_omni_flash_for_ten_seconds(monkeypatch):
    # Left empty, gflow lets Flow reuse whatever model the composer used last (cli_video.py:185-196), so what
    # a call costs could not be known before paying. The owner chose omni-flash for 10 s (2026-09-15).
    calls = {
        "gen_t2v": {"prompt": "a boat", "project": "P", "job_id": "job-t2v"},
        "gen_r2v": {"refs": ["/tmp/a.png"], "prompt": "a boat", "project": "P", "job_id": "job-r2v"},
        "gen_i2v": {"initial_frame": "/tmp/a.png", "prompt": "a boat", "project": "P", "job_id": "job-i2v"},
    }
    for tool, arguments in calls.items():
        argv = _argv_sent_by(monkeypatch, tool, arguments)
        assert (_flag(argv, "--model"), _flag(argv, "--duration")) == ("omni-flash", "10"), (tool, argv)


def test_the_video_tools_show_the_agent_that_the_model_defaults_to_omni_flash():
    # The backend fills the model in either way; the schema default is what an agent reads before it pays.
    tools = served_tool_objects()
    defaults = {
        name: tools[name].input_schema["properties"]["model"].get("default")
        for name in ("gen_t2v", "gen_r2v", "gen_i2v")
    }
    assert defaults == {"gen_t2v": "omni-flash", "gen_r2v": "omni-flash", "gen_i2v": "omni-flash"}


def test_a_null_model_is_treated_as_an_omitted_one(monkeypatch):
    argv = _argv_sent_by(
        monkeypatch, "gen_t2v", {"prompt": "a boat", "project": "P", "job_id": "j", "model": None}
    )
    assert (_flag(argv, "--model"), _flag(argv, "--duration")) == ("omni-flash", "10")


def test_a_veo_model_keeps_flows_own_length_because_veo_stops_at_eight_seconds(monkeypatch):
    arguments = {"prompt": "a boat", "project": "P", "job_id": "j", "model": "veo-lite"}
    argv = _argv_sent_by(monkeypatch, "gen_t2v", arguments)
    assert _flag(argv, "--model") == "veo-lite"
    assert "--duration" not in argv


def test_an_explicit_duration_is_sent_as_given(monkeypatch):
    argv = _argv_sent_by(
        monkeypatch, "gen_t2v", {"prompt": "a boat", "project": "P", "job_id": "j", "duration": 8}
    )
    assert (_flag(argv, "--model"), _flag(argv, "--duration")) == ("omni-flash", "8")


def _fixture(rpcid):
    return json.loads((FIXTURES / f"{rpcid}.json").read_text(encoding="utf-8"))["payload"]


def _payload(result):
    assert not result.is_error, [getattr(c, "text", "") for c in result.content]
    return json.loads("".join(getattr(c, "text", "") for c in result.content))


class _OfflineSession:
    """The real Backend and reader run against this: it records where it was sent and opens no browser."""

    def __init__(self):
        self.urls = []

    async def goto(self, url, *, ready=None, timeout_ms=60_000):
        self.urls.append(url)

    project_url = staticmethod(FlowSession.project_url)


def _offline_backend(monkeypatch, frames_for):
    """Swap only the browser out: capture runs the navigation, then answers with recorded rpc payloads."""
    session = _OfflineSession()

    async def fake_capture(s, action, *, settle):
        await action()
        return frames_for(s.urls[-1])

    async def fake_with(self, fn):
        return await fn(session)

    monkeypatch.setattr(reader, "capture", fake_capture)
    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend())
    return session


def test_flow_media_answers_one_object_whether_or_not_all_versions_is_set(monkeypatch):
    # Measured 2026-09-15: all_versions=true turned the reply from an object into a bare list, so code reading
    # payload["media"] worked until the flag was set. The versions ride in the same Zzl0ze payload.
    listing = _fixture("Zzl0ze")
    frames = {"ngNC2": [_fixture("ngNC2")], "yBhWQ": [_fixture("yBhWQ")], "Zzl0ze": [listing]}
    session = _offline_backend(monkeypatch, lambda url: frames)

    async def fn(s):
        plain = await s.call_tool("flow_media", {"project_id": "P"})
        every = await s.call_tool("flow_media", {"project_id": "P", "all_versions": True})
        return _payload(plain), _payload(every)

    plain, every = with_client(fn)
    assert sorted(plain) == ["media", "meta", "models"]
    assert sorted(every) == ["media", "meta", "models", "versions"]
    assert every["media"] == plain["media"]
    assert every["versions"] == json.loads(json.dumps(parsers.records(listing), default=str))
    assert session.urls == [FlowSession.project_url("P")] * 2


def _gallery_account(grid_payload):
    def frames_for(url):
        if url == MIGRATED_ROOT:
            return {"UpteDb": [grid_payload]}
        return {"tRARke": [_fixture("tRARke")]}

    return frames_for


def test_flow_tools_needs_no_project_and_opens_the_first_project_on_the_grid(monkeypatch):
    # Measured 2026-09-15: the grid never fires tRARke and /tools, /applets are 404s; only an open project loads
    # the gallery, and it is the same gallery in every project. An agent should not need an id just for that.
    grid = _fixture("UpteDb")
    session = _offline_backend(monkeypatch, _gallery_account(grid))

    async def fn(s):
        return _payload(await s.call_tool("flow_tools", {}))

    tools = with_client(fn)
    assert [t["id"] for t in tools] == [t["id"] for t in parsers.tools(_fixture("tRARke"))]
    assert session.urls == [MIGRATED_ROOT, FlowSession.project_url(parsers.projects(grid)[0]["id"])]


def test_flow_tools_with_a_project_goes_straight_to_it(monkeypatch):
    session = _offline_backend(monkeypatch, _gallery_account(_fixture("UpteDb")))

    async def fn(s):
        return _payload(await s.call_tool("flow_tools", {"project_id": "P"}))

    assert len(with_client(fn)) == len(parsers.tools(_fixture("tRARke")))
    assert session.urls == [FlowSession.project_url("P")]


def test_flow_tools_on_an_account_without_projects_says_how_to_get_one(monkeypatch):
    _offline_backend(monkeypatch, _gallery_account([[]]))

    async def fn(s):
        return await s.call_tool("flow_tools", {})

    result = with_client(fn)
    assert result.is_error
    assert "project_create" in "".join(getattr(c, "text", "") for c in result.content)


def _error_text_of_a_failing_generation(monkeypatch, exc):
    async def fake_run_job(job, out_dir, **kwargs):
        raise exc

    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend())
    monkeypatch.setattr(mcp_server.gen_mod, "run_job", fake_run_job)

    async def fn(session):
        return await session.call_tool("gen_t2v", {"prompt": "a boat", "project": "P", "job_id": "job-1"})

    result = with_client(fn)
    assert result.is_error
    return "".join(getattr(c, "text", "") for c in result.content)


def test_an_agent_reads_why_a_tool_failed(monkeypatch):
    # Measured 2026-09-15: mcp 2.2.0 shows only "Error executing tool <name>" for any exception that is not a
    # ToolError, so a WAF stop and a refused job_id reached the agent as the very same bare line.
    refused = gen.AlreadySubmitted("job job-1 already has a submitted row; use a new job id")
    assert "already has a submitted row" in _error_text_of_a_failing_generation(monkeypatch, refused)
    waf = RuntimeError("Google Flow flagged this request as unusual activity. Stop: do not retry")
    assert "Stop: do not retry" in _error_text_of_a_failing_generation(monkeypatch, waf)


def test_a_missing_argument_is_named_to_the_agent(monkeypatch):
    monkeypatch.setattr(mcp_server, "backend", _Recorder())

    async def fn(session):
        return await session.call_tool("gen_t2v", {"prompt": "a boat", "project": "", "job_id": "job-1"})

    result = with_client(fn)
    assert result.is_error
    assert "project is required" in "".join(getattr(c, "text", "") for c in result.content)


def test_an_error_shown_to_the_agent_never_carries_a_cookie(monkeypatch):
    leaked = RuntimeError("request failed with SAPISID=abc123secret in the header")
    text = _error_text_of_a_failing_generation(monkeypatch, leaked)
    assert "abc123secret" not in text
    assert "[redacted]" in text


def test_the_instructions_say_calls_are_slow_and_a_retry_must_keep_its_job_id():
    # Measured 2026-09-15: a context-free agent pointed out that nothing warned a call blocks for tens of
    # seconds, which is exactly when an agent retries, and a retry under a fresh job id is a second charge.
    text = mcp_server.server.instructions
    assert re.search(r"\b\d+\s*(?:s|seconds|min|minutes)\b", text), text
    assert "job_id" in text
    assert "omni-flash" in text
