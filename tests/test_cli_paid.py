"""The CLI's paid commands carry the MCP tools' own guards (plan AQ, B3, I-money-2): a job id is required, one
already in a ledger under out/ is refused before a browser opens, the out folder lies inside out/, and every call
goes through mcp_server.Backend, where the cells and the caps are decided."""

from pathlib import Path

import pytest
from click.testing import CliRunner
from test_mcp_server import leaf_commands, served_tool_objects, tool_for

from video import cli, gen, mcp_server

PAID = {"gen t2v", "gen i2v", "gen r2v", "flow clip extend", "flow clip edit", "flow agent send"}


def _command(path):
    node = cli.main
    for part in path.split():
        node = node.commands[part]
    return node


def _files(tmp_path, *names):
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    return [str(tmp_path / name) for name in names]


def _paid_args(tmp_path):
    frame, ref = _files(tmp_path, "a.png", "b.png")
    return {
        "gen t2v": ["gen", "t2v", "a boat", "--project", "P"],
        "gen i2v": ["gen", "i2v", frame, "a boat", "--project", "P"],
        "gen r2v": ["gen", "r2v", "a boat", "--ref", ref, "--project", "P"],
        "flow clip extend": ["flow", "clip", "extend", "P", "M", "keep going"],
        "flow clip edit": ["flow", "clip", "edit", "P", "M", "red shirt"],
        "flow agent send": ["flow", "agent", "send", "P", "hi"],
    }


@pytest.fixture(autouse=True)
def nothing_opens(monkeypatch):
    # Autouse: the first run of this module, with the CLI still on its old path, opened a real browser and ran gflow.
    def must_not_open(*args, **kwargs):
        raise AssertionError("opened a browser or ran gflow before the guard")

    monkeypatch.setattr(cli, "_read", must_not_open)
    monkeypatch.setattr(gen, "run_job", must_not_open)
    monkeypatch.setattr(mcp_server.Backend, "_with", must_not_open)


@pytest.mark.parametrize("command", sorted(PAID))
def test_a_paid_command_without_a_job_id_exits_2_before_anything_opens(nothing_opens, tmp_path, command):
    # Review 2026-10-08 (cli 2): with --job optional the CLI minted a uuid per run, so a retry paid twice. Click 8.5
    # does not enforce required on an option that also carries default=None (measured), so --job carries no default.
    result = CliRunner().invoke(cli.main, _paid_args(tmp_path)[command])

    assert result.exit_code == 2, result.output
    assert "Missing option '--job'" in result.output, result.output


def test_every_command_carries_the_job_option_its_tool_declares():
    # Read from click and from the served schema, so neither side can drift by hand: required where the tool
    # requires job_id (the spending tools), present but optional where the tool takes it (agent send, images).
    tools = served_tool_objects()
    checked = {}
    for command in leaf_commands(cli.main):
        tool = tool_for(command, tools)
        if tool is None or "job_id" not in (tools[tool].input_schema.get("properties") or {}):
            continue
        job = next((p for p in _command(command).params if p.name == "job_id"), None)
        assert job is not None, f"`video {command}` has no --job while {tool} takes job_id"
        checked[command] = (job.required, "job_id" in (tools[tool].input_schema.get("required") or []))

    assert PAID | {"gen t2i", "gen i2i"} <= set(checked), sorted(checked)
    assert [c for c, (cli_required, tool_required) in checked.items() if cli_required != tool_required] == []


