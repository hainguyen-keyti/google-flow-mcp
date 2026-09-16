import asyncio
import json
import re
from pathlib import Path
from urllib.parse import quote_plus

import pytest
from gflow_cli.api.video import Aspect, Mode, VideoModel
from gflow_cli.errors import UiSelectorDriftError

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


def test_resolve_names_an_image_by_its_own_workflows_only():
    second = _image(media=OTHER, title="other.png")
    records = [_record(), _record(media=OTHER, workflow="w-other")]
    references = ingredients.resolve(
        [_character()], [_image(), second], records, [ENTITY], [MEDIA], "omni-flash"
    )
    assert references[1].mention_ids == {WORKFLOW}


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
        # Measured 2026-09-16: a click commits the ACTIVE option; any other option only becomes the active one.
        if self.index == self.page.active:
            self.page.commit(self.index)
        elif not self.page.active_sticks:
            self.page.active = self.index


class _Options:
    def __init__(self, page):
        self.page = page

    async def count(self):
        return len(self.page.shown())

    def nth(self, index):
        return _Option(self.page, index)


class _Confirm:
    def __init__(self, page, index):
        self.page = page
        self.index = index

    async def count(self):
        return self.page.confirms if self.page.shown() else 0

    def nth(self, index):
        return _Confirm(self.page, index)

    async def is_visible(self):
        return True

    async def click(self, timeout=None):
        self.page.clicked.append("Add to prompt")
        self.page.commit(self.page.active)


class _MentionPage:
    """The @ picker as measured on 2026-09-16: options only once '@' is typed, no id on them; a click commits the
    active option (the first one to begin with) and only activates any other, which 'Add to prompt' then commits."""

    def __init__(
        self, options, *, shows_after_ms=0, chips=(), confirms=1, active_sticks=False, entity_after_ms=0
    ):
        self.options = options
        self.shows_after_ms = shows_after_ms
        self.chips = [dict(chip) for chip in chips]
        self.confirms = confirms
        self.active_sticks = active_sticks
        self.entity_after_ms = entity_after_ms
        self.active = 0
        self.closed = False
        self.clicked = []
        self.clock = 0
        self.keyboard = _Keyboard()

    def shown(self):
        if self.closed or "@" not in self.keyboard.typed or self.clock < self.shows_after_ms:
            return []
        return self.options

    def commit(self, index):
        chip = self.options[index].get("chip")
        if chip is not None:
            self.chips.append({**chip, "committed_at": self.clock})
        self.closed = True

    def chip_view(self, chip):
        # L2 run 3 (2026-09-16): a character chip lands with its data-mention-id and fills data-entity-id later.
        view = {key: value for key, value in chip.items() if key != "committed_at"}
        if "committed_at" in chip and self.clock < chip["committed_at"] + self.entity_after_ms:
            view["entity"] = ""
        return view

    async def wait_for_timeout(self, ms):
        self.clock += ms

    def locator(self, selector):
        if selector == ingredients.OPTION:
            return _Options(self)
        if selector == ingredients.CONFIRM:
            return _Confirm(self, 0)
        raise AssertionError(f"unexpected locator {selector}")

    async def evaluate(self, script, arg=None):
        if script == ingredients._OPTIONS_JS:
            return [
                {
                    "title": option["title"],
                    "kind": option["kind"],
                    "visible": option.get("visible", True),
                    "active": index == self.active,
                }
                for index, option in enumerate(self.shown())
            ]
        if script == ingredients._CHIPS_JS:
            return [self.chip_view(chip) for chip in self.chips]
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
    # L2 run 2 (2026-09-16): the character sat second, behind an image named after it, so the click only made it the
    # active option and 'Add to prompt' committed it.
    assert page.clicked == [("Thu", "Character"), "Add to prompt"]
    assert page.keyboard.typed == "@Thu"
    assert "Enter" not in page.keyboard.pressed


def test_attach_counts_only_the_options_it_can_see():
    hidden = {**_option("Thu", "Character", _chip("entity", ENTITY, ENTITY)), "visible": False}
    page = _MentionPage([hidden, _option("Thu", "Character", _chip("entity", ENTITY, ENTITY))])
    assert asyncio.run(ingredients.attach(page, THU))["id"] == ENTITY
    assert page.clicked == [("Thu", "Character"), "Add to prompt"]


