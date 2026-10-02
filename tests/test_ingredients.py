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
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video import gen, mcp_server
from video.flow import clips, composer, ingredients, overlays

ENTITY = "47e5150d-9c4b-4165-a937-4852e9abd194"
OTHER = "11111111-2222-3333-4444-555555555555"
MEDIA = "53a8930b-fe54-40a0-a9c2-a59859ff126f"
WORKFLOW = "02a43d09-76e6-463c-8765-f9ce8df34592"
OTHER_IMAGE_WORKFLOW = "9b1c7e52-3d4f-4a6b-8c9d-0e1f2a3b4c5d"
PROMPT = "stands in a sunny bakery and smiles at the camera"
# Measured 2026-10-01 (out/al/t4.json, t4b.json): a listing url ends on the image's bare token; the "+" dialog shows the
# same token on another host with a size suffix; a chip on the ingredient bar shows a signed url whose path ends with
# the image's workflow id.
TOKEN = "AJx9Qm3TzK7LpW2bN8cRv5YhD4fG6sUe1oXtZa0i"
TWIN_TOKEN = "Bq4Wn8ErT2yU6iO0pA3sD5fG7hJ9kL1zX3cV5bN7"
URL = f"https://lh3.googleusercontent.com/asb/{TOKEN}"
TAIL = TOKEN[-24:]
FLOW_SAYS = "Maximum image ingredients reached (3 allowed)"

THU = ingredients.Reference("entity", ENTITY, "Thu", frozenset({ENTITY}))
IMAGE = ingredients.Reference("media", MEDIA, "peobj1.png", frozenset({WORKFLOW}), TAIL)
# A request names a preset voice by its lowercase id and a custom voice by its workflow id (measured 2026-10-01).
LILY_VOICE = "17b8dafb-7c04-441e-9239-36feaf27eb65"
ACHIRD = ingredients.Reference("voice", "achird", "Achird", frozenset({"achird"}))
LILY = ingredients.Reference("voice", LILY_VOICE, "LilyVoice", frozenset({LILY_VOICE}), custom=True)
PRESETS = [{"id": "achird", "name": "Achird"}, {"id": "leda", "name": "Leda"}]
CUSTOMS = [
    {
        "workflow_id": LILY_VOICE,
        "media_id": "9712ce82-97a4-4d2c-9e22-9ad1188c6654",
        "name": "LilyVoice",
        "base": "Leda",
    }
]
AUDIO_CAP_SAYS = "Maximum audio ingredients reached (1 allowed)"


def _character(entity=ENTITY, name="Thu"):
    return {"entity_id": entity, "name": name, "portrait_media_id": None, "portrait_workflow_id": None}


def _image(media=MEDIA, title="peobj1.png", kind="image"):
    return {"id": media, "title": title, "kind": kind}


def _record(media=MEDIA, workflow=WORKFLOW, url=URL):
    return {"id": media, "workflow_id": workflow, "url": url}


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
    # The "+" dialog's row is told by the end of the image's listing url (plan AL, 2026-10-01).
    assert references[1].tail == TAIL and references[0].tail == ""


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
            [
                _record(),
                _record(
                    media=OTHER, workflow="w-twin", url=f"https://lh3.googleusercontent.com/asb/zz{TAIL}"
                ),
            ],
            LookupError,
            "ends with the same url",
        ),
        ([_image()], [_record(media=OTHER)], LookupError, "no workflow id"),
        ([_image()], [_record(url=None)], LookupError, "no url"),
    ],
)
def test_resolve_refuses_an_image_the_dialog_could_not_single_out(media, records, error, message):
    # Plan AL (2026-10-01): an image is attached through the "+" dialog and told by its url, so two images sharing a
    # title are refused only when their urls end alike; until then any shared title was refused.
    with pytest.raises(error, match=message):
        ingredients.resolve([_character()], media, records, [ENTITY], [MEDIA], "omni-flash")


def test_resolve_takes_images_sharing_a_title_when_their_urls_end_differently():
    twin = _image(media=OTHER, title="PEOBJ1.png")
    records = [
        _record(),
        _record(media=OTHER, workflow="w-twin", url=f"https://lh3.googleusercontent.com/asb/{TWIN_TOKEN}"),
    ]
    references = ingredients.resolve([], [_image(), twin], records, [], [MEDIA, OTHER], "omni-flash")
    assert [(r.id, r.tail) for r in references] == [(MEDIA, TAIL), (OTHER, TWIN_TOKEN[-24:])]


def test_resolve_takes_an_image_whose_title_would_leave_nothing_to_type_after_an_at_sign():
    # The title is the dialog's search text now, typed whole; only a character's name is still typed after '@'.
    references = ingredients.resolve([], [_image(title=" .png")], [_record()], [], [MEDIA], "omni-flash")
    assert references[0].title == " .png"


def test_resolve_refuses_more_references_than_the_model_takes():
    characters = [_character(entity=f"e{index}", name=f"n{index}") for index in range(3)]
    ids = [c["entity_id"] for c in characters]
    with pytest.raises(ValueError, match="at most 3"):
        ingredients.resolve(characters, [_image()], [_record()], ids, [MEDIA], "veo-lite")
    assert len(ingredients.resolve(characters, [_image()], [_record()], ids, [MEDIA], "omni-flash")) == 4


@pytest.mark.parametrize(
    ("model", "images", "flow_says"),
    [
        ("veo-lite", 4, "Maximum image ingredients reached (3 allowed)"),
        ("omni-flash", 8, "Maximum image ingredients reached (7 allowed)"),
        ("veo-quality", 1, "You cannot use image ingredients with this model."),
    ],
)
def test_resolve_refuses_more_images_than_the_model_takes_in_the_words_flow_shows(model, images, flow_says):
    # Read off the composer's own refusal cards on 2026-10-01 (out/flow_research/log_caps2.txt).
    listed = [_image(media=f"m{index}", title=f"i{index}.png") for index in range(images)]
    records = [_record(media=f"m{index}", workflow=f"w{index}") for index in range(images)]
    with pytest.raises(ValueError, match=re.escape(flow_says)) as refused:
        ingredients.resolve([], listed, records, [], [each["id"] for each in listed], model)
    assert "2026-10-01" in str(refused.value)


def test_resolve_counts_a_character_against_the_image_slots_in_the_words_flow_shows():
    # Measured 2026-10-01 (out/al/t4_live.json): on Veo 3.1 Lite a mentioned character and two images filled the three
    # slots, and the third image was refused with the sentence below.
    images = [_image(media=f"m{index}", title=f"i{index}.png") for index in range(3)]
    records = [_record(media=f"m{index}", workflow=f"w{index}") for index in range(3)]
    with pytest.raises(ValueError, match=re.escape(FLOW_SAYS)):
        ingredients.resolve(
            [_character()], images, records, [ENTITY], [each["id"] for each in images], "veo-lite"
        )
    assert (
        len(ingredients.resolve([_character()], images[:2], records, [ENTITY], ["m0", "m1"], "veo-lite")) == 3
    )


def test_resolve_never_puts_flows_image_refusal_in_the_mouth_of_a_character_overflow():
    # Flow's sentence was measured for images; four characters over veo-lite's three were never shown to it.
    characters = [_character(entity=f"e{index}", name=f"n{index}") for index in range(4)]
    with pytest.raises(ValueError, match="at most 3") as refused:
        ingredients.resolve(characters, [], [], [c["entity_id"] for c in characters], [], "veo-lite")
    assert "image ingredients" not in str(refused.value)


def _voices(*names, model="omni-flash", presets=PRESETS, customs=CUSTOMS):
    return ingredients.resolve(
        [_character()],
        [_image()],
        [_record()],
        [],
        [MEDIA],
        model,
        voices=list(names),
        presets=presets,
        customs=customs,
    )


def test_resolve_names_each_voice_by_what_the_request_carries_for_it():
    references = _voices("Achird", "LilyVoice")
    assert [(r.kind, r.id, r.title, r.custom) for r in references] == [
        ("media", MEDIA, "peobj1.png", False),
        ("voice", "achird", "Achird", False),
        ("voice", LILY_VOICE, "LilyVoice", True),
    ]
    assert references[1].mention_ids == {"achird"} and references[2].mention_ids == {LILY_VOICE}


def test_resolve_finds_a_voice_whatever_its_letter_case():
    # The dialog's search found Achird for 'achird' (measured 2026-10-01).
    assert _voices(" achird ")[1].title == "Achird"


def test_resolve_refuses_a_voice_the_project_does_not_offer_and_names_the_ones_it_does():
    with pytest.raises(LookupError, match="no voice named 'Zeus'") as refused:
        _voices("Zeus")
    assert "Leda" in str(refused.value) and "LilyVoice" in str(refused.value)


def test_resolve_takes_a_voice_by_its_whole_name_only():
    # The dialog's search matches inside a name ('Lily' found LilyVoice, measured 2026-10-01), and every later check
    # reads the voice that was found: a part of a name would buy a voice the caller never named (review of plan AL).
    with pytest.raises(LookupError, match="no voice named 'Lily'") as refused:
        _voices("Lily")
    assert "LilyVoice" in str(refused.value)


def test_resolve_tells_an_image_by_its_first_listed_record_as_the_frame_picker_does():
    # None of the 67 images measured holds two records; the rule only keeps the two pickers telling one image alike.
    older = _record(workflow="w-older", url=f"https://lh3.googleusercontent.com/asb/{TWIN_TOKEN}")
    references = ingredients.resolve([], [_image()], [_record(), older], [], [MEDIA], "omni-flash")
    assert references[0].tail == TAIL


@pytest.mark.parametrize(
    "customs",
    [
        [*CUSTOMS, {**CUSTOMS[0], "workflow_id": "w-twin", "media_id": "m-twin"}],
        [{**CUSTOMS[0], "name": "Achird"}],
    ],
    ids=["two voices of your own", "a voice of your own named like a preset"],
)
def test_resolve_refuses_a_voice_name_two_voices_carry(customs):
    name = customs[-1]["name"]
    with pytest.raises(LookupError, match="2 voices are named"):
        _voices(name, customs=customs)


def test_resolve_refuses_a_voice_named_twice():
    with pytest.raises(ValueError, match="named once"):
        _voices("Achird", "achird")


@pytest.mark.parametrize(
    ("model", "count", "flow_says"),
    [
        ("veo-lite", 2, "Maximum audio ingredients reached (1 allowed)"),
        ("veo-fast", 2, "Maximum audio ingredients reached (1 allowed)"),
        ("omni-flash", 6, "Maximum audio ingredients reached (5 allowed)"),
    ],
)
def test_resolve_refuses_more_voices_than_the_model_takes_in_the_words_flow_shows(model, count, flow_says):
    # Read off the composer's refusal cards on 2026-10-01 (out/flow_research/log_caps2.txt).
    presets = [{"id": f"v{index}", "name": f"V{index}"} for index in range(count)]
    with pytest.raises(ValueError, match=re.escape(flow_says)) as refused:
        _voices(*[each["name"] for each in presets], model=model, presets=presets, customs=[])
    assert "2026-10-01" in str(refused.value)
    assert (
        len(_voices(*[each["name"] for each in presets[:-1]], model=model, presets=presets, customs=[]))
        == count
    )


def test_resolve_refuses_no_reference_at_all_and_a_repeated_id():
    with pytest.raises(ValueError, match="at least one character or image"):
        ingredients.resolve([_character()], [_image()], [_record()], [], [], "omni-flash")
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


# the "+" dialog


def _row(title="peobj1.png", token=TOKEN, workflow=WORKFLOW, **more):
    return {
        "title": title,
        "src": f"https://flow.google.com/asb/{token}=s512-rw",
        "workflow": workflow,
        **more,
    }


def _bar_raw(workflow=WORKFLOW, *, refused=False):
    """A bar chip as the page shows it (out/al/t4.json): a refused image chip is `chip-container-disabled`, never the
    bare class `disabled`, and carries the error icon."""
    return {
        "cls": "chip-container chip-container-disabled" if refused else "chip-container",
        "text": "error cancel" if refused else "cancel",
        "error": refused,
        "src": f"https://flow-content.google/image/{workflow}?Expires=1790000000&KeyName=k&Signature=s",
    }


def _voice_row(title="Achird", *, custom=False, **more):
    return {"title": title, "category": "Voices", "custom": custom, "src": "", "workflow": title, **more}


def _voice_raw(name="Achird", *, refused=None):
    """A voice chip as the page shows it (out/al/t5.json): no name in it, only its icon; the hover card names the voice
    after `voice_selection`, behind Flow's refusal when there is one; a refused voice chip's class is `disabled`."""
    return {
        "cls": "chip-container disabled" if refused else "chip-container",
        "text": "error cancel cancel voice_selection" if refused else "cancel voice_selection",
        "error": bool(refused),
        "src": "",
        "card": f"{refused + ' ' if refused else ''}play_arrow 0:06 voice_selection {name}",
    }


VOICE_RAW = {"cls": "chip-container", "text": "cancel voice_selection", "error": False, "src": ""}
CHARACTER_RAW = {
    "cls": "chip-container",
    "text": "cancel accessibility_new",
    "error": False,
    "src": "https://flow-content.google/image/c0ffee00-0000-4000-8000-000000000000?Expires=1&KeyName=k&Signature=s",
}


class _DialogKeyboard:
    def __init__(self, page):
        self.page = page
        self.pressed = []

    async def press(self, key):
        self.pressed.append(key)
        if key == "Escape":
            self.page.escape()


class _Mouse:
    def __init__(self, page):
        self.page = page

    async def move(self, x, y):
        if self.page.hovered is not None:
            self.page.left = (self.page.hovered, self.page.clock)
        self.page.hovered = None


