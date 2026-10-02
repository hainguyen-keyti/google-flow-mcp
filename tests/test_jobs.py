"""Plan AN: a generation is submitted in one call and fetched in a later one. The money path is the composer's own
(`composer._submit`), run with `detach=True`; everything after it reads the ledger and the listing and never clicks."""

import asyncio
import json
import random
from pathlib import Path

import pytest
from test_ingredients import (
    JOB_WORKFLOW,
    KEPT_ONE_VOICE,
    PROMPT,
    SUBMIT_REPLY,
    _install,
    _paid_run,
    _paid_world,
    _status_reply,
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
    # Only the last run speaks: a started row of an earlier run says nothing of a later one that crashed after its
    # intent row (the CLI can run one id twice).
    again = [*rows, {"status": "failed", "spent": 0}, {"status": "submitted", "kind": "video"}]
    assert outcome.classify(again)["code"] == "UNKNOWN"


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
    # What was asked rides on the started row, for job_collect to hold the clip's recipe to: the same references
    # the blocking run checks, so the recipe it passes is passed here and one that dropped a voice is not.
    asked = submit.handed["started_extra"]["references"]
    assert [set(each) for each in asked] == [{"kind", "id", "mention_ids"}] * 3
    kept = [ingredients.Reference(a["kind"], a["id"], "", frozenset(a["mention_ids"])) for a in asked]
    assert ingredients.recipe_check(KEPT_ONE_VOICE, kept)["ok"] is True
    assert ingredients.recipe_check({**KEPT_ONE_VOICE, "voices": []}, kept)["missing"] == ["achird"]
    assert json.loads(json.dumps(asked)) == asked, "the row is written as JSON"


def test_a_blocking_ingredients_run_puts_nothing_new_on_its_rows(monkeypatch, tmp_path):
    # Non-goal of plan AN: what a blocking run records does not change.
    log = []
    _paid_world(monkeypatch, log, recipe=KEPT_ONE_VOICE)
    submit = _started({"ok": True})
    monkeypatch.setattr(ingredients.composer, "_submit", submit)

    _paid_run(tmp_path)

    assert submit.handed.get("started_extra") is None


def test_what_was_asked_is_written_on_the_started_row_and_on_no_other(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, replies={"click": [SUBMIT_REPLY]})
    asked = {"references": [{"kind": "voice", "id": "achird", "mention_ids": ["achird"]}]}

    async def verify(session):
        return {"prompt_text": f"Thu {PROMPT}"}

    options = {"detach": True, "strict_output": True, "started_extra": asked, "verify": verify}
    _submit(_SubmitSession(log), tmp_path, log, **options)

    submitted, started = _rows(tmp_path)
    assert started["references"] == asked["references"] and "references" not in submitted
    # What the prompt box held is what Flow stores for a job with chips, so the later claim by prompt needs it.
    assert started["prompt_text"] == f"Thu {PROMPT}" and started["prompt"] == PROMPT


def test_a_job_flow_failed_before_the_page_left_is_settled_where_its_reason_is_heard(monkeypatch, tmp_path):
    # Review of plan AN: Flow's status 4 came inside the submit window. Leaving then gives up the only page that
    # hears the reason, and has job_collect wait ten minutes for a clip Flow already refused.
    log = []
    refused = _status_reply(4, extra=[["PUBLIC_ERROR_AUDIO_FILTERED"]])
    replies = {"click": [SUBMIT_REPLY, _status_reply(2), refused]}
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies=replies)

    with pytest.raises(RuntimeError, match="Flow refused this job and charged nothing"):
        _submit(_SubmitSession(log), tmp_path, log, detach=True, strict_output=True)

    assert [row["status"] for row in _rows(tmp_path)] == ["submitted", "failed"]
    assert outcome.classify(_rows(tmp_path))["code"] == "AUDIO_FILTERED"


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


def _started_job(
    tmp_path,
    job_id="job-1",
    *,
    workflow="w-job",
    before=("w-before",),
    body_check=None,
    at=T0,
    prompt_text=None,
    references=None,
):
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
        prompt_text=prompt_text,
        flow={"workflow_id": workflow, "statuses": [6], "reasons": []},
        **({"body_check": body_check} if body_check else {}),
        **({"references": references} if references else {}),
    )
    rows = [json.loads(line) for line in ledger.path.read_text(encoding="utf-8").split("\n") if line.strip()]
    for row in rows:
        if row["job_id"] == job_id:
            row["ts"] = at
    ledger.path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return ledger


