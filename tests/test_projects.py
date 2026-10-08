import asyncio

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import projects
from video.session import MIGRATED_ROOT, FlowSession


class _Keyboard:
    def __init__(self):
        self.typed = []

    async def press(self, key):
        pass

    async def type(self, text):
        self.typed.append(text)


class _TitleBox:
    @property
    def first(self):
        return self

    async def wait_for(self, state=None, timeout=None):
        return None

    async def click(self, timeout=None):
        pass


class _Page:
    def __init__(self):
        self.keyboard = _Keyboard()

    def locator(self, selector):
        return _TitleBox()


class _Session:
    """No browser: records where rename sends the page, and what it types into the title box."""

    def __init__(self):
        self.page = _Page()
        self.urls = []

    async def goto(self, url, *, ready=None, timeout_ms=60_000):
        self.urls.append(url)

    project_url = staticmethod(FlowSession.project_url)


def _flow_answers(monkeypatch, *frames):
    queue = list(frames)

    async def fake_capture(session, action, *, settle):
        await action()
        return queue.pop(0)

    monkeypatch.setattr(projects, "capture", fake_capture)


def _grid_listing(project_id, title):
    # UpteDb is [[card, ...]] with card = [project_id, [title, _, [created], thumbnail, cover]] (tests/fixtures/rpc).
    cards = [[project_id, [title, None, [1789416314, 0], None, None]]]
    return {"UpteDb": [[cards]]}


RENAMED = {"o8DA4": [[]]}


def test_rename_confirms_the_new_title_on_the_grid_before_reporting_it(monkeypatch):
    # o8DA4 firing only says a request went out. The grid is what flow_projects reads, and measured 2026-09-15 it
    # already shows the new title on the first read after the rename.
    _flow_answers(monkeypatch, RENAMED, _grid_listing("P", "new title"))
    session = _Session()

    assert asyncio.run(projects.rename(session, "P", "new title")) == "new title"
    assert session.urls == [FlowSession.project_url("P"), MIGRATED_ROOT]
    assert session.page.keyboard.typed == ["new title"]


def test_rename_fails_when_the_grid_still_lists_another_title(monkeypatch):
    _flow_answers(monkeypatch, RENAMED, _grid_listing("P", "old title"))

    with pytest.raises(RuntimeError, match="old title"):
        asyncio.run(projects.rename(_Session(), "P", "new title"))


class _NewProjectButton:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    async def click(self, timeout=None):
        self.page.url = FlowSession.project_url("NEW")


class _GridPage(_Page):
    def __init__(self):
        super().__init__()
        self.url = MIGRATED_ROOT

    def get_by_role(self, role, name=None):
        return _NewProjectButton(self)


def test_create_with_a_title_that_does_not_land_still_names_the_project_it_made(monkeypatch):
    # Review 2026-09-15: once rename checked the grid, a failed title made project_create raise after the project
    # already existed, without its id, so an agent's retry would create a second project.
    _flow_answers(monkeypatch, {"jHPbke": [[]]}, RENAMED, _grid_listing("NEW", "Untitled project"))
    session = _Session()
    session.page = _GridPage()

    with pytest.raises(RuntimeError, match="project NEW was created"):
        asyncio.run(projects.create(session, "new title"))


class _WaitingBox(_TitleBox):
    """A locator that records which selector was waited for, and when the title box was clicked relative to it."""

    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    async def wait_for(self, state=None, timeout=None):
        self.page.waited.append(self.selector)

    async def click(self, timeout=None):
        self.page.clicked_box_after = list(self.page.waited)


class _ReadyPage(_GridPage):
    def __init__(self):
        super().__init__()
        self.waited = []
        self.clicked_box_after = None

    def locator(self, selector):
        return _WaitingBox(self, selector)


def test_create_waits_for_the_project_page_before_naming_it(monkeypatch):
    # Measured 2026-10-08 (live verification, D13): project_create with a title made the project and then timed out
    # on the title box, since the rename started on the grid page while the project page was still loading;
    # project_rename on its own, which waits for the page, worked.
    _flow_answers(monkeypatch, {"jHPbke": [[]]}, RENAMED, _grid_listing("NEW", "new title"))
    session = _Session()
    session.page = _ReadyPage()

    result = asyncio.run(projects.create(session, "new title"))

    assert result == {"id": "NEW", "rpcids": ["jHPbke"], "title": "new title"}, result
    assert projects.PROJECT_READY in (session.page.clicked_box_after or []), (
        "the title box was clicked before the project page was ready"
    )


