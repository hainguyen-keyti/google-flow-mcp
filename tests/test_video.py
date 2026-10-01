import asyncio
from pathlib import Path

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

    _submit(
        _SubmitSession(log), tmp_path, log, count=2, max_credits=24, expected_credits=24, strict_output=True
    )

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

    result = _submit(
        _SubmitSession(log), tmp_path, log, count=2, max_credits=24, expected_credits=24, strict_output=True
    )

    assert sorted(o["media_id"] for o in result["outputs"]) == ["m-w-a", "m-w-b"]
    assert len([line for line in log if line.startswith("fetch")]) == 2
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["status"] == "done"


def test_x2_with_one_clip_listed_is_not_done_and_says_do_not_run_again(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-a", PROMPT)], balance_reads=(200, 176, 176))
    _counted(monkeypatch, log, (24, 24))

    with pytest.raises(RuntimeError, match="do not run this job again"):
        _submit(
            _SubmitSession(log),
            tmp_path,
            log,
            count=2,
            max_credits=24,
            expected_credits=24,
            strict_output=True,
        )

    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert last["status"] != "done", last


# The driver (plan AB T3).
TEAPOT = {"id": "m-teapot", "title": "Green teapot on wooden table", "kind": "image"}
CUP = {"id": "m-cup", "title": "Blue ceramic cup on table", "kind": "image"}
RECORDS = [
    {"id": "m-teapot", "workflow_id": "w-teapot", "url": "https://lh3/aaaaaaaaaaaaaaaaaaaaaaaaaaaaTEAPOT"},
    {"id": "m-cup", "workflow_id": "w-cup", "url": "https://lh3/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbCUP"},
]


def test_frames_are_named_by_media_id_and_must_be_images_with_a_title():
    refs = video.frame_references([TEAPOT, CUP], RECORDS, "m-teapot", "m-cup")
    assert [(r.id, r.title, r.tail) for r in refs] == [
        ("m-teapot", "Green teapot on wooden table", RECORDS[0]["url"][-24:]),
        ("m-cup", "Blue ceramic cup on table", RECORDS[1]["url"][-24:]),
    ]
    with pytest.raises(LookupError, match="not a media of this project"):
        video.frame_references([TEAPOT], RECORDS, "m-nope", None)
    with pytest.raises(ValueError, match="is a video"):
        video.frame_references([TEAPOT | {"kind": "video"}], RECORDS, "m-teapot", None)
    with pytest.raises(LookupError, match="no title"):
        video.frame_references([TEAPOT | {"title": " "}], RECORDS, "m-teapot", None)


class _Loc:
    def __init__(self, page, items):
        self.page, self.items = page, items

    @property
    def first(self):
        return type(self)(self.page, self.items[:1])

    def nth(self, index):
        return type(self)(self.page, self.items[index : index + 1])

    def filter(self, has_text=None):
        # The pickers these fakes model pin on the tile click and show no 'Add to prompt'.
        return type(self)(self.page, [])

    async def count(self):
        return len(self.items)

    async def get_attribute(self, name):
        return self.items[0].get(name)

    async def click(self, timeout=None):
        self.page.clicked.append(self.items[0].get("what"))

    async def wait_for(self, state=None, timeout=None):
        if not self.items:
            raise video.PlaywrightTimeoutError("nothing")

    def get_by_role(self, role, name=None):
        return _Loc(self.page, [{"what": f"slot {name.pattern}"}])


class _PickerPage:
    """A composer whose picker offers tiles by src and whose slots read back as the buttons list says."""

    def __init__(self, tiles, after):
        self.tiles, self.after, self.clicked = tiles, after, []
        self.keyboard = self

    async def insert_text(self, text):
        self.clicked.append(f"typed {text}")

    async def wait_for_timeout(self, ms):
        return None

    def locator(self, selector):
        if "img" in selector:
            return _Loc(self, [{"src": src, "what": f"tile {src[-6:]}"} for src in self.tiles])
        if "input" in selector:
            return _Loc(self, [{"what": "search"}])
        return _Loc(self, [{"what": "bar"}])

    async def evaluate(self, script, arg=None):
        return self.after


def test_a_frame_is_pinned_by_the_tile_whose_src_ends_like_the_listing_url():
    ref = video.frame_references([TEAPOT, CUP], RECORDS, "m-teapot", None)[0]
    page = _PickerPage(
        ["https://x/other-tile-000000000000000000", "https://x/" + RECORDS[0]["url"][-40:]],
        [
            "Image ingredient",
            "Swap first and last frames",
            "End",
            "Agent",
            "Settings trigger",
            "Start generation",
        ],
    )

    asyncio.run(video.pin_frame(page, "Start", ref))

    assert page.clicked[-1] == "tile " + RECORDS[0]["url"][-6:], page.clicked


def test_a_picker_that_offers_no_tile_of_that_image_is_refused_without_a_click():
    ref = video.frame_references([TEAPOT, CUP], RECORDS, "m-teapot", None)[0]
    page = _PickerPage(["https://x/other-tile-000000000000000000"], [])

    with pytest.raises(LookupError, match="no tile"):
        asyncio.run(video.pin_frame(page, "Start", ref))
    assert not any(str(c).startswith("tile") for c in page.clicked)


def test_the_slots_read_back_filled_where_they_were_asked():
    filled = ["Image ingredient", "Swap first and last frames", "Image ingredient", "Agent"]
    assert video.slots_filled(filled) == {"Start": True, "End": True}
    assert video.slots_filled(["Image ingredient", "Swap first and last frames", "End"]) == {
        "Start": True,
        "End": False,
    }
    assert video.slots_filled(["Start", "Swap first and last frames", "End"]) == {
        "Start": False,
        "End": False,
    }


@pytest.mark.parametrize(
    ("mode_kind", "key", "duration", "ok"),
    [
        ("t2v", "abra_t2v_4s", 4, True),
        ("t2v", "abra_t2v_10s", 4, False),
        # gflow's note (migrated_composer.py:182-188): a plain Veo t2v submit sends a key with no mode in it.
        ("t2v", "veo_3_1_lite_lower_priority", None, True),
        ("t2v", "veo_3_1_r2v_lite_low_priority", None, False),
        ("i2v", "abra_i2v_6s", 6, True),
        ("i2v", "abra_t2v_6s", 6, False),
        ("r2v", "abra_r2v_8s", 8, True),
        ("r2v", "abra_t2v_8s", 8, False),
    ],
)
def test_the_body_check_holds_the_submit_to_the_mode_and_length_asked(mode_kind, key, duration, ok):
    check = video.VideoBodyCheck(mode_kind, [], duration)

    class _Request:
        url = "https://flow.google.com/_/x/data/batchexecute?rpcids=MZZa6b"
        post_data = f"f.req=%5B%22{key}%22%5D"

    check.on_request(_Request())
    assert check.report()["ok"] is ok, check.report()


def _driver_world(monkeypatch, *, submit_result=None):
    """video.generate over fakes: the listing, the pins and the money path record what they were asked."""
    calls = {"submit": None, "ingredients": None, "log": []}
    listing = object()

    async def fake_listing(session, project_id):
        calls["log"].append("listing")
        return listing

    monkeypatch.setattr(video.ingredients, "_listing", fake_listing)
    monkeypatch.setattr(video.parsers, "media", lambda payload: [TEAPOT, CUP])
    monkeypatch.setattr(video.parsers, "records", lambda payload: RECORDS)
    monkeypatch.setattr(video.parsers, "characters_from_listing", lambda payload: [])

    async def fake_set_mode(session, project_id, enabled):
        calls["log"].append(f"agent {enabled}")
        return {"was": False}

    async def fake_submit(session, project_id, **kwargs):
        calls["submit"] = kwargs
        if not kwargs.get("dry_run") and kwargs.get("watch") is not None:
            kwargs["watch"].seen = {"ok": True}
        return submit_result or {"job_id": kwargs.get("job_id"), "body_check": {"ok": True}}

    async def fake_ingredients(session, project_id, **kwargs):
        calls["ingredients"] = kwargs
        return {"job_id": kwargs.get("job_id")}

    monkeypatch.setattr(video.agent, "set_mode", fake_set_mode)
    monkeypatch.setattr(video.composer, "_submit", fake_submit)
    monkeypatch.setattr(video.ingredients, "generate", fake_ingredients)
    return calls


def _run(**kwargs):
    base = {"prompt": "a teapot", "job_id": "job-v", "max_credits": 20, "out_dir": Path("out/test_video")}
    return asyncio.run(video.generate(object(), "p-1", **(base | kwargs)))


def test_text_to_video_goes_through_frames_with_every_setting_and_the_cap(monkeypatch):
    calls = _driver_world(monkeypatch)

    _run(model="omni-flash", resolution="360p", duration=4, count=3, aspect="16:9")

    submit = calls["submit"]
    assert submit["mode"] == "Frames" and submit["count"] == 3 and submit["max_credits"] == 20
    assert submit["aspect"] == "16:9" and submit["kind"] == "video" and submit["strict_output"] is True
    assert (
        submit["expected_credits"] == 12
    )  # 4 credits x3 from the surveyed table, shown next to the live quote
    assert submit["watch"].kind == "t2v" and submit["watch"].duration == 4


def test_a_start_and_end_frame_make_an_i2v_run_whose_body_must_carry_both_images(monkeypatch):
    calls = _driver_world(monkeypatch)

    _run(model="veo-lite", start_frame="m-teapot", end_frame="m-cup")

    watch = calls["submit"]["watch"]
    assert watch.kind == "i2v" and watch.duration is None
    assert [r.id for r in watch.references] == ["m-teapot", "m-cup"]


def test_ingredients_go_through_the_proven_ingredients_driver_with_the_new_settings(monkeypatch):
    calls = _driver_world(monkeypatch)

    _run(model="omni-flash", resolution="360p", duration=6, count=2, media_ids=["m-teapot"])

    got = calls["ingredients"]
    assert got["media_ids"] == ["m-teapot"] and got["characters"] == []
    assert (got["duration"], got["resolution"], got["count"], got["max_credits"]) == (6, "360p", 2, 20)
    check = got["body_check_for"](["the references ingredients resolved"])
    assert check.kind == "r2v" and check.duration == 6
    assert check.references == ["the references ingredients resolved"]
    assert calls["submit"] is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"max_credits": None}, "max_credits is required"),
        ({"job_id": None}, "job_id is required"),
        ({"model": "veo-lite", "duration": 10}, "no length choice"),
        ({"start_frame": "m-teapot", "media_ids": ["m-cup"]}, "either frames or ingredients"),
    ],
)
def test_a_real_run_is_refused_before_the_listing_is_read(monkeypatch, change, message):
    calls = _driver_world(monkeypatch)

    with pytest.raises(ValueError, match=message):
        _run(**change)
    assert calls["log"] == []


