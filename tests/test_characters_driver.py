import asyncio
import re
from contextlib import asynccontextmanager

import pytest

from video.flow import characters

PROJECT = "c5d1301b-07fe-4485-86d9-49a47449a494"
ENTITY = "42085965-18b8-4d45-821e-1d61c01b3e84"
UPLOAD_WORKFLOW = "4acb51ea-7d3f-499c-b111-f21cf5aef9c8"
# maseQ's answer to the upload, in the record shape parsers.upload_record reads (measured 2026-09-15).
UPLOADED = [[UPLOAD_WORKFLOW, PROJECT, "ace881a5-303f-4aeb-83b5-b7e164d00ba0", "CAE", None, [None] * 14]]


def test_entity_id_from_url_reads_the_character_route():
    url = f"https://flow.google.com/project/{PROJECT}/character/a7269b1c-9dbb-4e09-9628-71993e17fdd0"
    assert characters.entity_id_from_url(url) == "a7269b1c-9dbb-4e09-9628-71993e17fdd0"


def test_entity_id_from_url_rejects_the_new_character_page():
    with pytest.raises(ValueError):
        characters.entity_id_from_url(f"https://flow.google.com/project/{PROJECT}/character")


def test_portrait_from_ogiz0b_uses_gflow_image_records(monkeypatch):
    calls = []

    def fake_image_records(rpcid, payload):
        calls.append((rpcid, payload))

        class Record:
            media_id = "17b41e47-177e-4f4a-aa87-b8d77485a589"
            image_url = "https://flow-content.google/image/17b41e47?sig"
            dimensions = (1024, 1024)
            prompt = "young woman"

        return [Record()]

    monkeypatch.setattr(characters, "image_records", fake_image_records)
    portrait = characters.portrait_from_frames({"ogiZ0b": [["payload"]]})
    # gflow's image record calls it media_id, but in this repo it is the WORKFLOW id: measured 2026-09-15,
    # flow_download rejects 5ab4f13a (what character_create reported) and accepts the listing's 750b6fd2.
    assert portrait == {
        "workflow_id": "17b41e47-177e-4f4a-aa87-b8d77485a589",
        "url": "https://flow-content.google/image/17b41e47?sig",
        "width": 1024,
        "height": 1024,
        "prompt": "young woman",
    }
    assert calls == [("ogiZ0b", ["payload"])]


def test_portrait_from_frames_without_ogiz0b_is_none():
    assert characters.portrait_from_frames({"WuwhI": [[]]}) is None


class _Button:
    def __init__(self, page, label, count):
        self.page = page
        self.label = label
        self._count = count

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def get_attribute(self, name):
        assert name == "disabled" and self.label == "Save new voice"
        self.page.polls += 1
        return "true" if self.page.polls <= self.page.disabled_polls else None

    async def click(self, timeout=None):
        self.page.log.append(f"click {self.label}")
        if self.label == "Preview":
            # Typing alone sends nothing: the footer just swaps play_arrow for autorenew. Clicking Preview is
            # what starts the synthesis (measured 2026-09-18, 60 s of silence after filling).
            self.page.waits_since_fill = 0
        if self.label == "Upload":
            self.page.chooser_open = True
        if self.label == "Start generation":
            self.page.url = f"https://flow.google.com/project/{PROJECT}/character/{ENTITY}"


class _Chooser:
    def __init__(self, page):
        self.page = page

    async def set_files(self, path):
        self.page.log.append(f"files {path}")
        if self.page.makes_character:
            self.page.url = f"https://flow.google.com/project/{PROJECT}/character/{ENTITY}"


class _ChooserInfo:
    def __init__(self, page):
        self.page = page

    @property
    def value(self):
        async def chooser():
            assert self.page.chooser_open, "the chooser only exists once Upload was clicked"
            return _Chooser(self.page)

        return chooser()