class _Log(list):
    """The steps a later call took, and where each download was asked to put its file and for which project."""

    def __init__(self):
        super().__init__()
        self.fetched = []


def _world(monkeypatch, tmp_path, listing, *, balance=190, fetch_error=None, recipe=None):
    """The listing and the balance a later call reads, the download a collect asks for, and the recipe it reads
    back. The download behaves as `download._write_new` does: it writes `<stem>.mp4` and refuses to overwrite."""
    log = _Log()

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
        path = stem.with_name(stem.name + ".mp4")
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"clip")
        log.fetched.append((stem, project_id))
        return path

    async def read_recipe(session, project_id, media_id, *, workflow_id=None):
        log.append(f"recipe {media_id} {workflow_id} of {project_id}")
        if isinstance(recipe, Exception):
            raise recipe
        return recipe or {}

    monkeypatch.setattr(jobs.composer, "snapshot", snapshot)
    monkeypatch.setattr(jobs.reader, "credits", credits)
    monkeypatch.setattr(jobs.composer, "fetch_720", fetch_720)
    monkeypatch.setattr(jobs.reader, "recipe", read_recipe)
    return log


def _status(ledger, job_id="job-1", now=T0 + 60, others=()):
    return asyncio.run(jobs.status(_Reader(), ledger, job_id, now=now, others=lambda: list(others)))


def _collect(ledger, job_id="job-1", now=T0 + 60, others=()):
    return asyncio.run(jobs.collect(_Reader(), ledger, job_id, now=now, others=lambda: list(others)))


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


def _theirs(*rows):
    """Rows of job-B as another ledger holds them: submitted five seconds after this job, then whatever follows."""
    first = {"job_id": "job-B", "status": "submitted", "project": "p-1", "prompt": PROMPT, "ts": T0 + 5}
    return [first, *({"job_id": "job-B", "ts": T0 + 30, **row} for row in rows)]


@pytest.mark.parametrize(
    "named",
    [
        {"status": "started", "workflow_id": "w-B"},
        {"status": "done", "media_id": "m-w-B"},
        {"status": "done", "outputs": [{"media_id": "m-w-B", "path": None}]},
    ],
    ids=["its started row", "its done row", "its outputs"],
)
def test_with_no_workflow_named_a_clip_another_job_names_is_never_this_jobs(monkeypatch, tmp_path, named):
    # Review of plan AN: Flow dropped this job and job-B, same prompt, made w-B. Taking it would settle one clip
    # under two jobs and call this job DONE, charged its quote, though Flow made nothing for it (I-money-5).
    ledger = _started_job(tmp_path, workflow=None)
    _world(monkeypatch, tmp_path, [_video("w-before", "old"), _video("w-B", PROMPT)], balance=200)
    others = _theirs(named)

    assert _status(ledger, others=others)["state"] == "not_listed"
    assert _collect(ledger, others=others)["state"] == "not_listed" and len(ledger.rows()) == 2
    # With nothing naming it, the one new clip of the prompt is this job's, as before.
    assert _status(ledger, others=_theirs())["media_id"] == "m-w-B"


def test_two_unnamed_jobs_of_one_prompt_cannot_tell_one_clip_apart(monkeypatch, tmp_path):
    # Both submits went unheard and one clip shows: whose it is cannot be told, so neither takes it.
    ledger = _started_job(tmp_path, workflow=None)
    _world(monkeypatch, tmp_path, [_video("w-new", PROMPT)])
    rival = _theirs({"status": "started", "workflow_id": None, "prompt": PROMPT})

    assert _status(ledger, others=rival)["state"] == "ambiguous"
    with pytest.raises(RuntimeError, match="job-B") as said:
        _collect(ledger, others=rival)

    settled = ledger.rows()[-1]
    assert settled["status"] == "unknown" and settled["candidates"] == ["m-w-new"]
    assert "none is taken" in str(said.value)


