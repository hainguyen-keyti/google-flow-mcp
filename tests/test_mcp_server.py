import asyncio
import importlib.util
import json
import re
import threading
import time
from pathlib import Path

import pytest
from gflow_cli.api.transports.migrated_composer import R2V_DURATION_S
from mcp.client.session import ClientSession
from mcp.shared.exceptions import MCPError
from mcp.shared.memory import create_client_server_memory_streams
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from test_ingredients import PROMPT, _video
from test_jobs import _Reader, _started_job
from test_jobs import _world as _job_world

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
    # Plan J: voices belong to a character, and two ways to turn a clip into project media. 37 to 43 on purpose.
    "flow_voices",
    "character_set_voice",
    "character_make_voice",
    "character_clear_voice",
    "clip_save_frame",
    "scene_save_clip",
    "agent_mode",
    "agent_send",
    "clip_download",
    "clip_extend",
    "clip_edit",
    "clip_reconcile",
    # Plan AB: one tool that covers every video option Flow's composer offers, 43 to 44 on purpose.
    "gen_video",
    "flow_uploads",
    # Plan AL: what a clip was made from, read back off the listing, 44 to 45 on purpose.
    "clip_recipe",
    # Plan AN: submit a generation and come back for it, three tools on purpose.
    "job_submit",
    "job_status",
    "job_collect",
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
    # Plan H T2: the filters ride here so the passthrough test proves each one reaches the backend rather than
    # being accepted and dropped, which is how all_versions behaved on 2026-09-13.
    "flow_media": {"project_id": "P", "kind": "video", "since": "2026-09-01", "limit": 5, "brief": True},
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
    "flow_voices": {"project_id": "P", "entity_id": "E"},
    "character_set_voice": {"project_id": "P", "entity_id": "E", "voice": "Leda"},
    "character_make_voice": {
        "project_id": "P",
        "entity_id": "E",
        "preset": "Leda",
        "performance": "giọng nữ Sài Gòn",
        "name": "SaigonGirl20",
    },
    "character_clear_voice": {"project_id": "P", "entity_id": "E"},
    "clip_save_frame": {"project_id": "P", "media_id": "M"},
    "scene_save_clip": {"project_id": "P", "scene_id": "S", "clip_id": "C"},
    "agent_mode": {"project_id": "P", "enabled": True},
    "agent_send": {"project_id": "P", "message": "hello", "job_id": "job-agent"},
    "clip_download": {"project_id": "P", "media_id": "M"},
    "clip_extend": {"project_id": "P", "media_id": "M", "prompt": "keep going", "job_id": "job-extend"},
    "clip_edit": {"project_id": "P", "media_id": "M", "prompt": "change the shirt", "job_id": "job-edit"},
    "clip_reconcile": {"project_id": "P"},
    "clip_recipe": {"project_id": "P", "media_id": "M", "workflow_id": "W"},
    "gen_t2v": {"prompt": "a boat", "project": "P", "job_id": "job-t2v"},
    "gen_i2v": {"initial_frame": "/tmp/a.png", "prompt": "a boat", "project": "P", "job_id": "job-i2v"},
    "gen_r2v": {"refs": ["/tmp/a.png"], "prompt": "a boat", "project": "P", "job_id": "job-r2v"},
    "gen_t2i": {"prompt": "a boat", "project": "P"},
    "gen_i2i": {"refs": ["/tmp/a.png"], "prompt": "a boat", "project": "P"},
    "gen_video": {"prompt": "a boat", "project": "P", "job_id": "job-video", "max_credits": 20},
    "gen_character": {
        "prompt": "a boat",
        "project": "P",
        "characters": ["E"],
        "job_id": "job-character",
        "duration": 10,
    },
    "job_submit": {"prompt": "a boat", "project": "P", "job_id": "job-submit", "max_credits": 20},
    "job_status": {"job_id": "job-status"},
    "job_collect": {"job_id": "job-collect"},
}


# A CLI command with no MCP tool is unreachable for an agent, which is the whole product. Every entry
# here is a claim that an agent never needs that command, so each one carries the reason it is safe.
EXCLUDED_COMMANDS = {
    "mcp run": "is the MCP server itself; it does not expose itself as a tool",
    "flow character list": "flow_characters already serves it",
    "flow survey": "a maintenance walk that rewrites the repo's own baselines with --write; run by the owner or an "
    "agent working on the repo, never by an agent using the tools (plan AC, 2026-09-29)",
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

    async def fake_media(project_id, all_versions=False, *_filters, **_kwargs):
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


def test_gen_i2v_prices_the_end_frame_run_separately_from_the_start_frame_one():
    """Plan I T3. The end frame goes out on Flow's interpolation submit, a different wire model from the plain
    start-frame run the 15 credits were measured on, so the description has to carry BOTH numbers and say which
    is which. Anything else lets an agent budget one run at the other's price."""
    description = served_tool_objects()["gen_i2v"].description
    assert "15" in description, description
    assert "end_frame" in description and "interpolation" in description.lower(), description
    # Only the end frame's own refusal is lifted; the shared job_id rule still says a used id is refused.
    assert "end_frame is refused" not in description, (
        "the refusal is lifted once the run has a measured price"
    )
    # Never merged into one figure: each form carries its own measurement, which is also how a reader can tell
    # the two were priced separately rather than assumed equal (they happen to match today, at 15 each).
    assert "105 s" in description and "119 s" in description, description
    assert re.search(r"start frame[^.]*?15 credits in 105 s", description), description
    assert re.search(r"end_frame = 15 credits in 119 s", description), description


@pytest.mark.parametrize(
    "settings",
    [{"model": "veo-lite"}, {"model": "veo-fast"}, {"model": "omni-flash", "duration": 8}, {"duration": 4}],
)
def test_gen_i2v_takes_an_end_frame_only_where_that_run_was_priced(monkeypatch, settings):
    """Review 2026-09-18 (Plan I). One run priced ONE cell: omni-flash at 10 s. gflow puts no model gate on
    --end-frame and picks a different interpolation model per cohort (veo_3_1_interpolation_lite against
    omni_flash_i2v_8s_first_last), so any other model or length submits something nobody has paid for once,
    while the sibling tools quote veo-lite at 10 and invite the agent to assume the same here."""
    called = []

    async def fake_generate(**kwargs):
        called.append(kwargs)
        return {"job_id": "x", "outputs": []}

    monkeypatch.setattr(mcp_server.backend, "generate", fake_generate)

    async def fn(session):
        return await session.call_tool(
            "gen_i2v",
            {
                "initial_frame": "/tmp/a.png",
                "end_frame": "/tmp/b.png",
                "prompt": "a boat",
                "project": "P",
                "job_id": "job-cell",
                **settings,
            },
        )

    result = with_client(fn)
    text = "".join(getattr(c, "text", "") for c in result.content)
    assert result.is_error, text
    assert "end_frame" in text and "omni-flash" in text, text
    assert called == [], "an unpriced cell must be refused before anything is spent"


def test_gen_i2v_hands_the_end_frame_all_the_way_into_the_argv(monkeypatch, tmp_path):
    """The refusal test used to be the only thing proving end_frame was read at all, and a version that dropped
    it anywhere on the way would leave the whole suite green while an agent pays for an interpolation and gets a
    plain start-frame clip (CLAUDE.md rule 10, the swallowed argument).

    Re-review 2026-09-18: stubbing `backend.generate` only pinned the tool's own hop, leaving the middle one,
    `Backend.generate` building the Job, uncovered. The stub goes at the far end instead, so this one run walks
    tool -> Job -> argv, the same route the paid run took."""
    argvs = []

    async def fake_run_job(job, out_dir, **kwargs):
        argvs.append(gen.build_argv(job, out_dir))
        return {"job_id": job.job_id, "outputs": []}

    monkeypatch.setattr(mcp_server.gen_mod, "run_job", fake_run_job)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))

    async def fn(session):
        return await session.call_tool(
            "gen_i2v",
            {
                "initial_frame": "/tmp/a.png",
                "end_frame": "/tmp/b.png",
                "prompt": "a boat",
                "project": "P",
                "job_id": "job-ends",
            },
        )

    assert not with_client(fn).is_error
    assert len(argvs) == 1, argvs
    assert "--end-frame" in argvs[0], argvs[0]
    assert argvs[0][argvs[0].index("--end-frame") + 1] == "/tmp/b.png", argvs[0]


def test_the_cli_says_an_end_frame_is_a_different_run(monkeypatch):
    """The CLI is the owner's own surface and it advertised 'optional last frame' with no help text at all, so
    `--help` sold a run nobody had priced (review 2026-09-18)."""
    i2v = cli.main.commands["gen"].commands["i2v"]
    end_frame = next(p for p in i2v.params if p.name == "end_frame")
    assert end_frame.help, "--end-frame had no help text at all"
    assert "credit" in end_frame.help.lower(), end_frame.help
    assert "optional last frame" not in (i2v.__doc__ or ""), i2v.__doc__


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
SPENDING_TOOLS = {
    "gen_t2v",
    "gen_i2v",
    "gen_r2v",
    "clip_extend",
    "clip_edit",
    "agent_send",
    "gen_character",
    "gen_video",
    "job_submit",
}
# A tool whose dry_run quotes for free needs no job_id to quote (plan character-generation, DECISIONS 2026-09-16); its
# real run is held to the job_id rule by test_gen_character_refuses_a_real_run_without_a_job_id_before_a_browser_opens.
DRY_RUN_QUOTES = {"gen_character", "gen_video"}


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


def test_the_descriptions_carry_flows_own_price_table_where_it_differs_from_the_measurements():
    """Plan H T8. An agent budgets from these descriptions, so where Flow's published table and this account's
    measurements disagree it has to read BOTH, with which is which.

    Flow's credits page (DECISIONS 2026-09-16): Veo Lite 4, 6, 8 s and Extend 10; Omni Flash 720p 8 s 12 and 10 s
    15; Omni Flash Edit 40, while every measured edit here cost 20; 1080p upscale 0; 4K is not offered below the
    Ultra plan, where it is 50; Omni 360p is half price.
    """
    tools = served_tool_objects()
    edit = tools["clip_edit"].description
    assert "40" in edit and "20" in edit, edit
    assert "table" in edit and "measured" in edit, edit
    # The clip editor never reads the live price line before clicking, unlike gen_character: say so, because the
    # only thing standing between a changed price and a surprise bill is the balance read around the click.
    assert "does not read" in edit and "balance" in edit, edit
    download = tools["clip_download"].description
    assert "Ultra" in download and "50" in download, download
    assert "1080p measured 0 credits" in download, download
    character = tools["gen_character"].description
    assert "12" in character and "10" in character and "20" in character, character


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


# Every served tool that takes an out_dir must appear in one of these two sets, and the test below reads the
# served schemas so neither can drift the way a hand-typed list did: clip_download sat outside the audit for a
# week while a commit called flow_download "the last tool" without a guard (review 2026-09-18).
EDITOR_OUT_DIR_TOOLS = {"clip_extend", "clip_edit", "gen_character", "gen_video", "job_submit"}
FILE_OUT_DIR_TOOLS = {"flow_download", "clip_download", "clip_reconcile", "scene_download"}

SPEND_CALLS = {
    "gen_t2v": {"prompt": "a boat", "project": "P", "job_id": "job-t2v"},
    "gen_i2v": {"initial_frame": "/tmp/a.png", "prompt": "a boat", "project": "P", "job_id": "job-i2v"},
    "gen_r2v": {"refs": ["/tmp/a.png"], "prompt": "a boat", "project": "P", "job_id": "job-r2v"},
    "clip_extend": {"project_id": "P", "media_id": "M", "prompt": "keep going", "job_id": "job-extend"},
    "clip_edit": {"project_id": "P", "media_id": "M", "prompt": "change the shirt", "job_id": "job-edit"},
    "agent_send": {"project_id": "P", "message": "hello", "job_id": "job-agent"},
    "gen_character": {"prompt": "a boat", "project": "P", "characters": ["E"], "job_id": "job-character"},
    "gen_video": {"prompt": "a boat", "project": "P", "job_id": "job-video", "max_credits": 20},
    "job_submit": {"prompt": "a boat", "project": "P", "job_id": "job-submit", "max_credits": 20},
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

    async def fake_video(session, project_id, **kwargs):
        # One driver serves both tools; job_submit is the one that runs it detached.
        reached.append(("job_submit" if kwargs.get("detach") else "gen_video", kwargs.get("job_id")))
        return {"project": project_id, **{k: str(v) if isinstance(v, Path) else v for k, v in kwargs.items()}}

    monkeypatch.setattr(mcp_server.video_mod, "generate", fake_video)

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


def test_gen_character_writes_where_out_dir_points(monkeypatch, tmp_path):
    # Plan H T4. Until now every character clip and its ledger row landed in out/, so an agent making one film had to
    # hunt its own clip among every clip ever made (owner, 2026-09-17, from the sg-bf test).
    reached = _spending_backend(monkeypatch, tmp_path)
    room = tmp_path / "films" / "bakery"

    async def fn(s):
        return await s.call_tool("gen_character", SPEND_CALLS["gen_character"] | {"out_dir": str(room)})

    payload = _payload(with_client(fn))
    assert payload["out_dir"] == str(room)
    assert reached == ["browser", ("gen_character", "job-character")]


def test_a_job_id_in_the_ledger_of_gen_characters_own_out_dir_is_refused(monkeypatch, tmp_path):
    # Moving the output moves the ledger a job_id is looked up in. What catches it today is the sweep of every
    # ledger under the out folder, which the custom dir has to sit inside; this pins that the two rules together
    # still leave no way to spend one job_id twice by naming another folder (I-tiền-1).
    reached = _spending_backend(monkeypatch, tmp_path)
    room = tmp_path / "films" / "bakery"
    gen.Ledger(room / "ledger.jsonl").append("job-character", "submitted", kind="character")

    async def fn(s):
        return await s.call_tool("gen_character", SPEND_CALLS["gen_character"] | {"out_dir": str(room)})

    result = with_client(fn)
    text = _texts([result])[0]
    assert result.is_error and "may already have spent credits" in text, text
    assert reached == []


def test_gen_character_refuses_an_out_dir_that_names_a_file_even_on_a_dry_run(monkeypatch, tmp_path):
    # A dry run spends nothing but still costs a browser and one to two minutes, and an out_dir that cannot hold a
    # ledger would only surface after the real run had started.
    reached = _spending_backend(monkeypatch, tmp_path)

    async def fn(s):
        return [
            await s.call_tool(
                "gen_character",
                SPEND_CALLS["gen_character"] | {"out_dir": str(tmp_path / "films" / "ledger.jsonl")},
            ),
            await s.call_tool(
                "gen_character",
                {
                    **SPEND_CALLS["gen_character"],
                    "job_id": None,
                    "dry_run": True,
                    "out_dir": str(tmp_path.parent / "elsewhere"),
                },
            ),
        ]

    results = with_client(fn)
    texts = _texts(results)
    assert [result.is_error for result in results] == [True, True], texts
    assert "must be a folder" in texts[0], texts[0]
    assert "out_dir must be inside" in texts[1], texts[1]
    assert reached == []


def test_every_served_tool_that_takes_an_out_dir_is_covered_by_a_refusal_case():
    """Review 2026-09-18: the list below was typed by hand, so clip_download sat outside the audit and kept
    writing wherever it was pointed while a commit message called flow_download "the last tool" without one.
    Read the served schemas instead (CLAIM rule 10: a check must read what it says it covers)."""
    served = {
        name: tool
        for name, tool in served_tool_objects().items()
        if "out_dir" in (tool.input_schema or {}).get("properties", {})
    }
    # The refusal cases parametrize from these same sets, so a tool cannot be listed here and yet go unrun:
    # deleting one from the call table used to drop its cases while this test stayed green (re-review 2026-09-18).
    assert set(FILE_OUT_DIR_CALLS) == FILE_OUT_DIR_TOOLS, sorted(FILE_OUT_DIR_TOOLS ^ set(FILE_OUT_DIR_CALLS))
    assert EDITOR_OUT_DIR_TOOLS <= set(SPEND_CALLS), sorted(EDITOR_OUT_DIR_TOOLS - set(SPEND_CALLS))
    covered = EDITOR_OUT_DIR_TOOLS | FILE_OUT_DIR_TOOLS
    assert set(served) - covered == set(), (
        f"these tools take an out_dir with no refusal case: {sorted(set(served) - covered)}"
    )
    assert covered - set(served) == set(), (
        f"these names are covered but no longer serve an out_dir: {sorted(covered - set(served))}"
    )


@pytest.mark.parametrize("tool", sorted(EDITOR_OUT_DIR_TOOLS))
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


FILE_OUT_DIR_CALLS = {
    "flow_download": {"project_id": "P", "media_id": "M"},
    "clip_download": {"project_id": "P", "media_id": "M"},
    "clip_reconcile": {"project_id": "P"},
    "scene_download": {"project_id": "P", "scene_id": "S"},
}


def _file_writing_backend(monkeypatch, tmp_path):
    """Every tool that writes a file or a ledger, with its driver stubbed: records what got past the guard."""
    reached = []

    async def fake_with(self, fn):
        reached.append("browser")
        return await fn(object())

    async def fake_download(session, project_id, media_id, target):
        reached.append(("flow_download", str(target)))
        return target / "clip.mp4"

    async def fake_rendition(session, project_id, media_id, quality, target, workflow_id=None):
        reached.append(("clip_download", str(target)))
        return target / "clip_1080p.mp4"

    async def fake_scene_download(session, project_id, scene_id, out_dir):
        reached.append(("scene_download", str(out_dir)))
        return {"path": str(out_dir / "film.mp4")}

    def fake_needs_flow(project_id, out_dir):
        reached.append(("clip_reconcile", str(out_dir)))
        return False

    async def fake_reconcile(session, project_id, out_dir):
        return []

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.download_mod, "download", fake_download)
    monkeypatch.setattr(mcp_server.clips_mod, "download_rendition", fake_rendition)
    monkeypatch.setattr(mcp_server.clips_mod, "reconcile_needs_flow", fake_needs_flow)
    monkeypatch.setattr(mcp_server.clips_mod, "reconcile_editor", fake_reconcile)
    monkeypatch.setattr(mcp_server.scenes_mod, "download", fake_scene_download)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "out"))
    return reached


