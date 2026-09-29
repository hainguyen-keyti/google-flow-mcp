import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlencode

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


# U+200B zero width space and U+00AD soft hyphen, built with chr() so this file carries no escape sequence.
_ZERO_WIDTH = str.maketrans("", "", chr(0x200B) + chr(0xAD))
# A title Playwright's normalization turns into nothing at all.
BLANK_TITLE = " " + chr(0x200B) + " "


def _normalized(text):
    # Playwright's normalizeWhiteSpace (driver/package/lib/coreBundle.js): drop U+200B and U+00AD, collapse runs of
    # whitespace, trim. It runs on the element's text AND on the exact string, so the two meet normalized.
    return " ".join(text.translate(_ZERO_WIDTH).split())


class _Text:
    """page.get_by_text: exact means an element's whole text IS the string, both normalized; otherwise the string is a
    case-insensitive part of it."""

    def __init__(self, text, exact):
        self.text = text
        self.exact = exact

    def fits(self, title):
        if self.exact:
            return _normalized(title) == _normalized(self.text)
        return self.text.lower() in title.lower()


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
    def __init__(self, page, where=None, title=None):
        self.page = page
        self.where = where
        self.title = title

    async def scroll_into_view_if_needed(self, timeout=None):
        # Measured 2026-09-17 (Plan H T1): the grid and the trash sit in one div.cdk-virtual-scrollable, which renders
        # a window of tiles; scrolling the last rendered one into view is what brings the next window in.
        if self.where is not None:
            self.page.scroll_to(self.where)

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

    def __init__(self, page, pattern=None, text=None, scenes_only=False):
        self.page = page
        self.pattern = pattern
        self.text = text
        # The grid holds media tiles too (measured 2026-09-17: 22 to 50 containers for at most 8 scene tiles), and
        # 'More options' on one of those is not a scene menu, so the driver has to say which kind it wants.
        self.scenes_only = scenes_only

    def _matches(self):
        """The rendered tiles this locator matches, each with its place in the whole view so that scrolling one
        into view moves to THAT tile and not to the first one sharing its text.

        Measured 2026-09-15 and 2026-09-16 (plan D T1): a scene tile's text is its title between two icon
        ligatures, and the title sits in an element of its own."""
        return [
            (where, title)
            for where, (title, is_scene) in self.page.shown()
            if (not self.scenes_only or is_scene)
            and (self.pattern is None or self.pattern.search(f"movie_edit {title} movie"))
            and (self.text is None or self.text.fits(title))
        ]

    def _titles(self):
        return [title for _, title in self._matches()]

    def filter(self, has_text=None, has=None):
        scenes_only = self.scenes_only or has == "flow-scene-tile"
        text = self.text if has == "flow-scene-tile" else (has or self.text)
        return _Tiles(self.page, has_text or self.pattern, text, scenes_only)

    @property
    def first(self):
        matches = self._matches()
        return _Tile(self.page, *(matches[0] if matches else (None, None)))

    @property
    def last(self):
        matches = self._matches()
        return _Tile(self.page, *(matches[-1] if matches else (None, None)))

    async def count(self):
        return len(self._titles())

    async def all_text_contents(self):
        # What Playwright reads off each rendered tile: the title between the two icon ligatures.
        return [f"movie_edit {title} movie" for title in self._titles()]


class _TrashPage:
    """The project's Trash view as measured on 2026-09-15: scene tiles carry no scene id, hovering one shows
    Restore and Delete permanently, and Restore asks for no confirmation. Tiles may render late: `titles` maps
    each shown title to the millisecond it appears, or lists titles shown at once, repeats allowed."""

    def __init__(self, titles, window=None):
        given = list(titles.items()) if isinstance(titles, dict) else [(title, 0) for title in titles]
        # A title written "media:<text>" is a tile with no flow-scene-tile inside, the kind the grid holds many of.
        self.titles = [
            (title.removeprefix("media:"), at, not title.startswith("media:")) for title, at in given
        ]
        self.elapsed_ms = 0
        self.restored = []
        # None means every tile renders at once, as a short view really does; a number is the virtual window measured
        # on 2026-09-17: 7 of 8 tiles on the grid, 16 of 37 in the trash.
        self.window = window
        self.offset = 0

    def _painted(self):
        return [(title, is_scene) for title, at, is_scene in self.titles if at <= self.elapsed_ms]

    def scroll_to(self, where):
        if self.window is None:
            return
        # The tile scrolled into view ends up at the top of the window, and the view stops at the last one.
        self.offset = max(0, min(where, len(self._painted()) - self.window))

    def shown(self):
        painted = list(enumerate(self._painted()))
        if self.window is None:
            return painted
        return painted[self.offset : self.offset + self.window]

    def locator(self, selector, has=None):
        if selector == "flow-scene-tile":
            return selector
        assert selector == "flow-tile-container"
        assert has in (None, "flow-scene-tile")
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

    def __init__(self, titles, window=None):
        super().__init__(titles, window)
        self.menu_for = None
        self.trashed = []

    def locator(self, selector, has=None):
        if selector == "[role=menuitem], .cdk-overlay-pane button":
            return _MenuItem(self)
        if selector == "[role=dialog], mat-dialog-container":
            return _NoDialog()
        return super().locator(selector, has)


class _Session:
    def __init__(self, titles, page_cls=_TrashPage, window=None):
        self.page = page_cls(titles, window)
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


def test_restore_refuses_when_two_trashed_scenes_share_the_exact_title(monkeypatch):
    _flow_answers(monkeypatch, _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "alpha", True)))
    session = _Session(["alpha", "alpha"])

    with pytest.raises(RuntimeError, match=re.escape("2 trashed scenes are titled 'alpha'")):
        asyncio.run(scenes.restore(session, PROJECT, SCENE))
    assert session.page.restored == []


def test_restore_looks_only_at_scene_tiles_when_a_media_tile_shows_the_same_title(monkeypatch):
    # The trash view holds media tiles too, and hovering one offers its own Restore for the clip, not for the scene.
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "scene thu", True)),
        RESTORED,
        _listing(_entry(SCENE, "scene thu", False)),
    )
    session = _Session(["media:scene thu", "scene thu", "media:bravo"], window=2)

    assert asyncio.run(scenes.restore(session, PROJECT, SCENE))["trashed"] is False
    assert session.page.restored == ["scene thu"]


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


def test_restore_refuses_a_title_that_is_only_whitespace_or_zero_width(monkeypatch):
    # Review of plan D (2026-09-16, finding 1): Playwright normalizes an exact text selector, so a title of spaces and
    # zero-width characters becomes "" and matches every tile element that shows no text of its own.
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, BLANK_TITLE, True)),
        RESTORED,
        _listing(_entry(SCENE, BLANK_TITLE, False)),
    )
    session = _Session([BLANK_TITLE])

    with pytest.raises(LookupError, match="has no title"):
        asyncio.run(scenes.restore(session, PROJECT, SCENE))
    assert session.page.restored == []


def test_restore_does_not_wait_for_a_trashed_scene_it_was_not_asked_about(monkeypatch):
    # Until Plan H the trash had to render one tile per trashed scene before anything was clicked, which a virtual
    # scroll never does (16 tiles of 37, measured 2026-09-17). What kept it honest was never the tile count: it is the
    # listing saying this title belongs to one trashed scene, and the exact-text match on the tile itself.
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "bravo", True)),
        RESTORED,
        _listing(_entry(SCENE, "alpha", False), _entry(OTHER, "bravo", True)),
    )
    session = _Session(["alpha"])

    assert asyncio.run(scenes.restore(session, PROJECT, SCENE))["trashed"] is False
    assert session.page.restored == ["alpha"]


def test_restore_waits_past_tiles_the_listing_does_not_know_for_the_exact_title(monkeypatch):
    # Re-review 2026-09-15: the wait ended once the tiles reached the trashed count, so with a tile the listing does not
    # know ("Scene 1 draft") the loop stopped before "Scene 1" rendered and the draft was the only match, and restored.
    # The exact-text match is what rules the draft out; the loop now keeps looking until the real tile renders.
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "Scene 1", True)),
        RESTORED,
        _listing(_entry(SCENE, "Scene 1", False)),
    )
    session = _Session({"Scene 1 draft": 0, "bravo": 0, "Scene 1": 2_000})

    assert asyncio.run(scenes.restore(session, PROJECT, SCENE))["trashed"] is False
    assert session.page.restored == ["Scene 1"]
    assert session.page.elapsed_ms >= 2_000


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


def _id(index):
    # The listing parser keeps only UUID scene ids, so a made-up one would be dropped and never reach the grid.
    return f"00000000-0000-4000-8000-{index:012d}"


def _many(count, prefix="scene", trashed=False):
    return [_entry(_id(i), f"{prefix} {i:02d}", trashed) for i in range(count)]


def test_delete_scrolls_the_virtual_grid_to_reach_a_tile_below_the_fold(monkeypatch):
    # Plan H T1 measured the grid rendering 7 scene tiles of 8 at the top, the 8th only from scrollTop 1728, so the
    # old wait for one tile per active scene refused every project with more scenes than one screen holds.
    entries = _many(8)
    target = entries[7]
    scene_id = target[0]
    _flow_answers(
        monkeypatch,
        _listing(*entries),
        TRASHED,
        _listing(*entries[:7], _entry(scene_id, "scene 07", True)),
    )
    session = _Session([f"scene {i:02d}" for i in range(8)], _GridPage, window=7)

    assert asyncio.run(scenes.delete(session, PROJECT, scene_id))["trashed"] is True
    assert session.page.trashed == ["scene 07"]


def test_restore_scrolls_the_trash_which_renders_sixteen_of_thirty_seven_tiles(monkeypatch):
    entries = _many(37, prefix="gone", trashed=True)
    target = entries[30]
    scene_id = target[0]
    restored = {"BpMsoe": [[[scene_id, "gone 30", None, [1789416689, 0], [1789436330, 0], 2, None, []]]]}
    _flow_answers(
        monkeypatch,
        _listing(*entries),
        restored,
        _listing(*entries[:30], _entry(scene_id, "gone 30", False), *entries[31:]),
    )
    session = _Session([f"gone {i:02d}" for i in range(37)], window=16)

    assert asyncio.run(scenes.restore(session, PROJECT, scene_id))["trashed"] is False
    assert session.page.restored == ["gone 30"]


def test_delete_refuses_a_title_two_active_scenes_share_even_when_one_is_off_screen(monkeypatch):
    # The listing knows every scene, the rendered window knows a few: uniqueness has to be decided from the listing,
    # or a title shared with a scene further down the grid is trashed by guess.
    entries = [*_many(7), _entry(OTHER, "scene 00", False)]
    _flow_answers(monkeypatch, _listing(*entries))
    session = _Session([f"scene {i:02d}" for i in range(7)] + ["scene 00"], _GridPage, window=7)

    with pytest.raises(RuntimeError, match=re.escape("2 active scenes are titled 'scene 00'")):
        asyncio.run(scenes.delete(session, PROJECT, _id(0)))
    assert session.page.trashed == []


def test_delete_looks_only_at_scene_tiles_when_a_media_tile_shows_the_same_title(monkeypatch):
    # Review 2026-09-18: the grid holds media tiles as well (22 to 50 containers for at most 8 scene tiles), and
    # nothing pinned that the driver asks for the ones holding a flow-scene-tile. 'More options' on a media tile
    # opens a different menu, so a title a clip and a scene share must not turn into a refusal or a wrong click.
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "alpha", False)),
        TRASHED,
        _listing(_entry(SCENE, "alpha", True)),
    )
    session = _Session(["media:alpha", "alpha", "media:bravo"], _GridPage, window=2)

    assert asyncio.run(scenes.delete(session, PROJECT, SCENE))["trashed"] is True
    assert session.page.trashed == ["alpha"]


def test_delete_refuses_when_two_tiles_on_the_page_show_the_title(monkeypatch):
    # A tile the listing does not know, showing the same title, is still a guess: refuse rather than trash one.
    _flow_answers(monkeypatch, _listing(_entry(SCENE, "alpha", False), _entry(OTHER, "bravo", False)))
    session = _Session(["alpha", "bravo", "alpha"], _GridPage, window=3)

    with pytest.raises(RuntimeError, match=re.escape("2 grid tiles show 'alpha'")):
        asyncio.run(scenes.delete(session, PROJECT, SCENE))
    assert session.page.trashed == []


def test_delete_says_so_when_the_whole_scrolled_grid_never_shows_the_title(monkeypatch):
    entries = _many(8)
    _flow_answers(monkeypatch, _listing(*entries))
    session = _Session([f"scene {i:02d}" for i in range(7)], _GridPage, window=3)

    with pytest.raises(LookupError, match="not found on the grid"):
        asyncio.run(scenes.delete(session, PROJECT, _id(7)))
    assert session.page.trashed == []


def test_delete_refuses_when_two_active_scenes_show_the_exact_title(monkeypatch):
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "alpha", False), _entry(OTHER, "alpha", False)),
        TRASHED,
        _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "alpha", False)),
    )
    session = _Session(["alpha", "alpha"], _GridPage)

    with pytest.raises(RuntimeError, match=re.escape("2 active scenes are titled 'alpha'")):
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
    # The grid holds one tile per active scene, so `alphabet` is the trashed one here: without it the run would stop at
    # the tile count and never reach the exact match this case is named after (rule 10, the early-branch false green).
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "alpha", False), _entry(OTHER, "alphabet", True)),
        TRASHED,
        _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "alphabet", True)),
    )
    session = _Session(["alphabet"], _GridPage)

    with pytest.raises(LookupError, match="not found on the grid"):
        asyncio.run(scenes.delete(session, PROJECT, SCENE))
    assert session.page.trashed == []


