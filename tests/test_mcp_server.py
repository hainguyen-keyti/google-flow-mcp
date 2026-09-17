import asyncio
import importlib.util
import json
import re
import threading
from pathlib import Path

import pytest
from gflow_cli.api.transports.migrated_composer import R2V_DURATION_S
from mcp.client.session import ClientSession
from mcp.shared.exceptions import MCPError
from mcp.shared.memory import create_client_server_memory_streams

from video import cli, gen, mcp_server
from video import session as session_mod
from video.flow import ingredients, parsers, reader
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
    # Plan character-generation T5: one tool added on purpose, so this roster grows from 32 to 33.
    "gen_character",
    "scene_list",
    "scene_create",
    "scene_delete",
    "scene_restore",
    # Plan scene-timeline-tools T4: three tools added on purpose, so this roster grows from 29 to 32.
    "scene_add_clip",
    "scene_download",
    "scene_rename",
    # Plan character-generation T10: the listing read that lets an agent name a clip on a timeline, the aspect ratio a
    # film is exported in, and taking a clip off or moving it, added on purpose, so this roster grows from 33 to 37.
    "scene_clips",
    "scene_set_aspect",
    "scene_remove_clip",
    "scene_move_clip",
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
    "scene_restore": {"project_id": "P", "scene_id": "S-trashed"},
    "scene_add_clip": {"project_id": "P", "scene_id": "S", "media_id": "M"},
    "scene_download": {"project_id": "P", "scene_id": "S", "out_dir": "out/films"},
    "scene_rename": {"project_id": "P", "scene_id": "S", "title": "a name"},
    "scene_clips": {"project_id": "P", "scene_id": "S"},
    "scene_set_aspect": {"project_id": "P", "scene_id": "S", "aspect": "9:16"},
    "scene_remove_clip": {"project_id": "P", "scene_id": "S", "clip_id": "C"},
    "scene_move_clip": {"project_id": "P", "scene_id": "S", "clip_id": "C", "position": 2},
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
    "gen_character": {"prompt": "a boat", "project": "P", "characters": ["E"], "job_id": "job-character"},
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
SPENDING_TOOLS = {"gen_t2v", "gen_i2v", "gen_r2v", "clip_extend", "clip_edit", "agent_send", "gen_character"}
# A tool whose dry_run quotes for free needs no job_id to quote (plan character-generation, DECISIONS 2026-09-16); its
# real run is held to the job_id rule by test_gen_character_refuses_a_real_run_without_a_job_id_before_a_browser_opens.
DRY_RUN_QUOTES = {"gen_character"}


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
        and name not in DRY_RUN_QUOTES
        and "0 credits" not in (tool.description or "")
    )
    assert sorted(SPENDING_TOOLS - required - DRY_RUN_QUOTES) == []
    assert optional_on_a_paid_tool == []
    for name in sorted(DRY_RUN_QUOTES):
        assert "dry_run" in tools[name].input_schema["properties"], name


def test_every_spending_description_keeps_the_same_job_id_for_a_call_still_running():
    # Re-review 2026-09-15: the shared job_id rule put "still running in another call" in the sentence ending "check
    # flow_media and flow_credits before starting it under a new one", which no check can confirm while a job is in
    # flight, so an agent following the description paid twice.
    tools = served_tool_objects()
    for name in sorted(SPENDING_TOOLS):
        sentences = re.split(r"(?<=\.)\s+", tools[name].description or "")
        running = [sentence for sentence in sentences if "still running" in sentence]
        assert running, name
        for sentence in running:
            assert "SAME job_id" in sentence, (name, sentence)
            assert "flow_" not in sentence and "under a new one" not in sentence, (name, sentence)
            assert "new" not in sentence.replace("never a new one", ""), (name, sentence)


def _flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


def _argv_sent_by(monkeypatch, tmp_path, tool, arguments):
    """Run a generate tool through the real Backend up to the gflow argv, with run_job stubbed out."""
    jobs = []

    async def fake_run_job(job, out_dir, **kwargs):
        jobs.append(job)
        return {"job_id": job.job_id, "outputs": []}

    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
    monkeypatch.setattr(mcp_server.gen_mod, "run_job", fake_run_job)

    async def fn(session):
        return await session.call_tool(tool, arguments)

    result = with_client(fn)
    assert not result.is_error, [getattr(c, "text", "") for c in result.content]
    return mcp_server.gen_mod.build_argv(jobs[0], Path("out"))


def test_t2v_and_i2v_without_a_model_go_out_as_omni_flash_for_ten_seconds(monkeypatch, tmp_path):
    # Left empty, gflow lets Flow reuse whatever model the composer used last (cli_video.py:185-196), so what
    # a call costs could not be known before paying. The owner chose omni-flash for 10 s (2026-09-15).
    calls = {
        "gen_t2v": {"prompt": "a boat", "project": "P", "job_id": "job-t2v"},
        "gen_i2v": {"initial_frame": "/tmp/a.png", "prompt": "a boat", "project": "P", "job_id": "job-i2v"},
    }
    for tool, arguments in calls.items():
        argv = _argv_sent_by(monkeypatch, tmp_path, tool, arguments)
        assert (_flag(argv, "--model"), _flag(argv, "--duration")) == ("omni-flash", "10"), (tool, argv)


def test_r2v_without_a_model_goes_out_as_omni_flash_and_leaves_its_only_length_to_gflow(
    monkeypatch, tmp_path
):
    # gflow refuses r2v on this host at any length but R2V_DURATION_S (migrated_composer.py:201, 968-983) and pins
    # that length itself when none is sent, even on a cohort with no duration row (DECISIONS 2026-09-15).
    r2v = {"refs": ["/tmp/a.png"], "prompt": "a boat", "project": "P", "job_id": "job-r2v"}
    argv = _argv_sent_by(monkeypatch, tmp_path, "gen_r2v", r2v)
    assert _flag(argv, "--model") == "omni-flash"
    assert "--duration" not in argv
    # Sent explicitly, the length goes through gflow's duration click, which raises exit 11 on a cohort with no
    # duration row (migrated_composer.py:1171-1173) after the ledger holds `submitted`: an 8 from the agent is dropped.
    argv = _argv_sent_by(monkeypatch, tmp_path, "gen_r2v", r2v | {"duration": R2V_DURATION_S})
    assert "--duration" not in argv


@pytest.mark.parametrize(
    ("model", "refs"), [("omni-flash", 8), (None, 8), ("veo-lite", 4), ("veo-quality", 1)]
)
def test_r2v_with_more_references_than_the_model_takes_is_refused_before_the_ledger(
    monkeypatch, tmp_path, model, refs
):
    # Re-review 2026-09-15: gflow checks reference_cap_for only after run_job has written `submitted`, so one image
    # too many burned the job_id; Flow itself keeps only the first images of a larger set.
    arguments = SPEND_CALLS["gen_r2v"] | {"model": model, "refs": [f"/tmp/{i}.png" for i in range(refs)]}
    text = _refused_before_the_ledger(monkeypatch, tmp_path, "gen_r2v", arguments)
    assert "reference images" in text, text


def test_r2v_at_exactly_the_models_reference_cap_still_runs(monkeypatch, tmp_path):
    arguments = SPEND_CALLS["gen_r2v"] | {"refs": [f"/tmp/{i}.png" for i in range(7)]}
    argv = _argv_sent_by(monkeypatch, tmp_path, "gen_r2v", arguments)
    assert argv.count("--ref") == 7


def _refused_before_the_ledger(monkeypatch, tmp_path, tool, arguments):
    ran = []

    async def fake_run_job(job, out_dir, **kwargs):
        ran.append(job)
        return {"job_id": job.job_id, "outputs": []}

    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
    monkeypatch.setattr(mcp_server.gen_mod, "run_job", fake_run_job)

    async def fn(session):
        return await session.call_tool(tool, arguments)

    result = with_client(fn)
    assert result.is_error
    assert ran == []
    return "".join(getattr(c, "text", "") for c in result.content)