@pytest.mark.parametrize(
    "rival",
    [
        _theirs({"status": "started", "workflow_id": None, "prompt": "another prompt"}),
        _theirs(
            {"status": "started", "workflow_id": None, "prompt": PROMPT}, {"status": "failed", "spent": 0}
        ),
        [
            {**row, "project": "p-2"}
            for row in _theirs({"status": "started", "workflow_id": None, "prompt": PROMPT})
        ],
    ],
    ids=["another prompt", "already settled", "another project"],
)
def test_an_unnamed_job_that_cannot_own_the_clip_is_no_rival(monkeypatch, tmp_path, rival):
    ledger = _started_job(tmp_path, workflow=None)
    _world(monkeypatch, tmp_path, [_video("w-new", PROMPT)])

    assert _status(ledger, others=rival)["state"] == "ready"


def test_an_unnamed_job_is_not_claimed_while_the_ledgers_cannot_be_read(monkeypatch, tmp_path):
    # Whether another job names the one new clip is exactly what the unread rows would say.
    ledger = _started_job(tmp_path, workflow=None)
    _world(monkeypatch, tmp_path, [_video("w-new", PROMPT)])

    def torn():
        raise json.JSONDecodeError("Unterminated string", '{"job_id": "jo', 14)

    for call in (jobs.status, jobs.collect):
        with pytest.raises(RuntimeError, match="cannot be read") as said:
            asyncio.run(call(_Reader(), ledger, "job-1", now=T0 + 60, others=torn))
        assert "call again" in str(said.value)
    assert len(ledger.rows()) == 2


def test_the_text_the_prompt_box_held_claims_a_clip_whose_record_carries_the_chips(monkeypatch, tmp_path):
    # A job with chips: Flow stores the chip titles joined to the typed text, which is what the box read.
    boxed = f"LilyNight {PROMPT}"
    ledger = _started_job(tmp_path, workflow=None, prompt_text=boxed)
    _world(monkeypatch, tmp_path, [_video("w-new", boxed)])

    assert _status(ledger)["media_id"] == "m-w-new"
    assert _status(_started_job(tmp_path / "typed-only", workflow=None))["state"] == "not_listed"


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
    # The file lands beside the job's own ledger, and the download knows the project for its editor fallback.
    ((stem, project),) = log.fetched
    assert stem.parent == ledger.path.parent and project == "p-1"
    assert Path(answer["path"]).parent == ledger.path.parent and Path(answer["path"]).is_file()
    said = outcome.classify(ledger.rows())
    assert (said["code"], said["charged"]) == ("DONE", 10) and "charged_from" not in said


def test_a_file_left_by_a_collect_that_died_is_kept_and_does_not_block_the_next(monkeypatch, tmp_path):
    # Killed after the download and before the row: the file is on disk and no row names it. Downloads never
    # overwrite, so without another name every later collect would fail on that file for good.
    ledger = _started_job(tmp_path)
    log = _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)])
    orphan = tmp_path / f"m-w-job_{composer._job_digest('job-1')[:8]}.mp4"
    orphan.write_bytes(b"half a clip")

    answer = _collect(ledger)

    assert answer["state"] == "collected" and Path(answer["path"]) != orphan
    assert Path(answer["path"]).read_bytes() == b"clip" and orphan.read_bytes() == b"half a clip"
    assert len(log.fetched) == 1 and ledger.rows()[-1]["path"] == answer["path"]


ASKED = [
    {"kind": "media", "id": "m-img", "mention_ids": ["w-img"]},
    {"kind": "voice", "id": "achird", "mention_ids": ["achird"]},
]
KEPT_ALL = {"reference_images": [{"workflow_id": "w-img"}], "voices": [{"voice": "achird"}], "characters": []}


def test_a_collected_ingredients_clip_has_its_recipe_read_back_as_gen_video_does(monkeypatch, tmp_path):
    # Review of plan AN: gen_video raises when the clip dropped a reference; a detached job must not end DONE in
    # silence for the same clip. What was asked rides on the started row, since the later call has no composer.
    ledger = _started_job(tmp_path, references=ASKED)
    log = _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)], recipe=KEPT_ALL)

    answer = _collect(ledger)

    assert answer["recipe_check"] == {"ok": True, "missing": [], "unexpected": []}
    assert ledger.rows()[-1]["recipe_check"]["ok"] is True
    # The recipe is read before the download, so nothing is awaited between the file and the row that names it.
    assert log[-2] == "recipe m-w-job w-job of p-1" and log[-1].startswith("fetch w-job")