class _CharacterPage:
    """The New character page as the 2026-09-16 probe saw it: one Upload button whose file chooser makes the
    character (C4BZMd, maseQ) and moves the page to /character/<entity>."""

    def __init__(self, *, uploads=1, makes_character=True):
        self.log = []
        self.url = f"https://flow.google.com/project/{PROJECT}/character"
        self.uploads = uploads
        self.makes_character = makes_character
        self.chooser_open = False

    async def wait_for_timeout(self, ms):
        return None

    def get_by_role(self, role, name):
        assert role == "button"
        for label in ("Upload", "Start generation", "Done"):
            if name.search(label):
                return _Button(self, label, self.uploads if label == "Upload" else 1)
        raise AssertionError(f"unexpected button {name.pattern}")

    @asynccontextmanager
    async def expect_file_chooser(self, timeout=None):
        self.log.append("expect chooser")
        yield _ChooserInfo(self)


class _CharacterSession:
    def __init__(self, page):
        self.page = page

    async def goto(self, url, ready=None):
        self.page.log.append(f"goto {url.rsplit('/', 1)[-1]}")

    @staticmethod
    def project_url(project_id):
        return f"https://flow.google.com/project/{project_id}"


def _patch_create(monkeypatch, page):
    async def capture(session, action, *, settle):
        await action()
        if page.log and page.log[-1].startswith("files"):
            return {"C4BZMd": [[]], "maseQ": [UPLOADED], "as29s": [[]]}
        return {"as29s": [[]]}

    async def type_prompt(session, prompt):
        page.log.append(f"typed {prompt}")

    async def rename(session, entity_id, name, *, navigate=True, project_id=None):
        page.log.append(f"named {entity_id} {name}")
        return name

    monkeypatch.setattr(characters, "capture", capture)
    monkeypatch.setattr(characters, "_type_prompt", type_prompt)
    monkeypatch.setattr(characters, "rename", rename)


def test_create_from_an_image_uploads_it_names_the_character_and_types_no_prompt(monkeypatch, tmp_path):
    image = tmp_path / "face.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    page = _CharacterPage()
    _patch_create(monkeypatch, page)
    result = asyncio.run(characters.create(_CharacterSession(page), PROJECT, image=image, name="Mai"))
    assert result["entity_id"] == ENTITY
    assert result["portrait"] == {"workflow_id": UPLOAD_WORKFLOW, "source": "upload"}
    assert result["name"] == "Mai"
    assert not any(entry.startswith("typed") for entry in page.log)
    assert page.log == [
        "goto character",
        "expect chooser",
        "click Upload",
        f"files {image.resolve()}",
        f"named {ENTITY} Mai",
        "click Done",
    ]


def test_create_from_a_prompt_still_types_it_and_starts_generation(monkeypatch):
    page = _CharacterPage()
    _patch_create(monkeypatch, page)
    result = asyncio.run(characters.create(_CharacterSession(page), PROJECT, "a calm face"))
    assert result["entity_id"] == ENTITY
    assert page.log == ["goto character", "typed a calm face", "click Start generation", "click Done"]


@pytest.mark.parametrize("given", ["neither", "both", "blank prompt"])
def test_create_takes_exactly_one_of_a_prompt_and_an_image_before_it_opens_a_page(
    monkeypatch, tmp_path, given
):
    image = tmp_path / "face.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    page = _CharacterPage()
    _patch_create(monkeypatch, page)
    options = {
        "neither": {},
        "both": {"prompt": "a calm face", "image": image},
        "blank prompt": {"prompt": "  "},
    }
    with pytest.raises(ValueError, match="exactly one of a prompt and an image"):
        asyncio.run(characters.create(_CharacterSession(page), PROJECT, **options[given]))
    assert page.log == []


