"""Plan AM: what a spending job came to, read off the rows its driver wrote. The rows are real ones (fixture)."""

import json
from pathlib import Path

import pytest

from video import outcome
from video.flow import composer

ROWS = json.loads((Path(__file__).parent / "fixtures" / "outcome_rows.json").read_text(encoding="utf-8"))
ROWS.pop("_about")

EXPECTED = {
    "composer_done": ("DONE", 10, False),
    "no_reason": ("NO_REASON", 0, True),
    "audio_filtered": ("AUDIO_FILTERED", 0, True),
    "audio_filtered_late_submit_reply": ("AUDIO_FILTERED", 0, True),
    "prominent_people": ("PROMINENT_PEOPLE", 0, False),
    "prominent_people_refused_at_once": ("PROMINENT_PEOPLE", 0, False),
    "unsafe_generation": ("UNSAFE_GENERATION", 0, False),
    "failed_while_flow_still_ran": ("NOTHING_GENERATED", 0, False),
    "failed_with_no_reply_heard": ("NOTHING_GENERATED", 0, False),
    "composer_pending": ("PENDING", 12, False),
    "gflow_selector_drift": ("TOOL_ERROR", 0, False),
    "gflow_exit_without_class": ("TOOL_ERROR", 0, False),
    "gflow_submitted_only": ("UNKNOWN", None, False),
    "editor_opening_only": ("UNKNOWN", None, False),
    "editor_failed_and_charged": ("CHARGED_NO_OUTPUT", 20, False),
    "editor_failed_uncharged": ("NOTHING_GENERATED", 0, False),
    "editor_pending": ("PENDING", 10, False),
    "editor_done": ("DONE", 10, False),
    "agent_done_uncharged": ("DONE", 0, False),
    "gflow_done": ("DONE", 10, False),
    "one_id_run_twice": ("DONE", 10, False),
    "done_then_other_rows": ("DONE", 10, False),
}


def test_every_real_shape_in_the_fixture_is_expected_by_name():
    # A row shape added to the fixture without a verdict here would be a shape nobody judged.
    assert set(ROWS) == set(EXPECTED)


@pytest.mark.parametrize("shape", sorted(EXPECTED))
def test_a_real_job_is_read_as_what_it_came_to(shape):
    code, charged, retryable = EXPECTED[shape]

    said = outcome.classify(ROWS[shape]["rows"])

    assert (said["code"], said["charged"], said["retryable"]) == (code, charged, retryable), said
    assert said["advice"] and isinstance(said["advice"], str)
    assert set(said) <= {"code", "charged", "retryable", "advice", "reasons"}


def test_the_failed_status_is_the_one_the_composer_waits_on():
    assert outcome.FLOW_FAILED == composer.STATUS_FAILED


def _failed(**outcome_row):
    return [
        {"status": "submitted", "kind": "video", "credits_before": 100, "project": "p", "prompt": "a prompt"},
        {"status": "failed", "spent": 0, "credits_before": 100, "credits_after": 100, **outcome_row},
    ]


def test_no_row_at_all_means_nothing_was_clicked():
    said = outcome.classify([])

    assert (said["code"], said["charged"], said["retryable"]) == ("NOT_SUBMITTED", 0, False)
    assert "same job_id" in said["advice"]


@pytest.mark.parametrize(
    "rows",
    [
        [{"status": "submitted", "kind": "video", "credits_before": 100}],
        [{"status": "opening", "kind": "edit", "credits_before": 100}],
        _failed() + [{"status": "submitted", "kind": "video", "credits_before": 100}],
        [
            {"status": "submitted", "kind": "video", "credits_before": 100},
            {"status": "unknown", "spent": None, "credits_before": 100, "credits_after": None},
        ],
        [
            {"status": "submitted", "kind": "video", "credits_before": 100},
            {"status": "unknown", "spent": 0, "credits_before": 100, "credits_after": 100},
        ],
        [{"status": "submitted", "kind": "video", "credits_before": 100}, {"status": "failed"}],
    ],
    ids=[
        "submitted and nothing after",
        "an editor opened and nothing after",
        "a second run started after a settled one",
        "unknown, balance unread",
        "unknown, balance unmoved so far",
        "failed with no balance bracket",
    ],
)
def test_a_job_with_no_settled_word_is_unknown_and_never_a_failure_to_retry(rows):
    # I-read-2: money may be gone, so nothing here may read as uncharged or as retryable.
    said = outcome.classify(rows)

    assert said["code"] == "UNKNOWN" and said["retryable"] is False, said
    assert "never" in said["advice"] and "new job_id" in said["advice"], said