class _DialogLocator:
    def __init__(self, page, selector, index=0):
        self.page, self.selector, self.index = page, selector, index

    @property
    def first(self):
        return self

    def nth(self, index):
        return _DialogLocator(self.page, self.selector, index)

    def locator(self, selector):
        return _DialogLocator(self.page, selector)

    def get_by_text(self, text, exact=False):
        assert exact, "a category is chosen by its whole label"
        return _DialogLocator(self.page, f"category {text}")

    async def count(self):
        return self.page.count(self.selector)

    async def is_visible(self):
        return True

    async def wait_for(self, state=None, timeout=None):
        if not self.page.count(self.selector):
            raise PlaywrightTimeoutError(f"Timeout {timeout}ms exceeded.")

    async def click(self, timeout=None):
        if self.selector == ingredients.ADD and self.page.add_times_out:
            raise PlaywrightTimeoutError(f"Locator.click: Timeout {timeout}ms exceeded.")
        self.page.click(self.selector, self.index)

    async def fill(self, text):
        assert self.selector == ingredients.SEARCH, self.selector
        self.page.query = text
        self.page.searched.append(text)
        self.page.searched_at = self.page.clock

    async def hover(self, timeout=None, force=False):
        assert self.selector == ingredients.BAR, self.selector
        self.page.hovered, self.page.hovered_at = self.index, self.page.clock


class _DialogPage:
    """The "+" dialog as measured on 2026-10-01 (out/al/t4.json, t4b.json): Images is a category, the search box takes
    a whole title and narrows the rows, a row carries a title and a thumbnail and no id; a click adds the ACTIVE row
    (the first one) and the dialog closes itself, any other row only becomes active and 'Add to prompt' then adds it;
    the first Escape after a search only empties the search box."""

    def __init__(
        self,
        rows,
        *,
        bar=(),
        confirms=1,
        active_sticks=False,
        rows_after_ms=0,
        unfiltered_ms=0,
        thumb_after_ms=0,
        stays_open=False,
        categories=("Images", "Voices", "Characters"),
        refused=None,
        never_closes=False,
        card_after_ms=0,
        card_lingers_ms=0,
        standing=(),
        foreign_overlay=None,
        add_times_out=False,
        add_covered=None,
        rows_flicker=False,
    ):
        self.rows = [dict(row) for row in rows]
        self.bar = [dict(chip) for chip in bar]
        self.confirms = confirms
        self.active_sticks = active_sticks
        self.rows_after_ms = rows_after_ms
        self.unfiltered_ms = unfiltered_ms
        self.thumb_after_ms = thumb_after_ms
        self.stays_open = stays_open
        self.categories = categories
        self.refused = refused
        self.never_closes = never_closes
        # A card shows this long after the pointer reaches its chip and stays this long after it leaves.
        self.card_after_ms = card_after_ms
        self.card_lingers_ms = card_lingers_ms
        # Panes that are on the page whatever is hovered (a toast).
        self.standing = list(standing)
        # The text of an overlay nobody here opened, standing with its backdrop over the composer.
        self.foreign_overlay = foreign_overlay
        # A click on the "+" button that times out, and what `overlays.covering` then reads over it.
        self.add_times_out = add_times_out
        self.add_covered = add_covered
        self.rows_flicker = rows_flicker
        self.reads = 0
        self.open = False
        self.category = None
        self.query = ""
        self.searched = []
        self.searched_at = 0
        self.active = 0
        self.hovered = None
        self.hovered_at = 0
        self.left = None
        self.clicked = []
        self.clock = 0
        self.keyboard = _DialogKeyboard(self)
        self.mouse = _Mouse(self)

    def shown(self):
        if not self.open or self.category is None or self.clock < self.searched_at + self.rows_after_ms:
            return []
        rows = [row for row in self.rows if row.get("category", "Images") == self.category]
        if self.clock < self.searched_at + self.unfiltered_ms:
            return rows
        return [row for row in rows if self.query.casefold() in row["title"].casefold()]

    def card(self):
        """The hover card on the page now: the hovered chip's once it has shown, or the last one while it lingers."""
        index = self.hovered
        if index is not None and self.clock < self.hovered_at + self.card_after_ms:
            index = None
        if index is None and self.left is not None and self.clock < self.left[1] + self.card_lingers_ms:
            index = self.left[0]
        if index is None:
            return []
        chip = self.bar[index]
        text = chip["card"] if "card" in chip else (self.refused if chip["error"] else None)
        return [text] if text else []

    def count(self, selector):
        if selector == ingredients.BACKDROP:
            return int(self.open or self.foreign_overlay is not None)
        if selector == ingredients.OPTION:
            return len(self.shown())
        if selector == ingredients.CONFIRM:
            return self.confirms if self.shown() else 0
        if selector == ingredients.SEARCH:
            return int(self.open)
        if selector == ingredients.BAR:
            return len(self.bar)
        if selector.startswith("category "):
            return int(self.open and selector.removeprefix("category ") in self.categories)
        raise AssertionError(f"unexpected locator {selector}")

    def click(self, selector, index):
        if selector == ingredients.ADD:
            assert not self.open, "the + button sits under the open dialog's backdrop"
            self.open, self.category, self.query, self.active = True, None, "", 0
            self.clicked.append("+")
        elif selector.startswith("category "):
            self.category = selector.removeprefix("category ")
            self.clicked.append(self.category)
        elif selector == ingredients.OPTION:
            row = self.shown()[index]
            self.clicked.append((row["title"], row["workflow"]))
            if index == self.active:
                self.commit(index)
            elif not self.active_sticks:
                self.active = index
        elif selector == ingredients.CONFIRM:
            self.clicked.append("Add to prompt")
            self.commit(self.active)
        else:
            raise AssertionError(f"unexpected click on {selector}")

    def commit(self, index):
        row = self.shown()[index]
        if "chips" in row:
            chips = row["chips"]
        elif "chip" in row:
            chips = [row["chip"]]
        elif row.get("category") == "Voices":
            chips = [_voice_raw(row["title"], refused=self.refused)]
        else:
            chips = [_bar_raw(row["workflow"], refused=self.refused is not None)]
        self.bar += [{**chip, "landed_at": self.clock} for chip in chips if chip is not None]
        if not self.stays_open:
            self.open = False

    def escape(self):
        if self.never_closes:
            return
        if not self.open:
            # Escape reaches whatever else is open: an overlay nobody here opened is dismissed by it.
            self.foreign_overlay = None
            return
        if self.query:
            self.query, self.searched_at = "", self.clock
        else:
            self.open = False

    async def wait_for_timeout(self, ms):
        self.clock += ms

    def locator(self, selector):
        if selector == ingredients.DIALOG:
            return _DialogLocator(self, "the dialog")
        return _DialogLocator(self, selector)

    async def evaluate(self, script, arg=None):
        if script == ingredients._ROWS_JS:
            assert arg == ingredients.OPTION
            self.reads += 1
            rows = [
                {
                    "title": row["title"],
                    "src": row["src"],
                    "visible": row.get("visible", True),
                    "active": index == self.active,
                    "custom": row.get("custom", False),
                }
                for index, row in enumerate(self.shown())
            ]
            # A list that never comes to rest: every read shows the rows in another order.
            return rows[::-1] if self.rows_flicker and self.reads % 2 else rows
        if script == composer._OVERLAY_TEXT_JS:
            return self.foreign_overlay or ""
        if script == overlays._COVERING_JS:
            assert arg == ingredients.ADD
            return self.add_covered
        if script == ingredients._BAR_JS:
            assert arg == ingredients.BAR
            return [
                {
                    **{key: value for key, value in chip.items() if key not in ("landed_at", "card")},
                    "src": ""
                    if "landed_at" in chip and self.clock < chip["landed_at"] + self.thumb_after_ms
                    else chip["src"],
                }
                for chip in self.bar
            ]
        if script == ingredients._CARD_JS:
            return [*self.standing, *self.card()]
        raise AssertionError(f"unexpected script {script[:40]!r}")


def test_attach_image_searches_the_whole_title_under_images_and_adds_the_row_carrying_the_images_url():
    page = _DialogPage([_row()])
    chip = asyncio.run(ingredients.attach_image(page, IMAGE))
    assert chip == {"kind": "media", "id": WORKFLOW, "text": "peobj1.png"}
    assert page.clicked == ["+", "Images", ("peobj1.png", WORKFLOW)]
    assert [each["src"] for each in page.bar] == [_bar_raw()["src"]]
    assert page.open is False
    # A submit here would spend with no ledger row: the dialog is driven by clicks and Escape alone.
    assert "Enter" not in page.keyboard.pressed


def test_attach_image_tells_twins_apart_by_the_url_and_adds_a_row_that_is_not_the_active_one_with_add_to_prompt():
    # Measured 2026-10-01 (t4b.json): the second of two rows titled alike only became active on its click, the preview
    # showed it, and 'Add to prompt' put it on the bar.
    twin = _row(token=TWIN_TOKEN, workflow=OTHER_IMAGE_WORKFLOW)
    page = _DialogPage([twin, _row()])
    chip = asyncio.run(ingredients.attach_image(page, IMAGE))
    assert chip["id"] == WORKFLOW
    assert page.clicked == ["+", "Images", ("peobj1.png", WORKFLOW), "Add to prompt"]
    assert [each["src"] for each in page.bar] == [_bar_raw()["src"]]


def test_attach_image_waits_for_the_search_to_narrow_the_rows_before_it_trusts_an_index():
    # The first read after typing can still show the unfiltered window, where the image sits at another index.
    others = [
        _row(title=f"other{index}.png", token=f"{index}" * 40, workflow=f"w-{index}") for index in range(3)
    ]
    page = _DialogPage([*others, _row()], unfiltered_ms=1_500)
    assert asyncio.run(ingredients.attach_image(page, IMAGE))["id"] == WORKFLOW
    assert page.clicked == ["+", "Images", ("peobj1.png", WORKFLOW)]


def test_attach_image_never_presses_add_to_prompt_while_another_row_is_active():
    twin = _row(token=TWIN_TOKEN, workflow=OTHER_IMAGE_WORKFLOW)
    page = _DialogPage([twin, _row()], active_sticks=True)
    with pytest.raises(LookupError, match="active"):
        asyncio.run(ingredients.attach_image(page, IMAGE))
    assert "Add to prompt" not in page.clicked
    assert page.bar == [] and page.open is False


@pytest.mark.parametrize("confirms", [0, 2])
def test_attach_image_refuses_to_guess_between_add_to_prompt_buttons(confirms):
    twin = _row(token=TWIN_TOKEN, workflow=OTHER_IMAGE_WORKFLOW)
    page = _DialogPage([twin, _row()], confirms=confirms)
    with pytest.raises(LookupError, match=f"{confirms} visible 'Add to prompt' buttons"):
        asyncio.run(ingredients.attach_image(page, IMAGE))
    assert page.bar == [] and page.open is False


def test_attach_image_refuses_an_image_the_dialog_does_not_offer_and_closes_the_dialog():
    # Measured 2026-10-01: lily_black_face.png is listed in the project and no row of the dialog carries it.
    twin = _row(token=TWIN_TOKEN, workflow=OTHER_IMAGE_WORKFLOW)
    page = _DialogPage([twin])
    with pytest.raises(LookupError, match="offers no image") as refused:
        asyncio.run(ingredients.attach_image(page, IMAGE))
    assert MEDIA in str(refused.value) and "1 row" in str(refused.value)
    assert page.clicked == ["+", "Images"]
    assert page.bar == [] and page.open is False
    assert page.clock <= ingredients.ROW_WAIT_MS + 10_000


def test_attach_image_refuses_two_rows_carrying_the_same_url():
    page = _DialogPage([_row(), _row()])
    with pytest.raises(LookupError, match="2 rows"):
        asyncio.run(ingredients.attach_image(page, IMAGE))
    assert page.clicked == ["+", "Images"] and page.open is False


def test_attach_image_never_counts_a_row_it_cannot_see():
    hidden = {**_row(), "visible": False}
    page = _DialogPage([hidden, _row()])
    assert asyncio.run(ingredients.attach_image(page, IMAGE))["id"] == WORKFLOW


def test_attach_image_waits_for_rows_that_arrive_late():
    # An image uploaded a moment ago reaches a picker's results late (measured 2026-09-29 on the frame picker).
    page = _DialogPage([_row()], rows_after_ms=9_000)
    assert asyncio.run(ingredients.attach_image(page, IMAGE))["id"] == WORKFLOW


def test_attach_image_refuses_a_chip_that_names_another_image():
    wrong = _row(chip=_bar_raw(OTHER_IMAGE_WORKFLOW))
    page = _DialogPage([wrong])
    with pytest.raises(LookupError, match="refusing to generate with the wrong image"):
        asyncio.run(ingredients.attach_image(page, IMAGE))


def test_attach_image_types_the_whole_title_into_the_search_box():
    page = _DialogPage([_row()])
    asyncio.run(ingredients.attach_image(page, IMAGE))
    assert page.searched == ["peobj1.png"]


def test_attach_image_refuses_an_add_that_lands_two_chips():
    page = _DialogPage([_row(chips=[_bar_raw(), _bar_raw(OTHER_IMAGE_WORKFLOW)])])
    with pytest.raises(LookupError, match="added 2 ingredient chips"):
        asyncio.run(ingredients.attach_image(page, IMAGE))


def test_attach_image_waits_for_an_earlier_chips_thumbnail_before_it_counts_what_it_added():
    # An earlier chip still without its thumbnail would turn into "another chip" while this one is being added.
    page = _DialogPage(
        [_row()], bar=[{**_bar_raw(OTHER_IMAGE_WORKFLOW), "landed_at": 0}], thumb_after_ms=3_000
    )
    assert asyncio.run(ingredients.attach_image(page, IMAGE))["id"] == WORKFLOW


def test_attach_image_names_an_overlay_it_did_not_open_and_presses_nothing():
    # Plan AL I5: Escape would dismiss it unnamed, so an overlay that is not the ingredients dialog stops the call.
    page = _DialogPage([_row()], foreign_overlay="What's new in Flow Try it now Got it")
    with pytest.raises(LookupError, match="What's new in Flow") as refused:
        asyncio.run(ingredients.attach_image(page, IMAGE))
    assert "nothing was pressed" in str(refused.value)
    assert page.keyboard.pressed == [] and page.clicked == []
    assert page.foreign_overlay is not None


def test_attach_image_closes_its_own_dialog_left_open_before_it_starts():
    page = _DialogPage([_row()])
    page.open, page.category = True, "Voices"
    assert asyncio.run(ingredients.attach_image(page, IMAGE))["id"] == WORKFLOW
    assert page.keyboard.pressed == ["Escape"]
    assert page.clicked == ["+", "Images", ("peobj1.png", WORKFLOW)]