def test_a_model_or_length_gflow_would_refuse_is_refused_before_the_ledger_is_touched(monkeypatch, tmp_path):
    # Review 2026-09-15: gflow's --model is an exact click.Choice and it checks lengths only after run_job has
    # written `submitted`, so "omni" burned the job_id for 0 credits, and duration 0 sent no --duration at all,
    # which let Flow fall back on the composer's remembered length.
    t2v = {"prompt": "a boat", "project": "P", "job_id": "job-1"}
    assert "model must be one of" in _refused_before_the_ledger(
        monkeypatch, tmp_path, "gen_t2v", t2v | {"model": "omni"}
    )
    assert "4/6/8/10" in _refused_before_the_ledger(monkeypatch, tmp_path, "gen_t2v", t2v | {"duration": 0})
    veo_ten = t2v | {"model": "veo-lite", "duration": 10}
    assert "caps at 8s" in _refused_before_the_ledger(monkeypatch, tmp_path, "gen_t2v", veo_ten)
    r2v_ten = {"refs": ["/tmp/a.png"], "prompt": "a boat", "project": "P", "job_id": "job-2", "duration": 10}
    assert f"only at {R2V_DURATION_S} s" in _refused_before_the_ledger(
        monkeypatch, tmp_path, "gen_r2v", r2v_ten
    )
    # Scoped re-review 2026-09-15: count 0 sent no --count, so a call asking for nothing paid for one clip.
    assert "count must be 1-4" in _refused_before_the_ledger(
        monkeypatch, tmp_path, "gen_t2v", t2v | {"count": 0}
    )
    assert "count must be 1-4" in _refused_before_the_ledger(
        monkeypatch, tmp_path, "gen_t2v", t2v | {"count": 5}
    )
    assert "aspect must be one of" in _refused_before_the_ledger(
        monkeypatch, tmp_path, "gen_t2v", t2v | {"aspect": "1:1"}
    )
    assert not (tmp_path / "ledger.jsonl").exists()


def test_the_video_tools_show_the_agent_that_the_model_defaults_to_omni_flash():
    # The backend fills the model in either way; the schema default is what an agent reads before it pays.
    tools = served_tool_objects()
    defaults = {
        name: tools[name].input_schema["properties"]["model"].get("default")
        for name in ("gen_t2v", "gen_r2v", "gen_i2v")
    }
    assert defaults == {"gen_t2v": "omni-flash", "gen_r2v": "omni-flash", "gen_i2v": "omni-flash"}


def test_a_null_model_is_treated_as_an_omitted_one(monkeypatch, tmp_path):
    argv = _argv_sent_by(
        monkeypatch, tmp_path, "gen_t2v", {"prompt": "a boat", "project": "P", "job_id": "j", "model": None}
    )
    assert (_flag(argv, "--model"), _flag(argv, "--duration")) == ("omni-flash", "10")


def test_a_veo_model_keeps_flows_own_length_because_veo_stops_at_eight_seconds(monkeypatch, tmp_path):
    arguments = {"prompt": "a boat", "project": "P", "job_id": "j", "model": "veo-lite"}
    argv = _argv_sent_by(monkeypatch, tmp_path, "gen_t2v", arguments)
    assert _flag(argv, "--model") == "veo-lite"
    assert "--duration" not in argv


def test_an_explicit_duration_is_sent_as_given(monkeypatch, tmp_path):
    argv = _argv_sent_by(
        monkeypatch, tmp_path, "gen_t2v", {"prompt": "a boat", "project": "P", "job_id": "j", "duration": 8}
    )
    assert (_flag(argv, "--model"), _flag(argv, "--duration")) == ("omni-flash", "8")


SPEND_CALLS = {
    "gen_t2v": {"prompt": "a boat", "project": "P", "job_id": "job-t2v"},
    "gen_i2v": {"initial_frame": "/tmp/a.png", "prompt": "a boat", "project": "P", "job_id": "job-i2v"},
    "gen_r2v": {"refs": ["/tmp/a.png"], "prompt": "a boat", "project": "P", "job_id": "job-r2v"},
    "clip_extend": {"project_id": "P", "media_id": "M", "prompt": "keep going", "job_id": "job-extend"},
    "clip_edit": {"project_id": "P", "media_id": "M", "prompt": "change the shirt", "job_id": "job-edit"},
    "agent_send": {"project_id": "P", "message": "hello", "job_id": "job-agent"},
    "gen_character": {"prompt": "a boat", "project": "P", "characters": ["E"], "job_id": "job-character"},
}


def _spending_backend(monkeypatch, tmp_path):
    """The real Backend with every spending driver stubbed: records what got past it, opens no browser."""
    reached = []

    async def fake_with(self, fn):
        reached.append("browser")
        return await fn(object())

    async def fake_extend(session, project_id, media_id, prompt, *, out_dir, job_id=None, wait=240.0):
        reached.append(("clip_extend", job_id))
        return {"job_id": job_id}

    async def fake_edit(session, project_id, media_id, prompt, *, out_dir, job_id=None, wait=240.0):
        reached.append(("clip_edit", job_id))
        return {"job_id": job_id}

    async def fake_send(session, project_id, message, wait=60.0, *, out_dir=None, job_id=None):
        reached.append(("agent_send", job_id))
        return {"job_id": job_id}

    async def fake_run_job(job, out_dir, **kwargs):
        reached.append((f"gen_{job.kind}", job.job_id))
        return {"job_id": job.job_id, "outputs": []}

    async def fake_character(session, project_id, **kwargs):
        reached.append(("gen_character", kwargs.get("job_id")))
        return {"project": project_id, **kwargs}

    monkeypatch.setattr(ingredients, "generate", fake_character)
    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.clips_mod, "extend", fake_extend)
    monkeypatch.setattr(mcp_server.clips_mod, "edit", fake_edit)
    monkeypatch.setattr(mcp_server.agent_mod, "send", fake_send)
    monkeypatch.setattr(mcp_server.gen_mod, "run_job", fake_run_job)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
    return reached


def _texts(results):
    return ["".join(getattr(c, "text", "") for c in result.content) for result in results]


def test_every_spending_call_reaches_its_driver_with_the_agents_own_job_id(monkeypatch, tmp_path):
    # Review 2026-09-15: with Backend dropping job_id on its way to the drivers, which then mint a uuid, the suite
    # stayed green. The schema requiring job_id means nothing if the id never reaches the ledger check.
    reached = _spending_backend(monkeypatch, tmp_path)

    async def fn(s):
        return [await s.call_tool(name, dict(arguments)) for name, arguments in SPEND_CALLS.items()]

    results = with_client(fn)
    assert [result.is_error for result in results] == [False] * len(SPEND_CALLS), _texts(results)
    assert sorted(step for step in reached if step != "browser") == sorted(
        (name, arguments["job_id"]) for name, arguments in SPEND_CALLS.items()
    )


def test_a_job_id_already_in_a_ledger_is_refused_before_a_browser_opens(monkeypatch, tmp_path):
    # Any row counts, `opening` included: the clip editor can spend 20 credits before it writes `submitted`
    # (DECISIONS 2026-09-13 and 2026-09-15). Both the default ledger and the one named by out_dir are checked.
    reached = _spending_backend(monkeypatch, tmp_path)
    gen.Ledger(tmp_path / "ledger.jsonl").append("job-extend", "opening", kind="extend", credits_before=245)
    other = tmp_path / "other"
    gen.Ledger(other / "ledger.jsonl").append("job-edit", "submitted", kind="edit")

    async def fn(s):
        return [
            await s.call_tool("clip_extend", dict(SPEND_CALLS["clip_extend"])),
            await s.call_tool("clip_extend", SPEND_CALLS["clip_extend"] | {"out_dir": str(other)}),
            await s.call_tool("clip_edit", SPEND_CALLS["clip_edit"] | {"out_dir": str(other)}),
        ]

    results = with_client(fn)
    assert [result.is_error for result in results] == [True, True, True]
    for text in _texts(results):
        assert "may already have spent credits" in text and "flow_credits" in text, text
        assert "use a new job id" not in text
    assert reached == []