def test_delete_waits_for_the_grid_to_show_every_active_scene_then_trashes_the_exact_title(monkeypatch):
    # L2 on 2026-09-16 failed with "not found on the grid" on a scene the listing already had. The grid was measured the
    # same day to hold exactly one tile per active scene and none for a trashed one, so wait for that many, as restore
    # does for the trash, instead of judging the first paint.
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "Scene 1", False), _entry(OTHER, "Scene 10", False)),
        TRASHED,
        _listing(_entry(SCENE, "Scene 1", True), _entry(OTHER, "Scene 10", False)),
    )
    session = _Session({"Scene 10": 0, "Scene 1": 2_500}, _GridPage)

    assert asyncio.run(scenes.delete(session, PROJECT, SCENE))["trashed"] is True
    assert session.page.trashed == ["Scene 1"]
    assert session.page.elapsed_ms >= 2_500


def test_delete_gives_up_when_the_grid_never_shows_the_scenes_own_tile(monkeypatch):
    _flow_answers(monkeypatch, _listing(_entry(SCENE, "alpha", False), _entry(OTHER, "bravo", False)))
    session = _Session(["bravo"], _GridPage)

    with pytest.raises(LookupError, match=re.escape("titled 'alpha' not found on the grid")):
        asyncio.run(scenes.delete(session, PROJECT, SCENE))
    assert session.page.trashed == []
    # It gives up at the wait limit, not at the sweep's own backstop: a grid that renders nothing new is done.
    assert scenes.TILE_WAIT_MS <= session.page.elapsed_ms <= scenes.TILE_WAIT_MS + 3 * scenes.TILE_STEP_MS


def test_delete_waits_past_tiles_the_listing_does_not_know_for_the_exact_title(monkeypatch):
    # A tile the listing does not know used to end the wait early and could be the only title match before the right
    # one rendered, which is how restore once restored a namesake. The exact-text match rules the draft out.
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "Scene 1", False)),
        TRASHED,
        _listing(_entry(SCENE, "Scene 1", True)),
    )
    session = _Session({"Scene 1 draft": 0, "bravo": 0, "Scene 1": 2_000}, _GridPage)

    assert asyncio.run(scenes.delete(session, PROJECT, SCENE))["trashed"] is True
    assert session.page.trashed == ["Scene 1"]
    assert session.page.elapsed_ms >= 2_000


def test_delete_does_not_wait_for_an_unrelated_tile_that_has_not_rendered(monkeypatch):
    # Plan H: waiting for every active scene to render is what broke on a virtual grid. Once this scene's own tile is
    # on the page and the listing says no other active scene carries that title, there is nothing left to wait for.
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "alpha", False), _entry(OTHER, "bravo", False)),
        TRASHED,
        _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "bravo", False)),
    )
    session = _Session({"alpha": 0, "bravo": 15_000}, _GridPage)

    assert asyncio.run(scenes.delete(session, PROJECT, SCENE))["trashed"] is True
    assert session.page.trashed == ["alpha"]
    assert session.page.elapsed_ms < 15_000


def test_delete_refuses_a_title_that_is_only_whitespace_or_zero_width(monkeypatch):
    # Review of plan D (2026-09-16, finding 1): Playwright normalizes an exact text selector, so this title becomes ""
    # and matches every tile element that shows no text of its own.
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, BLANK_TITLE, False), _entry(OTHER, "kept", False)),
        TRASHED,
        _listing(_entry(SCENE, BLANK_TITLE, True), _entry(OTHER, "kept", False)),
    )
    session = _Session(["kept", BLANK_TITLE], _GridPage)

    with pytest.raises(LookupError, match="has no title"):
        asyncio.run(scenes.delete(session, PROJECT, SCENE))
    assert session.page.trashed == []


def test_delete_refuses_a_scene_that_is_already_in_the_trash(monkeypatch):
    _flow_answers(monkeypatch, _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "bravo", False)))
    session = _Session(["bravo"], _GridPage)

    with pytest.raises(LookupError, match="already in the trash"):
        asyncio.run(scenes.delete(session, PROJECT, SCENE))
    assert session.page.trashed == []


def test_delete_names_another_scene_whose_trash_flag_changed(monkeypatch):
    # Grid tiles carry no ids, so a wrong tile can only be caught afterwards, in the listing: say which one moved.
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "alpha", False), _entry(OTHER, "bravo", False)),
        TRASHED,
        _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "bravo", True)),
    )

    with pytest.raises(RuntimeError, match=f"{OTHER} \\('bravo'\\)"):
        asyncio.run(scenes.delete(_Session(["alpha", "bravo"], _GridPage), PROJECT, SCENE))


# The scene page, measured 2026-09-16 (plan scene-timeline-tools T1): a built toolbar holds 27 buttons, among them
# "Add clip add_2" and "Download scene download"; an empty scene answers Add clip with the media picker while a scene
# that already holds a clip answers with a menu (Add clip, Extend) and the picker is one click further in; a picker row
# carries NO media id, only the media's title; clicking the row is the add, and the picker closes itself.
# Each button is (accessible name, text content). Keeping them apart matters: measured 2026-09-16, the timeline's
# button carries aria-label "Add clip" while its text is only the ligature "add_2", so a text match finds nothing and
# a name match finds it. An earlier version of this fake merged the two and made a driver that could never work pass.
TOOLBAR = [
    ("Back button to go to previous page", "arrow_back"),
    ("Favorite", "favorite"),
    ("Download scene", "download"),
    ("Move to trash", "delete"),
    ("More options", "more_vert"),
    ("Done editing scene", "Done"),
    ("Toggle aspect ratio", "crop_landscape 16:9"),
    ("Mute", "volume_up"),
    ("Play", "play_arrow"),
    ("Full screen", "fullscreen"),
    ("Disable loop", "repeat"),
    ("Add clip", "add_2"),
    ("Zoom out", "zoom_out"),
    ("Zoom in", "zoom_in"),
    ("Settings trigger", "settings"),
] + [(f"filler {n}", f"f{n}") for n in range(12)]

CLIP_SECONDS = 8


class _Clickable:
    def __init__(self, page, pair, kind):
        self.page = page
        self.pair = pair
        self.kind = kind

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def inner_text(self):
        return self.pair[1] if self.pair else None

    async def get_attribute(self, name):
        assert name == "aria-label"
        return (self.pair[0] or None) if self.pair else None

    async def is_disabled(self):
        # Measured 2026-09-16: Flow keeps 'Download scene' disabled until the editor really holds a clip, and a
        # freshly loaded page does not restore the timeline thumbnails at all.
        if self.pair and self.pair[0] == "Download scene":
            return not self.page.download_ready()
        return False

    async def click(self, timeout=None):
        self.page.click(self.pair, self.kind)


class _Matches:
    """A live locator over a list of labels: what it matches is read off the page at every call."""

    def __init__(self, page, kind, needle=None, exact=None, by="text"):
        self.page = page
        self.kind = kind
        self.needle = needle
        self.exact = exact
        self.by = by

    def _pairs(self):
        pairs = self.page.labels(self.kind)
        if self.exact is not None:
            return [pair for pair in pairs if self.exact.fits(pair[1])]
        if self.needle is None:
            return pairs
        index = 0 if self.by == "aria" else 1
        return [pair for pair in pairs if self.needle.search(pair[index])]

    def filter(self, has_text=None, has=None):
        return _Matches(self.page, self.kind, has_text or self.needle, has or self.exact, self.by)

    @property
    def first(self):
        pairs = self._pairs()
        return _Clickable(self.page, pairs[0] if pairs else None, self.kind)

    async def count(self):
        return len(self._pairs())


class _Keyboard:
    def __init__(self, page):
        self.page = page

    async def press(self, key):
        self.page.keys.append(key)

    async def type(self, text):
        self.page.typed = text


class _Download:
    def __init__(self, name):
        self.suggested_filename = name

    async def save_as(self, path):
        Path(path).write_bytes(b"film")


class _Expect:
    def __init__(self, page):
        self.page = page

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def value(self):
        async def get():
            if not self.page.downloaded:
                raise PlaywrightTimeoutError("no download started")
            return _Download(self.page.downloaded)

        return get()


class _ScenePage:
    def __init__(
        self,
        titles,
        *,
        clips=0,
        toolbar=None,
        builds_after=0,
        adds=True,
        saves=True,
        title_boxes=1,
        stray_boxes=0,
        label_lag_ms=0,
        clip_button_after=0,
    ):
        self.titles = list(titles)
        self.title_boxes = title_boxes
        # An editable text that is NOT the scene title, which is what the header anchor exists to step around.
        self.stray_boxes = stray_boxes
        self.menu_items = None
        self.adds = adds
        self.saves = saves
        self.saved = clips > 0
        # The truth about the scene, and what the page is willing to say about it. Measured 2026-09-16: a freshly
        # loaded page does NOT rebuild the thumbnail strip (0 images even after 14 s) while the Total duration label
        # does survive the load, though it can lag behind a click. An earlier version of this fake rebuilt the strip
        # on construction, which is the opposite of the measurement, and it let a driver that could not tell a new
        # clip from a strip waking up pass every test.
        self.clips = clips
        self.stale_clips = clips
        self.label_lag_ms = label_lag_ms
        self.lag_until = 0
        self.overlay = None
        self.toolbar = list(TOOLBAR if toolbar is None else toolbar)
        self.builds_after = builds_after
        # The timeline's own button arrives after the rest of the toolbar: live run 2026-09-16, a scene that already
        # held a clip crossed the button count while Add clip was still missing, and the driver read 0 and gave up.
        self.clip_button_after = clip_button_after
        self.elapsed_ms = 0
        self.clicked = []
        self.keys = []
        self.url = ""
        self.typed = None
        self.downloaded = None
        self.added = []

    def shown_clips(self):
        return self.clips if self.elapsed_ms >= self.lag_until else self.stale_clips

    def duration_text(self):
        seconds = self.shown_clips() * CLIP_SECONDS
        return (
            "crop_landscape 16:9 volume_up Current time: 00:00:00 skip_previous play_arrow skip_next "
            f"Total duration: 00:{seconds:02d}:00 fullscreen repeat 00 01 02 add_2 zoom_out zoom_in"
        )

    def labels(self, kind):
        if kind == "builder":
            return [("", self.duration_text())]
        if kind == "toolbar":
            if self.elapsed_ms < self.builds_after:
                return self.toolbar[:7]
            if self.elapsed_ms < self.clip_button_after:
                return [pair for pair in self.toolbar if "Add clip" not in pair[0]]
            return self.toolbar
        if kind == "overlay":
            # Overlay items carry no aria-label, only their own text, ligature included.
            if self.overlay == "menu":
                if self.menu_items is not None:
                    return self.menu_items
                return [("", "add Add clip"), ("", "keyboard_double_arrow_right Extend (Veo 3.1 - Lite)")]
            if self.overlay == "picker":
                rows = [("", title) for title in self.titles]
                return [("", "videocam Videos"), ("", "upload Upload media"), *rows, ("", "Add media")]
            return []
        if kind == "title_box":
            return [("", "title")] * self.title_boxes
        if kind == "any_editable":
            return [("", "title")] * self.title_boxes + [("", "stray")] * self.stray_boxes
        return []

    def click(self, pair, kind):
        if pair is None:
            raise AssertionError("clicked nothing")
        aria, text = pair
        self.clicked.append(aria or text)
        if kind == "toolbar" and "Add media" in aria:
            self.overlay = "menu"
        elif kind == "overlay" and "New scene" in text:
            self.url = f"{FlowSession.project_url(PROJECT)}/scene/made-1"
            self.overlay = None
        elif kind == "toolbar" and "Add clip" in aria:
            self.overlay = "menu" if self.clips else "picker"
        elif kind == "overlay" and "Add clip" in text:
            self.overlay = "picker"
        elif kind == "overlay" and text in self.titles:
            self.added.append(text)
            if self.adds:
                self.stale_clips = self.clips
                self.clips += 1
                self.lag_until = self.elapsed_ms + self.label_lag_ms
                self.saved = self.saves
            self.overlay = None
        elif kind == "toolbar" and "Download scene" in aria:
            self.downloaded = "pB-probe_20260916.mp4"

    def download_ready(self):
        return bool(self.clips and self.saved)

    def get_by_role(self, role, name=None):
        assert role == "button"
        return _Matches(self, "toolbar", name, by="aria")

    def locator(self, selector, has=None):
        if selector == "button":
            return _Matches(self, "toolbar")
        if selector == scenes.BUILDER:
            return _Matches(self, "builder")
        if "overlay" in selector or "dialog" in selector:
            return _Matches(self, "overlay")
        if selector == scenes.TITLE_BOX:
            return _Matches(self, "title_box")
        if "flow-editable-text" in selector:
            # A wider selector reaches every editable text on the page, the composer's included.
            return _Matches(self, "any_editable")
        raise AssertionError(f"unexpected selector {selector!r}")

    def get_by_text(self, text, exact=False):
        return _Text(text, exact)

    def expect_download(self, timeout=None):
        return _Expect(self)

    @property
    def keyboard(self):
        return _Keyboard(self)

    async def wait_for_timeout(self, ms):
        self.elapsed_ms += ms


class _SceneSession:
    def __init__(self, page, listing):
        self.page = page
        self.listing = listing
        self.urls = []

    async def goto(self, url, *, ready=None, timeout_ms=60_000):
        self.urls.append(url)

    project_url = staticmethod(FlowSession.project_url)


def _media(media_id, title, kind="video"):
    return {"id": media_id, "title": title, "kind": kind}


