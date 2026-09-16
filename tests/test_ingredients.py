import asyncio
import json
import re
from urllib.parse import quote_plus

import pytest

from video import gen
from video.flow import clips, composer, ingredients

ENTITY = "47e5150d-9c4b-4165-a937-4852e9abd194"
OTHER = "11111111-2222-3333-4444-555555555555"
MEDIA = "53a8930b-fe54-40a0-a9c2-a59859ff126f"
WORKFLOW = "02a43d09-76e6-463c-8765-f9ce8df34592"
PROMPT = "stands in a sunny bakery and smiles at the camera"

THU = ingredients.Reference("entity", ENTITY, "Thu", frozenset({ENTITY}))
IMAGE = ingredients.Reference("media", MEDIA, "peobj1.png", frozenset({WORKFLOW}))


def _character(entity=ENTITY, name="Thu"):
    return {"entity_id": entity, "name": name, "portrait_media_id": None, "portrait_workflow_id": None}


def _image(media=MEDIA, title="peobj1.png", kind="image"):
    return {"id": media, "title": title, "kind": kind}


def _record(media=MEDIA, workflow=WORKFLOW):
    return {"id": media, "workflow_id": workflow}


# resolve


def test_resolve_names_each_character_then_each_image_with_the_ids_their_chips_carry():
    references = ingredients.resolve(
        [_character()], [_image()], [_record(), _record(workflow="w-older")], [ENTITY], [MEDIA], "omni-flash"
    )
    assert [(r.kind, r.id, r.title) for r in references] == [
        ("entity", ENTITY, "Thu"),
        ("media", MEDIA, "peobj1.png"),
    ]
    assert references[0].mention_ids == {ENTITY}
    # An image chip names the image's workflow id, never its media id (measured in T1).
    assert references[1].mention_ids == {WORKFLOW, "w-older"}


def test_resolve_refuses_an_entity_that_is_not_a_character_of_the_project():
    with pytest.raises(LookupError, match=OTHER):
        ingredients.resolve([_character()], [], [], [OTHER], [], "omni-flash")


def test_resolve_refuses_a_character_whose_name_another_character_carries():
    twins = [_character(), _character(entity=OTHER, name=" THU ")]
    with pytest.raises(LookupError, match="2 characters are named"):
        ingredients.resolve(twins, [], [], [ENTITY], [], "omni-flash")


def test_resolve_refuses_a_character_with_no_name_to_type():
    with pytest.raises(LookupError, match="no name"):
        ingredients.resolve([_character(name="  ")], [], [], [ENTITY], [], "omni-flash")


@pytest.mark.parametrize(
    ("media", "records", "error", "message"),
    [
        ([], [], LookupError, "not a media of this project"),
        ([_image(kind="video")], [_record()], ValueError, "only images"),
        ([_image(title=" ")], [_record()], LookupError, "no title"),
        (
            [_image(), _image(media=OTHER, title="PEOBJ1.png")],
            [_record()],
            LookupError,
            "2 images are titled",
        ),
        ([_image()], [_record(media=OTHER)], LookupError, "no workflow id"),
    ],
)
def test_resolve_refuses_an_image_it_cannot_mention(media, records, error, message):
    with pytest.raises(error, match=message):
        ingredients.resolve([_character()], media, records, [ENTITY], [MEDIA], "omni-flash")


def test_resolve_refuses_more_references_than_the_model_takes():
    characters = [_character(entity=f"e{index}", name=f"n{index}") for index in range(3)]
    ids = [c["entity_id"] for c in characters]
    with pytest.raises(ValueError, match="at most 3"):
        ingredients.resolve(characters, [_image()], [_record()], ids, [MEDIA], "veo-lite")
    assert len(ingredients.resolve(characters, [_image()], [_record()], ids, [MEDIA], "omni-flash")) == 4