@pytest.mark.parametrize("tool", sorted(FILE_OUT_DIR_TOOLS))
@pytest.mark.parametrize(
    ("out_dir", "reason"),
    [
        ("elsewhere", "out_dir must be inside"),
        ("out/../elsewhere", "out_dir must be inside"),
        ("out/films/ledger.jsonl", "must be a folder"),
    ],
)
def test_a_tool_that_writes_refuses_an_out_dir_outside_the_out_folder(
    monkeypatch, tmp_path, tool, out_dir, reason
):
    """Plan I T1 and review 2026-09-18. A file dropped outside out/ escapes rule 5, and a LEDGER outside it is
    worse: `_spend_once` only sweeps ledgers under the out folder, so a job_id recorded elsewhere would never
    stop the second spend. clip_download and clip_reconcile were both missing this."""
    reached = _file_writing_backend(monkeypatch, tmp_path)

    async def fn(s):
        return await s.call_tool(tool, FILE_OUT_DIR_CALLS[tool] | {"out_dir": str(tmp_path / out_dir)})

    text = _texts([with_client(fn)])[0]
    assert reason in text, text
    assert reached == []


def test_flow_download_still_writes_where_a_folder_inside_out_points(monkeypatch, tmp_path):
    # The guard must not cost the tool its own feature: a folder inside out/ still reaches the driver.
    reached = []

    async def fake_with(self, fn):
        return await fn(object())

    async def fake_download(session, project_id, media_id, target):
        reached.append(str(target))
        return target / "clip.mp4"

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.download_mod, "download", fake_download)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "out"))
    room = tmp_path / "out" / "films"

    async def fn(s):
        return await s.call_tool("flow_download", {"project_id": "P", "media_id": "M", "out_dir": str(room)})

    payload = _payload(with_client(fn))
    assert reached == [str(room)]
    assert payload["path"].endswith("clip.mp4")


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
        ({"characters": []}, "at least one character or image"),
        ({"characters": [], "media_ids": []}, "at least one character or image"),
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
    # A non-default length on the one model that has one: a backend passing a constant 8 must not look correct.
    arguments = SPEND_CALLS["gen_character"] | {
        "media_ids": ["M"],
        "model": "omni-flash",
        "aspect": "16:9",
        "duration": 10,
    }

    async def fn(s):
        return await s.call_tool("gen_character", arguments)

    result = with_client(fn)
    assert not result.is_error, _texts([result])
    assert json.loads(_texts([result])[0]) == {
        "project": "P",
        "prompt": "a boat",
        "characters": ["E"],
        "media_ids": ["M"],
        "model": "omni-flash",
        "aspect": "16:9",
        "job_id": "job-character",
        "out_dir": str(tmp_path),
        "dry_run": False,
        "duration": 10,
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


def _offline_backend(monkeypatch, frames_for, out_dir=None):
    """Swap only the browser out: capture runs the navigation, then answers with recorded rpc payloads.

    `out_dir` is for the tools that now insist their out_dir sits inside the server's out folder: a test working
    in tmp_path has to hand that tmp_path over as the out folder (review 2026-09-18)."""
    session = _OfflineSession()

    async def fake_capture(s, action, *, settle):
        await action()
        return frames_for(s.urls[-1])

    async def fake_with(self, fn):
        return await fn(session)

    monkeypatch.setattr(reader, "capture", fake_capture)
    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=out_dir or Path("out")))
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


def _media_frames():
    listing = _fixture("Zzl0ze")
    return listing, {"ngNC2": [_fixture("ngNC2")], "yBhWQ": [_fixture("yBhWQ")], "Zzl0ze": [listing]}


def _call_media(monkeypatch, **arguments):
    _, frames = _media_frames()
    _offline_backend(monkeypatch, lambda url: frames)

    async def fn(s):
        return await s.call_tool("flow_media", {"project_id": "P", **arguments})

    return with_client(fn)


def test_flow_media_keeps_only_the_kind_that_was_asked_for(monkeypatch):
    # Plan H T2. An agent building a film wants the clips, not the reference images it uploaded on the way, and
    # reading them all costs it the whole answer: measured 2026-09-17, all_versions=true weighs 122,919 characters.
    payload = _payload(_call_media(monkeypatch, all_versions=True, kind="video"))
    assert {row["kind"] for row in payload["media"]} == {"video"}
    assert {row["kind"] for row in payload["versions"]} == {"video"}
    assert [row["kind"] for row in payload["media"]].count("video") == 12
    # The totals are what stops a filtered answer from reading like the whole project.
    assert payload["media_total"] == 15 and payload["versions_total"] == 15
    assert payload["truncated"] is True


def test_flow_media_limit_keeps_the_newest_rows_and_says_it_cut(monkeypatch):
    listing, _ = _media_frames()
    newest = sorted(parsers.media(listing), key=lambda row: row["created"], reverse=True)[:3]
    payload = _payload(_call_media(monkeypatch, all_versions=True, limit=3))
    assert [row["id"] for row in payload["media"]] == [row["id"] for row in newest]
    assert len(payload["versions"]) == 3
    assert payload["media_total"] == 15 and payload["truncated"] is True
    # Review 2026-09-18: a limit that does not bite used to answer in Flow's order while the description promised
    # newest first, and Flow's order is not by age (the fixture runs 1789219778, 1757327676, 1789218813, ...).
    loose = _payload(_call_media(monkeypatch, limit=99))
    ages = [row["created"] for row in loose["media"]]
    assert ages == sorted(ages, reverse=True), ages
    assert loose["truncated"] is False and loose["media_total"] == 15


def test_flow_media_since_takes_a_date_or_an_epoch_and_drops_older_rows(monkeypatch):
    listing, _ = _media_frames()
    rows = parsers.media(listing)
    cut = 1789219000
    kept = [row["id"] for row in rows if row["created"] >= cut]
    assert 0 < len(kept) < len(rows), "fixture must hold rows on both sides of the cut"
    by_epoch = _payload(_call_media(monkeypatch, since=cut))
    assert [row["id"] for row in by_epoch["media"]] == kept
    # 2026-09-11T00:00:00Z is epoch 1789084800, the same instant in every time zone.
    zoned = _payload(_call_media(monkeypatch, since="2026-09-11T00:00:00+00:00"))
    assert [row["id"] for row in zoned["media"]] == [
        row["id"] for row in rows if row["created"] >= 1789084800
    ]
    # A date with no zone is this machine's own day, which is what an agent asking for today means.
    local = _payload(_call_media(monkeypatch, since="2026-09-11"))
    midnight = time.mktime((2026, 9, 11, 0, 0, 0, 0, 0, -1))
    assert [row["id"] for row in local["media"]] == [row["id"] for row in rows if row["created"] >= midnight]
    # Review 2026-09-18: 20260911 is an ISO date too, and reading it as an epoch puts it in August 1970, which
    # filters nothing and says so nowhere. A date is tried before a number, so both spellings agree.
    compact = _payload(_call_media(monkeypatch, since="20260911"))
    assert [row["id"] for row in compact["media"]] == [row["id"] for row in local["media"]]
    assert len(compact["media"]) < compact["media_total"]


def test_flow_media_brief_drops_the_fields_that_weigh_the_answer_down(monkeypatch):
    # Measured 2026-09-17 (probe scene_editor, action media): of 122,919 characters, url weighs 28,205 and prompt
    # 24,607 across 135 version rows, so cutting rows alone still left 40,234 characters for the newest 20.
    full = _payload(_call_media(monkeypatch, all_versions=True))
    brief = _payload(_call_media(monkeypatch, all_versions=True, brief=True))
    for row in brief["media"] + brief["versions"]:
        assert "url" not in row
        assert len(row.get("prompt") or "") <= 121
    assert [row["id"] for row in brief["media"]] == [row["id"] for row in full["media"]]
    assert len(json.dumps(brief)) < len(json.dumps(full))
    # The fixture redacts its urls to 42 characters, so only the prompt cut can show here; the live saving is
    # measured by the smoke's own row, on the answer that really weighs 122,919 characters.
    trimmed = [row for row in brief["media"] if (row.get("prompt") or "").endswith("…")]
    assert len(trimmed) == 9, [len(row.get("prompt") or "") for row in full["media"]]


def test_flow_media_counts_nothing_as_cut_when_no_filter_asks_for_it(monkeypatch):
    payload = _payload(_call_media(monkeypatch, all_versions=True, kind="video", limit=12))
    assert payload["truncated"] is True
    payload = _payload(_call_media(monkeypatch, all_versions=True, limit=99))
    assert payload["truncated"] is False
    assert payload["media_total"] == 15 and len(payload["media"]) == 15


def test_flow_media_refuses_a_filter_it_cannot_honour_before_opening_a_browser(monkeypatch):
    # Rule 10: a tool that takes an argument it cannot honour and answers anyway is a silent wrong answer. Each of
    # these must fail loudly instead of quietly listing everything, and each must fail before a browser is worth
    # 15 to 50 s of the agent's time (review 2026-09-18: nothing pinned where the refusal happened).
    for arguments in ({"kind": "clip"}, {"limit": 0}, {"limit": -3}, {"since": "yesterday"}):
        _, frames = _media_frames()
        session = _offline_backend(monkeypatch, lambda url, frames=frames: frames)

        async def fn(s, arguments=arguments):
            return await s.call_tool("flow_media", {"project_id": "P", **arguments})

        result = with_client(fn)
        assert result.is_error, f"{arguments} was accepted"
        text = "".join(getattr(c, "text", "") for c in result.content)
        assert any(word in text for word in ("video", "image", "positive", "date")), text
        assert session.urls == [], f"{arguments} opened a page before being refused"


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

    _offline_backend(monkeypatch, lambda url: {}, out_dir=tmp_path)
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
    # The ledger a reconcile reads has to live under the server's out folder now (review 2026-09-18), so the
    # backend is given this tmp path as its own out folder rather than the repo's.
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))

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
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
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

    def locator(self, selector):
        return self

    async def add_locator_handler(self, locator, handler, **kwargs):
        pass

    async def close(self):
        pass


def test_a_notice_the_session_dismissed_is_named_in_the_tools_answer(monkeypatch, tmp_path):
    """Plan AL: the owner lets the driver press Flow's cookie notice; a tool that did so must say it did."""
    monkeypatch.setattr(
        mcp_server,
        "FlowSession",
        lambda profile: FlowSession(profile_dir=tmp_path, client_factory=_BrowserlessClient),
    )
    backend = mcp_server.Backend()
    said = "flow.google.com uses cookies from Google ... OK, got it"

    async def pressed_it(session):
        session.notices.append(said)
        return {"balance": 75}

    async def saw_none(session):
        return {"balance": 75}

    async def answers_a_list(session):
        session.notices.append(said)
        return [{"id": "P"}]

    assert asyncio.run(backend._with(pressed_it)) == {"balance": 75, "dismissed_notices": [said]}
    assert asyncio.run(backend._with(saw_none)) == {"balance": 75}
    # A list answer has no room for it, and Flow shows the notice once: the next answer that can carry it does
    # (review of plan AL, 2026-10-02: a press met by flow_projects went unreported for good).
    assert asyncio.run(backend._with(answers_a_list)) == [{"id": "P"}]
    assert asyncio.run(backend._with(saw_none)) == {"balance": 75, "dismissed_notices": [said]}
    assert asyncio.run(backend._with(saw_none)) == {"balance": 75}


def _browserless_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_server,
        "FlowSession",
        lambda profile: FlowSession(profile_dir=tmp_path, client_factory=_BrowserlessClient),
    )
    return mcp_server.Backend()