def _scene_answers(monkeypatch, media, listing_after=None):
    """capture for the rename, list_scenes for the confirmation."""

    async def fake_capture(session, action, *, settle):
        await action()
        return {"BpMsoe": [[]]}

    async def fake_list(session, project_id, *, include_trashed=False):
        return listing_after or []

    monkeypatch.setattr(scenes, "capture", fake_capture)
    monkeypatch.setattr(scenes, "list_scenes", fake_list)


def test_create_types_the_title_into_the_one_editable_box_of_the_header(monkeypatch):
    # Scoped re-review 2026-09-16: scenes.create had no test at all, so reverting it to `.first` of the old union
    # selector broke nothing, on a page whose buttons include Start generation.
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage([], toolbar=[("Add media", "add"), *TOOLBAR])
    page.menu_items = [("", "movie_edit New scene")]
    session = _SceneSession(page, media)

    result = asyncio.run(scenes.create(session, PROJECT, "a name"))

    assert result["scene_id"] == "made-1" and result["title"] == "a name"
    assert page.typed == "a name" and "Enter" in page.keys


def test_create_ignores_an_editable_text_that_is_not_the_scene_title(monkeypatch):
    # The anchor is the point: the scene page also carries a composer, and a wider selector would count it, then
    # either refuse or, worse, type the title into it next to a Start generation button.
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage([], toolbar=[("Add media", "add"), *TOOLBAR], stray_boxes=1)
    page.menu_items = [("", "movie_edit New scene")]

    result = asyncio.run(scenes.create(_SceneSession(page, media), PROJECT, "a name"))

    assert result["title"] == "a name" and page.typed == "a name"


def test_rename_ignores_an_editable_text_that_is_not_the_scene_title(monkeypatch):
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media, listing_after=[{"scene_id": "scene-1", "title": "new name"}])
    page = _ScenePage([], stray_boxes=1)

    result = asyncio.run(scenes.rename(_SceneSession(page, media), PROJECT, "scene-1", "new name"))

    assert result["title"] == "new name" and page.typed == "new name"


def test_create_refuses_when_the_page_shows_more_than_one_editable_title(monkeypatch):
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage([], toolbar=[("Add media", "add"), *TOOLBAR], title_boxes=2)
    page.menu_items = [("", "movie_edit New scene")]

    with pytest.raises(LookupError, match="2 editable titles"):
        asyncio.run(scenes.create(_SceneSession(page, media), PROJECT, "a name"))
    assert page.typed is None


def test_rename_refuses_when_the_page_shows_more_than_one_editable_title(monkeypatch):
    # Measured 2026-09-16: a scene page holds exactly one flow-editable-text, in the header, and the composer's
    # prompt box is not one. Typing into `.first` of a wider match would be a guess beside a Start generation button.
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media, listing_after=[{"scene_id": "scene-1", "title": "new name"}])
    page = _ScenePage([], title_boxes=2)

    with pytest.raises(LookupError, match="2 editable titles"):
        asyncio.run(scenes.rename(_SceneSession(page, media), PROJECT, "scene-1", "new name"))
    assert page.typed is None


def test_rename_types_the_title_and_confirms_it_in_the_listing(monkeypatch):
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media, listing_after=[{"scene_id": "scene-1", "title": "new name"}])
    page = _ScenePage([])

    result = asyncio.run(scenes.rename(_SceneSession(page, media), PROJECT, "scene-1", "new name"))

    assert page.typed == "new name" and "Enter" in page.keys
    assert result == {"scene_id": "scene-1", "title": "new name", "rpcids": ["BpMsoe"]}


def test_rename_refuses_a_title_that_normalizes_to_nothing(monkeypatch):
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage([])

    with pytest.raises(ValueError, match="title"):
        asyncio.run(scenes.rename(_SceneSession(page, media), PROJECT, "scene-1", BLANK_TITLE))
    assert page.typed is None


def test_rename_fails_when_the_listing_still_shows_the_old_title(monkeypatch):
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media, listing_after=[{"scene_id": "scene-1", "title": "old"}])
    page = _ScenePage([])

    with pytest.raises(RuntimeError, match="still reads"):
        asyncio.run(scenes.rename(_SceneSession(page, media), PROJECT, "scene-1", "new name"))


# Zzl0ze[6], measured 2026-09-17 (plan character-generation T9, src/video/probes/scene_editor.py): every scene's
# clips, each [[clip_id, _, _, [title, created, _, _, clip_workflow_id, _, updated], project_id], scene_id,
# [index or null for 0, [length], [start], [end], [1]]], listed in no particular order. Zzl0ze[4] entry [5] is the
# scene's aspect ratio, 1 for 9:16 and 2 for 16:9. These three are scene dancer-test-2 exactly as Flow listed them;
# its downloaded film plays preparing, rehearsing, streaming.
DANCER = "8c6c91da-0a5d-418d-ab3f-9aa957453aa8"
PREPARING = "05a0775f-c0fe-4e4f-b7ca-b967f042fce5"
STREAMING = "521495e1-d3d6-45b4-ac21-610b58f4ae30"
REHEARSING = "c6ec005d-2b4d-4a68-a292-580c0b9ecf5c"


def _clip_entry(clip_id, title, scene_id, index, *, length=(8,), start=(), end=(8,)):
    meta = [
        title,
        [1789625843, 500039000],
        None,
        None,
        "89e4ca45-0a8c-4830-a80f-68c15a3e896d",
        None,
        [1789625855, 0],
    ]
    return [
        [clip_id, None, None, meta, PROJECT],
        scene_id,
        [index, list(length), list(start), list(end), [1]],
    ]


def _scene_entry(scene_id, title, aspect, *, trashed=False):
    flags = [True] if trashed else []
    return [scene_id, title, None, [1789625670, 221531000], [1789626160, 693905000], aspect, None, flags]


def _timeline_listing(scene_entries, clip_entries):
    return {"Zzl0ze": [[None, [], [], [], list(scene_entries), [], list(clip_entries), []]]}


DANCER_CLIPS = [
    _clip_entry(PREPARING, "Dancer preparing for livestream", DANCER, None),
    _clip_entry(STREAMING, "Dancer streaming in studio", DANCER, 2),
    _clip_entry(REHEARSING, "Young woman rehearsing hip-hop d" + chr(0x2026), DANCER, 1),
]


def test_scene_clips_come_back_in_the_order_flow_keeps_not_the_order_they_are_listed_in(monkeypatch):
    _flow_answers(monkeypatch, _timeline_listing([_scene_entry(DANCER, "dancer-test-2", 1)], DANCER_CLIPS))

    result = asyncio.run(scenes.timeline(_Session([]), PROJECT, DANCER))

    assert [(c["position"], c["clip_id"]) for c in result["clips"]] == [
        (0, PREPARING),
        (1, REHEARSING),
        (2, STREAMING),
    ]
    assert result["clips"][1]["title"] == "Young woman rehearsing hip-hop d" + chr(0x2026)


def test_scene_clips_leave_out_every_other_scenes_clips(monkeypatch):
    other = [
        _clip_entry("9e7765e2-7f2b-4b8e-9a51-0c1d2e3f4a5b", "Model sailboat on wooden desk", OTHER, None)
    ]
    listing = _timeline_listing(
        [_scene_entry(DANCER, "dancer-test-2", 1), _scene_entry(OTHER, "pB", 2)], other + DANCER_CLIPS
    )
    _flow_answers(monkeypatch, listing)

    result = asyncio.run(scenes.timeline(_Session([]), PROJECT, DANCER))

    assert [c["clip_id"] for c in result["clips"]] == [PREPARING, REHEARSING, STREAMING]


@pytest.mark.parametrize(("aspect", "expected"), [(1, "9:16"), (2, "16:9"), (7, None), (None, None)])
def test_scene_clips_read_the_aspect_ratio_off_the_scene_entry(monkeypatch, aspect, expected):
    _flow_answers(
        monkeypatch, _timeline_listing([_scene_entry(DANCER, "dancer-test-2", aspect)], DANCER_CLIPS)
    )

    result = asyncio.run(scenes.timeline(_Session([]), PROJECT, DANCER))

    assert result["aspect"] == expected
    assert result["title"] == "dancer-test-2" and result["scene_id"] == DANCER and result["trashed"] is False


def test_scene_clips_count_what_each_clip_plays_from_its_start_to_its_end(monkeypatch):
    clips = [
        _clip_entry(PREPARING, "a", DANCER, None),
        _clip_entry(REHEARSING, "b", DANCER, 1, start=(2, 500000000), end=(8,)),
        _clip_entry(STREAMING, "c", DANCER, 2, length=(10,), end=(6, 250000000)),
    ]
    _flow_answers(monkeypatch, _timeline_listing([_scene_entry(DANCER, "d", 1)], clips))

    result = asyncio.run(scenes.timeline(_Session([]), PROJECT, DANCER))

    assert [c["seconds"] for c in result["clips"]] == [8.0, 5.5, 6.25]
    assert result["seconds"] == 19.75


def test_scene_clips_of_a_scene_holding_none_are_empty_and_last_nothing(monkeypatch):
    _flow_answers(monkeypatch, _timeline_listing([_scene_entry(DANCER, "d", 2)], []))

    result = asyncio.run(scenes.timeline(_Session([]), PROJECT, DANCER))

    assert result["clips"] == [] and result["seconds"] == 0


@pytest.mark.parametrize(
    "indexes",
    [(None, None, 2), (None, 2, 3), (1, 2, 3)],
    ids=["two clips at 0", "a gap at 1", "nothing at 0"],
)
def test_scene_clips_refuse_positions_that_repeat_or_skip_rather_than_guess_an_order(monkeypatch, indexes):
    clips = [
        _clip_entry(clip_id, "t", DANCER, index)
        for clip_id, index in zip((PREPARING, REHEARSING, STREAMING), indexes, strict=True)
    ]
    _flow_answers(monkeypatch, _timeline_listing([_scene_entry(DANCER, "d", 1)], clips))

    with pytest.raises(ValueError, match="not guessing"):
        asyncio.run(scenes.timeline(_Session([]), PROJECT, DANCER))


def test_scene_clips_refuse_a_scene_the_listing_does_not_have(monkeypatch):
    _flow_answers(monkeypatch, _timeline_listing([_scene_entry(OTHER, "pB", 2)], DANCER_CLIPS))

    with pytest.raises(LookupError, match=f"scene {DANCER} is not in the project listing"):
        asyncio.run(scenes.timeline(_Session([]), PROJECT, DANCER))


def test_scene_clips_say_when_the_scene_sits_in_the_trash(monkeypatch):
    listing = _timeline_listing([_scene_entry(DANCER, "dancer-test-2", 1, trashed=True)], DANCER_CLIPS)
    _flow_answers(monkeypatch, listing)

    result = asyncio.run(scenes.timeline(_Session([]), PROJECT, DANCER))

    assert result["trashed"] is True and len(result["clips"]) == 3


# The scene editor as probes/scene_editor.py measured it on 2026-09-17 (plan character-generation T9):
# - the listing (Zzl0ze) is where Flow keeps a scene: [1] media descriptors, [4] scenes with the aspect ratio at [5],
#   [6] every scene's clips with their index;
# - a loaded page selects clip 0, and the Add clip button sits inside the selected clip, or on the empty track;
# - Add clip on a scene holding clips opens a menu first (Add clip, Extend (Veo 3.1 - Lite)), on an empty scene the
#   picker straight away; the picker's newest row is already selected; clicking another row only selects it, while
#   clicking the selected row adds it at once; 'Add media' adds the selected row;
# - an add fires oWTRd [project, scene, [media_id], index], index being the selected clip's plus one, the page shows the
#   clip at once, and Flow stores it only when its reply comes back 11 to 14 s later, followed by GoMJte;
# - 'Toggle aspect ratio' fires BpMsoe [project, scene, [scene_id, _, _, _, _, 1 or 2], [["aspect_ratio"]]].
EDITOR_SCENE = "376cd4f9-51e6-404c-9198-55905ccca835"
WALKING = ("dbc80b71-a620-42ee-a646-c3ca2979da1b", "Person walking toward camera")
HOLDING = ("d314b53e-6b25-4a87-94bd-f884a7c4f1fe", "Person holding wooden sailboat m" + chr(0x2026))
SMILING = ("fcefc9b5-82dc-4c10-87c5-4742d6ec9dbe", "Person walking and smiling")
ORBIT = ("05202cf2-07c4-4a36-9123-1edc489cca24", "Slow orbit around wooden sailboat")
CAFE = ("981ca76a-f5e6-42df-a841-d6ede6fdae7a", "Woman speaking at sidewalk cafe")
CLIPS_SELECTOR = ".timeline-contents > .clip"


def _batch_body(rpcid, payload):
    return (
        ")]}'\n\n120\n"
        + json.dumps([["wrb.fr", rpcid, json.dumps(payload), None, None, None, "generic"]])
        + "\n"
    )


class _Call:
    def __init__(self, rpcid, payload):
        self.url = f"https://flow.google.com/_/PinholeUi/data/batchexecute?rpcids={rpcid}&rt=c"
        self.post_data = urlencode(
            {"f.req": json.dumps([[[rpcid, json.dumps(payload), None, "generic"]]]), "at": "x"}
        )


class _Answer:
    def __init__(self, call, rpcid, payload):
        self.request = call
        self.url = call.url
        self.body = _batch_body(rpcid, payload)

    async def text(self):
        return self.body


class _EditorElement:
    def __init__(self, page, kind, key):
        self.page = page
        self.kind = kind
        self.key = key

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def get_attribute(self, name):
        return self.page.attribute(self.kind, self.key, name)

    async def inner_text(self):
        return self.page.text_of(self.kind, self.key)

    async def click(self, timeout=None, button="left"):
        if self.kind == "clip":
            self.page.scroll_to(self.key)
        self.page.click(self.kind, self.key, button)

    async def scroll_into_view_if_needed(self, timeout=None):
        self.page.scroll_to(self.key)

    async def bounding_box(self):
        return self.page.box(self.key)

    async def is_disabled(self):
        return self.page.disabled(self.kind, self.key)