def test_resolve_refuses_no_character_and_a_repeated_id():
    with pytest.raises(ValueError, match="at least one character"):
        ingredients.resolve([_character()], [_image()], [_record()], [], [MEDIA], "omni-flash")
    with pytest.raises(ValueError, match="named once"):
        ingredients.resolve([_character()], [], [], [ENTITY, ENTITY], [], "omni-flash")


def test_query_is_the_title_up_to_its_first_space_without_an_image_extension():
    assert ingredients.Reference("entity", ENTITY, "Mai Anh", frozenset({ENTITY})).query == "Mai"
    assert IMAGE.query == "peobj1"
    assert (
        ingredients.Reference("media", MEDIA, "shot.final.JPG", frozenset({WORKFLOW})).query == "shot.final"
    )
    assert ingredients.Reference("entity", ENTITY, "Lab.v2", frozenset({ENTITY})).query == "Lab.v2"


# attach


class _Keyboard:
    def __init__(self):
        self.typed = ""
        self.pressed = []

    async def type(self, text, delay=0):
        self.typed += text

    async def press(self, key):
        self.pressed.append(key)
        if key == "Backspace":
            self.typed = self.typed[:-1]


class _Option:
    def __init__(self, page, index):
        self.page = page
        self.index = index

    async def click(self, timeout=None):
        option = self.page.shown()[self.index]
        self.page.clicked.append((option["title"], option["kind"]))
        if option.get("chip") is not None:
            self.page.chips.append(dict(option["chip"]))


class _Options:
    def __init__(self, page):
        self.page = page

    async def count(self):
        return len(self.page.shown())

    def nth(self, index):
        return _Option(self.page, index)


class _MentionPage:
    """The @ picker as T1 measured it: options only once '@' is typed, no id on them, and a click inserts the chip."""

    def __init__(self, options, *, shows_after_ms=0, chips=()):
        self.options = options
        self.shows_after_ms = shows_after_ms
        self.chips = [dict(chip) for chip in chips]
        self.clicked = []
        self.clock = 0
        self.keyboard = _Keyboard()

    def shown(self):
        if "@" not in self.keyboard.typed or self.clock < self.shows_after_ms:
            return []
        return self.options

    async def wait_for_timeout(self, ms):
        self.clock += ms

    def locator(self, selector):
        assert selector == ingredients.OPTION
        return _Options(self)

    async def evaluate(self, script, arg=None):
        if script == ingredients._OPTIONS_JS:
            return [
                {"title": o["title"], "kind": o["kind"], "visible": o.get("visible", True)}
                for o in self.shown()
            ]
        if script == ingredients._CHIPS_JS:
            return [dict(chip) for chip in self.chips]
        raise AssertionError(f"unexpected script {script[:40]!r}")


def _chip(kind, mention, entity="", text="Thu"):
    return {"kind": kind, "id": mention, "entity": entity, "text": text}


def _option(title, kind, chip):
    return {"title": title, "kind": kind, "chip": chip}


def test_attach_clicks_the_one_option_of_that_title_and_kind_and_never_presses_enter():
    page = _MentionPage(
        [
            _option("Thu", "Image", _chip("media", "w-namesake", text="Thu")),
            _option("Thu", "Character", _chip("entity", ENTITY, ENTITY)),
        ]
    )
    chip = asyncio.run(ingredients.attach(page, THU))
    assert chip == {"kind": "entity", "id": ENTITY, "text": "Thu"}
    assert page.clicked == [("Thu", "Character")]
    assert page.keyboard.typed == "@Thu"
    assert "Enter" not in page.keyboard.pressed


def test_attach_refuses_when_no_option_is_that_title_and_kind_and_erases_its_query():
    page = _MentionPage([_option("Thursday", "Character", _chip("entity", OTHER, OTHER))])
    with pytest.raises(LookupError, match="0 picker options"):
        asyncio.run(ingredients.attach(page, THU))
    assert page.clicked == []
    assert page.keyboard.pressed.count("Backspace") == len("@Thu")
    assert page.keyboard.typed == ""