def test_attach_commits_the_active_first_option_with_its_click_alone():
    page = _MentionPage([_option("Thu", "Character", _chip("entity", ENTITY, ENTITY))])
    assert asyncio.run(ingredients.attach(page, THU))["id"] == ENTITY
    assert page.clicked == [("Thu", "Character")]


def test_attach_never_presses_add_to_prompt_while_another_option_is_active():
    page = _MentionPage(
        [
            _option("Thu", "Image", _chip("media", "w-namesake", text="Thu")),
            _option("Thu", "Character", _chip("entity", ENTITY, ENTITY)),
        ],
        active_sticks=True,
    )
    with pytest.raises(LookupError, match="active"):
        asyncio.run(ingredients.attach(page, THU))
    assert "Add to prompt" not in page.clicked
    assert page.chips == []


@pytest.mark.parametrize("confirms", [0, 2])
def test_attach_refuses_to_guess_between_add_to_prompt_buttons(confirms):
    page = _MentionPage(
        [
            _option("Thu", "Image", _chip("media", "w-namesake", text="Thu")),
            _option("Thu", "Character", _chip("entity", ENTITY, ENTITY)),
        ],
        confirms=confirms,
    )
    with pytest.raises(LookupError, match=f"{confirms} visible 'Add to prompt' buttons"):
        asyncio.run(ingredients.attach(page, THU))
    assert page.chips == []


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


def test_attach_refuses_an_entity_chip_for_an_image_even_when_it_names_the_image_workflow():
    page = _MentionPage([_option("peobj1.png", "Image", _chip("entity", WORKFLOW, text="peobj1.png"))])
    with pytest.raises(LookupError, match="refusing"):
        asyncio.run(ingredients.attach(page, IMAGE))


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


def test_attach_waits_for_a_character_chip_to_carry_its_entity_id_before_judging_it():
    page = _MentionPage([_option("Thu", "Character", _chip("entity", ENTITY, ENTITY))], entity_after_ms=3_000)
    assert asyncio.run(ingredients.attach(page, THU)) == {"kind": "entity", "id": ENTITY, "text": "Thu"}


def test_attach_refuses_a_character_chip_that_never_carries_its_entity_id():
    page = _MentionPage([_option("Thu", "Character", _chip("entity", ENTITY, ENTITY))], entity_after_ms=10**9)
    with pytest.raises(LookupError, match="refusing"):
        asyncio.run(ingredients.attach(page, THU))
    assert page.clock <= ingredients.OPTION_WAIT_MS + 3 * ingredients.CHIP_WAIT_MS


def test_attach_refuses_a_click_that_adds_no_chip():
    page = _MentionPage([_option("Thu", "Character", None)])
    with pytest.raises(LookupError, match="added no chip and closed the picker; refusing"):
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


def test_body_check_judges_the_first_submit_and_ignores_a_later_one():
    check = ingredients.SubmitBodyCheck([THU, IMAGE])
    check.on_request(_request("MZZa6b", ENTITY, WORKFLOW))
    check.on_request(_request("MZZa6b", ENTITY))
    assert check.report()["ok"] is True and check.report()["missing"] == []


def test_body_check_ignores_requests_that_are_not_a_submit():
    check = ingredients.SubmitBodyCheck([THU])
    check.on_request(_request("WuwhI", ENTITY))
    check.on_request(_request("nzlxg"))
    assert check.report() == {"rpcid": None, "model_keys": [], "missing": [ENTITY], "ok": False}


# the settings pass


class _Composer:
    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0

    async def apply_video_settings(self, page, request):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)


def _drift(detail):
    return UiSelectorDriftError(detail=detail)


EMPTY_PANE = "migrated host: the settings pane opened but rendered no option groups ([role='radiogroup'])"


def test_apply_settings_asks_for_ingredients_by_its_characters_and_leaves_the_length_to_pin_duration(
    monkeypatch,
):
    # Measured 2026-09-16 (L2 run 2): with an r2v request gflow pins 8 s itself, and for veo-lite it counted the Omni
    # 8s radio just before switching the model removed that row, then raised. A t2v request carrying the characters
    # still selects Ingredients (migrated_composer.py:947) and leaves the length to pin_duration.
    seen = []

    class Recording:
        async def apply_video_settings(self, page, request):
            seen.append(request)

    monkeypatch.setattr(ingredients.mc, "MigratedComposer", Recording)
    asyncio.run(ingredients.apply_settings(object(), "veo-lite", "16:9", [THU, IMAGE]))
    request = seen[0]
    assert request.mode is Mode.T2V
    assert request.reference_entities == (ENTITY,) and request.reference_entity_names == ("Thu",)
    assert request.duration is None
    assert request.model is VideoModel.VEO_3_1_LITE and request.aspect is Aspect.LANDSCAPE