def test_a_dry_run_needs_no_cap_and_no_job_id(monkeypatch):
    calls = _driver_world(monkeypatch)

    _run(dry_run=True, max_credits=None, job_id=None)

    assert calls["submit"]["dry_run"] is True


def test_a_submit_that_carried_the_wrong_mode_or_length_is_an_error_after_the_run(monkeypatch):
    calls = _driver_world(monkeypatch, submit_result={"job_id": "job-v", "spent": 15, "path": "out/x.mp4"})

    async def submit_seen_wrong(session, project_id, **kwargs):
        calls["submit"] = kwargs
        kwargs["watch"].seen = {"ok": False, "model_keys": ["abra_t2v_10s"], "missing": [], "rpcid": "MZZa6b"}
        return {"job_id": "job-v", "spent": 15, "path": "out/x.mp4", "body_check": kwargs["watch"].report()}

    monkeypatch.setattr(video.composer, "_submit", submit_seen_wrong)
    with pytest.raises(RuntimeError, match="15 credits were spent.*abra_t2v_10s"):
        _run(model="omni-flash", resolution="720p", duration=4)


def test_the_ingredients_driver_builds_the_body_check_from_the_references_it_resolved(monkeypatch, tmp_path):
    from test_ingredients import MEDIA, _generate_world

    log = []
    captured = _generate_world(monkeypatch, log)
    asyncio.run(
        video.ingredients.generate(
            object(),
            "p-1",
            prompt=PROMPT,
            characters=[],
            media_ids=[MEDIA],
            model="omni-flash",
            duration=6,
            job_id="job-1",
            out_dir=tmp_path,
            max_credits=10,
            count=2,
            body_check_for=lambda references: ("built", [r.id for r in references]),
        )
    )
    assert captured["watch"] == ("built", [MEDIA])
    assert captured["count"] == 2 and captured["max_credits"] == 10