def test_attach_refuses_two_options_of_the_same_title_and_kind():
    same = _chip("entity", ENTITY, ENTITY)
    page = _MentionPage([_option("Thu", "Character", same), _option("thu", "Character", same)])
    with pytest.raises(LookupError, match="2 picker options"):
        asyncio.run(ingredients.attach(page, THU))
    assert page.clicked == []


def test_attach_refuses_a_chip_of_the_wrong_kind():
    page = _MentionPage([_option("Thu", "Character", _chip("media", ENTITY))])
    with pytest.raises(LookupError, match="refusing"):
        asyncio.run(ingredients.attach(page, THU))


def test_attach_refuses_a_chip_that_names_another_entity():
    page = _MentionPage([_option("Thu", "Character", _chip("entity", OTHER, OTHER))])
    with pytest.raises(LookupError, match="refusing"):
        asyncio.run(ingredients.attach(page, THU))


def test_attach_refuses_a_character_chip_whose_entity_attribute_disagrees():
    page = _MentionPage([_option("Thu", "Character", _chip("entity", ENTITY, OTHER))])
    with pytest.raises(LookupError, match="refusing"):
        asyncio.run(ingredients.attach(page, THU))


def test_attach_checks_an_image_chip_against_the_image_workflow_id():
    good = _MentionPage([_option("peobj1.png", "Image", _chip("media", WORKFLOW, text="peobj1.png"))])
    assert asyncio.run(ingredients.attach(good, IMAGE))["id"] == WORKFLOW
    assert good.keyboard.typed == "@peobj1"
    by_media_id = _MentionPage([_option("peobj1.png", "Image", _chip("media", MEDIA, text="peobj1.png"))])
    with pytest.raises(LookupError, match="refusing"):
        asyncio.run(ingredients.attach(by_media_id, IMAGE))


def test_attach_judges_only_the_chip_its_own_click_added():
    earlier = _chip("entity", OTHER, OTHER, text="Lan")
    page = _MentionPage([_option("Thu", "Character", _chip("entity", ENTITY, ENTITY))], chips=[earlier])
    assert asyncio.run(ingredients.attach(page, THU))["id"] == ENTITY


def test_attach_refuses_a_click_that_adds_no_chip():
    page = _MentionPage([_option("Thu", "Character", None)])
    with pytest.raises(LookupError, match="refusing"):
        asyncio.run(ingredients.attach(page, THU))


def test_attach_waits_for_options_that_render_late_and_gives_up_after_its_bound():
    late = _MentionPage([_option("Thu", "Character", _chip("entity", ENTITY, ENTITY))], shows_after_ms=4_000)
    assert asyncio.run(ingredients.attach(late, THU))["id"] == ENTITY
    never = _MentionPage([_option("Thu", "Character", _chip("entity", ENTITY, ENTITY))], shows_after_ms=10**9)
    with pytest.raises(LookupError, match="0 picker options"):
        asyncio.run(ingredients.attach(never, THU))
    assert never.clock <= ingredients.OPTION_WAIT_MS + 5_000


# the submit body


def _request(rpcid, *parts):
    url = f"https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids={rpcid}&source-path=%2Fp"
    inner = json.dumps(["abra_r2v_8s", *parts])
    return type(
        "Request", (), {"url": url, "post_data": "f.req=" + quote_plus(json.dumps([[[rpcid, inner]]]))}
    )()


def test_body_check_accepts_a_references_submit_carrying_every_reference():
    check = ingredients.SubmitBodyCheck([THU, IMAGE])
    check.on_request(_request("MZZa6b", ENTITY, WORKFLOW))
    assert check.report() == {"rpcid": "MZZa6b", "model_keys": ["abra_r2v_8s"], "missing": [], "ok": True}


