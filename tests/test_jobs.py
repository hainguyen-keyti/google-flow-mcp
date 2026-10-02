"""Plan AN: a generation is submitted in one call and fetched in a later one. The money path is the composer's own
(`composer._submit`), run with `detach=True`; everything after it reads the ledger and the listing and never clicks."""

import asyncio
import json

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
from video.flow import composer, ingredients, jobs, video

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


# The later calls: they read the ledger and the listing. `_Reader` has no page, so a click or a keystroke would raise.

T0 = 1_000_000.0


class _Reader:
    """A session a later call may only READ through: touching `page` is a failure of the test."""

    @property
    def page(self):
        raise AssertionError("a later call touched the page: it may only read the listing and the balance")


def _started_job(tmp_path, job_id="job-1", *, workflow="w-job", before=("w-before",), body_check=None, at=T0):
    """The two rows a detached submit leaves, written at `at`."""
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        job_id, "submitted", kind="video", project="p-1", prompt=PROMPT, quoted_credits=10, credits_before=200
    )
    ledger.append(
        job_id,
        "started",
        workflow_id=workflow,
        workflows_before=list(before),
        prompt=PROMPT,
        prompt_text=None,
        flow={"workflow_id": workflow, "statuses": [6], "reasons": []},
        **({"body_check": body_check} if body_check else {}),
    )
    rows = [json.loads(line) for line in ledger.path.read_text(encoding="utf-8").split("\n") if line.strip()]
    for row in rows:
        if row["job_id"] == job_id:
            row["ts"] = at
    ledger.path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return ledger


def _world(monkeypatch, tmp_path, listing, *, balance=190, fetch_error=None):
    """The listing and the balance a later call reads, and the download a collect asks for."""
    log = []

    async def snapshot(session, project_id, attempts=4):
        log.append(f"snapshot {project_id}")
        return [dict(row) for row in listing], set()

    async def credits(session):
        log.append("credits")
        return {"balance": balance}

    async def fetch_720(session, record, stem, attempts=6, *, project_id=None):
        log.append(f"fetch {record['workflow_id']} into {stem.name}")
        if fetch_error is not None:
            raise fetch_error
        return tmp_path / f"{stem.name}.mp4"

    monkeypatch.setattr(jobs.composer, "snapshot", snapshot)
    monkeypatch.setattr(jobs.reader, "credits", credits)
    monkeypatch.setattr(jobs.composer, "fetch_720", fetch_720)
    return log


def _status(ledger, job_id="job-1", now=T0 + 60):
    return asyncio.run(jobs.status(_Reader(), ledger, job_id, now=now))


def _collect(ledger, job_id="job-1", now=T0 + 60, others=()):
    return asyncio.run(jobs.collect(_Reader(), ledger, job_id, now=now, others=list(others)))


@pytest.mark.parametrize(
    ("listing", "state"),
    [
        ([_video("w-before", "old"), _video("w-job", PROMPT, done=False)], "rendering"),
        ([_video("w-before", "old"), _video("w-job", PROMPT)], "ready"),
        ([_video("w-before", "old")], "not_listed"),
    ],
)
def test_status_says_where_a_started_job_stands_and_writes_nothing(monkeypatch, tmp_path, listing, state):
    ledger = _started_job(tmp_path)
    log = _world(monkeypatch, tmp_path, listing)
    rows_before = ledger.rows()

    said = _status(ledger)

    assert said["state"] == state and said["job_id"] == "job-1" and said["workflow_id"] == "w-job"
    assert (said["credits_before"], said["credits_now"], said["quoted_credits"]) == (200, 190, 10)
    assert said["age_s"] == 60 and said.get("media_id") == ("m-w-job" if state != "not_listed" else None)
    assert ledger.rows() == rows_before and log == ["snapshot p-1", "credits"]


def test_a_clip_is_claimed_by_the_workflow_flow_named_and_by_nothing_else(monkeypatch, tmp_path):
    # AN3 (3), I-money-5: another new record carrying this job's very prompt is another job's clip.
    ledger = _started_job(tmp_path)
    _world(monkeypatch, tmp_path, [_video("w-before", "old"), _video("w-other", PROMPT)])

    assert _status(ledger)["state"] == "not_listed"
    assert _collect(ledger)["state"] == "not_listed" and len(ledger.rows()) == 2