def test_a_job_id_in_any_ledger_under_the_out_folder_is_refused_before_a_browser_opens(monkeypatch, tmp_path):
    # Re-review 2026-09-15: only the default ledger and the one out_dir named were read, so the same job_id sent with
    # another out_dir, or already spent by a run that wrote elsewhere under out, was not seen (DECISIONS 2026-09-15).
    reached = _spending_backend(monkeypatch, tmp_path)
    gen.Ledger(tmp_path / "story2" / "ledger.jsonl").append("job-edit", "done", kind="edit", spent=20)
    gen.Ledger(tmp_path / "deep" / "run" / "ledger.jsonl").append("job-t2v", "submitted", kind="t2v")

    async def fn(s):
        return [
            await s.call_tool("clip_edit", SPEND_CALLS["clip_edit"] | {"out_dir": str(tmp_path / "fresh")}),
            await s.call_tool("gen_t2v", dict(SPEND_CALLS["gen_t2v"])),
        ]

    results = with_client(fn)
    assert [result.is_error for result in results] == [True, True]
    texts = _texts(results)
    assert "may already have spent credits" in texts[0] and "story2" in texts[0], texts[0]
    assert "may already have spent credits" in texts[1] and "deep" in texts[1], texts[1]
    assert reached == []


@pytest.mark.parametrize("tool", sorted(SPEND_CALLS))
def test_a_job_id_the_ledger_scrub_would_change_is_refused_before_a_browser_opens(
    monkeypatch, tmp_path, tool
):
    # Review of plan B (HANDOFF ngã rẽ 5): "__Secure- x" was written as "[redacted] x", so the ledger check never found
    # that id again and a retry with it ran the driver twice (DECISIONS 2026-09-16).
    reached = _spending_backend(monkeypatch, tmp_path)

    async def fn(s):
        return await s.call_tool(tool, SPEND_CALLS[tool] | {"job_id": "__Secure- x"})

    result = with_client(fn)
    text = _texts([result])[0]
    assert result.is_error and "job_id" in text, text
    assert reached == []


@pytest.mark.parametrize("tool", ["clip_extend", "clip_edit"])
@pytest.mark.parametrize("outside", ["elsewhere", "out/../elsewhere"])
def test_an_editor_out_dir_outside_the_out_folder_is_refused_before_a_browser_opens(
    monkeypatch, tmp_path, tool, outside
):
    # DECISIONS 2026-09-15 (plan B): a job_id is looked up only in ledgers under the out folder, so an editor job may
    # only write its ledger inside it (CLAUDE.md rule 5).
    reached = _spending_backend(monkeypatch, tmp_path / "out")

    async def fn(s):
        return await s.call_tool(tool, SPEND_CALLS[tool] | {"out_dir": str(tmp_path / outside)})

    text = _texts([with_client(fn)])[0]
    assert "out_dir must be inside" in text, text
    assert reached == []


def test_a_second_call_with_the_same_job_id_is_refused_while_the_first_still_runs(monkeypatch, tmp_path):
    # Re-review 2026-09-15: the ledger check and the spend are two steps with awaits between, so two calls with one
    # job_id both passed the check before either had written a row.
    gate = {}

    async def slow_run_job(job, out_dir, **kwargs):
        gate["started"].set()
        await gate["release"].wait()
        return {"job_id": job.job_id, "outputs": []}

    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
    monkeypatch.setattr(mcp_server.gen_mod, "run_job", slow_run_job)

    # A long id, so the guidance has to come before it to survive the 500-character cut on errors.
    call = SPEND_CALLS["gen_t2v"] | {"job_id": "job-" + "x" * 600}

    async def fn(s):
        gate["started"], gate["release"] = asyncio.Event(), asyncio.Event()
        first = asyncio.create_task(s.call_tool("gen_t2v", dict(call)))
        await asyncio.wait_for(gate["started"].wait(), 5)
        try:
            second = await asyncio.wait_for(s.call_tool("gen_t2v", dict(call)), 5)
            # A refused call must not clear the marker of the call that is still running.
            again = await asyncio.wait_for(s.call_tool("gen_t2v", dict(call)), 5)
        finally:
            gate["release"].set()
        done = await first
        # Nothing was written, so once the first call is over the id is free again rather than stuck.
        third = await asyncio.wait_for(s.call_tool("gen_t2v", dict(call)), 5)
        return done, second, again, third

    done, second, again, third = with_client(fn)
    assert not done.is_error, _texts([done])
    for refused in (second, again):
        text = _texts([refused])[0]
        # Review 2026-09-15: "start it under a new job_id only if it did not run" cannot be checked while the job is
        # still in flight (flow_media and flow_credits wait or see nothing yet), and following it pays twice.
        assert refused.is_error and "still running" in text and "SAME job_id" in text, text
        assert "never start it under a new job_id" in text and "tell the owner" in text, text
        assert "flow_media" not in text and "new job_id only if" not in text, text
        assert "new job_id" not in text.replace("never start it under a new job_id", ""), text
    assert not third.is_error, _texts([third])


def test_a_job_id_is_free_again_after_its_call_was_cancelled_without_writing_anything(monkeypatch, tmp_path):
    # Re-review 2026-09-15: a client read timeout cancels the handler; the marker has to go then too, or every retry of
    # an id that spent nothing is told it is still running until the server restarts.
    calls = []
    gate = {}

    async def blocked_once(job, out_dir, **kwargs):
        calls.append(job.job_id)
        if len(calls) == 1:
            try:
                await asyncio.Event().wait()
            finally:
                gate["cancelled"].set()
        return {"job_id": job.job_id, "outputs": []}

    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
    monkeypatch.setattr(mcp_server.gen_mod, "run_job", blocked_once)

    async def fn(s):
        gate["cancelled"] = asyncio.Event()
        with pytest.raises(MCPError, match="timed out"):
            await s.call_tool("gen_t2v", dict(SPEND_CALLS["gen_t2v"]), read_timeout_seconds=0.3)
        await asyncio.wait_for(gate["cancelled"].wait(), 5)
        await asyncio.sleep(0.05)
        return await asyncio.wait_for(s.call_tool("gen_t2v", dict(SPEND_CALLS["gen_t2v"])), 5)

    retry = with_client(fn)
    assert not retry.is_error, _texts([retry])
    assert calls == ["job-t2v", "job-t2v"]


@pytest.mark.parametrize(
    "path",
    [
        "out/ledger.jsonl",
        "out/s3/ledger.jsonl",
        "out/clip.mp4",
        # Narrow re-review of plan B (DECISIONS 2026-09-15 debt, plan D T6): only the last part was compared, case and all.
        "out/x/LEDGER.JSONL",
        "out/y/ledger.jsonl/run",
        "out/clip.mp4/x",
        # Review of plan D (2026-09-16, finding 3): the resolved path was checked but the unresolved one was returned,
        # so the driver still made a folder named ledger.jsonl and every later spend failed reading it.
        "out/ledger.jsonl/..",
        "out/x/LEDGER.JSONL/..",
    ],
)
def test_an_editor_out_dir_that_is_a_ledger_or_a_file_is_refused_before_a_browser_opens(
    monkeypatch, tmp_path, path
):
    # Re-review 2026-09-15: an out_dir copied from the "ledger" path clip_reconcile returns made the driver create a
    # folder named ledger.jsonl, after which every spend reading that path failed with IsADirectoryError.
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "clip.mp4").write_bytes(b"")
    reached = _spending_backend(monkeypatch, tmp_path / "out")

    async def fn(s):
        return await s.call_tool("clip_edit", SPEND_CALLS["clip_edit"] | {"out_dir": str(tmp_path / path)})

    text = _texts([with_client(fn)])[0]
    assert "out_dir must be a folder" in text, text
    assert reached == []