def test_attach_image_names_what_covers_the_plus_button_instead_of_timing_out():
    cover = {"tag": "div", "id": "", "text": "Rate your experience"}
    page = _DialogPage([_row()], add_times_out=True, add_covered=cover)
    with pytest.raises(LookupError, match="covered by div, which says 'Rate your experience'"):
        asyncio.run(ingredients.attach_image(page, IMAGE))
    uncovered = _DialogPage([_row()], add_times_out=True)
    with pytest.raises(PlaywrightTimeoutError):
        asyncio.run(ingredients.attach_image(uncovered, IMAGE))


def test_attach_image_refuses_rows_that_never_settle():
    twin = _row(token=TWIN_TOKEN, workflow=OTHER_IMAGE_WORKFLOW)
    page = _DialogPage([twin, _row()], rows_flicker=True)
    with pytest.raises(LookupError, match="never settled"):
        asyncio.run(ingredients.attach_image(page, IMAGE))
    assert page.clicked == ["+", "Images"] and page.open is False


def test_attach_image_says_so_when_the_dialogs_window_of_rows_is_full():
    # Measured 2026-10-01: the dialog renders 15 rows of 67 images. Whether a search can match more than it renders
    # was never measured, so a full window is said, not passed off as "Flow does not offer it".
    crowd = [
        _row(token=f"{index:02d}" + "q" * 38, workflow=f"w-{index}")
        for index in range(ingredients.ROWS_WINDOW)
    ]
    page = _DialogPage(crowd)
    with pytest.raises(LookupError, match="window") as refused:
        asyncio.run(ingredients.attach_image(page, IMAGE))
    assert "still does not offer" not in str(refused.value)
    few = _DialogPage(crowd[:2])
    with pytest.raises(LookupError, match="still does not offer"):
        asyncio.run(ingredients.attach_image(few, IMAGE))


def test_attach_image_refuses_a_click_that_adds_no_chip():
    page = _DialogPage([_row(chip=None)])
    with pytest.raises(LookupError, match="added 0 ingredient chips"):
        asyncio.run(ingredients.attach_image(page, IMAGE))


def test_attach_image_judges_only_the_chip_its_own_click_added():
    page = _DialogPage([_row()], bar=[_bar_raw(OTHER_IMAGE_WORKFLOW)])
    assert asyncio.run(ingredients.attach_image(page, IMAGE))["id"] == WORKFLOW
    assert len(page.bar) == 2


def test_attach_image_waits_for_the_thumbnail_before_it_judges_the_chip():
    # Measured 2026-10-01 (plan AK): read too early, three image chips looked like unknown chips.
    page = _DialogPage([_row()], thumb_after_ms=4_000)
    assert asyncio.run(ingredients.attach_image(page, IMAGE))["id"] == WORKFLOW
    never = _DialogPage([_row()], thumb_after_ms=10**9)
    with pytest.raises(LookupError, match="refusing to generate with the wrong image"):
        asyncio.run(ingredients.attach_image(never, IMAGE))
    assert never.clock <= ingredients.ROW_WAIT_MS + ingredients.BAR_WAIT_MS


def test_attach_image_says_in_flows_own_words_why_a_chip_is_refused():
    # Measured 2026-10-01 (t4.json): a fourth image on Veo 3.1 Lite lands as a chip, greyed, and its hover card reads
    # the sentence below; the price line still shows 10.
    page = _DialogPage([_row()], refused=FLOW_SAYS)
    with pytest.raises(LookupError, match=re.escape(FLOW_SAYS)) as refused:
        asyncio.run(ingredients.attach_image(page, IMAGE))
    assert "peobj1.png" in str(refused.value)
    assert page.hovered is None, "the pointer is moved off the chip, so its card does not cover the composer"


def test_attach_image_closes_a_dialog_that_stays_open_after_the_chip_landed():
    page = _DialogPage([_row()], stays_open=True)
    assert asyncio.run(ingredients.attach_image(page, IMAGE))["id"] == WORKFLOW
    assert page.open is False
    # With a search typed the first Escape only empties the search box (measured 2026-10-01).
    assert page.keyboard.pressed == ["Escape", "Escape"]


def test_attach_image_says_so_when_the_dialog_shows_no_images_category():
    page = _DialogPage([_row()], categories=("Voices",))
    with pytest.raises(LookupError, match="no Images category"):
        asyncio.run(ingredients.attach_image(page, IMAGE))
    assert page.open is False


def test_attach_image_refuses_a_reference_that_carries_no_url_to_match():
    # An empty tail would match every row.
    page = _DialogPage([_row()])
    with pytest.raises(ValueError, match="url"):
        asyncio.run(ingredients.attach_image(page, THU))
    with pytest.raises(ValueError, match="url"):
        asyncio.run(
            ingredients.attach_image(page, ingredients.Reference("media", MEDIA, "x.png", frozenset({"w"})))
        )
    assert page.clicked == []


def test_attach_voice_searches_the_name_under_voices_and_reads_the_name_back_off_the_chips_card():
    page = _DialogPage([_voice_row("Achird")])
    chip = asyncio.run(ingredients.attach_voice(page, ACHIRD))
    assert chip == {"kind": "voice", "id": "achird", "text": "Achird"}
    assert page.clicked == ["+", "Voices", ("Achird", "Achird")]
    assert page.open is False and page.hovered is None
    assert "Enter" not in page.keyboard.pressed


def test_attach_voice_adds_the_voice_of_your_own_and_never_the_preset_it_shadows():
    page = _DialogPage([_voice_row("LilyVoice", custom=True)])
    assert asyncio.run(ingredients.attach_voice(page, LILY)) == {
        "kind": "voice",
        "id": LILY_VOICE,
        "text": "LilyVoice",
    }
    # A preset row named like a custom voice of your own is not it, and the other way round.
    twins = _DialogPage([_voice_row("Achird", custom=True), _voice_row("Achird")])
    assert asyncio.run(ingredients.attach_voice(twins, ACHIRD))["id"] == "achird"
    assert twins.clicked[-2:] == [("Achird", "Achird"), "Add to prompt"]
    mirrored = _DialogPage([_voice_row("LilyVoice"), _voice_row("LilyVoice", custom=True)])
    assert asyncio.run(ingredients.attach_voice(mirrored, LILY))["id"] == LILY_VOICE
    assert mirrored.clicked[-2:] == [("LilyVoice", "LilyVoice"), "Add to prompt"]
    assert [chip["card"] for chip in mirrored.bar] == ["play_arrow 0:06 voice_selection LilyVoice"]


def test_attach_voice_takes_the_row_with_that_exact_title():
    # The search matches inside a title ('Lily' found LilyVoice, measured 2026-10-01), so the row's own title decides.
    page = _DialogPage([_voice_row("LilyVoice 2", custom=True), _voice_row("LilyVoice", custom=True)])
    assert asyncio.run(ingredients.attach_voice(page, LILY))["id"] == LILY_VOICE
    assert page.clicked[-2:] == [("LilyVoice", "LilyVoice"), "Add to prompt"]


def test_attach_voice_reads_the_card_of_the_chip_its_own_click_added():
    # Two voice chips look alike on the bar; only the place of the new one tells which card to read.
    page = _DialogPage([_voice_row("Achird")], bar=[_voice_raw("Leda")])
    assert asyncio.run(ingredients.attach_voice(page, ACHIRD)) == {
        "kind": "voice",
        "id": "achird",
        "text": "Achird",
    }


def test_attach_voice_waits_for_a_card_that_shows_late_and_gives_up_after_its_bound():
    late = _DialogPage([_voice_row("Achird")], card_after_ms=1_500)
    assert asyncio.run(ingredients.attach_voice(late, ACHIRD))["id"] == "achird"
    never = _DialogPage([_voice_row("Achird")], card_after_ms=10**9)
    with pytest.raises(LookupError, match="names 'nothing'"):
        asyncio.run(ingredients.attach_voice(never, ACHIRD))


def test_attach_voice_reads_its_card_beside_a_pane_that_is_not_one():
    # A toast is a `.cdk-overlay-pane` too.
    page = _DialogPage([_voice_row("Achird")], standing=["Image added to your project"])
    assert asyncio.run(ingredients.attach_voice(page, ACHIRD))["id"] == "achird"


def test_attach_voice_keeps_flows_refusal_when_the_card_names_no_voice():
    # A refused chip stops the run whatever it is; Flow's own words are worth more than "wrong voice".
    refused = {**_voice_raw("Achird", refused=AUDIO_CAP_SAYS), "card": AUDIO_CAP_SAYS}
    page = _DialogPage([_voice_row("Achird", chip=refused)])
    with pytest.raises(LookupError, match=re.escape(AUDIO_CAP_SAYS)) as failed:
        asyncio.run(ingredients.attach_voice(page, ACHIRD))
    assert "wrong voice" not in str(failed.value)


def test_attach_voice_refuses_a_voice_the_dialog_does_not_offer():
    page = _DialogPage([_voice_row("Leda")])
    with pytest.raises(LookupError, match="offers no voice 'Achird'"):
        asyncio.run(ingredients.attach_voice(page, ACHIRD))
    assert page.bar == [] and page.open is False


def test_attach_voice_says_in_flows_own_words_why_a_voice_is_refused():
    # Measured 2026-10-01: Veo 3.1 Lite takes one audio ingredient, and a character with a voice already holds it.
    page = _DialogPage([_voice_row("Achird")], refused=AUDIO_CAP_SAYS)
    with pytest.raises(LookupError, match=re.escape(AUDIO_CAP_SAYS)) as refused:
        asyncio.run(ingredients.attach_voice(page, ACHIRD))
    assert "play_arrow" not in str(refused.value)
    assert page.hovered is None


def test_attach_voice_refuses_a_chip_whose_card_names_another_voice():
    page = _DialogPage([_voice_row("Achird", chip=_voice_raw("Leda"))])
    with pytest.raises(LookupError, match="wrong voice"):
        asyncio.run(ingredients.attach_voice(page, ACHIRD))


def test_attach_voice_refuses_a_reference_that_is_no_voice():
    page = _DialogPage([_voice_row("Achird")])
    with pytest.raises(ValueError, match="voice"):
        asyncio.run(ingredients.attach_voice(page, IMAGE))
    assert page.clicked == []


def test_attach_image_never_takes_a_voice_row_for_its_image():
    page = _DialogPage([_voice_row("peobj1.png"), _row()])
    assert asyncio.run(ingredients.attach_image(page, IMAGE))["id"] == WORKFLOW
    assert page.clicked[:2] == ["+", "Images"]


@pytest.mark.parametrize(
    ("card", "name", "said"),
    [
        ("play_arrow 0:06 voice_selection Achird", "Achird", ""),
        (
            "Maximum audio ingredients reached (1 allowed) play_arrow 0:06 voice_selection Achird",
            "Achird",
            "Maximum audio ingredients reached (1 allowed)",
        ),
        (
            "An audio ingredient requires other ingredients to function. play_arrow 0:04 voice_selection LilyVoice",
            "LilyVoice",
            "An audio ingredient requires other ingredients to function.",
        ),
        (
            "Maximum image ingredients reached (3 allowed)",
            "",
            "Maximum image ingredients reached (3 allowed)",
        ),
        ("", "", ""),
    ],
    ids=["a voice", "a refused voice", "a voice alone", "a refused image", "no card"],
)
def test_a_hover_card_gives_the_voices_name_and_flows_refusal_apart(card, name, said):
    # The cards as read on 2026-10-01 (out/al/t5.json, t1b.json, t4.json).
    cards = [card] if card else []
    assert ingredients._card_voice(cards) == name
    assert ingredients._card_refusal(cards) == said


@pytest.mark.parametrize(
    ("cards", "name"),
    [
        (["play_arrow 0:06 voice_selection Lily Night Voice"], "Lily Night Voice"),
        (["Image added to your project", "play_arrow 0:06 voice_selection Achird"], "Achird"),
        (["play_arrow 0:06 voice_selection Achird", "Image added to your project"], "Achird"),
        (["play_arrow 0:06 voice_selection play_arrow girl"], "play_arrow girl"),
        (["play_arrow 0:06 voice_selection Achird", "play_arrow 0:04 voice_selection Leda"], ""),
        (["Image added to your project"], ""),
    ],
    ids=[
        "a name of several words",
        "a toast before the card",
        "a toast after the card",
        "a name holding an icon's word",
        "two cards naming two voices",
        "no card names a voice",
    ],
)
def test_a_voice_is_named_by_the_one_pane_that_is_a_voice_card(cards, name):
    assert ingredients._card_voice(cards) == name


def test_a_refusal_is_read_off_every_pane_and_never_holds_a_voices_player():
    cards = ["Maximum audio ingredients reached (1 allowed) play_arrow 0:06 voice_selection play_arrow girl"]
    assert ingredients._card_refusal(cards) == "Maximum audio ingredients reached (1 allowed)"
    assert ingredients._card_refusal(["play_arrow 0:06 voice_selection Achird"]) == ""
    # Two panes that came up with the pointer on the chip are both quoted: the driver cannot tell which of them is
    # Flow's refusal, and the first could be a toast.
    both = ["Image added to your project", "Maximum image ingredients reached (3 allowed)"]
    assert ingredients._card_refusal(both) == (
        "Image added to your project / Maximum image ingredients reached (3 allowed)"
    )


def test_close_dialog_gives_up_on_a_dialog_that_will_not_close():
    page = _DialogPage([_row()], never_closes=True)
    page.open = True
    with pytest.raises(LookupError, match="did not close"):
        asyncio.run(ingredients.close_dialog(page))
    assert 1 <= page.keyboard.pressed.count("Escape") <= 6


def test_close_dialog_presses_nothing_when_no_dialog_is_open():
    page = _DialogPage([_row()])
    asyncio.run(ingredients.close_dialog(page))
    assert page.keyboard.pressed == []


# the ingredient bar