def test_with_no_workflow_named_the_one_new_record_of_the_prompt_is_the_clip(monkeypatch, tmp_path):
    ledger = _started_job(tmp_path, workflow=None)
    _world(monkeypatch, tmp_path, [_video("w-before", PROMPT), _video("w-new", PROMPT)])

    said = _status(ledger)

    # The record listed before the submit carries the prompt too and is not new, so it is no candidate.
    assert said["state"] == "ready" and said["media_id"] == "m-w-new" and said["workflow_id"] is None


def test_with_no_workflow_named_two_new_records_of_the_prompt_are_never_chosen_between(monkeypatch, tmp_path):
    # AN3 (4). The job may have made one of them; which one cannot be told, so it is settled unknown.
    ledger = _started_job(tmp_path, workflow=None)
    _world(monkeypatch, tmp_path, [_video("w-a", PROMPT), _video("w-b", PROMPT)])

    assert _status(ledger)["state"] == "ambiguous"
    with pytest.raises(RuntimeError, match="2 new records") as said:
        _collect(ledger)

    settled = ledger.rows()[-1]
    assert settled["status"] == "unknown" and settled["candidates"] == ["m-w-a", "m-w-b"]
    assert "never run this job again under a new job_id" in str(said.value)
    assert outcome.classify(ledger.rows())["code"] == "UNKNOWN"


def test_collect_fetches_the_finished_clip_and_writes_the_one_settled_row(monkeypatch, tmp_path):
    ledger = _started_job(tmp_path)
    log = _world(monkeypatch, tmp_path, [_video("w-before", "old"), _video("w-job", PROMPT)])

    # The job's own rows sit among "the rows of every ledger" too, and share nothing with itself.
    answer = _collect(ledger, others=[{"job_id": "job-1", "status": "started", "ts": T0 + 1}])

    assert answer["state"] == "collected" and answer["media_id"] == "m-w-job"
    assert answer["path"].endswith(".mp4") and "m-w-job_" in answer["path"]
    assert (answer["spent"], answer["spent_from"], answer["credits_after"]) == (10, "bracket", 190)
    settled = ledger.rows()[-1]
    assert (settled["status"], settled["spent"], settled["media_id"]) == ("done", 10, "m-w-job")
    assert (
        settled["credits_before"] == 200
        and settled["credits_after"] == 190
        and settled["path"] == answer["path"]
    )
    assert [step.split()[0] for step in log] == ["snapshot", "credits", "fetch"]
    said = outcome.classify(ledger.rows())
    assert (said["code"], said["charged"]) == ("DONE", 10)


def test_a_job_already_settled_is_answered_from_its_row_with_no_look_at_flow(monkeypatch, tmp_path):
    # AN3 (5), I-money-6: one settled row per job, whatever is called after it.
    ledger = _started_job(tmp_path)
    log = _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)])
    first = _collect(ledger)
    del log[:]

    again = _collect(ledger)
    seen = _status(ledger)

    assert again["state"] == "settled" and again["path"] == first["path"] and again["media_id"] == "m-w-job"
    assert seen["state"] == "settled" and seen["path"] == first["path"]
    assert log == [] and [row["status"] for row in ledger.rows()] == ["submitted", "started", "done"]


def test_a_balance_other_jobs_moved_is_never_written_as_this_jobs_own_spend(monkeypatch, tmp_path):
    # AN3 (7), I-read-2: two jobs rendering at once share the balance between their submits and their collects.
    ledger = _started_job(tmp_path)
    _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)], balance=170)
    other = {"job_id": "job-2", "status": "submitted", "ts": T0 + 5}

    answer = _collect(ledger, others=[other])

    assert (answer["spent"], answer["spent_from"]) == (10, "quoted")
    settled = ledger.rows()[-1]
    assert settled["spent"] == 10 and settled["spent_from"] == "quoted" and settled["credits_after"] == 170
    # A row of another job from BEFORE this job's submit shares nothing.
    earlier = _started_job(tmp_path / "b")
    _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)], balance=190)
    alone = asyncio.run(
        jobs.collect(_Reader(), earlier, "job-1", now=T0 + 60, others=[{**other, "ts": T0 - 5}])
    )
    assert (alone["spent"], alone["spent_from"]) == (10, "bracket")