def test_an_outcome_says_uncharged_only_when_the_balance_bracket_says_so():
    # I-read-2 over every real shape: charged 0 needs spent 0, or a bracket whose two ends are equal.
    for shape, job in ROWS.items():
        said = outcome.classify(job["rows"])
        last = [r for r in job["rows"] if r["status"] in outcome.SETTLED][-1:] or [{}]
        bracket = (
            last[0].get("spent")
            if last[0].get("spent") is not None
            else (
                last[0]["credits_before"] - last[0]["credits_after"]
                if "credits_after" in last[0] and last[0]["credits_after"] is not None
                else None
            )
        )
        if said["charged"] == 0:
            assert bracket == 0, (shape, said, last)
        if said["code"] not in ("UNKNOWN",):
            assert said["charged"] == bracket, (shape, said, last)


def test_a_failure_that_moved_the_balance_is_never_retryable_whatever_flow_said():
    # AM3 (1): the audio filter is retryable only because it is uncharged.
    rows = _failed(flow={"statuses": [6, 2, 4], "reasons": ["PUBLIC_ERROR_AUDIO_FILTERED"]})
    rows[-1].update(spent=10, credits_after=90)

    said = outcome.classify(rows)

    assert (said["code"], said["charged"], said["retryable"]) == ("CHARGED_NO_OUTPUT", 10, False)


def test_a_balance_that_went_up_is_not_called_uncharged():
    rows = _failed(flow={"statuses": [6, 2, 4], "reasons": []})
    rows[-1].update(spent=-5, credits_after=105)

    said = outcome.classify(rows)

    assert said["code"] == "CHARGED_NO_OUTPUT" and said["charged"] == -5 and said["retryable"] is False


@pytest.mark.parametrize(
    ("reasons", "code"),
    [
        (["PUBLIC_ERROR_AUDIO_FILTERED", "PUBLIC_ERROR_UNSAFE_GENERATION"], "UNSAFE_GENERATION"),
        (["PUBLIC_ERROR_UNSAFE_GENERATION", "PUBLIC_ERROR_AUDIO_FILTERED"], "UNSAFE_GENERATION"),
        (["PUBLIC_ERROR_AUDIO_FILTERED", "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED"], "PROMINENT_PEOPLE"),
        (["PUBLIC_ERROR_AUDIO_FILTERED", "PUBLIC_ERROR_SOMETHING_NEW"], "REFUSED"),
        (["PUBLIC_ERROR_SOMETHING_NEW"], "REFUSED"),
    ],
)
def test_a_reason_that_must_not_be_retried_outranks_one_that_may(reasons, code):
    # AM3 (7). A reason nobody has measured is not retried on a guess.
    said = outcome.classify(_failed(flow={"statuses": [6, 2, 4], "reasons": reasons}))

    assert (said["code"], said["retryable"]) == (code, False), said
    assert said["reasons"] == reasons


def test_a_retryable_reason_needs_flows_own_word_that_the_job_failed():
    # sg-bf-2 (2026-09-17): statuses 6, 2 and no failed status: Flow may still finish the job and bill it.
    still_running = outcome.classify(
        _failed(flow={"statuses": [6, 2], "reasons": ["PUBLIC_ERROR_AUDIO_FILTERED"]})
    )

    assert (still_running["code"], still_running["retryable"]) == ("NOTHING_GENERATED", False)
    # The failed status has to be Flow's LAST word (review of plan AM, M1), not one it said along the way.
    moved_on = outcome.classify(_failed(flow={"statuses": [6, 4, 2], "reasons": []}))
    assert (moved_on["code"], moved_on["retryable"]) == ("NOTHING_GENERATED", False)