@pytest.mark.parametrize(
    ("raw", "chip"),
    [
        (_bar_raw(), {"kind": "image", "id": WORKFLOW, "refused": False}),
        (_bar_raw(refused=True), {"kind": "image", "id": WORKFLOW, "refused": True}),
        (VOICE_RAW, {"kind": "voice", "id": "", "refused": False}),
        # A refused voice chip is the one kind whose class is the bare `disabled` (measured 2026-10-01).
        (
            {
                **VOICE_RAW,
                "cls": "chip-container disabled",
                "text": "error cancel voice_selection",
                "error": True,
            },
            {"kind": "voice", "id": "", "refused": True},
        ),
        (CHARACTER_RAW, {"kind": "character", "id": "", "refused": False}),
        # A refused character chip keeps the plain class; only its wrapper turns inactive, and the error icon shows.
        (
            {**CHARACTER_RAW, "text": "error cancel accessibility_new", "error": True},
            {"kind": "character", "id": "", "refused": True},
        ),
        ({"cls": "chip-container", "text": "cancel", "error": False, "src": ""}, None),
        ({"cls": "chip-container", "text": "cancel movie", "error": False, "src": ""}, None),
    ],
    ids=[
        "image",
        "refused image",
        "voice",
        "refused voice",
        "character",
        "refused character",
        "thumbnail not there yet",
        "a kind this driver does not attach",
    ],
)
def test_a_bar_chip_is_read_by_its_icon_its_thumbnail_and_the_error_icon(raw, chip):
    read = ingredients._bar_chip(raw)
    if chip is None:
        assert read["kind"] == "other" and read["refused"] is False
    else:
        assert {key: read[key] for key in chip} == chip


def test_a_bar_chip_never_carries_the_signed_url_it_was_read_from():
    assert "Signature" not in json.dumps(ingredients._bar_chip(_bar_raw()))


def _on_bar(kind, mention="", refused=False, name=""):
    """A chip as bar_chips reads it, with the hover card the page would show for it."""
    chip = {"kind": kind, "id": mention, "refused": refused, "seen": "cancel"}
    if kind == "voice":
        chip["name"] = name
        chip["card"] = f"{AUDIO_CAP_SAYS + ' ' if refused else ''}play_arrow 0:06 voice_selection {name}"
    elif refused:
        chip["card"] = FLOW_SAYS
    return chip


def _show_bar(monkeypatch, chips):
    """The ingredient bar a generate test reads from here on, its hover cards included."""
    monkeypatch.setattr(ingredients, "bar_chips", lambda page: _async([dict(chip) for chip in chips]))
    monkeypatch.setattr(
        ingredients,
        "_card",
        lambda page, index: _async([chips[index]["card"]] if "card" in chips[index] else []),
    )


GOOD_BAR = [_on_bar("character"), _on_bar("image", WORKFLOW)]
VOICE_BAR = [*GOOD_BAR, _on_bar("voice", name="Achird"), _on_bar("voice", name="LilyVoice")]


def test_the_bar_is_right_with_each_voice_asked_for_named_once():
    assert ingredients.bar_problems(VOICE_BAR, [THU, IMAGE, ACHIRD, LILY]) == []


@pytest.mark.parametrize(
    ("bar", "says"),
    [
        (VOICE_BAR[:3], "LilyVoice"),
        ([*VOICE_BAR[:3], _on_bar("voice", name="Leda")], "Leda"),
        ([*VOICE_BAR, _on_bar("voice", name="achird")], "2 chips"),
        ([*VOICE_BAR[:3], _on_bar("voice", name="LilyVoice", refused=True)], "refuses"),
        ([*VOICE_BAR[:3], _on_bar("voice")], "nothing"),
    ],
    ids=[
        "a voice never reached the bar",
        "another voice in its place",
        "a voice twice",
        "Flow refuses a voice",
        "a voice chip whose card named nothing",
    ],
)
def test_the_bar_is_wrong_when_its_voices_differ_from_what_was_asked(bar, says):
    problems = ingredients.bar_problems(bar, [THU, IMAGE, ACHIRD, LILY])
    assert problems and says in " ".join(problems), problems


def test_check_bar_reads_each_voices_name_off_its_card():
    page = _DialogPage([], bar=[_bar_raw(), _voice_raw("Achird"), _voice_raw("LilyVoice")])
    chips = asyncio.run(ingredients.check_bar(page, [IMAGE, ACHIRD, LILY]))
    assert [chip["name"] for chip in chips if chip["kind"] == "voice"] == ["Achird", "LilyVoice"]
    assert page.hovered is None


def test_check_bar_refuses_a_voice_flow_refuses_in_its_own_words():
    page = _DialogPage([], bar=[_bar_raw(), _voice_raw("Achird", refused=AUDIO_CAP_SAYS)])
    with pytest.raises(LookupError, match=re.escape(AUDIO_CAP_SAYS)) as refused:
        asyncio.run(ingredients.check_bar(page, [IMAGE, ACHIRD]))
    assert "play_arrow" not in str(refused.value)


def test_check_bar_reads_one_card_at_a_time_while_the_last_one_lingers():
    # Measured 2026-10-02 (out/al/t7_card.json): a card is gone 0.2 s after the pointer leaves its chip.
    page = _DialogPage(
        [], bar=[_bar_raw(), _voice_raw("Achird"), _voice_raw("LilyVoice")], card_lingers_ms=400
    )
    chips = asyncio.run(ingredients.check_bar(page, [IMAGE, ACHIRD, LILY]))
    assert [chip["name"] for chip in chips if chip["kind"] == "voice"] == ["Achird", "LilyVoice"]
    assert page.card() == [], "no card is left over the composer"


def test_check_bar_refuses_to_go_on_under_a_card_that_stays_open():
    # A card left over the composer could take the click on Start generation, and a click that never happened would
    # then be reported as "credits may already be spent" (review of plan AL, 2026-10-02). Before the click it is a
    # plain refusal.
    page = _DialogPage([], bar=[_bar_raw(), _voice_raw("Achird")], card_lingers_ms=10**9)
    with pytest.raises(LookupError, match="stayed open"):
        asyncio.run(ingredients.check_bar(page, [IMAGE, ACHIRD]))


def test_check_bar_reads_a_voices_card_beside_a_toast():
    page = _DialogPage([], bar=[_bar_raw(), _voice_raw("Achird")], standing=["Image added to your project"])
    chips = asyncio.run(ingredients.check_bar(page, [IMAGE, ACHIRD]))
    assert [chip["name"] for chip in chips if chip["kind"] == "voice"] == ["Achird"]


def test_the_bar_is_right_when_every_reference_has_its_one_chip_and_nothing_else_is_on_it():
    assert ingredients.bar_problems(GOOD_BAR, [THU, IMAGE]) == []
    assert ingredients.bar_problems([GOOD_BAR[1]], [IMAGE]) == []
    assert ingredients.bar_problems([GOOD_BAR[0]], [THU]) == []


@pytest.mark.parametrize(
    ("bar", "says"),
    [
        ([GOOD_BAR[0]], "peobj1.png"),
        ([GOOD_BAR[0], _on_bar("image", OTHER_IMAGE_WORKFLOW)], OTHER_IMAGE_WORKFLOW),
        ([*GOOD_BAR, _on_bar("image", OTHER_IMAGE_WORKFLOW)], OTHER_IMAGE_WORKFLOW),
        ([*GOOD_BAR, GOOD_BAR[1]], "2 chips"),
        ([GOOD_BAR[1]], "0 character chips for 1"),
        ([GOOD_BAR[0], *GOOD_BAR], "2 character chips for 1"),
        ([*GOOD_BAR, _on_bar("voice")], "voice"),
        ([*GOOD_BAR, _on_bar("other")], "none of"),
        ([GOOD_BAR[0], _on_bar("image", WORKFLOW, refused=True)], "refuses"),
        ([_on_bar("character", refused=True), GOOD_BAR[1]], "refuses"),
    ],
    ids=[
        "the image chip is gone",
        "another image in its place",
        "an image nobody asked for",
        "the image twice",
        "the character chip is gone",
        "a character twice",
        "a voice nobody asked for",
        "a chip of no known kind",
        "Flow refuses the image",
        "Flow refuses the character",
    ],
)
def test_the_bar_is_wrong_when_it_differs_from_what_was_asked(bar, says):
    problems = ingredients.bar_problems(bar, [THU, IMAGE])
    assert problems and says in " ".join(problems), problems


def test_check_bar_waits_for_thumbnails_and_then_passes_a_bar_that_is_right():
    page = _DialogPage([], bar=[{**_bar_raw(), "landed_at": 0}], thumb_after_ms=5_000)
    chips = asyncio.run(ingredients.check_bar(page, [IMAGE]))
    assert [(chip["kind"], chip["id"]) for chip in chips] == [("image", WORKFLOW)]
    assert 5_000 <= page.clock <= ingredients.BAR_WAIT_MS


def test_check_bar_refuses_a_refused_chip_in_flows_own_words():
    page = _DialogPage([], bar=[_bar_raw(refused=True)], refused=FLOW_SAYS)
    with pytest.raises(LookupError, match=re.escape(FLOW_SAYS)):
        asyncio.run(ingredients.check_bar(page, [IMAGE]))
    assert page.hovered is None


def test_check_bar_still_refuses_a_refused_chip_whose_card_never_shows():
    page = _DialogPage([], bar=[_bar_raw(refused=True)], refused=None)
    with pytest.raises(LookupError, match="refuses"):
        asyncio.run(ingredients.check_bar(page, [IMAGE]))


def test_the_dialog_and_bar_scripts_read_what_was_measured():
    assert ".asset-title" in ingredients._ROWS_JS and "asset-item-active" in ingredients._ROWS_JS
    # The error icon marks a refused chip of every kind; the class differs by kind (measured 2026-10-01).
    assert ".disabled-error-icon" in ingredients._BAR_JS
    assert ingredients.BAR == "flow-prompt-box flow-ingredient-bar button.chip-container"
    # The exact label: `aria-label*='ngredient'` also matches a chip, whose label is "Ingredient", and a click on a
    # chip removes it ($0 probe 2026-10-01).
    assert ingredients.ADD == "flow-prompt-box button[aria-label='Add ingredients to the prompt box']"
    assert ingredients.SEARCH == "input[aria-label='Search assets']"
    # No test runs these scripts in a page, so what each must look at is pinned by name: the badge that tells a voice
    # of your own, only panes that are on screen, and the element really under a control's middle.
    assert ".custom-voice-badge-icon')" in ingredients._ROWS_JS
    assert ".cdk-overlay-pane" in ingredients._CARD_JS and "offsetParent !== null" in ingredients._CARD_JS
    assert "elementFromPoint" in overlays._COVERING_JS and "top.contains(el)" in overlays._COVERING_JS
    assert "flow-ingredient-bar button.chip-container" in composer._LEFT_JS


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


def _submit_body(
    voices=None, images=(WORKFLOW,), key="veo_3_1_r2v_lite", prompt=PROMPT, rpcid="MZZa6b", then=()
):
    """An Ingredients submit in its measured shape (out/flow_research/body_ak-*.txt, 2026-10-01): the item carries the
    prompt, the images as [None, workflow id], the model key, and at [7] the voices when any ride. `then` adds one
    more item for each voice list in it: every measured body held one item, and several were never seen."""

    def item(spoken):
        made = [
            [None, None, [[[prompt]]]],
            [[None, w] for w in images],
            key,
            1,
            None,
            [None, None, None, None, "s" * 36],
        ]
        return made if spoken is None else [*made, None, [[voice] for voice in spoken]]

    items = [item(voices), *(item(spoken) for spoken in then)]
    inner = json.dumps([items, [None, 22, None, None, None, "p-1"], ["x" * 36, 2]])
    return "f.req=" + quote_plus(json.dumps([[[rpcid, inner, None, "generic"]]])) + "&at=AJpMio%3A1790000000"


def test_request_voices_reads_the_voices_a_submit_carries_off_their_own_field():
    assert ingredients.request_voices(_submit_body([LILY_VOICE, "achird"])) == [[LILY_VOICE, "achird"]]
    assert ingredients.request_voices(_submit_body()) == [[]]
    # A prompt that names a voice carries no voice.
    assert ingredients.request_voices(_submit_body(prompt="achird and LilyVoice speak")) == [[]]


def test_request_voices_keeps_each_item_of_a_submit_apart():
    # Review of plan AL: every saved body holds one item. Should Flow send one per clip for x2, a list summed over
    # the items would count each voice twice, so each item answers for itself.
    body = _submit_body(["achird"], then=[["achird"], None])
    assert ingredients.request_voices(body) == [["achird"], ["achird"], []]


@pytest.mark.parametrize(
    "body",
    [
        "f.req=" + quote_plus("not json"),
        "",
        _submit_body(["achird"], rpcid="WuwhI"),
        "f.req=" + quote_plus("[]"),
    ],
    ids=["not json", "empty", "not a submit", "no frame"],
)
def test_request_voices_says_it_cannot_tell_rather_than_guess(body):
    assert ingredients.request_voices(body) is None


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
        return {"mentions": self.mentions, "bar": 0, "text": ""}


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

    async def fetch_720(session, record, stem, attempts=6, *, project_id):
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


def test_a_retry_is_named_on_the_row_written_before_the_click_and_only_there(monkeypatch, tmp_path):
    # Plan AM, I-money-4: the server reads this link to refuse a second retry of one refused job, so it has to be
    # on record before the click, not after.
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", f"Thu {PROMPT}")])

    _submit(_SubmitSession(log), tmp_path, log, retry_of="job-0")

    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows()
    assert [(row["status"], row.get("retry_of")) for row in rows] == [("submitted", "job-0"), ("done", None)]


def test_the_row_keeps_as_much_of_the_prompt_as_a_retry_is_held_to(monkeypatch, tmp_path):
    # outcome.retry_refusal compares a retry's prompt with this row, cut at the same length.
    from video import outcome

    log = []
    _install(monkeypatch, tmp_path, log)
    long = "a teapot on a table, " * 20
    assert len(long) > outcome.PROMPT_KEPT

    with pytest.raises(RuntimeError):
        _submit(_SubmitSession(log), tmp_path, log, prompt=long)

    row = gen.Ledger(tmp_path / "ledger.jsonl").rows()[0]
    assert row["status"] == "submitted" and row["prompt"] == long[: outcome.PROMPT_KEPT]


