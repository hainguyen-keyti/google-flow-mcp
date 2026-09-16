import asyncio
import re
from pathlib import Path

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


def test_delete_gives_up_when_the_grid_never_shows_every_active_scene(monkeypatch):
    _flow_answers(monkeypatch, _listing(_entry(SCENE, "alpha", False), _entry(OTHER, "bravo", False)))
    session = _Session(["bravo"], _GridPage)

    with pytest.raises(LookupError, match="grid shows 1 scene tiles for 2 active scenes"):
        asyncio.run(scenes.delete(session, PROJECT, SCENE))
    assert session.page.trashed == []


def test_delete_refuses_when_the_grid_shows_more_scene_tiles_than_active_scenes(monkeypatch):
    # A tile the listing does not know would end the wait early and can be the only title match before the right one
    # renders, which is how restore once restored a namesake.
    _flow_answers(monkeypatch, _listing(_entry(SCENE, "Scene 1", False)))
    session = _Session({"Scene 1 draft": 0, "bravo": 0, "Scene 1": 2_000}, _GridPage)

    with pytest.raises(LookupError, match="grid shows 2 scene tiles for 1 active scenes"):
        asyncio.run(scenes.delete(session, PROJECT, SCENE))
    assert session.page.trashed == []


def test_delete_counts_a_tile_that_appears_during_the_last_wait(monkeypatch):
    _flow_answers(
        monkeypatch,
        _listing(_entry(SCENE, "alpha", False), _entry(OTHER, "bravo", False)),
        TRASHED,
        _listing(_entry(SCENE, "alpha", True), _entry(OTHER, "bravo", False)),
    )
    session = _Session({"alpha": 0, "bravo": 15_000}, _GridPage)

    assert asyncio.run(scenes.delete(session, PROJECT, SCENE))["trashed"] is True
    assert session.page.trashed == ["alpha"]
    assert session.page.elapsed_ms >= 15_000


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
        label_lag_ms=0,
    ):
        self.titles = list(titles)
        self.title_boxes = title_boxes
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
        self.elapsed_ms = 0
        self.clicked = []
        self.keys = []
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
            return self.toolbar if self.elapsed_ms >= self.builds_after else self.toolbar[:7]
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
        return []

    def click(self, pair, kind):
        if pair is None:
            raise AssertionError("clicked nothing")
        aria, text = pair
        self.clicked.append(aria or text)
        if kind == "toolbar" and "Add clip" in aria:
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
        if "flow-editable-text" in selector:
            return _Matches(self, "title_box")
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
    """reader.project for the media titles, capture for the rename, list_scenes for the confirmation."""

    async def fake_project(session, project_id, *args, **kwargs):
        return {"media": media}

    async def fake_capture(session, action, *, settle):
        await action()
        return {"BpMsoe": [[]]}

    async def fake_list(session, project_id, *, include_trashed=False):
        return listing_after or []

    monkeypatch.setattr(scenes.reader, "project", fake_project, raising=False)
    monkeypatch.setattr(scenes, "capture", fake_capture)
    monkeypatch.setattr(scenes, "list_scenes", fake_list)


def test_add_clip_adds_the_media_whose_title_is_exactly_the_one_asked_for(monkeypatch):
    media = [_media(SCENE, "Model sailboat on wooden desk"), _media(OTHER, "probe_upload.png", "image")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Model sailboat on wooden desk", "probe_upload.png"])
    session = _SceneSession(page, media)

    result = asyncio.run(scenes.add_clip(session, PROJECT, "scene-1", SCENE))

    assert page.added == ["Model sailboat on wooden desk"]
    assert result["duration_before"] == "00:00:00" and result["duration_after"] == "00:08:00"
    assert result["changed"] is True
    # The scene it was asked about, not the project page and not another scene.
    assert session.urls == [f"{FlowSession.project_url(PROJECT)}/scene/scene-1"]


def test_add_clip_goes_through_the_menu_when_the_scene_already_holds_a_clip(monkeypatch):
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Model sailboat on wooden desk"], clips=1)

    asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))

    assert "add Add clip" in page.clicked
    assert page.added == ["Model sailboat on wooden desk"]


def test_add_clip_takes_the_row_whose_title_is_exactly_the_medias_not_one_containing_it(monkeypatch):
    # The bug class plan D paid for in delete and restore: a substring match makes "Scene 1" fit "Scene 10" too.
    media = [_media(SCENE, "Scene 1"), _media(OTHER, "Scene 10")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Scene 10", "Scene 1"])

    asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))

    assert page.added == ["Scene 1"]


def test_add_clip_refuses_when_the_picker_shows_that_title_twice(monkeypatch):
    # The listing knows one media under this title, yet the picker offers two rows: still a coin flip, still refused.
    media = [_media(SCENE, "alpha")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["alpha", "alpha"])

    with pytest.raises(RuntimeError, match="2 picker rows"):
        asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))
    assert page.added == []