@pytest.mark.parametrize("path", ["out/ledgers/run", "out/Ledger-notes/x"])
def test_an_editor_out_dir_that_only_mentions_a_ledger_is_still_accepted(monkeypatch, tmp_path, path):
    # Plan D T6: the stricter check looks at whole path parts, so a folder whose name merely contains "ledger" still works.
    (tmp_path / "out").mkdir()
    reached = _spending_backend(monkeypatch, tmp_path / "out")

    async def fn(s):
        return await s.call_tool("clip_edit", SPEND_CALLS["clip_edit"] | {"out_dir": str(tmp_path / path)})

    result = with_client(fn)
    assert not result.is_error, _texts([result])
    assert [step for step in reached if step != "browser"] == [("clip_edit", "job-edit")]


def test_a_job_id_in_a_ledger_file_named_in_another_case_is_refused_before_a_browser_opens(
    monkeypatch, tmp_path
):
    # Narrow re-review of plan B (DECISIONS 2026-09-15 debt): the scan used rglob("ledger.jsonl"), which misses
    # Ledger.jsonl even where the disk does not tell the two names apart.
    reached = _spending_backend(monkeypatch, tmp_path)
    gen.Ledger(tmp_path / "deep" / "Ledger.jsonl").append("job-t2v", "submitted", kind="t2v")

    async def fn(s):
        return await s.call_tool("gen_t2v", dict(SPEND_CALLS["gen_t2v"]))

    result = with_client(fn)
    text = _texts([result])[0]
    assert result.is_error and "may already have spent credits" in text, text
    assert reached == []


def test_a_job_id_is_free_again_after_its_call_failed_without_writing_anything(monkeypatch, tmp_path):
    # The running marker has to go on every exit: an error that wrote no row leaves nothing spent to protect.
    calls = []

    async def failing_then_fine(job, out_dir, **kwargs):
        calls.append(job.job_id)
        if len(calls) == 1:
            raise RuntimeError("gflow never started")
        return {"job_id": job.job_id, "outputs": []}

    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
    monkeypatch.setattr(mcp_server.gen_mod, "run_job", failing_then_fine)

    async def fn(s):
        return [await s.call_tool("gen_t2v", dict(SPEND_CALLS["gen_t2v"])) for _ in range(2)]

    first, second = with_client(fn)
    assert first.is_error and "gflow never started" in _texts([first])[0], _texts([first])
    assert not second.is_error, _texts([second])
    assert calls == ["job-t2v", "job-t2v"]


def test_editor_calls_inside_a_relative_out_folder_are_accepted(monkeypatch, tmp_path):
    # Review 2026-09-15: the real server keeps Path("out") relative to its working directory; comparing a resolved
    # out_dir with that folder unresolved would refuse every editor call, while test tmp paths are already absolute.
    monkeypatch.chdir(tmp_path)
    reached = _spending_backend(monkeypatch, Path("out"))
    inside = str(tmp_path / "out" / "run-2")

    async def fn(s):
        return [
            await s.call_tool("clip_edit", dict(SPEND_CALLS["clip_edit"])),
            await s.call_tool("clip_extend", SPEND_CALLS["clip_extend"] | {"out_dir": "out/run-1"}),
            await s.call_tool(
                "clip_edit", SPEND_CALLS["clip_edit"] | {"job_id": "job-edit-2", "out_dir": inside}
            ),
        ]

    results = with_client(fn)
    assert [result.is_error for result in results] == [False, False, False], _texts(results)
    assert [step for step in reached if step != "browser"] == [
        ("clip_edit", "job-edit"),
        ("clip_extend", "job-extend"),
        ("clip_edit", "job-edit-2"),
    ]


def test_a_folder_named_ledger_jsonl_under_out_does_not_block_every_spend(monkeypatch, tmp_path):
    # Review 2026-09-15: clip_edit with an out_dir ending in "ledger.jsonl" makes Ledger.append create a folder of that
    # name, and the scan read it as a ledger, so every later spend failed with IsADirectoryError.
    reached = _spending_backend(monkeypatch, tmp_path)
    nested = tmp_path / "story3" / "ledger.jsonl" / "ledger.jsonl"
    gen.Ledger(nested).append("other-job", "done", kind="edit", spent=20)

    async def fn(s):
        return await s.call_tool("gen_t2v", dict(SPEND_CALLS["gen_t2v"]))

    result = with_client(fn)
    assert not result.is_error, _texts([result])
    assert ("gen_t2v", "job-t2v") in reached


def test_generation_and_agent_calls_check_the_ledger_before_anything_runs(monkeypatch, tmp_path):
    # Scoped re-review 2026-09-15: only the clip tools were pinned, so generate or agent_send skipping the check
    # left every test green.
    reached = _spending_backend(monkeypatch, tmp_path)
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("job-t2v", "submitted", kind="t2v")
    ledger.append("job-agent", "done", kind="agent", spent=0)

    async def fn(s):
        return [
            await s.call_tool("gen_t2v", dict(SPEND_CALLS["gen_t2v"])),
            await s.call_tool("agent_send", dict(SPEND_CALLS["agent_send"])),
        ]

    results = with_client(fn)
    assert [result.is_error for result in results] == [True, True]
    assert all("may already have spent credits" in text for text in _texts(results)), _texts(results)
    assert reached == []


def test_the_refusal_guidance_survives_a_long_ledger_path(monkeypatch, tmp_path):
    # Errors reaching the agent are cut at 500 characters, so the guidance has to come before the path.
    deep = tmp_path.joinpath(*["very-long-directory-name-" * 4] * 4)
    assert len(str(deep)) > 400
    reached = _spending_backend(monkeypatch, tmp_path)
    gen.Ledger(deep / "ledger.jsonl").append("job-edit", "opening", kind="edit")

    async def fn(s):
        return await s.call_tool("clip_edit", SPEND_CALLS["clip_edit"] | {"out_dir": str(deep)})

    text = _texts([with_client(fn)])[0]
    assert "check flow_media and flow_credits" in text and "only if it did not run" in text, text
    assert reached == []


def test_a_refusal_from_inside_a_driver_says_check_the_money_rather_than_pay_again(monkeypatch, tmp_path):
    # The drivers' own refusal ends "use a new job id"; passed on verbatim it told the agent to pay a second time.
    refused = gen.AlreadySubmitted("job job-1 already has a submitted row; use a new job id")
    text = _error_text_of_a_failing_generation(monkeypatch, tmp_path, refused)
    assert "already has a submitted row" in text
    assert "may already have spent credits" in text
    assert "use a new job id" not in text


@pytest.mark.parametrize(
    ("tool", "field"), [("gen_t2v", "prompt"), ("clip_edit", "prompt"), ("agent_send", "message")]
)
def test_a_prompt_of_only_spaces_is_refused_before_anything_runs(monkeypatch, tmp_path, tool, field):
    # Re-review 2026-09-15: only an empty string was refused, so "   " reached gflow or the editor after the ledger
    # already held the job, burning the job_id for nothing.
    reached = _spending_backend(monkeypatch, tmp_path)

    async def fn(s):
        return await s.call_tool(tool, SPEND_CALLS[tool] | {field: "   "})

    text = _texts([with_client(fn)])[0]
    assert f"{field} is required" in text, text
    assert reached == []


def test_an_empty_job_id_is_refused_with_the_reason(monkeypatch, tmp_path):
    reached = _spending_backend(monkeypatch, tmp_path)

    async def fn(s):
        return await s.call_tool("gen_t2v", SPEND_CALLS["gen_t2v"] | {"job_id": ""})

    result = with_client(fn)
    assert result.is_error
    assert "job_id is required" in _texts([result])[0]
    assert reached == []


