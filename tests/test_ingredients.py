import asyncio
import contextlib
import json
import re
from pathlib import Path
from urllib.parse import quote_plus

import pytest
from gflow_cli.api.video import Aspect, Mode, VideoModel
from gflow_cli.errors import UiSelectorDriftError
from mcp.server.mcpserver.exceptions import ToolError
from playwright.async_api import Error as PlaywrightError

from video import gen, mcp_server
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
        self.url = "https://flow.google.com/project/p-1"
        self.listeners = []
        self.responders = []
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
        assert event in ("request", "response")
        if event == "response":
            self.responders.append(listener)
            return
        self.listeners.append(listener)
        self.log.append("listen")

    def remove_listener(self, event, listener):
        if event == "response":
            self.responders.remove(listener)
            return
        self.listeners.remove(listener)
        self.log.append("unlisten")

    def answer(self, responses):
        for response in responses:
            for responder in list(self.responders):
                responder(response)

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
    monkeypatch,
    tmp_path,
    log,
    *,
    prices=(12, 12),
    fresh=(),
    before=None,
    poll_error=None,
    fetch_error=None,
    balance_reads=(200, 188, 188),
    replies=None,
):
    seen_before = [_video("w-before", "old")] if before is None else before
    listings = iter([seen_before, *([[*seen_before, *fresh]] * 20)])
    balances = iter(balance_reads)
    pre, confirm = prices
    replies = replies or {}

    async def snapshot(session, project_id, attempts=4):
        log.append("snapshot")
        rows = next(listings)
        if "click" in log:
            session.page.answer(replies.get("poll", ()))
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
        session.page.answer(replies.get("click", ()))
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


def test_submit_dry_run_returns_what_verify_read(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log)
    read = {"chips": [{"kind": "entity", "id": ENTITY, "text": "Thu"}], "prompt_text": f"Thu {PROMPT}"}

    async def verify(session):
        return read

    result = _submit(_SubmitSession(log), tmp_path, log, job_id=None, dry_run=True, verify=verify)
    assert result["chips"] == read["chips"] and result["prompt_text"] == read["prompt_text"]


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


@pytest.mark.parametrize(
    "stored",
    ["a prompt nobody typed", "", "Thu  stands in a sunny bakery, smiling at the camera"],
    ids=["another prompt", "prompt field empty", "typed text altered"],
)
def test_submit_strict_output_leaves_a_paid_job_unknown_when_it_cannot_claim_the_new_video(
    monkeypatch, tmp_path, stored
):
    # Re-review B (2026-09-17): "nothing was generated" beside a new video and a moved balance invites paying twice.
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", stored)])
    with pytest.raises(RuntimeError, match="could be this job's clip") as raised:
        _submit(_SubmitSession(log), tmp_path, log, verify=_reads(f"Thu {PROMPT}"), strict_output=True)
    assert "nothing was generated" not in str(raised.value)
    assert "no submit reply from Flow was heard" in str(raised.value)
    assert not any(entry.startswith("fetch") for entry in log)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert (last["status"], last["candidates"], last["spent"]) == ("unknown", ["m-w-new"], 12)


def test_submit_strict_output_leaves_a_job_unknown_when_a_new_video_shows_up_with_the_balance_unmoved(
    monkeypatch, tmp_path
):
    log = []
    fresh = [_video("w-new", "a prompt nobody typed")]
    _install(monkeypatch, tmp_path, log, fresh=fresh, balance_reads=(200, 200, 200))
    with pytest.raises(RuntimeError, match="could be this job's clip"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert (last["status"], last["candidates"], last["spent"]) == ("unknown", ["m-w-new"], 0)


def test_submit_keeps_the_story_failure_when_nothing_is_picked(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log)
    with pytest.raises(RuntimeError, match="nothing was generated within 0s, spent 12 credits"):
        _submit(_SubmitSession(log), tmp_path, log)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert last["status"] == "failed" and "candidates" not in last


def test_submit_strict_output_leaves_a_job_unknown_when_the_balance_moved_and_no_video_showed_up(
    monkeypatch, tmp_path
):
    log = []
    _install(monkeypatch, tmp_path, log)
    with pytest.raises(RuntimeError, match="balance moved by 12") as raised:
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert "nothing was generated" not in str(raised.value)
    assert "no submit reply from Flow was heard" in str(raised.value)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert (last["status"], last["candidates"]) == ("unknown", [])


def test_submit_strict_output_counts_a_balance_that_went_up_as_moved(monkeypatch, tmp_path):
    # The daily credits refresh on the first generation of the day (DECISIONS 2026-09-16), so a refill can land here.
    log = []
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 250, 250))
    with pytest.raises(RuntimeError, match="balance moved by -50") as raised:
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert "nothing was generated" not in str(raised.value)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert (last["status"], last["spent"]) == ("unknown", -50)