def test_add_clip_refuses_when_no_picker_row_carries_that_title(monkeypatch):
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["something else"])

    with pytest.raises(LookupError, match="not offered by the picker"):
        asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))
    assert page.added == []


def test_add_clip_refuses_a_media_the_project_does_not_have(monkeypatch):
    media = [_media(OTHER, "kept")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["kept"])

    with pytest.raises(LookupError, match="is not in the project"):
        asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))


def test_add_clip_refuses_a_media_whose_title_normalizes_to_nothing(monkeypatch):
    media = [_media(SCENE, BLANK_TITLE)]
    _scene_answers(monkeypatch, media)
    page = _ScenePage([BLANK_TITLE])

    with pytest.raises(LookupError, match="has no title"):
        asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))
    assert page.added == []


def test_add_clip_waits_for_the_toolbar_instead_of_judging_a_half_built_page(monkeypatch):
    # Measured 2026-09-16: a scene page dumped too early showed 7 buttons where a built one shows 27.
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Model sailboat on wooden desk"], builds_after=3_000)

    asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))

    assert page.elapsed_ms >= 3_000
    assert page.added == ["Model sailboat on wooden desk"]


def test_add_clip_gives_up_when_the_scene_page_never_finishes_building(monkeypatch):
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Model sailboat on wooden desk"], builds_after=10_000_000)

    with pytest.raises(LookupError, match="never finished building"):
        asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))


def test_add_clip_never_clicks_the_paid_item_of_the_add_clip_menu(monkeypatch):
    # The Add clip menu's second item is Extend (Veo 3.1 - Lite), 10 credits, and a scene that already holds a clip
    # opens that menu before the picker.
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Model sailboat on wooden desk"], clips=1)
    page.menu_items = [("", "keyboard_double_arrow_right Extend (Veo 3.1 - Lite)")]

    with pytest.raises(LookupError, match="is not offered by the picker"):
        asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))
    assert page.added == []
    assert not any("Extend" in label for label in page.clicked)


def test_add_clip_reports_a_duration_that_never_changed_instead_of_failing(monkeypatch):
    # The picker closes itself either way, so a click that changed nothing looks exactly like a good one. Report it
    # and let the agent read the film back: raising after a click that DID land makes it add the clip twice.
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Model sailboat on wooden desk"], adds=False)

    result = asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))

    assert result["changed"] is False
    assert result["duration_before"] == result["duration_after"] == "00:00:00"
    assert page.added == ["Model sailboat on wooden desk"]


def test_add_clip_on_a_scene_that_already_holds_one_is_judged_by_the_duration(monkeypatch):
    # Scoped re-review 2026-09-16: the old post-check counted timeline thumbnails, which a cold page never restores,
    # so for every add after the first it read 0 then 8 and called a click that added nothing a success.
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Model sailboat on wooden desk"], clips=1, adds=False)

    result = asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))

    assert result["changed"] is False
    assert result["duration_before"] == result["duration_after"] == "00:08:00"


def test_add_clip_waits_for_a_duration_label_that_lags_behind_the_click(monkeypatch):
    # Measured 2026-09-16: the label was still reading 00:08:00 right after a second clip landed.
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Model sailboat on wooden desk"], clips=1, label_lag_ms=6_000)

    result = asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))

    assert result["duration_before"] == "00:08:00" and result["duration_after"] == "00:16:00"
    assert result["changed"] is True
    assert page.elapsed_ms >= 6_000


def test_add_clip_does_not_reload_to_confirm_an_add_that_already_landed(monkeypatch):
    # Live run 2026-09-16: add_clip raised "did not stick" after 20 s and the very next call downloaded the 8.0 s film
    # anyway. Review 2026-09-16: the answer was worthless anyway, since "Download scene" only says the scene holds SOME
    # clip, so from the second add on it said yes whatever happened. The film is the proof, not a reload.
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Model sailboat on wooden desk"], saves=False)
    session = _SceneSession(page, media)

    result = asyncio.run(scenes.add_clip(session, PROJECT, "scene-1", SCENE))

    assert page.added == ["Model sailboat on wooden desk"]
    assert "confirmed_after_reload" not in result
    assert len(session.urls) == 1 and page.elapsed_ms < scenes.SAVE_WAIT_MS


def test_add_clip_refuses_when_another_media_in_the_project_shares_the_title(monkeypatch):
    # Review 2026-09-16: the picker is tabbed, so two media with one title can show as a single row and the click is a
    # coin flip that still reports the media_id asked for. Judge the clash on the listing, which is already in hand.
    media = [_media(SCENE, "probe_upload.png"), _media(OTHER, "probe_upload.png", "image")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["probe_upload.png"])

    with pytest.raises(RuntimeError, match="2 media in this project are titled"):
        asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))
    assert page.added == []


def test_add_clip_adds_a_clip_whose_title_reads_like_a_paid_control(monkeypatch):
    # Review 2026-09-16: Flow titles a clip from its prompt, so the guard meant for controls refused real clips and
    # told the agent they spend credits. A row is a clip, not a control.
    media = [_media(SCENE, "robot generates a sandwich")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["robot generates a sandwich"])

    asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))

    assert page.added == ["robot generates a sandwich"]