def test_create_refuses_an_image_that_is_missing_or_not_a_picture_before_it_opens_a_page(
    monkeypatch, tmp_path
):
    page = _CharacterPage()
    _patch_create(monkeypatch, page)
    with pytest.raises(FileNotFoundError):
        asyncio.run(characters.create(_CharacterSession(page), PROJECT, image=tmp_path / "gone.jpg"))
    notes = tmp_path / "notes.txt"
    notes.write_text("not a face")
    with pytest.raises(ValueError, match="png, jpg, jpeg or webp"):
        asyncio.run(characters.create(_CharacterSession(page), PROJECT, image=notes))
    assert page.log == []


@pytest.mark.parametrize("uploads", [0, 2])
def test_create_from_an_image_refuses_a_page_without_exactly_one_upload_button(
    monkeypatch, tmp_path, uploads
):
    image = tmp_path / "face.png"
    image.write_bytes(b"\x89PNG")
    page = _CharacterPage(uploads=uploads)
    _patch_create(monkeypatch, page)
    with pytest.raises(LookupError, match=f"{uploads} Upload buttons"):
        asyncio.run(characters.create(_CharacterSession(page), PROJECT, image=image))
    assert "expect chooser" not in page.log


def test_create_from_an_image_says_so_when_flow_makes_no_character(monkeypatch, tmp_path):
    image = tmp_path / "face.png"
    image.write_bytes(b"\x89PNG")
    page = _CharacterPage(makes_character=False)
    _patch_create(monkeypatch, page)
    with pytest.raises(RuntimeError, match="created no character"):
        asyncio.run(characters.create(_CharacterSession(page), PROJECT, image=image, name="Mai"))
    assert not any(entry.startswith("named") for entry in page.log)
    assert "click Done" not in page.log


# Measured 2026-09-18 off the live row: a row is a button[role=option] holding span.asset-title,
# span.asset-description and, for a voice saved on this account, mat-icon.custom-voice-badge-icon. Its
# textContent runs them together with NO whitespace ("voice_selectionAchernarFemale, soft, high pitch"), which
# is why matching the row by its text was wrong in both directions.
VOICE_ROWS = [
    ["SaigonGirl20", "giọng nữ Sài Gòn, nhỏ nhẹ, nhí nhảnh, khoảng 20 tuổi", True],
    ["Achernar", "Female, soft, high pitch", False],
    ["Leda", "Female, youthful, mid-high pitch", False],
    ["Zephyr", "Female, bright, mid-high pitch", False],
]


class _VoicePage:
    """The character edit page and its voice selector, as measured 2026-09-18: the list renders a window at a
    time, attaching fires rzMKMb, and the page then reads 'voice_selection <voice lowercased>' beside a
    play_arrow and a Remove button."""

    def __init__(self, *, window=2, attached=None, rows=None, active=None, disabled_polls=0, preview_after=1):
        self.log = []
        self.window = window
        self.offset = 0
        self.attached = attached
        self.rows = rows or VOICE_ROWS
        # The dialog opens with one row aria-selected: the voice it is editing. Clicking that row DESELECTS it
        # and empties the dialog, taking Add to character with it (measured 2026-09-18).
        self.active = active
        self.deselected = False
        # Save new voice is enabled BEFORE Flow starts synthesising and goes disabled while it runs, so the
        # answer to no0P6 is the signal, not the flag (measured 2026-09-18: a click at the wrong moment is a
        # no-op that returns no rpc at all).
        self.disabled_polls = disabled_polls
        self.polls = 0
        self.preview_after = preview_after
        self.handlers = []
        self.waits_since_fill = None
        self.url = f"https://flow.google.com/project/{PROJECT}/character/{ENTITY}"

    def on(self, event, handler):
        assert event == "response"
        self.handlers.append(handler)

    def remove_listener(self, event, handler):
        self.handlers.remove(handler)

    async def wait_for_timeout(self, ms):
        if self.waits_since_fill is None:
            return
        self.waits_since_fill += 1
        if self.preview_after is not None and self.waits_since_fill == self.preview_after:
            self.log.append("preview no0P6")
            for handler in list(self.handlers):
                handler(_Response("https://flow.google.com/_/data/batchexecute?rpcids=no0P6"))
        return

    def _rendered(self):
        return self.rows[self.offset : self.offset + self.window]

    async def evaluate(self, script, arg=None):
        if "scrollTop" in script:
            was = self.offset
            self.offset = min(self.offset + self.window, max(0, len(self.rows) - self.window))
            self.log.append("scroll")
            return None if was == self.offset else [was, self.offset]
        if "role=option" in script or "voice" in script:
            return [list(row) for row in self._rendered()]
        return []

    def _dom_text(self):
        # What a DOM scrape sees: the Material icon's ligature is part of the text content.
        opener = "Select a voice" if self.attached is None else f"voice_selection {self.attached}"
        commit = [] if self.deselected else ["Add to character", "Save new voice", "Preview"]
        return [opener, *commit] + ([] if self.attached is None else ["Remove"])

    def _accessible(self):
        # What get_by_role sees: the icon span carries aria-hidden, so its ligature is NOT in the name.
        return [label.replace("voice_selection ", "") for label in self._dom_text()]

    def get_by_role(self, role, name):
        assert role == "button"
        for label in self._accessible():
            if name.search(label):
                return _Button(self, label, 1)
        return _Button(self, name.pattern, 0)

    def locator(self, selector, has_text=None):
        if "asset-title" in selector:
            return _Title(has_text)
        if "textarea" in selector or "input" in selector:
            return _Fields(self, "textarea" if "textarea" in selector else "input")
        if "cdk-overlay-pane" in selector:
            return _VoiceRows(self, None)
        return _PageButtons(self, None)


