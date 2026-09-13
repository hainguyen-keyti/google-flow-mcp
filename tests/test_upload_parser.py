import asyncio

import pytest

from video.flow import parsers, uploads

PROJECT = "c5d1301b-07fe-4485-86d9-49a47449a494"
MASEQ = [
    [
        "7a2f2075-d94b-4b77-ae6b-ca31b9168729",
        PROJECT,
        "3212a1b5-9919-45b5-8945-ae8d8b2d7467",
        "CAE",
        None,
        [
            [1789225542, 27616000],
            None,
            None,
            None,
            None,
            None,
            [None, None, None, None, 1],
            None,
            None,
            1,
            None,
            None,
            None,
            271467,
        ],
        [None, [None, None, None, None, 0], [13]],
    ]
]


def test_upload_record_reads_media_workflow_project_and_size():
    assert parsers.upload_record(MASEQ) == {
        "media_id": "7a2f2075-d94b-4b77-ae6b-ca31b9168729",
        "project_id": PROJECT,
        "workflow_id": "3212a1b5-9919-45b5-8945-ae8d8b2d7467",
        "size_bytes": 271467,
    }


def test_upload_record_rejects_a_payload_without_a_record():
    with pytest.raises(TypeError):
        parsers.upload_record([840, 1, 2, 2, None, 840])


class _NavMissing:
    """The Uploads nav item a project only grows once something has been uploaded into it."""

    first = property(lambda self: self)

    async def count(self):
        return 0

    async def click(self, timeout=None):
        raise AssertionError("clicked a nav item that is not on the page")


class _EmptyProjectPage:
    def locator(self, selector, has_text=None):
        return _NavMissing()

    async def wait_for_timeout(self, ms):
        return None

    async def evaluate(self, script):
        return 0


class _EmptyProjectSession:
    page = _EmptyProjectPage()

    def project_url(self, project_id):
        return f"https://flow.google.com/project/{project_id}"

    async def goto(self, url, ready=None):
        return None


def test_list_uploads_of_a_project_with_no_uploads_answers_empty(monkeypatch):
    # A project that has never had an upload has no "Uploads" nav item at all, so the click used to sit
    # there until it timed out. Measured 2026-09-13 against a fresh project: flow_uploads failed with
    # `TimeoutError: Locator.click: Timeout 8000ms exceeded` while the project was perfectly healthy.
    async def never_captures(session, action, *, settle):
        raise AssertionError("captured a click that should never have happened")

    monkeypatch.setattr(uploads, "capture", never_captures)

    out = asyncio.run(uploads.list_uploads(_EmptyProjectSession(), "p"))

    assert out["count"] == 0
    assert out["tiles"] == 0
    assert out["rpcids"] == []
    assert out["head"] is None
