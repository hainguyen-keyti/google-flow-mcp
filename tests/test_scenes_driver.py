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


class _Text:
    """page.get_by_text: exact means an element's whole text is the string, otherwise a case-insensitive part of it."""

    def __init__(self, text, exact):
        self.text = text
        self.exact = exact

    def fits(self, title):
        return title == self.text if self.exact else self.text.lower() in title.lower()


class _Button:
    def __init__(self, tile, name):
        self.tile = tile
        self.name = name

    @property
    def first(self):
        return self

    async def click(self, timeout=None):
        page = self.tile.page
        if self.name == "^Restore$":
            page.restored.append(self.tile.title)
        else:
            assert self.name == "More options"
            page.menu_for = self.tile.title


class _Tile:
    def __init__(self, page, title):
        self.page = page
        self.title = title

    async def count(self):
        return 0 if self.title is None else 1

    async def wait_for(self, state=None, timeout=None):
        if self.title is None:
            raise PlaywrightTimeoutError(f"Locator.wait_for: Timeout {timeout}ms exceeded")

    async def hover(self, timeout=None):
        pass

    def get_by_role(self, role, name=None):
        assert role == "button"
        return _Button(self, name.pattern)


class _Tiles:
    """A live locator: what it matches is read off the page each time, so a late tile shows up in a later count."""

    def __init__(self, page, pattern=None, text=None):
        self.page = page
        self.pattern = pattern
        self.text = text

    def _titles(self):
        # Measured 2026-09-15 and 2026-09-16 (plan D T1): a scene tile's text is its title between two icon ligatures,
        # and the title sits in an element of its own.
        return [
            title
            for title in self.page.shown()
            if (self.pattern is None or self.pattern.search(f"movie_edit {title} movie"))
            and (self.text is None or self.text.fits(title))
        ]

    def filter(self, has_text=None, has=None):
        return _Tiles(self.page, has_text or self.pattern, has or self.text)

    @property
    def first(self):
        titles = self._titles()
        return _Tile(self.page, titles[0] if titles else None)

    async def count(self):
        return len(self._titles())


class _TrashPage:
    """The project's Trash view as measured on 2026-09-15: scene tiles carry no scene id, hovering one shows
    Restore and Delete permanently, and Restore asks for no confirmation. Tiles may render late: `titles` maps
    each shown title to the millisecond it appears, or lists titles shown at once, repeats allowed."""

    def __init__(self, titles):
        self.titles = list(titles.items()) if isinstance(titles, dict) else [(title, 0) for title in titles]
        self.elapsed_ms = 0
        self.restored = []

    def shown(self):
        return [title for title, at in self.titles if at <= self.elapsed_ms]

    def locator(self, selector, has=None):
        if selector == "flow-scene-tile":
            return selector
        assert selector == "flow-tile-container" and has == "flow-scene-tile"
        return _Tiles(self)

    def get_by_text(self, text, exact=False):
        return _Text(text, exact)

    async def wait_for_timeout(self, ms):
        self.elapsed_ms += ms


class _MenuItem:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def filter(self, has_text=None):
        assert has_text.pattern == "Move to trash"
        return self

    async def click(self, timeout=None):
        self.page.trashed.append(self.page.menu_for)


class _NoDialog:
    @property
    def first(self):
        return self

    async def wait_for(self, state=None, timeout=None):
        raise PlaywrightTimeoutError(f"Locator.wait_for: Timeout {timeout}ms exceeded")


class _GridPage(_TrashPage):
    """The project grid as measured on 2026-09-16 (plan D T1): a scene tile shows its title between two ligatures and
    carries no scene id. 'More options' then 'Move to trash' trashes the scene; this fake shows no confirm dialog."""

    def __init__(self, titles):
        super().__init__(titles)
        self.menu_for = None
        self.trashed = []

    def locator(self, selector, has=None):
        if selector == "[role=menuitem], .cdk-overlay-pane button":
            return _MenuItem(self)
        if selector == "[role=dialog], mat-dialog-container":
            return _NoDialog()
        return super().locator(selector, has)


class _Session:
    def __init__(self, titles, page_cls=_TrashPage):
        self.page = page_cls(titles)
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
TRASHED = {"BpMsoe": [[]]}


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


def test_restore_brings_back_the_tile_whose_title_is_exactly_the_scenes_not_one_containing_it(monkeypatch):
    # Plan D (DECISIONS 2026-09-15): "scene" inside "scene thu" used to be refused as ambiguous; matching the whole
    # title finds the one tile that is exactly "scene".
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "scene", True), _entry(OTHER, "scene thu", True)),
        RESTORED,
        _listing(_entry(SCENE, "scene", False), _entry(OTHER, "scene thu", True)),
    )
    session = _Session(["scene thu", "scene"])

    assert asyncio.run(scenes.restore(session, PROJECT, SCENE))["trashed"] is False
    assert session.page.restored == ["scene"]