class _PageButtons:
    """page.locator("button") filtered by has_text, which Playwright matches against TEXT CONTENT, so an
    aria-hidden icon ligature counts here and not in get_by_role."""

    def __init__(self, page, wanted):
        self.page = page
        self.wanted = wanted

    def filter(self, has_text=None):
        return _PageButtons(self.page, has_text)

    @property
    def first(self):
        return self

    def _hits(self):
        if self.wanted is None:
            return list(self.page._dom_text())
        pattern = getattr(self.wanted, "pattern", self.wanted)
        return [label for label in self.page._dom_text() if re.search(pattern, label, re.IGNORECASE)]

    async def count(self):
        return len(self._hits())

    async def click(self, timeout=None):
        hits = self._hits()
        assert hits, "clicked a page button that is not there"
        self.page.log.append(f"click {hits[0]}")


class _Response:
    def __init__(self, url):
        self.request = type("Req", (), {"url": url})()


class _Fields:
    """The maker's own controls: two textareas (sample dialogue, then performance) and the voice name input."""

    def __init__(self, page, tag):
        self.page = page
        self.tag = tag

    async def count(self):
        return 2 if self.tag == "textarea" else 1

    def nth(self, index):
        return _Field(self.page, f"{self.tag}{index}")

    @property
    def first(self):
        return self.nth(0)


class _Field:
    def __init__(self, page, name):
        self.page = page
        self.name = name

    async def count(self):
        return 1

    async def fill(self, text):
        self.page.log.append(f"fill {self.name} {text}")


class _Title:
    """page.locator('.asset-title', has_text=...): the name element a row is filtered by."""

    def __init__(self, wanted):
        self.wanted = wanted


class _VoiceRows:
    def __init__(self, page, wanted):
        self.page = page
        self.wanted = wanted

    def filter(self, has=None):
        assert isinstance(has, _Title), "a voice row is found by its title element, not by its run-on text"
        return _VoiceRows(self.page, has.wanted)

    @property
    def first(self):
        return self

    async def count(self):
        return len([r for r in self.page._rendered() if self._fits(r)])

    def _fits(self, row):
        if self.wanted is None:
            return True
        pattern = getattr(self.wanted, "pattern", self.wanted)
        return bool(re.search(pattern, row[0], re.IGNORECASE))

    async def get_attribute(self, name):
        assert name == "aria-selected"
        rows = [r for r in self.page._rendered() if self._fits(r)]
        return "true" if rows and rows[0][0] == self.page.active else "false"

    async def click(self, timeout=None):
        rows = [r for r in self.page._rendered() if self._fits(r)]
        assert rows, "clicked a voice row that is not rendered"
        self.page.log.append(f"row {rows[0][0]}")
        if rows[0][0] == self.page.active:
            self.page.active = None
            self.page.deselected = True