def test_a_job_id_already_in_a_ledger_under_out_is_refused_before_a_browser_opens(nothing_opens, tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        gen.Ledger(Path("out") / "ledger.jsonl").append("dup-1", "submitted", kind="t2v")

        result = runner.invoke(
            cli.main, ["gen", "t2v", "a boat", "--project", "P", "--job", "dup-1", "--out", "out/again"]
        )

    assert result.exit_code == 1, result.output
    assert "dup-1" in result.output and "already" in result.output, result.output
    assert "Traceback" not in result.output


def test_an_out_folder_outside_out_is_refused_before_a_browser_opens(nothing_opens, tmp_path):
    result = CliRunner().invoke(
        cli.main, ["gen", "t2v", "a boat", "--project", "P", "--job", "far-1", "--out", str(tmp_path / "x")]
    )

    assert result.exit_code == 1, result.output
    assert "inside" in result.output and "out" in result.output, result.output


def test_gen_goes_through_the_backend_with_the_profile_and_every_option(monkeypatch, tmp_path):
    handed = {}

    async def fake_generate(self, **kwargs):
        handed.update(kwargs, profile=self.profile)
        return {"job_id": kwargs["job_id"], "outputs": []}

    monkeypatch.setattr(mcp_server.Backend, "generate", fake_generate)
    (frame,) = _files(tmp_path, "a.png")

    result = CliRunner().invoke(
        cli.main,
        [
            "gen",
            "i2v",
            frame,
            "a boat",
            "--project",
            "P",
            "--job",
            "i2v-1",
            "--profile",
            "acc2",
            "--aspect",
            "9:16",
        ],
    )

    assert result.exit_code == 0, result.output
    assert handed == {
        "kind": "i2v",
        "prompt": "a boat",
        "project": "P",
        "model": None,
        "aspect": "9:16",
        "count": 1,
        "duration": None,
        "resolution": None,
        "job_id": "i2v-1",
        "out_dir": "out",
        "initial_frame": frame,
        "end_frame": None,
        "profile": "acc2",
    }, handed


@pytest.mark.parametrize(
    ("args", "method", "expected"),
    [
        (
            ["flow", "clip", "extend", "P", "M", "keep going", "--job", "ext-1", "--wait", "30"],
            "clip_extend",
            {
                "project_id": "P",
                "media_id": "M",
                "prompt": "keep going",
                "job_id": "ext-1",
                "out_dir": "out",
                "wait": 30.0,
            },
        ),
        (
            ["flow", "clip", "edit", "P", "M", "red shirt", "--job", "edit-1"],
            "clip_edit",
            {
                "project_id": "P",
                "media_id": "M",
                "prompt": "red shirt",
                "job_id": "edit-1",
                "out_dir": "out",
                "wait": 240.0,
            },
        ),
        (
            ["flow", "agent", "send", "P", "hi there", "--job", "agent-1"],
            "agent_send",
            {"project_id": "P", "message": "hi there", "job_id": "agent-1", "wait": 60.0},
        ),
    ],
    ids=["clip extend", "clip edit", "agent send"],
)
def test_the_editor_and_agent_commands_go_through_the_backend(monkeypatch, args, method, expected):
    handed = {}

    async def fake(self, *positional, **kwargs):
        handed.update(kwargs, profile=self.profile)
        return {"ok": True}

    monkeypatch.setattr(mcp_server.Backend, method, fake)

    result = CliRunner().invoke(cli.main, args)

    assert result.exit_code == 0, result.output
    assert handed == expected | {"profile": "default"}, handed


def test_a_refusal_of_the_backend_is_the_exit_message_not_a_traceback(monkeypatch):
    async def refuse(self, **kwargs):
        raise ValueError("veo-quality was never paid for through gen_t2v; nothing was spent")

    monkeypatch.setattr(mcp_server.Backend, "generate", refuse)

    result = CliRunner().invoke(cli.main, ["gen", "t2v", "a boat", "--project", "P", "--job", "q-1"])

    assert result.exit_code == 1, result.output
    assert "never paid" in result.output and "Traceback" not in result.output, result.output


def test_mcp_run_serves_the_profile_it_is_given(monkeypatch):
    # Review 2026-10-08 (D19): the served backend was hard-coded to the default profile, so a second account needed
    # an in-process swap.
    served = []

    async def fake_stdio():
        served.append(mcp_server.backend.profile)

    monkeypatch.setattr(mcp_server.server, "run_stdio_async", fake_stdio)
    monkeypatch.setattr(mcp_server, "backend", mcp_server.Backend())

    result = CliRunner().invoke(cli.main, ["mcp", "run", "--profile", "acc2"])

    assert result.exit_code == 0, result.output
    assert served == ["acc2"], served
    assert CliRunner().invoke(cli.main, ["mcp", "run"]).exit_code == 0 and served[-1] == "default"