class _EditorDownload:
    """A scene film as Playwright hands it over: the file Chrome wrote, and save_as, which streams it back."""

    def __init__(self, page):
        self.page = page
        self.suggested_filename = "pe-scene_20260917164409.mp4"

    async def path(self):
        if self.page.download_fails:
            raise RuntimeError("download.path: canceled")
        written = self.page.artifacts / f"artifact-{self.page.elapsed_ms}"
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_bytes(self.page.film)
        return str(written)

    async def failure(self):
        return "canceled" if self.page.download_fails else None

    async def save_as(self, path):
        # Measured 2026-09-17: Playwright's Python save_as streams the film in 1 MiB chunks, and a browser that closed
        # midway left a film of exactly 18 MiB under its final name in the dancer test.
        raise AssertionError("save_as streams the film chunk by chunk over the driver pipe")


class _EditorExpect:
    def __init__(self, page, timeout):
        self.page = page
        self.timeout = timeout
        self.started = page.elapsed_ms

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def is_done(self):
        return self.page.download is not None or self.page.elapsed_ms - self.started >= self.timeout

    @property
    def value(self):
        async def get():
            if self.page.download is None:
                raise PlaywrightTimeoutError(
                    f'Timeout {self.timeout}ms exceeded while waiting for event "download"'
                )
            return self.page.download

        return get()


class _Mouse:
    """Drag and drop as Angular CDK sees it: a press on a clip, moves, a release over the clip whose place it takes."""

    def __init__(self, page):
        self.page = page
        self.at = (0, 0)
        self.held = None

    async def move(self, x, y, steps=1):
        self.at = (x, y)

    async def down(self):
        self.held = self.page.clip_at(*self.at)

    async def up(self):
        source, self.held = self.held, None
        self.page.drop(source, self.at)


class _EditorMatches:
    """A live locator over the editor: what it matches is read off the page at every call."""

    def __init__(self, page, kind, keys):
        self.page = page
        self.kind = kind
        self.keys = keys

    def _keys(self):
        return self.keys()

    def filter(self, has_text=None, has=None):
        def narrowed():
            kept = []
            for key in self._keys():
                text = self.page.text_of(self.kind, key)
                if has_text is not None and not has_text.search(text):
                    continue
                if has is not None and not has.fits(self.page.title_of(self.kind, key)):
                    continue
                kept.append(key)
            return kept

        return _EditorMatches(self.page, self.kind, narrowed)

    def nth(self, index):
        keys = self._keys()
        return _EditorElement(self.page, self.kind, keys[index] if index < len(keys) else None)

    @property
    def first(self):
        return self.nth(0)

    async def count(self):
        return len(self._keys())


class _Editor:
    """The scene page and the Flow behind it, both as measured on 2026-09-17 (see the note above)."""

    def __init__(self, videos, *, clips=(), aspect=2, others=()):
        self.videos = list(videos)
        self.others = list(others)
        self.server = [self._clip(media) for media in clips]
        self.aspect = aspect
        self.shown = []
        self.shown_aspect = aspect
        self.selected = None
        self.overlay = None
        self.row = None
        self.elapsed_ms = 0
        self.listeners = {"request": [], "response": []}
        self.pending = []
        self.clicked = []
        self.sent = []
        self.loads = 0
        self.listings = 0
        # What can go wrong, each measured or reasoned from a measurement.
        self.reply_ms = 12_000
        self.answers = True
        self.sends = True
        self.stores = True
        # Listing reads that still miss a clip Flow already answered for (measured 2026-09-29: all four adds of a film).
        self.stale_listings = 0
        self.lagging = None
        self.old_aspect = None
        self.selects_on_click = True
        self.row_click_selects = None
        self.insert_at = None
        self.page_clips = None
        self.picker = None
        self.rows_after = 0
        self.sends_media = None
        self.sends_aspect = None
        self.shuffles = False
        self.saving = False
        self.left_while_saving = False
        self.toolbar = list(TOOLBAR)
        self.builds_after = 0
        self.add_clip_after = 0
        self.add_clip_buttons = 1
        self.toggles = 1
        self.zoom = 0
        self.zooms = True
        self.scroll = 0
        self.drops_at = None
        self.context_items = None
        self.deletes_index = None
        self._mouse = _Mouse(self)
        self.viewport_size = {"width": 1280, "height": 720}
        self.twice_selected = False
        self.two_rows_selected = False
        self.row_click_adds = False
        self.sends_scene = None
        self.exporting = False
        self.snacks = []
        self.download = None
        self.exports = True
        self.hands_over = True
        self.download_fails = False
        self.export_ms = 34_000
        self.handoff_ms = 2_000
        self.film = b"film" * 64
        self.artifacts = None
        self.menu_items = None

    @staticmethod
    def _clip(media):
        media_id, title = media
        return {
            "clip_id": f"clip-{media_id[:8]}-{title[:4]}",
            "media_id": media_id,
            "title": title,
            "seconds": 8,
        }

    def media(self):
        return [*self.videos, *self.others]

    def listing(self):
        self.listings += 1
        descriptors = [
            [media_id, None, None, [title, [1789600000, 0], None, None, f"wf-{media_id[:8]}"], PROJECT]
            for media_id, title in self.media()
        ]
        listed_aspect = self.aspect
        if self.old_aspect is not None and self.stale_listings > 0:
            listed_aspect = self.old_aspect
            self.stale_listings -= 1
        scene = [EDITOR_SCENE, "pe-scene", None, [1789637131, 0], [1789637141, 0], listed_aspect, None, []]
        entries = [
            [
                [
                    clip["clip_id"],
                    None,
                    None,
                    [clip["title"], [1789637209, 0], None, None, "wf", None],
                    PROJECT,
                ],
                EDITOR_SCENE,
                [index or None, [8], [], [clip["seconds"]], [1]],
            ]
            for index, clip in enumerate(self.server)
            if not (self.lagging == clip["clip_id"] and self.stale_listings > 0)
        ]
        if self.lagging and self.stale_listings > 0:
            self.stale_listings -= 1
        # Flow lists clips in no particular order; reversed here so a reader that trusts listing order is caught.
        return [None, descriptors, [], [], [scene], [], list(reversed(entries)), []]

    def load(self):
        self.loads += 1
        self.shown = [dict(clip) for clip in self.server]
        if self.page_clips is not None:
            self.shown = self.shown[: self.page_clips]
        self.shown_aspect = self.aspect
        self.selected = 0 if self.shown else None
        self.overlay = None

    # Locators.
    def locator(self, selector, has=None):
        if selector == "button":
            return _EditorMatches(self, "toolbar", self._toolbar_keys)
        if selector == CLIPS_SELECTOR:
            return _EditorMatches(self, "clip", lambda: list(range(len(self.shown))))
        if selector == f"{CLIPS_SELECTOR}.selected":
            return _EditorMatches(self, "clip", self._selected_keys)
        if selector == scenes.ROW:
            return _EditorMatches(self, "row", self._row_keys)
        if selector == f"{scenes.ROW}[aria-selected=true]":
            return _EditorMatches(
                self,
                "row",
                lambda: [k for k in self._row_keys() if k == self.row or (self.two_rows_selected and k == 0)],
            )
        if selector == scenes.OVERLAY:
            return _EditorMatches(self, "overlay", self._overlay_keys)
        if selector == scenes.MENU_ITEM:
            return _EditorMatches(self, "overlay", self._menu_item_keys)
        if selector == scenes.SNACKBAR:
            return _EditorMatches(self, "snack", lambda: list(range(len(self.snacks))))
        raise AssertionError(f"unexpected selector {selector!r}")

    # Download scene, measured 2026-09-17: the film is built inside the page. The button turns into a disabled spinner
    # and a snackbar reads 'Exporting your scene…'; once built, a snackbar reads 'Your scene has been downloaded!' and
    # the file comes from a blob URL about 2 s later. A 40 s film took 33.5 s and 41.5 s.
    def expect_download(self, timeout=None):
        return _EditorExpect(self, timeout)

    def disabled(self, kind, key):
        if kind == "named" and self._named_label(key)[0] == "Download scene":
            return not self.shown or self.exporting
        return False

    def _export(self):
        if not self.exports:
            return
        self.exporting = True
        self.snacks = ["Exporting your scene… Dismiss"]

        def built():
            self.exporting = False
            self.snacks = ["check_circlecheck_circle Your scene has been downloaded! Dismiss"]
            if self.hands_over:
                self._later(self.handoff_ms, lambda: setattr(self, "download", _EditorDownload(self)))

        self._later(self.export_ms, built)

    # The timeline as laid out on screen, measured 2026-09-17: an 8 s clip is 816 px wide from x 21, every zoom-out
    # halves that, the viewport is 1280 px wide, and a locator click scrolls its clip into view.

    @property
    def mouse(self):
        return self._mouse

    def clip_width(self):
        return 816 / 2 ** (self.zoom if self.zooms else 0)

    def _clamp(self):
        # Like a browser: the timeline cannot scroll past its content, which shrinks with every zoom-out.
        content = 21 + len(self.shown) * self.clip_width()
        self.scroll = min(max(self.scroll, 0), max(0, content - self.viewport_size["width"]))

    def box(self, index):
        self._clamp()
        width = self.clip_width()
        return {"x": 21 + index * width - self.scroll, "y": 471, "width": width, "height": 62}

    def scroll_to(self, index):
        box = self.box(index)
        if box["x"] < 0 or box["x"] + box["width"] > self.viewport_size["width"]:
            self.scroll += box["x"] - 21
        self._clamp()

    def clip_at(self, x, y):
        # Measured 2026-09-17: the Add clip button belongs to the selected clip and sits 5 to 29 px past its right edge,
        # over the next clip, so a press there grabs the selected clip.
        if self.selected is not None and 490 <= y <= 514:
            right = self.box(self.selected)["x"] + self.box(self.selected)["width"]
            if right + 5 <= x <= right + 29:
                return self.selected
        index = int((x - 21 + self.scroll) // self.clip_width())
        return index if 471 <= y <= 533 and 0 <= index < len(self.shown) else None

    def drop(self, source, at):
        """CDK sorting: a dragged clip passes a sibling only once the pointer is past that sibling's middle."""
        x, y = at
        if source is None or not 471 <= y <= 533:
            return
        middles = [self.box(i)["x"] + self.box(i)["width"] / 2 for i in range(len(self.shown))]
        passed_right = [i for i in range(source + 1, len(self.shown)) if x > middles[i]]
        passed_left = [i for i in range(source) if x < middles[i]]
        target = max(passed_right) if passed_right else min(passed_left) if passed_left else source
        if target == source:
            return
        target = self.drops_at if self.drops_at is not None else target
        self.clicked.append(f"drag {source} to {target}")
        self.shown.insert(target, self.shown.pop(source))
        self._save()

    def _selected_keys(self):
        if self.selected is None:
            return []
        return [self.selected, 0] if self.twice_selected else [self.selected]

    def _menu_item_keys(self):
        """Both menus are made of role=menuitem items: Add clip's (Add clip, Extend) and a clip's right-click menu."""
        if self.overlay == "menu":
            return self._overlay_keys()
        return self._context_keys()

    def _context_keys(self):
        if not (isinstance(self.overlay, tuple) and self.overlay[0] == "context"):
            return []
        items = self.context_items or [
            ("", "content_copyCopy"),
            ("", "content_pastePaste"),
            ("", "saveSave to Project"),
            ("", "downloadDownload"),
            ("", "deleteDelete"),
        ]
        return [("context", i, label) for i, label in enumerate(items)]

    def _save(self):
        """The page sends the whole ordered list (GoMJte) and Flow keeps it once it answers."""
        if not self.sends:
            return
        order = [dict(clip) for clip in self.shown]
        payload = [
            PROJECT,
            EDITOR_SCENE,
            [
                [
                    [clip["clip_id"], None, None, [None, None, None, None, "wf"]],
                    EDITOR_SCENE,
                    [i or None, [8], [], [8]],
                ]
                for i, clip in enumerate(order)
            ],
        ]
        call = _Call("GoMJte", payload)
        self.sent.append(("GoMJte", payload))
        self._emit("request", call)

        def reply():
            if self.stores:
                self.server = order
            self._emit("response", _Answer(call, "GoMJte", [[]]))

        if self.answers:
            self._later(1_500, reply)

    def get_by_role(self, role, name=None):
        assert role == "button"
        return _EditorMatches(
            self, "named", lambda: [k for k in self._named_keys() if name.search(self.text_of("named", k))]
        )

    def get_by_text(self, text, exact=False):
        return _Text(text, exact)

    def on(self, event, handler):
        self.listeners[event].append(handler)

    def remove_listener(self, event, handler):
        self.listeners[event].remove(handler)

    async def wait_for_timeout(self, ms):
        self.elapsed_ms += ms
        due = [item for item in self.pending if item[0] <= self.elapsed_ms]
        self.pending = [item for item in self.pending if item[0] > self.elapsed_ms]
        for _, deliver in due:
            deliver()
        for _ in range(3):
            await asyncio.sleep(0)

    def _toolbar_keys(self):
        count = 7 if self.elapsed_ms < self.builds_after else len(self.toolbar)
        return list(range(count))

    def _named_keys(self):
        # Add clip and the aspect toggle depend on the page's state, so they come from it, not from the fixed toolbar.
        stateful = ("Add clip", "Toggle aspect ratio")
        keys = [("toolbar", i) for i in self._toolbar_keys() if not self.toolbar[i][0].startswith(stateful)]
        on_track = not self.shown or self.selected is not None
        if on_track and self.elapsed_ms >= self.add_clip_after:
            keys += [("add_clip", n) for n in range(self.add_clip_buttons)]
        keys += [("toggle", n) for n in range(self.toggles)]
        return keys

    def rows(self):
        return self.videos if self.picker is None else self.picker

    def _row_keys(self):
        if self.overlay != "picker" or self.elapsed_ms < self.rows_after:
            return []
        return list(range(len(self.rows())))

    def _overlay_keys(self):
        if self.overlay == "menu":
            items = self.menu_items or [
                ("", "addAdd clip"),
                ("", "keyboard_double_arrow_rightExtend (Veo 3.1 - Lite)"),
            ]
            return [("menu", i, label) for i, label in enumerate(items)]
        if self.overlay == "picker":
            # Measured 2026-09-17: the picker's rows are buttons in the same overlay pane as its controls.
            rows = [("rowbutton", i, ("", title)) for i, (_, title) in enumerate(self.rows())]
            return [*rows, ("button", 0, ("", "uploadUpload media")), ("button", 1, ("", "Add media"))]
        return []

    def attribute(self, kind, key, name):
        if kind == "clip":
            if name == "class":
                marks = ["cdk-drag", "mat-context-menu-trigger", "clip"]
                marks += ["is-first"] if key == 0 else []
                marks += ["is-last"] if key == len(self.shown) - 1 else []
                marks += ["selected"] if key == self.selected else []
                return " ".join(marks)
            return None
        if kind == "row":
            return {"aria-selected": "true" if key == self.row else "false"}.get(name)
        if kind == "overlay":
            return key[2][0] or None if name == "aria-label" else None
        if kind == "named":
            return self._named_label(key)[0] if name == "aria-label" else None
        if kind == "toolbar":
            return self.toolbar[key][0] if name == "aria-label" else None
        return None

    def _named_label(self, key):
        what, index = key
        if what == "toolbar":
            return self.toolbar[index]
        if what == "add_clip":
            return next(pair for pair in self.toolbar if pair[0].startswith("Add clip"))
        return (
            "Toggle aspect ratio",
            "crop_portrait9:16" if self.shown_aspect == 1 else "crop_landscape16:9",
        )

    def text_of(self, kind, key):
        if kind == "named":
            label = self._named_label(key)
            if label[0] == "Download scene" and self.exporting:
                return "Download scene progress_activity"
            return " ".join(part for part in label if part)
        if kind == "overlay":
            return key[2][1]
        if kind == "snack":
            return self.snacks[key]
        if kind == "row":
            return self.rows()[key][1]
        if kind == "toolbar":
            return self.toolbar[key][1]
        return ""

    def title_of(self, kind, key):
        return self.rows()[key][1] if kind == "row" else self.text_of(kind, key)

    # What a click does.
    def click(self, kind, key, button):
        if key is None:
            raise AssertionError(f"clicked a {kind} that is not there")
        if kind == "clip":
            self.clicked.append(f"clip {key}" + (" right" if button == "right" else ""))
            if self.selects_on_click:
                self.selected = key
            if button == "right":
                self.overlay = ("context", key)
            return
        if kind == "named":
            label = self._named_label(key)
            self.clicked.append(label[0])
            if key[0] == "add_clip":
                self.overlay = "menu" if self.shown else "picker"
                self.row = 0 if self.overlay == "picker" else None
            elif key[0] == "toggle":
                self._toggle()
            elif label[0] == "Zoom out":
                self.zoom = min(self.zoom + 1, 4)
            elif label[0] == "Download scene":
                self._export()
            return
        if kind == "overlay" and key[0] == "rowbutton":
            return self.click("row", key[1], button)
        if kind == "overlay":
            label = key[2][1]
            self.clicked.append(label)
            if key[0] == "menu" and "Add clip" in label:
                self.overlay, self.row = "picker", 0
            elif label == "Add media":
                self._add(self.rows()[self.row])
            elif key[0] == "context" and label.endswith("Delete"):
                self.overlay = None
                gone = self.deletes_index if self.deletes_index is not None else self.selected
                self.shown.pop(gone)
                self.selected = None
                self._save()
            return
        if kind == "row":
            self.clicked.append(f"row {self.rows()[key][1]}")
            if key == self.row or self.row_click_adds:
                self._add(self.rows()[key])
            else:
                self.row = key if self.row_click_selects is None else self.row_click_selects
            return
        raise AssertionError(f"clicked a {kind}")

    def _emit(self, event, item):
        for handler in list(self.listeners[event]):
            handler(item)

    def _later(self, ms, deliver):
        self.pending.append((self.elapsed_ms + ms, deliver))

    def _add(self, media):
        self.overlay = None
        if not self.sends:
            return
        index = (self.selected + 1 if self.shown else 0) if self.insert_at is None else self.insert_at
        clip = self._clip(media)
        payload = [PROJECT, EDITOR_SCENE, [self.sends_media or media[0]], index]
        call = _Call("oWTRd", payload)
        self.sent.append(("oWTRd", payload))
        self._emit("request", call)
        self.shown.insert(index, clip)

        def reply():
            if self.stores:
                self.server.insert(index, clip)
                if self.stale_listings:
                    self.lagging = clip["clip_id"]
            if self.shuffles:
                self.server[0], self.server[1] = self.server[1], self.server[0]
            if not self.answers:
                # Flow stored the clip and its answer never reached this browser: measured on 2026-09-17, two adds
                # passed 90 s with no reply and one of them had landed.
                return
            entry = [
                [clip["clip_id"], None, None, [clip["title"]], PROJECT],
                EDITOR_SCENE,
                [index or None, [8]],
            ]
            self._emit(
                "response", _Answer(call, "oWTRd", [[entry], [["wf", PROJECT, clip["clip_id"], "CAE"]]])
            )
            save = [
                PROJECT,
                EDITOR_SCENE,
                [[[c["clip_id"]], EDITOR_SCENE, [i or None]] for i, c in enumerate(self.shown)],
            ]
            save_call = _Call("GoMJte", save)
            self.sent.append(("GoMJte", save))
            self._emit("request", save_call)
            self.saving = True

            def saved():
                self.saving = False
                self._emit("response", _Answer(save_call, "GoMJte", [[]]))

            self._later(1_500, saved)

        self._later(self.reply_ms, reply)

    def _toggle(self):
        self.shown_aspect = 1 if self.shown_aspect == 2 else 2
        if not self.sends:
            return
        payload = [
            PROJECT,
            EDITOR_SCENE,
            [
                self.sends_scene or EDITOR_SCENE,
                None,
                None,
                None,
                None,
                self.sends_aspect or self.shown_aspect,
            ],
            [["aspect_ratio"]],
        ]
        call = _Call("BpMsoe", payload)
        self.sent.append(("BpMsoe", payload))
        self._emit("request", call)
        wanted = self.shown_aspect

        def reply():
            if self.stores:
                if self.stale_listings:
                    self.old_aspect = self.aspect
                self.aspect = wanted
            self._emit(
                "response", _Answer(call, "BpMsoe", [[EDITOR_SCENE, "pe-scene", None, None, None, wanted]])
            )

        if self.answers:
            self._later(900, reply)


class _EditorSession:
    def __init__(self, page):
        self.page = page
        self.urls = []

    async def goto(self, url, *, ready=None, timeout_ms=60_000):
        self.urls.append(url)
        if "/scene/" in url:
            self.page.load()

    project_url = staticmethod(FlowSession.project_url)


def _editor(monkeypatch, page):
    async def fake_listing(session, project_id):
        assert project_id == PROJECT
        session.urls.append("listing")
        # Reading the listing leaves the scene page: a save still in flight would be cut off.
        session.page.left_while_saving |= session.page.saving
        return session.page.listing()

    monkeypatch.setattr(scenes, "_listing", fake_listing)
    return _EditorSession(page)


def test_set_aspect_clicks_the_toggle_once_and_confirms_the_ratio_in_the_listing(monkeypatch):
    page = _Editor([WALKING], clips=[WALKING], aspect=2)
    session = _editor(monkeypatch, page)

    result = asyncio.run(scenes.set_aspect(session, PROJECT, EDITOR_SCENE, "9:16"))

    assert page.clicked == ["Toggle aspect ratio"]
    assert page.sent == [
        ("BpMsoe", [PROJECT, EDITOR_SCENE, [EDITOR_SCENE, None, None, None, None, 1], [["aspect_ratio"]]])
    ]
    assert page.aspect == 1
    assert result == {"scene_id": EDITOR_SCENE, "aspect": "9:16", "changed": True, "rpcids": ["BpMsoe"]}
    assert session.urls[-1] == "listing"


def test_set_aspect_leaves_a_scene_already_at_that_ratio_alone(monkeypatch):
    page = _Editor([WALKING], aspect=1)
    session = _editor(monkeypatch, page)

    result = asyncio.run(scenes.set_aspect(session, PROJECT, EDITOR_SCENE, "9:16"))

    assert result == {"scene_id": EDITOR_SCENE, "aspect": "9:16", "changed": False, "rpcids": []}
    assert page.clicked == [] and page.loads == 0


def test_set_aspect_refuses_a_ratio_flow_does_not_offer_before_reading_anything(monkeypatch):
    page = _Editor([WALKING])
    session = _editor(monkeypatch, page)

    with pytest.raises(ValueError, match="9:16 or 16:9"):
        asyncio.run(scenes.set_aspect(session, PROJECT, EDITOR_SCENE, "4:3"))
    assert session.urls == []


def test_set_aspect_refuses_when_the_editor_already_shows_the_ratio_the_listing_does_not(monkeypatch):
    # The toggle flips whatever is shown: clicking it here would set the ratio the agent did NOT ask for.
    page = _Editor([WALKING], aspect=2)
    session = _editor(monkeypatch, page)
    load = page.load

    def load_showing_portrait():
        load()
        page.shown_aspect = 1

    page.load = load_showing_portrait

    with pytest.raises(RuntimeError, match="not toggling"):
        asyncio.run(scenes.set_aspect(session, PROJECT, EDITOR_SCENE, "9:16"))
    assert page.clicked == []


def test_set_aspect_fails_when_the_listing_still_reads_the_old_ratio_after_its_one_click(monkeypatch):
    page = _Editor([WALKING], aspect=2)
    page.stores = False
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="still reads 16:9"):
        asyncio.run(scenes.set_aspect(session, PROJECT, EDITOR_SCENE, "9:16"))
    assert page.clicked == ["Toggle aspect ratio"]