def test_a_collected_clip_that_dropped_a_reference_is_an_error_though_it_was_paid(monkeypatch, tmp_path):
    ledger = _started_job(tmp_path, references=ASKED)
    _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)], recipe={**KEPT_ALL, "voices": []})

    with pytest.raises(RuntimeError, match="kept another set: missing \\['achird'\\]") as said:
        _collect(ledger)

    settled = ledger.rows()[-1]
    assert settled["status"] == "done" and settled["recipe_check"]["missing"] == ["achird"]
    assert "10 credits were spent" in str(said.value) and "under a new job_id" in str(said.value)
    assert settled["path"] in str(said.value)


def test_a_recipe_that_cannot_be_read_does_not_fail_a_paid_clip(monkeypatch, tmp_path):
    ledger = _started_job(tmp_path, references=ASKED)
    _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)], recipe=RuntimeError("no Zzl0ze frame"))

    answer = _collect(ledger)

    assert answer["state"] == "collected" and answer["recipe_check"]["ok"] is None
    assert "no Zzl0ze frame" in ledger.rows()[-1]["recipe_check"]["error"]


def test_a_job_that_asked_for_no_reference_reads_no_recipe(monkeypatch, tmp_path):
    ledger = _started_job(tmp_path)
    log = _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)], recipe=RuntimeError("never asked"))

    answer = _collect(ledger)

    assert "recipe_check" not in answer and not [step for step in log if step.startswith("recipe")]


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
    # The typed outcome says the figure is the quote: "charged" otherwise reads as what left the account.
    said = outcome.classify(ledger.rows())
    assert (said["code"], said["charged"], said["charged_from"]) == ("DONE", 10, "quoted")


def test_a_balance_that_cannot_be_read_is_never_written_as_a_bracket(monkeypatch, tmp_path):
    ledger = _started_job(tmp_path)
    _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)], balance=None)

    answer = _collect(ledger)

    assert (answer["spent"], answer["spent_from"], answer["credits_after"]) == (10, "quoted", None)
    assert ledger.rows()[-1]["spent_from"] == "quoted"


@pytest.mark.parametrize("balance", [170, 200], ids=["moved", "unmoved"])
def test_two_candidates_on_a_shared_balance_say_nothing_of_what_was_charged(monkeypatch, tmp_path, balance):
    # Review of plan AN: `spent` was null and the row's two balances still let the outcome say charged=30, or 0.
    ledger = _started_job(tmp_path, workflow=None)
    _world(monkeypatch, tmp_path, [_video("w-a", PROMPT), _video("w-b", PROMPT)], balance=balance)

    with pytest.raises(RuntimeError, match="2 new records"):
        _collect(ledger, others=[{"job_id": "job-2", "status": "submitted", "ts": T0 + 5}])

    settled = ledger.rows()[-1]
    assert (settled["status"], settled["spent"], settled["balance_shared"]) == ("unknown", None, True)
    said = outcome.classify(ledger.rows())
    assert (said["code"], said["charged"]) == ("UNKNOWN", None)


@pytest.mark.parametrize(
    "ended",
    [
        {"status": "pending", "spent": 10},
        {"status": "unknown", "spent": None},
        {"status": "failed", "spent": 0, "flow": {"statuses": [6, 2], "reasons": []}},
    ],
    ids=["pending", "unknown", "never shown"],
)
def test_a_job_that_ended_open_before_this_submit_shares_the_balance(monkeypatch, tmp_path, ended):
    # Its own outcome says it may still finish and bill; a charge landing later lands in this job's bracket.
    ledger = _started_job(tmp_path)
    _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)], balance=180)
    earlier = [
        {"job_id": "job-0", "status": "submitted", "kind": "video", "ts": T0 - 90},
        {"job_id": "job-0", "ts": T0 - 60, **ended},
    ]

    answer = _collect(ledger, others=earlier)

    assert (answer["spent"], answer["spent_from"]) == (10, "quoted")