def test_the_start_and_end_submit_is_heard_though_its_rpc_name_rides_in_the_body():
    """Review of plan AB, B1: nprQif carries no rpcids query (gflow migrated_composer.py:174-178), so a check that
    read only the URL reported every paid start+end run as an error."""
    refs = video.frame_references([TEAPOT, CUP], RECORDS, "m-teapot", "m-cup")
    check = video.VideoBodyCheck("i2v", refs, None)

    class _Request:
        url = "https://flow.google.com/_/x/data/batchexecute?rt=c"
        post_data = 'f.req=[[["nprQif","[[\\"w-teapot\\",\\"w-cup\\"],\\"veo_3_1_interpolation_lite\\"]",null,"generic"]]]'

    check.on_request(_Request())
    report = check.report()
    assert report["ok"] is True and report["rpcid"] == "nprQif", report


def test_a_live_price_that_differs_from_the_surveyed_cell_is_refused_before_the_click(monkeypatch, tmp_path):
    """Review of plan AB, finding 2: under a cap alone, a pin that slipped to a cheaper cell (asked 720p, got 360p)
    paid the cheaper price for the wrong clip. A surveyed cell must quote exactly its surveyed price."""
    log = []
    _install(monkeypatch, tmp_path, log)
    _counted(monkeypatch, log, (6, 6))

    with pytest.raises(RuntimeError, match="surveyed.*12"):
        _submit(_SubmitSession(log), tmp_path, log, expected_credits=12, max_credits=12)
    assert "click" not in log
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows() == []