def test_set_aspect_fails_when_the_click_sends_nothing_and_does_not_click_again(monkeypatch):
    page = _Editor([WALKING], aspect=2)
    page.sends = False
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="no aspect ratio change left the page"):
        asyncio.run(scenes.set_aspect(session, PROJECT, EDITOR_SCENE, "9:16"))
    assert page.clicked == ["Toggle aspect ratio"]


def test_set_aspect_refuses_a_page_with_two_toggles(monkeypatch):
    page = _Editor([WALKING], aspect=2)
    page.toggles = 2
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="2 Toggle aspect ratio"):
        asyncio.run(scenes.set_aspect(session, PROJECT, EDITOR_SCENE, "9:16"))
    assert page.clicked == []


def test_set_aspect_fails_when_the_change_it_sent_asks_for_the_other_ratio(monkeypatch):
    page = _Editor([WALKING], aspect=2)
    page.sends_aspect = 2
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="asked Flow for"):
        asyncio.run(scenes.set_aspect(session, PROJECT, EDITOR_SCENE, "9:16"))
    assert page.clicked == ["Toggle aspect ratio"]


def test_set_aspect_fails_when_flow_never_answers_the_change(monkeypatch):
    page = _Editor([WALKING], aspect=2)
    page.answers = False
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="Flow never answered the aspect ratio change"):
        asyncio.run(scenes.set_aspect(session, PROJECT, EDITOR_SCENE, "9:16"))
    assert page.clicked == ["Toggle aspect ratio"]


def _stored(page):
    return [clip["title"] for clip in page.server]


def test_add_clip_selects_the_last_clip_first_so_the_new_clip_lands_at_the_end(monkeypatch):
    # Measured 2026-09-17: a new clip goes right after the SELECTED clip and a loaded page selects clip 0, so without
    # selecting the last clip this add would have gone between WALKING and HOLDING.
    page = _Editor([CAFE, SMILING, HOLDING, WALKING], clips=[WALKING, HOLDING])
    session = _editor(monkeypatch, page)

    result = asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SMILING[0]))

    assert page.sent[0] == ("oWTRd", [PROJECT, EDITOR_SCENE, [SMILING[0]], 2])
    assert page.clicked[0] == "clip 1"
    assert _stored(page) == [WALKING[1], HOLDING[1], SMILING[1]]
    assert result["position"] == 2 and result["clip_id"] == page.server[2]["clip_id"]
    assert [clip["title"] for clip in result["clips"]] == [WALKING[1], HOLDING[1], SMILING[1]]
    assert (result["scene_id"], result["media_id"], result["title"]) == (EDITOR_SCENE, SMILING[0], SMILING[1])
    assert result["seconds"] == 24 and result["rpcids"] == ["GoMJte", "oWTRd"]


def test_add_clip_on_an_empty_scene_adds_at_position_0_without_selecting_a_clip(monkeypatch):
    page = _Editor([CAFE, WALKING])
    session = _editor(monkeypatch, page)

    result = asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))

    assert page.sent[0] == ("oWTRd", [PROJECT, EDITOR_SCENE, [WALKING[0]], 0])
    assert _stored(page) == [WALKING[1]] and result["position"] == 0
    assert not any(label.startswith("clip ") for label in page.clicked)