def test_body_check_names_a_reference_the_body_does_not_carry():
    check = ingredients.SubmitBodyCheck([THU, IMAGE])
    check.on_request(_request("MZZa6b", ENTITY, MEDIA))
    assert check.report()["missing"] == [MEDIA]
    assert check.report()["ok"] is False


def test_body_check_fails_a_submit_that_is_not_references_to_video():
    check = ingredients.SubmitBodyCheck([THU])
    request = _request("MZZa6b", ENTITY)
    request.post_data = request.post_data.replace("r2v", "t2v")
    check.on_request(request)
    assert check.report()["ok"] is False


def test_body_check_ignores_requests_that_are_not_a_submit():
    check = ingredients.SubmitBodyCheck([THU])
    check.on_request(_request("WuwhI", ENTITY))
    check.on_request(_request("nzlxg"))
    assert check.report() == {"rpcid": None, "model_keys": [], "missing": [ENTITY], "ok": False}


# the duration pin


class _Radio:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    async def count(self):
        return 1 if self.page.clock >= self.page.appears_after_ms else 0

    async def get_attribute(self, name):
        assert name == "aria-checked"
        return "true" if self.page.checked else "false"

    async def click(self, timeout=None):
        self.page.clicks += 1
        self.page.checked = self.page.sticks


class _Radios:
    def __init__(self, page):
        self.page = page

    def filter(self, has_text):
        assert has_text.search("8s") and not has_text.search("18s") and not has_text.search("8s info")
        return _Radio(self.page)


class _RadioPage:
    def __init__(self, *, appears_after_ms=0, checked=False, sticks=True):
        self.appears_after_ms = appears_after_ms
        self.checked = checked
        self.sticks = sticks
        self.clicks = 0
        self.clock = 0
        self.keyboard = _Keyboard()

    async def wait_for_timeout(self, ms):
        self.clock += ms

    def locator(self, selector):
        assert selector == ingredients.RADIO
        return _Radios(self)


@pytest.fixture
def no_settings_pane(monkeypatch):
    opened = []

    async def open_settings(page, label="settings"):
        opened.append(label)
        return ""

    monkeypatch.setattr(composer, "_open_settings", open_settings)
    return opened


def test_pin_duration_clicks_8s_once_its_radio_renders(no_settings_pane):
    page = _RadioPage(appears_after_ms=3_000)
    assert asyncio.run(ingredients.pin_duration(page)) == "pinned"
    assert page.clicks == 1
    assert page.keyboard.pressed[-1] == "Escape"


def test_pin_duration_leaves_a_checked_radio_alone(no_settings_pane):
    page = _RadioPage(checked=True)
    assert asyncio.run(ingredients.pin_duration(page)) == "pinned"
    assert page.clicks == 0


def test_pin_duration_reports_absent_when_no_radio_renders_within_its_bound(no_settings_pane):
    page = _RadioPage(appears_after_ms=10**9)
    assert asyncio.run(ingredients.pin_duration(page)) == "absent"
    assert page.clock <= ingredients.RADIO_WAIT_MS + 2_000
    assert page.keyboard.pressed[-1] == "Escape"


def test_pin_duration_refuses_a_radio_that_will_not_stay_checked(no_settings_pane):
    page = _RadioPage(sticks=False)
    with pytest.raises(LookupError, match="did not stay checked"):
        asyncio.run(ingredients.pin_duration(page))


# composer._submit


class _Start:
    def __init__(self, log):
        self.log = log

    @property
    def first(self):
        return self

    async def click(self, timeout=None):
        self.log.append("click")


class _Box:
    first = None


class _SubmitPage:
    def __init__(self, log):
        self.log = log
        self.listeners = []

    async def wait_for_timeout(self, ms):
        return None

    def locator(self, selector):
        return type("Box", (), {"first": object()})()

    def get_by_role(self, role, name=None):
        assert role == "button" and name.search("Start generation")
        return _Start(self.log)

    async def screenshot(self, path=None):
        return None

    def on(self, event, listener):
        assert event == "request"
        self.listeners.append(listener)
        self.log.append("listen")

    def remove_listener(self, event, listener):
        self.listeners.remove(listener)
        self.log.append("unlisten")

    async def evaluate(self, script, arg=None):
        assert script == composer._LEFT_JS
        return {"mentions": 0, "text": ""}