def test_a_promptless_video_never_matches_a_job_whose_box_was_not_read():
    # Measured in out/records_now.json: an upscale adds a video record whose prompt is ''.
    assert composer.matching_outputs([_video("w-upscale", "")], PROMPT) == []


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
    _install(monkeypatch, tmp_path, log, before=[_video("w-old", PROMPT)], balance_reads=(200, 200, 200))
    with pytest.raises(RuntimeError, match="nothing was generated"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert not any(entry.startswith("fetch") for entry in log)


def test_submit_strict_output_never_takes_an_image_record(monkeypatch, tmp_path):
    log = []
    image = {**_video("w-image", PROMPT), "kind": "image"}
    _install(monkeypatch, tmp_path, log, fresh=[image], balance_reads=(200, 200, 200))
    with pytest.raises(RuntimeError, match="nothing was generated"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert not any(entry.startswith("fetch") for entry in log)


def test_submit_strict_output_names_the_two_exact_records_as_candidates_beside_other_new_videos(
    monkeypatch, tmp_path
):
    log = []
    fresh = [_video("w-one", PROMPT), _video("w-two", PROMPT), _video("w-near", f"{PROMPT} by the door")]
    _install(monkeypatch, tmp_path, log, fresh=fresh)
    with pytest.raises(RuntimeError, match="2 new records"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert last["status"] == "unknown" and last["candidates"] == ["m-w-one", "m-w-two"]


LONG_JOB = "job-" + "x" * 600


def _agent_sees(exc):
    """The error text an agent receives through the MCP server, which cuts every error to 500 characters."""
    server = mcp_server.TellingServer("probe")

    @server.tool(name="boom")
    async def boom() -> str:
        raise exc

    with pytest.raises(ToolError) as raised:
        asyncio.run(server.call_tool("boom", {}))
    return str(raised.value)


@pytest.mark.parametrize(
    ("world", "advice"),
    [
        (
            {"poll_error": LookupError("rpc Zzl0ze not observed; saw []")},
            ["credits may already be spent", "never run this job again under a new job_id"],
        ),
        ({"fresh": [_video("w-mine", PROMPT, done=False)]}, ["do not run this job again", "flow_download"]),
        (
            {"fresh": [_video("w-other", "a prompt nobody typed")]},
            ["could be this job's clip", "never run this job again under a new job_id"],
        ),
        ({}, ["balance moved", "never run this job again under a new job_id"]),
    ],
    ids=["failure after the click", "clip still rendering", "a new video it cannot claim", "balance moved"],
)
def test_the_advice_after_a_paid_click_reaches_an_agent_whatever_the_job_id_length(
    monkeypatch, tmp_path, world, advice
):
    # Re-review A (2026-09-17): the messages led with the job_id, so a long one pushed the advice past the cut.
    log = []
    _install(monkeypatch, tmp_path, log, **world)
    with pytest.raises(RuntimeError) as raised:
        _submit(_SubmitSession(log), tmp_path, log, job_id=LONG_JOB, strict_output=True)
    seen = _agent_sees(raised.value)
    assert all(phrase in seen for phrase in advice), seen


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


def test_submit_a_dead_browser_after_the_click_still_writes_unknown_and_guesses_no_spend(
    monkeypatch, tmp_path
):
    log = []
    _install(monkeypatch, tmp_path, log)
    closed = PlaywrightError("Target page, context or browser has been closed")

    async def snapshot(session, project_id, attempts=4):
        log.append("snapshot")
        if "click" in log:
            raise closed
        return [_video("w-before", "old")], set()

    async def credits(session):
        log.append("credits")
        if "click" in log:
            raise closed
        return {"balance": 200}

    monkeypatch.setattr(composer, "snapshot", snapshot)
    monkeypatch.setattr(composer.reader, "credits", credits)
    with pytest.raises(RuntimeError) as raised:
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert "credits may already be spent" in str(raised.value)
    assert "never run this job again under a new job_id" in str(raised.value)
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert [row["status"] for row in rows] == ["submitted", "unknown"]
    assert rows[-1]["credits_after"] is None and rows[-1]["spent"] is None
    assert log.count("click") == 1


def test_submit_a_failure_after_the_click_keeps_what_the_watch_heard(monkeypatch, tmp_path):
    # Re-review E (2026-09-17): the unknown row dropped the body check the watch had already heard.
    log = []
    _install(monkeypatch, tmp_path, log, poll_error=LookupError("rpc Zzl0ze not observed; saw []"))
    watch = _Watch()
    with pytest.raises(RuntimeError, match="credits may already be spent"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True, watch=watch)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert last["status"] == "unknown" and last["body_check"] == {"ok": True, "heard": 1}


def test_submit_a_failure_inside_the_click_window_claims_no_rpcids(monkeypatch, tmp_path):
    # Re-review E (2026-09-17): rpcids [] is how this repo reads a click that sent nothing.
    log = []
    _install(monkeypatch, tmp_path, log)

    async def await_submit(session, click, *, settle=30.0, patience=90.0):
        await click()
        raise RuntimeError("Page.wait_for_timeout: Target page, context or browser has been closed")

    monkeypatch.setattr(composer.clips, "_await_submit", await_submit)
    with pytest.raises(RuntimeError, match="credits may already be spent"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert log.count("click") == 1 and last["status"] == "unknown" and last["rpcids"] is None


@pytest.mark.parametrize(("heard", "rpcids"), [({"MZZa6b": [[]]}, ["MZZa6b"]), ({}, [])])
def test_submit_a_failure_after_the_click_window_names_the_rpcids_it_heard(
    monkeypatch, tmp_path, heard, rpcids
):
    log = []
    _install(monkeypatch, tmp_path, log, poll_error=LookupError("rpc Zzl0ze not observed; saw []"))

    async def await_submit(session, click, *, settle=30.0, patience=90.0):
        await click()
        return heard

    monkeypatch.setattr(composer.clips, "_await_submit", await_submit)
    with pytest.raises(RuntimeError, match="credits may already be spent"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert last["status"] == "unknown" and last["rpcids"] == rpcids


JOB_WORKFLOW = "5b0c9a6e-7a41-4f8e-9d1b-2c3e4f5a6b7c"
JOB_MEDIA = "8d2e4f60-1b3c-4d5e-8f70-9a1b2c3d4e5f"
OTHER_WORKFLOW = "0f1e2d3c-4b5a-4968-8776-655443322110"
FLOW_PROJECT = "118aece2-f6f3-4121-8d11-2b36037d9b36"


class _FlowReply:
    def __init__(self, rpcid, payload, *, error=None):
        self.url = f"https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids={rpcid}&rt=c"
        self.body = ")]}'\n\n" + json.dumps(
            [["wrb.fr", rpcid, json.dumps(payload), None, None, None, "generic"]]
        )
        self.error = error

    async def text(self):
        if self.error is not None:
            raise self.error
        return self.body


def _flow_record(status, *, workflow=JOB_WORKFLOW, extra=()):
    """A generation record the way gflow's batchexecute.py reads it: DETAILS[8] holds the status."""
    details = [[1789582830, 0], None, None, None, None, None, None, None, [status], None, None, *extra]
    return [workflow, FLOW_PROJECT, JOB_MEDIA, "CAE", None, details, None, [[None] * 13]]


def _status_reply(status, **record):
    return _FlowReply("jwpduf", [None, 1, [_flow_record(status, **record)]])


SUBMIT_REPLY = _FlowReply("MZZa6b", [None, 1, [[JOB_MEDIA]], [_flow_record(6)]])


def test_submit_records_what_flow_replied_about_the_job_it_submitted(monkeypatch, tmp_path):
    # Measured 2026-09-17 (plan E, L3): Flow rendered the job to 23%, then dropped it with no record, no charge and
    # nothing left on the page, so its own replies are the only place a reason can show up.
    log = []
    replies = {
        "click": [
            _status_reply(2, workflow=OTHER_WORKFLOW),
            SUBMIT_REPLY,
            _status_reply(2),
            _status_reply(2),
        ],
        "poll": [
            _status_reply(7, workflow=OTHER_WORKFLOW),
            _FlowReply("Zzl0ze", [[_flow_record(9)]]),
            _status_reply(5, extra=[["PUBLIC_ERROR_UNSAFE_FACE"]]),
        ],
    }
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies=replies)
    with pytest.raises(RuntimeError, match="again in a few minutes") as raised:
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    flow = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["flow"]
    assert (flow["workflow_id"], flow["media_id"], flow["statuses"]) == (JOB_WORKFLOW, JOB_MEDIA, [6, 2, 5])
    assert flow["unmeasured"]["status"] == 5 and flow["reasons"] == ["PUBLIC_ERROR_UNSAFE_FACE"]
    assert "status 5" in str(raised.value) and "PUBLIC_ERROR_UNSAFE_FACE" in str(raised.value)


def test_submit_leaves_a_job_flow_still_reports_running_unknown_rather_than_nothing_generated(
    monkeypatch, tmp_path
):
    # sg-bf-2 (2026-09-17): statuses 6, 2, no new video, balance unmoved; the job was written failed "nothing was
    # generated" while Flow's last word was that it was running.
    log = []
    replies = {"click": [SUBMIT_REPLY, _status_reply(2)]}
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies=replies)
    with pytest.raises(RuntimeError) as raised:
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    message = str(raised.value)
    assert message.startswith(
        "check flow_media and flow_credits again in a few minutes and never run this job again under a new job_id"
    )
    assert f"Flow last reported status 2 for workflow {JOB_WORKFLOW}" in message
    assert "nothing was generated" not in message
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert (last["status"], last["candidates"], last["spent"]) == ("unknown", [], 0)


REFUSAL = "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED"
FLOW_NOTICE = (
    "This prompt might violate our policies about generating prominent people. Please try a different prompt. "
    "You have not been charged for this generation."
)


def test_submit_calls_a_job_flow_reported_failed_a_failure(monkeypatch, tmp_path):
    # Measured 2026-09-17 (plan E, L4): statuses 6, 2, 4 with PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED, no charge.
    log = []
    filtered = _status_reply(4, extra=[[REFUSAL]])
    replies = {"click": [SUBMIT_REPLY, _status_reply(2)], "poll": [filtered]}
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies=replies)
    with pytest.raises(RuntimeError, match="Flow refused this job and charged nothing") as raised:
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert f"Flow failed workflow {JOB_WORKFLOW} (status 4) with {REFUSAL}" in str(raised.value)
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["status"] == "failed"


def test_submit_never_says_charged_nothing_when_the_balance_moved(monkeypatch, tmp_path):
    # The story path settles a moved balance as failed too, so the refusal wording must read the balance itself.
    log = []
    replies = {"click": [SUBMIT_REPLY, _status_reply(2)], "poll": [_status_reply(4, extra=[[REFUSAL]])]}
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 188, 188), replies=replies)
    with pytest.raises(RuntimeError, match="nothing was generated within 0s, spent 12 credits") as raised:
        _submit(_SubmitSession(log), tmp_path, log)
    assert "charged nothing" not in str(raised.value)