class _VoiceSession:
    def __init__(self, page):
        self.page = page
        self.urls = []

    async def goto(self, url, *, ready=None, timeout_ms=60_000):
        self.urls.append(url)

    project_url = staticmethod(lambda project_id: f"https://flow.google.com/project/{project_id}")


def test_set_voice_scrolls_to_the_named_preset_and_attaches_it():
    """The selector renders a window at a time, exactly like the project grid: the first attempt at Leda failed
    with 'no voice row named Leda' because it sat below the fold (measured 2026-09-18)."""
    page = _VoicePage(window=2)
    session = _VoiceSession(page)

    result = asyncio.run(characters.set_voice(session, PROJECT, ENTITY, "Zephyr"))

    assert "scroll" in page.log, "a voice below the fold is reached by scrolling, not by guessing"
    assert page.log[-2:] == ["row Zephyr", "click Add to character"]
    assert result["voice"] == "Zephyr" and result["rpcids"] == ["rzMKMb"]


def test_set_voice_refuses_a_voice_the_selector_never_shows():
    page = _VoicePage(window=2)
    session = _VoiceSession(page)

    with pytest.raises(LookupError, match="no voice named"):
        asyncio.run(characters.set_voice(session, PROJECT, ENTITY, "Khong Co"))
    assert not any(step.startswith("row ") for step in page.log)
    assert "click Add to character" not in page.log


def test_clear_voice_takes_the_voice_off_the_character():
    page = _VoicePage(attached="leda")
    session = _VoiceSession(page)

    result = asyncio.run(characters.clear_voice(session, PROJECT, ENTITY))

    assert page.log == ["click Remove"]
    assert result["voice"] is None


def test_set_voice_opens_the_selector_on_a_character_that_already_has_a_voice():
    """Measured 2026-09-18: the opener of a character WITH a voice reads 'voice_selection leda' in the DOM but
    its accessible name is only 'leda', because the words sit in an aria-hidden Material icon. A driver that
    knows only the empty state cannot change a voice that exists: the live probe died on exactly this."""
    page = _VoicePage(window=3, attached="leda")
    session = _VoiceSession(page)

    result = asyncio.run(characters.set_voice(session, PROJECT, ENTITY, "Zephyr"))

    assert page.log[0] == "click voice_selection leda", "the attached voice itself is what opens the selector"
    assert page.log[-2:] == ["row Zephyr", "click Add to character"]
    assert result["voice"] == "Zephyr"


def test_list_voices_reads_every_preset_the_selector_holds():
    page = _VoicePage(window=2)
    session = _VoiceSession(page)

    voices = asyncio.run(characters.list_voices(session, PROJECT, ENTITY))

    assert [v["name"] for v in voices] == ["SaigonGirl20", "Achernar", "Leda", "Zephyr"]
    assert voices[2]["description"] == "Female, youthful, mid-high pitch"


def test_list_voices_names_a_saved_voice_of_your_own_rather_than_its_icon():
    """A saved voice reads 'voice_selection settings_2 <name> <description>'. Reading the first word after the
    row icon calls every custom voice 'settings_2' and hides the one name an agent would ask for."""
    page = _VoicePage(window=4)
    session = _VoiceSession(page)

    voices = asyncio.run(characters.list_voices(session, PROJECT, ENTITY))

    assert voices[0] == {
        "name": "SaigonGirl20",
        "description": "giọng nữ Sài Gòn, nhỏ nhẹ, nhí nhảnh, khoảng 20 tuổi",
        "custom": True,
    }
    assert voices[1]["custom"] is False