def test_a_job_that_retries_nothing_carries_no_retry_field(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log, fresh=[_video("w-new", f"Thu {PROMPT}")])

    _submit(_SubmitSession(log), tmp_path, log)

    assert all("retry_of" not in row for row in gen.Ledger(tmp_path / "ledger.jsonl").rows())


def test_the_ingredients_driver_hands_the_retry_link_to_the_money_path(monkeypatch, tmp_path):
    log, handed = [], {}
    _paid_world(monkeypatch, log, recipe=KEPT_ONE_VOICE)

    async def submit(session, project_id, **kwargs):
        handed.update(kwargs)
        return {"job_id": kwargs["job_id"], "media_id": "m-new", "spent": 10, "body_check": {"ok": True}}

    monkeypatch.setattr(ingredients.composer, "_submit", submit)

    _paid_run(tmp_path, retry_of="job-0")
    assert handed["retry_of"] == "job-0"
    handed.clear()
    _paid_run(tmp_path)
    assert handed["retry_of"] is None


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
        # The bar is read too: images and voices are chips there, not mentions (review of plan AL, 2026-10-02).
        "composer_left": {"mentions": 0, "bar": 0, "text": ""},
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


@pytest.mark.parametrize(
    ("reason", "kind", "may_retry"),
    [
        ([["PUBLIC_ERROR_AUDIO_FILTERED"]], "character", True),
        ([], "video", True),
        ([["PUBLIC_ERROR_UNSAFE_GENERATION"]], "video", False),
        ([["PUBLIC_ERROR_AUDIO_FILTERED"]], "story", False),
    ],
    ids=["the audio filter", "no reason", "unsafe", "a kind with no retry_of"],
)
def test_a_refusal_that_may_be_retried_says_how_and_one_that_may_not_says_never(
    monkeypatch, tmp_path, reason, kind, may_retry
):
    # Plan AM: the error's own words must not forbid the retry its outcome allows (ak-v3 passed on an identical
    # retry; the audio filter is random on Veo).
    log = []
    replies = {"click": [SUBMIT_REPLY, _status_reply(2)], "poll": [_status_reply(4, extra=reason)]}
    _install(monkeypatch, tmp_path, log, balance_reads=(200, 200, 200), replies=replies)

    with pytest.raises(RuntimeError, match="Flow refused this job and charged nothing") as raised:
        _submit(_SubmitSession(log), tmp_path, log, strict_output=True, kind=kind)

    said = str(raised.value)
    assert ("retry_of" in said) is may_retry, said
    assert ("do not retry the same inputs" in said) is (not may_retry), said


def test_submit_never_says_flow_refused_a_job_flow_last_reported_running(monkeypatch, tmp_path):
    # The story path settles an unmoved balance as failed whatever Flow last said; only status 4 is a refusal.
    log = []
    _install(
        monkeypatch,
        tmp_path,
        log,
        balance_reads=(200, 200, 200),
        replies={"click": [SUBMIT_REPLY, _status_reply(2)]},
    )
    with pytest.raises(RuntimeError, match="nothing was generated within 0s, spent 0 credits") as raised:
        _submit(_SubmitSession(log), tmp_path, log)
    assert "refused" not in str(raised.value)


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


def test_submit_keeps_waiting_for_a_clip_when_flow_has_said_nothing_yet(monkeypatch, tmp_path):
    log = []
    _install(monkeypatch, tmp_path, log)
    before = [_video("w-before", "old")]
    polls = iter([before, before, [*before, _video("w-late", PROMPT)]])

    async def snapshot(session, project_id, attempts=4):
        log.append("snapshot")
        return next(polls), set()

    real_sleep = asyncio.sleep

    async def sleep(seconds):
        await real_sleep(0)

    monkeypatch.setattr(composer, "snapshot", snapshot)
    monkeypatch.setattr(composer.asyncio, "sleep", sleep)
    result = _submit(_SubmitSession(log), tmp_path, log, strict_output=True, wait=600.0)
    assert result["media_id"] == "m-w-late" and log.count("snapshot") == 3


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


def _judged(responses, about=None, editor=False, source_media=None):
    """Feed replies straight to a FlowReplies and return its report."""

    async def run():
        replies = (
            composer.FlowReplies(editor=editor, source_media=source_media)
            if source_media
            else composer.FlowReplies(editor=editor)
        )
        replies.about(about)
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
    said = composer.flow_said(flow)
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
    said = composer.flow_said(flow)
    assert flow["statuses"] == [6, 7, 2] and flow["unmeasured"]["status"] == 7
    assert "last reported status 2" in said and "unmeasured status 7" in said and "failed" not in said
    last_odd = composer.flow_said(_judged([SUBMIT_REPLY, _status_reply(5)]))
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
    said = composer.flow_said(_judged([SUBMIT_REPLY, _status_reply(7), _status_reply(5)]))
    assert said.startswith(
        f"Flow last reported unmeasured status 5 for workflow {JOB_WORKFLOW} after unmeasured status 7"
    )


def test_flow_replies_call_status_4_a_failure_with_the_reason_it_carried():
    # Measured 2026-09-17 (plan E, L4): statuses 6, 2, 4 with PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED, 0 credits.
    filtered = _status_reply(4, extra=[["PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED"]])
    flow = _judged([SUBMIT_REPLY, _status_reply(2), filtered])
    assert (flow["statuses"], flow["unmeasured"]) == ([6, 2, 4], None)
    assert composer.flow_said(flow).startswith(
        f"Flow failed workflow {JOB_WORKFLOW} (status 4) with PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED"
    )


