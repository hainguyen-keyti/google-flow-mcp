"""Plan AY: what the month's credits went on and what the balance still buys, read off the ledgers. $0, no browser."""

import time

from video.flow import budget, video


def _ts(day, month="2026-11"):
    return time.mktime(time.strptime(f"{month}-{day:02d} 12:00", "%Y-%m-%d %H:%M"))


def _job(job_id, day, spent, status="done", month="2026-11", **submitted):
    fields = {"kind": "video", "model": "omni-1.2-flash", "resolution": "720p", "seconds": 8} | submitted
    return [
        {"ts": _ts(day, month) - 60, "job_id": job_id, "status": "submitted", **fields},
        {"ts": _ts(day, month), "job_id": job_id, "status": status, "spent": spent},
    ]


ROWS = [
    *_job("a", 2, 24),
    *_job("b", 3, 8, resolution="360p"),
    *_job("c", 3, 10, model="veo-lite", resolution=None, seconds=None),
    *_job("lost", 4, 24, status="failed"),
    *_job("free", 5, 0, status="failed"),
    *_job("old", 20, 24, month="2026-10"),
    {"ts": _ts(6), "job_id": "edit-1", "status": "opening", "kind": "edit"},
    {"ts": _ts(6), "job_id": "edit-1", "status": "done", "spent": 4},
]
SHOTS = [
    {"ts": _ts(3), "job_id": "b", "status": "draft"},
    {"ts": _ts(2), "job_id": "a", "status": "final", "final_of": "b"},
]


def _summary(**change):
    args = {"rows": ROWS, "shots": SHOTS, "balance": 100, "month": "2026-11", "now": _ts(10)} | change
    return budget.summary(**args)


def test_the_month_is_summed_from_each_jobs_own_outcome_row():
    said = _summary()

    assert said["month"] == "2026-11" and said["spent"] == 24 + 8 + 10 + 24 + 4
    assert said["jobs_charged"] == 5, "a job that cost nothing is not counted, nor one of another month"


def test_the_spend_is_split_by_what_was_made():
    by = {
        (row["kind"], row["model"], row["resolution"]): (row["credits"], row["jobs"])
        for row in _summary()["by"]
    }

    assert by[("video", "omni-1.2-flash", "720p")] == (48, 2)
    assert by[("video", "omni-1.2-flash", "360p")] == (8, 1)
    assert by[("video", "veo-lite", None)] == (10, 1)
    assert by[("edit", None, None)] == (4, 1)


def test_credits_charged_for_nothing_are_said_apart():
    said = _summary()

    assert said["charged_with_no_clip"] == {"credits": 24, "jobs": 1}


def test_drafts_and_finals_are_counted_from_the_shots_beside_the_ledger():
    said = _summary()

    assert said["drafts"] == {"credits": 8, "jobs": 1} and said["finals"] == {"credits": 24, "jobs": 1}


def test_the_balance_is_said_in_clips_from_the_surveyed_table():
    buys = {
        (row["model"], row["resolution"], row["seconds"]): row["clips"] for row in _summary()["balance_buys"]
    }

    assert buys[("omni-1.2-flash", "360p", 8)] == 100 // video.price("omni-1.2-flash", "360p", 8, 1) == 12
    assert buys[("omni-1.2-flash", "720p", 8)] == 100 // video.price("omni-1.2-flash", "720p", 8, 1) == 4
    assert buys[("veo-lite", None, 8)] == 100 // video.price("veo-lite", None, None, 1) == 10


def test_the_daily_rate_is_the_months_spend_over_the_days_so_far():
    said = _summary()

    assert said["days_so_far"] == 10 and said["per_day"] == round(70 / 10, 1)


def test_a_month_with_nothing_spent_answers_zeros_not_an_error():
    said = _summary(month="2026-08")

    assert said["spent"] == 0 and said["by"] == [] and said["per_day"] == 0


def test_the_time_in_flows_balance_reply_is_passed_on_as_unmeasured():
    said = _summary(balance_raw=[100, 1, 2, 2, None, 100, [1796112586, 415883000]])

    assert said["flow_time"]["epoch"] == 1796112586 and "unmeasured" in said["flow_time"]["note"]
    assert "flow_time" not in _summary(balance_raw=[100, 1])


# Review of plan AY.


def test_a_job_with_two_outcome_rows_is_billed_once_at_the_last():
    """m8: a job that read `pending` and then `done` carries the same bill twice in its rows."""
    rows = [
        {"ts": _ts(2), "job_id": "p", "status": "submitted", "kind": "video"},
        {"ts": _ts(2), "job_id": "p", "status": "pending", "spent": 24},
        {"ts": _ts(2) + 60, "job_id": "p", "status": "done", "spent": 24},
    ]
    said = _summary(rows=rows, shots=[])

    assert said["spent"] == 24 and said["jobs_charged"] == 1
    assert said["charged_with_no_clip"] == {"credits": 0, "jobs": 0}


def test_a_spent_that_is_not_a_number_of_credits_is_not_counted():
    """m15: True is an int to Python."""
    rows = [{"ts": _ts(2), "job_id": "t", "status": "done", "spent": True}]

    assert _summary(rows=rows, shots=[])["spent"] == 0


def test_a_submitted_job_nobody_collected_is_said_with_its_quoted_price():
    """N5: job_submit is charged at the click (measured 2026-10-03), and its bill reaches the ledger only at
    job_collect. Until then the month's spend lacks it, so it is named apart, at the price Flow quoted."""
    rows = [
        {"ts": _ts(7), "job_id": "s", "status": "submitted", "kind": "video", "quoted_credits": 9},
        {"ts": _ts(7) + 5, "job_id": "s", "status": "started"},
        *_job("a", 2, 24),
    ]
    said = _summary(rows=rows, shots=[])

    assert said["spent"] == 24
    assert said["submitted_not_collected"] == {"quoted_credits": 9, "jobs": 1}


def test_a_past_month_is_averaged_over_its_own_days():
    said = _summary(month="2026-10")

    assert said["spent"] == 24 and said["days_so_far"] == 31 and said["per_day"] == round(24 / 31, 1)
