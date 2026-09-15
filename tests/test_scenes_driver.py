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
    """A live locator: what it matches is read off the page each time, so a late tile shows up in a later count."""

    def __init__(self, page, pattern=None):
        self.page = page
        self.pattern = pattern

    def _titles(self):
        # Measured 2026-09-15: a trash tile's text is its title between two icon ligatures.
        shown = self.page.shown()
        return [t for t in shown if self.pattern is None or self.pattern.search(f"movie_edit {t} movie")]

    def filter(self, has_text=None):
        return _Tiles(self.page, has_text)

    @property
    def first(self):
        titles = self._titles()
        return _Tile(self.page, titles[0] if titles else None)

    async def count(self):
        return len(self._titles())


class _TrashPage:
    """The project's Trash view as measured on 2026-09-15: scene tiles carry no scene id, hovering one shows
    Restore and Delete permanently, and Restore asks for no confirmation. Tiles may render late: `titles` maps
    each shown title to the millisecond it appears."""

    def __init__(self, titles):
        self.titles = titles if isinstance(titles, dict) else {title: 0 for title in titles}
        self.elapsed_ms = 0
        self.restored = []

    def shown(self):
        return [title for title, at in self.titles.items() if at <= self.elapsed_ms]

    def locator(self, selector, has=None):
        if selector == "flow-scene-tile":
            return selector
        assert selector == "flow-tile-container" and has == "flow-scene-tile"
        return _Tiles(self)

    async def wait_for_timeout(self, ms):
        self.elapsed_ms += ms


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


def test_restore_waits_for_every_trashed_scene_tile_before_it_matches_the_title(monkeypatch):
    # Review 2026-09-15: counting as soon as the first match showed let "Scene 10", rendered before "Scene 1",
    # be the only fit for "Scene 1" and get restored in its place.
    _flow_answers(monkeypatch, _listing(_entry(SCENE, "Scene 1", True), _entry(OTHER, "Scene 10", True)))
    session = _Session({"Scene 10": 0, "Scene 1": 2_000})

    with pytest.raises(RuntimeError, match=re.escape("2 trash tiles match 'Scene 1'")):
        asyncio.run(scenes.restore(session, PROJECT, SCENE))
    assert session.page.restored == []


def test_restore_gives_up_when_the_trash_never_shows_every_trashed_scene(monkeypatch):
    _flow_answers(monkeypatch, _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "bravo", True)))
    session = _Session(["alpha"])

    with pytest.raises(LookupError, match="shows 1 scene tiles for 2 trashed scenes"):
        asyncio.run(scenes.restore(session, PROJECT, SCENE))
    assert session.page.restored == []


def test_restore_counts_a_tile_that_appears_during_the_last_wait(monkeypatch):
    # Scoped re-review 2026-09-15: the loop gave up right after its last wait without looking again.
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "bravo", True)),
        RESTORED,
        _listing(_entry(SCENE, "alpha", False), _entry(OTHER, "bravo", True)),
    )
    session = _Session({"alpha": 0, "bravo": 15_000})

    assert asyncio.run(scenes.restore(session, PROJECT, SCENE))["trashed"] is False
    assert session.page.restored == ["alpha"]


def test_restore_names_another_scene_whose_trash_flag_changed(monkeypatch):
    # The trash shows no ids, so a wrong tile can only be caught afterwards, in the listing: say which one moved.
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "bravo", True)),
        RESTORED,
        _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "bravo", False)),
    )

    with pytest.raises(RuntimeError, match=f"{OTHER} \\('bravo'\\)"):
        asyncio.run(scenes.restore(_Session(["alpha", "bravo"]), PROJECT, SCENE))


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