def test_a_call_that_fails_still_says_which_notice_the_driver_pressed(monkeypatch, tmp_path):
    backend = _browserless_backend(monkeypatch, tmp_path)
    said = "flow.google.com uses cookies from Google ... OK, got it"

    async def pressed_then_failed(session):
        session.notices.append(said)
        raise RuntimeError("Start generation was clicked, so credits may already be spent")

    async def saw_none(session):
        return {"balance": 75}

    with pytest.raises(RuntimeError) as failed:
        asyncio.run(backend._with(pressed_then_failed))
    # The error's own words stay first and untouched: an agent reads at most the head of it.
    assert str(failed.value) == "Start generation was clicked, so credits may already be spent"
    assert failed.value.__notes__ == [
        f"the driver pressed Flow's cookie notice (a later answer names it again): {said}"
    ]
    # Scoped re-review of plan AL, 2026-10-02: an error can be swallowed by a retry (scene_download) or by a step
    # that must not fail (putting Agent mode back), so a failure never spends the press: an answer does.
    assert asyncio.run(backend._with(saw_none)) == {"balance": 75, "dismissed_notices": [said]}
    assert asyncio.run(backend._with(saw_none)) == {"balance": 75}


def test_a_call_cancelled_after_a_press_leaves_the_press_for_the_next_answer(monkeypatch, tmp_path):
    # Round two of the scoped re-review: a client whose read timeout runs out cancels the call, which answers
    # nobody; the notice is gone from then on, so the press it made would be told to no one.
    backend = _browserless_backend(monkeypatch, tmp_path)
    said = "flow.google.com uses cookies from Google ... OK, got it"

    async def pressed_then_cancelled(session):
        session.notices.append(said)
        raise asyncio.CancelledError

    async def saw_none(session):
        return {"balance": 75}

    async def answers_a_list(session):
        session.notices.append("an earlier notice")
        return [{"id": "P"}]

    # A press an earlier list answer left behind is kept beside the cancelled call's own.
    assert asyncio.run(backend._with(answers_a_list)) == [{"id": "P"}]
    with pytest.raises(asyncio.CancelledError) as cancelled:
        asyncio.run(backend._with(pressed_then_cancelled))
    assert not getattr(cancelled.value, "__notes__", [])
    assert asyncio.run(backend._with(saw_none)) == {
        "balance": 75,
        "dismissed_notices": ["an earlier notice", said],
    }


def test_a_press_no_answer_has_carried_yet_is_named_by_the_next_call_that_fails(monkeypatch, tmp_path):
    backend = _browserless_backend(monkeypatch, tmp_path)
    said = "flow.google.com uses cookies from Google ... OK, got it"

    async def answers_a_list(session):
        session.notices.append(said)
        return [{"id": "P"}]

    async def fails(session):
        raise LookupError("no project 'P' in the grid")

    assert asyncio.run(backend._with(answers_a_list)) == [{"id": "P"}]
    with pytest.raises(LookupError) as failed:
        asyncio.run(backend._with(fails))
    assert failed.value.__notes__ == [
        f"the driver pressed Flow's cookie notice (a later answer names it again): {said}"
    ]


@pytest.mark.parametrize("fails", [False, True])
def test_what_a_press_still_in_flight_leaves_is_read_after_it(monkeypatch, tmp_path, fails):
    # Scoped re-review of plan AL, 2026-10-02: the action that met the notice can time out while the handler is
    # still pressing; the failing call then read the session's lists before the handler wrote, and the reason for
    # the failure reached only the server's stderr.
    said = "flow.google.com uses cookies from Google ... OK, got it"
    left = "Flow's cookie notice did not go away (TimeoutError, its button was not pressed): 'uses cookies'"

    class _PressingSession(FlowSession):
        async def notices_settled(self):
            self.notices.append(said)
            self.unpressed.append(left)

    monkeypatch.setattr(
        mcp_server,
        "FlowSession",
        lambda profile: _PressingSession(profile_dir=tmp_path, client_factory=_BrowserlessClient),
    )
    backend = mcp_server.Backend()

    async def call(session):
        if fails:
            raise PlaywrightTimeoutError("Locator.click: Timeout 8000ms exceeded.")
        return {"balance": 75}

    if fails:
        with pytest.raises(PlaywrightTimeoutError) as failed:
            asyncio.run(backend._with(call))
        assert failed.value.__notes__ == [
            f"the driver pressed Flow's cookie notice (a later answer names it again): {said}",
            left,
        ]
    else:
        assert asyncio.run(backend._with(call)) == {
            "balance": 75,
            "dismissed_notices": [said],
            "notices_left_standing": [left],
        }


def _generation_over_real_sessions(
    monkeypatch, tmp_path, *, presses_in, chip_times_out=False, left=None, run_fails=False
):
    """Backend.generate with its real Agent step and the real Backend._with over browserless sessions. The cookie
    notice is pressed in the session `presses_in` names: 'agent off', 'agent back on' or 'image listing'."""
    said = "flow.google.com uses cookies from Google ... OK, got it"

    async def fake_set_mode(session, project_id, enabled):
        if presses_in == ("agent back on" if enabled else "agent off"):
            session.notices.append(said)
        if left and not enabled:
            session.unpressed.append(left)
        if chip_times_out:
            raise PlaywrightTimeoutError(
                'Locator.click: Timeout 8000ms exceeded.\nCall log:\n  - waiting for locator("button.agent-mode-chip")'
            )
        return {"enabled": enabled, "was": not enabled, "panel_closed": False, "rpcids": []}

    async def fake_run_job(job, out_dir, **kwargs):
        if run_fails:
            raise RuntimeError("gflow broke")
        return {"job_id": job.job_id, "outputs": [{"media_id": "wf-1", "path": "out/x.png"}]}

    async def fake_project(session, project_id, **kwargs):
        if presses_in == "image listing":
            session.notices.append(said)
        return {"media": [{"id": "m-1", "workflow_id": "wf-1"}]}

    for name, fn in REAL_AGENT.items():
        monkeypatch.setattr(mcp_server.Backend, name, fn)
    monkeypatch.setattr(
        mcp_server,
        "FlowSession",
        lambda profile: FlowSession(profile_dir=tmp_path, client_factory=_BrowserlessClient),
    )
    monkeypatch.setattr(mcp_server.agent_mod, "set_mode", fake_set_mode)
    monkeypatch.setattr(mcp_server.gen_mod, "run_job", fake_run_job)
    monkeypatch.setattr(mcp_server.reader, "project", fake_project)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
    return said


@pytest.mark.parametrize(
    ("tool", "presses_in"),
    [
        ("gen_t2i", "agent off"),
        ("gen_t2i", "agent back on"),
        ("gen_t2i", "image listing"),
        ("gen_t2v", "agent off"),
        ("gen_t2v", "agent back on"),
    ],
)
def test_a_press_met_in_a_session_the_agent_never_sees_is_named_by_the_tools_own_answer(
    monkeypatch, tmp_path, tool, presses_in
):
    # Scoped re-review of plan AL, 2026-10-02: the tools that run gflow open sessions of their own first (Agent
    # mode off and back on, the listing read for an image's media id). Their answers stay inside the server, and
    # the first notice of a fresh profile, met there, was handed to one of them and never reported. The video
    # tools are the ones that pay, and they read no listing (round two of that re-review).
    said = _generation_over_real_sessions(monkeypatch, tmp_path, presses_in=presses_in)

    result = _call(tool, {"prompt": "a cup", "project": "P", "job_id": f"carry-{tool}-1"})

    assert not result.is_error, _texts([result])
    answer = _payload(result)
    assert answer["dismissed_notices"] == [said], answer
    assert answer["agent_mode_restored"] is True
    assert answer["outputs"][0]["media_id"] == ("m-1" if tool == "gen_t2i" else "wf-1")


@pytest.mark.parametrize("presses_in", ["agent off", "agent back on"])
def test_a_generation_gflow_failed_names_the_press_of_its_own_sessions(monkeypatch, tmp_path, presses_in):
    # The agent may stop at this error, and the press would then be told to nobody.
    said = _generation_over_real_sessions(monkeypatch, tmp_path, presses_in=presses_in, run_fails=True)

    result = _call("gen_t2i", {"prompt": "a cup", "project": "P"})

    text = _texts([result])[0]
    assert result.is_error and text.index("gflow broke") < text.index(said), text


def test_a_notice_over_the_agent_chip_is_named_beside_the_advice_about_the_chip(monkeypatch, tmp_path):
    # The Agent step raises its own advice FROM the timeout that carries the note, so the note sits one cause down.
    # The notice's words are the real ones, 164 characters: gflow's redaction cuts what the agent reads at 500
    # (gflow_cli/data/redaction.py), so the reason has to come before the words it quotes.
    notice = (
        "flow.google.com uses cookies from Google to deliver and enhance the quality of its services "
        "and to analyze traffic. Learn more OK, got it"
    )
    left = f"Flow's cookie notice shows 2 accept buttons, not one; nothing was clicked: {notice!r}"
    said = _generation_over_real_sessions(
        monkeypatch, tmp_path, presses_in="nowhere", chip_times_out=True, left=left
    )

    result = _call("gen_t2v", {"prompt": "a cup", "project": "P", "job_id": "covered-chip-1"})

    text = _texts([result])[0]
    assert result.is_error and "prompt bar" in text and "nothing was spent" in text.lower(), text
    assert "Flow's cookie notice shows 2 accept buttons, not one; nothing was clicked" in text, text
    assert text.index("prompt bar") < text.index("2 accept buttons") and "Call log" not in text, text
    assert said not in text


def test_a_film_fetched_at_the_second_try_still_names_the_press_of_the_first(monkeypatch, tmp_path):
    backend = _browserless_backend(monkeypatch, tmp_path)
    backend.out_dir = tmp_path
    said = "flow.google.com uses cookies from Google ... OK, got it"
    tries = []

    async def fake_download(session, project_id, scene_id, *, out_dir):
        tries.append(scene_id)
        if len(tries) == 1:
            session.notices.append(said)
            raise RuntimeError("Target page, context or browser has been closed")
        return {"path": str(out_dir / "film.mp4")}

    monkeypatch.setattr(mcp_server.scenes_mod, "download", fake_download)

    film = asyncio.run(backend.scene_download("P", "S"))

    assert film["attempts"] == 2 and film["dismissed_notices"] == [said], film


def test_a_notice_the_driver_could_not_press_is_named_beside_whatever_failed_after_it(monkeypatch, tmp_path):
    # Review of plan AL, 2026-10-02: the handler's own error never reaches a caller, so the session keeps what it
    # could not press and the failing call carries it.
    backend = _browserless_backend(monkeypatch, tmp_path)
    left = "Flow's cookie notice shows 2 accept buttons, not one; nothing was clicked: 'uses cookies'"

    async def left_standing_then_failed(session):
        session.unpressed.append(left)
        raise TimeoutError("Locator.click: Timeout 8000ms exceeded.")

    async def left_standing_and_read_anyway(session):
        session.unpressed.append(left)
        return {"balance": 75}

    with pytest.raises(TimeoutError) as failed:
        asyncio.run(backend._with(left_standing_then_failed))
    assert failed.value.__notes__ == [left]
    assert asyncio.run(backend._with(left_standing_and_read_anyway)) == {
        "balance": 75,
        "notices_left_standing": [left],
    }


def test_the_agent_is_shown_what_a_failed_call_noted_about_flows_notice(monkeypatch, tmp_path):
    left = "Flow's cookie notice shows 2 accept buttons, not one; nothing was clicked: 'uses cookies'"
    failure = TimeoutError("Locator.click: Timeout 8000ms exceeded.\nCall log:\n  - cookie: SID=sid-secret")
    failure.add_note(left)

    text = _error_text_of_a_failing_generation(monkeypatch, tmp_path, failure)

    assert text.index("Timeout 8000ms exceeded") < text.index("2 accept buttons"), text
    assert "sid-secret" not in text and "Call log" not in text, text


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
        def locator(self, selector):
            return self

        async def add_locator_handler(self, locator, handler, **kwargs):
            pass

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
    phrases = ("at the end", "position", "clip_id", "scene_clips", "never add it again", "answered", "Free.")
    for phrase in phrases:
        assert phrase in text, phrase
    assert "changed" not in text and "usually" not in text


def test_the_measured_price_a_bill_is_judged_against_is_the_one_the_tool_promises():
    """Two copies of a price drift, and the one that drifts is the one nobody is looking at: `clips.MEASURED_PRICE`
    decides whether a bill gets flagged, while the tool description is what an agent reads before spending. This
    reads BOTH sides, the table and the descriptions the server is serving right now, and pins the mapping so a
    new paid kind cannot be added to the table without a description that states its price."""
    from video.flow import clips

    tool_of = {"extend": "clip_extend", "edit": "clip_edit", "agent": "agent_send"}
    assert sorted(tool_of) == sorted(clips.MEASURED_PRICE), (
        f"a kind in the price table has no tool to check it against: {sorted(clips.MEASURED_PRICE)}"
    )
    served = {name: (tool.description or "") for name, tool in served_tool_objects().items()}

    for kind, price in clips.MEASURED_PRICE.items():
        description = served[tool_of[kind]]
        # A plain substring let "0 credit" hide inside "10 credits" and "20 credits" (review of plan P).
        assert re.search(rf"(?<!\d){price} credits?\b", description), (
            f"{tool_of[kind]} never states the measured {price}: {description[:160]}"
        )


def test_the_mcp_generate_path_spends_on_the_profile_it_reads_the_balance_on(monkeypatch, tmp_path):
    """The tool has one profile (`Backend.profile`); the job it hands over must carry it, so gflow's spend and the
    balance reads bracketing it land on the same account (plan R, 2026-09-28)."""
    handed = {}

    async def fake_run_job(job, out_dir, **kwargs):
        handed.update(kwargs)
        return {"job_id": job.job_id, "outputs": []}

    monkeypatch.setattr(mcp_server.gen_mod, "run_job", fake_run_job)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(profile="acc2", out_dir=tmp_path))

    async def fn(session):
        return await session.call_tool("gen_t2i", {"prompt": "a boat", "project": "P", "job_id": "job-r2"})

    with_client(fn)

    assert handed.get("profile") == "acc2", handed
    assert "read_credits" not in handed, "a second, separate profile for the reads is how the two drift apart"


def _dummy_value(prop):
    kind = prop.get("type") or next(
        (option.get("type") for option in prop.get("anyOf", []) if option.get("type") != "null"), "string"
    )
    if "enum" in prop:
        return prop["enum"][0]
    return {"string": "x", "integer": 1, "number": 1, "boolean": False, "array": ["x"]}.get(kind, "x")