def test_a_multiline_prompt_is_refused_before_it_can_be_typed_as_enter(monkeypatch, tmp_path):
    # Review 2026-09-15: keyboard.type presses Enter for a newline, and the clip editor and the agent box are typed
    # into before the `submitted` row is written.
    reached = _spending_backend(monkeypatch, tmp_path)

    async def fn(s):
        return [
            await s.call_tool("clip_edit", SPEND_CALLS["clip_edit"] | {"prompt": "red shirt\nstill camera"}),
            await s.call_tool("clip_extend", SPEND_CALLS["clip_extend"] | {"prompt": "keep going\r"}),
            await s.call_tool("agent_send", SPEND_CALLS["agent_send"] | {"message": "hi\nthere"}),
        ]

    results = with_client(fn)
    assert [result.is_error for result in results] == [True, True, True]
    assert all("one line" in text for text in _texts(results)), _texts(results)
    assert reached == []


def test_gen_character_refuses_a_real_run_without_a_job_id_before_a_browser_opens(monkeypatch, tmp_path):
    # Its job_id is optional only because a dry_run quotes for free (DECISIONS 2026-09-16); a real run is refused
    # without one, exactly like every other tool that spends.
    reached = _spending_backend(monkeypatch, tmp_path)
    arguments = {key: value for key, value in SPEND_CALLS["gen_character"].items() if key != "job_id"}

    async def fn(s):
        return [
            await s.call_tool("gen_character", dict(arguments)),
            await s.call_tool("gen_character", arguments | {"job_id": "   "}),
        ]

    results = with_client(fn)
    assert [result.is_error for result in results] == [True, True]
    assert all("job_id is required" in text for text in _texts(results)), _texts(results)
    assert reached == []


def test_gen_character_dry_run_quotes_without_a_job_id_while_a_real_run_still_checks_the_ledger(
    monkeypatch, tmp_path
):
    reached = _spending_backend(monkeypatch, tmp_path)
    gen.Ledger(tmp_path / "ledger.jsonl").append("job-character", "done", kind="character", spent=12)
    arguments = {key: value for key, value in SPEND_CALLS["gen_character"].items() if key != "job_id"}

    async def fn(s):
        return [
            await s.call_tool("gen_character", arguments | {"dry_run": True}),
            await s.call_tool("gen_character", dict(SPEND_CALLS["gen_character"])),
        ]

    results = with_client(fn)
    texts = _texts(results)
    assert [result.is_error for result in results] == [False, True], texts
    assert json.loads(texts[0])["dry_run"] is True
    assert "may already have spent credits" in texts[1], texts[1]
    assert [step for step in reached if step != "browser"] == [("gen_character", None)]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"characters": []}, "characters is required"),
        ({"characters": ["E", "E"]}, "named once"),
        ({"media_ids": ["E"]}, "named once"),
        ({"characters": [" "]}, "must not be blank"),
        ({"media_ids": [""]}, "must not be blank"),
        ({"model": "veo-quality"}, "model must be one of"),
        ({"aspect": "1:1"}, "aspect must be one of"),
        ({"prompt": "   "}, "prompt is required"),
        ({"project": ""}, "project is required"),
    ],
)
def test_gen_character_refuses_what_it_cannot_price_or_name_before_a_browser_opens(
    monkeypatch, tmp_path, change, message
):
    reached = _spending_backend(monkeypatch, tmp_path)

    async def fn(s):
        return await s.call_tool("gen_character", SPEND_CALLS["gen_character"] | change)

    result = with_client(fn)
    text = _texts([result])[0]
    assert result.is_error and message in text, text
    assert reached == []
    assert not (tmp_path / "ledger.jsonl").exists()


def test_gen_character_forwards_every_option_to_the_driver(monkeypatch, tmp_path):
    _spending_backend(monkeypatch, tmp_path)
    arguments = SPEND_CALLS["gen_character"] | {"media_ids": ["M"], "model": "veo-lite", "aspect": "16:9"}

    async def fn(s):
        return await s.call_tool("gen_character", arguments)

    result = with_client(fn)
    assert not result.is_error, _texts([result])
    assert json.loads(_texts([result])[0]) == {
        "project": "P",
        "prompt": "a boat",
        "characters": ["E"],
        "media_ids": ["M"],
        "model": "veo-lite",
        "aspect": "16:9",
        "job_id": "job-character",
        "out_dir": str(tmp_path),
        "dry_run": False,
    }


def test_gen_character_shows_the_agent_its_defaults():
    schema = served_tool_objects()["gen_character"].input_schema["properties"]
    assert schema["model"].get("default") == "omni-flash"
    assert schema["aspect"].get("default") == "9:16"
    assert schema["dry_run"].get("default") is False


def test_gen_character_tells_the_agent_a_filtered_run_costs_nothing_and_must_not_be_retried_as_is():
    # Measured 2026-09-17 (plan E, L4): Flow's prominent-people filter refused a character made from a real photo.
    description = served_tool_objects()["gen_character"].description
    assert "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED" in description
    assert "charges nothing" in description and "do not retry the same inputs" in description
    # dancer-1 (2026-09-17): a dry run passed all its checks and the real run was refused; mf0916 passed some runs.
    assert "a dry run cannot tell in advance" in description
    assert "the same character can pass one run and be refused the next" in description
    # dancer-1 (2026-09-17): the brief said a chip carries an entity_id, the result carries "id".
    assert "each chip's id is the character's entity id or the image's workflow id" in description


def test_character_create_takes_exactly_one_of_prompt_and_image_before_a_browser_opens(monkeypatch, tmp_path):
    # Plan character-generation T5b: a character can come from the owner's own face photo (DECISIONS 2026-09-16).
    recorder = _Recorder()
    monkeypatch.setattr(mcp_server, "backend", recorder)
    face = tmp_path / "face.jpg"
    face.write_bytes(b"\xff\xd8\xff")

    async def fn(s):
        return [
            await s.call_tool("character_create", {"project_id": "P"}),
            await s.call_tool(
                "character_create", {"project_id": "P", "prompt": "a calm face", "image": str(face)}
            ),
            await s.call_tool("character_create", {"project_id": "P", "prompt": "   "}),
            await s.call_tool("character_create", {"project_id": "P", "image": str(tmp_path / "gone.jpg")}),
            await s.call_tool("character_create", {"project_id": "P", "image": str(tmp_path)}),
        ]

    results = with_client(fn)
    texts = _texts(results)
    assert [result.is_error for result in results] == [True] * 5, texts
    assert all("exactly one of prompt and image" in text for text in texts[:3]), texts
    assert "gone.jpg" in texts[3] and "no image file" in texts[4], texts
    assert recorder.calls == []


def test_character_create_forwards_an_image_to_the_driver(monkeypatch, tmp_path):
    face = tmp_path / "face.jpg"
    face.write_bytes(b"\xff\xd8\xff")
    seen = {}

    async def fake_with(self, fn):
        return await fn(object())

    async def fake_create(
        session, project_id, prompt=None, *, image=None, name=None, personality=None, wait=90.0
    ):
        seen.update(project_id=project_id, prompt=prompt, image=image, name=name)
        return {"entity_id": "E"}

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.characters_mod, "create", fake_create)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))

    async def fn(s):
        return await s.call_tool("character_create", {"project_id": "P", "image": str(face), "name": "Mai"})

    result = with_client(fn)
    assert not result.is_error, _texts([result])
    assert seen == {"project_id": "P", "prompt": None, "image": face, "name": "Mai"}


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


def test_clip_reconcile_names_the_ledger_it_read_so_an_empty_answer_is_not_ambiguous(monkeypatch, tmp_path):
    # Measured 2026-09-15: clip_reconcile answered [] both for a clean ledger and for an out_dir holding no ledger
    # at all (Ledger.rows() is [] for a missing file), so "nothing left open" and "looked in the wrong place"
    # were the same reply.
    ledger = gen.Ledger(tmp_path / "kept" / "ledger.jsonl")
    ledger.append("j", "submitted", kind="t2v")
    ledger.append("j", "done", spent=10)
    seen = []

    async def fake_reconcile(session, project_id, *, out_dir):
        seen.append(out_dir)
        return []

    _offline_backend(monkeypatch, lambda url: {})
    monkeypatch.setattr(mcp_server.clips_mod, "reconcile_editor", fake_reconcile)

    async def fn(s):
        kept = await s.call_tool("clip_reconcile", {"project_id": "P", "out_dir": str(tmp_path / "kept")})
        typo = await s.call_tool("clip_reconcile", {"project_id": "P", "out_dir": str(tmp_path / "typo")})
        return _payload(kept), _payload(typo)

    kept, typo = with_client(fn)
    assert kept == {
        "ledger": str((tmp_path / "kept" / "ledger.jsonl").resolve()),
        "ledger_exists": True,
        "ledger_rows": 2,
        "jobs": [],
    }
    assert typo == {
        "ledger": str((tmp_path / "typo" / "ledger.jsonl").resolve()),
        "ledger_exists": False,
        "ledger_rows": 0,
        "jobs": [],
    }
    assert seen == [tmp_path / "kept", tmp_path / "typo"]