def test_submit_stops_waiting_once_flow_reports_the_job_failed(monkeypatch, tmp_path):
    # dancer-1 (2026-09-17): Flow showed a Failed tile about 30 s after the click and the agent still waited 360 s.
    log = []
    replies = {"click": [SUBMIT_REPLY, _status_reply(2)], "poll": [_status_reply(4, extra=[[REFUSAL]])]}
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies=replies)
    slept = []
    real_sleep = asyncio.sleep

    async def sleep(seconds):
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(composer.asyncio, "sleep", sleep)
    with pytest.raises(RuntimeError, match="Flow refused this job and charged nothing"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True, wait=600.0)
    assert len(slept) <= 1 and log.count("snapshot") <= 3
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["status"] == "failed"


def test_submit_leads_a_refusal_with_its_advice_and_flows_own_words_within_the_mcp_cut(monkeypatch, tmp_path):
    # dancer-1 (2026-09-17): the refusal opened with "nothing was generated" and Flow's own notice was cut at 500.
    log = []
    replies = {"click": [SUBMIT_REPLY, _status_reply(2)], "poll": [_status_reply(4, extra=[[REFUSAL]])]}
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies=replies)

    async def notice(page):
        return FLOW_NOTICE

    monkeypatch.setattr(composer, "_notice", notice)
    with pytest.raises(RuntimeError) as raised:
        _submit(_SubmitSession(log), tmp_path, log, job_id=LONG_JOB, strict_output=True)
    assert str(raised.value).startswith(
        "Flow refused this job and charged nothing: do not retry the same inputs hoping they pass, tell the owner"
    )
    seen = _agent_sees(raised.value)
    assert REFUSAL in seen and FLOW_NOTICE[:60] in seen, seen