def _call_every_tool_with(monkeypatch, typo):
    """Every served tool, called with its required arguments filled from its own served schema plus one typo, with
    every backend access recorded (not raised, which a swallowed exception would hide)."""
    touched = []

    class _Recorder:
        def __getattr__(self, name):
            touched.append(name)
            raise AssertionError(name)

    monkeypatch.setattr(mcp_server, "backend", _Recorder())
    tools = served_tool_objects()
    assert len(tools) >= 40, sorted(tools)

    async def fn(session):
        answers = {}
        for name, tool in sorted(tools.items()):
            schema = tool.input_schema or {}
            args = {key: _dummy_value(schema["properties"][key]) for key in schema.get("required", [])}
            args.update(typo)
            result = await session.call_tool(name, args)
            answers[name] = (result.is_error, "".join(getattr(c, "text", "") for c in result.content))
        return answers

    return with_client(fn), touched


def test_a_valid_call_with_one_mistyped_argument_is_refused_before_any_backend_runs(monkeypatch):
    """Measured 2026-09-28 (plan R, job plan-r-t2i-2): gen_t2i took an `out_dir` it does not declare, succeeded, and
    wrote its row elsewhere; every tool swallowed unknown arguments. The case that costs money is a CORRECT call with
    one typo beside it (`aspect_ratio` for `aspect` pays for the default 9:16), so that is what is sent here, to all
    44 tools served, generated from their own schemas (review of plan S: a typo sent alone never reached a tool that
    has required arguments, so a check that refused only all-unknown calls stayed green)."""
    answers, touched = _call_every_tool_with(monkeypatch, {"aspect_ratio_typo": "16:9"})

    bad = {n: text[:160] for n, (err, text) in answers.items() if not (err and "aspect_ratio_typo" in text)}
    assert bad == {}, bad
    assert all("unknown argument" in text for _, text in answers.values())
    assert touched == [], f"a refused call still reached the backend: {touched}"


def test_a_mistyped_argument_set_to_null_is_refused_too(monkeypatch):
    """A null typo changes nothing today, but a check that skips null values would pass a typo the day its value is
    filled in by a caller's default."""
    answers, touched = _call_every_tool_with(monkeypatch, {"aspect_ratio_typo": None})

    bad = sorted(n for n, (err, text) in answers.items() if not (err and "aspect_ratio_typo" in text))
    assert bad == [], bad
    assert touched == [], touched


def test_gen_character_refuses_a_length_its_model_does_not_offer(monkeypatch, tmp_path):
    """Refused at the tool, before a browser: veo-lite shows no duration group in this composer (2026-09-28)."""

    class _Untouchable:
        def __getattr__(self, name):
            raise AssertionError(f"the backend ran ({name})")

    monkeypatch.setattr(mcp_server, "backend", _Untouchable())

    async def fn(session):
        result = await session.call_tool(
            "gen_character",
            {
                "project": "P",
                "prompt": "p",
                "characters": ["E"],
                "model": "veo-lite",
                "duration": 10,
                "job_id": "j",
            },
        )
        return result.is_error, "".join(getattr(c, "text", "") for c in result.content)

    is_error, text = with_client(fn)
    assert is_error and "duration must be one of" in text and "veo-lite" in text, text
    assert "unknown argument" not in text, "refused as an undeclared argument, not by the length rule"


def test_every_tool_that_checks_its_bill_against_a_measured_price_says_what_balance_moved_means():
    """Plan W. clips.MEASURED_PRICE decides which tools answer `balance_moved`; an agent reading only the
    description never learned that key exists, so it could not tell a price change from a normal answer."""
    from video.flow import clips

    tools = served_tool_objects()
    checked = {name for kind in clips.MEASURED_PRICE for name in SPENDING_TOOLS if kind in name.split("_")}
    assert checked == {"clip_extend", "clip_edit", "agent_send"}, checked
    assert [name for name in sorted(checked) if "balance_moved" not in (tools[name].description or "")] == []


def test_agent_send_does_not_pin_a_count_of_measured_sends_that_goes_stale():
    # The description said "3 measured sends" while the ledgers held 5 (2026-09-28); a count typed into prose
    # drifts the day the next send is measured.
    assert not re.search(r"\b\d+ measured sends\b", served_tool_objects()["agent_send"].description or "")


def test_gen_character_takes_project_images_alone_for_the_one_ten_second_reference_run(monkeypatch, tmp_path):
    """Plan X. gflow refuses r2v at any length but 8 s, so this driver is the only way to 10 s, and it demanded a
    character even when the agent only had images."""
    _spending_backend(monkeypatch, tmp_path)
    arguments = {key: value for key, value in SPEND_CALLS["gen_character"].items() if key != "characters"}
    arguments |= {"media_ids": ["M"], "duration": 10}

    async def fn(s):
        return await s.call_tool("gen_character", arguments)

    result = with_client(fn)
    assert not result.is_error, _texts([result])
    forwarded = json.loads(_texts([result])[0])
    assert forwarded["characters"] == [] and forwarded["media_ids"] == ["M"], forwarded


def test_clip_download_says_4k_is_greyed_out_on_this_account_and_refused_before_any_click():
    # Plan Y, probe 2026-09-29: the Download menu renders `4K Upscaled` disabled on this Pro account.
    download = served_tool_objects()["clip_download"].description
    assert "greyed out" in download and "refused before" in download, download
    assert "may spend" not in download, download


def test_clip_extend_tells_the_agent_to_fetch_the_new_clip_through_its_scene():
    # Plan Z, measured 2026-09-29: the extension lives in a new scene; flow_download got HTTP 400 on both renditions,
    # clip_download waited 60 s for an editor that never opened, and scene_download fetched the film.
    extend = served_tool_objects()["clip_extend"].description
    assert "scene_download" in extend and "HTTP 400" in extend, extend


REAL_AGENT = {
    name: mcp_server.Backend.__dict__[name]
    for name in ("_agent_off", "_agent_restore")
    if name in mcp_server.Backend.__dict__
}


def _agent_world(monkeypatch, tmp_path, *, chip_states, run_fails=False, chip_missing=False):
    """The real Agent step over a fake set_mode: chip_states is what each set_mode call leaves the chip at."""
    log = []
    states = iter(chip_states)

    async def fake_with(self, fn):
        return await fn(object())

    async def fake_set_mode(session, project_id, enabled):
        if chip_missing:
            raise PlaywrightTimeoutError(chip_missing)
        was, now = next(states)
        log.append(("set_mode", enabled, was, now))
        return {"enabled": now, "was": was, "panel_closed": False, "rpcids": []}

    async def fake_run_job(job, out_dir, **kwargs):
        log.append(("run_job", job.kind))
        if run_fails:
            raise RuntimeError("gflow broke")
        return {"job_id": job.job_id, "outputs": []}

    for name, fn in REAL_AGENT.items():
        monkeypatch.setattr(mcp_server.Backend, name, fn)
    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.agent_mod, "set_mode", fake_set_mode)
    monkeypatch.setattr(mcp_server.gen_mod, "run_job", fake_run_job)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
    return log


def _call(name, arguments):
    async def fn(s):
        return await s.call_tool(name, arguments)

    return with_client(fn)


def test_a_gflow_generation_turns_agent_mode_off_first_and_back_on_after(monkeypatch, tmp_path):
    """Plan AA. gflow dies with exit 25 while Flow's Agent chip is on (measured 2026-09-28), and a new user whose
    project has it on could not generate at all. The chip sits in the prompt bar next to "+"."""
    log = _agent_world(monkeypatch, tmp_path, chip_states=[(True, False), (False, True)])

    result = _call("gen_t2i", {"prompt": "a cup", "project": "P"})

    assert not result.is_error, _texts([result])
    assert log == [("set_mode", False, True, False), ("run_job", "t2i"), ("set_mode", True, False, True)], log


def test_agent_mode_that_was_off_is_left_off(monkeypatch, tmp_path):
    log = _agent_world(monkeypatch, tmp_path, chip_states=[(False, False)])

    result = _call("gen_t2v", {"prompt": "a cup", "project": "P", "job_id": "agent-off-1"})

    assert not result.is_error, _texts([result])
    assert log == [("set_mode", False, False, False), ("run_job", "t2v")], log


def test_agent_mode_that_stays_on_stops_the_call_before_gflow_runs(monkeypatch, tmp_path):
    log = _agent_world(monkeypatch, tmp_path, chip_states=[(True, True)])

    result = _call("gen_t2v", {"prompt": "a cup", "project": "P", "job_id": "agent-stuck-1"})

    assert result.is_error and "Agent" in _texts([result])[0], _texts([result])
    assert ("run_job", "t2v") not in log
    assert not (tmp_path / "ledger.jsonl").exists()


def test_agent_mode_is_put_back_even_when_gflow_fails(monkeypatch, tmp_path):
    log = _agent_world(monkeypatch, tmp_path, chip_states=[(True, False), (False, True)], run_fails=True)

    result = _call("gen_t2i", {"prompt": "a cup", "project": "P"})

    assert result.is_error and "gflow broke" in _texts([result])[0], _texts([result])
    assert log[-1] == ("set_mode", True, False, True), log


def test_a_missing_agent_chip_says_where_the_chip_lives(monkeypatch, tmp_path):
    log = _agent_world(
        monkeypatch,
        tmp_path,
        chip_states=[(False, False)],
        chip_missing='Locator.wait_for: Timeout 15000ms exceeded. waiting for locator("button.agent-mode-chip, '
        'flow-agent-mode-toggle-chip button").first to be visible',
    )

    result = _call("gen_t2v", {"prompt": "a cup", "project": "P", "job_id": "agent-missing-1"})

    text = _texts([result])[0]
    assert result.is_error and "prompt bar" in text and "nothing was spent" in text.lower(), text
    assert log == []


def test_every_tool_that_runs_gflow_says_agent_mode_is_turned_off_for_it():
    # Plan AA: the set is every tool whose call reaches Backend.generate, read from the tool functions' own source.
    import inspect

    runs_gflow = {
        name
        for name, tool in mcp_server.server._tool_manager._tools.items()
        if "_gen(" in inspect.getsource(tool.fn)
    }
    assert runs_gflow == {"gen_t2v", "gen_i2v", "gen_r2v", "gen_t2i", "gen_i2i"}, runs_gflow
    tools = served_tool_objects()
    assert [
        name for name in sorted(runs_gflow) if "Agent mode is turned off" not in tools[name].description
    ] == []


def test_a_project_page_that_never_loads_is_not_blamed_on_the_agent_chip(monkeypatch, tmp_path):
    """Review of plan AA, e1: a wrong project id times out on the project page, not on the chip."""
    _agent_world(
        monkeypatch,
        tmp_path,
        chip_states=[(False, False)],
        chip_missing='Locator.wait_for: Timeout 60000ms exceeded. waiting for locator("flow-project-page")',
    )

    result = _call("gen_t2v", {"prompt": "a cup", "project": "P", "job_id": "agent-no-page-1"})

    text = _texts([result])[0]
    assert result.is_error and "flow-project-page" in text and "prompt bar" not in text, text


def test_gen_video_describes_every_model_in_the_surveyed_options_file():
    # Plan AB: the description is written from flow_options.json, so a model the survey adds shows up by itself.
    from video.flow import video

    description = served_tool_objects()["gen_video"].description
    missing = [
        name
        for name, entry in video.VIDEO["models"].items()
        if name not in description or entry["label"] not in description
    ]
    assert missing == [], missing
    assert "max_credits" in description and "dry_run" in description


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"max_credits": None}, "max_credits is required"),
        ({"max_credits": 0}, "max_credits must be at least 1"),
        ({"model": "veo-lite", "duration": 10}, "no length choice"),
        ({"resolution": "1080p"}, "resolution must be one of"),
        ({"count": 5}, "count must be one of"),
        ({"start_frame": "S", "media_ids": ["M"]}, "either frames or ingredients"),
        ({"end_frame": "E"}, "end_frame needs a start_frame"),
        ({"media_ids": [" "]}, "must not be blank"),
        ({"media_ids": ["M"], "voices": [" "]}, "must not be blank"),
        ({"start_frame": "S", "voices": ["Achird"]}, "either frames or ingredients"),
        # Flow's own refusal of a voice with nothing beside it (measured 2026-10-01, out/al/t5.json).
        ({"voices": ["Achird"]}, "An audio ingredient requires other ingredients to function."),
    ],
)
def test_gen_video_refuses_what_it_cannot_run_before_a_browser_opens(monkeypatch, tmp_path, change, message):
    reached = _spending_backend(monkeypatch, tmp_path)
    arguments = {k: v for k, v in (SPEND_CALLS["gen_video"] | change).items() if v is not None}

    result = _call("gen_video", arguments)

    text = _texts([result])[0]
    assert result.is_error and message in text, text
    assert reached == []


def test_gen_video_dry_run_needs_neither_a_job_id_nor_a_cap(monkeypatch, tmp_path):
    reached = _spending_backend(monkeypatch, tmp_path)

    result = _call("gen_video", {"prompt": "a boat", "project": "P", "dry_run": True})

    assert not result.is_error, _texts([result])
    assert reached == ["browser", ("gen_video", None)]


def test_gen_video_forwards_every_option_to_the_driver(monkeypatch, tmp_path):
    _spending_backend(monkeypatch, tmp_path)
    arguments = SPEND_CALLS["gen_video"] | {
        "model": "omni-flash",
        "aspect": "16:9",
        "resolution": "360p",
        "duration": 6,
        "count": 3,
        "start_frame": "S",
        "end_frame": "E",
    }

    result = _call("gen_video", arguments)

    assert not result.is_error, _texts([result])
    got = json.loads(_texts([result])[0])
    assert {
        k: got[k] for k in ("model", "aspect", "resolution", "duration", "count", "start_frame", "end_frame")
    } == {
        "model": "omni-flash",
        "aspect": "16:9",
        "resolution": "360p",
        "duration": 6,
        "count": 3,
        "start_frame": "S",
        "end_frame": "E",
    }
    assert got["max_credits"] == 20 and got["job_id"] == "job-video" and got["out_dir"] == str(tmp_path)


def test_gen_video_forwards_the_voices_beside_the_ingredients(monkeypatch, tmp_path):
    _spending_backend(monkeypatch, tmp_path)
    arguments = SPEND_CALLS["gen_video"] | {"media_ids": ["M"], "voices": ["Achird", "LilyVoice"]}

    result = _call("gen_video", arguments)

    assert not result.is_error, _texts([result])
    got = json.loads(_texts([result])[0])
    assert got["voices"] == ["Achird", "LilyVoice"] and got["media_ids"] == ["M"]


def test_the_tools_say_what_flow_does_with_an_input_it_would_drop_or_ignore():
    # Plan AL, G4, each measured on 2026-10-01: a character beside frames is dropped from the request (ak-a3); an Omni
    # edit keeps the clip's audio and burns new words in as a subtitle (ak-n1-edit-line); an extension is a 7 s clip,
    # so the film runs 8 + 7 = 15.0 s (ak-x1).
    served = served_tool_objects()
    assert "without the character" in served["gen_video"].description
    edit = served["clip_edit"].description
    assert "cannot change what is said" in edit and "subtitle" in edit
    extend = served["clip_extend"].description
    assert "8 + 7" in extend and "overlapping" not in extend