def test_a_job_nobody_can_read_is_unknown_without_reading_any_row():
    said = outcome.unreadable()

    assert (said["code"], said["charged"], said["retryable"]) == ("UNKNOWN", None, False)


@pytest.mark.parametrize("kind", ["extend", "edit", "agent", "t2v", "i2v", "r2v", "story"])
def test_only_a_job_of_the_composer_tools_is_offered_a_retry(kind):
    # retry_of exists on gen_video and gen_character only (kinds video and character).
    rows = _failed(flow={"statuses": [6, 2, 4], "reasons": ["PUBLIC_ERROR_AUDIO_FILTERED"]})
    rows[0]["kind"] = kind

    said = outcome.classify(rows)

    assert said["code"] == "AUDIO_FILTERED" and said["retryable"] is False
    assert "retry_of" not in said["advice"]


@pytest.mark.parametrize("kind", ["video", "character"])
def test_a_retryable_refusal_says_how_to_retry(kind):
    rows = _failed(flow={"statuses": [6, 2, 4], "reasons": []})
    rows[0]["kind"] = kind

    said = outcome.classify(rows)

    assert said["retryable"] is True and "retry_of" in said["advice"] and "new job_id" in said["advice"]


@pytest.mark.parametrize(
    "row",
    [
        {"exit_code": 10},
        {"exit_code": 10, "error_class": "WafRejectionError"},
        {"exit_code": 1, "error_class": "WafRejectionError"},
    ],
)
def test_googles_unusual_activity_stop_is_its_own_code(row):
    rows = [
        {"status": "submitted", "kind": "t2v", "credits_before": 100},
        {"status": "failed", "credits_before": 100, "credits_after": 100, **row},
    ]

    said = outcome.classify(rows)

    assert (said["code"], said["charged"], said["retryable"]) == ("UNUSUAL_ACTIVITY", 0, False)
    assert "Stop" in said["advice"] and "owner" in said["advice"]


def test_the_codes_are_a_closed_set_with_one_line_of_advice_each():
    assert set(outcome.CODES) == {
        "DONE",
        "PENDING",
        "NOT_SUBMITTED",
        "UNKNOWN",
        "STARTED",
        "CHARGED_NO_OUTPUT",
        "AUDIO_FILTERED",
        "NO_REASON",
        "UNSAFE_GENERATION",
        "PROMINENT_PEOPLE",
        "REFUSED",
        "NOTHING_GENERATED",
        "TOOL_ERROR",
        "UNUSUAL_ACTIVITY",
    }
    seen = {outcome.classify(job["rows"])["code"] for job in ROWS.values()}
    assert seen <= set(outcome.CODES)


PROMPT = "a cup on a table, slow push in"


def _failed_row(spent=0, statuses=(6, 2, 4), reasons=("PUBLIC_ERROR_AUDIO_FILTERED",)):
    flow = {"statuses": list(statuses), "reasons": list(reasons)}
    bracket = {"credits_before": 100, "credits_after": 100 - spent}
    return {"status": "failed", "spent": spent, **bracket, "flow": flow}


def _job(kind="video", project="P", prompt=PROMPT, retry_of=None, outcome_row=None):
    """The rows of one composer job: its `submitted` row and, unless told otherwise, an uncharged audio-filter fail."""
    intent = {"status": "submitted", "kind": kind, "project": project, "prompt": prompt[:200]}
    if retry_of:
        intent["retry_of"] = retry_of
    settled = _failed_row() if outcome_row is None else outcome_row
    return [intent, settled] if settled else [intent]


def _refusal(jobs, target="a", **request):
    return outcome.retry_refusal(target, jobs, **{"project": "P", "prompt": PROMPT, **request})


def test_a_job_flow_refused_without_charging_may_be_retried_with_the_same_request():
    assert _refusal({"a": _job()}) is None
    assert _refusal({"a": _job(kind="character")}) is None
    no_reason = _job()
    no_reason[-1]["flow"] = {"statuses": [6, 2, 4], "reasons": []}
    assert _refusal({"a": no_reason}) is None


def test_a_retry_of_a_job_nobody_recorded_is_refused():
    said = _refusal({"b": _job()})

    assert "nothing to retry" in said and "'a'" in said


