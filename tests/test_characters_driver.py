import asyncio
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

    async def click(self, timeout=None):
        self.page.log.append(f"click {self.label}")
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