def test_gen_video_tells_where_voices_come_from_and_what_each_model_takes():
    description = served_tool_objects()["gen_video"].description
    assert "voices" in description and "flow_voices" in description
    # The caps read off the composer on 2026-10-01, written from the table the driver refuses by.
    assert ingredients.voice_caps_text() in description


def _image_world(monkeypatch, tmp_path, listing):
    """gflow answers an image's workflow id as its media_name (measured 2026-09-30: gen_i2i said 68211a91..., the
    listing holds that image as media 9e41c03f... under workflow 68211a91...)."""
    reads = []

    async def fake_run_job(job, out_dir, **kwargs):
        return {"job_id": job.job_id, "outputs": [{"media_id": "WF-1", "path": "out/WF-1_1.jpg"}]}

    async def fake_with(self, fn):
        return await fn(object())

    async def fake_project(session, project_id, settle=10.0, *, versions=False):
        reads.append(project_id)
        return {"meta": {}, "models": [], "media": listing(len(reads))}

    monkeypatch.setattr(mcp_server.gen_mod, "run_job", fake_run_job)
    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.reader, "project", fake_project)
    monkeypatch.setattr(mcp_server, "IMAGE_LISTING_POLL_S", 0, raising=False)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
    return reads


_IMAGE_CALLS = {
    "gen_t2i": {"prompt": "a boat", "project": "P", "job_id": "job-t2i"},
    "gen_i2i": {"refs": ["/tmp/a.png"], "prompt": "a boat", "project": "P", "job_id": "job-i2i"},
}


@pytest.mark.parametrize("tool", sorted(_IMAGE_CALLS))
def test_an_image_answers_the_media_id_gen_video_takes_not_its_workflow(monkeypatch, tmp_path, tool):
    image = {"id": "MEDIA-1", "workflow_id": "WF-1", "kind": "image", "title": "Woman posing in bedroom"}
    # The listing trails a fresh image (scenes: LISTING_LAG_MS), so the first read misses it.
    reads = _image_world(monkeypatch, tmp_path, lambda n: [image] if n > 1 else [])

    result = with_client(lambda session: session.call_tool(tool, _IMAGE_CALLS[tool]))

    assert not result.is_error, _texts([result])
    out = json.loads(_texts([result])[0])["outputs"][0]
    assert (out["media_id"], out["workflow_id"]) == ("MEDIA-1", "WF-1"), out
    assert reads == ["P", "P"], reads


def test_an_image_the_listing_never_shows_gets_no_media_id_rather_than_its_workflow(monkeypatch, tmp_path):
    _image_world(monkeypatch, tmp_path, lambda n: [])

    result = with_client(lambda session: session.call_tool("gen_i2i", _IMAGE_CALLS["gen_i2i"]))

    assert not result.is_error, _texts([result])
    out = json.loads(_texts([result])[0])["outputs"][0]
    assert out["media_id"] is None and out["workflow_id"] == "WF-1", out
    assert "flow_upload" in out["media_id_note"], out


def test_an_image_whose_listing_read_fails_still_answers_its_file(monkeypatch, tmp_path):
    """Review of plan AF: the listing is read after run_job wrote `done`, and the ledger refuses the job id again, so
    an error here would lose a finished image's path for good."""
    _image_world(monkeypatch, tmp_path, lambda n: [])

    async def broken(session, project_id, settle=10.0, *, versions=False):
        raise RuntimeError("no Zzl0ze frame")

    monkeypatch.setattr(mcp_server.reader, "project", broken)

    result = with_client(lambda session: session.call_tool("gen_i2i", _IMAGE_CALLS["gen_i2i"]))

    assert not result.is_error, _texts([result])
    out = json.loads(_texts([result])[0])["outputs"][0]
    assert (out["media_id"], out["workflow_id"], out["path"]) == (None, "WF-1", "out/WF-1_1.jpg"), out
    assert "Zzl0ze" in out["media_id_note"], out


# Plan AM: every spending tool says what the job came to, read off the rows its driver wrote.

VIDEO_CALL = {"project": "P", "prompt": "a cup on a table", "max_credits": 10}
AUDIO = {"statuses": [6, 2, 4], "reasons": ["PUBLIC_ERROR_AUDIO_FILTERED"]}


def _video_world(monkeypatch, tmp_path, rows, *, fails=None, answer=None):
    """gen_video over the real Backend, with a driver that writes `rows` to the job's ledger and then answers or
    fails. No session opens."""

    async def fake_with(self, fn):
        return await fn(object())

    reached = []

    async def fake_generate(session, project, *, prompt, out_dir, job_id=None, **options):
        reached.append({"project": project, "prompt": prompt, "job_id": job_id, **options})
        ledger = gen.Ledger(out_dir / "ledger.jsonl")
        for status, fields in rows:
            ledger.append(job_id, status, **fields)
        if fails is not None:
            raise fails
        return dict(answer if answer is not None else {"job_id": job_id, "path": "out/x.mp4"})

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.video_mod, "generate", fake_generate)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
    return reached


def _submitted(**more):
    fields = {"kind": "video", "project": "P", "prompt": "a cup on a table", "credits_before": 100}
    return ("submitted", {**fields, **more})


def test_a_paid_answer_says_what_the_job_came_to(monkeypatch, tmp_path):
    done = ("done", {"spent": 10, "credits_before": 100, "credits_after": 90})
    _video_world(monkeypatch, tmp_path, [_submitted(), done])

    answer = _payload(_call("gen_video", {**VIDEO_CALL, "job_id": "am-done-1"}))

    assert answer["outcome"]["code"] == "DONE" and answer["outcome"]["charged"] == 10
    assert answer["outcome"]["retryable"] is False and answer["path"] == "out/x.mp4"


def test_an_answer_whose_job_left_no_row_carries_no_outcome(monkeypatch, tmp_path):
    # A driver that answered and wrote nothing is not a job this server can vouch for: it says nothing, not
    # "nothing was submitted" beside a file.
    _video_world(monkeypatch, tmp_path, [])

    answer = _payload(_call("gen_video", {**VIDEO_CALL, "job_id": "am-no-row-1"}))

    assert "outcome" not in answer


def test_a_refusal_flow_did_not_charge_for_leads_the_error_with_its_code(monkeypatch, tmp_path):
    failed = ("failed", {"spent": 0, "credits_before": 100, "credits_after": 100, "flow": AUDIO})
    _video_world(
        monkeypatch,
        tmp_path,
        [_submitted(), failed],
        fails=RuntimeError("Flow refused this job and charged nothing: do not retry the same inputs"),
    )

    result = _call("gen_video", {**VIDEO_CALL, "job_id": "am-audio-1"})

    text = _texts([result])[0]
    assert result.is_error
    assert text.startswith(
        "Error executing tool gen_video: outcome code=AUDIO_FILTERED charged=0 retryable=yes; RuntimeError: Flow refused"
    ), text
    said = result.structured_content["outcome"]
    assert (said["code"], said["charged"], said["retryable"]) == ("AUDIO_FILTERED", 0, True)
    assert "retry_of" in said["advice"]


def test_a_job_refused_before_any_click_is_said_to_be_free_to_run_again(monkeypatch, tmp_path):
    _video_world(monkeypatch, tmp_path, [], fails=LookupError("the ingredients dialog offers no image"))

    result = _call("gen_video", {**VIDEO_CALL, "job_id": "am-early-1"})

    text = _texts([result])[0]
    assert result.is_error and "outcome code=NOT_SUBMITTED charged=0 retryable=no; LookupError" in text, text
    assert "same job_id" in result.structured_content["outcome"]["advice"]


def test_a_job_started_and_never_settled_is_unknown_whatever_the_error_says(monkeypatch, tmp_path):
    # I-read-2: a crash between the intent row and the outcome row is money that may be gone.
    _video_world(monkeypatch, tmp_path, [_submitted()], fails=RuntimeError("the browser has been closed"))

    result = _call("gen_video", {**VIDEO_CALL, "job_id": "am-crash-1"})

    text = _texts([result])[0]
    assert "outcome code=UNKNOWN charged=unknown retryable=no; RuntimeError" in text, text


def test_a_job_id_used_again_is_refused_with_what_the_first_run_came_to(monkeypatch, tmp_path):
    done = ("done", {"spent": 10, "credits_before": 100, "credits_after": 90})
    _video_world(monkeypatch, tmp_path, [_submitted(), done])
    first = _call("gen_video", {**VIDEO_CALL, "job_id": "am-twice-1"})
    assert not first.is_error

    again = _call("gen_video", {**VIDEO_CALL, "job_id": "am-twice-1"})

    text = _texts([again])[0]
    assert again.is_error and "outcome code=DONE charged=10 retryable=no; AlreadySubmitted" in text, text
    assert len(gen.Ledger(tmp_path / "ledger.jsonl").rows("am-twice-1")) == 2, "the second call wrote nothing"


def test_a_driver_that_finds_the_job_already_submitted_says_what_it_came_to(monkeypatch, tmp_path):
    # The drivers keep their own check: a row written between the server's sweep and the driver's start.
    done = ("done", {"spent": 10, "credits_before": 100, "credits_after": 90})
    _video_world(
        monkeypatch,
        tmp_path,
        [_submitted(), done],
        fails=gen.AlreadySubmitted("job am-race-1 is already done; delete its output to redo it"),
    )

    text = _texts([_call("gen_video", {**VIDEO_CALL, "job_id": "am-race-1"})])[0]

    head = "outcome code=DONE charged=10 retryable=no; AlreadySubmitted: that job may already have spent"
    assert head in text, text


def test_the_outcome_survives_the_cut_an_agent_reads_through(monkeypatch, tmp_path):
    # gflow's redaction keeps 500 characters of an error: the code leads, so no long error can push it out.
    failed = ("failed", {"spent": 0, "credits_before": 100, "credits_after": 100, "flow": AUDIO})
    _video_world(monkeypatch, tmp_path, [_submitted(), failed], fails=RuntimeError("x" * 900))

    text = _texts([_call("gen_video", {**VIDEO_CALL, "job_id": "am-long-1"})])[0]

    assert "outcome code=AUDIO_FILTERED charged=0 retryable=yes; RuntimeError: xxx" in text, text[:200]


def test_a_ledger_that_cannot_be_read_never_costs_a_paid_answer(monkeypatch, tmp_path):
    # The outcome is read after the driver came back: a failure there must not replace a finished, paid result.
    done = ("done", {"spent": 10, "credits_before": 100, "credits_after": 90})
    _video_world(monkeypatch, tmp_path, [_submitted(), done])

    def broken(rows):
        raise ValueError("rows nobody can read")

    monkeypatch.setattr(mcp_server.outcome_mod, "classify", broken)

    answer = _payload(_call("gen_video", {**VIDEO_CALL, "job_id": "am-unreadable-1"}))

    assert answer["path"] == "out/x.mp4" and "outcome" not in answer


def test_an_error_whose_outcome_cannot_be_read_is_unknown_and_keeps_its_own_words(monkeypatch, tmp_path):
    # Review of plan AM, S1: the descriptions say an error with no outcome line was refused before any job started,
    # so a job that ran and whose rows cannot be read must not come back without one.
    _video_world(monkeypatch, tmp_path, [_submitted()], fails=RuntimeError("credits may already be spent"))

    def broken(rows):
        raise ValueError("rows nobody can read")

    monkeypatch.setattr(mcp_server.outcome_mod, "classify", broken)

    result = _call("gen_video", {**VIDEO_CALL, "job_id": "am-unreadable-2"})

    text = _texts([result])[0]
    assert result.is_error and "credits may already be spent" in text, text
    assert "outcome code=UNKNOWN charged=unknown retryable=no; RuntimeError" in text, text


def _half_written(ledger_path):
    """A ledger whose last line was cut short, as a crash mid-write leaves it."""
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write('{"ts": 1, "job_id": "am-cut", "status": "do')


def test_a_ledger_cut_mid_line_leaves_a_failed_job_unknown_and_a_paid_answer_whole(monkeypatch, tmp_path):
    # Review of plan AM, N1: the guard on the ledger READ, not only on the classifier.
    done = ("done", {"spent": 10, "credits_before": 100, "credits_after": 90})

    async def fake_with(self, fn):
        return await fn(object())

    def driver(fails):
        async def fake_generate(session, project, *, prompt, out_dir, job_id=None, **options):
            ledger = gen.Ledger(out_dir / "ledger.jsonl")
            status, fields = _submitted()
            ledger.append(job_id, status, **fields)
            if not fails:
                ledger.append(job_id, done[0], **done[1])
            _half_written(ledger.path)
            if fails:
                raise RuntimeError("Start generation was clicked, so credits may already be spent")
            return {"job_id": job_id, "path": "out/x.mp4"}

        return fake_generate

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.video_mod, "generate", driver(fails=True))
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "a"))
    text = _texts([_call("gen_video", {**VIDEO_CALL, "job_id": "am-cut-1"})])[0]
    assert "outcome code=UNKNOWN charged=unknown retryable=no; RuntimeError: Start generation" in text, text

    monkeypatch.setattr(mcp_server.video_mod, "generate", driver(fails=False))
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path / "b"))
    answer = _payload(_call("gen_video", {**VIDEO_CALL, "job_id": "am-cut-2"}))
    assert answer["path"] == "out/x.mp4" and "outcome" not in answer


def test_a_job_run_in_a_folder_of_its_own_is_read_from_that_folder(monkeypatch, tmp_path):
    # Review of plan AM, S4: read from the out folder's own ledger, a clicked job would come back NOT_SUBMITTED.
    failed = ("failed", {"spent": 0, "credits_before": 100, "credits_after": 100, "flow": AUDIO})
    _video_world(monkeypatch, tmp_path, [_submitted(), failed], fails=RuntimeError("Flow refused this job"))
    film = tmp_path / "film-1"

    text = _texts([_call("gen_video", {**VIDEO_CALL, "job_id": "am-film-1", "out_dir": str(film)})])[0]

    assert "outcome code=AUDIO_FILTERED charged=0 retryable=yes" in text, text
    assert len(gen.Ledger(film / "ledger.jsonl").rows("am-film-1")) == 2


def test_a_job_still_running_in_another_call_is_refused_as_unknown(monkeypatch, tmp_path):
    # Review of plan AM, N3: that error had no outcome line while the id was spending in another call.
    backend = mcp_server.Backend(out_dir=tmp_path)

    async def both():
        release = asyncio.Event()

        async def slow():
            await release.wait()
            return {"job_id": "am-flight"}

        first = asyncio.create_task(backend._spend_once("am-flight", tmp_path, slow))
        await asyncio.sleep(0.01)
        with pytest.raises(gen.AlreadySubmitted) as refused:
            await backend._spend_once("am-flight", tmp_path, slow)
        release.set()
        await first
        return refused.value

    refused = asyncio.run(both())

    assert refused.outcome["code"] == "UNKNOWN" and "still running" in str(refused)