def test_add_clip_selects_its_row_then_clicks_add_media_exactly_once(monkeypatch):
    # Measured 2026-09-17: clicking a row only selects it; 'Add media' is what adds.
    page = _Editor([CAFE, WALKING])
    session = _editor(monkeypatch, page)

    asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))

    assert page.clicked == ["Add clip", f"row {WALKING[1]}", "Add media"]


def test_add_clip_never_clicks_the_row_the_picker_already_selected(monkeypatch):
    # Measured by the dancer test on 2026-09-17: clicking the row the picker already selected adds it at once, so a
    # driver that clicked it and then 'Add media' put the clip on twice.
    page = _Editor([WALKING, CAFE])
    session = _editor(monkeypatch, page)

    asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))

    assert page.clicked == ["Add clip", "Add media"]
    assert _stored(page) == [WALKING[1]]


def test_add_clip_waits_for_flow_to_store_the_clip_before_reading_the_listing_back(monkeypatch):
    # The page shows the clip at once; Flow stores it only when its reply comes back, 11 to 14 s later (2026-09-17).
    page = _Editor([CAFE, WALKING])
    page.reply_ms = 40_000
    session = _editor(monkeypatch, page)

    result = asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))

    assert page.elapsed_ms >= 40_000
    assert result["position"] == 0 and _stored(page) == [WALKING[1]]


def test_add_clip_fails_when_its_click_sends_no_add_and_never_clicks_again(monkeypatch):
    page = _Editor([CAFE, WALKING])
    page.sends = False
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="no add request left the page"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))
    assert page.clicked.count("Add media") == 1


def test_add_clip_answers_from_the_listing_when_flows_own_reply_never_arrives(monkeypatch):
    # Measured 2026-09-17 (acceptance run 3): two adds passed 90 s with no reply and one of them had landed anyway, so
    # the listing, not the reply, decides whether the clip is there.
    page = _Editor([CAFE, WALKING], clips=[CAFE])
    page.answers = False
    session = _editor(monkeypatch, page)

    result = asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))

    assert result["position"] == 1 and result["title"] == WALKING[1] and result["answered"] is False
    assert _stored(page) == [CAFE[1], WALKING[1]]
    assert page.clicked.count("Add media") == 1


def test_add_clip_fails_when_flow_neither_answers_nor_puts_the_clip_on(monkeypatch):
    page = _Editor([CAFE, WALKING], clips=[CAFE])
    page.answers = False
    page.stores = False
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="Flow never answered the add") as caught:
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))
    assert "scene_clips before adding again" in str(caught.value)
    assert page.clicked.count("Add media") == 1


def test_add_clip_that_flow_answers_says_so(monkeypatch):
    page = _Editor([CAFE, WALKING], clips=[CAFE])
    session = _editor(monkeypatch, page)

    result = asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))

    assert result["answered"] is True and result["position"] == 1


def test_add_clip_fails_when_flow_answers_but_the_listing_does_not_hold_the_clip(monkeypatch):
    page = _Editor([CAFE, WALKING], clips=[CAFE])
    page.stores = False
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="the listing reads") as caught:
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))
    assert "scene_clips before adding again" in str(caught.value)


def test_add_clip_reports_a_clip_that_landed_anywhere_but_the_end(monkeypatch):
    page = _Editor([CAFE, SMILING, WALKING], clips=[WALKING, CAFE])
    page.insert_at = 0
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="position 0 instead of 2") as caught:
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SMILING[0]))
    assert "scene_move_clip" in str(caught.value) and "do not add it again" in str(caught.value)


def test_add_clip_refuses_when_the_page_shows_fewer_clips_than_flow_lists(monkeypatch):
    # Clips carry no id on the page, so the last one is only the last one when the page and the listing agree.
    page = _Editor([CAFE, SMILING, WALKING], clips=[WALKING, CAFE])
    page.page_clips = 1
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="shows 1 clips while Flow lists 2"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SMILING[0]))
    assert page.sent == [] and page.clicked == []


def test_add_clip_refuses_when_clicking_the_last_clip_does_not_select_it(monkeypatch):
    page = _Editor([CAFE, SMILING, WALKING], clips=[WALKING, CAFE])
    page.selects_on_click = False
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="did not select it"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SMILING[0]))
    assert page.sent == [] and "Add clip" not in page.clicked


def test_add_clip_refuses_when_the_picker_selects_another_row_than_its_own(monkeypatch):
    page = _Editor([CAFE, SMILING, WALKING])
    page.row_click_selects = 1
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="not adding"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))
    assert "Add media" not in page.clicked and page.sent == []


def test_add_clip_lets_the_pages_own_save_land_before_leaving_the_scene(monkeypatch):
    # Measured 2026-09-17: right after Flow answers the add, the page sends the whole ordered clip list (GoMJte).
    page = _Editor([CAFE, WALKING], clips=[CAFE])
    session = _editor(monkeypatch, page)

    asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))

    assert page.left_while_saving is False


def test_add_clip_fails_when_the_add_it_sent_names_another_media(monkeypatch):
    page = _Editor([CAFE, WALKING], clips=[CAFE])
    page.sends_media = CAFE[0]
    session = _editor(monkeypatch, page)

    with pytest.raises(
        RuntimeError, match=f"names media \\['{CAFE[0]}'\\] instead of {WALKING[0]}"
    ) as caught:
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))
    assert "scene_remove_clip" in str(caught.value) and "do not add it again" in str(caught.value)


def test_add_clip_fails_when_the_clips_already_there_no_longer_keep_their_order(monkeypatch):
    page = _Editor([CAFE, SMILING, WALKING], clips=[WALKING, CAFE])
    page.shuffles = True
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="the listing reads"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SMILING[0]))


def test_add_clip_waits_for_picker_rows_that_render_after_the_dialog_opens(monkeypatch):
    page = _Editor([CAFE, WALKING])
    page.rows_after = 4_000
    session = _editor(monkeypatch, page)

    asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))

    assert _stored(page) == [WALKING[1]]


def test_add_clip_goes_through_the_menu_when_the_scene_already_holds_a_clip(monkeypatch):
    page = _Editor([CAFE, WALKING], clips=[CAFE])
    session = _editor(monkeypatch, page)

    asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))

    assert "addAdd clip" in page.clicked
    assert _stored(page) == [CAFE[1], WALKING[1]]


def test_add_clip_takes_the_row_whose_title_is_exactly_the_medias_not_one_containing_it(monkeypatch):
    # The bug class plan D paid for in delete and restore: a substring match makes "Scene 1" fit "Scene 10" too.
    page = _Editor([(OTHER, "Scene 10"), (SCENE, "Scene 1")])
    session = _editor(monkeypatch, page)

    asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SCENE))

    assert _stored(page) == ["Scene 1"]


def test_add_clip_refuses_when_the_picker_shows_that_title_twice(monkeypatch):
    # The listing knows one media under this title, yet the picker offers two rows: still a coin flip, still refused.
    page = _Editor([(SCENE, "alpha")])
    page.picker = [(SCENE, "alpha"), (OTHER, "alpha")]
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="2 picker rows"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SCENE))
    assert page.sent == []


def test_add_clip_refuses_when_no_picker_row_carries_that_title(monkeypatch):
    page = _Editor([(SCENE, "Model sailboat on wooden desk")])
    page.picker = [(OTHER, "something else")]
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="not offered by the picker"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SCENE))
    assert page.sent == []


def test_add_clip_refuses_a_media_the_project_does_not_have(monkeypatch):
    page = _Editor([(OTHER, "kept")])
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="is not in the project"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SCENE))
    assert page.loads == 0


def test_add_clip_refuses_a_media_whose_title_normalizes_to_nothing(monkeypatch):
    page = _Editor([(SCENE, BLANK_TITLE)])
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="has no title"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SCENE))
    assert page.loads == 0


def test_add_clip_waits_for_the_toolbar_instead_of_judging_a_half_built_page(monkeypatch):
    # Measured 2026-09-16: a scene page dumped too early showed 7 buttons where a built one shows 27.
    page = _Editor([CAFE, WALKING])
    page.builds_after = 3_000
    session = _editor(monkeypatch, page)

    asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))

    assert page.elapsed_ms >= 3_000 and _stored(page) == [WALKING[1]]


def test_add_clip_waits_for_a_control_that_arrives_after_the_rest_of_the_toolbar(monkeypatch):
    # Live run 2026-09-16: on a scene that already held a clip the page crossed the button count while the timeline's
    # own Add clip button was still missing, and the driver read 0 matches and gave up.
    page = _Editor([CAFE, WALKING], clips=[CAFE])
    page.add_clip_after = 4_000
    session = _editor(monkeypatch, page)

    asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))

    assert _stored(page) == [CAFE[1], WALKING[1]]


def test_add_clip_gives_up_when_the_scene_page_never_finishes_building(monkeypatch):
    page = _Editor([CAFE, WALKING])
    page.builds_after = 10_000_000
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="never finished building"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))


def test_add_clip_refuses_a_menu_that_offers_only_the_paid_item(monkeypatch):
    # The Add clip menu's second item is Extend (Veo 3.1 - Lite), 10 credits, still there on 2026-09-17. This pins the
    # route, not the guard: the menu filter never matches Extend.
    page = _Editor([CAFE, WALKING], clips=[CAFE])
    page.menu_items = [("", "keyboard_double_arrow_rightExtend (Veo 3.1 - Lite)")]
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="is not offered by the picker"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))
    assert page.sent == [] and not any("Extend" in label for label in page.clicked)


def test_add_clip_refuses_a_menu_item_whose_paid_name_is_only_in_its_text(monkeypatch):
    # Scoped re-review 2026-09-16: overlay items carry no aria-label, so the text is the load bearing half here.
    page = _Editor([CAFE, WALKING], clips=[CAFE])
    page.menu_items = [("", "addAdd clip and extend this shot (Veo 3.1 - Lite)")]
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="spends credits"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))
    assert page.sent == []


def test_add_clip_refuses_a_namesake_that_differs_only_by_an_invisible_character(monkeypatch):
    # Scoped re-review 2026-09-16: without the normalization on both sides, the clash goes unseen and the driver
    # clicks the row of the OTHER media while still reporting the media_id it was asked for.
    page = _Editor([(SCENE, "alpha")], others=[(OTHER, "al" + chr(0x200B) + "pha")])
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="2 media in this project are titled"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SCENE))
    assert page.loads == 0


def test_add_clip_refuses_when_another_media_in_the_project_shares_the_title(monkeypatch):
    # Review 2026-09-16: the picker is tabbed, so two media with one title can show as a single row and the click is a
    # coin flip that still reports the media_id asked for. Judge the clash on the listing, which is already in hand.
    page = _Editor([(SCENE, "probe_upload.png")], others=[(OTHER, "probe_upload.png")])
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="2 media in this project are titled"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SCENE))
    assert page.loads == 0


def test_add_clip_adds_a_clip_whose_title_reads_like_a_paid_control(monkeypatch):
    # Review 2026-09-16: Flow titles a clip from its prompt, so the guard meant for controls refused real clips and
    # told the agent they spend credits. A row is a clip, not a control.
    page = _Editor([CAFE, (SCENE, "robot generates a sandwich")])
    session = _editor(monkeypatch, page)

    asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SCENE))

    assert _stored(page) == ["robot generates a sandwich"]


def test_add_clip_refuses_a_control_whose_paid_name_is_only_in_its_aria_label(monkeypatch):
    # Review 2026-09-16: these buttons keep their name in aria-label and only a ligature as text, so a guard reading
    # the text alone was blind for every control matched by name.
    page = _Editor([CAFE, WALKING])
    page.toolbar = [
        ("Add clip Extend (Veo 3.1 - Lite)" if aria == "Add clip" else aria, text) for aria, text in TOOLBAR
    ]
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="spends credits"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))
    assert page.clicked == []


def test_add_clip_refuses_when_two_controls_answer_to_the_same_name(monkeypatch):
    page = _Editor([CAFE, WALKING])
    page.add_clip_buttons = 2
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="2 controls match"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))
    assert page.clicked == []


def _ids(page):
    return [clip["clip_id"] for clip in page.server]


FIVE = [WALKING, ORBIT, SMILING, HOLDING, CAFE]


def test_remove_clip_deletes_the_named_clip_from_its_menu_and_confirms_the_rest_in_the_listing(monkeypatch):
    # Measured 2026-09-17: right-clicking a clip selects it and opens its menu; Delete asks nothing and sends GoMJte
    # with the clips that remain.
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)
    gone = page.server[2]["clip_id"]
    kept = [clip_id for clip_id in _ids(page) if clip_id != gone]

    result = asyncio.run(scenes.remove_clip(session, PROJECT, EDITOR_SCENE, gone))

    assert page.clicked == ["clip 2 right", "deleteDelete"]
    assert _ids(page) == kept
    assert [entry[0][0] for entry in page.sent[0][1][2]] == kept
    assert [clip["clip_id"] for clip in result["clips"]] == kept
    assert result["removed_position"] == 2 and result["seconds"] == 32 and result["rpcids"] == ["GoMJte"]


def test_remove_clip_refuses_a_clip_id_that_is_not_on_the_timeline(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="is not on scene"):
        asyncio.run(scenes.remove_clip(session, PROJECT, EDITOR_SCENE, "clip-nowhere"))
    assert page.loads == 0 and page.sent == []


def test_remove_clip_refuses_when_the_page_shows_another_number_of_clips(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.page_clips = 4
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="shows 4 clips while Flow lists 5"):
        asyncio.run(scenes.remove_clip(session, PROJECT, EDITOR_SCENE, page.server[1]["clip_id"]))
    assert page.clicked == [] and page.sent == []