def test_add_clip_refuses_a_control_whose_paid_name_is_only_in_its_aria_label(monkeypatch):
    # Review 2026-09-16: these buttons keep their name in aria-label and only a ligature as text, so a guard reading
    # the text alone was blind for every control matched by name.
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    toolbar = [
        ("Add clip Extend (Veo 3.1 - Lite)" if aria == "Add clip" else aria, text) for aria, text in TOOLBAR
    ]
    page = _ScenePage(["Model sailboat on wooden desk"], toolbar=toolbar)

    with pytest.raises(RuntimeError, match="spends credits"):
        asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))
    assert page.clicked == []


def test_add_clip_refuses_when_two_controls_answer_to_the_same_name(monkeypatch):
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Model sailboat on wooden desk"], toolbar=[*TOOLBAR, ("Add clip", "add_2")])

    with pytest.raises(LookupError, match="2 controls match"):
        asyncio.run(scenes.add_clip(_SceneSession(page, media), PROJECT, "scene-1", SCENE))
    assert page.clicked == []


def test_download_refuses_when_the_page_shows_two_download_buttons(monkeypatch, tmp_path):
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage([], clips=1, toolbar=[*TOOLBAR, ("Download scene", "download")])

    with pytest.raises(LookupError, match="2 Download scene buttons"):
        asyncio.run(scenes.download(_SceneSession(page, media), PROJECT, "scene-1", out_dir=tmp_path))
    assert page.downloaded is None


def test_download_writes_into_the_folder_it_was_given(monkeypatch, tmp_path):
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage([], clips=1)
    room = tmp_path / "films"

    result = asyncio.run(scenes.download(_SceneSession(page, media), PROJECT, "scene-1", out_dir=room))

    assert Path(result["path"]).parent == room


def test_download_never_overwrites_a_film_that_is_already_there(monkeypatch, tmp_path):
    # CLAUDE.md rule 5: footage is never overwritten, and a stamped name still collides inside one second.
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage([], clips=1)
    monkeypatch.setattr(scenes.time, "strftime", lambda fmt: "20260916_120000")
    (tmp_path / "scene-1_20260916_120000.mp4").write_bytes(b"older film")

    with pytest.raises(FileExistsError):
        asyncio.run(scenes.download(_SceneSession(page, media), PROJECT, "scene-1", out_dir=tmp_path))
    assert (tmp_path / "scene-1_20260916_120000.mp4").read_bytes() == b"older film"


def test_rename_refuses_when_the_page_shows_more_than_one_editable_title(monkeypatch):
    # Measured 2026-09-16: a scene page holds exactly one flow-editable-text, in the header, and the composer's
    # prompt box is not one. Typing into `.first` of a wider match would be a guess beside a Start generation button.
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media, listing_after=[{"scene_id": "scene-1", "title": "new name"}])
    page = _ScenePage([], title_boxes=2)

    with pytest.raises(LookupError, match="2 editable titles"):
        asyncio.run(scenes.rename(_SceneSession(page, media), PROJECT, "scene-1", "new name"))
    assert page.typed is None


def test_two_downloads_of_one_scene_do_not_collide(monkeypatch, tmp_path):
    # Downloading a scene again after adding another clip is the normal way to work, so the film carries a stamp.
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Model sailboat on wooden desk"], clips=2)
    stamps = iter(["20260916_120000", "20260916_120500"])
    monkeypatch.setattr(scenes.time, "strftime", lambda fmt: next(stamps))
    session = _SceneSession(page, media)

    first = asyncio.run(scenes.download(session, PROJECT, "scene-1", out_dir=tmp_path))
    second = asyncio.run(scenes.download(session, PROJECT, "scene-1", out_dir=tmp_path))

    assert first["path"] != second["path"]
    assert Path(first["path"]).exists() and Path(second["path"]).exists()


def test_download_saves_the_film_under_the_scene_id(monkeypatch, tmp_path):
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Model sailboat on wooden desk"], clips=2)

    result = asyncio.run(scenes.download(_SceneSession(page, media), PROJECT, "scene-1", out_dir=tmp_path))

    assert Path(result["path"]).exists() and Path(result["path"]).name.startswith("scene-1")
    assert result["bytes"] == len(b"film") and result["suggested"].endswith(".mp4")


def test_download_refuses_a_scene_with_nothing_on_the_timeline(monkeypatch, tmp_path):
    media = [_media(SCENE, "Model sailboat on wooden desk")]
    _scene_answers(monkeypatch, media)
    page = _ScenePage(["Model sailboat on wooden desk"])

    with pytest.raises(LookupError, match="no clip"):
        asyncio.run(scenes.download(_SceneSession(page, media), PROJECT, "scene-1", out_dir=tmp_path))
    assert page.downloaded is None


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
