"""Plan AN: a generation is submitted in one call and fetched in a later one. The money path is the composer's own
(`composer._submit`), run with `detach=True`; everything after it reads the ledger and the listing and never clicks."""

import pytest
from test_ingredients import (
    JOB_WORKFLOW,
    KEPT_ONE_VOICE,
    PROMPT,
    SUBMIT_REPLY,
    _install,
    _paid_run,
    _paid_world,
    _submit,
    _SubmitSession,
    _video,
)
from test_video import _driver_world, _run

from video import gen, outcome
from video.flow import composer, ingredients, video

WENT_OUT_WRONG = "the job was started, but Flow's submit request did not match"


def _rows(tmp_path):
    return gen.Ledger(tmp_path / "ledger.jsonl").rows()


def test_a_detached_submit_returns_once_the_request_has_left_and_says_how_to_find_its_clip(
    monkeypatch, tmp_path
):
    log = []
    _install(monkeypatch, tmp_path, log, replies={"click": [SUBMIT_REPLY]})

    result = _submit(_SubmitSession(log), tmp_path, log, detach=True, strict_output=True)

    assert result["state"] == "started" and result["workflow_id"] == JOB_WORKFLOW
    assert result["quoted_credits"] == 12 and result["credits_before"] == 200
    # Nothing is read, waited for or fetched once the request is out: that is the later calls' work.
    after = log[log.index("click") + 1 :]
    assert [step for step in after if step in ("snapshot", "credits") or step.startswith("fetch")] == [], (
        after
    )
    submitted, started = _rows(tmp_path)
    assert (submitted["status"], started["status"]) == ("submitted", "started")
    assert started["workflow_id"] == JOB_WORKFLOW and started["workflows_before"] == ["w-before"]
    assert started["prompt"] == PROMPT and started["rpcids"] == ["MZZa6b"]
    assert outcome.classify(_rows(tmp_path))["code"] == "STARTED"


@pytest.mark.parametrize(
    "heard", [{}, {"jwpduf": [[]]}], ids=["no request at all", "a request that is no submit"]
)
def test_a_submit_nobody_saw_leave_is_never_called_started(monkeypatch, tmp_path, heard):
    # AN3 (1). Flow sends the submit about 20 s after the click; a call that left before it would cancel it, and one
    # that reported "started" without it would have the caller wait for a clip that was never asked for.
    log = []
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 200, 200))

    async def nothing_left(session, click, *, settle=30.0, patience=90.0):
        await click()
        return heard

    monkeypatch.setattr(composer.clips, "_await_submit", nothing_left)

    with pytest.raises(RuntimeError):
        _submit(_SubmitSession(log), tmp_path, log, detach=True, strict_output=True)

    statuses = [row["status"] for row in _rows(tmp_path)]
    assert "started" not in statuses and statuses[0] == "submitted" and len(statuses) == 2, statuses


def test_a_detached_submit_with_no_reply_heard_names_no_workflow(monkeypatch, tmp_path):
    # The request left, Flow's reply was not read: the clip is then found by its prompt, never guessed here.
    log = []
    _install(monkeypatch, tmp_path, log)

    result = _submit(_SubmitSession(log), tmp_path, log, detach=True, strict_output=True)

    assert result["state"] == "started" and result["workflow_id"] is None
    assert _rows(tmp_path)[1]["workflow_id"] is None


def test_a_detached_submit_of_several_clips_is_refused_before_anything(monkeypatch, tmp_path):
    # How Flow names the workflows of x2 to x4 was never measured, so their clips could not be claimed later.
    log = []
    _install(monkeypatch, tmp_path, log)

    with pytest.raises(ValueError, match="one clip"):
        _submit(_SubmitSession(log), tmp_path, log, detach=True, strict_output=True, count=2)

    assert log == [] and _rows(tmp_path) == []


def test_a_run_that_is_not_detached_is_what_it_was(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", f"Thu {PROMPT}")])

    result = _submit(_SubmitSession(log), tmp_path, log)

    assert [row["status"] for row in _rows(tmp_path)] == ["submitted", "done"]
    assert "state" not in result and result["spent"] == 12


def test_a_started_job_is_neither_a_failure_nor_a_retry():
    rows = [
        {"status": "submitted", "kind": "video", "project": "P", "prompt": "a", "credits_before": 100},
        {"status": "started", "workflow_id": "w-1"},
    ]

    said = outcome.classify(rows)

    assert (said["code"], said["charged"], said["retryable"]) == ("STARTED", None, False)
    assert "job_status" in said["advice"] and "job_collect" in said["advice"] and "never" in said["advice"]
    # A started row with no intent row before it says nothing: the job was never recorded as submitted.
    assert outcome.classify([{"status": "started"}])["code"] == "NOT_SUBMITTED"
    assert outcome.retry_refusal("a", {"a": rows}, project="P", prompt="a") is not None


def test_the_video_driver_hands_detach_to_whichever_mode_runs(monkeypatch):
    calls = _driver_world(monkeypatch)

    _run(detach=True)
    assert calls["submit"]["detach"] is True
    _run()
    assert calls["submit"]["detach"] is False
    _run(model="veo-lite", media_ids=["m-teapot"], detach=True)
    assert calls["ingredients"]["detach"] is True
    _run(model="veo-lite", media_ids=["m-teapot"])
    assert calls["ingredients"]["detach"] is False


def _started(body_check):
    async def submit(session, project_id, **kwargs):
        submit.handed = kwargs
        return {
            "job_id": kwargs["job_id"],
            "state": "started",
            "workflow_id": "w-1",
            "body_check": body_check,
        }

    return submit


def test_a_detached_frames_job_whose_request_went_out_wrong_says_so_at_once(monkeypatch):
    # The blocking driver says "N credits were spent, but ..." after the wait. A detached job has spent nothing yet
    # that anyone has read, and it IS running: the caller is told now, in words that are true now.
    _driver_world(monkeypatch)
    wrong = {"ok": False, "model_keys": ["abra_t2v_10s"], "missing": [], "rpcid": "eb1hJf"}
    monkeypatch.setattr(video.composer, "_submit", _started(wrong))

    with pytest.raises(RuntimeError, match=WENT_OUT_WRONG) as said:
        _run(model="omni-flash", resolution="720p", duration=4, detach=True)

    assert "credits were spent" not in str(said.value) and "job_collect" in str(said.value)


def test_the_ingredients_driver_detaches_and_reads_no_recipe(monkeypatch, tmp_path):
    log = []
    _paid_world(monkeypatch, log, recipe=KEPT_ONE_VOICE)
    submit = _started({"ok": True})
    monkeypatch.setattr(ingredients.composer, "_submit", submit)

    result = _paid_run(tmp_path, detach=True)

    assert submit.handed["detach"] is True and result["state"] == "started"
    assert "recipe_check" not in result and not [line for line in log if line.startswith("recipe")]
    assert "agent True" in log, "Agent mode is put back when the call returns, as after a blocking run"


def test_a_detached_ingredients_job_whose_request_dropped_a_reference_says_so_at_once(monkeypatch, tmp_path):
    log = []
    _paid_world(monkeypatch, log, recipe=KEPT_ONE_VOICE)
    wrong = {"ok": False, "model_keys": ["veo_3_1_r2v_lite"], "missing": ["achird"], "rpcid": "MZZa6b"}
    monkeypatch.setattr(ingredients.composer, "_submit", _started(wrong))

    with pytest.raises(RuntimeError, match=WENT_OUT_WRONG) as said:
        _paid_run(tmp_path, detach=True)

    assert "missing ['achird']" in str(said.value) and "credits were spent" not in str(said.value)