def test_the_outcome_the_server_read_leads_over_one_a_cause_carries(monkeypatch, tmp_path):
    inner = RuntimeError("an earlier error")
    inner.outcome = {"code": "DONE", "charged": 99, "retryable": False, "advice": "from elsewhere"}
    outer = RuntimeError("the ingredients dialog offers no image")
    outer.__cause__ = inner
    _video_world(monkeypatch, tmp_path, [], fails=outer)

    text = _texts([_call("gen_video", {**VIDEO_CALL, "job_id": "am-chain-1"})])[0]

    assert "outcome code=NOT_SUBMITTED charged=0" in text and "charged=99" not in text, text


def test_a_refused_retry_holds_no_slot(monkeypatch, tmp_path):
    # Review of plan AM, N5: a slot taken before the refusal would lock the job against every later retry.
    _video_world(monkeypatch, tmp_path, [_submitted(), PAID])
    _refused_earlier(tmp_path, job_id="am-first", prompt="another prompt")

    refused = _call("gen_video", {**VIDEO_CALL, "job_id": "am-second", "retry_of": "am-first"})

    assert refused.is_error and mcp_server.backend._retrying == set()


def test_one_ledger_named_two_ways_is_one_ledger(monkeypatch, tmp_path):
    # Scoped re-review of plan AM: the server's out folder is relative ("out"), an agent's out_dir may be absolute;
    # the same file under two spellings was counted as two ledgers and an honest retry was refused.
    monkeypatch.chdir(tmp_path)
    _refused_earlier(tmp_path / "out", folder="film-1")
    backend = mcp_server.Backend(out_dir=Path("out"))

    async def run():
        return {"job_id": "am-second"}

    request = {"project": "P", "prompt": "a cup on a table"}
    absolute = tmp_path / "out" / "film-1"
    answer = asyncio.run(
        backend._spend_once("am-second", absolute, run, retry_of="am-first", request=request)
    )

    assert answer == {"job_id": "am-second"}


def test_a_retry_of_a_job_that_sits_in_two_ledgers_is_refused(monkeypatch, tmp_path):
    # Review of plan AM, N2: the CLI and the story pipeline can run one job id twice, in two folders. Read in file
    # order, a paid run under an uncharged refusal would look retryable, so such a job is not vouched for at all.
    reached = _video_world(monkeypatch, tmp_path, [_submitted(), PAID])
    _refused_earlier(tmp_path, folder="a-paid", outcome_row=PAID)
    _refused_earlier(tmp_path, folder="b-refused")

    result = _call("gen_video", {**VIDEO_CALL, "job_id": "am-second", "retry_of": "am-first"})

    text = _texts([result])[0]
    assert result.is_error and "2 ledgers" in text and reached == [], text


FILTERED = ("failed", {"spent": 0, "credits_before": 100, "credits_after": 100, "flow": AUDIO})
PAID = ("done", {"spent": 10, "credits_before": 100, "credits_after": 90})


def _refused_earlier(tmp_path, job_id="am-first", outcome_row=FILTERED, folder="", **intent):
    """A job already on record: by default one Flow refused with the audio filter and did not charge for."""
    ledger = gen.Ledger(tmp_path / folder / "ledger.jsonl")
    status, fields = _submitted(**intent)
    ledger.append(job_id, status, **fields)
    if outcome_row:
        ledger.append(job_id, outcome_row[0], **outcome_row[1])


def test_a_retry_the_ledger_vouches_for_runs_and_carries_its_link_to_the_driver(monkeypatch, tmp_path):
    # Plan AM, I-money-4. The refused job sits in another ledger folder of the same out folder: the sweep finds it.
    reached = _video_world(monkeypatch, tmp_path, [_submitted(retry_of="am-first"), PAID])
    _refused_earlier(tmp_path, folder="film-1")

    answer = _payload(_call("gen_video", {**VIDEO_CALL, "job_id": "am-second", "retry_of": "am-first"}))

    assert answer["outcome"]["code"] == "DONE" and len(reached) == 1
    assert reached[0]["retry_of"] == "am-first" and reached[0]["job_id"] == "am-second"


@pytest.mark.parametrize(
    ("earlier", "said"),
    [
        ({"outcome_row": PAID}, "DONE"),
        ({"outcome_row": None}, "UNKNOWN"),
        ({"prompt": "another prompt"}, "same project and prompt"),
        ({"project": "Q"}, "same project and prompt"),
        ({"job_id": "someone-else"}, "no ledger row"),
    ],
    ids=["it finished and was paid", "it never settled", "another prompt", "another project", "no such job"],
)
def test_a_retry_the_ledger_cannot_vouch_for_is_refused_before_anything_opens(
    monkeypatch, tmp_path, earlier, said
):
    reached = _video_world(monkeypatch, tmp_path, [_submitted(), PAID])
    _refused_earlier(tmp_path, **earlier)

    result = _call("gen_video", {**VIDEO_CALL, "job_id": "am-second", "retry_of": "am-first"})

    text = _texts([result])[0]
    assert result.is_error and said in text and reached == [], text
    # Nothing ran under the new id, so it is free to use again, and the error says so in its first line.
    assert "outcome code=NOT_SUBMITTED charged=0 retryable=no" in text, text
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows("am-second") == []


def test_one_refused_job_takes_one_retry_at_a_time(monkeypatch, tmp_path):
    # Two calls naming the same refused job, the first still in flight: the second would be a second clip.
    _refused_earlier(tmp_path)
    backend = mcp_server.Backend(out_dir=tmp_path)
    started, release = [], None

    async def both():
        nonlocal release
        release = asyncio.Event()

        async def slow():
            started.append("first")
            await release.wait()
            return {"job_id": "am-second"}

        async def never():
            started.append("second")
            return {"job_id": "am-third"}

        request = {"project": "P", "prompt": "a cup on a table"}
        first = asyncio.create_task(
            backend._spend_once("am-second", tmp_path, slow, retry_of="am-first", request=request)
        )
        await asyncio.sleep(0.01)
        with pytest.raises(ValueError, match="another call is retrying"):
            await backend._spend_once("am-third", tmp_path, never, retry_of="am-first", request=request)
        release.set()
        await first
        # The first call left no submitted row here (a stub), so the slot is free again for an honest retry.
        return await backend._spend_once("am-fourth", tmp_path, never, retry_of="am-first", request=request)

    assert asyncio.run(both()) == {"job_id": "am-third"}
    assert started == ["first", "second"]


def test_a_job_cannot_be_named_as_its_own_retry(monkeypatch, tmp_path):
    reached = _video_world(monkeypatch, tmp_path, [_submitted(), PAID])

    result = _call("gen_video", {**VIDEO_CALL, "job_id": "am-self", "retry_of": "am-self"})

    assert result.is_error and "cannot be its own retry" in _texts([result])[0] and reached == []


def test_a_dry_run_takes_no_retry_of(monkeypatch, tmp_path):
    reached = _video_world(monkeypatch, tmp_path, [])
    _refused_earlier(tmp_path)

    result = _call("gen_video", {"project": "P", "prompt": "a cup", "dry_run": True, "retry_of": "am-first"})

    assert result.is_error and "dry run" in _texts([result])[0] and reached == []


def test_gen_character_takes_a_retry_the_same_way(monkeypatch, tmp_path):
    reached = []

    async def fake_with(self, fn):
        return await fn(object())

    async def fake_generate(session, project, *, prompt, out_dir, job_id=None, **options):
        reached.append({"job_id": job_id, **options})
        return {"job_id": job_id}

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server.ingredients_mod, "generate", fake_generate)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
    _refused_earlier(tmp_path, kind="character")
    call = {"project": "P", "prompt": "a cup on a table", "characters": ["e-1"]}

    ok = _call("gen_character", {**call, "job_id": "am-char-2", "retry_of": "am-first"})
    assert not ok.is_error, _texts([ok])
    assert reached[0]["retry_of"] == "am-first"

    other = _call("gen_character", {**call, "prompt": "a dog", "job_id": "am-char-3", "retry_of": "am-first"})
    assert other.is_error and "same project and prompt" in _texts([other])[0] and len(reached) == 1


def test_every_spending_description_says_how_to_read_an_outcome():
    # An agent holding nothing but this MCP has to learn from the description what the new line means.
    tools = served_tool_objects()
    for name in sorted(SPENDING_TOOLS):
        description = tools[name].description or ""
        assert "outcome {code, charged, retryable, advice}" in description, name
        assert "outcome code=" in description and "UNKNOWN" in description, name
        assert "money may be gone" in description and "no outcome line" in description, name


def test_a_description_talks_of_retry_of_exactly_when_the_tool_takes_it():
    tools = served_tool_objects()
    for name, tool in tools.items():
        takes = "retry_of" in ((tool.input_schema or {}).get("properties") or {})
        assert ("retry_of" in (tool.description or "")) is takes, name
    for name in ("gen_video", "gen_character", "job_submit"):
        description = tools[name].description
        bound = f"at most {mcp_server.outcome_mod.MAX_RETRIES}"
        assert "NEW job_id" in description and bound in description, name
        assert "AUDIO_FILTERED" in description and "NO_REASON" in description, name


def test_the_instructions_name_the_outcome_and_the_one_retry_the_server_vouches_for():
    text = mcp_server.server.instructions

    assert "outcome" in text and "retry_of" in text and "retryable=yes" in text
    # The old rule stays for everything else: no new job_id after an error or a timeout.
    assert "Never call one again under a new job_id" in text


def test_only_the_composer_tools_take_a_retry_of():
    # Read off the served schemas: retry_of is vouched for from Flow's own reason, which only the composer path hears.
    # job_submit is that path too: a refusal it heard before leaving is retried through it (re-review of plan AN).
    takes = {
        name
        for name, tool in served_tool_objects().items()
        if "retry_of" in ((tool.input_schema or {}).get("properties") or {})
    }

    assert takes == {"gen_video", "gen_character", "job_submit"}


def test_a_gflow_job_with_no_job_id_of_its_own_still_says_what_it_came_to(monkeypatch, tmp_path):
    # gen_t2i takes no job_id from the agent: the backend mints one, and the answer names it.
    async def fake_run_job(job, out_dir, **kwargs):
        ledger = gen.Ledger(out_dir / "ledger.jsonl")
        ledger.append(job.job_id, "submitted", kind=job.kind, credits_before=100)
        ledger.append(job.job_id, "done", credits_before=100, credits_after=100, spent=0)
        return {"job_id": job.job_id, "outputs": []}

    monkeypatch.setattr(mcp_server.gen_mod, "run_job", fake_run_job)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))

    answer = _payload(_call("gen_t2i", {"prompt": "a cup", "project": "P"}))

    assert (answer["outcome"]["code"], answer["outcome"]["charged"]) == ("DONE", 0)


# Plan AN: a generation submitted in one call, looked at and fetched in later ones.

STARTED = (
    "started",
    {"workflow_id": "w-job", "workflows_before": ["w-before"], "prompt": "a cup on a table"},
)
STARTED_ANSWER = {"state": "started", "workflow_id": "w-job", "quoted_credits": 10, "credits_before": 100}


def _job_backend(monkeypatch, tmp_path):
    """The real Backend over tmp_path; its sessions are a reader with no page, and `opened` counts them."""
    opened = []

    async def fake_with(self, fn):
        opened.append("browser")
        return await fn(_Reader())

    monkeypatch.setattr(mcp_server.Backend, "_with", fake_with)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))
    return opened


def test_job_submit_runs_the_video_driver_detached_and_says_the_job_is_started(monkeypatch, tmp_path):
    reached = _video_world(monkeypatch, tmp_path, [_submitted(), STARTED], answer=STARTED_ANSWER)

    call = {**VIDEO_CALL, "job_id": "an-1", "start_frame": "M", "duration": 6, "out_dir": str(tmp_path / "f")}
    answer = _payload(_call("job_submit", call))

    assert reached[0]["detach"] is True and reached[0]["job_id"] == "an-1" and reached[0]["max_credits"] == 10
    assert reached[0]["start_frame"] == "M" and reached[0]["duration"] == 6
    assert (
        reached[0].get("count", 1) == 1 and not reached[0].get("dry_run") and not reached[0].get("retry_of")
    )
    assert (answer["state"], answer["workflow_id"]) == ("started", "w-job")
    assert (answer["outcome"]["code"], answer["outcome"]["charged"]) == ("STARTED", None)
    assert "job_collect" in answer["outcome"]["advice"]
    assert len(gen.Ledger(tmp_path / "f" / "ledger.jsonl").rows("an-1")) == 2


def test_job_submit_takes_what_gen_video_takes_less_the_dry_run_and_the_count():
    # Read off the served schemas, so an option added to gen_video and forgotten here goes red.
    tools = served_tool_objects()
    blocking = set(tools["gen_video"].input_schema["properties"])
    detached = tools["job_submit"].input_schema

    assert set(detached["properties"]) == blocking - {"dry_run", "count"}
    assert {"project", "prompt", "job_id", "max_credits"} <= set(detached["required"])


@pytest.mark.parametrize(
    "change",
    [
        {"prompt": "one\ntwo"},
        {"prompt": "   "},
        {"project": " "},
        {"job_id": "  "},
        {"model": "no-such-model"},
        {"aspect": "4:3"},
        {"duration": 11},
        {"max_credits": 0},
        {"media_ids": [" "]},
        {"voices": ["Achird"]},
        {"start_frame": "M", "characters": ["E"]},
        {"retry_of": "  "},
        {"retry_of": "an-bad-1"},
    ],
    ids=lambda change: "+".join(f"{key}={value!r}" for key, value in change.items()),
)
def test_job_submit_refuses_before_a_browser_opens_what_gen_video_refuses(monkeypatch, tmp_path, change):
    reached = _video_world(monkeypatch, tmp_path, [_submitted(), STARTED], answer=STARTED_ANSWER)
    call = {**VIDEO_CALL, "job_id": "an-bad-1", **change}

    blocking, detached = _call("gen_video", call), _call("job_submit", call)

    assert blocking.is_error and detached.is_error and reached == []
    assert _texts([detached])[0] == _texts([blocking])[0].replace("gen_video", "job_submit")
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows() == []


def test_a_job_id_with_rows_is_refused_by_job_submit_with_what_that_job_came_to(monkeypatch, tmp_path):
    # AN3 (8), I-money-1: one click per job id across calls, whichever tool wrote the rows.
    reached = _video_world(monkeypatch, tmp_path, [_submitted(), STARTED], answer=STARTED_ANSWER)
    first = _call("job_submit", {**VIDEO_CALL, "job_id": "an-twice-1"})
    assert not first.is_error and len(reached) == 1

    again = _call("job_submit", {**VIDEO_CALL, "job_id": "an-twice-1"})
    blocking = _call("gen_video", {**VIDEO_CALL, "job_id": "an-twice-1"})

    for refused in (again, blocking):
        text = _texts([refused])[0]
        assert (
            refused.is_error and "outcome code=STARTED charged=unknown retryable=no; AlreadySubmitted" in text
        )
    assert len(reached) == 1 and len(gen.Ledger(tmp_path / "ledger.jsonl").rows("an-twice-1")) == 2