def test_remove_clip_refuses_when_the_right_click_does_not_select_the_clip(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.selects_on_click = False
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="did not select it"):
        asyncio.run(scenes.remove_clip(session, PROJECT, EDITOR_SCENE, page.server[3]["clip_id"]))
    assert "deleteDelete" not in page.clicked and page.sent == []


def test_remove_clip_refuses_a_menu_without_exactly_one_delete(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.context_items = [("", "content_copyCopy"), ("", "deleteDelete"), ("", "deleteDelete")]
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="2 controls match"):
        asyncio.run(scenes.remove_clip(session, PROJECT, EDITOR_SCENE, page.server[1]["clip_id"]))
    assert page.sent == []


def test_remove_clip_fails_when_delete_sends_nothing_and_never_clicks_it_again(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.sends = False
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="no timeline change left the page"):
        asyncio.run(scenes.remove_clip(session, PROJECT, EDITOR_SCENE, page.server[1]["clip_id"]))
    assert page.clicked.count("deleteDelete") == 1


def test_remove_clip_fails_when_the_change_it_sent_drops_another_clip(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.deletes_index = 0
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="the timeline sent to Flow reads") as caught:
        asyncio.run(scenes.remove_clip(session, PROJECT, EDITOR_SCENE, page.server[2]["clip_id"]))
    assert "scene_clips" in str(caught.value)


def test_remove_clip_fails_when_flow_never_answers_and_says_to_read_the_timeline(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.answers = False
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="Flow never answered") as caught:
        asyncio.run(scenes.remove_clip(session, PROJECT, EDITOR_SCENE, page.server[2]["clip_id"]))
    assert "scene_clips" in str(caught.value)


def test_remove_clip_fails_when_the_listing_still_holds_the_clip(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.stores = False
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="the listing reads"):
        asyncio.run(scenes.remove_clip(session, PROJECT, EDITOR_SCENE, page.server[2]["clip_id"]))


def test_move_clip_drags_a_clip_later_and_confirms_the_new_order_in_the_listing(monkeypatch):
    # Measured 2026-09-17: dragging clip 1 onto clip 3 sent GoMJte with clip 1 at index 3.
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)
    ids = _ids(page)
    moved = ids[1]
    expected = [ids[0], ids[2], ids[3], moved, ids[4]]

    result = asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, moved, 3))

    assert _ids(page) == expected
    assert [entry[0][0] for entry in page.sent[0][1][2]] == expected
    assert [clip["clip_id"] for clip in result["clips"]] == expected
    assert result["position"] == 3 and result["moved"] is True and result["rpcids"] == ["GoMJte"]


def test_move_clip_drags_a_clip_earlier(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)
    ids = _ids(page)

    asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, ids[3], 0))

    assert _ids(page) == [ids[3], ids[0], ids[1], ids[2], ids[4]]


def test_move_clip_zooms_out_until_both_places_are_on_screen_before_dragging(monkeypatch):
    # Measured 2026-09-17: five 8 s clips at 816 px each run far past a 1280 px timeline; two zoom-outs fit them.
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)
    ids = _ids(page)

    asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, ids[0], 4))

    drag = page.clicked.index("drag 0 to 4")
    assert page.clicked[:drag].count("Zoom out") >= 1
    assert _ids(page)[4] == ids[0]


def test_move_clip_gives_up_when_the_clips_never_fit_on_screen(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.zooms = False
    session = _editor(monkeypatch, page)
    ids = _ids(page)

    with pytest.raises(LookupError, match="off screen"):
        asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, ids[0], 4))
    assert page.sent == [] and not any(label.startswith("drag") for label in page.clicked)


def test_move_clip_leaves_a_clip_already_at_that_position_alone(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)
    ids = _ids(page)

    result = asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, ids[2], 2))

    assert result["moved"] is False and result["position"] == 2 and page.loads == 0 and page.sent == []


@pytest.mark.parametrize("position", [-1, 5, 99])
def test_move_clip_refuses_a_position_outside_the_timeline_before_opening_the_scene(monkeypatch, position):
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)

    with pytest.raises(ValueError, match="position must be 0 to 4"):
        asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, page.server[0]["clip_id"], position))
    assert page.loads == 0


def test_move_clip_refuses_a_clip_id_that_is_not_on_the_timeline(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="is not on scene"):
        asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, "clip-nowhere", 0))
    assert page.loads == 0


def test_move_clip_refuses_when_the_page_shows_another_number_of_clips(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.page_clips = 3
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="shows 3 clips while Flow lists 5"):
        asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, page.server[0]["clip_id"], 2))
    assert page.sent == []


def test_move_clip_fails_when_the_drop_sends_nothing(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.sends = False
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="no timeline change left the page"):
        asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, page.server[1]["clip_id"], 3))


def test_move_clip_fails_when_the_order_it_sent_is_not_the_one_asked_for(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.drops_at = 2
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="the timeline sent to Flow reads") as caught:
        asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, page.server[1]["clip_id"], 3))
    assert "scene_clips" in str(caught.value)


def test_a_stored_order_is_read_by_index_not_by_the_order_entries_arrive_in():
    payload = [
        PROJECT,
        EDITOR_SCENE,
        [
            [["c2", None, None, [None, None, None, None, "wf"]], EDITOR_SCENE, [2, [8], [], [8]]],
            [["c0", None, None, [None, None, None, None, "wf"]], EDITOR_SCENE, [None, [8], [], [8]]],
            [["c1", None, None, [None, None, None, None, "wf"]], EDITOR_SCENE, [1, [8], [], [8]]],
        ],
    ]

    assert scenes._saved_order(payload) == ["c0", "c1", "c2"]


def test_move_clip_fails_when_flow_never_answers(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.answers = False
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="Flow never answered"):
        asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, page.server[1]["clip_id"], 3))


def test_move_clip_fails_when_the_listing_does_not_show_the_new_order(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.stores = False
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="the listing reads"):
        asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, page.server[1]["clip_id"], 3))


def _downloading(monkeypatch, tmp_path, clips=FIVE):
    page = _Editor(FIVE, clips=clips)
    page.artifacts = tmp_path / "playwright-artifacts"
    return page, _editor(monkeypatch, page)


def test_download_waits_out_the_export_and_copies_the_finished_film_into_the_folder_given(
    monkeypatch, tmp_path
):
    page, session = _downloading(monkeypatch, tmp_path)
    room = tmp_path / "out" / "films"

    result = asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=room))

    film = Path(result["path"])
    assert film.parent == room and film.name.startswith(EDITOR_SCENE) and film.suffix == ".mp4"
    assert film.read_bytes() == page.film and result["bytes"] == len(page.film)
    assert result["seconds"] == 40 and result["suggested"].endswith(".mp4")
    # A list like every other scene tool answers with, so the film can be checked clip by clip (review 2026-09-17).
    assert [clip["title"] for clip in result["clips"]] == [title for _, title in FIVE]
    assert page.clicked == ["Download scene"] and page.elapsed_ms >= page.export_ms
    assert sorted(path.name for path in room.iterdir()) == [film.name]


def test_download_refuses_a_scene_with_nothing_on_the_timeline_before_opening_it(monkeypatch, tmp_path):
    page, session = _downloading(monkeypatch, tmp_path, clips=[])

    with pytest.raises(LookupError, match="no clip"):
        asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=tmp_path))
    assert page.loads == 0 and page.clicked == []


def test_download_never_overwrites_a_film_that_is_already_there(monkeypatch, tmp_path):
    # CLAUDE.md rule 5: footage is never overwritten, and a stamped name still collides inside one second.
    page, session = _downloading(monkeypatch, tmp_path)
    monkeypatch.setattr(scenes.time, "strftime", lambda fmt: "20260917_120000")
    (tmp_path / f"{EDITOR_SCENE}_20260917_120000.mp4").write_bytes(b"older film")

    with pytest.raises(FileExistsError):
        asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=tmp_path))
    assert (tmp_path / f"{EDITOR_SCENE}_20260917_120000.mp4").read_bytes() == b"older film"
    assert page.clicked == []


def test_two_downloads_of_one_scene_do_not_collide(monkeypatch, tmp_path):
    # Downloading a scene again after adding another clip is the normal way to work, so the film carries a stamp.
    page, session = _downloading(monkeypatch, tmp_path)
    stamps = iter(["20260917_120000", "20260917_120500"])
    monkeypatch.setattr(scenes.time, "strftime", lambda fmt: next(stamps))

    first = asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=tmp_path / "films"))
    page.download = None
    second = asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=tmp_path / "films"))

    assert first["path"] != second["path"]
    assert Path(first["path"]).exists() and Path(second["path"]).exists()


def test_download_refuses_when_the_page_shows_two_download_buttons(monkeypatch, tmp_path):
    page, session = _downloading(monkeypatch, tmp_path)
    page.toolbar = [*TOOLBAR, ("Download scene", "download")]

    with pytest.raises(LookupError, match="2 Download scene buttons"):
        asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=tmp_path))
    assert page.clicked == []


def test_download_fails_fast_when_its_click_starts_no_export(monkeypatch, tmp_path):
    # Measured 2026-09-16: a click can fire no download at all, which the old fixed wait turned into minutes of nothing.
    page, session = _downloading(monkeypatch, tmp_path)
    page.exports = False

    with pytest.raises(RuntimeError, match="started no export"):
        asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=tmp_path))
    assert page.elapsed_ms < 60_000 and page.clicked == ["Download scene"]


def test_download_keeps_waiting_while_a_long_film_is_still_exporting(monkeypatch, tmp_path):
    # The export runs inside the page and grows with the film: 33.5 s and 41.5 s for 40 s on a loaded machine.
    six = [*FIVE, (OTHER, "Scene six")]
    page, session = _downloading(monkeypatch, tmp_path, clips=six)
    page.export_ms = 200_000

    result = asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=tmp_path / "films"))

    assert result["seconds"] == 48 and Path(result["path"]).read_bytes() == page.film
    assert page.elapsed_ms >= 200_000


def test_download_gives_up_on_an_export_that_never_finishes(monkeypatch, tmp_path):
    page, session = _downloading(monkeypatch, tmp_path, clips=[WALKING])
    page.export_ms = 100_000_000

    with pytest.raises(RuntimeError, match="did not finish"):
        asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=tmp_path))


def test_download_says_so_when_the_page_reports_the_film_downloaded_but_no_file_arrives(
    monkeypatch, tmp_path
):
    page, session = _downloading(monkeypatch, tmp_path)
    page.hands_over = False

    with pytest.raises(RuntimeError, match="no file reached this browser"):
        asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=tmp_path))
    assert page.elapsed_ms < page.export_ms + 60_000


def test_download_reports_a_film_the_browser_failed_to_fetch(monkeypatch, tmp_path):
    page, session = _downloading(monkeypatch, tmp_path)
    page.download_fails = True

    with pytest.raises(RuntimeError, match="failed in the browser"):
        asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=tmp_path / "films"))
    assert not (tmp_path / "films").exists() or list((tmp_path / "films").iterdir()) == []


def test_download_never_leaves_a_partial_film_under_any_name_when_the_copy_breaks(monkeypatch, tmp_path):
    # The dancer test's failed run left a film of exactly 18 MiB under its final name, which ffprobe still read as 40 s.
    _, session = _downloading(monkeypatch, tmp_path)
    room = tmp_path / "films"

    def broken_copy(source, target):
        Path(target).write_bytes(Path(source).read_bytes()[:10])
        raise OSError("disk went away")

    monkeypatch.setattr(scenes.shutil, "copyfile", broken_copy)

    with pytest.raises(OSError, match="disk went away"):
        asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=room))
    assert list(room.iterdir()) == []


def test_download_refuses_a_copy_shorter_than_the_film_the_browser_wrote(monkeypatch, tmp_path):
    _, session = _downloading(monkeypatch, tmp_path)
    room = tmp_path / "films"

    def short_copy(source, target):
        Path(target).write_bytes(Path(source).read_bytes()[:10])

    monkeypatch.setattr(scenes.shutil, "copyfile", short_copy)

    with pytest.raises(OSError, match="copied 10 of"):
        asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=room))
    assert list(room.iterdir()) == []


# Review of plan character-generation T10 (2026-09-17): one blocking finding, one drag finding, and rules no test held.
def test_add_clip_on_an_empty_scene_never_takes_a_picker_row_for_the_add_clip_menu(monkeypatch):
    # Blocking finding: an empty scene opens the picker at once, whose rows are buttons in the same overlay pane. A
    # newest media titled with the words "add clip" was clicked as the menu item, and clicking the row the picker had
    # selected added that other media on the spot.
    newest = ("5b0e3a1c-8d2f-4e6a-9c7b-1a2b3c4d5e6f", "How to add clip transitions")
    page = _Editor([newest, WALKING])
    session = _editor(monkeypatch, page)

    result = asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))

    assert _stored(page) == [WALKING[1]] and result["media_id"] == WALKING[0]
    assert f"row {newest[1]}" not in page.clicked
    assert [payload[2] for rpc, payload in page.sent if rpc == "oWTRd"] == [[WALKING[0]]]


def test_add_clip_says_so_when_an_add_leaves_before_its_own_add_media_click(monkeypatch):
    # Should Flow ever add on a row click again, as it did before 2026-09-17, that add must not pass unnoticed.
    page = _Editor([CAFE, WALKING])
    page.row_click_adds = True
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="before Add media was clicked") as caught:
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))
    assert "scene_clips before adding again" in str(caught.value)
    assert "Add media" not in page.clicked


def test_add_clip_refuses_when_more_than_one_clip_reads_selected(monkeypatch):
    page = _Editor([CAFE, SMILING, WALKING], clips=[WALKING, CAFE])
    page.twice_selected = True
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="did not select it"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, SMILING[0]))
    assert page.sent == []