def test_clip_reconcile_opens_no_browser_when_no_job_can_be_judged_here(monkeypatch, tmp_path):
    # Review 2026-09-15 (finding 4): clip_reconcile opened Chrome (12-17 s) before reading a ledger holding nothing it
    # could judge: a gen job, another project's editor job, an old row that lists no workflows.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("g", "submitted", kind="t2v", credits_before=295)
    ledger.append(
        "b", "opening", kind="edit", source_media_id="m", credits_before=295, project="Q", workflows_before=[]
    )
    ledger.append("old", "opening", kind="extend", source_media_id="m", credits_before=295)
    # An opening row of this project that names no prompt cannot be judged either (DECISIONS 2026-09-15).
    ledger.append(
        "noprompt",
        "opening",
        kind="edit",
        source_media_id="m",
        credits_before=295,
        project="P",
        workflows_before=["w"],
    )
    # A settled job of this project is not open, so it is no reason to read the listing either. It names its workflows
    # and prompt, so only its done row keeps it out (review of plan C, 2026-09-15).
    ledger.append(
        "s",
        "opening",
        kind="edit",
        source_media_id="m",
        credits_before=295,
        project="P",
        workflows_before=["w"],
        prompt="make it night",
    )
    ledger.append("s", "done", spent=20)

    async def no_browser(self, fn):
        raise AssertionError("clip_reconcile opened a browser with nothing to judge")

    monkeypatch.setattr(mcp_server.Backend, "_with", no_browser)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend())

    async def fn(s):
        return await s.call_tool("clip_reconcile", {"project_id": "P", "out_dir": str(tmp_path)})

    jobs = _payload(with_client(fn))["jobs"]
    assert [(j["job_id"], j["verdict"]) for j in jobs] == [
        ("g", "skipped"),
        ("b", "skipped"),
        ("old", "unknown"),
        ("noprompt", "unknown"),
    ]


def test_clip_reconcile_opens_the_browser_when_a_job_of_this_project_can_be_judged(monkeypatch, tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        "j",
        "opening",
        kind="edit",
        source_media_id="m",
        credits_before=295,
        project="P",
        workflows_before=["w"],
        prompt="make it night",
    )
    opened = []

    async def browser(self, fn):
        opened.append(True)
        return await fn("session")

    async def fake_reconcile(session, project_id, *, out_dir):
        return [{"job_id": "j", "verdict": "unknown", "session": session}]

    monkeypatch.setattr(mcp_server.Backend, "_with", browser)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend())
    monkeypatch.setattr(mcp_server.clips_mod, "reconcile_editor", fake_reconcile)

    async def fn(s):
        return await s.call_tool("clip_reconcile", {"project_id": "P", "out_dir": str(tmp_path)})

    assert _payload(with_client(fn))["jobs"] == [{"job_id": "j", "verdict": "unknown", "session": "session"}]
    assert opened == [True]


def test_project_rename_replies_with_the_project_id_and_the_title_the_grid_lists(monkeypatch):
    # Measured 2026-09-15: the reply was only {title}, the very string the agent had just sent.
    async def fake_rename(project_id, title):
        return "as listed"

    monkeypatch.setattr(mcp_server.backend, "project_rename", fake_rename)

    async def fn(s):
        return _payload(await s.call_tool("project_rename", {"project_id": "P", "title": "new title"}))

    assert with_client(fn) == {"id": "P", "title": "as listed"}


def _error_text_of_a_failing_generation(monkeypatch, tmp_path, exc):
    async def fake_run_job(job, out_dir, **kwargs):
        raise exc

    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
    monkeypatch.setattr(mcp_server.gen_mod, "run_job", fake_run_job)

    async def fn(session):
        return await session.call_tool("gen_t2v", {"prompt": "a boat", "project": "P", "job_id": "job-1"})

    result = with_client(fn)
    assert result.is_error
    return "".join(getattr(c, "text", "") for c in result.content)


def test_an_agent_reads_why_a_tool_failed(monkeypatch, tmp_path):
    # Measured 2026-09-15: mcp 2.2.0 shows only "Error executing tool <name>" for any exception that is not a
    # ToolError, so a WAF stop and a refused job_id reached the agent as the very same bare line.
    refused = gen.AlreadySubmitted("job job-1 already has a submitted row; use a new job id")
    assert "already has a submitted row" in _error_text_of_a_failing_generation(
        monkeypatch, tmp_path, refused
    )
    waf = RuntimeError("Google Flow flagged this request as unusual activity. Stop: do not retry")
    assert "Stop: do not retry" in _error_text_of_a_failing_generation(monkeypatch, tmp_path, waf)


def test_a_missing_argument_is_named_to_the_agent(monkeypatch):
    monkeypatch.setattr(mcp_server, "backend", _Recorder())

    async def fn(session):
        return await session.call_tool("gen_t2v", {"prompt": "a boat", "project": "", "job_id": "job-1"})

    result = with_client(fn)
    assert result.is_error
    assert "project is required" in "".join(getattr(c, "text", "") for c in result.content)


def test_an_error_shown_to_the_agent_never_carries_a_cookie(monkeypatch, tmp_path):
    # Review 2026-09-15: the ledger's regex covers SAPISID, __Secure- and "Authorization:" only, while a Playwright
    # request error appends a call log listing request headers, cookies included.
    leaked = RuntimeError(
        "request failed with SAPISID=abc123secret in the header; cookie: SID=sid-secret; HSID=hsid-secret; "
        "authorization: Bearer ya29.bearer-secret\nCall log:\n  - cookie: SSID=ssid-secret"
    )
    text = _error_text_of_a_failing_generation(monkeypatch, tmp_path, leaked)
    for secret in ("abc123secret", "sid-secret", "hsid-secret", "bearer-secret", "ssid-secret", "Call log"):
        assert secret not in text, text
    assert "request failed" in text


def test_the_server_log_of_a_failed_tool_carries_no_cookie_either(monkeypatch, tmp_path, caplog):
    # Scoped re-review 2026-09-15: the traceback logged to the server's stderr still held the raw cookies.
    leaked = RuntimeError("request failed; cookie: SID=sid-secret\nCall log:\n  - cookie: SSID=ssid-secret")
    with caplog.at_level("DEBUG"):
        _error_text_of_a_failing_generation(monkeypatch, tmp_path, leaked)
    assert "tool gen_t2v failed" in caplog.text
    assert "sid-secret" not in caplog.text and "ssid-secret" not in caplog.text, caplog.text


class _BrowserlessClient:
    """Stands in for gflow's FlowApiClient so a real FlowSession, lock included, runs with no Chrome."""

    def __init__(self, profile_dir, *, headless):
        self._context = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def new_page(self):
        return self

    async def close(self):
        pass