def test_restore_waits_for_every_trashed_scene_tile_then_restores_the_exact_title(monkeypatch):
    # Review 2026-09-15: counting as soon as the first match showed let "Scene 10", rendered before "Scene 1", be the only
    # fit for "Scene 1". The wait stays, and on the whole title "Scene 1" is found once its own tile renders (plan D).
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "Scene 1", True), _entry(OTHER, "Scene 10", True)),
        RESTORED,
        _listing(_entry(SCENE, "Scene 1", False), _entry(OTHER, "Scene 10", True)),
    )
    session = _Session({"Scene 10": 0, "Scene 1": 2_000})

    assert asyncio.run(scenes.restore(session, PROJECT, SCENE))["trashed"] is False
    assert session.page.restored == ["Scene 1"]
    assert session.page.elapsed_ms >= 2_000


def test_restore_refuses_when_two_trashed_scenes_have_the_exact_title(monkeypatch):
    _flow_answers(monkeypatch, _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "alpha", True)))
    session = _Session(["alpha", "alpha"])

    with pytest.raises(RuntimeError, match=re.escape("2 trash tiles match 'alpha'")):
        asyncio.run(scenes.restore(session, PROJECT, SCENE))
    assert session.page.restored == []


def test_restore_refuses_a_scene_without_a_title(monkeypatch):
    # An empty title fits every tile, and the trash shows no ids.
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, None, True)),
        RESTORED,
        _listing(_entry(SCENE, None, False)),
    )
    session = _Session([""])

    with pytest.raises(LookupError, match="has no title"):
        asyncio.run(scenes.restore(session, PROJECT, SCENE))
    assert session.page.restored == []


def test_restore_gives_up_when_the_trash_never_shows_every_trashed_scene(monkeypatch):
    _flow_answers(monkeypatch, _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "bravo", True)))
    session = _Session(["alpha"])

    with pytest.raises(LookupError, match="shows 1 scene tiles for 2 trashed scenes"):
        asyncio.run(scenes.restore(session, PROJECT, SCENE))
    assert session.page.restored == []


def test_restore_refuses_when_the_trash_shows_more_scene_tiles_than_trashed_scenes(monkeypatch):
    # Re-review 2026-09-15: the wait ended once the tiles reached the trashed count, so with a tile the listing does not
    # know ("Scene 1 draft") the loop stopped before "Scene 1" rendered and the draft was the only match, and restored.
    _flow_answers(monkeypatch, _listing(_entry(SCENE, "Scene 1", True)))
    session = _Session({"Scene 1 draft": 0, "bravo": 0, "Scene 1": 2_000})

    with pytest.raises(LookupError, match="shows 2 scene tiles for 1 trashed scenes"):
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


@pytest.mark.parametrize(
    "grid", [["Scene 10", "Scene 1"], ["Scene 1 draft", "Scene 1"], ["Scene 1", "Scene 10"]]
)
def test_delete_trashes_the_tile_whose_title_is_exactly_the_scenes(monkeypatch, grid):
    # Plan D (DECISIONS 2026-09-15, 2026-09-16): grid tiles carry no scene id, and the old substring match took the first
    # tile containing the title, so "Scene 10" or "Scene 1 draft" could be trashed in place of "Scene 1".
    other = next(title for title in grid if title != "Scene 1")
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "Scene 1", False), _entry(OTHER, other, False)),
        TRASHED,
        _listing(_entry(SCENE, "Scene 1", True), _entry(OTHER, other, False)),
    )
    session = _Session(grid, _GridPage)

    assert asyncio.run(scenes.delete(session, PROJECT, SCENE))["trashed"] is True
    assert session.page.trashed == ["Scene 1"]


def test_delete_refuses_when_two_active_scenes_show_the_exact_title(monkeypatch):
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "alpha", False), _entry(OTHER, "alpha", False)),
        TRASHED,
        _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "alpha", False)),
    )
    session = _Session(["alpha", "alpha"], _GridPage)

    with pytest.raises(RuntimeError, match=re.escape("2 scene tiles show 'alpha'")):
        asyncio.run(scenes.delete(session, PROJECT, SCENE))
    assert session.page.trashed == []


def test_delete_refuses_a_scene_without_a_title(monkeypatch):
    # An empty title fits every tile, and grid tiles carry no scene id (plan D T1).
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, None, False), _entry(OTHER, "kept", False)),
        TRASHED,
        _listing(_entry(SCENE, None, True), _entry(OTHER, "kept", False)),
    )
    session = _Session(["kept", ""], _GridPage)

    with pytest.raises(LookupError, match="has no title"):
        asyncio.run(scenes.delete(session, PROJECT, SCENE))
    assert session.page.trashed == []


def test_delete_says_so_when_no_tile_shows_the_exact_title(monkeypatch):
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "alpha", False), _entry(OTHER, "alphabet", False)),
        TRASHED,
        _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "alphabet", False)),
    )
    session = _Session(["alphabet"], _GridPage)

    with pytest.raises(LookupError, match="not found on the grid"):
        asyncio.run(scenes.delete(session, PROJECT, SCENE))
    assert session.page.trashed == []
