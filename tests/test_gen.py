import asyncio
import json
import re
from pathlib import Path, PurePosixPath

import pytest

from video import gen

FIXTURES = Path(__file__).parent / "fixtures" / "gflow"


def job(**overrides):
    base = {
        "job_id": "job-1",
        "kind": "t2v",
        "prompt": "a boat",
        "project": "P",
        "model": "veo-lite",
        "aspect": "16:9",
    }
    base.update(overrides)
    return gen.Job(**base)


def test_argv_t2v_carries_project_model_aspect_json_and_out_dir():
    argv = gen.build_argv(job(), Path("out"))
    assert argv[:3] == ["video", "t2v", "a boat"]
    assert argv[argv.index("--project") + 1] == "P"
    assert argv[argv.index("--model") + 1] == "veo-lite"
    assert argv[argv.index("--aspect") + 1] == "16:9"
    assert argv[argv.index("--out-dir") + 1] == "out"
    assert "--json" in argv


def test_argv_i2v_uses_initial_frame():
    argv = gen.build_argv(job(kind="i2v", initial_frame=Path("f.png")), Path("out"))
    assert argv[:2] == ["video", "i2v"]
    assert argv[argv.index("--initial-frame") + 1] == "f.png"
    assert "a boat" in argv


def test_argv_r2v_and_i2i_repeat_ref():
    r2v = gen.build_argv(job(kind="r2v", refs=[Path("a.jpg"), Path("b.jpg")]), Path("out"))
    assert r2v[:2] == ["video", "r2v"] and r2v.count("--ref") == 2
    i2i = gen.build_argv(job(kind="i2i", model="nano2", refs=[Path("a.jpg")]), Path("out"))
    assert i2i[:2] == ["image", "i2i"] and i2i.count("--ref") == 1


def test_argv_t2i_uses_out_and_count():
    argv = gen.build_argv(job(kind="t2i", model="nano2", count=2), Path("out"))
    assert argv[:2] == ["image", "t2i"]
    assert argv[argv.index("--out") + 1] == "out"
    assert argv[argv.index("-n") + 1] == "2"


def test_argv_refuses_missing_project_or_frame():
    with pytest.raises(ValueError):
        gen.build_argv(job(project=""), Path("out"))
    with pytest.raises(ValueError):
        gen.build_argv(job(kind="i2v"), Path("out"))
    with pytest.raises(ValueError):
        gen.build_argv(job(kind="r2v"), Path("out"))


def test_parse_image_result_from_fixture():
    outputs = gen.parse_result("t2i", (FIXTURES / "t2i.json").read_text(encoding="utf-8"))
    assert outputs == [
        {
            "media_id": "31262e48-5af1-4d64-b66c-403f9122aa93",
            "path": "out/t5/31262e48-5af1-4d64-b66c-403f9122aa93_1.jpg",
            "width": 1376,
            "height": 768,
        }
    ]


def test_parse_video_result_uses_media_id_and_local_path():
    payload = {
        "status": "ok",
        "command": "video t2v",
        "media_id": "1480bbf8-ebdc-41d1-ba7d-60163ccf1d4f",
        "generation_status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
        "succeeded": True,
        "local_path": "out/1480bbf8-ebdc-41d1-ba7d-60163ccf1d4f.mp4",
        "failure_reasons": [],
        "error_message": None,
        "request": {
            "model": "veo-lite",
            "mode": "t2v",
            "aspect": "16:9",
            "duration": None,
            "count": 1,
            "seed": None,
        },
    }
    assert gen.parse_result("t2v", json.dumps(payload)) == [
        {
            "media_id": "1480bbf8-ebdc-41d1-ba7d-60163ccf1d4f",
            "path": "out/1480bbf8-ebdc-41d1-ba7d-60163ccf1d4f.mp4",
        }
    ]


def test_parse_result_fail_status_raises():
    with pytest.raises(RuntimeError):
        gen.parse_result("t2i", json.dumps({"status": "fail", "error": {"title": "quota"}}))