def test_apply_settings_opens_the_pane_a_second_time_when_the_first_open_showed_no_option_groups(monkeypatch):
    # Measured 2026-09-16 (L2 run 1, then a $0 diagnosis): right after the composer's own settings pass, gflow's first
    # open of the pane showed no option groups while a second open worked. Opening the pane spends nothing.
    fake = _Composer([_drift(EMPTY_PANE)])
    monkeypatch.setattr(ingredients.mc, "MigratedComposer", lambda: fake)
    asyncio.run(ingredients.apply_settings(object(), "omni-flash", "9:16", [THU]))
    assert fake.calls == 2


def test_apply_settings_retries_that_one_failure_only_once(monkeypatch):
    fake = _Composer([_drift(EMPTY_PANE), _drift(EMPTY_PANE)])
    monkeypatch.setattr(ingredients.mc, "MigratedComposer", lambda: fake)
    with pytest.raises(UiSelectorDriftError, match="no option groups"):
        asyncio.run(ingredients.apply_settings(object(), "omni-flash", "9:16", [THU]))
    assert fake.calls == 2


def test_apply_settings_never_retries_another_settings_failure(monkeypatch):
    fake = _Composer([_drift("migrated host: no 'aspect' radio offering 'crop_9_16' in the settings pane")])
    monkeypatch.setattr(ingredients.mc, "MigratedComposer", lambda: fake)
    with pytest.raises(UiSelectorDriftError, match="aspect"):
        asyncio.run(ingredients.apply_settings(object(), "omni-flash", "9:16", [THU]))
    assert fake.calls == 1


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


# composer._type_prompt


class _TypedBox:
    def __init__(self, page):
        self.page = page

    async def click(self, timeout=None):
        self.page.log.append("box click")

    async def inner_text(self):
        return self.page.text


class _TypingPage:
    def __init__(self, lands=True):
        self.log = []
        self.text = ""
        self.lands = lands
        self.keyboard = self

    async def insert_text(self, text):
        self.log.append("insert")
        if self.lands:
            self.text += text

    async def wait_for_timeout(self, ms):
        return None


def test_type_prompt_without_a_click_inserts_once_where_the_caret_already_is():
    page = _TypingPage()
    assert asyncio.run(composer._type_prompt(page, _TypedBox(page), PROMPT, click=False)) is True
    assert page.log == ["insert"]


def test_type_prompt_without_a_click_never_inserts_a_second_copy_when_the_first_did_not_land():
    page = _TypingPage(lands=False)
    assert asyncio.run(composer._type_prompt(page, _TypedBox(page), PROMPT, click=False)) is False
    assert page.log == ["insert"]


def test_type_prompt_by_default_clicks_the_box_before_each_attempt():
    page = _TypingPage(lands=False)
    assert asyncio.run(composer._type_prompt(page, _TypedBox(page), PROMPT)) is False
    assert page.log == ["box click", "insert"] * 3


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
        self.mentions = 0
        self.shots = []
        self.screenshot_error = None

    async def wait_for_timeout(self, ms):
        return None

    def locator(self, selector):
        return type("Box", (), {"first": object()})()

    def get_by_role(self, role, name=None):
        assert role == "button" and name.search("Start generation")
        return _Start(self.log)

    async def screenshot(self, path=None):
        self.shots.append(path)
        if self.screenshot_error is not None:
            raise self.screenshot_error

    def on(self, event, listener):
        assert event == "request"
        self.listeners.append(listener)
        self.log.append("listen")

    def remove_listener(self, event, listener):
        self.listeners.remove(listener)
        self.log.append("unlisten")

    async def evaluate(self, script, arg=None):
        assert script == composer._LEFT_JS
        self.log.append("left read")
        return {"mentions": self.mentions, "text": ""}


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