def test_a_call_cancelled_while_it_waits_for_the_browser_does_not_wedge_later_calls(monkeypatch, tmp_path):
    # Review 2026-09-15: a client whose read timeout runs out sends notifications/cancelled and the SDK cancels the
    # handler. One cancelled while waiting for the session lock used to take the lock anyway, and every later
    # browser tool hung until the server restarted.
    guard = threading.Lock()
    monkeypatch.setattr(session_mod, "_GUARD", guard)
    cancelled_waiters = []

    class _WatchedSession(FlowSession):
        async def __aenter__(self):
            try:
                return await super().__aenter__()
            except asyncio.CancelledError:
                cancelled_waiters.append(self)
                raise

    monkeypatch.setattr(
        mcp_server,
        "FlowSession",
        lambda profile: _WatchedSession(profile_dir=tmp_path, client_factory=_BrowserlessClient),
    )
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend())

    async def fn(s):
        release = asyncio.Event()

        async def slow_credits(session):
            await release.wait()
            return {"balance": 1, "raw": [1]}

        async def quick_lane(session):
            return {"verdict": "MIGRATED"}

        async def quick_projects(session):
            return [{"id": "P"}]

        monkeypatch.setattr(mcp_server.reader, "credits", slow_credits)
        monkeypatch.setattr(mcp_server.lane_mod, "run", quick_lane)
        monkeypatch.setattr(mcp_server.reader, "projects", quick_projects)
        holder = asyncio.create_task(s.call_tool("flow_credits", {}))
        await asyncio.sleep(0.2)
        with pytest.raises(MCPError, match="timed out"):
            await s.call_tool("flow_lane", {}, read_timeout_seconds=0.3)
        # Let the lock go only once the server has really cancelled the waiter, or the test races past the bug.
        for _ in range(40):
            if cancelled_waiters:
                break
            await asyncio.sleep(0.05)
        assert cancelled_waiters, "the server never cancelled the call that timed out"
        release.set()
        held = await holder
        try:
            later = await asyncio.wait_for(s.call_tool("flow_projects", {}), 3)
        finally:
            while guard.locked():
                guard.release()
                await asyncio.sleep(0.05)
        return _payload(held), _payload(later)

    held, later = with_client(fn)
    assert held["balance"] == 1
    assert later == [{"id": "P"}]


def test_a_call_cancelled_while_it_holds_the_browser_still_gives_the_profile_back(monkeypatch, tmp_path):
    # Review 2026-09-15: the SDK keeps cancelling a cancelled handler at every await, so page.close() raised inside
    # teardown and client.__aexit__, which returns gflow's profile lease, never ran: later calls got
    # ProfileLockedError until the server restarted.
    guard = threading.Lock()
    monkeypatch.setattr(session_mod, "_GUARD", guard)
    lease = {"held": False}

    class _Page:
        async def close(self):
            await asyncio.sleep(0.05)

    class _LeasedClient:
        def __init__(self, profile_dir, *, headless):
            self._context = self

        async def __aenter__(self):
            if lease["held"]:
                raise RuntimeError("ProfileLockedError: the profile lease is already held in this process")
            lease["held"] = True
            return self

        async def __aexit__(self, *exc):
            lease["held"] = False

        async def new_page(self):
            return _Page()

    monkeypatch.setattr(
        mcp_server,
        "FlowSession",
        lambda profile: FlowSession(profile_dir=tmp_path, client_factory=_LeasedClient),
    )
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend())

    async def fn(s):
        async def never_answers(session):
            await asyncio.Event().wait()

        async def quick_projects(session):
            return [{"id": "P"}]

        monkeypatch.setattr(mcp_server.reader, "credits", never_answers)
        monkeypatch.setattr(mcp_server.reader, "projects", quick_projects)
        with pytest.raises(MCPError, match="timed out"):
            await s.call_tool("flow_credits", {}, read_timeout_seconds=0.3)
        for _ in range(40):
            if not guard.locked():
                break
            await asyncio.sleep(0.05)
        return await asyncio.wait_for(s.call_tool("flow_projects", {}), 3)

    later = with_client(fn)
    assert not later.is_error, _texts([later])
    assert _payload(later) == [{"id": "P"}]


def test_the_instructions_say_calls_are_slow_and_a_retry_must_keep_its_job_id():
    # Measured 2026-09-15: a context-free agent pointed out that nothing warned a call blocks for tens of
    # seconds, which is exactly when an agent retries, and a retry under a fresh job id is a second charge.
    text = mcp_server.server.instructions
    assert re.search(r"\b\d+\s*(?:s|seconds|min|minutes)\b", text), text
    assert "job_id" in text
    assert "omni-flash" in text


def _smoke_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "acceptance" / "mcp_smoke.py"
    spec = importlib.util.spec_from_file_location("mcp_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_tool_that_says_it_can_spend_is_classified_as_free_by_the_smoke():
    # The smoke's buckets are one claim about money and the descriptions are another; they must not disagree. Found
    # by the scoped review 2026-09-16: clip_download sat under "free for Flow" while its own description says the 4k
    # rendition may spend. Harmless while neither bucket is called, dangerous the day someone trusts the bucket name.
    smoke = _smoke_module()
    free = {*smoke.READ_ONLY_NO_ARGS, *smoke.READ_ONLY_PER_PROJECT, *smoke.MUTATING, *smoke.DOWNLOADING}
    tools = mcp_server.server._tool_manager._tools
    says_it_spends = {
        name
        for name, tool in tools.items()
        if any(
            phrase in (tool.description or "").lower() for phrase in ("spends credits", "may spend credits")
        )
    }
    assert sorted(says_it_spends & free) == []


@pytest.mark.parametrize("bad", ["elsewhere", "out/../elsewhere", "out/x/LEDGER.JSONL"])
def test_a_scene_download_out_dir_outside_the_out_folder_is_refused_before_a_browser_opens(
    monkeypatch, tmp_path, bad
):
    # scene_download writes a film, so it obeys the rule the editor jobs obey (CLAUDE.md rule 5). Found by mutation:
    # dropping the guard from Backend.scene_download broke no test at all (plan scene-timeline-tools T4).
    reached = []

    async def fake_download(session, project_id, scene_id, *, out_dir):
        reached.append(out_dir)
        return {"path": str(out_dir)}

    monkeypatch.setattr(mcp_server.scenes_mod, "download", fake_download)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "out"))

    async def fn(s):
        arguments = {"project_id": "P", "scene_id": "S", "out_dir": str(tmp_path / bad)}
        return await s.call_tool("scene_download", arguments)

    text = _texts([with_client(fn)])[0]
    assert "out_dir must be" in text, text
    assert reached == []


@pytest.mark.parametrize(
    "first_failure",
    [
        "Download.path: Target page, context or browser has been closed",
        'Timeout 290000ms exceeded while waiting for event "download"',
        "clicked Download scene once and the page started no export within 20s (scene S)",
        "the page says scene S was downloaded but no file reached this browser within 30s",
        "the film of scene S failed in the browser (Error: canceled)",
    ],
)
def test_a_scene_download_retries_once_when_the_film_never_lands(monkeypatch, tmp_path, first_failure):
    # Both measured on 2026-09-16: the page can be gone by the time save_as runs, and a click can fire no download at
    # all. The download changes nothing, so a second session is safe; a different failure must still come straight out.
    sessions = []

    async def fake_with(self, fn):
        sessions.append(len(sessions))
        if len(sessions) == 1:
            raise RuntimeError(first_failure)
        return await fn(object())

    async def fake_download(session, project_id, scene_id, *, out_dir):
        return {"scene_id": scene_id, "path": str(out_dir / "film.mp4")}

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.scenes_mod, "download", fake_download)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "out"))

    async def fn(s):
        return await s.call_tool("scene_download", {"project_id": "P", "scene_id": "S"})

    result = with_client(fn)
    assert not result.is_error, _texts([result])[0]
    assert len(sessions) == 2
    assert json.loads(_texts([result])[0])["attempts"] == 2


def test_a_scene_download_does_not_retry_another_kind_of_failure(monkeypatch, tmp_path):
    sessions = []

    async def fake_with(self, fn):
        sessions.append(len(sessions))
        raise LookupError("there is no clip on this scene's timeline to download")

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "out"))

    async def fn(s):
        return await s.call_tool("scene_download", {"project_id": "P", "scene_id": "S"})

    result = with_client(fn)
    assert result.is_error and "no clip" in _texts([result])[0]
    assert len(sessions) == 1


