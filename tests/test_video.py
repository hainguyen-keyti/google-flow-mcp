"""gen_video's driver (plan AB): every video option Flow's composer offers, read from the surveyed options file."""

import pytest
from test_ingredients import PROMPT, _install, _submit, _SubmitSession, _video

from video import gen
from video.flow import composer, video


def test_the_options_file_holds_what_the_composer_showed_on_2026_09_29():
    # The owner's screenshot and the $0 walk agree: Omni 1.1 Flash 720p 10 s x1 is 15 credits.
    assert set(video.OPTIONS["video"]["models"]) == {"omni-flash", "veo-lite", "veo-fast", "veo-quality"}
    assert video.price("omni-flash", "720p", 10, 1) == 15
    assert video.price("omni-flash", "360p", 4, 4) == 16
    assert video.price("veo-quality", None, None, 1) == 100
    assert video.OPTIONS["video"]["aspects"] == ["16:9", "9:16"]


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"model": "veo-ultra"}, "model must be one of"),
        ({"model": "veo-lite", "duration": 10}, "veo-lite offers no length choice"),
        ({"model": "veo-lite", "resolution": "360p"}, "veo-lite offers no resolution choice"),
        ({"model": "omni-flash", "duration": 5}, "duration must be one of [4, 6, 8, 10]"),
        ({"model": "omni-flash", "resolution": "1080p"}, "resolution must be one of ['360p', '720p']"),
        ({"count": 5}, "count must be one of [1, 2, 3, 4]"),
        ({"aspect": "1:1"}, "aspect must be one of ['16:9', '9:16']"),
    ],
)
def test_a_setting_the_composer_does_not_offer_is_refused(settings, message):
    base = {"model": "omni-flash", "resolution": "720p", "duration": 8, "count": 1, "aspect": "9:16"}
    with pytest.raises(ValueError) as refused:
        video.check_settings(**(base | settings))
    assert message in str(refused.value)


def test_omni_takes_every_offered_cell_and_veo_takes_its_defaults():
    video.check_settings(model="omni-flash", resolution="360p", duration=4, count=4, aspect="16:9")
    video.check_settings(model="veo-fast", resolution=None, duration=None, count=2, aspect="9:16")


@pytest.mark.parametrize(
    ("refs", "mode"),
    [
        ({}, "Frames"),
        ({"start_frame": "s"}, "Frames"),
        ({"start_frame": "s", "end_frame": "e"}, "Frames"),
        ({"media_ids": ["m"]}, "Ingredients"),
        ({"characters": ["c"]}, "Ingredients"),
    ],
)
def test_the_mode_follows_what_was_passed(refs, mode):
    assert video.mode_for(**refs) == mode


def test_frames_and_ingredients_cannot_be_mixed_and_an_end_needs_a_start():
    with pytest.raises(ValueError, match="either frames or ingredients"):
        video.mode_for(start_frame="s", media_ids=["m"])
    with pytest.raises(ValueError, match="end_frame needs a start_frame"):
        video.mode_for(end_frame="e")


# _submit, the one money path, with gen_video's three additions (plan AB T2), over the fakes test_ingredients drives.


def _counted(monkeypatch, log, prices):
    async def configure(session, *, mode="Ingredients", aspect="9:16", count="x1", label="settings"):
        log.append(f"configure {label} {count}")
        return {"applied": {}, "price": prices[0] if label == "pre" else prices[1], "settings_text": ""}

    monkeypatch.setattr(composer, "configure", configure)


def test_submit_sets_the_count_at_both_settings_passes(monkeypatch, tmp_path):
    log = []
    _install(
        monkeypatch,
        tmp_path,
        log,
        fresh=[_video("w-a", PROMPT), _video("w-b", PROMPT)],
        balance_reads=(200, 176, 176),
    )
    _counted(monkeypatch, log, (24, 24))

    _submit(_SubmitSession(log), tmp_path, log, count=2, max_credits=24, strict_output=True)

    assert "configure pre x2" in log and "configure confirm x2" in log, log


def test_a_live_price_over_the_cap_is_refused_before_the_click_and_writes_no_row(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log)
    _counted(monkeypatch, log, (20, 20))

    with pytest.raises(RuntimeError, match="20 credits.*max_credits 15"):
        _submit(_SubmitSession(log), tmp_path, log, max_credits=15)

    assert "click" not in log
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows() == []


def test_a_live_price_under_the_cap_runs_whatever_the_table_said(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", PROMPT)], balance_reads=(200, 190, 190))
    _counted(monkeypatch, log, (10, 10))

    result = _submit(
        _SubmitSession(log), tmp_path, log, expected_credits=0, max_credits=15, strict_output=True
    )

    assert result["quoted_credits"] == 10 and result["spent"] == 10


def test_an_unread_price_is_refused_under_a_cap_too(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log)
    _counted(monkeypatch, log, (None, None))

    with pytest.raises(RuntimeError, match="could not read"):
        _submit(_SubmitSession(log), tmp_path, log, max_credits=50)
    assert "click" not in log


def test_x2_is_done_only_with_both_clips_fetched(monkeypatch, tmp_path):
    log = []
    fresh = [_video("w-a", PROMPT), _video("w-b", PROMPT)]
    _install(monkeypatch, tmp_path, log, fresh=fresh, balance_reads=(200, 176, 176))
    _counted(monkeypatch, log, (24, 24))

    result = _submit(_SubmitSession(log), tmp_path, log, count=2, max_credits=24, strict_output=True)

    assert sorted(o["media_id"] for o in result["outputs"]) == ["m-w-a", "m-w-b"]
    assert len([line for line in log if line.startswith("fetch")]) == 2
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["status"] == "done"


def test_x2_with_one_clip_listed_is_not_done_and_says_do_not_run_again(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-a", PROMPT)], balance_reads=(200, 176, 176))
    _counted(monkeypatch, log, (24, 24))

    with pytest.raises(RuntimeError, match="do not run this job again"):
        _submit(_SubmitSession(log), tmp_path, log, count=2, max_credits=24, strict_output=True)

    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert last["status"] != "done", last