def test_a_job_still_in_flight_from_before_this_submit_shares_the_balance_too(monkeypatch, tmp_path):
    # Two jobs render at once and the later one is collected first: the earlier one has no row after this job's
    # submit, and its charge, or the refund of its failure, still lands inside this job's bracket.
    ledger = _started_job(tmp_path)
    _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)], balance=180)
    flying = [
        {"job_id": "job-0", "status": "submitted", "ts": T0 - 30},
        {"job_id": "job-0", "status": "started", "ts": T0 - 10},
    ]

    answer = _collect(ledger, others=flying)

    assert (answer["spent"], answer["spent_from"]) == (10, "quoted")
    assert "balance_moved" not in answer and ledger.rows()[-1]["spent_from"] == "quoted"


@pytest.mark.parametrize(
    "others",
    [
        [
            {"job_id": "job-0", "status": "submitted", "ts": T0 - 30},
            {"job_id": "job-0", "status": "done", "ts": T0 - 5},
        ],
        [{"job_id": "job-0", "status": "submitted", "ts": T0 - jobs.IN_FLIGHT_S - 1}],
        [{"job_id": "job-0", "status": "noted", "ts": T0 + 5}],
        [
            {"job_id": "job-0", "status": "submitted", "kind": "video", "ts": T0 - 90},
            {
                "job_id": "job-0",
                "status": "failed",
                "spent": 0,
                "flow": {"statuses": [6, 2, 4], "reasons": ["PUBLIC_ERROR_AUDIO_FILTERED"]},
                "ts": T0 - 60,
            },
        ],
    ],
    ids=[
        "settled before this submit",
        "left unsettled long ago",
        "a row that is no step of a job",
        "refused by Flow before this submit",
    ],
)
def test_a_job_that_could_not_move_the_balance_meanwhile_shares_nothing(monkeypatch, tmp_path, others):
    # A job that crashed last week has no settled row either: it must not mark every later bracket as shared.
    ledger = _started_job(tmp_path)
    _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)], balance=190)

    answer = _collect(ledger, others=others)

    assert (answer["spent"], answer["spent_from"]) == (10, "bracket")


def test_ledgers_that_cannot_be_read_are_taken_as_sharing_the_balance(monkeypatch, tmp_path):
    # Another process appending a row can leave a torn last line for a moment: a ledger that cannot be read may hold
    # a job that moved the balance, and a paid clip must still be fetched.
    ledger = _started_job(tmp_path)
    _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)], balance=190)

    def torn():
        raise json.JSONDecodeError("Unterminated string", '{"job_id": "jo', 14)

    answer = asyncio.run(jobs.collect(_Reader(), ledger, "job-1", now=T0 + 60, others=torn))

    assert (answer["state"], answer["spent"], answer["spent_from"]) == ("collected", 10, "quoted")
    assert ledger.rows()[-1]["status"] == "done"