def test_a_scene_download_folder_inside_out_reaches_the_driver(monkeypatch, tmp_path):
    seen = {}

    async def fake_with(self, fn):
        return await fn(object())

    async def fake_download(session, project_id, scene_id, *, out_dir):
        seen["out_dir"] = out_dir
        return {"scene_id": scene_id, "path": str(out_dir / "film.mp4")}

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.scenes_mod, "download", fake_download)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "out"))

    async def fn(s):
        arguments = {"project_id": "P", "scene_id": "S", "out_dir": str(tmp_path / "out" / "films")}
        return await s.call_tool("scene_download", arguments)

    result = with_client(fn)
    assert not result.is_error, _texts([result])[0]
    assert seen["out_dir"] == tmp_path / "out" / "films"


def test_scene_clips_answers_with_the_timeline_the_driver_read_off_the_listing(monkeypatch, tmp_path):
    read = {}
    timeline = {
        "scene_id": "S",
        "title": "dancer-test-2",
        "trashed": False,
        "aspect": "9:16",
        "seconds": 16.0,
        "clips": [
            {"position": 0, "clip_id": "c0", "title": "Dancer preparing for livestream", "seconds": 8.0},
            {"position": 1, "clip_id": "c1", "title": "Dancer streaming in studio", "seconds": 8.0},
        ],
    }

    async def fake_with(self, fn):
        return await fn("session")

    async def fake_timeline(session, project_id, scene_id):
        read.update(session=session, project_id=project_id, scene_id=scene_id)
        return timeline

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.scenes_mod, "timeline", fake_timeline)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "out"))

    async def fn(s):
        return await s.call_tool("scene_clips", {"project_id": "P", "scene_id": "S"})

    result = with_client(fn)
    assert not result.is_error, _texts([result])[0]
    assert json.loads(_texts([result])[0]) == timeline
    assert read == {"session": "session", "project_id": "P", "scene_id": "S"}


def test_scene_clips_tells_the_agent_the_order_is_the_films_and_how_clips_are_named():
    text = served_tool_objects()["scene_clips"].description
    for phrase in ("order the film plays", "position", "from 0", "clip_id", "aspect", "Free."):
        assert phrase in text, phrase
    assert "scene_move_clip" in text and "scene_remove_clip" in text


def test_scene_remove_clip_and_scene_move_clip_reach_the_driver_with_the_clip_named(monkeypatch, tmp_path):
    seen = []

    async def fake_with(self, fn):
        return await fn("session")

    async def fake_remove(session, project_id, scene_id, clip_id):
        seen.append(("remove", session, project_id, scene_id, clip_id))
        return {"clips": []}

    async def fake_move(session, project_id, scene_id, clip_id, position):
        seen.append(("move", session, project_id, scene_id, clip_id, position))
        return {"clips": []}

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.scenes_mod, "remove_clip", fake_remove)
    monkeypatch.setattr(mcp_server.scenes_mod, "move_clip", fake_move)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "out"))

    async def fn(s):
        removed = await s.call_tool(
            "scene_remove_clip", {"project_id": "P", "scene_id": "S", "clip_id": "C1"}
        )
        moved = await s.call_tool(
            "scene_move_clip", {"project_id": "P", "scene_id": "S", "clip_id": "C2", "position": 3}
        )
        return removed, moved

    removed, moved = with_client(fn)
    assert not removed.is_error and not moved.is_error, _texts([removed, moved])
    assert seen == [("remove", "session", "P", "S", "C1"), ("move", "session", "P", "S", "C2", 3)]


def test_scene_move_clip_refuses_a_negative_position_before_a_browser_opens(monkeypatch, tmp_path):
    sessions = []

    async def fake_with(self, fn):
        sessions.append(fn)
        return {}

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "out"))

    async def fn(s):
        return await s.call_tool(
            "scene_move_clip", {"project_id": "P", "scene_id": "S", "clip_id": "C", "position": -1}
        )

    result = with_client(fn)
    assert result.is_error and "position" in _texts([result])[0]
    assert sessions == []


@pytest.mark.parametrize("tool", ["scene_remove_clip", "scene_move_clip"])
def test_scene_clip_tools_refuse_a_blank_clip_id_before_a_browser_opens(monkeypatch, tmp_path, tool):
    sessions = []

    async def fake_with(self, fn):
        sessions.append(fn)
        return {}

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "out"))
    arguments = {"project_id": "P", "scene_id": "S", "clip_id": "  "}
    if tool == "scene_move_clip":
        arguments["position"] = 1

    async def fn(s):
        return await s.call_tool(tool, arguments)

    result = with_client(fn)
    assert result.is_error and "clip_id is required" in _texts([result])[0]
    assert sessions == []


def test_scene_remove_clip_tells_the_agent_flow_asks_nothing_and_the_media_stays():
    text = served_tool_objects()["scene_remove_clip"].description
    for phrase in ("clip_id", "scene_clips", "asks nothing", "stays in the project", "Free."):
        assert phrase in text, phrase


def test_scene_move_clip_tells_the_agent_how_positions_count_and_how_the_move_is_done():
    text = served_tool_objects()["scene_move_clip"].description
    for phrase in ("clip_id", "position", "from 0", "drag", "scene_clips", "Free."):
        assert phrase in text, phrase


def test_scene_download_tells_the_agent_the_film_is_built_in_the_page_and_how_long_that_takes():
    # The dancer test (2026-09-17) waited on a 40 s film that failed twice with nothing in the description to go by.
    text = served_tool_objects()["scene_download"].description
    for phrase in ("built inside the page", "40 s film", "seconds", "clips", "partial", "attempts", "Free."):
        assert phrase in text, phrase


@pytest.mark.parametrize("aspect", ["4:3", "9x16", "portrait", ""])
def test_scene_set_aspect_refuses_a_ratio_flow_does_not_offer_before_a_browser_opens(
    monkeypatch, tmp_path, aspect
):
    sessions = []

    async def fake_with(self, fn):
        sessions.append(fn)
        return {}

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "out"))

    async def fn(s):
        return await s.call_tool("scene_set_aspect", {"project_id": "P", "scene_id": "S", "aspect": aspect})

    result = with_client(fn)
    assert result.is_error and "9:16 or 16:9" in _texts([result])[0]
    assert sessions == []


def test_scene_set_aspect_reaches_the_driver_with_the_ratio_asked_for(monkeypatch, tmp_path):
    seen = {}

    async def fake_with(self, fn):
        return await fn("session")

    async def fake_set_aspect(session, project_id, scene_id, aspect):
        seen.update(session=session, project_id=project_id, scene_id=scene_id, aspect=aspect)
        return {"scene_id": scene_id, "aspect": aspect, "changed": True, "rpcids": ["BpMsoe"]}

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.scenes_mod, "set_aspect", fake_set_aspect)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "out"))

    async def fn(s):
        return await s.call_tool("scene_set_aspect", {"project_id": "P", "scene_id": "S", "aspect": "16:9"})

    result = with_client(fn)
    assert not result.is_error, _texts([result])[0]
    assert seen == {"session": "session", "project_id": "P", "scene_id": "S", "aspect": "16:9"}


def test_scene_set_aspect_tells_the_agent_both_ratios_and_that_a_scene_already_there_is_left_alone():
    text = served_tool_objects()["scene_set_aspect"].description
    for phrase in ("9:16", "16:9", "already", "scene_clips", "Free."):
        assert phrase in text, phrase


def test_scene_add_clip_tells_the_agent_the_clip_goes_last_and_to_read_the_timeline_rather_than_add_again():
    # The dancer test (2026-09-17) followed the old text, "changed=false usually means the label had not caught up",
    # while its timeline was empty: Flow had changed its picker and nothing had been added at all.
    text = served_tool_objects()["scene_add_clip"].description
    for phrase in ("at the end", "position", "clip_id", "scene_clips", "never add it again", "Free."):
        assert phrase in text, phrase
    assert "changed" not in text and "usually" not in text