def _install(
    monkeypatch, tmp_path, log, *, prices=(12, 12), fresh=(), before=None, poll_error=None, fetch_error=None
):
    seen_before = [_video("w-before", "old")] if before is None else before
    listings = iter([seen_before, *([[*seen_before, *fresh]] * 20)])
    balances = iter([200, 188, 188])
    pre, confirm = prices

    async def snapshot(session, project_id, attempts=4):
        log.append("snapshot")
        rows = next(listings)
        if poll_error is not None and "click" in log:
            raise poll_error
        return rows, set()

    async def credits(session):
        log.append("credits")
        return {"balance": next(balances)}

    async def set_mode(session, project_id, enabled):
        log.append(f"agent {enabled}")
        return {"was": False}

    async def clear_prompt(session):
        log.append("clear")
        session.page.mentions = 0
        return {"chips": 0, "thumbs": 0, "text": 0}

    async def configure(session, *, mode="Ingredients", aspect="9:16", count="x1", label="settings"):
        log.append(f"configure {label}")
        return {"applied": {}, "price": pre if label == "pre" else confirm, "settings_text": ""}

    async def type_prompt(page, box, prompt, attempts=3, *, click=True):
        log.append(f"type click={click}")
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
        log.append(f"fetch {record['workflow_id']} into {stem.name}")
        if fetch_error is not None:
            raise fetch_error
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


def _setup(log, result=None, mentions=0):
    async def setup(session):
        log.append("setup")
        session.page.mentions = mentions
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
        "type click=True",
        "configure confirm",
        "ledger at click ['submitted']",
        "click",
    ]
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert [row["status"] for row in rows] == ["submitted", "done"]
    assert rows[0]["credits_before"] == 200
    assert (rows[1]["credits_before"], rows[1]["credits_after"], rows[1]["spent"]) == (200, 188, 12)
    assert result["spent"] == 12 and result["media_id"] == "m-w-new"


def test_submit_refuses_a_job_id_that_is_already_done_before_reading_anything(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log)
    gen.Ledger(tmp_path / "ledger.jsonl").append("job-1", "done", kind="character", spent=12)
    with pytest.raises(gen.AlreadySubmitted):
        _submit(_SubmitSession(log), tmp_path, log)
    assert log == []


def test_submit_dry_run_never_clicks_never_reads_the_balance_and_writes_no_ledger(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log)
    setup = _setup(log, {"chips": [{"kind": "entity", "id": ENTITY, "text": "Thu"}]}, mentions=1)
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
    # The emptiness it reports is read after it emptied the composer, never before.
    assert log[-2:] == ["clear", "left read"]
    assert not (tmp_path / "ledger.jsonl").exists()


def test_submit_dry_run_reports_a_price_that_differs_instead_of_raising(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, prices=(12, 15))
    result = _submit(_SubmitSession(log), tmp_path, log, job_id=None, dry_run=True)
    assert result["quoted_credits"] == 15 and result["price_ok"] is False


@pytest.mark.parametrize("live", [15, 6])
def test_submit_refuses_a_price_above_or_below_the_table_with_no_row_and_an_empty_composer(
    monkeypatch, tmp_path, live
):
    log = []
    _install(monkeypatch, tmp_path, log, prices=(12, live))
    session = _SubmitSession(log)
    with pytest.raises(RuntimeError, match=f"composer says {live}"):
        _submit(session, tmp_path, log, setup=_setup(log, mentions=2))
    assert "click" not in log
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows() == []
    # Review F4 (2026-09-16): a refused run must not leave its chips in the project's shared composer.
    assert log[-1] == "clear" and session.page.mentions == 0


def test_submit_prices_the_run_on_the_read_taken_after_the_prompt_was_built(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, prices=(15, 12), fresh=[_video("w-new", f"Thu {PROMPT}")])
    assert _submit(_SubmitSession(log), tmp_path, log)["spent"] == 12
    log.clear()
    second = tmp_path / "second"
    _install(monkeypatch, second, log, prices=(12, 15))
    with pytest.raises(RuntimeError, match="composer says 15"):
        _submit(_SubmitSession(log), second, log)