def test_flow_replies_never_call_a_statusless_last_record_unmeasured():
    said = composer.flow_said(_judged([SUBMIT_REPLY, _status_reply(2), _status_reply(None)]))
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
        self.clock = 0

    async def wait_for_timeout(self, ms):
        self.clock += ms

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

    monkeypatch.setattr(ingredients.parsers, "voices_from_listing", lambda payload: PRESETS)
    monkeypatch.setattr(ingredients.parsers, "custom_voices", lambda payload: CUSTOMS)

    resolved = []
    shown = []
    really_resolve = ingredients.resolve

    def resolve(*args, **kwargs):
        resolved[:] = really_resolve(*args, **kwargs)
        return list(resolved)

    async def attach_image(page, reference):
        log.append(f"attach_image {reference.id}")
        return {"kind": "media", "id": min(reference.mention_ids), "text": reference.title}

    async def attach_voice(page, reference):
        log.append(f"attach_voice {reference.id}")
        return {"kind": "voice", "id": reference.id, "text": reference.title}

    def on_bar(reference):
        if reference.kind == "entity":
            return _on_bar("character")
        if reference.kind == "voice":
            return _on_bar("voice", name=reference.title)
        return _on_bar("image", min(reference.mention_ids))

    async def bar_chips(page):
        """The bar as Flow shows it once everything asked for is attached: a mention puts its character there too."""
        shown[:] = [on_bar(reference) for reference in resolved]
        return [dict(chip) for chip in shown]

    async def card(page, index):
        return [shown[index]["card"]] if "card" in shown[index] else []

    monkeypatch.setattr(ingredients, "resolve", resolve)
    monkeypatch.setattr(ingredients, "attach_image", attach_image)
    monkeypatch.setattr(ingredients, "attach_voice", attach_voice)
    monkeypatch.setattr(ingredients, "bar_chips", bar_chips)
    monkeypatch.setattr(ingredients, "_card", card)

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

    async def pin_duration(page, seconds=None):
        log.append("pin")
        assert seconds == ingredients.SECONDS, f"the default run pinned {seconds}s, not 8s"
        return "pinned"

    async def attach(page, reference):
        log.append(f"attach {reference.id}")
        return {"kind": reference.kind, "id": min(reference.mention_ids), "text": reference.title}

    async def chips_on(page):
        return [{}]

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
    # The character is mentioned first: the '@' picker is the "+" dialog and keeps the category that dialog last
    # showed, under which it offers no character (measured 2026-10-01, twice). The image goes in through the dialog.
    assert log[3:] == [
        f"settings omni-flash 9:16 {[ENTITY, MEDIA]}",
        "pin",
        f"click {ingredients.BOX}",
        f"attach {ENTITY}",
        f"attach_image {MEDIA}",
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
    with pytest.raises(LookupError, match="2 mention chips for 1 characters"):
        asyncio.run(captured["setup"](session))


def _prepared(monkeypatch, log, on_page, *, box_text=f"Thu {PROMPT}", caret_lands=True, bar=None, voices=()):
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
    if bar is not None:
        _show_bar(monkeypatch, bar)
    asyncio.run(
        ingredients.generate(
            object(),
            "p-1",
            prompt=PROMPT,
            characters=[ENTITY],
            media_ids=[MEDIA],
            dry_run=True,
            voices=voices,
        )
    )
    page = _GeneratePage(log, box_text=box_text, caret_lands=caret_lands)
    return captured["setup"], captured["verify"], type("Session", (), {"page": page})()


# What the prompt box holds: the character's mention chip. The image is a chip on the ingredient bar (GOOD_BAR).
ON_PAGE = [{"kind": "entity", "id": ENTITY, "entity": ENTITY, "text": "Thu"}]


def test_generate_setup_refuses_a_prompt_holding_fewer_chips_than_references(monkeypatch):
    setup, _, session = _prepared(monkeypatch, [], [])
    with pytest.raises(LookupError, match="0 mention chips for 1 characters"):
        asyncio.run(setup(session))


def test_generate_setup_refuses_a_box_it_cannot_put_the_caret_at_the_end_of(monkeypatch):
    setup, _, session = _prepared(monkeypatch, [], ON_PAGE, caret_lands=False)
    with pytest.raises(LookupError, match="caret"):
        asyncio.run(setup(session))


@pytest.mark.parametrize(
    "bar",
    [
        [GOOD_BAR[0]],
        [GOOD_BAR[0], _on_bar("image", WORKFLOW, refused=True)],
        [*GOOD_BAR, _on_bar("voice")],
    ],
    ids=["the image never reached the bar", "Flow refuses the image", "a chip nobody asked for"],
)
def test_generate_setup_refuses_a_bar_that_is_not_what_was_asked(monkeypatch, bar):
    log = []
    setup, _, session = _prepared(monkeypatch, log, ON_PAGE, bar=bar)
    with pytest.raises(LookupError, match="refusing to generate"):
        asyncio.run(setup(session))
    assert "caret end" not in log


def test_generate_setup_says_in_flows_own_words_why_a_chip_on_the_bar_is_refused(monkeypatch):
    bar = [GOOD_BAR[0], _on_bar("image", WORKFLOW, refused=True)]
    setup, _, session = _prepared(monkeypatch, [], ON_PAGE, bar=bar)
    with pytest.raises(LookupError, match=re.escape(FLOW_SAYS)):
        asyncio.run(setup(session))


def test_generate_verify_passes_the_chips_setup_attached_and_reports_the_prompt_it_will_send(monkeypatch):
    setup, verify, session = _prepared(monkeypatch, [], ON_PAGE)
    asyncio.run(setup(session))
    assert asyncio.run(verify(session)) == {
        "chips": [
            {"kind": "entity", "id": ENTITY, "text": "Thu"},
            {"kind": "media", "id": WORKFLOW, "text": "peobj1.png"},
        ],
        "prompt_text": f"Thu {PROMPT}",
    }


@pytest.mark.parametrize(
    ("at_click", "box_text"),
    [
        ([], PROMPT),
        ([{**ON_PAGE[0], "id": OTHER, "entity": OTHER}], f"Thu {PROMPT}"),
        ([{**ON_PAGE[0], "entity": ""}], f"Thu {PROMPT}"),
        ([{**ON_PAGE[0], "entity": OTHER}], f"Thu {PROMPT}"),
        (
            [*ON_PAGE, {"kind": "media", "id": "w-stray", "entity": "", "text": "x.png"}],
            f"Thu x.png {PROMPT}",
        ),
        ([*ON_PAGE, ON_PAGE[0]], f"Thu Thu {PROMPT}"),
        (ON_PAGE, "Thu stands in a sunny"),
        (ON_PAGE, f"Thu {PROMPT[: -len(' camera')]}"),
        (ON_PAGE, f"Thu {PROMPT} {PROMPT}"),
        (ON_PAGE, f"words an earlier run left Thu {PROMPT}"),
        (ON_PAGE, f"Thu @Thu {PROMPT}"),
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
    box_text = f"Thu  \n{PROMPT} "
    setup, verify, session = _prepared(monkeypatch, [], ON_PAGE, box_text=box_text)
    asyncio.run(setup(session))
    assert asyncio.run(verify(session))["prompt_text"] == box_text


def test_generate_verify_ignores_letter_case(monkeypatch):
    # innerText applies CSS text-transform while a chip's textContent does not.
    box_text = f"THU {PROMPT}"
    setup, verify, session = _prepared(monkeypatch, [], ON_PAGE, box_text=box_text)
    asyncio.run(setup(session))
    assert asyncio.run(verify(session))["prompt_text"] == box_text


@pytest.mark.parametrize(
    "at_click",
    [
        [GOOD_BAR[0]],
        [GOOD_BAR[0], _on_bar("image", OTHER_IMAGE_WORKFLOW)],
        [*GOOD_BAR, _on_bar("image", OTHER_IMAGE_WORKFLOW)],
        [*GOOD_BAR, GOOD_BAR[1]],
        [GOOD_BAR[1]],
        [*GOOD_BAR, _on_bar("voice")],
        [*GOOD_BAR, _on_bar("other")],
        [GOOD_BAR[0], _on_bar("image", WORKFLOW, refused=True)],
        [_on_bar("character", refused=True), GOOD_BAR[1]],
    ],
    ids=[
        "the image chip is gone",
        "another image in its place",
        "an image nobody asked for",
        "the image twice",
        "the character chip is gone",
        "a voice nobody asked for",
        "a chip of no known kind",
        "Flow refuses the image",
        "Flow refuses the character",
    ],
)
def test_generate_verify_refuses_a_bar_that_changed_after_setup(monkeypatch, at_click):
    # I3 (plan AL): the price line reads the same with a refused chip on the bar (10 on Veo 3.1 Lite with a fourth
    # image, measured 2026-10-01), so only this read stands between a refused or missing ingredient and the click.
    setup, verify, session = _prepared(monkeypatch, [], ON_PAGE)
    asyncio.run(setup(session))
    _show_bar(monkeypatch, at_click)
    with pytest.raises(LookupError, match="refusing to spend"):
        asyncio.run(verify(session))


def test_generate_verify_says_in_flows_own_words_why_a_chip_is_refused_at_the_click(monkeypatch):
    setup, verify, session = _prepared(monkeypatch, [], ON_PAGE)
    asyncio.run(setup(session))
    _show_bar(monkeypatch, [GOOD_BAR[0], _on_bar("image", WORKFLOW, refused=True)])
    with pytest.raises(LookupError, match=re.escape(FLOW_SAYS)):
        asyncio.run(verify(session))


def test_generate_setup_attaches_the_voices_last_and_reports_them_with_the_other_chips(monkeypatch):
    # The order T1b measured with a quote and no refusal (2026-10-01): the character's mention, then the image, then
    # the voice, both through the "+" dialog.
    log = []
    setup, verify, session = _prepared(monkeypatch, log, ON_PAGE, voices=["Achird", "LilyVoice"])
    extra = asyncio.run(setup(session))
    assert [line for line in log if line.startswith("attach")] == [
        f"attach_image {MEDIA}",
        "attach_voice achird",
        f"attach_voice {LILY_VOICE}",
    ]
    voices = [chip for chip in extra["chips"] if chip["kind"] == "voice"]
    assert voices == [
        {"kind": "voice", "id": "achird", "text": "Achird"},
        {"kind": "voice", "id": LILY_VOICE, "text": "LilyVoice"},
    ]
    read = asyncio.run(verify(session))
    assert [chip for chip in read["chips"] if chip["kind"] == "voice"] == voices


@pytest.mark.parametrize(
    "at_click",
    [
        [*GOOD_BAR, _on_bar("voice", name="Achird")],
        [*GOOD_BAR, _on_bar("voice", name="Achird"), _on_bar("voice", name="Leda")],
        [*GOOD_BAR, _on_bar("voice", name="Achird"), _on_bar("voice", name="LilyVoice", refused=True)],
    ],
    ids=["a voice is gone", "another voice in its place", "Flow refuses a voice"],
)
def test_generate_verify_refuses_voices_that_changed_after_setup(monkeypatch, at_click):
    setup, verify, session = _prepared(monkeypatch, [], ON_PAGE, voices=["Achird", "LilyVoice"])
    asyncio.run(setup(session))
    _show_bar(monkeypatch, at_click)
    with pytest.raises(LookupError, match="refusing to spend"):
        asyncio.run(verify(session))


def test_generate_from_images_alone_types_the_prompt_into_a_box_holding_no_chip(monkeypatch):
    log = []
    captured = _generate_world(monkeypatch, log)

    async def nothing(*args):
        return "pinned"

    async def mention(page, reference):
        raise AssertionError("an image is never mentioned with '@'")

    monkeypatch.setattr(ingredients, "apply_settings", nothing)
    monkeypatch.setattr(ingredients, "pin_duration", nothing)
    monkeypatch.setattr(ingredients, "attach", mention)
    monkeypatch.setattr(ingredients, "chips_on", lambda page: _async([]))
    asyncio.run(
        ingredients.generate(object(), "p-1", prompt=PROMPT, characters=[], media_ids=[MEDIA], dry_run=True)
    )
    session = type("Session", (), {"page": _GeneratePage(log, box_text=PROMPT)})()
    extra = asyncio.run(captured["setup"](session))
    assert extra["chips"] == [{"kind": "media", "id": WORKFLOW, "text": "peobj1.png"}]
    assert asyncio.run(captured["verify"](session)) == {
        "chips": [{"kind": "media", "id": WORKFLOW, "text": "peobj1.png"}],
        "prompt_text": PROMPT,
    }


async def _async(value):
    return value


def test_the_option_script_reads_title_and_kind_and_the_chip_script_reads_kind_and_mention_id():
    assert ".asset-title" in ingredients._OPTIONS_JS and ".type-subtitle" in ingredients._OPTIONS_JS
    assert "data-reference-type" in ingredients._CHIPS_JS and "data-mention-id" in ingredients._CHIPS_JS
    assert re.fullmatch(r"flow-prompt-box \.mention-chip", ingredients.CHIP)


def test_a_job_whose_submit_reply_names_nothing_is_named_by_the_caller():
    """Measured 2026-09-28 on a paid clip_edit (job plan-n-edit-1, 20 credits, 79 to 59): the editor submits with
    `jIps6`, which is in neither of gflow's rpc sets and appears nowhere in its source, so the listener heard 20
    rpcids and still reported workflow_id null, statuses [], reasons [] for a job that ran fine. The editor's own
    listing knows the workflow (that run wrote d2327819-... into its outputs), so it says so, and the report says
    the name came from there. No media id comes with it: on the editor path the output keeps the SOURCE clip's
    media id (that run's source and output are both 26f0e503-...), so counting it as the job's own would let a code
    about the source clip be read as this job's."""
    flow = _judged([_status_reply(4)], about=JOB_WORKFLOW, editor=True)

    assert flow["workflow_id"] == JOB_WORKFLOW
    assert flow["named_by_caller"] is True
    assert flow["media_id"] is None
    assert flow["statuses"] == [4]
    assert composer.flow_said(flow).startswith(f"Flow failed workflow {JOB_WORKFLOW}")


def test_a_named_job_that_flow_says_it_failed_ends_the_wait():
    """The one control-flow consequence of a name on a job already paid for (review F4): without it,
    `reported_failed` was structurally dead on the editor path, since no submit reply ever named the workflow."""

    async def run():
        replies = composer.FlowReplies(editor=True)
        replies.on_response(_status_reply(4))
        replies.about(JOB_WORKFLOW)
        await replies.report()
        return replies.reported_failed()

    assert asyncio.run(run()) is True


def test_a_named_job_that_flow_says_is_running_keeps_waiting():
    """The counter-case, so the test above cannot pass by returning True for everything: money is already spent
    here, and abandoning a job Flow never failed loses the asset it paid for."""

    async def run():
        replies = composer.FlowReplies(editor=True)
        replies.on_response(_status_reply(2))
        replies.about(JOB_WORKFLOW)
        await replies.report()
        return replies.reported_failed()

    assert asyncio.run(run()) is False


def test_a_name_from_the_caller_is_never_a_word_from_flow():
    """The listing is not Flow's reply: named a workflow no reply mentions, the reader must still report that it
    heard nothing, not dress the caller's own id up as something Flow said."""
    flow = _judged([_status_reply(3, workflow=OTHER_WORKFLOW)], about=JOB_WORKFLOW, editor=True)

    assert flow["workflow_id"] is None
    assert flow["named_by_caller"] is False
    assert flow["statuses"] == []
    assert composer.flow_said(flow).startswith("no submit reply from Flow was heard")


def test_a_reply_under_the_editors_submit_rpc_is_read_and_not_dropped_as_noise():
    """What was lost before: the RECORDS inside a `jIps6` reply, always, because the rpcid was in neither of
    gflow's sets, so no status of the job could come from its own submit reply and a code in there could at best
    be listed apart as another workflow's. The live payload SHAPE is still unmeasured (that paid run kept no
    body): what this pins is that the body is kept whatever its size and searched wherever the record sits."""
    big = _FlowReply("jIps6", [None, 1, [[JOB_MEDIA]], ["x" * 30_000, [_flow_record(4)]]])

    flow = _judged([big], about=JOB_WORKFLOW, editor=True)

    assert composer._about_the_job("jIps6", composer.FlowReplies(editor=True).job_rpcs) is True
    assert flow["workflow_id"] == JOB_WORKFLOW
    assert flow["statuses"] == [4]


def test_the_editors_submit_reply_cannot_hand_the_job_another_workflows_record():
    """An edit is submitted against a source clip, so its replies can carry records that are not the job's. The
    job's identity stays the caller's: a record under the editor's rpc only ever adds to the job it names."""
    source = _FlowReply("jIps6", [None, 1, [_flow_record(3, workflow=OTHER_WORKFLOW)]])

    flow = _judged([source], about=JOB_WORKFLOW, editor=True)

    assert flow["workflow_id"] is None
    assert flow["statuses"] == []


def test_the_gen_path_never_hears_the_editors_submit_rpc():
    """The gen path's replies are the measured ones, and `jIps6` is not among them: an unmeasured rpc must not be
    able to add a status, a reason or an early stop to a job the composer submitted (review F3)."""
    editor_reply = _FlowReply("jIps6", [None, 1, [_flow_record(4)]])

    flow = _judged([SUBMIT_REPLY, _status_reply(2), editor_reply])

    assert flow["statuses"] == [6, 2]
    assert flow["reasons"] == []
    assert composer.flow_said(flow).startswith(f"Flow last reported status 2 for workflow {JOB_WORKFLOW}")


def test_a_gen_job_is_still_named_by_flows_own_submit_reply():
    """The gen path passes no name: what it hears must not change, and a name must never outrank the reply."""
    assert _judged([SUBMIT_REPLY, _status_reply(2)])["workflow_id"] == JOB_WORKFLOW
    assert _judged([SUBMIT_REPLY, _status_reply(2)])["statuses"] == [6, 2]
    assert _judged([SUBMIT_REPLY, _status_reply(2)])["named_by_caller"] is False
    assert _judged([SUBMIT_REPLY, _status_reply(2)], about=OTHER_WORKFLOW)["workflow_id"] == JOB_WORKFLOW


def test_a_named_job_no_reply_mentions_leaves_a_trail_worth_measuring():
    """Measured 2026-09-28 on three paid editor runs (60 credits): the caller named the workflow from the listing and
    NOT ONE reply heard between the click and the outcome carried a record for it, so the reader honestly reported
    nothing. What it could not say is which workflows those replies DID name, or what the editor's own submit reply
    held, and without that the question costs another paid run to ask again."""
    editor = _FlowReply("jIps6", [None, 1, [_flow_record(6, workflow=OTHER_WORKFLOW)]])

    flow = _judged([editor], about=JOB_WORKFLOW, editor=True)

    assert flow["workflow_id"] is None
    assert flow["told_unmatched"]["workflow"] == JOB_WORKFLOW
    assert flow["told_unmatched"]["records_named"] == [f"jIps6:{OTHER_WORKFLOW}"]
    assert OTHER_WORKFLOW in flow["told_unmatched"]["editor_reply"]


def test_the_trail_is_absent_when_flow_did_name_the_job():
    """A diagnostic that is always there is one more thing to read on the rows that need reading least."""
    assert _judged([_status_reply(3)], about=JOB_WORKFLOW, editor=True)["told_unmatched"] is None
    assert _judged([SUBMIT_REPLY, _status_reply(3)])["told_unmatched"] is None
    assert _judged([_status_reply(3)])["told_unmatched"] is None


def test_the_trail_carries_no_signed_url_out_of_the_editors_reply():
    """It goes into a ledger file: a signed lh3 query in there is session material on disk."""
    token = "A" * 200
    editor = _FlowReply(
        "jIps6", [None, 1, [f"https://lh3.googleusercontent.com/x?Expires=1&Signature={token}"]]
    )

    trail = _judged([editor], about=JOB_WORKFLOW, editor=True)["told_unmatched"]

    assert token not in trail["editor_reply"]
    assert "<token>" in trail["editor_reply"] or "<redacted:url>" in trail["editor_reply"]


def test_resolve_says_so_when_it_is_given_a_character_name_instead_of_its_entity_id():
    """Measured 2026-09-28 in the price batch: `characters=["Mia"]` got "Mia is not a character of this project" while
    Mia was one; the parameter takes entity ids. The refusal has to say what went wrong and hand over the id."""
    with pytest.raises(LookupError) as caught:
        ingredients.resolve([_character()], [], [], ["Thu"], [], "omni-flash")

    said = str(caught.value)
    assert "name" in said and ENTITY in said, said
    assert "is not a character of this project" not in said, said


def test_a_name_two_characters_share_is_not_answered_with_ids_that_would_be_refused():
    """Review of plan S: "pass A or B" led to a second refusal, since the picker cannot tell two same-named
    characters apart whichever id is passed."""
    twins = [_character(), _character(entity=OTHER, name=" THU ")]

    with pytest.raises(LookupError, match="rename one"):
        ingredients.resolve(twins, [], [], ["Thu"], [], "omni-flash")


OTHER_MEDIA = "7c3b2a19-5d4e-4f60-8a71-b2c3d4e5f6a7"


def _edit_record(status, *, workflow=JOB_WORKFLOW, media=JOB_MEDIA, version="CAI"):
    """An Omni edit's record, measured 2026-09-28 in the jIps6 replies of jobs price2-edit and plan-t-edit-1: the
    generation-record shape, the edit's workflow first, the SOURCE clip's media id third, and in the fourth field the
    base64 protobuf {1: version} of that clip, "CAI" (2) for its first edit and "CAM" (3) for its second."""
    record = _flow_record(status, workflow=workflow)
    record[2], record[3] = media, version
    return record


def _edit_submit(*records):
    """jIps6 as it answered ONE paid edit (price2-edit): null, the balance after the charge, the source clip, then a list
    whose first record was the job's. The trail was cut at 1,200 characters, so whether that list can also carry the
    clip's earlier edits is unmeasured, which is why the reader never trusts it alone to end a wait."""
    return _FlowReply(
        "jIps6",
        [
            None,
            941,
            [[JOB_MEDIA, None, None, ["Wooden sailboat model on desk"], FLOW_PROJECT]],
            list(records),
        ],
    )


def test_the_editor_names_its_job_from_the_one_edit_record_its_submit_reply_carries():
    """Measured 2026-09-28 (job price2-edit, 20 credits): the editor's submit reply held the job's own record, typed
    "CAI", and the reader, which accepted only "CAE", heard nothing for four paid edits in a row. Named at submit, the
    job is known before any listing shows it, which is the only way to hear why Flow drops one that never appears."""
    flow = _judged([_edit_submit(_edit_record(6)), _status_reply(2)], editor=True, source_media=JOB_MEDIA)

    assert flow["workflow_id"] == JOB_WORKFLOW, flow
    assert flow["named_by_editor_submit"] is True and flow["named_by_caller"] is False, flow
    assert flow["statuses"] == [6, 2], flow


def test_the_gen_reader_learns_nothing_from_an_edit_submit_reply():
    """The gen path's replies are the measured ones; an edit's record type must not change what it hears."""
    flow = _judged([_edit_submit(_edit_record(6)), _status_reply(2)])

    assert flow["workflow_id"] is None and flow["statuses"] == [], flow


def test_an_edit_record_on_another_clip_does_not_name_the_job():
    """An edit keeps its SOURCE clip's media id (measured on 26f0e503 and b59ab4e8), so a record on another clip is
    another job's, whatever rpc it arrives under."""
    flow = _judged([_edit_submit(_edit_record(6, media=OTHER_MEDIA))], editor=True, source_media=JOB_MEDIA)

    assert flow["workflow_id"] is None, flow


def test_two_edit_records_on_the_source_clip_name_no_job():
    """Two candidates is a guess, and a guessed identity puts another job's status and reasons in this job's row."""
    both = _edit_submit(_edit_record(6), _edit_record(6, workflow=OTHER_WORKFLOW))

    flow = _judged([both], editor=True, source_media=JOB_MEDIA)

    assert flow["workflow_id"] is None, flow


def test_an_edit_record_in_a_status_reply_counts_for_the_workflow_the_listing_named():
    """A boundary, not a measurement (review of plan T: no status reply has yet been seen carrying an edit's record):
    if one does, an edit named by the listing reads its status from it, as a generation reads its "CAE" record."""
    flow = _judged([_FlowReply("as29s", [None, 1, [_edit_record(3)]])], about=JOB_WORKFLOW, editor=True)

    assert flow["workflow_id"] == JOB_WORKFLOW and flow["statuses"] == [3], flow


def test_the_gen_reader_never_counts_an_edit_record_as_its_jobs():
    """Pins the boundary plan T promised rather than a measured event: the gen path hears only "CAE" records, so an
    edit record carrying the gen job's own workflow id adds nothing to its statuses. Without this, a reader that
    parsed "CAI" everywhere passed every other test (mutant T-M2, 2026-09-28)."""
    edit_status = _FlowReply("as29s", [None, 1, [_edit_record(4)]])

    flow = _judged([SUBMIT_REPLY, _status_reply(2), edit_status])

    assert flow["statuses"] == [6, 2], flow


EARLIER_EDIT = "3e2d1c0b-9a8f-4e7d-8c6b-5a4f3e2d1c0b"


def test_a_listing_and_an_edit_record_that_disagree_name_no_job():
    """Review of plan T, F1: the one edit record in jIps6 silently beat the workflow the listing named, so an earlier
    failed edit of the same clip could lend its status 4 to the paid job and end its wait. When the two disagree the
    reader names neither and says so."""
    flow = _judged(
        [
            _edit_submit(_edit_record(4, workflow=EARLIER_EDIT)),
            _FlowReply("as29s", [None, 1, [_edit_record(2)]]),
        ],
        about=JOB_WORKFLOW,
        editor=True,
        source_media=JOB_MEDIA,
    )

    assert flow["workflow_id"] is None, flow
    assert flow["identity_conflict"] == sorted([EARLIER_EDIT, JOB_WORKFLOW]), flow
    # Neither source may be reported as the one that named the job, since neither was adopted.
    assert flow["named_by_caller"] is False and flow["named_by_editor_submit"] is False, flow


def test_an_edit_record_alone_never_ends_the_wait():
    """Named only by an edit record nobody has confirmed, a status 4 may be an earlier edit's: the ledger records it,
    but the wait for a paid job does not end on it."""

    async def run():
        replies = composer.FlowReplies(editor=True, source_media=JOB_MEDIA)
        replies.on_response(_edit_submit(_edit_record(4)))
        await replies.report()
        return replies.reported_failed(), await replies.report()

    stopped, flow = asyncio.run(run())

    assert flow["workflow_id"] == JOB_WORKFLOW and flow["statuses"] == [4], flow
    assert stopped is False, "an unconfirmed name ended the wait on a paid job"


def test_an_edit_record_the_listing_confirms_may_end_the_wait():
    """The counter-case: both sources name the same workflow, so Flow's status 4 is this job's."""

    async def run():
        replies = composer.FlowReplies(editor=True, source_media=JOB_MEDIA)
        replies.on_response(_edit_submit(_edit_record(4)))
        replies.about(JOB_WORKFLOW)
        await replies.report()
        return replies.reported_failed(), await replies.report()

    stopped, flow = asyncio.run(run())

    assert stopped is True, flow
    assert flow["named_by_caller"] is True and flow["named_by_editor_submit"] is True, flow


def test_only_the_editors_submit_reply_can_name_an_edit_job():
    """Review of plan T, M1: an edit record under any other rpc (a status reply, extend's submit) names nothing."""
    flow = _judged([_FlowReply("as29s", [None, 1, [_edit_record(2)]])], editor=True, source_media=JOB_MEDIA)

    assert flow["workflow_id"] is None, flow


def test_a_generation_record_under_the_editors_submit_rpc_is_not_an_edit_record():
    """Review of plan T, M3: the name comes from a record typed "CAI" only."""
    flow = _judged([_edit_submit(_flow_record(6))], editor=True, source_media=JOB_MEDIA)

    assert flow["workflow_id"] is None, flow


def test_flows_own_submit_reply_outranks_an_edit_record():
    """Review of plan T, M11: gflow's submit reply is the measured source and wins over everything."""
    flow = _judged(
        [SUBMIT_REPLY, _edit_submit(_edit_record(6, workflow=EARLIER_EDIT))],
        editor=True,
        source_media=JOB_MEDIA,
    )

    assert flow["workflow_id"] == JOB_WORKFLOW, flow
    assert flow["named_by_editor_submit"] is False and flow["named_by_caller"] is False, flow


def test_the_ledger_lists_every_edit_record_the_submit_reply_carried():
    """The next paid edit measures what one truncated sample could not: whether jIps6 carries the clip's earlier edits,
    and which status an edit record really holds at submit."""
    flow = _judged(
        [_edit_submit(_edit_record(6), _edit_record(3, workflow=EARLIER_EDIT, media=OTHER_MEDIA))],
        editor=True,
        source_media=JOB_MEDIA,
    )

    assert flow["edit_records"] == sorted([f"{JOB_WORKFLOW}:v2:6:source", f"{EARLIER_EDIT}:v2:3:other"]), flow


@pytest.mark.parametrize(
    ("token", "version"),
    [
        ("CAE", 1),
        ("CAI", 2),
        ("CAM", 3),
        ("CAQ", 4),
        ("CBA", 16),
        ("CKwC", 300),  # computed, not measured: a two-byte varint whose first byte alone would read 172
        ("CAA", None),  # version 0 does not exist
        ("", None),
        ("XYZ", None),
        (None, None),
    ],
)
def test_a_records_fourth_field_is_the_version_of_its_media(token, version):
    """Measured 2026-09-28: a clip's own generation is "CAE", its first edit "CAI", its second "CAM"; decoded, these
    are the protobuf {1: 1}, {1: 2}, {1: 3}. The reader treated the field as a type and matched "CAI" literally, so
    the second edit of a clip (plan-t-edit-1, 20 credits) was heard as nothing again."""
    assert composer._record_version(token) == version


def test_the_editor_hears_a_second_edit_of_the_same_clip():
    """The exact case that failed live: the edit record of a clip's SECOND edit is typed "CAM"."""
    flow = _judged(
        [_edit_submit(_edit_record(6, version="CAM")), _status_reply(2)], editor=True, source_media=JOB_MEDIA
    )

    assert flow["workflow_id"] == JOB_WORKFLOW and flow["named_by_editor_submit"] is True, flow


def test_an_original_generation_under_the_editors_submit_is_not_an_edit():
    """Version 1 is a clip's own generation; only a later version is an edit of it."""
    flow = _judged([_edit_submit(_edit_record(6, version="CAE"))], editor=True, source_media=JOB_MEDIA)

    assert flow["workflow_id"] is None, flow


def test_the_gen_reader_still_hears_only_first_versions():
    """A generation is always its media's version 1; the gen path's reader stays exactly as it was."""
    later = _FlowReply("as29s", [None, 1, [_edit_record(4, version="CAM")]])

    flow = _judged([SUBMIT_REPLY, _status_reply(2), later])

    assert flow["statuses"] == [6, 2], flow


# ten seconds (plan U)


class _PickyRadios(_Radios):
    """Records which length the pin asked for, instead of assuming 8 s."""

    def filter(self, has_text):
        self.page.asked.append(has_text)
        return _Radio(self.page)


class _PickyRadioPage(_RadioPage):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.asked = []

    def locator(self, selector):
        assert selector == ingredients.RADIO
        return _PickyRadios(self)


def test_pin_duration_checks_the_length_it_is_asked_for(no_settings_pane):
    """Measured 2026-09-28: the Ingredients composer offers 4s 6s 8s 10s for Omni 1.1 Flash on this account."""
    page = _PickyRadioPage()

    assert asyncio.run(ingredients.pin_duration(page, 10)) == "pinned"

    pattern = page.asked[0]
    assert pattern.search("10s") and not pattern.search("8s") and not pattern.search("110s"), pattern.pattern


class _NoBrowser:
    """A session that must never be touched: every refusal here has to come before a browser opens."""

    def __getattr__(self, name):
        raise AssertionError(f"a browser was reached ({name}) for a call that should have been refused")


def test_ten_seconds_on_a_model_that_offers_no_length_is_refused_before_a_browser_opens():
    """Measured 2026-09-28: with Veo 3.1 Lite chosen the composer shows no duration group at all."""
    # Refused by the length rule itself, not by the price rule that would also stop it (mutant U-M1, 2026-09-28).
    with pytest.raises(ValueError, match="duration must be one of"):
        asyncio.run(
            ingredients.generate(
                _NoBrowser(), "P", prompt="p", characters=[ENTITY], model="veo-lite", duration=10, job_id="j"
            )
        )


def test_a_length_whose_price_was_never_measured_is_quoted_but_never_run(monkeypatch):
    """The repo refuses rather than guesses a price: an unmeasured length may be read with dry_run, which clicks
    nothing, and is refused for a real run before any browser opens."""
    monkeypatch.setattr(ingredients, "LONGER_PRICES", {})

    with pytest.raises(ValueError, match="no measured price"):
        asyncio.run(
            ingredients.generate(
                _NoBrowser(),
                "P",
                prompt="p",
                characters=[ENTITY],
                model="omni-flash",
                duration=10,
                job_id="j",
            )
        )


def test_every_gen_character_price_is_the_one_the_tool_promises():
    """Both sides read, never a copy typed into the test, and each price tied to its own model and length (review of
    plan U, F4: a bare "N credits" anywhere let 10 s at 12 pass because 12 is the 8 s price)."""
    import re as _re

    from video import mcp_server

    tools = asyncio.run(mcp_server.server.list_tools())
    description = next(tool.description for tool in tools if tool.name == "gen_character")
    for model, price in ingredients.PRICES.items():
        assert _re.search(rf"{_re.escape(model)}\b[^;.]*?(?<!\d){price} credits", description), (model, price)
    for (model, seconds), price in ingredients.LONGER_PRICES.items():
        assert f"duration={seconds} is offered on {model} at {price} credits" in description, (
            model,
            seconds,
            price,
        )


def _ten_second_setup_world(monkeypatch, log, pinned="pinned"):
    captured = _generate_world(monkeypatch, log)
    asked = []

    async def apply_settings(page, model, aspect, references):
        log.append("settings")

    async def pin_duration(page, seconds=None):
        asked.append(seconds)
        return pinned

    async def attach(page, reference):
        return {"kind": reference.kind, "id": min(reference.mention_ids), "text": reference.title}

    async def chips_on(page):
        return [{}]

    monkeypatch.setattr(ingredients, "apply_settings", apply_settings)
    monkeypatch.setattr(ingredients, "pin_duration", pin_duration)
    monkeypatch.setattr(ingredients, "attach", attach)
    monkeypatch.setattr(ingredients, "chips_on", chips_on)
    return captured, asked


def test_a_ten_second_run_is_priced_pinned_and_recorded_at_ten_seconds(monkeypatch, tmp_path):
    """Review of plan U, F2: a driver that priced, pinned or recorded 8 s whatever it was asked passed every test,
    so the whole feature could collapse into a silent 8 s run at 12 credits. Driven end to end at 10 s here."""
    log = []
    captured, asked = _ten_second_setup_world(monkeypatch, log)

    asyncio.run(
        ingredients.generate(
            object(),
            "p-1",
            prompt=PROMPT,
            characters=[ENTITY],
            duration=10,
            job_id="job-10",
            out_dir=tmp_path,
        )
    )
    extra = asyncio.run(captured["setup"](type("Session", (), {"page": _GeneratePage(log)})()))

    assert captured["expected_credits"] == ingredients.LONGER_PRICES[("omni-flash", 10)]
    assert asked == [10], asked
    assert extra["seconds"] == 10, extra


def test_ten_seconds_asked_for_and_no_length_control_found_is_refused_before_the_click(monkeypatch, tmp_path):
    """Review of plan U, F3: an absent length row let the run go on at whatever length the composer remembered, while
    the row said 10. setup runs before the click, so refusing there spends nothing."""
    log = []
    captured, _ = _ten_second_setup_world(monkeypatch, log, pinned="absent")

    asyncio.run(
        ingredients.generate(
            object(),
            "p-1",
            prompt=PROMPT,
            characters=[ENTITY],
            duration=10,
            job_id="job-10",
            out_dir=tmp_path,
        )
    )

    with pytest.raises(LookupError, match="10s"):
        asyncio.run(captured["setup"](type("Session", (), {"page": _GeneratePage(log)})()))


def test_an_unmeasured_length_is_still_quoted_by_a_dry_run(monkeypatch, tmp_path):
    """Review of plan U, F5: only the refusal half of "quoted but never run" was tested. The quote carries no expected
    price, so the dry run can never report a guessed price as matching."""
    log = []
    captured = _generate_world(monkeypatch, log)
    monkeypatch.setattr(ingredients, "LONGER_PRICES", {})

    asyncio.run(
        ingredients.generate(
            object(), "p-1", prompt=PROMPT, characters=[ENTITY], duration=10, dry_run=True, out_dir=tmp_path
        )
    )

    assert captured["dry_run"] is True and captured["expected_credits"] == 0, captured


def test_a_paid_run_whose_submit_carried_no_references_is_not_reported_as_done(monkeypatch, tmp_path):
    """Review of plan U, F1: the body check only watched. gflow warns that off 8 s Flow drops the references and runs
    text to video at full price, and on omni-flash 10 s that is the same 15 credits, so the price guard cannot tell;
    the run came back done. The request is already gone by then, so the credits are spent: the caller must hear it."""
    log = []
    _generate_world(monkeypatch, log, was=True)

    async def submit(session, project_id, **kwargs):
        log.append("submit")
        return {
            "status": "done",
            "path": "out/x.mp4",
            "credits_before": 100,
            "credits_after": 85,
            "body_check": {
                "rpcid": "MZZa6b",
                "model_keys": ["abra_t2v_10s"],
                "missing": [ENTITY],
                "ok": False,
            },
        }

    monkeypatch.setattr(ingredients.composer, "_submit", submit)

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(
            ingredients.generate(
                object(),
                "p-1",
                prompt=PROMPT,
                characters=[ENTITY],
                duration=10,
                job_id="job-10",
                out_dir=tmp_path,
            )
        )

    said = str(caught.value)
    assert "15 credits" in said and "abra_t2v_10s" in said and ENTITY in said, said
    assert log[-1] == "agent True", "agent mode must be restored before the error leaves"


def test_a_dry_run_is_never_failed_by_the_body_check(monkeypatch, tmp_path):
    """A dry run sends no request, so its body check always reads 'never saw a submit'; that is not a failure."""
    log = []
    _generate_world(monkeypatch, log)

    async def submit(session, project_id, **kwargs):
        return {
            "dry_run": True,
            "body_check": {"rpcid": None, "model_keys": [], "missing": [ENTITY], "ok": False},
        }

    monkeypatch.setattr(ingredients.composer, "_submit", submit)

    result = asyncio.run(
        ingredients.generate(
            object(), "p-1", prompt=PROMPT, characters=[ENTITY], dry_run=True, out_dir=tmp_path
        )
    )
    assert result["dry_run"] is True


def test_resolve_takes_images_alone():
    """Plan X: a reference run from project images only, the one way to a 10 s reference video on this host."""
    references = ingredients.resolve([_character()], [_image()], [_record()], [], [MEDIA], "omni-flash")
    assert [(r.kind, r.id) for r in references] == [("media", MEDIA)]
    assert references[0].mention_ids == frozenset({WORKFLOW})


def test_generate_from_images_alone_still_submits_in_ingredients_mode_under_the_body_check(
    monkeypatch, tmp_path
):
    """Plan X, review: gflow picks Ingredients only for a request carrying characters, so an images-only run relies on
    the composer being told the mode. Sent under Frames instead it would be a plain t2v at the same 15 credits at 10 s,
    which the price check cannot tell apart, and only the body check would catch it after the money is gone."""
    log = []
    captured = _generate_world(monkeypatch, log)
    asyncio.run(
        ingredients.generate(
            object(),
            "p-1",
            prompt=PROMPT,
            characters=[],
            media_ids=[MEDIA],
            model="omni-flash",
            duration=10,
            job_id="job-1",
            out_dir=tmp_path,
        )
    )
    assert captured["mode"] == "Ingredients" and captured["kind"] == "character"
    assert captured["expected_credits"] == 15
    assert [r.id for r in captured["watch"].references] == [MEDIA]


def test_two_images_the_dialog_cannot_tell_apart_are_refused_with_the_way_out():
    """Plan AE: the refusal named the problem but not the fix, and the fix is a rename before flow_upload. Since plan AL
    the dialog tells images apart by their url, so only twins whose urls end alike are refused."""
    twin = _image(media="m-twin")
    records = [_record(), _record(media="m-twin", workflow="w-twin")]
    with pytest.raises(LookupError, match="rename the file"):
        ingredients.resolve([_character()], [_image(), twin], records, [], [MEDIA], "omni-flash")


# the recipe, read back after a paid run

KEPT = {
    "voices": [{"voice": "achird"}, {"voice": LILY_VOICE}],
    "reference_images": [{"workflow_id": WORKFLOW}],
    "characters": [{"entity_id": ENTITY}],
}


def test_the_recipe_check_passes_a_clip_that_kept_everything_it_was_given():
    assert ingredients.recipe_check(KEPT, [THU, IMAGE, ACHIRD, LILY]) == {
        "ok": True,
        "missing": [],
        "unexpected": [],
    }


@pytest.mark.parametrize(
    ("recipe", "missing", "unexpected"),
    [
        ({**KEPT, "voices": [{"voice": "achird"}]}, [LILY_VOICE], []),
        ({**KEPT, "voices": [*KEPT["voices"], {"voice": "leda"}]}, [], ["leda"]),
        ({**KEPT, "reference_images": []}, [MEDIA], []),
        (
            {**KEPT, "reference_images": [{"workflow_id": WORKFLOW}, {"workflow_id": "w-other"}]},
            [],
            ["w-other"],
        ),
        ({**KEPT, "characters": []}, [ENTITY], []),
        ({**KEPT, "characters": [{"entity_id": ENTITY}, {"entity_id": OTHER}]}, [], [OTHER]),
        (
            {**KEPT, "reference_images": [], "frames": [{"slot": "start", "workflow_id": WORKFLOW}]},
            [MEDIA],
            [],
        ),
    ],
    ids=[
        "a voice dropped",
        "a voice nobody asked for",
        "the image dropped",
        "an image nobody asked for",
        "the character dropped",
        "a character nobody asked for",
        "the image kept as a frame, which is another kind of run",
    ],
)
def test_the_recipe_check_names_what_the_clip_dropped_or_gained(recipe, missing, unexpected):
    assert ingredients.recipe_check(recipe, [THU, IMAGE, ACHIRD, LILY]) == {
        "ok": False,
        "missing": missing,
        "unexpected": unexpected,
    }


def _paid_world(monkeypatch, log, *, recipe=None, recipe_error=None):
    _generate_world(monkeypatch, log, was=True)

    async def submit(session, project_id, **kwargs):
        log.append("submit")
        return {
            "job_id": kwargs["job_id"],
            "media_id": "m-new",
            "path": "out/x.mp4",
            "spent": 10,
            "credits_before": 75,
            "credits_after": 65,
            "body_check": {"ok": True},
        }

    async def read_recipe(session, project_id, media_id, **kwargs):
        log.append(f"recipe {media_id}")
        if recipe_error is not None:
            raise recipe_error
        return recipe

    monkeypatch.setattr(ingredients.composer, "_submit", submit)
    monkeypatch.setattr(ingredients.reader, "recipe", read_recipe)


def _paid_run(tmp_path, **options):
    return asyncio.run(
        ingredients.generate(
            object(),
            "p-1",
            prompt=PROMPT,
            characters=[ENTITY],
            media_ids=[MEDIA],
            voices=["Achird"],
            model="veo-lite",
            job_id="job-1",
            out_dir=tmp_path,
            **options,
        )
    )


KEPT_ONE_VOICE = {**KEPT, "voices": [{"voice": "achird"}]}


def test_a_paid_run_reads_the_clips_recipe_back_and_says_it_kept_every_input(monkeypatch, tmp_path):
    # Plan AL I4: the request carried the voices, and the listing says the clip kept them.
    log = []
    _paid_world(monkeypatch, log, recipe=KEPT_ONE_VOICE)
    result = _paid_run(tmp_path)
    assert result["recipe_check"] == {"ok": True, "missing": [], "unexpected": []}
    assert log[-3:] == ["submit", "agent True", "recipe m-new"]


def test_a_paid_clip_whose_recipe_dropped_a_voice_is_an_error_though_it_was_paid(monkeypatch, tmp_path):
    log = []
    _paid_world(monkeypatch, log, recipe={**KEPT, "voices": []})
    with pytest.raises(RuntimeError) as caught:
        _paid_run(tmp_path)
    said = str(caught.value)
    assert "10 credits were spent" in said and "achird" in said and "m-new" in said, said
    assert "Do not run this job again under a new job_id" in said
    assert "agent True" in log


def test_a_recipe_that_cannot_be_read_never_turns_a_paid_clip_into_an_error(monkeypatch, tmp_path):
    log = []
    _paid_world(monkeypatch, log, recipe_error=LookupError("rpc Zzl0ze not observed; saw []"))
    result = _paid_run(tmp_path)
    assert result["recipe_check"]["ok"] is None and "Zzl0ze" in result["recipe_check"]["error"]
    assert result["path"] == "out/x.mp4"


def test_a_dry_run_reads_no_recipe(monkeypatch, tmp_path):
    log = []
    _paid_world(monkeypatch, log, recipe=KEPT_ONE_VOICE)

    async def quote(session, project_id, **kwargs):
        return {"dry_run": True, "quoted_credits": 10, "body_check": {"ok": False}}

    monkeypatch.setattr(ingredients.composer, "_submit", quote)
    result = _paid_run(tmp_path, dry_run=True)
    assert "recipe_check" not in result and not [line for line in log if line.startswith("recipe")]


def test_a_paid_run_whose_request_lacked_a_voice_says_which_voices_it_did_send(monkeypatch, tmp_path):
    log = []
    _paid_world(monkeypatch, log, recipe=KEPT_ONE_VOICE)

    async def submit(session, project_id, **kwargs):
        return {
            "spent": 10,
            "path": "out/x.mp4",
            "body_check": {
                "rpcid": "MZZa6b",
                "model_keys": ["veo_3_1_r2v_lite"],
                "missing": ["achird"],
                "voices": [["leda"]],
                "ok": False,
            },
        }

    monkeypatch.setattr(ingredients.composer, "_submit", submit)
    with pytest.raises(RuntimeError, match=re.escape("voices sent [['leda']]")) as caught:
        _paid_run(tmp_path)
    assert "missing ['achird']" in str(caught.value)


STILL_RENDERING = (
    "do not run this job again: the clip m-new exists but no file came back (still rendering after 360s); fetch it "
    "with flow_download once it has finished; spent 10 credits; job job-1"
)


@pytest.mark.parametrize(
    ("seen", "told"),
    [
        ({"rpcid": "MZZa6b", "model_keys": ["veo_3_1_r2v_lite"], "missing": ["achird"], "ok": False}, True),
        ({"rpcid": "MZZa6b", "model_keys": ["veo_3_1_r2v_lite"], "missing": [], "ok": True}, False),
        (None, False),
    ],
    ids=["the request dropped a voice", "the request carried everything", "no request was heard"],
)
def test_a_clip_still_rendering_never_hides_a_request_that_dropped_a_voice(monkeypatch, tmp_path, seen, told):
    # Review of plan AL, 2026-10-02: the money path raises for a clip that is paid and not fetched yet, and the
    # request check sat behind that raise, so a clip made without its voice reached the caller as one to go and
    # download. The advice still leads: an agent reads the head of an error.
    log = []
    _paid_world(monkeypatch, log, recipe=KEPT_ONE_VOICE)

    async def submit(session, project_id, **kwargs):
        kwargs["watch"].seen = seen
        raise RuntimeError(STILL_RENDERING)

    monkeypatch.setattr(ingredients.composer, "_submit", submit)
    with pytest.raises(RuntimeError) as caught:
        _paid_run(tmp_path)
    said = str(caught.value)
    assert said.startswith(STILL_RENDERING)
    assert ("did not match what was asked" in said and "achird" in said) is told, said
    assert "agent True" in log


def test_a_run_cancelled_after_the_click_is_passed_on_as_the_cancellation_it_is(monkeypatch, tmp_path):
    # Scoped re-review of plan AL, 2026-10-02: only an error is reworded with what the request check heard. A
    # cancellation turned into a RuntimeError would make the caller's own cancel look like a failed tool.
    log = []
    _paid_world(monkeypatch, log, recipe=KEPT_ONE_VOICE)

    async def submit(session, project_id, **kwargs):
        kwargs["watch"].seen = {"rpcid": "MZZa6b", "model_keys": [], "missing": ["achird"], "ok": False}
        raise asyncio.CancelledError

    monkeypatch.setattr(ingredients.composer, "_submit", submit)
    with pytest.raises(asyncio.CancelledError):
        _paid_run(tmp_path)
    assert "agent True" in log


def test_a_request_that_named_the_wrong_model_is_not_said_to_lack_a_reference(monkeypatch, tmp_path):
    # The check also fails on the model key's mode, length and resolution, with nothing missing: the words must hold
    # for those too (the Frames driver already says it this way).
    log = []
    _paid_world(monkeypatch, log, recipe=KEPT_ONE_VOICE)

    async def submit(session, project_id, **kwargs):
        return {
            "spent": 10,
            "path": "out/x.mp4",
            "body_check": {"rpcid": "MZZa6b", "model_keys": ["veo_3_1_t2v_lite"], "missing": [], "ok": False},
        }

    monkeypatch.setattr(ingredients.composer, "_submit", submit)
    with pytest.raises(RuntimeError, match="10 credits were spent") as caught:
        _paid_run(tmp_path)
    said = str(caught.value)
    assert "did not match what was asked" in said and "veo_3_1_t2v_lite" in said, said
    assert "missing []" in said and "did not carry" not in said and "may not show them" not in said, said


def test_a_failure_before_any_click_is_passed_on_as_it_is(monkeypatch, tmp_path):
    log = []
    _paid_world(monkeypatch, log, recipe=KEPT_ONE_VOICE)
    refused = LookupError("Flow refuses the voice 'Achird' on this model; refusing to generate")

    async def submit(session, project_id, **kwargs):
        raise refused

    monkeypatch.setattr(ingredients.composer, "_submit", submit)
    with pytest.raises(LookupError) as caught:
        _paid_run(tmp_path)
    assert caught.value is refused