def test_ledger_rows_and_submitted_check(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("job-1", "planned", kind="t2v")
    assert not ledger.has_submitted("job-1")
    ledger.append("job-1", "submitted")
    assert ledger.has_submitted("job-1")
    assert [r["status"] for r in ledger.rows("job-1")] == ["planned", "submitted"]


def test_ledger_scrubs_session_material(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("job-z", "planned", note="SAPISID=abc __Secure-1PSID=def Authorization: Bearer ya29.x")
    text = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")
    assert "SAPISID=" not in text and "__Secure-" not in text and "Authorization:" not in text


def test_ledger_rows_stay_readable_json_whatever_strings_they_carry(tmp_path):
    # Review of plan B (HANDOFF ngã rẽ 5): the scrub ran over the whole JSON line, so "Authorization: denied" in a prompt
    # ate the quote and comma after it, and every later read of any ledger under out/ failed with JSONDecodeError.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        "job-a",
        "submitted",
        prompt="Authorization: denied, then SAPISID=abc",
        argv=["video", "t2v", "__Secure-1PSID=def", "--json"],
        outputs=[{"note": "Authorization: Bearer ya29.x"}],
    )
    ledger.append("job-b", "planned", prompt="plain")

    rows = ledger.rows()

    assert [row["job_id"] for row in rows] == ["job-a", "job-b"]
    text = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")
    assert "SAPISID=" not in text and "__Secure-" not in text and "Authorization:" not in text
    assert rows[0]["argv"][:2] == ["video", "t2v"] and rows[0]["argv"][-1] == "--json"
    assert "[redacted]" in rows[0]["prompt"] and "[redacted]" in rows[0]["outputs"][0]["note"]


@pytest.mark.parametrize("code", [0x2028, 0x2029, 0x85])
def test_ledger_rows_survive_a_unicode_line_separator_in_a_prompt(tmp_path, code):
    # Review of plan D (2026-09-16, F2): `splitlines()` also breaks on these three, so one prompt carrying one made every
    # later read of that ledger raise JSONDecodeError, which blocks every spend and every reconcile.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("job-a", "submitted", prompt="night" + chr(code) + "city")
    ledger.append("job-b", "planned", prompt="plain")

    rows = ledger.rows()

    assert [row["job_id"] for row in rows] == ["job-a", "job-b"]
    assert rows[0]["prompt"] == "night" + chr(code) + "city"


def test_ledger_scrubs_values_json_cannot_encode_on_its_own(tmp_path):
    # Review of plan D (2026-09-16, finding 4): the per-string scrub walks strings, so a secret inside a set, bytes, a
    # PurePosixPath or a dict key reached disk raw, where the old whole-line scrub had caught it.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        "job-c",
        "planned",
        tags={"SAPISID=abc"},
        raw=b"__Secure-1PSID=def",
        pure=PurePosixPath("/x/SAPISID=ghi"),
        **{"Authorization: Bearer key": 1},
    )

    rows = ledger.rows()

    text = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")
    assert [row["job_id"] for row in rows] == ["job-c"]
    assert "SAPISID=" not in text and "__Secure-" not in text and "Authorization:" not in text
    # The walk itself turns each of these into text or a list, so none of them reaches the encoder as it was given.
    assert rows[0]["tags"] == ["[redacted]"]
    assert rows[0]["raw"].startswith("b'[redacted]") and rows[0]["pure"] == "/x/[redacted]"
    assert rows[0]["[redacted]"] == 1


@pytest.mark.parametrize("job_id", ["__Secure- x", "run SAPISID=1", "Authorization: me"])
def test_the_ledger_refuses_a_job_id_the_scrub_would_change(tmp_path, job_id):
    # Review of plan B (HANDOFF ngã rẽ 5): "__Secure- x" was stored as "[redacted] x", so the next call with the same id
    # found no row and ran again (DECISIONS 2026-09-16: refused at Ledger.append as well as in MCP).
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")

    with pytest.raises(ValueError, match="job_id"):
        ledger.append(job_id, "submitted", kind="t2v")
    assert not (tmp_path / "ledger.jsonl").exists()