def test_the_other_jobs_rows_are_read_after_the_balance(monkeypatch, tmp_path):
    # A job submitted while this collect waited for the browser is in the rows only when they are read last.
    ledger = _started_job(tmp_path)
    log = _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)])

    def others():
        log.append("others")
        return []

    asyncio.run(jobs.collect(_Reader(), ledger, "job-1", now=T0 + 60, others=others))

    assert log[:3] == ["snapshot p-1", "credits", "others"]


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
        (
            jobs.NOT_LISTED_S + 1,
            170,
            ({"job_id": "job-2", "status": "started", "ts": T0 + 5},),
            "unknown",
            None,
        ),
    ],
    ids=[
        "too soon to say",
        "never shown, balance unmoved",
        "never shown, balance moved",
        "never shown, balance shared and unmoved",
        "never shown, balance shared and moved",
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
    typed = outcome.classify(ledger.rows())
    assert typed["code"] == ("NOTHING_GENERATED" if status == "failed" else "UNKNOWN")
    # A shared balance says nothing of this job's charge: not the bracket of both jobs, and not 0 either.
    assert typed["charged"] == spent and typed["retryable"] is False
    assert ("never run this job again under a new job_id" in str(said.value)) is (status == "unknown")
    # The words must not invite what the outcome's own advice forbids: the job may still finish and bill.
    assert "new attempt" not in str(said.value) and "flow_media" in str(said.value)
    assert settled["flow"] == {"workflow_id": "w-job", "statuses": [6], "reasons": []}


def test_a_collected_clip_whose_request_went_out_wrong_is_an_error_though_it_was_paid(monkeypatch, tmp_path):
    wrong = {"ok": False, "model_keys": ["abra_t2v_10s"], "missing": [], "rpcid": "eb1hJf"}
    ledger = _started_job(tmp_path, body_check=wrong)
    _world(monkeypatch, tmp_path, [_video("w-job", PROMPT)])

    with pytest.raises(
        RuntimeError, match="10 credits were spent, but Flow's submit request did not match"
    ) as said:
        _collect(ledger)

    assert ledger.rows()[-1]["status"] == "done" and "abra_t2v_10s" in str(said.value)
    assert ledger.rows()[-1]["body_check"] == wrong


def test_any_order_of_later_calls_settles_a_job_once_and_only_reads(monkeypatch, tmp_path):
    """I-money-6 and I-read-2 as laws over sequences, not single calls: whatever order status and collect come in
    while Flow's listing moves from nothing to rendering to ready, whether a download fails, whether the call is
    late, and whether another job is in flight, a job gets at most one settled row, a settled job is never looked
    up or fetched again, status writes nothing, a clip is downloaded once, a shared balance is never written as
    the job's own bracket, and no call touches the page (`_Reader` raises if one does)."""
    rng = random.Random(20261002)
    listings = [[], [_video("w-job", PROMPT, done=False)], [_video("w-job", PROMPT)]]
    twins = [_video("w-job", PROMPT), _video("w-twin", PROMPT)]
    flying = [{"job_id": "job-0", "status": "submitted", "ts": T0 - 30}]
    seen = set()
    for trial in range(300):
        folder = tmp_path / str(trial)
        # Half the jobs were named no workflow at the submit, and are claimed by their prompt.
        named = rng.random() < 0.5
        ledger = _started_job(folder, workflow="w-job" if named else None, before=())
        stage, downloads = 0, 0
        for _ in range(10):
            stage = min(2, stage + rng.choice([0, 0, 1]))
            broken = rng.random() < 0.3
            log = _world(
                monkeypatch,
                folder,
                twins if stage == 2 and rng.random() < 0.2 else listings[stage],
                # The quote is 10 and the bracket never is, so a row cannot pass one off as the other.
                balance=rng.choice([185, 200]),
                fetch_error=RuntimeError("x") if broken else None,
            )
            before = ledger.rows()
            others = rng.choice([(), flying])
            call = rng.choice(["status", "collect"])
            now = T0 + rng.choice([60, jobs.NOT_LISTED_S + 1])
            try:
                _status(ledger, now=now) if call == "status" else _collect(ledger, now=now, others=others)
            except RuntimeError:
                pass
            after = ledger.rows()
            settled = [row for row in after if row["status"] in outcome.SETTLED]
            assert len(settled) <= 1, (trial, after)
            assert after[: len(before)] == before, "a row was rewritten"
            if call == "status" or len(before) == 3:
                assert after == before, (trial, call)
            if len(before) == 3:
                assert log == [], (trial, "a settled job was looked up again", log)
            downloads += sum(1 for step in log if step.startswith("fetch") and not broken)
            done = [row for row in settled if row["status"] == "done"]
            assert len(done) == downloads <= 1, (trial, after, downloads)
            if len(after) > len(before):
                written = after[-1]
                said = outcome.classify(after)
                if others:
                    assert written["status"] != "failed", "a shared balance cannot say nothing was charged"
                    assert written["balance_shared"] is True and written["spent"] in (None, 10), written
                    assert written.get("spent_from") in (None, "quoted"), written
                    assert said["charged"] == (10 if written["status"] == "done" else None), (written, said)
                else:
                    assert written["spent"] in (0, 15) and said["charged"] == written["spent"], written
                    assert written.get("spent_from") in (None, "bracket") and "charged_from" not in said
                seen.add(
                    (written["status"], "named" if named else "unnamed", bool(written.get("candidates")))
                )
    # The walk reached every way a job can end, or the laws above were checked on less than they claim.
    ends = {
        ("done", "named", False),
        ("done", "unnamed", False),
        ("failed", "named", False),
        ("failed", "unnamed", False),
        ("unknown", "named", False),
        ("unknown", "unnamed", False),
        ("unknown", "unnamed", True),
    }
    assert seen == ends, seen ^ ends


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