@pytest.mark.parametrize(
    ("url", "moved"),
    [
        ("https://flow.google.com/project/p-1", False),
        ("https://flow.google.com/project/p-1/edit/m-w-before", True),
    ],
    ids=["still on the project", "moved into a clip editor"],
)
def test_submit_records_the_page_it_found_right_after_the_click(monkeypatch, tmp_path, url, moved):
    # sg-bf-2 (2026-09-17): the page was found in the editor of the job before, with nothing in the row to say so.
    log = []
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 200, 200))
    session = _SubmitSession(log)
    session.page.url = url
    with pytest.raises(RuntimeError, match="nothing was generated") as raised:
        _submit(session, tmp_path, log, strict_output=True)
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["page_after_click"] == url
    assert (f"the page had moved to {url} after the click" in str(raised.value)) is moved


MOVED_PAGE = "https://flow.google.com/project/p-1/edit/m-w-before"


@pytest.mark.parametrize(
    "world",
    [
        {"poll_error": LookupError("rpc Zzl0ze not observed; saw []")},
        {"replies": {"click": [SUBMIT_REPLY, _status_reply(2)]}, "balance_reads": (200, 200, 200)},
        {"fresh": [_video("w-other", "a prompt nobody typed")]},
        {},
    ],
    ids=[
        "a failure after the click",
        "a job Flow still runs",
        "a new video it cannot claim",
        "a moved balance",
    ],
)
def test_submit_names_a_page_that_left_the_project_on_every_outcome_it_cannot_settle(
    monkeypatch, tmp_path, world
):
    log = []
    _install(monkeypatch, tmp_path, log, **world)
    session = _SubmitSession(log)
    session.page.url = MOVED_PAGE
    with pytest.raises(RuntimeError) as raised:
        _submit(session, tmp_path, log, strict_output=True)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert (last["status"], last["page_after_click"]) == ("unknown", MOVED_PAGE)
    assert f"the page had moved to {MOVED_PAGE} after the click" in str(raised.value)


def test_submit_says_what_flow_last_reported_when_no_failure_reply_came(monkeypatch, tmp_path):
    log = []
    replies = {"click": [SUBMIT_REPLY, _status_reply(None), _status_reply(2)]}
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies=replies)
    with pytest.raises(RuntimeError, match="again in a few minutes") as raised:
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    flow = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["flow"]
    assert (flow["statuses"], flow["unmeasured"], flow["reasons"]) == ([6, None, 2], None, [])
    assert "status 2" in str(raised.value)


def test_submit_keeps_what_flow_replied_on_the_unknown_row_after_a_failure(monkeypatch, tmp_path):
    log = []
    poll_error = LookupError("rpc Zzl0ze not observed; saw []")
    _install(monkeypatch, tmp_path, log, poll_error=poll_error, replies={"click": [SUBMIT_REPLY]})
    with pytest.raises(RuntimeError, match="credits may already be spent") as raised:
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert last["status"] == "unknown" and last["flow"]["workflow_id"] == JOB_WORKFLOW
    assert f"status 6 for workflow {JOB_WORKFLOW}" in str(raised.value)


@pytest.mark.parametrize("poll_error", [None, LookupError("rpc Zzl0ze not observed; saw []")])
def test_submit_stops_hearing_flow_once_the_outcome_is_written(monkeypatch, tmp_path, poll_error):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", PROMPT)], poll_error=poll_error)
    session = _SubmitSession(log)
    with contextlib.suppress(RuntimeError):
        _submit(session, tmp_path, log, strict_output=True)
    assert session.page.responders == []
    flow = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["flow"]
    assert (flow["workflow_id"], flow["statuses"], flow["heard"]) == (None, [], []) and "error" not in flow