class _SubmitSession:
    def __init__(self, log):
        self.log = log
        self.page = _SubmitPage(log)

    async def goto(self, url, ready=None):
        self.log.append("goto")

    @staticmethod
    def project_url(project_id):
        return f"https://flow.google.com/project/{project_id}"


def _video(workflow, prompt, done=True):
    return {
        "workflow_id": workflow,
        "id": f"m-{workflow}",
        "kind": "video",
        "prompt": prompt,
        "status": clips.DONE_STATUS if done else 1,
        "url": "https://lh3/x" if done else None,
        "created": 1,
    }


def _install(monkeypatch, tmp_path, log, *, price=12, fresh=()):
    listings = iter([[_video("w-before", "old")], *([[_video("w-before", "old"), *fresh]] * 20)])
    balances = iter([200, 188])

    async def snapshot(session, project_id, attempts=4):
        log.append("snapshot")
        return next(listings), set()

    async def credits(session):
        log.append("credits")
        return {"balance": next(balances)}

    async def set_mode(session, project_id, enabled):
        log.append(f"agent {enabled}")
        return {"was": False}

    async def clear_prompt(session):
        log.append("clear")
        return {"chips": 0, "thumbs": 0, "text": 0}

    async def configure(session, *, mode="Ingredients", aspect="9:16", count="x1", label="settings"):
        log.append(f"configure {label}")
        return {"applied": {}, "price": price, "settings_text": ""}

    async def type_prompt(page, box, prompt, attempts=3):
        log.append("type")
        return True

    async def await_submit(session, click, *, settle=30.0, patience=90.0):
        statuses = [row["status"] for row in gen.Ledger(tmp_path / "ledger.jsonl").rows()]
        log.append(f"ledger at click {statuses}")
        for listener in list(session.page.listeners):
            listener("request during the click")
        await click()
        return {"MZZa6b": [[]]}

    async def notice(page):
        return ""

    async def fetch_720(session, record, stem, attempts=6):
        log.append(f"fetch {record['workflow_id']}")
        return tmp_path / "clip.mp4"

    monkeypatch.setattr(composer, "snapshot", snapshot)
    monkeypatch.setattr(composer.reader, "credits", credits)
    monkeypatch.setattr(composer.agent, "set_mode", set_mode)
    monkeypatch.setattr(composer, "clear_prompt", clear_prompt)
    monkeypatch.setattr(composer, "configure", configure)
    monkeypatch.setattr(composer, "_type_prompt", type_prompt)
    monkeypatch.setattr(composer.clips, "_await_submit", await_submit)
    monkeypatch.setattr(composer, "_notice", notice)
    monkeypatch.setattr(composer, "fetch_720", fetch_720)


def _setup(log, result=None):
    async def setup(session):
        log.append("setup")
        return result

    return setup


def _submit(session, tmp_path, log, **options):
    arguments = {
        "prompt": PROMPT,
        "setup": _setup(log),
        "kind": "character",
        "job_id": "job-1",
        "expected_credits": 12,
        "out_dir": tmp_path,
        "wait": 0.0,
        **options,
    }
    return asyncio.run(composer._submit(session, "p-1", **arguments))