def test_an_own_bracket_that_is_not_the_quoted_price_is_said(monkeypatch, tmp_path):
    ledger = _started_job(tmp_path)
    _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)], balance=180)

    answer = _collect(ledger)

    assert (answer["spent"], answer["spent_from"]) == (20, "bracket")
    assert answer["balance_moved"] == {"quoted": 10, "moved": 20}


def test_a_clip_still_rendering_is_left_for_a_later_collect(monkeypatch, tmp_path):
    ledger = _started_job(tmp_path)
    log = _world(monkeypatch, tmp_path, [_video("w-job", PROMPT, done=False)])

    answer = _collect(ledger)

    assert answer["state"] == "rendering" and len(ledger.rows()) == 2
    assert not [step for step in log if step.startswith("fetch")]


def test_a_finished_clip_whose_file_did_not_come_back_is_not_settled(monkeypatch, tmp_path):
    # The clip is paid for and exists: a later collect, or flow_download, fetches it. Writing a row here would
    # close the job with no file.
    ledger = _started_job(tmp_path)
    _world(
        monkeypatch, tmp_path, [_video("w-job", PROMPT)], fetch_error=RuntimeError("no rendition answered")
    )

    with pytest.raises(RuntimeError, match="no file came back") as said:
        _collect(ledger)

    assert "job_collect again" in str(said.value) and "m-w-job" in str(said.value)
    assert len(ledger.rows()) == 2
    _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)])
    assert _collect(ledger)["state"] == "collected"


@pytest.mark.parametrize(
    ("age", "balance", "others", "status", "spent"),
    [
        (60, 200, (), None, None),
        (jobs.NOT_LISTED_S + 1, 200, (), "failed", 0),
        (jobs.NOT_LISTED_S + 1, 190, (), "unknown", 10),
        (
            jobs.NOT_LISTED_S + 1,
            200,
            ({"job_id": "job-2", "status": "started", "ts": T0 + 5},),
            "unknown",
            None,
        ),
    ],
    ids=[
        "too soon to say",
        "never shown, balance unmoved",
        "never shown, balance moved",
        "never shown, balance shared",
    ],
)
def test_a_job_that_never_shows_is_settled_only_once_it_is_late(
    monkeypatch, tmp_path, age, balance, others, status, spent
):
    ledger = _started_job(tmp_path)
    _world(monkeypatch, tmp_path, [_video("w-before", "old")], balance=balance)

    if status is None:
        assert _collect(ledger, now=T0 + age)["state"] == "not_listed" and len(ledger.rows()) == 2
        return
    with pytest.raises(RuntimeError) as said:
        _collect(ledger, now=T0 + age, others=others)

    settled = ledger.rows()[-1]
    assert (settled["status"], settled["spent"]) == (status, spent), settled
    code = outcome.classify(ledger.rows())["code"]
    assert code == ("NOTHING_GENERATED" if status == "failed" else "UNKNOWN")
    assert outcome.classify(ledger.rows())["retryable"] is False
    assert ("never run this job again under a new job_id" in str(said.value)) is (status == "unknown")


def test_a_collected_clip_whose_request_went_out_wrong_is_an_error_though_it_was_paid(monkeypatch, tmp_path):
    wrong = {"ok": False, "model_keys": ["abra_t2v_10s"], "missing": [], "rpcid": "eb1hJf"}
    ledger = _started_job(tmp_path, body_check=wrong)
    _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)])

    with pytest.raises(
        RuntimeError, match="10 credits were spent, but Flow's submit request did not match"
    ) as said:
        _collect(ledger)

    assert ledger.rows()[-1]["status"] == "done" and "abra_t2v_10s" in str(said.value)


@pytest.mark.parametrize(
    "rows", [[], [("submitted", {"kind": "video", "project": "p-1", "credits_before": 200})]]
)
def test_a_job_that_was_not_started_by_job_submit_is_not_collected(monkeypatch, tmp_path, rows):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    for status, fields in rows:
        ledger.append("job-1", status, **fields)
    log = _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)])

    with pytest.raises(LookupError, match="job_submit"):
        _collect(ledger)
    with pytest.raises(LookupError, match="job_submit"):
        _status(ledger)

    assert log == [] and len(ledger.rows()) == len(rows)