def test_a_cell_with_no_surveyed_price_runs_on_the_cap_alone(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", PROMPT)], balance_reads=(200, 191, 191))
    _counted(monkeypatch, log, (9, 9))

    result = _submit(
        _SubmitSession(log), tmp_path, log, expected_credits=0, max_credits=10, strict_output=True
    )

    assert result["spent"] == 9


def test_ingredients_holds_its_run_to_the_surveyed_price_gen_video_passes(monkeypatch):
    calls = _driver_world(monkeypatch)

    _run(model="omni-flash", resolution="360p", duration=8, media_ids=["m-teapot"])

    assert calls["ingredients"]["table_credits"] == 6


def test_x2_keeps_waiting_while_only_one_clip_is_listed(monkeypatch, tmp_path):
    """Mutant of plan AB: stopping at the first clip of an x2 run passed every other test, since they list both clips
    at once. Here the first poll holds one clip and the second both; the run must wait for the second."""
    log = []
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 176, 176))
    _counted(monkeypatch, log, (24, 24))
    before = [_video("w-before", "old")]
    polls = iter(
        [
            before,
            [*before, _video("w-a", PROMPT)],
            *([[*before, _video("w-a", PROMPT), _video("w-b", PROMPT)]] * 5),
        ]
    )

    async def snapshot(session, project_id, attempts=4):
        log.append("snapshot")
        return next(polls), set()

    real_sleep = asyncio.sleep

    async def no_wait(seconds):
        await real_sleep(0)

    monkeypatch.setattr(composer, "snapshot", snapshot)
    monkeypatch.setattr(composer.asyncio, "sleep", no_wait)

    result = _submit(
        _SubmitSession(log),
        tmp_path,
        log,
        count=2,
        max_credits=24,
        expected_credits=24,
        strict_output=True,
        wait=60.0,
    )

    assert sorted(o["media_id"] for o in result["outputs"]) == ["m-w-a", "m-w-b"]


@pytest.mark.parametrize(
    ("key", "resolution", "ok"),
    [
        # Measured 2026-09-29 (plan AB, jobs ab-1 and ab-4): a 360p submit's key ends in _360p, a 720p one carries none.
        ("abra_t2v_4s_360p", "360p", True),
        ("abra_t2v_4s", "720p", True),
        ("abra_t2v_4s", "360p", False),
        ("abra_t2v_4s_360p", "720p", False),
        ("veo_3_1_t2v_lite", None, True),
    ],
)
def test_the_body_check_holds_the_submit_to_the_resolution_asked(key, resolution, ok):
    check = video.VideoBodyCheck("t2v", [], None, resolution)

    class _Request:
        url = "https://flow.google.com/_/x/data/batchexecute?rpcids=YhhmEf"
        post_data = f"f.req=%5B%22{key}%22%5D"

    check.on_request(_Request())
    assert check.report()["ok"] is ok, check.report()