def test_set_voice_takes_the_voice_whose_name_is_the_one_asked_for():
    """The row's textContent runs the icon, the name and the description together, so a substring match on it
    hands back whichever longer name renders first."""
    page = _VoicePage(
        window=4,
        rows=[["LedaSoft", "Female, soft", True], ["Leda", "Female, youthful, mid-high pitch", False]],
    )
    session = _VoiceSession(page)

    result = asyncio.run(characters.set_voice(session, PROJECT, ENTITY, "Leda"))

    assert page.log[-2:] == ["row Leda", "click Add to character"]
    assert result["voice"] == "Leda"


def test_set_voice_attaches_a_saved_voice_of_your_own():
    """The custom row carries the settings_2 ligature, so a pattern of 'voice_selection <name>' never matches it
    and the tool refuses a voice the picker is plainly showing."""
    page = _VoicePage(window=4)
    session = _VoiceSession(page)

    result = asyncio.run(characters.set_voice(session, PROJECT, ENTITY, "SaigonGirl20"))

    assert page.log[-2:] == ["row SaigonGirl20", "click Add to character"]
    assert result["voice"] == "SaigonGirl20"


def test_set_voice_leaves_the_row_the_dialog_already_has_selected_alone():
    """Reopening the picker after saving a voice shows that voice already aria-selected, with Add to character
    right there. Clicking it again deselects it and the commit button goes with it, which is how the first live
    attempt died (measured 2026-09-18)."""
    page = _VoicePage(window=4, attached="leda", active="SaigonGirl20")
    session = _VoiceSession(page)

    result = asyncio.run(characters.set_voice(session, PROJECT, ENTITY, "SaigonGirl20"))

    assert "row SaigonGirl20" not in page.log, "the selected row must not be clicked a second time"
    assert page.log[-1] == "click Add to character"
    assert result["voice"] == "SaigonGirl20"


def _maker(monkeypatch, **kwargs):
    monkeypatch.setattr(characters, "VOICE_READY_WAIT_S", 12.0)
    monkeypatch.setattr(characters, "VOICE_READY_STEP_S", 2.0)

    async def capture(session, action, *, settle):
        # The two rpcs the live save fired (2026-09-18): the sample becomes visible media, then it is named.
        await action()
        return {"lt8g5": [[]], "mYWVGd": [[]]}

    monkeypatch.setattr(characters, "capture", capture)
    return _VoicePage(window=4, **kwargs)


def test_make_voice_writes_the_performance_waits_for_flow_then_saves(monkeypatch):
    """Measured 2026-09-18: typing a performance turns the picker into a voice maker whose Save new voice stays
    disabled="true" for about 28 s while Flow synthesises the preview (rpc no0P6). Clicking it while disabled is
    the no-op that made an earlier run believe the save fired nothing at all."""
    page = _maker(monkeypatch, disabled_polls=3)
    session = _VoiceSession(page)

    result = asyncio.run(
        characters.make_voice(
            session, PROJECT, ENTITY, "Leda", "giọng nữ Sài Gòn, nhí nhảnh", name="SaigonGirl20", attach=False
        )
    )

    assert page.log == [
        "click Select a voice",
        "row Leda",
        "fill textarea0 Xin chào",
        "fill textarea1 giọng nữ Sài Gòn, nhí nhảnh",
        "fill input0 SaigonGirl20",
        "click Preview",
        "preview no0P6",
        "click Save new voice",
    ]
    assert result["voice"] == "SaigonGirl20" and result["preset"] == "Leda"
    assert result["attached"] is False
    assert result["polls"] == 4, "it polls until the button stops being disabled, then clicks once"
    assert result["rpcids"] == ["lt8g5", "mYWVGd"]