def test_run_job_never_spawns_gflow_for_a_job_id_the_ledger_refuses(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    runner = FakeRunner(ledger, "{}", "__Secure- x")

    async def read_credits():
        return 100

    with pytest.raises(ValueError, match="job_id"):
        asyncio.run(
            gen.run_job(
                job(job_id="__Secure- x"), tmp_path, ledger=ledger, runner=runner, read_credits=read_credits
            )
        )
    assert runner.calls == []


class FakeRunner:
    def __init__(self, ledger, stdout, job_id):
        self.ledger = ledger
        self.stdout = stdout
        self.job_id = job_id
        self.calls = []
        self.submitted_before_call = None

    async def __call__(self, argv):
        self.calls.append(list(argv))
        self.submitted_before_call = self.ledger.has_submitted(self.job_id)
        return 0, self.stdout, ""


def test_run_job_marks_submitted_before_spawning_and_records_credits(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    runner = FakeRunner(ledger, (FIXTURES / "t2i.json").read_text(encoding="utf-8"), "job-x")
    balances = iter([840, 830])

    async def read_credits():
        return next(balances)

    result = asyncio.run(
        gen.run_job(
            job(job_id="job-x", kind="t2i", model="nano2"),
            tmp_path,
            ledger=ledger,
            runner=runner,
            read_credits=read_credits,
        )
    )
    assert runner.submitted_before_call is True
    assert result["outputs"][0]["media_id"] == "31262e48-5af1-4d64-b66c-403f9122aa93"
    last = ledger.rows("job-x")[-1]
    assert last["status"] == "done"
    assert (last["credits_before"], last["credits_after"]) == (840, 830)


def test_run_job_refuses_to_resubmit(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("job-y", "submitted", kind="t2v")
    runner = FakeRunner(ledger, "", "job-y")

    async def read_credits():
        return 1

    with pytest.raises(gen.AlreadySubmitted):
        asyncio.run(
            gen.run_job(
                job(job_id="job-y"), tmp_path, ledger=ledger, runner=runner, read_credits=read_credits
            )
        )
    assert runner.calls == []


def test_run_job_failure_keeps_gflow_problem_detail(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    problem = {
        "error_class": "UiSelectorDriftError",
        "problem": {"title": "Flow UI changed", "detail": "Frames picker tile not found", "route": "i2v"},
        "incident": {"id": "abc-123"},
        "event": "error_raised",
    }
    stderr = "noise line\n" + json.dumps(problem) + "\nUI selector drift\n"

    class FailingRunner(FakeRunner):
        async def __call__(self, argv):
            self.calls.append(list(argv))
            return 23, "", stderr

    runner = FailingRunner(ledger, "", "job-d")

    async def read_credits():
        return 5

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(
            gen.run_job(
                job(job_id="job-d", kind="i2v", initial_frame=Path("f.png")),
                tmp_path,
                ledger=ledger,
                runner=runner,
                read_credits=read_credits,
            )
        )
    assert "Frames picker tile not found" in str(excinfo.value)
    row = ledger.rows("job-d")[-1]
    assert row["status"] == "failed"
    assert row["error_class"] == "UiSelectorDriftError"
    assert row["detail"] == "Frames picker tile not found"
    assert row["incident"] == "abc-123"


def test_run_job_records_failure_and_reraises(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")

    class FailingRunner(FakeRunner):
        async def __call__(self, argv):
            self.calls.append(list(argv))
            return 3, "", "Authentication expired"

    runner = FailingRunner(ledger, "", "job-f")

    async def read_credits():
        return 5

    with pytest.raises(RuntimeError):
        asyncio.run(
            gen.run_job(
                job(job_id="job-f"), tmp_path, ledger=ledger, runner=runner, read_credits=read_credits
            )
        )
    assert ledger.rows("job-f")[-1]["status"] == "failed"


def test_run_job_stops_hard_when_google_flags_unusual_activity(tmp_path):
    # gflow maps WafRejectionError to exit 10 and its default remediation says "Re-authenticate", which is
    # the one thing this migrated account must never do (CLAUDE.md rule 1). Owner decision 2026-09-14:
    # stop, never retry, tell the owner. The failed row still lands in the ledger before the error goes up.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    problem = {
        "error_class": "WafRejectionError",
        "problem": {"title": "WAF rejection (HTTP 403)", "detail": "Flow returned 403", "route": "t2v"},
        "event": "error_raised",
    }

    class FlaggedRunner(FakeRunner):
        async def __call__(self, argv):
            self.calls.append(list(argv))
            return 10, "", json.dumps(problem) + "\n"

    runner = FlaggedRunner(ledger, "", "job-w")

    async def read_credits():
        return 5

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(
            gen.run_job(
                job(job_id="job-w"), tmp_path, ledger=ledger, runner=runner, read_credits=read_credits
            )
        )
    message = str(excinfo.value)
    assert "do not retry" in message
    assert "auth login" in message
    assert len(runner.calls) == 1
    row = ledger.rows("job-w")[-1]
    assert row["status"] == "failed" and row["exit_code"] == 10


def test_run_job_says_an_account_without_flow_access_is_not_a_retry(tmp_path):
    """Plan F T2. gflow 0.78.0 added FlowAccessUnavailableError, exit 39: Flow itself renders a "you don't
    have access" screen for the account. Through 0.73.1 that state surfaced as exit 23 UiSelectorDriftError,
    "gflow's selectors have drifted" (their own A/B, 2026-09-15), which reads like a bug to wait out and invites
    exactly the retry that pays twice. It is not retryable and no re-login fixes it, so say so."""
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    problem = {
        "error_class": "FlowAccessUnavailableError",
        "problem": {"title": "Flow access unavailable", "detail": "Flow shows no access for this account"},
        "event": "error_raised",
    }

    class NoAccessRunner(FakeRunner):
        async def __call__(self, argv):
            self.calls.append(list(argv))
            return 39, "", json.dumps(problem) + "\n"

    runner = NoAccessRunner(ledger, "", "job-a")

    async def read_credits():
        return 5

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(
            gen.run_job(
                job(job_id="job-a"), tmp_path, ledger=ledger, runner=runner, read_credits=read_credits
            )
        )
    message = str(excinfo.value)
    assert "do not retry" in message
    assert "access" in message
    assert "auth login" in message, "a re-login does not restore access; say so before the agent tries it"
    assert len(runner.calls) == 1
    row = ledger.rows("job-a")[-1]
    assert row["status"] == "failed" and row["exit_code"] == 39


def test_every_ledger_row_says_which_code_wrote_it(tmp_path):
    """Paid for on 2026-09-28: 20 credits were read as proof of a fix they never ran. The MCP server process had
    started at 00:33:51, before the commits at 01:01 and 02:02, and Python keeps the modules it loaded, so the tool
    answered with old code while the repo on disk held the new. Nothing in the row said so."""
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("job-code", "done", spent=20)

    row = ledger.rows("job-code")[0]

    assert row["code"] == gen.code_version()
    assert re.fullmatch(r"[0-9a-f]{7,40}(-dirty)?|unknown", row["code"]), row["code"]


def test_the_code_marker_says_dirty_while_the_source_is_uncommitted(monkeypatch, request):
    """A sha alone would name the wrong code for any run made from a working tree: this session spent money at
    three different states of the same sha."""
    calls = []

    def fake_git(args):
        calls.append(args)
        return "abc1234" if "rev-parse" in args else " M src/video/gen.py"

    request.addfinalizer(gen.code_version.cache_clear)
    gen.code_version.cache_clear()
    monkeypatch.setattr(gen, "_git", fake_git)
    assert gen.code_version() == "abc1234-dirty"

    gen.code_version.cache_clear()
    monkeypatch.setattr(gen, "_git", lambda args: "abc1234" if "rev-parse" in args else "")
    assert gen.code_version() == "abc1234"

    gen.code_version.cache_clear()
    monkeypatch.setattr(gen, "_git", lambda args: "")
    assert gen.code_version() == "unknown"


def test_the_code_marker_reads_git_once_per_process(monkeypatch, request):
    """It runs on the money path, once per ledger row otherwise: two subprocesses per row is a tax on every write."""
    runs = []

    request.addfinalizer(gen.code_version.cache_clear)
    gen.code_version.cache_clear()
    monkeypatch.setattr(gen, "_git", lambda args: runs.append(args) or "abc1234")
    for _ in range(5):
        gen.code_version()

    assert len(runs) == 2, runs


def test_the_gflow_process_is_told_which_profile_to_spend_on(monkeypatch):
    """Measured 2026-09-28: with two gflow profiles on the machine, gflow refused to guess ("Cannot pick a default
    profile") and every gen_* died at exit 2. The argv names the profile with --profile; the environment names the
    same one as a second layer, overriding whatever the parent process had set, and the rest of it still arrives."""
    seen = {}

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"{}", b""

    async def fake_exec(*args, **kwargs):
        seen["args"], seen["env"] = args, kwargs.get("env")
        return _Proc()

    monkeypatch.setenv("PATH_CANARY", "still-here")
    monkeypatch.setenv("GFLOW_CLI_PROFILE", "someone-else")
    monkeypatch.setattr(gen.asyncio, "create_subprocess_exec", fake_exec)

    asyncio.run(gen.run_gflow(["image", "t2i", "a boat"], profile="acc2"))

    assert seen["env"]["GFLOW_CLI_PROFILE"] == "acc2", seen["env"] and seen["env"].get("GFLOW_CLI_PROFILE")
    assert seen["env"]["PATH_CANARY"] == "still-here"


def test_run_job_spends_and_reads_the_balance_on_one_and_the_same_profile(monkeypatch, tmp_path):
    """The balance was read on the profile the repo named while gflow spent on the one gflow guessed; they agreed
    only because this machine used to hold one profile. Point gflow's default at another account and the ledger
    brackets account A while account B pays, so `spent`, `balance_moved` and the once-only job_id all lie."""
    used = {"spend": [], "read": []}

    async def fake_gflow(argv, profile="default"):
        used["spend"].append(profile)
        image = {"media_name": "m1", "local_path": "x.jpg", "dimensions": {"width": 1376, "height": 768}}
        return 0, json.dumps({"status": "ok", "images": [image]}), ""

    async def fake_read(profile="default"):
        used["read"].append(profile)
        return 100

    monkeypatch.setattr(gen, "run_gflow", fake_gflow)
    monkeypatch.setattr(gen, "read_credits_live", fake_read)

    asyncio.run(gen.run_job(job(kind="t2i", job_id="job-acc2"), tmp_path, profile="acc2"))

    assert used["spend"] == ["acc2"], used
    assert used["read"] and set(used["read"]) == {"acc2"}, used


def test_a_gflow_error_printed_on_stdout_is_not_lost(tmp_path):
    """Measured 2026-09-28: gflow printed "Cannot pick a default profile..." on STDOUT with an empty stderr, and the
    agent was told only `gflow t2i exit 2 (?):` while the ledger kept `stderr_tail: ""`."""
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    said = "Cannot pick a default profile.\nAvailable: default, old-account\n"

    async def runner(argv):
        return 2, said, ""

    async def read_credits():
        return 1050

    with pytest.raises(RuntimeError, match="Cannot pick a default profile"):
        asyncio.run(
            gen.run_job(
                job(kind="t2i", job_id="job-out"),
                tmp_path,
                ledger=ledger,
                runner=runner,
                read_credits=read_credits,
            )
        )

    failed = ledger.rows("job-out")[-1]
    assert "Cannot pick a default profile" in json.dumps(failed), failed


def test_the_cli_spends_on_the_profile_it_was_given(monkeypatch, tmp_path):
    """`video gen ... --profile` named the account for the balance reads only; gflow still guessed its own."""
    from click.testing import CliRunner

    from video import cli

    handed = {}

    async def fake_run_job(job, out_dir, **kwargs):
        handed.update(kwargs)
        return {"job_id": job.job_id, "outputs": []}

    monkeypatch.setattr(gen, "run_job", fake_run_job)

    result = CliRunner().invoke(
        cli.main, ["gen", "t2i", "a boat", "--project", "P", "--profile", "acc2", "--out", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert handed == {"profile": "acc2"}, handed


def test_no_caller_reads_the_balance_on_a_profile_of_its_own():
    """The mechanical form of plan R's invariant, read from the source so a new caller is covered the day it is
    written: every call to run_job in src/ hands over ONE profile and nothing else that could carry a second account,
    no `read_credits=`, no `runner=`, no `**kwargs`, and run_job is never passed around under another name (review
    of plan R: a regex scan let 7 of 8 such mutants through)."""
    import ast

    src = Path(__file__).resolve().parents[1] / "src"
    offenders, calls = [], 0
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name == "run_job":
                    calls += 1
                    called.add(id(node.func))
                    for keyword in node.keywords:
                        if keyword.arg in (None, "read_credits", "runner"):
                            offenders.append(
                                f"{path.relative_to(src)}:{node.lineno} passes {keyword.arg or '**'}"
                            )
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)) and id(node) not in called:
                name = getattr(node, "attr", None) or getattr(node, "id", None)
                if name == "run_job" and not isinstance(getattr(node, "ctx", None), ast.Store):
                    offenders.append(f"{path.relative_to(src)}:{node.lineno} uses run_job without calling it")
    assert calls >= 3, f"the scan found only {calls} run_job calls; it is not reading what it claims to"
    assert offenders == [], offenders


def test_the_argv_a_paid_call_records_names_the_account_it_spends_on(tmp_path):
    """All five generation commands take --profile (measured 2026-09-28 on gflow 0.78.0; an earlier reading said
    they did not, because zsh passed `image t2i` as ONE word to --help). The flag outranks every other way gflow picks
    a profile, and it lands in the ledger's argv, so each bill says which account paid it."""
    argv = gen.build_argv(job(kind="t2i"), tmp_path, profile="acc2")

    assert argv[argv.index("--profile") + 1] == "acc2", argv
    assert "--profile" not in gen.build_argv(job(kind="t2i"), tmp_path), "no profile named, none invented"


def test_run_job_writes_the_profile_into_the_argv_it_records(tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    seen = []

    async def runner(argv):
        seen.append(argv)
        image = {"media_name": "m1", "local_path": "x.jpg", "dimensions": {"width": 1, "height": 1}}
        return 0, json.dumps({"status": "ok", "images": [image]}), ""

    async def read_credits():
        return 100

    asyncio.run(
        gen.run_job(
            job(kind="t2i", job_id="job-argv"),
            tmp_path,
            ledger=ledger,
            runner=runner,
            read_credits=read_credits,
            profile="acc2",
        )
    )

    recorded = ledger.rows("job-argv")[0]["argv"]
    assert recorded[recorded.index("--profile") + 1] == "acc2", recorded
    assert seen == [recorded], "the argv run must be the argv recorded"


def test_a_stdout_reason_beats_log_noise_on_stderr(tmp_path):
    """Review of plan R: gflow logs JSON to stderr when piped, so a plain refusal on stdout lost to log lines."""
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")

    async def runner(argv):
        return (
            2,
            "Cannot pick a default profile.\n",
            '{"event": "client.context_cookie_state", "level": "info"}\n',
        )

    async def read_credits():
        return 100

    with pytest.raises(RuntimeError, match="Cannot pick a default profile"):
        asyncio.run(
            gen.run_job(
                job(kind="t2i", job_id="job-noise"),
                tmp_path,
                ledger=ledger,
                runner=runner,
                read_credits=read_credits,
            )
        )


def test_the_stdout_tail_carries_no_signed_url_into_the_ledger(tmp_path):
    """Review of plan R: a success JSON printed before a late failure can put a signed lh3 URL in the last 300
    characters of stdout, and the ledger scrub only knows cookie names."""
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    signed = "https://lh3.googleusercontent.com/abc?Expires=1790000000&Signature=" + "S" * 80
    stdout = json.dumps({"status": "ok", "fife_url": signed}) + "\nTraceback: teardown failed\n"

    async def runner(argv):
        return 1, stdout, ""

    async def read_credits():
        return 100

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(
            gen.run_job(
                job(kind="t2i", job_id="job-signed"),
                tmp_path,
                ledger=ledger,
                runner=runner,
                read_credits=read_credits,
            )
        )

    row = json.dumps(ledger.rows("job-signed")[-1])
    assert "Signature=" not in row and "S" * 40 not in row, row
    assert "Signature=" not in str(caught.value), caught.value


def test_no_test_leaves_a_fake_code_marker_behind(tmp_path):
    """The marker tests plant a fake git; its answer outlived them in the cache, so every later ledger row in the run
    claimed to come from `abc1234-dirty` (seen 2026-09-28). A row written here must name the real code."""
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("job-real", "planned")

    assert ledger.rows("job-real")[0]["code"] == gen.code_version()
    assert not gen.code_version().startswith("abc1234"), gen.code_version()
