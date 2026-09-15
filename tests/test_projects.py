import asyncio

import pytest

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