def test_make_voice_refuses_to_click_a_save_that_never_enables(monkeypatch):
    page = _maker(monkeypatch, disabled_polls=999)
    session = _VoiceSession(page)

    with pytest.raises(TimeoutError, match="still disabled"):
        asyncio.run(characters.make_voice(session, PROJECT, ENTITY, "Leda", "nhí nhảnh", name="SaigonGirl20"))
    assert "click Save new voice" not in page.log


def test_make_voice_refuses_a_sample_longer_than_flow_accepts(monkeypatch):
    page = _maker(monkeypatch, disabled_polls=0)
    session = _VoiceSession(page)

    with pytest.raises(ValueError, match="120"):
        asyncio.run(
            characters.make_voice(session, PROJECT, ENTITY, "Leda", "nhí nhảnh", name="X", sample="a" * 121)
        )
    assert session.urls == [], "the length is checked before a page is opened"


def test_make_voice_attaches_the_saved_voice_when_asked(monkeypatch):
    page = _maker(monkeypatch, disabled_polls=1)
    session = _VoiceSession(page)
    attached = []

    async def fake_set_voice(session_, project_id, entity_id, voice):
        attached.append(voice)
        return {"entity_id": entity_id, "voice": voice, "rpcids": ["rzMKMb"]}

    monkeypatch.setattr(characters, "set_voice", fake_set_voice)

    result = asyncio.run(
        characters.make_voice(session, PROJECT, ENTITY, "Leda", "nhí nhảnh", name="SaigonGirl20")
    )

    assert attached == ["SaigonGirl20"], "a voice nobody attached cannot speak in a generation"
    assert result["attached"] is True


def test_make_voice_waits_for_flows_preview_answer_before_it_saves(monkeypatch):
    """Measured 2026-09-18: the footer is enabled BEFORE Flow starts synthesising, goes disabled for the ~24 s
    the synthesis takes, and only a click after the answer saves anything. A run that clicked at the first
    moment the flag looked clear returned rpcids [] and left no voice behind."""
    page = _maker(monkeypatch, preview_after=3)
    session = _VoiceSession(page)

    result = asyncio.run(
        characters.make_voice(session, PROJECT, ENTITY, "Leda", "nhí nhảnh", name="V", attach=False)
    )

    assert page.log.index("preview no0P6") < page.log.index("click Save new voice")
    assert result["rpcids"] == ["lt8g5", "mYWVGd"]


def test_make_voice_says_so_when_flow_never_answers_the_preview(monkeypatch):
    page = _maker(monkeypatch, preview_after=None)
    session = _VoiceSession(page)

    with pytest.raises(TimeoutError, match="no0P6"):
        asyncio.run(characters.make_voice(session, PROJECT, ENTITY, "Leda", "nhí nhảnh", name="V"))
    assert "click Save new voice" not in page.log


def test_make_voice_refuses_to_report_a_save_that_fired_nothing(monkeypatch):
    """rpcids [] is exactly what the failed live run returned: the click landed on a button that did nothing."""
    page = _maker(monkeypatch, preview_after=1)
    session = _VoiceSession(page)

    async def silent_capture(session_, action, *, settle):
        await action()
        return {}

    monkeypatch.setattr(characters, "capture", silent_capture)

    with pytest.raises(RuntimeError, match="no rpc"):
        asyncio.run(
            characters.make_voice(session, PROJECT, ENTITY, "Leda", "nhí nhảnh", name="V", attach=False)
        )


def test_make_voice_clicks_preview_because_typing_sends_nothing(monkeypatch):
    """Measured 2026-09-18: after filling the boxes the footer reads 'autorenew Preview' and Flow sends NOTHING
    for a minute. The synthesis a save needs starts only when Preview is clicked."""
    page = _maker(monkeypatch, preview_after=2)
    session = _VoiceSession(page)

    asyncio.run(characters.make_voice(session, PROJECT, ENTITY, "Leda", "nhí nhảnh", name="V", attach=False))

    assert page.log.index("click Preview") < page.log.index("preview no0P6")
    assert page.log.index("preview no0P6") < page.log.index("click Save new voice")
