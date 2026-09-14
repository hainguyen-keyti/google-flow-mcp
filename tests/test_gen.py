import asyncio
import json
from pathlib import Path

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