def test_job_status_says_where_a_started_job_stands_and_writes_nothing(monkeypatch, tmp_path):
    opened = _job_backend(monkeypatch, tmp_path)
    ledger = _started_job(tmp_path / "films")
    log = _job_world(monkeypatch, tmp_path, [_video("w-before", "old"), _video("w-job", PROMPT)])
    rows_before = ledger.rows()

    answer = _payload(_call("job_status", {"job_id": "job-1"}))

    assert (answer["state"], answer["media_id"], answer["workflow_id"]) == ("ready", "m-w-job", "w-job")
    assert (answer["credits_before"], answer["credits_now"]) == (200, 190)
    assert answer["outcome"]["code"] == "STARTED"
    assert ledger.rows() == rows_before and log == ["snapshot p-1", "credits"] and opened == ["browser"]


def test_job_collect_fetches_the_clip_writes_the_one_settled_row_and_says_done(monkeypatch, tmp_path):
    opened = _job_backend(monkeypatch, tmp_path)
    ledger = _started_job(tmp_path / "films")
    _job_world(monkeypatch, tmp_path, [_video("w-before", "old"), _video("w-job", PROMPT)])

    answer = _payload(_call("job_collect", {"job_id": "job-1"}))

    assert (answer["state"], answer["media_id"]) == ("collected", "m-w-job")
    assert (answer["spent"], answer["spent_from"]) == (10, "bracket")
    assert (answer["outcome"]["code"], answer["outcome"]["charged"]) == ("DONE", 10)
    assert [row["status"] for row in ledger.rows()] == ["submitted", "started", "done"]
    assert opened == ["browser"] and mcp_server.backend._running == set()


def test_a_settled_job_is_answered_by_both_later_tools_with_no_browser(monkeypatch, tmp_path):
    # AN3 (5), I-money-6: the row is the answer, so nothing is looked up and nothing is written again.
    opened = _job_backend(monkeypatch, tmp_path)
    ledger = _started_job(tmp_path / "films")
    _job_world(monkeypatch, tmp_path, [_video("w-job", PROMPT)])
    first = _payload(_call("job_collect", {"job_id": "job-1"}))
    del opened[:]

    again = _payload(_call("job_collect", {"job_id": "job-1"}))
    seen = _payload(_call("job_status", {"job_id": "job-1"}))

    for answer in (again, seen):
        assert (answer["state"], answer["status"], answer["path"]) == ("settled", "done", first["path"])
        assert (answer["outcome"]["code"], answer["outcome"]["charged"]) == ("DONE", 10)
    assert opened == [] and len(ledger.rows()) == 3


def test_job_collect_reads_every_ledger_under_the_out_folder_for_a_job_sharing_the_balance(
    monkeypatch, tmp_path
):
    # AN3 (7): the other job was written by another call into another folder; its rows still count.
    _job_backend(monkeypatch, tmp_path)
    ledger = _started_job(tmp_path / "films")
    gen.Ledger(tmp_path / "other" / "ledger.jsonl").append("job-2", "submitted", kind="video", project="p-1")
    _job_world(monkeypatch, tmp_path, [_video("w-job", PROMPT)], balance=170)

    answer = _payload(_call("job_collect", {"job_id": "job-1"}))

    assert (answer["spent"], answer["spent_from"], answer["credits_after"]) == (10, "quoted", 170)
    assert ledger.rows()[-1]["spent_from"] == "quoted"


def test_job_collect_of_a_job_another_call_holds_is_refused_as_started(monkeypatch, tmp_path):
    # Its submit, or another collect: two calls fetching one job would write two settled rows.
    opened = _job_backend(monkeypatch, tmp_path)
    ledger = _started_job(tmp_path / "films")
    _job_world(monkeypatch, tmp_path, [_video("w-job", PROMPT)])
    mcp_server.backend._running.add("job-1")

    result = _call("job_collect", {"job_id": "job-1"})

    text = _texts([result])[0]
    assert (
        result.is_error and "outcome code=UNKNOWN charged=unknown retryable=no; AlreadySubmitted" in text
    ), text
    assert "in another call" in text and opened == [] and len(ledger.rows()) == 2
    assert mcp_server.backend._running == {"job-1"}, "the other call's mark is not this call's to drop"


@pytest.mark.parametrize("tool", ["job_status", "job_collect"])
def test_a_later_tool_asked_for_a_job_no_ledger_holds_says_nothing_was_submitted(monkeypatch, tmp_path, tool):
    opened = _job_backend(monkeypatch, tmp_path)
    _started_job(tmp_path / "films")

    result = _call(tool, {"job_id": "job-nobody-ran"})

    text = _texts([result])[0]
    assert result.is_error and "outcome code=NOT_SUBMITTED charged=0 retryable=no; LookupError" in text, text
    assert "no ledger" in text and opened == []


@pytest.mark.parametrize("tool", ["job_status", "job_collect"])
def test_a_job_whose_rows_sit_in_two_ledgers_is_not_looked_for(monkeypatch, tmp_path, tool):
    # Which run the rows describe cannot be told, so neither is fetched and no outcome is vouched for.
    opened = _job_backend(monkeypatch, tmp_path)
    _started_job(tmp_path / "films")
    _started_job(tmp_path / "again")

    result = _call(tool, {"job_id": "job-1"})

    text = _texts([result])[0]
    assert result.is_error and "outcome code=UNKNOWN charged=unknown retryable=no; LookupError" in text, text
    assert "2 ledgers" in text and opened == []


@pytest.mark.parametrize("tool", ["job_status", "job_collect"])
def test_a_job_that_job_submit_did_not_start_is_answered_with_its_outcome(monkeypatch, tmp_path, tool):
    # A blocking tool that crashed after its intent row: money may be gone, and no clip can be looked for here.
    opened = _job_backend(monkeypatch, tmp_path)
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    status, fields = _submitted()
    ledger.append("job-crashed", status, **fields)

    result = _call(tool, {"job_id": "job-crashed"})

    text = _texts([result])[0]
    assert result.is_error and "outcome code=UNKNOWN charged=unknown retryable=no; LookupError" in text, text
    # A job_submit killed between its click and its started row leaves the same rows: the words must not say
    # that job_submit never ran it.
    assert "no started row" in text and "did not start it" not in text and "runs again" not in text
    assert len(ledger.rows()) == 1 and mcp_server.backend._running == set()
    assert opened == []


def test_job_status_of_a_job_another_call_holds_says_so_with_no_browser(monkeypatch, tmp_path):
    # While its job_submit still runs the job has an intent row and no started row yet: that is not a job nobody
    # started, and reading it as one would send the agent to its outcome of UNKNOWN.
    opened = _job_backend(monkeypatch, tmp_path)
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    status, fields = _submitted()
    ledger.append("job-flying", status, **fields)
    mcp_server.backend._running.add("job-flying")

    answer = _payload(_call("job_status", {"job_id": "job-flying"}))

    assert (answer["job_id"], answer["state"]) == ("job-flying", "in_another_call")
    assert "outcome" not in answer and opened == [] and len(ledger.rows()) == 1


def test_an_error_of_job_collect_opens_with_what_its_own_row_now_says(monkeypatch, tmp_path):
    # The job never showed and the balance never moved: collect settles it and the error is typed off that row.
    _job_backend(monkeypatch, tmp_path)
    ledger = _started_job(tmp_path / "films")
    _job_world(monkeypatch, tmp_path, [_video("w-before", "old")], balance=200)

    result = _call("job_collect", {"job_id": "job-1"})

    text = _texts([result])[0]
    assert (
        result.is_error and "outcome code=NOTHING_GENERATED charged=0 retryable=no; RuntimeError" in text
    ), text
    assert ledger.rows()[-1]["status"] == "failed" and mcp_server.backend._running == set()


@pytest.mark.parametrize("tool", ["job_status", "job_collect"])
def test_a_later_tool_refuses_a_job_id_the_ledger_would_store_as_another(monkeypatch, tmp_path, tool):
    opened = _job_backend(monkeypatch, tmp_path)

    result = _call(tool, {"job_id": "__Secure- x"})

    assert result.is_error and "job_id" in _texts([result])[0] and opened == []


def test_the_job_tools_say_how_they_fit_together_and_what_each_costs():
    # An agent holding nothing but this MCP learns the three steps from the descriptions and the instructions.
    tools = served_tool_objects()
    submit, status, collect = (
        tools[name].description for name in ("job_submit", "job_status", "job_collect")
    )

    assert "spends credits" in submit and "job_status" in submit and "job_collect" in submit
    assert "max_credits" in submit and "STARTED" in submit
    for free in (status, collect):
        assert free.endswith("Free.") and "never clicks Start generation" in free
    assert "writes no ledger row" in status and "settled row" in collect
    for name in ("job_submit", "job_status", "job_collect"):
        assert name in mcp_server.server.instructions


def test_the_job_tools_do_not_promise_what_the_code_does_not_do():
    # Review of plan AN, each a sentence the code contradicted.
    tools = served_tool_objects()
    submit, status, collect = (
        tools[name].description for name in ("job_submit", "job_status", "job_collect")
    )

    # job_submit offers no count, so it must not carry gen_video's sentence about one.
    assert "multiplies the price" not in submit and "multiplies the price" in tools["gen_video"].description
    # With no submit request seen the call stays and answers as gen_video does.
    assert "stays" in submit and "as gen_video does" in submit
    # A blocking run made while a submitted job renders has no shared-balance check of its own.
    assert "balance_moved" in submit
    # job_status appends the balance it read to out/credits.jsonl, so "writes nothing" was untrue.
    assert "writes nothing" not in status and "credits.jsonl" in status
    # When every direct link fails the download goes through the editor's own Download menu, which is clicked.
    assert "never clicks and never types" not in collect and "Download menu" in collect
    # A 360p job has no 720p rendition: its own file is what is fetched.
    assert "360p" in collect and "recipe" in collect and "charged_from" in collect


def test_job_submit_hands_the_driver_what_gen_video_hands_it(monkeypatch, tmp_path):
    # Review of plan AN: job_submit could drop the aspect, the model, the ingredients, or swap the two frames, with
    # the suite green. The two tools are run with the same options, a set per mode and one with none at all (so a
    # default that drifts shows), and the driver must get the same call but for the job id and the detach.
    _spending_backend(monkeypatch, tmp_path)
    option_sets = [
        {"model": "omni-flash", "aspect": "16:9", "resolution": "360p", "duration": 6, "start_frame": "S"}
        | {"end_frame": "E"},
        {"model": "veo-lite", "media_ids": ["M"], "characters": ["E"], "voices": ["Achird"]},
        {"out_dir": str(tmp_path / "films")},
        {},
    ]
    schema = served_tool_objects()["job_submit"].input_schema["properties"]
    named = {key for options in option_sets for key in options}
    # retry_of needs a refused job on record and has its own test below.
    unexercised = {"project", "prompt", "job_id", "max_credits", "retry_of"}
    assert set(schema) - named == unexercised, "an option no set exercises"

    for index, options in enumerate(option_sets):
        blocking = _payload(
            _call("gen_video", SPEND_CALLS["gen_video"] | options | {"job_id": f"fwd-a{index}"})
        )
        detached = _payload(
            _call("job_submit", SPEND_CALLS["job_submit"] | options | {"job_id": f"fwd-b{index}"})
        )

        assert (blocking.pop("count"), blocking.pop("dry_run"), detached.pop("detach")) == (1, False, True)
        assert (blocking.pop("job_id"), detached.pop("job_id")) == (f"fwd-a{index}", f"fwd-b{index}")
        assert detached == blocking, options


def test_gen_video_still_names_a_blank_project_before_anything_about_a_retry():
    # The order gen_video refused in before its checks were shared with job_submit (non-goal: it does not change).
    result = _call("gen_video", {"project": " ", "prompt": "a boat", "dry_run": True, "retry_of": "j"})

    assert result.is_error and "project is required" in _texts([result])[0]


@pytest.mark.parametrize("tool", ["job_status", "job_collect"])
def test_a_ledger_that_cannot_be_read_never_leaves_a_later_tool_without_an_outcome(
    monkeypatch, tmp_path, tool
):
    # Review of plan AN: a torn line in ANOTHER ledger gave a bare JSONDecodeError, which the job id rule tells an
    # agent to read as "refused before any job started", for a job that is started and paid.
    opened = _job_backend(monkeypatch, tmp_path)
    ledger = _started_job(tmp_path / "films")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "ledger.jsonl").write_text('{"job_id": "jo', encoding="utf-8")

    result = _call(tool, {"job_id": "job-1"})

    text = _texts([result])[0]
    assert result.is_error and "outcome code=UNKNOWN charged=unknown retryable=no" in text, text
    assert "cannot be read" in text and opened == [] and len(ledger.rows()) == 2


def test_two_collects_of_one_job_at_once_fetch_and_settle_it_once(monkeypatch, tmp_path):
    # The first collect is inside its session when the second arrives, and the mark is what turns the second away
    # before it opens a session of its own (the real sessions queue behind one guard; these fakes do not).
    ledger = _started_job(tmp_path / "films")
    log = _job_world(monkeypatch, tmp_path, [_video("w-job", PROMPT)])
    inside, leave = asyncio.Event(), asyncio.Event()
    sessions = []

    async def held_with(self, fn):
        sessions.append(fn)
        if len(sessions) == 1:
            inside.set()
            await leave.wait()
        else:
            # A second session means the mark did not hold. Let the first go, so this test fails on what both of
            # them then write instead of hanging (it hung a mutant run for an hour and a half, 2026-10-02).
            leave.set()
        return await fn(_Reader())

    monkeypatch.setattr(mcp_server.Backend, "_with", held_with)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=tmp_path))

    async def both(session):
        first = asyncio.create_task(session.call_tool("job_collect", {"job_id": "job-1"}))
        await inside.wait()
        second = await session.call_tool("job_collect", {"job_id": "job-1"})
        seen = await session.call_tool("job_status", {"job_id": "job-1"})
        leave.set()
        return await first, second, seen

    first, second, seen = with_client(both)

    assert not first.is_error and second.is_error and "in another call" in _texts([second])[0]
    assert _payload(seen)["state"] == "in_another_call"
    assert [row["status"] for row in ledger.rows()] == ["submitted", "started", "done"]
    assert len(log.fetched) == 1 and len(sessions) == 1 and mcp_server.backend._running == set()