def test_submit_records_what_verify_reads_right_before_the_intent_row(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", f"Thu {PROMPT}")])
    at_setup = [{"kind": "entity", "id": ENTITY, "text": "as setup saw it"}]
    at_click = [{"kind": "entity", "id": ENTITY, "text": "Thu"}]

    async def verify(session):
        log.append("verify")
        return {"chips": at_click, "prompt_text": f"Thu {PROMPT}"}

    result = _submit(
        _SubmitSession(log), tmp_path, log, setup=_setup(log, {"chips": at_setup}), verify=verify
    )
    assert log.index("configure confirm") < log.index("verify") < log.index("ledger at click ['submitted']")
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert rows[0]["chips"] == at_click and rows[0]["prompt_text"] == f"Thu {PROMPT}"
    assert result["chips"] == at_click


def test_submit_refuses_a_prompt_that_changed_before_the_click_with_no_row_and_an_empty_composer(
    monkeypatch, tmp_path
):
    # Review F1 (2026-09-16): the chips were judged at setup, then typing and the confirm pass still ran.
    log = []
    _install(monkeypatch, tmp_path, log)
    session = _SubmitSession(log)

    async def verify(active):
        raise LookupError("right before the click the prompt holds [('media', 'w')]; refusing to spend")

    with pytest.raises(LookupError, match="refusing to spend"):
        _submit(session, tmp_path, log, setup=_setup(log, mentions=2), verify=verify)
    assert "click" not in log
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows() == []
    assert log[-1] == "clear" and session.page.mentions == 0


def test_submit_can_type_the_prompt_without_clicking_the_box(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", PROMPT)])
    _submit(_SubmitSession(log), tmp_path, log, click_box=False, strict_output=True)
    assert "type click=False" in log and "type click=True" not in log


def test_submit_keeps_the_story_defaults_when_no_new_option_is_given(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", "a prompt nobody typed")])
    result = _submit(_SubmitSession(log), tmp_path, log)
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert [row["status"] for row in rows] == ["submitted", "done"]
    assert any(entry.startswith("fetch w-new") for entry in log), "story still takes the newest video"
    assert "listen" not in log and "type click=True" in log
    story_keys = set(rows[0]) | set(rows[1]) | set(result)
    assert not {"chips", "body_check", "candidates", "prompt_text", "error"} & story_keys


def test_submit_strict_output_takes_no_record_that_lacks_the_prompt(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", "a prompt nobody typed")])
    with pytest.raises(RuntimeError, match="nothing was generated"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert not any(entry.startswith("fetch") for entry in log)
    assert [row["status"] for row in gen.Ledger(tmp_path / "ledger.jsonl").rows()] == ["submitted", "failed"]


def test_submit_strict_output_never_picks_between_two_records_carrying_the_prompt(monkeypatch, tmp_path):
    log = []
    fresh = [_video("w-one", PROMPT), _video("w-two", PROMPT)]
    _install(monkeypatch, tmp_path, log, fresh=fresh)
    with pytest.raises(RuntimeError, match="2 new records"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert not any(entry.startswith("fetch") for entry in log)
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert rows[-1]["status"] == "unknown"
    assert rows[-1]["candidates"] == ["m-w-one", "m-w-two"]


def test_submit_strict_output_stops_waiting_once_two_records_carry_the_prompt(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-one", PROMPT), _video("w-two", PROMPT)])
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(composer.asyncio, "sleep", sleep)
    with pytest.raises(RuntimeError, match="2 new records"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True, wait=600.0)
    assert slept == []


def test_submit_strict_output_lists_candidates_only_on_a_job_it_could_not_settle(monkeypatch, tmp_path):
    log = []
    near = _video("w-other", "stands in a sunny bakery and smiles at the door")
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-mine", PROMPT), near])
    assert _submit(_SubmitSession(log), tmp_path, log, strict_output=True)["media_id"] == "m-w-mine"
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert last["status"] == "done" and "candidates" not in last


def test_submit_strict_output_takes_the_one_record_carrying_the_prompt(monkeypatch, tmp_path):
    log = []
    fresh = [_video("w-other", "someone else's prompt"), _video("w-mine", PROMPT)]
    _install(monkeypatch, tmp_path, log, fresh=fresh)
    result = _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert result["media_id"] == "m-w-mine"


STYLE = "Cinematic vertical shot, soft daylight, "


def test_submit_strict_output_never_takes_a_record_sharing_only_the_prompts_first_40_characters(
    monkeypatch, tmp_path
):
    # Review F3 (2026-09-16): scenes of one ad often open with the same style line, exactly 40 characters here.
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-scene-3", STYLE + "Thu opens the bakery door")])
    with pytest.raises(RuntimeError, match="could be this job's clip"):
        _submit(
            _SubmitSession(log), tmp_path, log, prompt=STYLE + "Thu tastes a croissant", strict_output=True
        )
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert rows[-1]["status"] == "unknown" and rows[-1]["candidates"] == ["m-w-scene-3"]
    assert not any(entry.startswith("fetch") for entry in log)


def _reads(text):
    async def verify(session):
        return {"prompt_text": text}

    return verify


def test_submit_strict_output_never_takes_a_record_whose_prompt_only_contains_the_prompt(
    monkeypatch, tmp_path
):
    # Review F3 (2026-09-16): a short prompt sits inside another job's prompt.
    log = []
    _install(
        monkeypatch, tmp_path, log, fresh=[_video("w-earlier-job", "Thu smiles at the camera by the door")]
    )
    with pytest.raises(RuntimeError, match="could be this job's clip"):
        _submit(
            _SubmitSession(log),
            tmp_path,
            log,
            prompt="smiles",
            verify=_reads("Thu smiles"),
            strict_output=True,
        )
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert rows[-1]["status"] == "unknown" and rows[-1]["candidates"] == ["m-w-earlier-job"]
    assert not any(entry.startswith("fetch") for entry in log)


def test_submit_strict_output_never_takes_another_characters_clip_with_the_same_words(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-lan", "Lan smiles")])
    with pytest.raises(RuntimeError, match="could be this job's clip"):
        _submit(
            _SubmitSession(log),
            tmp_path,
            log,
            prompt="smiles",
            verify=_reads("Thu smiles"),
            strict_output=True,
        )
    assert not any(entry.startswith("fetch") for entry in log)


def test_submit_strict_output_takes_the_record_whose_prompt_is_the_box_text_as_flow_stores_it(
    monkeypatch, tmp_path
):
    # Measured in out/records_now.json: a chip's title, then the typed text, joined with two spaces.
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-mine", f"Thu  peobj1.png  {PROMPT}")])
    result = _submit(
        _SubmitSession(log), tmp_path, log, verify=_reads(f"Thu peobj1.png {PROMPT}"), strict_output=True
    )
    assert result["media_id"] == "m-w-mine"


def test_submit_strict_output_never_takes_a_record_that_existed_before_the_click(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, before=[_video("w-old", PROMPT)])
    with pytest.raises(RuntimeError, match="nothing was generated"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert not any(entry.startswith("fetch") for entry in log)


def test_submit_strict_output_never_takes_an_image_record(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[{**_video("w-image", PROMPT), "kind": "image"}])
    with pytest.raises(RuntimeError, match="nothing was generated"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert not any(entry.startswith("fetch") for entry in log)


@pytest.mark.parametrize("job_id", ["../escape", "scene-1", "job-" + "x" * 600])
def test_submit_names_no_file_after_the_job_id(monkeypatch, tmp_path, job_id):
    # Review F2 (2026-09-16): a job_id naming an existing file, or too long for the disk, crashed after the click.
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", PROMPT)])
    session = _SubmitSession(log)
    assert _submit(session, tmp_path, log, job_id=job_id, strict_output=True)["media_id"] == "m-w-new"
    names = [Path(shot).name for shot in session.page.shots]
    names += [entry.split(" into ")[1] for entry in log if entry.startswith("fetch")]
    assert len(names) == 2 and all(job_id not in name for name in names), names
    assert all(Path(shot).parent == tmp_path for shot in session.page.shots)


def test_submit_a_failure_after_the_click_writes_an_outcome_row_and_says_credits_may_be_spent(
    monkeypatch, tmp_path
):
    log = []
    _install(monkeypatch, tmp_path, log, poll_error=LookupError("rpc Zzl0ze not observed; saw []"))
    with pytest.raises(RuntimeError, match="credits may already be spent") as raised:
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert "Zzl0ze" in str(raised.value)
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert [row["status"] for row in rows] == ["submitted", "unknown"]
    assert "LookupError" in rows[-1]["error"] and rows[-1]["spent"] == 12
    assert log.count("click") == 1


def test_submit_a_screenshot_that_fails_after_the_click_changes_nothing(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", PROMPT)])
    session = _SubmitSession(log)
    session.page.screenshot_error = OSError(63, "File name too long")
    assert _submit(session, tmp_path, log, strict_output=True)["media_id"] == "m-w-new"
    assert [row["status"] for row in gen.Ledger(tmp_path / "ledger.jsonl").rows()] == ["submitted", "done"]


def test_submit_a_finished_clip_that_cannot_be_downloaded_is_pending_not_failed(monkeypatch, tmp_path):
    log = []
    _install(
        monkeypatch,
        tmp_path,
        log,
        fresh=[_video("w-new", PROMPT)],
        fetch_error=FileExistsError("out/m-w-new.mp4"),
    )
    with pytest.raises(RuntimeError, match="do not run this job again"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert (
        last["status"] == "pending" and last["media_id"] == "m-w-new" and "FileExistsError" in last["error"]
    )


def test_submit_a_clip_still_rendering_at_the_deadline_is_pending_not_nothing_generated(
    monkeypatch, tmp_path
):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-mine", PROMPT, done=False)])
    with pytest.raises(RuntimeError, match="still rendering") as raised:
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert "nothing was generated" not in str(raised.value)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert (last["status"], last["media_id"], last["spent"]) == ("pending", "m-w-mine", 12)


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
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", PROMPT)])
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
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", PROMPT)])
    extra = {"model": "omni-flash", "chips": [{"kind": "entity", "id": ENTITY, "text": "Thu"}]}
    result = _submit(_SubmitSession(log), tmp_path, log, setup=_setup(log, extra), strict_output=True)
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert rows[0]["chips"] == extra["chips"] and rows[0]["model"] == "omni-flash"
    assert result["chips"] == extra["chips"]


# generate


class _GeneratePage:
    def __init__(self, log, *, box_text="", caret_lands=True):
        self.log = log
        self.box_text = box_text
        self.caret_lands = caret_lands

    def locator(self, selector):
        log = self.log

        class _First:
            async def click(self, timeout=None):
                log.append(f"click {selector}")

        return type("Found", (), {"first": _First()})()

    async def evaluate(self, script, arg=None):
        assert arg == ingredients.BOX
        if script == ingredients._CARET_END_JS:
            self.log.append("caret end")
            return self.caret_lands
        if script == ingredients._BOX_TEXT_JS:
            return self.box_text
        raise AssertionError(f"unexpected script {script[:40]!r}")


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
    # Review F1 (2026-09-16): no click on a box that holds chips, and the chips are read again at the click.
    assert captured["click_box"] is False and callable(captured["verify"])
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


def test_a_failed_agent_mode_restore_never_hides_why_the_run_failed(monkeypatch):
    # Review F2 (2026-09-16): a TimeoutError from the restore replaced "spent 12 credits".
    log = []
    _generate_world(monkeypatch, log, was=True)

    async def failing(session, project_id, **kwargs):
        raise RuntimeError("job-1: nothing was generated within 360s, spent 12 credits")

    async def set_mode(session, project_id, enabled):
        log.append(f"agent {enabled}")
        if enabled:
            raise TimeoutError("waiting for the agent chip")
        return {"was": True}

    monkeypatch.setattr(ingredients.composer, "_submit", failing)
    monkeypatch.setattr(ingredients.agent, "set_mode", set_mode)
    with pytest.raises(RuntimeError, match="spent 12 credits"):
        asyncio.run(ingredients.generate(object(), "p-1", prompt=PROMPT, characters=[ENTITY], job_id="job-1"))
    assert log == ["listing", "agent False", "agent True"]


def test_a_failed_agent_mode_restore_after_a_finished_run_is_reported_not_raised(monkeypatch):
    log = []
    _generate_world(monkeypatch, log, was=True)

    async def set_mode(session, project_id, enabled):
        if enabled:
            raise TimeoutError("waiting for the agent chip")
        return {"was": True}

    monkeypatch.setattr(ingredients.agent, "set_mode", set_mode)
    result = asyncio.run(
        ingredients.generate(object(), "p-1", prompt=PROMPT, characters=[ENTITY], job_id="job-1")
    )
    assert result["ok"] is True and result["agent_mode_restored"] == "no: TimeoutError"


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
        "caret end",
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


def _prepared(monkeypatch, log, on_page, *, box_text=f"Thu peobj1.png {PROMPT}", caret_lands=True):
    """Run generate as a dry run against a stubbed _submit, then return its setup, verify and a page to run them on."""
    captured = _generate_world(monkeypatch, log)

    async def nothing(*args):
        return "pinned"

    async def attach(page, reference):
        return {"kind": reference.kind, "id": min(reference.mention_ids), "text": reference.title}

    async def chips_on(page):
        return [dict(chip) for chip in on_page]

    monkeypatch.setattr(ingredients, "apply_settings", nothing)
    monkeypatch.setattr(ingredients, "pin_duration", nothing)
    monkeypatch.setattr(ingredients, "attach", attach)
    monkeypatch.setattr(ingredients, "chips_on", chips_on)
    asyncio.run(
        ingredients.generate(
            object(), "p-1", prompt=PROMPT, characters=[ENTITY], media_ids=[MEDIA], dry_run=True
        )
    )
    page = _GeneratePage(log, box_text=box_text, caret_lands=caret_lands)
    return captured["setup"], captured["verify"], type("Session", (), {"page": page})()


ON_PAGE = [
    {"kind": "entity", "id": ENTITY, "entity": ENTITY, "text": "Thu"},
    {"kind": "media", "id": WORKFLOW, "entity": "", "text": "peobj1.png"},
]


def test_generate_setup_refuses_a_prompt_holding_fewer_chips_than_references(monkeypatch):
    setup, _, session = _prepared(monkeypatch, [], ON_PAGE[:1])
    with pytest.raises(LookupError, match="1 chips for 2 references"):
        asyncio.run(setup(session))


def test_generate_setup_refuses_a_box_it_cannot_put_the_caret_at_the_end_of(monkeypatch):
    setup, _, session = _prepared(monkeypatch, [], ON_PAGE, caret_lands=False)
    with pytest.raises(LookupError, match="caret"):
        asyncio.run(setup(session))


def test_generate_verify_passes_the_chips_setup_attached_and_reports_the_prompt_it_will_send(monkeypatch):
    setup, verify, session = _prepared(monkeypatch, [], ON_PAGE)
    asyncio.run(setup(session))
    assert asyncio.run(verify(session)) == {
        "chips": [
            {"kind": "entity", "id": ENTITY, "text": "Thu"},
            {"kind": "media", "id": WORKFLOW, "text": "peobj1.png"},
        ],
        "prompt_text": f"Thu peobj1.png {PROMPT}",
    }


@pytest.mark.parametrize(
    ("at_click", "box_text"),
    [
        (ON_PAGE[1:], f"peobj1.png {PROMPT}"),
        ([{**ON_PAGE[0], "id": OTHER, "entity": OTHER}, ON_PAGE[1]], f"Thu peobj1.png {PROMPT}"),
        ([{**ON_PAGE[0], "entity": ""}, ON_PAGE[1]], f"Thu peobj1.png {PROMPT}"),
        ([{**ON_PAGE[0], "entity": OTHER}, ON_PAGE[1]], f"Thu peobj1.png {PROMPT}"),
        ([*ON_PAGE, {"kind": "media", "id": "w-stray", "entity": "", "text": "x.png"}], f"Thu {PROMPT}"),
        (ON_PAGE, "Thu peobj1.png stands in a sunny"),
    ],
    ids=[
        "a chip went missing",
        "another character",
        "entity id not bound",
        "entity id disagrees",
        "a stray chip",
        "prompt cut short",
    ],
)
def test_generate_verify_refuses_a_prompt_that_changed_after_setup(monkeypatch, at_click, box_text):
    setup, verify, session = _prepared(monkeypatch, [], ON_PAGE)
    asyncio.run(setup(session))
    monkeypatch.setattr(ingredients, "chips_on", lambda page: _async([dict(chip) for chip in at_click]))
    session.page.box_text = box_text
    with pytest.raises(LookupError, match="refusing to spend"):
        asyncio.run(verify(session))


async def _async(value):
    return value


def test_the_option_script_reads_title_and_kind_and_the_chip_script_reads_kind_and_mention_id():
    assert ".asset-title" in ingredients._OPTIONS_JS and ".type-subtitle" in ingredients._OPTIONS_JS
    assert "data-reference-type" in ingredients._CHIPS_JS and "data-mention-id" in ingredients._CHIPS_JS
    assert re.fullmatch(r"flow-prompt-box \.mention-chip", ingredients.CHIP)