def test_the_driver_asks_the_body_check_for_the_resolution_it_pinned(monkeypatch):
    calls = _driver_world(monkeypatch)

    _run(model="omni-flash", resolution="360p", duration=4)
    assert calls["submit"]["watch"].resolution == "360p"

    _run(model="veo-lite")
    assert calls["submit"]["watch"].resolution is None


class _LatePickerPage(_PickerPage):
    """A picker whose search results show the just-uploaded image only after a few looks (measured 2026-09-29)."""

    def __init__(self, tiles, after, looks_before):
        super().__init__(tiles, after)
        self.looks_before = looks_before

    def locator(self, selector):
        if "img" in selector:
            self.looks_before -= 1
            if self.looks_before >= 0:
                return _Loc(self, [{"src": "https://x/other-tile-000000000000000000", "what": "tile other"}])
        return super().locator(selector)


def test_a_frame_just_uploaded_is_waited_for_in_the_picker():
    """Measured 2026-09-29: the picker did not list v4_s3_last.png right after flow_upload; the same call a moment later
    found it."""
    ref = video.frame_references([TEAPOT, CUP], RECORDS, "m-teapot", None)[0]
    page = _LatePickerPage(["https://x/" + RECORDS[0]["url"][-40:]], [], looks_before=3)

    asyncio.run(video.pin_frame(page, "Start", ref))

    assert page.clicked[-1] == "tile " + RECORDS[0]["url"][-6:], page.clicked


def test_a_frame_the_picker_never_shows_is_still_refused_without_a_click():
    ref = video.frame_references([TEAPOT, CUP], RECORDS, "m-teapot", None)[0]
    page = _LatePickerPage(["https://x/" + RECORDS[0]["url"][-40:]], [], looks_before=10_000)

    with pytest.raises(LookupError, match="no tile"):
        asyncio.run(video.pin_frame(page, "Start", ref))
    assert not any(str(c).startswith("tile") and "other" not in str(c) for c in page.clicked)


TWIN = CUP | {"title": TEAPOT["title"]}


def test_two_images_with_one_title_are_each_told_apart_by_their_own_url():
    """Measured 2026-09-30: gen_i2i images carry Flow's own titles, which repeat ("Woman posing in bedroom" twice), and
    the unique-title rule refused them; the picker tile is chosen by the listing url, the title is only search text."""
    refs = video.frame_references([TEAPOT, TWIN], RECORDS, "m-teapot", "m-cup")

    assert [(r.id, r.title, r.tail) for r in refs] == [
        ("m-teapot", TEAPOT["title"], RECORDS[0]["url"][-24:]),
        ("m-cup", TEAPOT["title"], RECORDS[1]["url"][-24:]),
    ]


@pytest.mark.parametrize(("asked", "record"), [("m-teapot", 0), ("m-cup", 1)])
def test_a_picker_showing_both_same_titled_images_pins_only_the_one_asked(asked, record):
    ref = video.frame_references([TEAPOT, TWIN], RECORDS, asked, None)[0]
    page = _PickerPage(["https://x/" + r["url"][-40:] for r in RECORDS], [])

    asyncio.run(video.pin_frame(page, "Start", ref))

    tiles = [c for c in page.clicked if str(c).startswith("tile")]
    assert tiles == ["tile " + RECORDS[record]["url"][-6:]], page.clicked


def test_two_same_titled_images_whose_urls_end_alike_are_still_refused():
    same_tail = [RECORDS[0], RECORDS[1] | {"url": "https://lh3/zz" + RECORDS[0]["url"][-24:]}]
    with pytest.raises(LookupError, match="rename the file"):
        video.frame_references([TEAPOT, TWIN], same_tail, "m-teapot", None)