def test_submit_skips_a_reply_whose_body_cannot_be_read(monkeypatch, tmp_path):
    log = []
    broken = _status_reply(5)
    broken.error = PlaywrightError("Response body is unavailable for redirect responses")
    _install(
        monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies={"click": [SUBMIT_REPLY, broken]}
    )
    with pytest.raises(RuntimeError, match="again in a few minutes"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    flow = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["flow"]
    assert (flow["workflow_id"], flow["statuses"], flow["unmeasured"]) == (JOB_WORKFLOW, [6], None)


def test_submit_reads_every_record_of_a_status_reply_not_only_the_first(monkeypatch, tmp_path):
    log = []
    both = _FlowReply("jwpduf", [None, 1, [_flow_record(2, workflow=OTHER_WORKFLOW), _flow_record(5)]])
    _install(
        monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies={"click": [SUBMIT_REPLY, both]}
    )
    with pytest.raises(RuntimeError, match="status 5"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["flow"]["statuses"] == [6, 5]


def test_submit_hears_a_failure_reason_that_another_rpc_carries(monkeypatch, tmp_path):
    log = []
    notice = _FlowReply("Xq9Tzb", [["PUBLIC_ERROR_UNSAFE_IDENTITY", JOB_WORKFLOW]])
    _install(
        monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies={"click": [SUBMIT_REPLY, notice]}
    )
    with pytest.raises(RuntimeError, match="PUBLIC_ERROR_UNSAFE_IDENTITY") as raised:
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert "other replies naming it: Xq9Tzb" in str(raised.value)
    flow = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["flow"]
    assert flow["reasons"] == ["PUBLIC_ERROR_UNSAFE_IDENTITY"] and "Xq9Tzb" in flow["named_by"]


def test_submit_never_waits_past_its_bound_for_a_reply_body(monkeypatch, tmp_path):
    log = []

    class _Stuck(_FlowReply):
        async def text(self):
            await asyncio.Event().wait()

    monkeypatch.setattr(composer, "REPLY_READ_S", 0.05)
    stuck = _Stuck("jwpduf", [])
    _install(
        monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies={"click": [SUBMIT_REPLY, stuck]}
    )
    with pytest.raises(RuntimeError, match="again in a few minutes"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    assert gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["flow"]["statuses"] == [6]


def test_submit_writes_the_outcome_row_even_when_flows_replies_cannot_be_judged(monkeypatch, tmp_path):
    log = []

    def broken(self, texts):
        raise ValueError("a reply shape nobody measured")

    monkeypatch.setattr(composer.FlowReplies, "_judge", broken)
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies={"click": [SUBMIT_REPLY]})
    with pytest.raises(RuntimeError, match="could not be read"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    last = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]
    assert last["status"] == "failed" and "ValueError" in last["flow"]["error"]


def test_submit_keeps_no_signed_url_from_a_failed_reply(monkeypatch, tmp_path):
    log = []
    signed = "https://flow-content.google/video/x?Expires=1789608354&KeyName=labs-flow-prod-cdn-key&Signature=MOP4f"
    replies = {"click": [SUBMIT_REPLY, _status_reply(5, extra=[signed])]}
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies=replies)
    with pytest.raises(RuntimeError, match="again in a few minutes"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    head = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["flow"]["unmeasured"]["head"]
    assert JOB_WORKFLOW in head and "Signature=" not in head and "Expires=" not in head


def _judged(responses):
    """Feed replies straight to a FlowReplies and return its report."""

    async def run():
        replies = composer.FlowReplies()
        for response in responses:
            replies.on_response(response)
        return await replies.report()

    return asyncio.run(run())


def test_flow_replies_never_credit_this_job_with_a_code_another_workflow_carried():
    # Re-review G1 (2026-09-17): every code heard was spoken next to this job's workflow.
    flow = _judged(
        [
            SUBMIT_REPLY,
            _status_reply(2, workflow=OTHER_WORKFLOW, extra=[["PUBLIC_ERROR_UNSAFE_FACE"]]),
            _status_reply(2),
            _FlowReply("Zzl0ze", [["listing", "PUBLIC_ERROR_UNSAFE_CONTENT"]]),
        ]
    )
    assert (flow["reasons"], flow["reasons_elsewhere"]) == (
        [],
        ["PUBLIC_ERROR_UNSAFE_CONTENT", "PUBLIC_ERROR_UNSAFE_FACE"],
    )
    said = composer._flow_said(flow)
    assert said.startswith(f"Flow last reported status 2 for workflow {JOB_WORKFLOW};")
    assert "codes heard in other replies" in said


@pytest.mark.parametrize(
    "reply",
    [
        _FlowReply("jwpduf", [None, 1, [_flow_record(5)], [["PUBLIC_ERROR_UNSAFE_FACE"]]]),
        _status_reply(5, extra=["x" * 600, ["PUBLIC_ERROR_UNSAFE_FACE"]]),
        _FlowReply(
            "jwpduf",
            [
                None,
                1,
                [
                    _flow_record(2, workflow=OTHER_WORKFLOW),
                    _flow_record(5, extra=[["PUBLIC_ERROR_UNSAFE_FACE"]]),
                ],
            ],
        ),
    ],
    ids=[
        "beside its record in a reply about it alone",
        "past the 500 characters the head keeps",
        "inside its record in a reply about two workflows",
    ],
)
def test_flow_replies_credit_this_job_with_a_code_its_own_reply_carries(reply):
    flow = _judged([SUBMIT_REPLY, reply])
    assert (flow["reasons"], flow["reasons_elsewhere"]) == (["PUBLIC_ERROR_UNSAFE_FACE"], [])


def test_flow_replies_call_an_unmeasured_status_unmeasured_and_speak_of_the_last_one():
    # Re-review G2 (2026-09-17): a status gflow never saw is not proof of a failure when the job keeps running.
    flow = _judged([SUBMIT_REPLY, _status_reply(7), _status_reply(2)])
    said = composer._flow_said(flow)
    assert flow["statuses"] == [6, 7, 2] and flow["unmeasured"]["status"] == 7
    assert "last reported status 2" in said and "unmeasured status 7" in said and "failed" not in said
    last_odd = composer._flow_said(_judged([SUBMIT_REPLY, _status_reply(5)]))
    assert last_odd.startswith(f"Flow last reported unmeasured status 5 for workflow {JOB_WORKFLOW}")


def test_flow_replies_name_a_small_reply_of_another_rpc_that_names_the_job():
    notice = _FlowReply("Kp2Wfd", [[JOB_MEDIA, "generation stopped"]])
    flow = _judged([SUBMIT_REPLY, notice, _FlowReply("Zzl0ze", [["x" * 30_000, JOB_MEDIA]])])
    assert list(flow["named_by"]) == ["Kp2Wfd"] and JOB_MEDIA in flow["named_by"]["Kp2Wfd"]


def test_flow_replies_never_read_a_body_that_is_not_batchexecute():
    class _Media:
        url = "https://lh3.googleusercontent.com/abc=m22"
        read = False

        async def text(self):
            _Media.read = True
            return "x"

    flow = _judged([_Media()])
    assert _Media.read is False and flow["heard"] == []


def test_flow_replies_count_a_record_without_its_media_info():
    # gflow batchexecute.py locates a record by [uuid, uuid, uuid, "CAE", _, DETAILS]; a running job has no media yet.
    short = _flow_record(2)[:6]
    assert composer._records_in([None, 1, [short]]) == [short]


def test_flow_replies_turn_a_long_token_into_a_placeholder_in_a_head():
    token = "A" * 130
    flow = _judged([SUBMIT_REPLY, _status_reply(5, extra=[f"https://lh3.googleusercontent.com/{token}"])])
    head = flow["unmeasured"]["head"]
    assert token not in head and "<token>" in head


def test_flow_replies_cancel_a_read_still_pending_at_their_bound(monkeypatch):
    monkeypatch.setattr(composer, "REPLY_READ_S", 0.05)

    class _Stuck:
        url = "https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=jwpduf&rt=c"

        async def text(self):
            await asyncio.Event().wait()

    async def run():
        replies = composer.FlowReplies()
        replies.on_response(_Stuck())
        flow = await replies.report()
        await asyncio.sleep(0)
        return flow, [read.cancelled() for read in replies._reads]

    flow, cancelled = asyncio.run(run())
    assert flow["heard"] == ["jwpduf"] and cancelled == [True]


SIGNED_URL = "https://flow-content.google/video/p?Expires=1789608354&KeyName=labs-flow-prod-cdn-key&Signature=MOP4fXq9Tz"


class _EscapedReply(_FlowReply):
    """A reply whose inner JSON string escapes '=' and '&' the way Google's batchexecute bodies often do."""

    def __init__(self, rpcid, payload):
        super().__init__(rpcid, payload)
        self.body = self.body.replace("=", "\\u003d").replace("&", "\\u0026")


def test_flow_replies_keep_no_signed_query_an_escaped_reply_naming_the_job_carries(monkeypatch, tmp_path):
    # Re-review H1 (2026-09-17): the excerpt was cut from the raw body, where an escaped '=' hid the query.
    log = []
    naming = _EscapedReply("Kp2Wfd", [[JOB_MEDIA, "poster", SIGNED_URL]])
    _install(
        monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies={"click": [SUBMIT_REPLY, naming]}
    )
    with pytest.raises(RuntimeError, match="again in a few minutes"):
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True)
    excerpt = gen.Ledger(tmp_path / "ledger.jsonl").rows()[-1]["flow"]["named_by"]["Kp2Wfd"]
    ledger_text = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")
    assert JOB_MEDIA in excerpt and "MOP4fXq9Tz" not in ledger_text


@pytest.mark.parametrize("before", ["z" * 50, ""], ids=["cut at the excerpt edge", "beside the job id"])
def test_flow_replies_turn_a_long_token_near_the_job_id_into_a_placeholder(before):
    token = "B" * 300
    naming = _FlowReply("Kp2Wfd", [["https://lh3.googleusercontent.com/" + token, before, JOB_MEDIA]])
    excerpt = _judged([SUBMIT_REPLY, naming])["named_by"]["Kp2Wfd"]
    assert "B" * 40 not in excerpt and "<token>" in excerpt


def test_flow_replies_credit_this_job_only_with_the_code_paired_with_its_own_id():
    # Re-review H2 (2026-09-17): a reply pairing codes with two workflows credited both to this job.
    notice = _FlowReply(
        "Xq9Tzb",
        [["PUBLIC_ERROR_UNSAFE_FACE", OTHER_WORKFLOW], ["PUBLIC_ERROR_UNSAFE_CONTENT", JOB_WORKFLOW]],
    )
    flow = _judged([SUBMIT_REPLY, notice])
    assert (flow["reasons"], flow["reasons_elsewhere"]) == (
        ["PUBLIC_ERROR_UNSAFE_CONTENT"],
        ["PUBLIC_ERROR_UNSAFE_FACE"],
    )


def test_flow_replies_redact_a_signed_query_before_cutting_the_excerpt_through_it():
    # gflow's rule needs "signature=" in the token, so a cut landing inside it would leave the value behind.
    for size in range(400):
        payload = [[SIGNED_URL + "&x=" + ("k." * 200)[:size], JOB_MEDIA]]
        text = composer._LONG_TOKEN_RE.sub("<token>", json.dumps(payload))
        if text.find(JOB_MEDIA) - 150 == text.find("Signature=") + 6:
            break
    else:
        raise AssertionError("no padding puts the excerpt's start inside the signed query")
    excerpt = _judged([SUBMIT_REPLY, _FlowReply("Kp2Wfd", payload)])["named_by"]["Kp2Wfd"]
    assert JOB_MEDIA in excerpt and "MOP4fXq9Tz" not in excerpt


def test_flow_replies_cut_the_excerpt_around_the_job_id_wherever_it_sits():
    naming = _FlowReply("Kp2Wfd", [["n" * 30] * 40 + [JOB_MEDIA]])
    assert JOB_MEDIA in _judged([SUBMIT_REPLY, naming])["named_by"]["Kp2Wfd"]


def test_flow_replies_credit_codes_by_the_ids_beside_them():
    notice = _FlowReply(
        "Xq9Tzb",
        [
            [JOB_WORKFLOW, ["PUBLIC_ERROR_UNSAFE_FACE"]],
            [f"workflow {JOB_WORKFLOW} stopped: PUBLIC_ERROR_UNSAFE_GENERATION"],
            [JOB_WORKFLOW, f"{OTHER_WORKFLOW}: PUBLIC_ERROR_UNSAFE_CONTENT"],
            ["PUBLIC_ERROR_UNSAFE_IDENTITY", FLOW_PROJECT],
            ["note", OTHER_WORKFLOW],
        ],
    )
    flow = _judged([SUBMIT_REPLY, notice])
    assert flow["reasons"] == ["PUBLIC_ERROR_UNSAFE_FACE", "PUBLIC_ERROR_UNSAFE_GENERATION"]
    assert flow["reasons_elsewhere"] == ["PUBLIC_ERROR_UNSAFE_CONTENT", "PUBLIC_ERROR_UNSAFE_IDENTITY"]


def test_flow_replies_call_any_last_status_outside_the_measured_ones_unmeasured():
    # Re-review H3 (2026-09-17): statuses 6, 7, 5 read "last reported status 5 ... after unmeasured status 7".
    said = composer._flow_said(_judged([SUBMIT_REPLY, _status_reply(7), _status_reply(5)]))
    assert said.startswith(
        f"Flow last reported unmeasured status 5 for workflow {JOB_WORKFLOW} after unmeasured status 7"
    )


def test_flow_replies_call_status_4_a_failure_with_the_reason_it_carried():
    # Measured 2026-09-17 (plan E, L4): statuses 6, 2, 4 with PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED, 0 credits.
    filtered = _status_reply(4, extra=[["PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED"]])
    flow = _judged([SUBMIT_REPLY, _status_reply(2), filtered])
    assert (flow["statuses"], flow["unmeasured"]) == ([6, 2, 4], None)
    assert composer._flow_said(flow).startswith(
        f"Flow failed workflow {JOB_WORKFLOW} (status 4) with PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED"
    )


def test_flow_replies_never_call_a_statusless_last_record_unmeasured():
    said = composer._flow_said(_judged([SUBMIT_REPLY, _status_reply(2), _status_reply(None)]))
    assert said.startswith(f"Flow last reported status None for workflow {JOB_WORKFLOW}")
    assert "unmeasured" not in said


CHARACTER = "c6a1d67c-fe9b-486c-bc36-4116288ac8f5"


def test_flow_replies_count_the_references_the_jobs_own_record_names_as_the_jobs():
    # Re-review F1 (2026-09-17): the failed L4 record names its character and image; a code beside them is the job's.
    notice = _FlowReply(
        "Xq9Tzb", [[JOB_WORKFLOW, CHARACTER, "PUBLIC_ERROR_UNSAFE_FACE"], ["note", OTHER_WORKFLOW]]
    )
    flow = _judged([SUBMIT_REPLY, _status_reply(2, extra=[[[CHARACTER]]]), notice])
    assert (flow["reasons"], flow["reasons_elsewhere"]) == (["PUBLIC_ERROR_UNSAFE_FACE"], [])


def test_flow_replies_redact_a_long_unspaced_reply_in_linear_time():
    # Re-review F2 (2026-09-17): \S*(...)\S* took 22 s on 40,000 unspaced characters, after a paid click.
    # Base64-like text: '+' and '/' break the long-token rule, so the signed-query rule sees the whole run.
    naming = _FlowReply("Kp2Wfd", [[JOB_MEDIA, "ab+/" * 4_750]])
    assert len(naming.body) <= composer.SMALL_REPLY
    started = asyncio.run(_timed(lambda: _judged([SUBMIT_REPLY, naming])))
    assert started < 2.0


async def _timed(run):
    loop = asyncio.get_running_loop()
    begin = loop.time()
    await asyncio.to_thread(run)
    return loop.time() - begin


def test_flow_replies_redact_an_escaped_signed_query_in_a_reply_without_frames():
    # Re-review F3 (2026-09-17): a body with no wrb.fr frame was redacted raw, where an escaped '=' hid the query.
    body = ')]}\'\n\n[["er", null, "' + JOB_MEDIA + " " + SIGNED_URL.replace("=", "\\u003d") + '"]]'
    raw = _FlowReply("Kp2Wfd", [])
    raw.body = body
    assert composer.parse_frames(body) == []
    excerpt = _judged([SUBMIT_REPLY, raw])["named_by"]["Kp2Wfd"]
    assert JOB_MEDIA in excerpt and "MOP4fXq9Tz" not in excerpt


def test_flow_replies_read_every_frame_of_a_reply():
    reply = _FlowReply("Kp2Wfd", [["unrelated"]])
    second = json.dumps([[JOB_MEDIA, "PUBLIC_ERROR_UNSAFE_FACE"]])
    frame = f'["wrb.fr", "Kp2Wfd", {json.dumps(second)}, null, null, null, "generic"]'
    reply.body = reply.body.rstrip("]") + "], " + frame + "]"
    assert len(composer.parse_frames(reply.body)) == 2
    flow = _judged([SUBMIT_REPLY, reply])
    assert flow["reasons"] == ["PUBLIC_ERROR_UNSAFE_FACE"] and JOB_MEDIA in flow["named_by"]["Kp2Wfd"]


@pytest.mark.parametrize(
    ("reply", "field", "expected"),
    [
        (
            _FlowReply("Zzl0ze", [["x" * 30_000, ["PUBLIC_ERROR_UNSAFE_FACE", JOB_WORKFLOW]]]),
            "reasons",
            ["PUBLIC_ERROR_UNSAFE_FACE"],
        ),
        (_FlowReply("jwpduf", [None, 1, [_flow_record(2, extra=["y" * 30_000])]]), "statuses", [6, 2]),
    ],
    ids=["a large reply carrying a code", "a large status reply"],
)
def test_flow_replies_keep_a_large_reply_that_matters(reply, field, expected):
    assert len(reply.body) > composer.SMALL_REPLY
    assert _judged([SUBMIT_REPLY, reply])[field] == expected


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
        (
            [*ON_PAGE, {"kind": "media", "id": "w-stray", "entity": "", "text": "x.png"}],
            f"Thu peobj1.png x.png {PROMPT}",
        ),
        ([*ON_PAGE, ON_PAGE[0]], f"Thu peobj1.png Thu {PROMPT}"),
        (ON_PAGE, "Thu peobj1.png stands in a sunny"),
        (ON_PAGE, f"Thu peobj1.png {PROMPT[: -len(' camera')]}"),
        (ON_PAGE, f"Thu peobj1.png {PROMPT} {PROMPT}"),
        (ON_PAGE, f"words an earlier run left Thu peobj1.png {PROMPT}"),
        (ON_PAGE, f"Thu peobj1.png @peobj1 {PROMPT}"),
    ],
    ids=[
        "a chip went missing",
        "another character",
        "entity id not bound",
        "entity id disagrees",
        "a stray chip",
        "a chip appears twice",
        "prompt cut short",
        "last word missing",
        "prompt typed twice",
        "leftover text",
        "mention query left as text",
    ],
)
def test_generate_verify_refuses_a_prompt_that_changed_after_setup(monkeypatch, at_click, box_text):
    # Re-review C (2026-09-17): a box that merely CONTAINS the prompt let a doubled prompt or leftover text go out.
    setup, verify, session = _prepared(monkeypatch, [], ON_PAGE)
    asyncio.run(setup(session))
    monkeypatch.setattr(ingredients, "chips_on", lambda page: _async([dict(chip) for chip in at_click]))
    session.page.box_text = box_text
    with pytest.raises(LookupError, match="refusing to spend"):
        asyncio.run(verify(session))


def test_generate_verify_compares_the_box_text_with_its_whitespace_collapsed(monkeypatch):
    box_text = f"Thu  peobj1.png\n{PROMPT} "
    setup, verify, session = _prepared(monkeypatch, [], ON_PAGE, box_text=box_text)
    asyncio.run(setup(session))
    assert asyncio.run(verify(session))["prompt_text"] == box_text


def test_generate_verify_ignores_letter_case(monkeypatch):
    # innerText applies CSS text-transform while a chip's textContent does not.
    box_text = f"THU PEOBJ1.PNG {PROMPT}"
    setup, verify, session = _prepared(monkeypatch, [], ON_PAGE, box_text=box_text)
    asyncio.run(setup(session))
    assert asyncio.run(verify(session))["prompt_text"] == box_text


async def _async(value):
    return value


def test_the_option_script_reads_title_and_kind_and_the_chip_script_reads_kind_and_mention_id():
    assert ".asset-title" in ingredients._OPTIONS_JS and ".type-subtitle" in ingredients._OPTIONS_JS
    assert "data-reference-type" in ingredients._CHIPS_JS and "data-mention-id" in ingredients._CHIPS_JS
    assert re.fullmatch(r"flow-prompt-box \.mention-chip", ingredients.CHIP)