class _StuckTitleBox(_TitleBox):
    async def click(self, timeout=None):
        raise PlaywrightTimeoutError("Locator.click: Timeout 8000ms exceeded")


def test_create_names_the_project_it_made_whatever_stopped_the_rename(monkeypatch):
    # Scoped re-review 2026-09-15: a Playwright timeout is neither LookupError nor RuntimeError and escaped bare.
    _flow_answers(monkeypatch, {"jHPbke": [[]]})
    session = _Session()
    session.page = _GridPage()
    session.page.locator = lambda selector: _StuckTitleBox()

    with pytest.raises(RuntimeError, match="project NEW was created"):
        asyncio.run(projects.create(session, "new title"))


def test_rename_fails_when_the_project_is_not_on_the_grid(monkeypatch):
    _flow_answers(monkeypatch, RENAMED, _grid_listing("OTHER", "new title"))

    # Match the message, not the type alone: a KeyError from reading the missing project is a LookupError too.
    with pytest.raises(LookupError, match="project P is not on the grid"):
        asyncio.run(projects.rename(_Session(), "P", "new title"))


def test_project_id_from_url_accepts_uuid_and_backfill_ids():
    assert projects.project_id_from_url(
        "https://flow.google.com/project/5bcbfdd7-0c50-4bd3-b902-57d0c173f683"
    ) == ("5bcbfdd7-0c50-4bd3-b902-57d0c173f683")
    assert projects.project_id_from_url(
        "https://flow.google.com/project/8822142b-ca75-46b7-aac8-03d2831_backfill/tools"
    ) == ("8822142b-ca75-46b7-aac8-03d2831_backfill")


def test_project_id_from_url_rejects_non_project_pages():
    with pytest.raises(ValueError):
        projects.project_id_from_url("https://flow.google.com/")
    with pytest.raises(ValueError):
        projects.project_id_from_url("https://flow.google.com/about")


def test_delete_refuses_ids_not_created_by_this_pipeline_unless_explicit():
    assert projects.may_delete("abc", created_ids={"abc"}, explicit=False) is True
    assert projects.may_delete("xyz", created_ids={"abc"}, explicit=False) is False
    assert projects.may_delete("xyz", created_ids=set(), explicit=True) is True


class _ReachedTheCard(Exception):
    """Raised by the fake card's hover: proof that delete got past the "is it on the grid" check."""


class _LateCard:
    def __init__(self, *, shows_up: bool):
        self.shows_up = shows_up
        self.waited = False

    @property
    def first(self):
        return self

    async def wait_for(self, state=None, timeout=None):
        if not self.shows_up:
            raise PlaywrightTimeoutError("no such card")
        self.waited = True

    async def count(self):
        return 1 if self.waited else 0

    async def hover(self, timeout=None):
        raise _ReachedTheCard


class _CardGridPage:
    def __init__(self, card):
        self.card = card

    def locator(self, selector, has=None):
        return self.card if selector == "flow-project-card" else object()

    async def wait_for_timeout(self, ms):
        return None


class _CardGridSession:
    def __init__(self, card):
        self.page = _CardGridPage(card)

    async def goto(self, url, *, ready=None, timeout_ms=60_000):
        return None


def test_delete_waits_for_the_card_the_listing_draws_after_the_new_project_button():
    """Review of plan Q (2026-09-28): the home page can now be ready on the New project button before the listing
    has drawn the project cards, and delete counted the card at once, so on an account WITH projects it could say
    "not on the grid" about a project that exists. An agent reads that as "already gone"."""
    with pytest.raises(_ReachedTheCard):
        asyncio.run(projects.delete(_CardGridSession(_LateCard(shows_up=True)), "p-late"))


def test_delete_still_says_so_when_the_card_never_shows_up():
    with pytest.raises(LookupError, match="not on the grid"):
        asyncio.run(projects.delete(_CardGridSession(_LateCard(shows_up=False)), "p-gone"))
