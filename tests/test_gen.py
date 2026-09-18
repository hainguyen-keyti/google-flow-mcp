import asyncio
import json
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