DONE = {"status": "done", "spent": 10, "credits_before": 100, "credits_after": 90}
UNSAFE = _failed_row(reasons=("PUBLIC_ERROR_UNSAFE_GENERATION",))
CHARGED = _failed_row(spent=10)
RUNNING = _failed_row(statuses=(6, 2), reasons=())


@pytest.mark.parametrize(
    ("settled", "code"),
    [
        (DONE, "DONE"),
        (UNSAFE, "UNSAFE_GENERATION"),
        (CHARGED, "CHARGED_NO_OUTPUT"),
        (RUNNING, "NOTHING_GENERATED"),
        ({"status": "unknown", "spent": None}, "UNKNOWN"),
        (False, "UNKNOWN"),
    ],
    ids=["done", "unsafe", "charged", "flow still running", "unknown", "never settled"],
)
def test_a_retry_is_refused_unless_the_job_is_a_refusal_that_may_be_retried(settled, code):
    # AM3 (3): a job that spent, or may have, is never vouched for.
    said = _refusal({"a": _job(outcome_row=settled)})

    assert said is not None and code in said, said


@pytest.mark.parametrize(
    "request_",
    [{"project": "another"}, {"prompt": "a dog on a table, slow push in"}, {"prompt": PROMPT + " and more"}],
    ids=["another project", "another prompt", "a longer prompt"],
)
def test_a_retry_sends_the_request_the_refused_job_sent(request_):
    # AM3 (6): "retry unchanged". The ledger keeps the project and the first 200 characters of the prompt.
    said = _refusal({"a": _job()}, **request_)

    assert said is not None and "same" in said, said


def test_a_prompt_longer_than_the_ledger_keeps_is_held_to_what_the_ledger_kept():
    long = PROMPT + " " + "and then " * 40
    assert len(long) > 200

    assert _refusal({"a": _job(prompt=long)}, prompt=long) is None
    assert _refusal({"a": _job(prompt=long)}, prompt="x" + long[1:]) is not None


def test_a_job_is_retried_once_and_the_next_retry_names_the_retry():
    # AM3 (4): two retries of one refused job would be two clips of one request.
    retried = {"a": _job(), "b": _job(retry_of="a")}

    said = _refusal(retried, target="a")

    assert said is not None and "'b'" in said and "already" in said, said
    assert _refusal(retried, target="b") is None


def test_a_third_retry_of_one_original_job_is_refused():
    # AM3 (5): at most two retries, the bound the skill design uses for a random filter.
    chain = {"a": _job(), "b": _job(retry_of="a"), "c": _job(retry_of="b")}

    said = _refusal(chain, target="c")

    assert said is not None and str(outcome.MAX_RETRIES) in said and "'a'" in said, said


def test_a_retry_link_that_loops_is_refused_and_never_followed_forever():
    loop = {"a": _job(retry_of="b"), "b": _job(retry_of="a")}
    assert _refusal(loop, target="b") is not None
    # Nothing retried c yet, and its own links run a, b, a, b: the walk stops at the bound.
    behind = {"c": _job(retry_of="a"), "a": _job(retry_of="b"), "b": _job(retry_of="a")}
    assert str(outcome.MAX_RETRIES) in _refusal(behind, target="c")


def test_the_second_retry_of_an_original_job_is_still_allowed():
    chain = {"a": _job(), "b": _job(retry_of="a")}

    assert _refusal(chain, target="b") is None


def test_only_a_job_that_was_submitted_takes_the_retry_slot():
    # A retry refused before its click wrote no row, so it holds nothing; one that reached the click does.
    assert _refusal({"a": _job(), "b": []}) is None
    opened = [{"status": "opening", "kind": "edit", "retry_of": "a"}]
    assert _refusal({"a": _job(), "b": opened}) is None


def test_the_head_of_an_error_carries_the_outcome_in_one_short_line():
    said = outcome.classify(ROWS["audio_filtered"]["rows"])

    assert outcome.head(said) == "outcome code=AUDIO_FILTERED charged=0 retryable=yes"
    assert outcome.head(outcome.classify(ROWS["gflow_submitted_only"]["rows"])) == (
        "outcome code=UNKNOWN charged=unknown retryable=no"
    )