class _ListPicker:
    """Flow's frame picker as measured 2026-09-30 when the search finds several images: a list of results beside a
    preview, where clicking a result only previews it and 'Add to prompt' pins the previewed image."""

    def __init__(self, results):
        self.results, self.preview, self.pinned, self.clicked = results, results[0], None, []
        self.keyboard = self

    async def insert_text(self, text):
        self.clicked.append(f"typed {text}")

    async def wait_for_timeout(self, ms):
        return None

    def locator(self, selector):
        if "img" in selector:
            items = [{"src": s, "what": ("select", s)} for s in self.results] + [
                {"src": self.preview, "what": "big"}
            ]
            return _ListLoc(self, items)
        if "button" in selector:
            return _ListLoc(
                self, [{"text": video.ADD_TO_PROMPT, "what": "add"}] if self.pinned is None else []
            )
        if "input" in selector:
            return _ListLoc(self, [{"what": "search"}])
        return _ListLoc(self, [{"what": "bar"}])

    async def evaluate(self, script, arg=None):
        if script == video._PICKER_PREVIEW_JS:
            return self.preview
        return ["Image ingredient" if self.pinned else "Start", "Swap first and last frames", "End"]

    def act(self, what):
        self.clicked.append(what)
        if isinstance(what, tuple):
            self.preview = what[1]
        elif what == "add":
            self.pinned = self.preview


class _ListLoc(_Loc):
    def filter(self, has_text=None):
        return _ListLoc(self.page, [i for i in self.items if has_text in i.get("text", "")])

    async def click(self, timeout=None):
        self.page.act(self.items[0].get("what"))


def test_a_list_picker_pins_the_asked_image_through_add_to_prompt_after_its_preview_shows_it():
    """Live dry run 2026-09-30 with a shared title: the url tile was found and clicked, and the Start slot stayed empty;
    the click had only previewed the image, 'Add to prompt' pins it."""
    ref = video.frame_references([TEAPOT, TWIN], RECORDS, "m-cup", None)[0]
    cup, teapot = ("https://x/" + r["url"][-40:] for r in (RECORDS[1], RECORDS[0]))
    page = _ListPicker([teapot, cup])

    asyncio.run(video.pin_frame(page, "Start", ref))

    assert page.pinned == cup, page.clicked
    assert page.clicked[-2:] == [("select", cup), "add"], page.clicked


def test_a_preview_that_does_not_show_the_asked_image_is_never_added():
    ref = video.frame_references([TEAPOT, TWIN], RECORDS, "m-cup", None)[0]
    cup, teapot = ("https://x/" + r["url"][-40:] for r in (RECORDS[1], RECORDS[0]))
    page = _ListPicker([teapot, cup])
    page.act = lambda what: page.clicked.append(
        what
    )  # the click never moves the preview off the first result

    with pytest.raises(LookupError, match="preview"):
        asyncio.run(video.pin_frame(page, "Start", ref))
    assert "add" not in page.clicked, page.clicked


SIZED = "=s512-rw"


def test_a_tile_whose_src_carries_a_size_suffix_is_still_the_asked_image():
    """Measured 2026-10-01: overnight the picker's img src gained '=s512-rw' after the token the listing url ends on
    ('...RpDlNcxn=s512-rw' against '...RpDlNcxn'), so no tile matched and every Frames run was refused."""
    ref = video.frame_references([TEAPOT, CUP], RECORDS, "m-cup", None)[0]
    cup, teapot = ("https://x/" + r["url"][-40:] + SIZED for r in (RECORDS[1], RECORDS[0]))
    page = _ListPicker([teapot, cup])

    asyncio.run(video.pin_frame(page, "Start", ref))

    assert page.pinned == cup, page.clicked
    assert page.clicked[-2:] == [("select", cup), "add"], page.clicked


def test_a_sized_tile_whose_token_only_contains_the_asked_tail_is_never_clicked():
    ref = video.frame_references([TEAPOT, CUP], RECORDS, "m-cup", None)[0]
    page = _ListPicker(["https://x/" + RECORDS[1]["url"][-40:] + "EXTRA" + SIZED])

    with pytest.raises(LookupError, match="no tile"):
        asyncio.run(video.pin_frame(page, "Start", ref))
    assert not any(isinstance(c, tuple) or c == "add" for c in page.clicked), page.clicked


@pytest.mark.parametrize(
    ("src", "bare"),
    [
        ("https://flow.google.com/asb/TOKEN=s512-rw", "https://flow.google.com/asb/TOKEN"),
        ("https://lh3/asb/TOKEN=m22", "https://lh3/asb/TOKEN"),
        ("https://lh3/asb/TOKEN", "https://lh3/asb/TOKEN"),
        ("https://lh3/asb/TO=KEN", "https://lh3/asb/TO"),
        (None, ""),
    ],
)
def test_a_rendition_suffix_is_dropped_from_the_end_of_a_src(src, bare):
    assert video._bare_src(src) == bare
