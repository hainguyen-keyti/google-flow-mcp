import asyncio
import re

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import scenes
from video.session import FlowSession

PROJECT = "118aece2-f6f3-4121-8d11-2b36037d9b36"
SCENE = "7e933504-5cf0-465f-8863-bea7e5a8a132"
OTHER = "0b3c8f5e-4c2a-4d7e-9a61-2f3e5d7c9b10"


def _entry(scene_id, title, trashed):
    # Zzl0ze[4] entry: [scene_id, title, _, [created], [updated], status, _, flags]; trashed means flags == [true].
    return [scene_id, title, None, [1789416689, 0], [1789416764, 0], 2, None, [True] if trashed else []]


def _listing(*entries):
    return {"Zzl0ze": [[None, None, None, None, list(entries)]]}


class _Button:
    def __init__(self, tile):
        self.tile = tile

    @property
    def first(self):
        return self

    async def click(self, timeout=None):
        self.tile.page.restored.append(self.tile.title)


class _Tile:
    def __init__(self, page, title):
        self.page = page
        self.title = title

    async def wait_for(self, state=None, timeout=None):
        if self.title is None:
            raise PlaywrightTimeoutError(f"Locator.wait_for: Timeout {timeout}ms exceeded")

    async def hover(self, timeout=None):
        pass

    def get_by_role(self, role, name=None):
        assert role == "button" and name.pattern == "^Restore$"
        return _Button(self)


class _Tiles:
    def __init__(self, page, titles):
        self.page = page
        self.titles = titles

    def filter(self, has_text=None):
        # Measured 2026-09-15: a trash tile's text is its title between two icon ligatures.
        return _Tiles(self.page, [t for t in self.titles if has_text.search(f"movie_edit {t} movie")])

    @property
    def first(self):
        return _Tile(self.page, self.titles[0] if self.titles else None)

    async def count(self):
        return len(self.titles)


class _TrashPage:
    """The project's Trash view as measured on 2026-09-15: scene tiles carry no scene id, hovering one shows
    Restore and Delete permanently, and Restore asks for no confirmation."""

    def __init__(self, titles):
        self.titles = titles
        self.restored = []

    def locator(self, selector, has=None):
        if selector == "flow-scene-tile":
            return selector
        assert selector == "flow-tile-container" and has == "flow-scene-tile"
        return _Tiles(self, list(self.titles))

    async def wait_for_timeout(self, ms):
        pass


class _Session:
    def __init__(self, trash_titles):
        self.page = _TrashPage(trash_titles)
        self.urls = []

    async def goto(self, url, *, ready=None, timeout_ms=60_000):
        self.urls.append(url)

    project_url = staticmethod(FlowSession.project_url)


def _flow_answers(monkeypatch, *frames):
    queue = list(frames)

    async def fake_capture(session, action, *, settle):
        await action()
        return queue.pop(0)

    monkeypatch.setattr(scenes, "capture", fake_capture)


RESTORED = {"BpMsoe": [[[SCENE, "scene thu", None, [1789416689, 0], [1789436330, 0], 2, None, []]]]}


def test_restore_brings_back_the_one_trashed_scene_with_that_title(monkeypatch):
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "scene thu", True), _entry(OTHER, "kept", False)),
        RESTORED,
        _listing(_entry(SCENE, "scene thu", False), _entry(OTHER, "kept", False)),
    )
    session = _Session(["scene thu"])

    result = asyncio.run(scenes.restore(session, PROJECT, SCENE))

    assert result == {"scene_id": SCENE, "trashed": False, "rpcids": ["BpMsoe"], "active": 2}
    assert session.page.restored == ["scene thu"]
    assert f"{FlowSession.project_url(PROJECT)}/trash" in session.urls


def test_restore_refuses_to_guess_when_more_than_one_trash_tile_matches_the_title(monkeypatch):
    # The trash shows no ids, so a title that also fits another tile ("scene" inside "scene thu") is ambiguous.
    _flow_answers(monkeypatch, _listing(_entry(SCENE, "scene", True), _entry(OTHER, "scene thu", True)))
    session = _Session(["scene thu", "scene"])

    with pytest.raises(RuntimeError, match=re.escape("2 trash tiles match 'scene'")):
        asyncio.run(scenes.restore(session, PROJECT, SCENE))
    assert session.page.restored == []


def test_restore_refuses_a_scene_that_is_not_in_the_trash(monkeypatch):
    _flow_answers(monkeypatch, _listing(_entry(SCENE, "scene thu", False)))
    session = _Session([])

    with pytest.raises(LookupError, match="is not in the trash"):
        asyncio.run(scenes.restore(session, PROJECT, SCENE))
    assert session.page.restored == []


def test_restore_refuses_a_scene_the_project_does_not_have(monkeypatch):
    _flow_answers(monkeypatch, _listing(_entry(OTHER, "kept", True)))

    with pytest.raises(LookupError, match="is not in the project listing"):
        asyncio.run(scenes.restore(_Session(["kept"]), PROJECT, SCENE))


def test_restore_fails_when_the_listing_still_shows_the_scene_trashed(monkeypatch):
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "scene thu", True)),
        RESTORED,
        _listing(_entry(SCENE, "scene thu", True)),
    )

    with pytest.raises(RuntimeError, match="still in the trash"):
        asyncio.run(scenes.restore(_Session(["scene thu"]), PROJECT, SCENE))