def test_job_status_types_an_error_by_the_jobs_own_rows(monkeypatch, tmp_path):
    # A listing that cannot be read is no reason to call a started job UNKNOWN: it is still Flow's to render.
    _job_backend(monkeypatch, tmp_path)
    _started_job(tmp_path / "films")

    async def broken(session, project_id, attempts=4):
        raise RuntimeError("no Zzl0ze frame")

    monkeypatch.setattr(mcp_server.jobs_mod.composer, "snapshot", broken)

    text = _texts([_call("job_status", {"job_id": "job-1"})])[0]

    assert "outcome code=STARTED charged=unknown retryable=no; RuntimeError: no Zzl0ze frame" in text, text


def test_job_status_of_a_job_not_listed_yet_still_says_it_is_started(monkeypatch, tmp_path):
    _job_backend(monkeypatch, tmp_path)
    _started_job(tmp_path / "films")
    _job_world(monkeypatch, tmp_path, [_video("w-before", "old")])

    answer = _payload(_call("job_status", {"job_id": "job-1"}))

    assert (answer["state"], answer["outcome"]["code"]) == ("not_listed", "STARTED")


def test_job_status_reads_every_ledger_for_a_clip_another_job_names(monkeypatch, tmp_path):
    # I-money-5 through the server: the other job's rows sit in another folder's ledger.
    _job_backend(monkeypatch, tmp_path)
    _started_job(tmp_path / "films", workflow=None, at=time.time())
    theirs = gen.Ledger(tmp_path / "other" / "ledger.jsonl")
    theirs.append("job-B", "submitted", kind="video", project="p-1", prompt=PROMPT)
    theirs.append("job-B", "started", workflow_id="w-B", prompt=PROMPT)
    _job_world(monkeypatch, tmp_path, [_video("w-B", PROMPT)])

    assert _payload(_call("job_status", {"job_id": "job-1"}))["state"] == "not_listed"
    assert _payload(_call("job_collect", {"job_id": "job-1"}))["state"] == "not_listed"


def test_a_collected_clips_path_is_spelled_as_the_out_folder_is(monkeypatch, tmp_path):
    # Blocking rows hold `out/...` as the server was given it; a resolved path would put the home folder in the row.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mcp_server.Backend, "_with", lambda self, fn: fn(_Reader()))
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend(out_dir=Path("out")))
    ledger = _started_job(Path("out") / "films")
    _job_world(monkeypatch, tmp_path, [_video("w-job", PROMPT)])

    answer = _payload(_call("job_collect", {"job_id": "job-1"}))

    assert answer["path"].startswith("out/films/m-w-job_") and ledger.rows()[-1]["path"] == answer["path"]


def test_job_submit_retries_a_refused_job_as_gen_video_does(monkeypatch, tmp_path):
    # A refusal Flow gave while the submitting page was still open is typed retryable, and its advice says to call
    # the same tool again with retry_of: job_submit takes it, vouched for off the same ledgers.
    reached = _video_world(
        monkeypatch, tmp_path, [_submitted(retry_of="am-first"), STARTED], answer=STARTED_ANSWER
    )
    _refused_earlier(tmp_path, folder="film-1")

    answer = _payload(_call("job_submit", {**VIDEO_CALL, "job_id": "an-retry-1", "retry_of": "am-first"}))

    assert answer["outcome"]["code"] == "STARTED" and len(reached) == 1
    assert reached[0]["retry_of"] == "am-first" and reached[0]["detach"] is True

    other = _call(
        "job_submit", {**VIDEO_CALL, "prompt": "a dog", "job_id": "an-retry-2", "retry_of": "am-first"}
    )
    assert other.is_error and "same project and prompt" in _texts([other])[0] and len(reached) == 1
    done = _call("job_submit", {**VIDEO_CALL, "job_id": "an-retry-3", "retry_of": "an-retry-1"})
    assert done.is_error and "STARTED" in _texts([done])[0] and len(reached) == 1


CUP = "a cup on a table"
LONG_CUP = "a cup on a table, " * 20
REFUSED_ROW = (
    "failed",
    {"spent": 0, "flow": {"statuses": [6, 2, 4], "reasons": ["PUBLIC_ERROR_AUDIO_FILTERED"]}},
)


def _submitted_job(
    tmp_path,
    job_id="an-flying",
    *,
    project="P",
    prompt=CUP,
    started=True,
    detach=True,
    settled=None,
    age=0.0,
    prompt_text=None,
):
    """The rows of a job as the composer writes them for job_submit (`detach` false: for a blocking run), the last
    of them `age` seconds old."""
    ledger = gen.Ledger(tmp_path / "films" / "ledger.jsonl")
    box = {"prompt_text": prompt_text} if prompt_text else {}
    mark = {"detach": True} if detach else {}
    kept = prompt[: mcp_server.outcome_mod.PROMPT_KEPT]
    ledger.append(
        job_id, "submitted", kind="video", project=project, prompt=kept, credits_before=100, **mark, **box
    )
    if started:
        ledger.append(job_id, "started", workflow_id="w-flying", workflows_before=[], prompt=prompt, **box)
    if settled:
        ledger.append(job_id, settled[0], **settled[1])
    rows = [{**row, "ts": time.time() - age} for row in ledger.rows()]
    ledger.path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return ledger


@pytest.mark.parametrize(
    ("job", "prompt"),
    [
        ({}, "A cup  on a table"),
        ({"started": False}, CUP),
        ({"settled": ("unknown", {"spent": None, "candidates": ["m-x"]})}, CUP),
        ({"settled": ("failed", {"spent": 0})}, CUP),
        ({"prompt_text": f"Thu {CUP}"}, f"Thu {CUP}"),
        ({"prompt": LONG_CUP}, LONG_CUP),
        ({"prompt": LONG_CUP, "started": False}, LONG_CUP),
        ({"age": 3000}, CUP),
    ],
    ids=[
        "rendering",
        "cut off before its started row",
        "settled unknown",
        "never shown",
        "the text its prompt box held",
        "a prompt longer than the intent row keeps",
        "a long prompt and only the intent row",
        "fifty minutes old",
    ],
)
@pytest.mark.parametrize("tool", ["gen_video", "gen_character"])
def test_a_blocking_run_that_could_take_a_submitted_jobs_clip_is_refused(
    monkeypatch, tmp_path, tool, job, prompt
):
    # Re-reviews of plan AN (C2, F1, G1, G2). A blocking run takes the one new clip that carries its prompt; beside
    # a submitted job of that prompt whose clip may still show, the clip could be the other job's: both would end
    # DONE on it and the blocking run's own clip would be left to nobody. Before job_submit no two jobs of one
    # server overlapped.
    reached = _spending_backend(monkeypatch, tmp_path)
    _submitted_job(tmp_path, **job)
    call = SPEND_CALLS[tool] | {"project": "P", "prompt": prompt, "job_id": "an-blocking-1"}

    result = _call(tool, call)

    text = _texts([result])[0]
    assert result.is_error and "outcome code=NOT_SUBMITTED charged=0 retryable=no" in text, text
    assert "an-flying" in text and "job_collect" in text and reached == []
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows() == []


@pytest.mark.parametrize(
    ("job", "call"),
    [
        ({"settled": ("done", {"media_id": "m-w-flying", "spent": 10})}, {}),
        ({"settled": REFUSED_ROW}, {}),
        ({"prompt": "a dog on a rug"}, {}),
        ({"project": "Q"}, {}),
        ({}, {"dry_run": True}),
        ({"started": False, "detach": False}, {}),
        ({"age": 3601}, {}),
        ({"started": False, "age": 3601}, {}),
        ({"prompt": LONG_CUP}, {"prompt": LONG_CUP + "and a spoon"}),
        (
            {"prompt": LONG_CUP, "started": False, "prompt_text": LONG_CUP},
            {"prompt": LONG_CUP + "and a spoon"},
        ),
        ({"prompt_text": f"Thu {CUP}", "prompt": "walks in"}, {}),
    ],
    ids=[
        "collected",
        "refused by Flow",
        "another prompt",
        "another project",
        "a dry run",
        "a blocking run that left only its intent row",
        "started over an hour ago",
        "cut off over an hour ago",
        "another long prompt with the same head",
        "another long prompt, the whole one known from the box",
        "another typed prompt and another box text",
    ],
)
def test_a_blocking_run_no_submitted_job_can_be_confused_with_goes_ahead(monkeypatch, tmp_path, job, call):
    # A clip listed over an hour ago is in the blocking run's own "before" listing, so it cannot be taken; and what
    # a blocking run did before job_submit existed (run beside a crashed blocking job) is not changed.
    reached = _spending_backend(monkeypatch, tmp_path)
    _submitted_job(tmp_path, **job)
    arguments = SPEND_CALLS["gen_video"] | {"project": "P", "prompt": CUP} | call

    result = _call("gen_video", arguments)

    assert not result.is_error, _texts([result])
    assert ("gen_video", "job-video") in reached


def test_a_blocking_run_called_while_a_submit_of_its_prompt_is_in_its_session_is_refused(
    monkeypatch, tmp_path
):
    # Re-review two (F1). A submit writes its first row about two minutes into its call; a blocking run called
    # before then found no row to be warned by, queued for the browser and ran right after the submit.
    reached = _spending_backend(monkeypatch, tmp_path)
    inside, leave = asyncio.Event(), asyncio.Event()
    sessions = []

    async def held_with(self, fn):
        sessions.append(fn)
        if len(sessions) == 1:
            inside.set()
            await leave.wait()
        else:
            # The blocking run got as far as a session: let the submit go, so this test fails instead of hanging.
            leave.set()
        return await fn(object())

    monkeypatch.setattr(mcp_server.Backend, "_with", held_with)
    submit = SPEND_CALLS["job_submit"] | {"project": "P", "prompt": CUP, "job_id": "an-in-session"}
    beside = SPEND_CALLS["gen_video"] | {"project": "P", "prompt": "A cup on  a table", "job_id": "an-beside"}

    async def both(session):
        first = asyncio.create_task(session.call_tool("job_submit", submit))
        await inside.wait()
        second = await session.call_tool("gen_video", beside)
        leave.set()
        return await first, second

    first, second = with_client(both)

    text = _texts([second])[0]
    assert not first.is_error and second.is_error, text
    assert "outcome code=NOT_SUBMITTED charged=0 retryable=no" in text and "an-in-session" in text, text
    assert reached == [("job_submit", "an-in-session")] and len(sessions) == 1
    assert mcp_server.backend._submitting == [] and mcp_server.backend._running == set()


def test_a_second_submit_of_a_prompt_a_submitted_job_still_renders_goes_ahead(monkeypatch, tmp_path):
    # Two takes of one prompt are what job_submit is for: each is claimed by the workflow Flow named for it.
    reached = _spending_backend(monkeypatch, tmp_path)
    _submitted_job(tmp_path)
    call = SPEND_CALLS["job_submit"] | {"project": "P", "prompt": CUP, "job_id": "an-take-2"}

    assert not _call("job_submit", call).is_error and ("job_submit", "an-take-2") in reached


@pytest.mark.parametrize("tool", ["job_status", "job_collect"])
def test_a_ledger_line_that_is_no_row_never_leaves_a_later_tool_without_an_outcome(
    monkeypatch, tmp_path, tool
):
    # `[1, 2]` is valid JSON, so no ValueError: the read for a job id fails on the row itself.
    opened = _job_backend(monkeypatch, tmp_path)
    _started_job(tmp_path / "films")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "ledger.jsonl").write_text("[1, 2]\n", encoding="utf-8")

    text = _texts([_call(tool, {"job_id": "job-1"})])[0]

    assert "outcome code=UNKNOWN charged=unknown retryable=no" in text and "cannot be read" in text, text
    assert opened == []


@pytest.mark.parametrize("alias", ["aa-alias", "zz-alias"])
def test_a_ledger_reached_by_two_spellings_is_one_ledger_and_the_clip_lands_beside_the_real_one(
    monkeypatch, tmp_path, alias
):
    # A ledger linked into another folder under out/: the job is in one file, not in two, and its clip belongs
    # beside that file, whether the link sorts before it or after.
    opened = _job_backend(monkeypatch, tmp_path)
    ledger = _started_job(tmp_path / "films")
    (tmp_path / alias).mkdir()
    (tmp_path / alias / "ledger.jsonl").symlink_to(ledger.path)
    log = _job_world(monkeypatch, tmp_path, [_video("w-job", PROMPT)])

    answer = _payload(_call("job_collect", {"job_id": "job-1"}))

    assert answer["state"] == "collected" and opened == ["browser"]
    assert Path(answer["path"]).parent == tmp_path / "films" and len(log.fetched) == 1
    assert [row["status"] for row in ledger.rows()] == ["submitted", "started", "done"]


def test_the_blocking_composer_tools_and_the_instructions_say_when_a_submitted_job_holds_them_back():
    # The refusal is a deliberate change to gen_video and gen_character (DECISIONS 2026-10-03): an agent reading
    # only their own descriptions has to meet it there, not in job_submit's.
    tools = served_tool_objects()
    for name in ("gen_video", "gen_character"):
        description = tools[name].description
        assert (
            "job_submit" in description
            and "job_collect" in description
            and "same project and prompt" in description
        )
    assert "refused while a job that job_submit" in mcp_server.server.instructions
    # No other tool is held to it, so no other description says it is.
    held = {
        name for name, tool in tools.items() if "is refused while a job that job_submit" in tool.description
    }
    assert held == {"gen_video", "gen_character"}


@pytest.mark.parametrize(
    "change", [{"prompt": "a dog on a rug"}, {"project": "Q"}], ids=["another prompt", "another project"]
)
def test_a_blocking_run_a_submit_in_its_session_cannot_be_confused_with_is_not_refused(
    monkeypatch, tmp_path, change
):
    # What the server remembers of a submit in its session holds back only runs of its own project and prompt.
    reached = _spending_backend(monkeypatch, tmp_path)
    inside, leave = asyncio.Event(), asyncio.Event()
    sessions = []

    async def held_with(self, fn):
        sessions.append(fn)
        if len(sessions) == 1:
            inside.set()
            await leave.wait()
        else:
            # The real sessions queue behind one guard; here the second lets the first go once it is in.
            leave.set()
        return await fn(object())

    monkeypatch.setattr(mcp_server.Backend, "_with", held_with)
    submit = SPEND_CALLS["job_submit"] | {"project": "P", "prompt": CUP, "job_id": "an-in-session"}
    beside = SPEND_CALLS["gen_video"] | {"project": "P", "prompt": CUP, "job_id": "an-beside"} | change

    async def both(session):
        first = asyncio.create_task(session.call_tool("job_submit", submit))
        await inside.wait()
        second = await asyncio.wait_for(session.call_tool("gen_video", beside), 10)
        leave.set()
        return await first, second

    first, second = with_client(both)

    assert not first.is_error and not second.is_error, _texts([first, second])
    assert sorted(reached) == [("gen_video", "an-beside"), ("job_submit", "an-in-session")]