def test_add_clip_refuses_when_the_picker_shows_more_than_one_selected_row(monkeypatch):
    page = _Editor([CAFE, SMILING, WALKING])
    page.two_rows_selected = True
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="not adding"):
        asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))
    assert page.sent == []


def test_remove_clip_refuses_when_more_than_one_clip_reads_selected(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.twice_selected = True
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="did not select it"):
        asyncio.run(scenes.remove_clip(session, PROJECT, EDITOR_SCENE, page.server[3]["clip_id"]))
    assert page.sent == []


def test_move_clip_never_presses_on_the_add_clip_button_beside_the_selected_clip(monkeypatch):
    # Drag finding: at the fourth zoom-out an 8 s clip is 51 px wide, and the middle of clip 1 falls on the Add clip
    # button of the selected clip 0, which would drag clip 0 instead.
    many = [(f"{n:08d}-0000-4000-8000-000000000000", f"clip number {n}") for n in range(13)]
    page = _Editor(many, clips=many)
    session = _editor(monkeypatch, page)
    ids = _ids(page)

    asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, ids[1], 12))

    assert page.zoom == 4
    assert _ids(page) == [ids[0], *ids[2:], ids[1]]


def test_move_clip_refuses_a_clip_scrolled_off_the_left_of_the_screen(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    page.zooms = False
    page.scroll = 5_000
    session = _editor(monkeypatch, page)
    ids = _ids(page)

    with pytest.raises(LookupError, match="off screen"):
        asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, ids[0], 4))
    assert page.sent == []


@pytest.mark.parametrize("position", [True, 2.0, "2"])
def test_move_clip_refuses_a_position_that_is_not_a_whole_number(monkeypatch, position):
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)

    with pytest.raises(ValueError, match="position must be 0 to 4"):
        asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, page.server[0]["clip_id"], position))
    assert page.loads == 0


def test_set_aspect_refuses_a_toggle_that_shows_neither_ratio(monkeypatch):
    # The toggle flips whatever is shown, so a label naming no ratio gives no ground to click on.
    page = _Editor([WALKING], aspect=2)
    session = _editor(monkeypatch, page)
    label = page._named_label
    page._named_label = lambda key: (
        ("Toggle aspect ratio", "crop_landscape") if key[0] == "toggle" else label(key)
    )

    with pytest.raises(RuntimeError, match="not toggling"):
        asyncio.run(scenes.set_aspect(session, PROJECT, EDITOR_SCENE, "9:16"))
    assert page.clicked == []


def test_set_aspect_fails_when_the_change_it_sent_names_another_scene(monkeypatch):
    page = _Editor([WALKING], aspect=2)
    page.sends_scene = OTHER
    session = _editor(monkeypatch, page)

    with pytest.raises(RuntimeError, match="asked Flow for"):
        asyncio.run(scenes.set_aspect(session, PROJECT, EDITOR_SCENE, "9:16"))


def test_scene_changes_leave_no_listener_behind_on_the_page(monkeypatch):
    page = _Editor(FIVE, clips=FIVE[:4], aspect=2)
    session = _editor(monkeypatch, page)

    asyncio.run(scenes.set_aspect(session, PROJECT, EDITOR_SCENE, "9:16"))
    asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, CAFE[0]))
    asyncio.run(scenes.move_clip(session, PROJECT, EDITOR_SCENE, page.server[0]["clip_id"], 2))
    asyncio.run(scenes.remove_clip(session, PROJECT, EDITOR_SCENE, page.server[1]["clip_id"]))

    assert page.listeners == {"request": [], "response": []}


def test_download_never_overwrites_a_film_that_appears_while_the_scene_exports(monkeypatch, tmp_path):
    # The check before the click cannot see a file written during a 30 to 40 s export (CLAUDE.md rule 5).
    page, session = _downloading(monkeypatch, tmp_path)
    monkeypatch.setattr(scenes.time, "strftime", lambda fmt: "20260917_120000")
    target = tmp_path / f"{EDITOR_SCENE}_20260917_120000.mp4"
    export = page._export

    def export_and_collide():
        export()
        page._later(1_000, lambda: target.write_bytes(b"older film"))

    page._export = export_and_collide

    with pytest.raises(FileExistsError):
        asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=tmp_path))
    assert target.read_bytes() == b"older film"
    assert sorted(path.name for path in tmp_path.iterdir() if path.is_file()) == [target.name]


def test_download_keeps_counting_an_export_whose_snackbar_went_away(monkeypatch, tmp_path):
    # Once the page showed an export, a later poll that looks idle is not "no export started".
    page, session = _downloading(monkeypatch, tmp_path)
    page.handoff_ms = 24_000
    export = page._export

    def export_then_dismiss():
        export()
        page._later(page.export_ms + 500, lambda: setattr(page, "snacks", []))

    page._export = export_then_dismiss

    result = asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=tmp_path / "films"))

    assert result["bytes"] == len(page.film)


def test_download_gives_the_browser_twice_its_own_limits_so_its_own_errors_come_first(monkeypatch, tmp_path):
    # Review 2026-09-17: the loop counts only its sleeps, so on a slow page Playwright's timeout could fire first and turn
    # a stuck export into a retried "waiting for event" instead of the plain "did not finish".
    page, session = _downloading(monkeypatch, tmp_path, clips=[WALKING])
    asked = []
    expect = page.expect_download

    def recording(timeout=None):
        asked.append(timeout)
        return expect(timeout=timeout)

    page.expect_download = recording

    asyncio.run(scenes.download(session, PROJECT, EDITOR_SCENE, out_dir=tmp_path / "films"))

    own = scenes.EXPORT_START_MS + scenes.EXPORT_MIN_MS + scenes.HANDOFF_WAIT_MS
    assert asked == [2 * own]


def _media_in_turn(answers):
    rounds = list(answers)

    async def fake_media_ids(session, project_id):
        return rounds.pop(0) if len(rounds) > 1 else rounds[0]

    return fake_media_ids


def test_save_clip_to_project_puts_a_timeline_clip_on_the_grid(monkeypatch):
    """Measured 2026-09-18: 'Save to Project' in a clip's right-click menu fires rpc Sc7aEb and the clip reaches
    the grid as a new media (d0e23b3b..., video, listed), after the same indexing lag a saved frame shows."""
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)
    wanted = _ids(page)[1]
    seen = {"m1": {"id": "m1", "kind": "video"}}
    fresh = {"id": "saved-1", "kind": "video", "title": ORBIT[1], "created": 9}
    monkeypatch.setattr(
        scenes.clips_mod, "media_ids", _media_in_turn([seen, seen, {**seen, "saved-1": fresh}])
    )
    monkeypatch.setattr(scenes.clips_mod, "INDEX_STEP_S", 0.01)
    monkeypatch.setattr(scenes.clips_mod, "INDEX_WAIT_S", 0.05)

    async def fake_capture(session_, action, *, settle):
        await action()
        return {"Sc7aEb": [[]]}

    monkeypatch.setattr(scenes, "capture", fake_capture)

    result = asyncio.run(scenes.save_clip_to_project(session, PROJECT, EDITOR_SCENE, wanted))

    assert page.clicked == ["clip 1 right", "saveSave to Project"]
    assert result["media_id"] == "saved-1" and result["clip_id"] == wanted
    assert result["rpcids"] == ["Sc7aEb"]
    assert _ids(page) == _ids(page), "saving copies a clip, it never changes the timeline"


def test_save_clip_to_project_reports_the_rpcs_it_heard_and_still_trusts_the_listing(monkeypatch):
    """The answer names what was overheard rather than a constant. It is NOT a gate: a live run on 2026-09-18
    put the copy on the grid without Sc7aEb showing up in the window after the click, so requiring it refused a
    save that had worked. The listing is the evidence."""
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)
    wanted = _ids(page)[1]
    seen = {"m1": {"id": "m1", "kind": "video"}}
    fresh = {"id": "saved-1", "kind": "video", "title": ORBIT[1], "created": 9}
    monkeypatch.setattr(scenes.clips_mod, "media_ids", _media_in_turn([seen, {**seen, "saved-1": fresh}]))
    monkeypatch.setattr(scenes.clips_mod, "INDEX_STEP_S", 0.01)
    monkeypatch.setattr(scenes.clips_mod, "INDEX_WAIT_S", 0.05)
    heard = {"Sc7aEb": [[]], "WuwhI": [[]]}

    async def fake_capture(session_, action, *, settle):
        await action()
        return heard

    monkeypatch.setattr(scenes, "capture", fake_capture)

    result = asyncio.run(scenes.save_clip_to_project(session, PROJECT, EDITOR_SCENE, wanted))

    assert result["rpcids"] == ["Sc7aEb", "WuwhI"]

    heard.clear()
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)
    monkeypatch.setattr(scenes.clips_mod, "media_ids", _media_in_turn([seen, {**seen, "saved-1": fresh}]))

    quiet = asyncio.run(scenes.save_clip_to_project(session, PROJECT, EDITOR_SCENE, _ids(page)[1]))

    assert quiet["media_id"] == "saved-1" and quiet["rpcids"] == []


def test_save_clip_to_project_ignores_an_image_that_landed_during_the_wait(monkeypatch):
    """The indexing wait is 90 s wide; a saved frame or an upload finishing inside it is new media too, and
    answering with it hands the caller someone else's id (review 2026-09-18)."""
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)
    wanted = _ids(page)[1]
    seen = {"m1": {"id": "m1", "kind": "video"}}
    frame = {"id": "frame-9", "kind": "image", "title": "Saved frame from something", "created": 8}
    clip = {"id": "saved-1", "kind": "video", "title": ORBIT[1], "created": 9}
    monkeypatch.setattr(
        scenes.clips_mod,
        "media_ids",
        _media_in_turn([seen, {**seen, "frame-9": frame}, {**seen, "frame-9": frame, "saved-1": clip}]),
    )
    monkeypatch.setattr(scenes.clips_mod, "INDEX_STEP_S", 0.01)
    monkeypatch.setattr(scenes.clips_mod, "INDEX_WAIT_S", 0.05)

    async def fake_capture(session_, action, *, settle):
        await action()
        return {"Sc7aEb": [[]]}

    monkeypatch.setattr(scenes, "capture", fake_capture)

    result = asyncio.run(scenes.save_clip_to_project(session, PROJECT, EDITOR_SCENE, wanted))

    assert result["media_id"] == "saved-1", "an image that landed meanwhile is not the clip that was saved"


def test_save_clip_to_project_takes_the_copy_that_carries_the_clips_own_title(monkeypatch):
    """Measured 2026-09-18 (refix_live3): the copy reaches the grid under the clip's own title, 'Camera drifts
    to left'. Another video landing inside the 90 s wait is a video too, so the title the timeline already knows
    is what tells them apart."""
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)
    wanted = _ids(page)[1]
    title = asyncio.run(scenes.timeline(session, PROJECT, EDITOR_SCENE))["clips"][1]["title"]
    seen = {"m1": {"id": "m1", "kind": "video"}}
    stranger = {"id": "gen-9", "kind": "video", "title": "A dancer in a studio", "created": 8}
    copy = {"id": "saved-1", "kind": "video", "title": title, "created": 9}
    monkeypatch.setattr(
        scenes.clips_mod,
        "media_ids",
        _media_in_turn([seen, {**seen, "gen-9": stranger}, {**seen, "gen-9": stranger, "saved-1": copy}]),
    )
    monkeypatch.setattr(scenes.clips_mod, "INDEX_STEP_S", 0.01)
    monkeypatch.setattr(scenes.clips_mod, "INDEX_WAIT_S", 0.05)

    async def fake_capture(session_, action, *, settle):
        await action()
        return {}

    monkeypatch.setattr(scenes, "capture", fake_capture)

    result = asyncio.run(scenes.save_clip_to_project(session, PROJECT, EDITOR_SCENE, wanted))

    assert result["media_id"] == "saved-1", "a generation landing meanwhile is not the copy"


def test_save_clip_to_project_refuses_a_clip_id_that_is_not_on_the_timeline(monkeypatch):
    page = _Editor(FIVE, clips=FIVE)
    session = _editor(monkeypatch, page)

    with pytest.raises(LookupError, match="not on scene"):
        asyncio.run(scenes.save_clip_to_project(session, PROJECT, EDITOR_SCENE, "no-such-clip"))
    assert page.clicked == []


def test_add_clip_reads_the_listing_again_while_it_still_misses_the_clip_flow_answered_for(monkeypatch):
    """Measured 2026-09-29 (film LeeLyLy review 01): all four adds were answered by Flow, the listing read right after
    missed the clip, and the tool raised although every clip was on the timeline a moment later."""
    page = _Editor([CAFE, WALKING], clips=[CAFE])
    page.stale_listings = 2
    session = _editor(monkeypatch, page)

    result = asyncio.run(scenes.add_clip(session, PROJECT, EDITOR_SCENE, WALKING[0]))

    assert result["answered"] is True and result["position"] == 1
    assert session.urls.count("listing") >= 3


def test_set_aspect_reads_the_listing_again_while_it_still_shows_the_old_ratio(monkeypatch):
    """Measured 2026-09-29 (film Toi nay mac gi): the scene was 9:16 a moment later, yet the tool reported the listing
    still read 16:9, the same lag scene_add_clip had."""
    page = _Editor([WALKING], clips=[WALKING], aspect=2)
    page.stale_listings = 2
    session = _editor(monkeypatch, page)

    result = asyncio.run(scenes.set_aspect(session, PROJECT, EDITOR_SCENE, "9:16"))

    assert result["aspect"] == "9:16" and result["changed"] is True
    assert page.clicked == ["Toggle aspect ratio"]