def test_submit_writes_the_intent_row_before_the_click_and_the_outcome_after(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", f"Thu {PROMPT}")])
    result = _submit(_SubmitSession(log), tmp_path, log)
    assert log[: log.index("click") + 1] == [
        "snapshot",
        "credits",
        "agent False",
        "goto",
        "clear",
        "configure pre",
        "setup",
        "type",
        "configure confirm",
        "ledger at click ['submitted']",
        "click",
    ]
    assert [row["status"] for row in gen.Ledger(tmp_path / "ledger.jsonl").rows()] == ["submitted", "done"]
    assert result["spent"] == 12 and result["media_id"] == "m-w-new"


def test_submit_dry_run_never_clicks_never_reads_the_balance_and_writes_no_ledger(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log)
    setup = _setup(log, {"chips": [{"kind": "entity", "id": ENTITY, "text": "Thu"}]})
    result = _submit(_SubmitSession(log), tmp_path, log, job_id=None, dry_run=True, setup=setup)
    assert result == {
        "dry_run": True,
        "kind": "character",
        "quoted_credits": 12,
        "expected_credits": 12,
        "price_ok": True,
        "composer_left": {"mentions": 0, "text": ""},
        "chips": [{"kind": "entity", "id": ENTITY, "text": "Thu"}],
    }
    assert "click" not in log and "snapshot" not in log and "credits" not in log
    assert log.count("clear") == 2 and log[-1] == "clear"
    assert not (tmp_path / "ledger.jsonl").exists()


def test_submit_dry_run_reports_a_price_that_differs_instead_of_raising(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, price=15)
    result = _submit(_SubmitSession(log), tmp_path, log, job_id=None, dry_run=True)
    assert result["quoted_credits"] == 15 and result["price_ok"] is False


def test_submit_refuses_a_price_that_differs_before_any_ledger_row(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, price=15)
    with pytest.raises(RuntimeError, match="composer says 15"):
        _submit(_SubmitSession(log), tmp_path, log)
    assert "click" not in log
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows() == []


def test_submit_keeps_the_story_defaults_when_no_new_option_is_given(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", "a prompt nobody typed")])
    result = _submit(_SubmitSession(log), tmp_path, log)
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert [row["status"] for row in rows] == ["submitted", "done"]
    assert "fetch w-new" in log, "without strict_output the newest video is still taken, as story expects"
    assert "listen" not in log
    assert not {"chips", "body_check", "candidates"} & (set(rows[0]) | set(rows[1]) | set(result))


def test_submit_strict_output_takes_no_record_that_lacks_the_prompt(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", "a prompt nobody typed")])
    with pytest.raises(RuntimeError, match="nothing was generated"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert not any(entry.startswith("fetch") for entry in log)
    assert [row["status"] for row in gen.Ledger(tmp_path / "ledger.jsonl").rows()] == ["submitted", "failed"]


def test_submit_strict_output_never_picks_between_two_records_carrying_the_prompt(monkeypatch, tmp_path):
    log = []
    fresh = [_video("w-one", f"Thu {PROMPT}"), _video("w-two", f"Thu {PROMPT}")]
    _install(monkeypatch, tmp_path, log, fresh=fresh)
    with pytest.raises(RuntimeError, match="2 new records"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert not any(entry.startswith("fetch") for entry in log)
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert rows[-1]["status"] == "unknown"
    assert rows[-1]["candidates"] == ["m-w-one", "m-w-two"]


def test_submit_strict_output_takes_the_one_record_carrying_the_prompt(monkeypatch, tmp_path):
    log = []
    fresh = [_video("w-other", "someone else's prompt"), _video("w-mine", f"Thu {PROMPT}")]
    _install(monkeypatch, tmp_path, log, fresh=fresh)
    result = _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert result["media_id"] == "m-w-mine"


class _Watch:
    def __init__(self):
        self.heard = []

    def on_request(self, request):
        self.heard.append(request)

    def report(self):
        return {"ok": bool(self.heard), "heard": len(self.heard)}


def test_submit_watch_hears_the_click_window_only_and_its_report_lands_in_the_outcome_row(
    monkeypatch, tmp_path
):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", f"Thu {PROMPT}")])
    watch = _Watch()
    result = _submit(_SubmitSession(log), tmp_path, log, watch=watch, strict_output=True)
    assert log.index("configure confirm") < log.index("listen") < log.index("click") < log.index("unlisten")
    assert watch.heard == ["request during the click"]
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert "body_check" not in rows[0]
    assert rows[1]["body_check"] == {"ok": True, "heard": 1}
    assert result["body_check"] == {"ok": True, "heard": 1}


def test_submit_merges_what_setup_returns_into_the_intent_row_and_the_result(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", f"Thu {PROMPT}")])
    extra = {"model": "omni-flash", "chips": [{"kind": "entity", "id": ENTITY, "text": "Thu"}]}
    result = _submit(_SubmitSession(log), tmp_path, log, setup=_setup(log, extra), strict_output=True)
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert rows[0]["chips"] == extra["chips"] and rows[0]["model"] == "omni-flash"
    assert result["chips"] == extra["chips"]


# generate


class _GeneratePage:
    def __init__(self, log):
        self.log = log

    def locator(self, selector):
        log = self.log

        class _First:
            async def click(self, timeout=None):
                log.append(f"click {selector}")

        return type("Found", (), {"first": _First()})()


def _generate_world(monkeypatch, log, *, was=False, chips_on_page=None):
    async def listing(session, project_id):
        log.append("listing")
        return "payload"

    monkeypatch.setattr(ingredients, "_listing", listing)
    monkeypatch.setattr(ingredients.parsers, "characters_from_listing", lambda payload: [_character()])
    monkeypatch.setattr(ingredients.parsers, "media", lambda payload: [_image()])
    monkeypatch.setattr(ingredients.parsers, "records", lambda payload: [_record()])

    async def set_mode(session, project_id, enabled):
        log.append(f"agent {enabled}")
        return {"was": was}

    monkeypatch.setattr(ingredients.agent, "set_mode", set_mode)
    captured = {}

    async def submit(session, project_id, **kwargs):
        log.append("submit")
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(ingredients.composer, "_submit", submit)
    return captured


def test_generate_resolves_the_references_before_it_opens_the_composer(monkeypatch):
    log = []
    _generate_world(monkeypatch, log)
    with pytest.raises(LookupError, match=OTHER):
        asyncio.run(ingredients.generate(object(), "p-1", prompt=PROMPT, characters=[OTHER], job_id="job-1"))
    assert log == ["listing"]


def test_generate_prices_by_model_and_asks_for_strict_output_and_the_body_check(monkeypatch, tmp_path):
    log = []
    captured = _generate_world(monkeypatch, log)
    asyncio.run(
        ingredients.generate(
            object(),
            "p-1",
            prompt=PROMPT,
            characters=[ENTITY],
            media_ids=[MEDIA],
            model="veo-lite",
            aspect="16:9",
            job_id="job-1",
            out_dir=tmp_path,
        )
    )
    assert captured["expected_credits"] == 10
    assert captured["strict_output"] is True and captured["dry_run"] is False
    assert (
        captured["kind"] == "character" and captured["mode"] == "Ingredients" and captured["aspect"] == "16:9"
    )
    assert isinstance(captured["watch"], ingredients.SubmitBodyCheck)
    assert [r.id for r in captured["watch"].references] == [ENTITY, MEDIA]
    assert log == ["listing", "agent False", "submit"]


def test_generate_puts_agent_mode_back_the_way_it_found_it_even_when_the_submit_fails(monkeypatch):
    log = []
    _generate_world(monkeypatch, log, was=True)

    async def failing(session, project_id, **kwargs):
        log.append("submit")
        raise RuntimeError("nothing was generated")

    monkeypatch.setattr(ingredients.composer, "_submit", failing)
    with pytest.raises(RuntimeError):
        asyncio.run(ingredients.generate(object(), "p-1", prompt=PROMPT, characters=[ENTITY], job_id="job-1"))
    assert log == ["listing", "agent False", "submit", "agent True"]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"model": "veo-quality", "job_id": "job-1"}, "model must be one of"),
        ({"aspect": "1:1", "job_id": "job-1"}, "aspect must be one of"),
        ({"job_id": None}, "job_id is required"),
    ],
)
def test_generate_refuses_what_it_cannot_price_before_reading_anything(monkeypatch, options, message):
    log = []
    _generate_world(monkeypatch, log)
    with pytest.raises(ValueError, match=message):
        asyncio.run(ingredients.generate(object(), "p-1", prompt=PROMPT, characters=[ENTITY], **options))
    assert log == []


def test_generate_setup_sets_the_model_pins_8s_and_attaches_every_reference_in_order(monkeypatch, tmp_path):
    log = []
    captured = _generate_world(monkeypatch, log)

    async def apply_settings(page, model, aspect, references):
        log.append(f"settings {model} {aspect} {[r.id for r in references]}")

    async def pin_duration(page):
        log.append("pin")
        return "pinned"

    async def attach(page, reference):
        log.append(f"attach {reference.id}")
        return {"kind": reference.kind, "id": min(reference.mention_ids), "text": reference.title}

    async def chips_on(page):
        return [{}, {}]

    monkeypatch.setattr(ingredients, "apply_settings", apply_settings)
    monkeypatch.setattr(ingredients, "pin_duration", pin_duration)
    monkeypatch.setattr(ingredients, "attach", attach)
    monkeypatch.setattr(ingredients, "chips_on", chips_on)
    asyncio.run(
        ingredients.generate(
            object(),
            "p-1",
            prompt=PROMPT,
            characters=[ENTITY],
            media_ids=[MEDIA],
            dry_run=True,
            out_dir=tmp_path,
        )
    )
    session = type("Session", (), {"page": _GeneratePage(log)})()
    extra = asyncio.run(captured["setup"](session))
    assert log[3:] == [
        f"settings omni-flash 9:16 {[ENTITY, MEDIA]}",
        "pin",
        f"click {ingredients.BOX}",
        f"attach {ENTITY}",
        f"attach {MEDIA}",
    ]
    assert extra == {
        "model": "omni-flash",
        "aspect": "9:16",
        "seconds": 8,
        "duration_row": "pinned",
        "chips": [
            {"kind": "entity", "id": ENTITY, "text": "Thu"},
            {"kind": "media", "id": WORKFLOW, "text": "peobj1.png"},
        ],
    }


def test_generate_setup_refuses_a_prompt_holding_more_chips_than_references(monkeypatch, tmp_path):
    log = []
    captured = _generate_world(monkeypatch, log)

    async def nothing(*args):
        return "pinned"

    async def attach(page, reference):
        return {"kind": reference.kind, "id": reference.id, "text": reference.title}

    async def chips_on(page):
        return [{}, {}]

    monkeypatch.setattr(ingredients, "apply_settings", nothing)
    monkeypatch.setattr(ingredients, "pin_duration", nothing)
    monkeypatch.setattr(ingredients, "attach", attach)
    monkeypatch.setattr(ingredients, "chips_on", chips_on)
    asyncio.run(ingredients.generate(object(), "p-1", prompt=PROMPT, characters=[ENTITY], dry_run=True))
    session = type("Session", (), {"page": _GeneratePage(log)})()
    with pytest.raises(LookupError, match="2 chips for 1 references"):
        asyncio.run(captured["setup"](session))


def test_the_option_script_reads_title_and_kind_and_the_chip_script_reads_kind_and_mention_id():
    assert ".asset-title" in ingredients._OPTIONS_JS and ".type-subtitle" in ingredients._OPTIONS_JS
    assert "data-reference-type" in ingredients._CHIPS_JS and "data-mention-id" in ingredients._CHIPS_JS
    assert re.fullmatch(r"flow-prompt-box \.mention-chip", ingredients.CHIP)
