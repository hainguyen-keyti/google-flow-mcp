import asyncio

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from test_upload_parser import MASEQ

from video.flow import uploads


class _FakeLocator:
    def __init__(self, page, selector, has_text):
        self._page = page
        self._selector = selector
        self._has_text = has_text

    @property
    def first(self):
        return self

    async def count(self):
        return self._page.matches(self._selector, self._has_text)

    async def wait_for(self, state=None, timeout=None):
        # Playwright keeps polling until the element turns up or the timeout runs out, so a wait is the
        # one operation that is allowed to let a late sidebar finish rendering.
        self._page.elapsed_ms = max(self._page.elapsed_ms, self._page.sidebar_ready_at_ms)
        if await self.count() == 0:
            raise PlaywrightTimeoutError(f"Locator.wait_for: Timeout {timeout}ms exceeded")

    def filter(self, has_text=None):
        return self

    async def click(self, timeout=None):
        if self._selector == "mat-list-item" and await self.count() == 0:
            raise AssertionError("clicked a nav item that is not on the page")
        if self._has_text == "Uploads":
            self._page.showing_uploads = True


class _FakePage:
    """Enough of a Playwright page to drive list_uploads with no Chrome and no Flow.

    Two measured facts from 2026-09-14 are built in, because both of them broke list_uploads.
    One: the project sidebar renders a beat after PROJECT_READY and WHEN is not fixed, ~2000ms on a
    project holding uploads and ~3000ms on an upload-free one, so reading it after a flat sleep reads
    whatever happens to be there. Two: the selector union
    `flow-tile-container, flow-image-tile, flow-video-tile` counts every upload TWICE because it matches
    the wrapper and the tile nested inside it (container 4, image-tile 4, union 8 on a 4-upload project).
    """

    def __init__(self, *, uploads_on_disk, all_media_tiles=9, sidebar_ready_at_ms=2_500):
        self.uploads_on_disk = uploads_on_disk
        self.all_media_tiles = all_media_tiles
        self.sidebar_ready_at_ms = sidebar_ready_at_ms
        self.elapsed_ms = 0
        self.showing_uploads = False

    def matches(self, selector, has_text):
        if self.elapsed_ms < self.sidebar_ready_at_ms or selector != "mat-list-item":
            return 0
        if has_text is None:
            return 7 if self.uploads_on_disk else 4
        return 1 if has_text == "Uploads" and self.uploads_on_disk else 0

    def locator(self, selector, has_text=None, **kwargs):
        return _FakeLocator(self, selector, has_text)

    def get_by_role(self, role, name=None):
        return _FakeLocator(self, f"role={role}", None)

    async def wait_for_timeout(self, ms):
        self.elapsed_ms += ms

    async def evaluate(self, script):
        on_screen = self.uploads_on_disk if self.showing_uploads else self.all_media_tiles
        if "flow-image-tile" in script or "flow-video-tile" in script:
            return on_screen * 2
        if "flow-tile-container" in script:
            return on_screen
        raise AssertionError(f"unexpected evaluate: {script!r}")


class _FakeSession:
    def __init__(self, page):
        self.page = page

    def project_url(self, project_id):
        return f"https://flow.google.com/project/{project_id}"

    async def goto(self, url, ready=None):
        return None


def _no_capture(session, action, *, settle):
    raise AssertionError("list_uploads reached batchexecute; the Uploads view stopped firing WuwhI")


def test_list_uploads_waits_for_a_late_sidebar_before_calling_a_project_empty(monkeypatch):
    # Measured 2026-09-14 on 604b2de7, which holds 4 uploads: list_uploads slept a flat 2000ms and the
    # sidebar was not up yet, so it read nav.count() == 0 and answered `count: 0`. A confident wrong
    # answer is worse than the null it used to give, because 0 reads like a real result.
    monkeypatch.setattr(uploads, "capture", _no_capture)
    page = _FakePage(uploads_on_disk=4, sidebar_ready_at_ms=2_500)

    out = asyncio.run(uploads.list_uploads(_FakeSession(page), "p"))

    assert out["count"] == 4


def test_list_uploads_counts_the_uploads_view_without_any_batchexecute(monkeypatch):
    # Measured 2026-09-14: clicking Uploads fires NO batchexecute at all (rpcids []), twice in a row, and
    # the URL does not change. It is a client-side filter now, so the count has to come from the DOM.
    # all_media_tiles stays different from uploads_on_disk on purpose: a count taken without opening the
    # Uploads view would come back 9 instead of 4.
    monkeypatch.setattr(uploads, "capture", _no_capture)
    page = _FakePage(uploads_on_disk=4, all_media_tiles=9, sidebar_ready_at_ms=0)

    out = asyncio.run(uploads.list_uploads(_FakeSession(page), "p"))

    assert out["count"] == 4


def test_list_uploads_of_a_project_with_no_uploads_answers_exactly_zero(monkeypatch):
    # A project that has never had an upload grows no "Uploads" nav item at all, so the click used to sit
    # there until it timed out. Measured 2026-09-13 against a fresh project: flow_uploads failed with
    # `TimeoutError: Locator.click: Timeout 8000ms exceeded` while the project was perfectly healthy.
    # The reply carries one field and nothing else: rpcids and head died with WuwhI, and tiles would only
    # ever repeat count. Contract agreed 2026-09-14, see DECISIONS.
    monkeypatch.setattr(uploads, "capture", _no_capture)
    page = _FakePage(uploads_on_disk=0, sidebar_ready_at_ms=0)

    out = asyncio.run(uploads.list_uploads(_FakeSession(page), "p"))

    assert out == {"count": 0}


def test_upload_reports_the_tiles_it_can_see_not_double(monkeypatch, tmp_path):
    # Same doubling selector, second caller. Measured 2026-09-14 in the Uploads view of 604b2de7:
    # flow-tile-container 4, flow-image-tile 4, flow-video-tile 0, and the union 8, against a screenshot
    # showing 4 uploads. upload() reports that figure after every upload, so it doubled there too.
    async def fake_capture(session, action, *, settle):
        return {"maseQ": [MASEQ]}

    async def fake_project(session, project_id, settle=10.0, *, versions=False):
        return {"meta": {"id": project_id}, "media": []}

    monkeypatch.setattr(uploads, "capture", fake_capture)
    monkeypatch.setattr(uploads.reader, "project", fake_project, raising=False)
    page = _FakePage(uploads_on_disk=4, all_media_tiles=4, sidebar_ready_at_ms=0)
    local = tmp_path / "shot.png"
    local.write_bytes(b"\x89PNG\r\n\x1a\n")

    out = asyncio.run(uploads.upload(_FakeSession(page), "p", local))

    assert out["tiles"] == 4


# Plan AO: an upload whose reply was not seen is settled by the listing, never by the silence alone. On 2026-10-02
# `upload` raised "rpc maseQ not observed; saw []" twice while the image had reached the project each time, and the
# second call, made on that error, left two images of one name.


def _image(media_id, title, workflow="w"):
    return {
        "id": media_id,
        "kind": "image",
        "title": title,
        "workflow_id": f"{workflow}-{media_id}",
        "size_bytes": 5,
    }


def _upload_world(monkeypatch, tmp_path, *, before, after, frames):
    """The listing before and after the file is chosen, and the batchexecute frames heard in between."""
    listings = [before, after]
    read = []

    async def fake_project(session, project_id, settle=10.0, *, versions=False):
        read.append(project_id)
        return {"meta": {"id": project_id}, "media": list(listings[min(len(read), 2) - 1])}

    async def fake_capture(session, action, *, settle):
        return frames

    monkeypatch.setattr(uploads.reader, "project", fake_project)
    monkeypatch.setattr(uploads, "capture", fake_capture)
    local = tmp_path / "shot.png"
    local.write_bytes(b"\x89PNG\r\n\x1a\n")
    page = _FakePage(uploads_on_disk=4, all_media_tiles=4, sidebar_ready_at_ms=0)
    return _FakeSession(page), local, read


def test_an_upload_flow_answered_is_told_by_its_reply(monkeypatch, tmp_path):
    session, local, read = _upload_world(
        monkeypatch, tmp_path, before=[], after=[], frames={"maseQ": [MASEQ]}
    )

    out = asyncio.run(uploads.upload(session, "p", local))

    assert out["media_id"] == MASEQ[0][2] and "found_by" not in out
    assert len(read) == 1, "the listing is read once before; the reply needs no second look"


def test_an_upload_whose_reply_was_not_seen_is_found_in_the_listing(monkeypatch, tmp_path):
    twin = _image("m-old", "shot.png")
    session, local, read = _upload_world(
        monkeypatch, tmp_path, before=[twin], after=[twin, _image("m-new", "shot.png")], frames={}
    )

    out = asyncio.run(uploads.upload(session, "p", local))

    assert (out["media_id"], out["workflow_id"], out["size_bytes"]) == ("m-new", "w-m-new", 5)
    assert out["found_by"] == "listing" and out["rpcids"] == [] and len(read) == 2


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ([], []),
        ([_image("m-old", "shot.png")], [_image("m-old", "shot.png")]),
        ([], [_image("m-else", "another.png")]),
        ([], [{**_image("m-clip", "shot.png"), "kind": "video"}]),
    ],
    ids=["nothing new", "only the twin that was there before", "a new image of another name", "a new video"],
)
def test_an_upload_neither_answered_nor_listed_says_nothing_was_uploaded(
    monkeypatch, tmp_path, before, after
):
    session, local, _ = _upload_world(
        monkeypatch, tmp_path, before=before, after=after, frames={"as29s": [[]]}
    )

    with pytest.raises(RuntimeError, match="nothing was uploaded") as said:
        asyncio.run(uploads.upload(session, "p", local))

    assert "uploading again is safe" in str(said.value) and "as29s" in str(said.value)


def test_two_new_images_of_the_name_are_not_chosen_between(monkeypatch, tmp_path):
    after = [_image("m-a", "shot.png"), _image("m-b", "shot.png")]
    session, local, _ = _upload_world(monkeypatch, tmp_path, before=[], after=after, frames={})

    with pytest.raises(RuntimeError, match="2 new images") as said:
        asyncio.run(uploads.upload(session, "p", local))

    assert "m-a" in str(said.value) and "m-b" in str(said.value) and "flow_media" in str(said.value)
    assert "safe" not in str(said.value)


def test_an_unanswered_upload_of_a_video_is_not_judged_by_a_rule_measured_on_images(monkeypatch, tmp_path):
    # How an uploaded video is titled in the listing was never measured, so "no new image of that name" proves
    # nothing about it: the caller is sent to the listing, and is not told that uploading again is safe.
    session, _, _ = _upload_world(monkeypatch, tmp_path, before=[], after=[], frames={})
    clip = tmp_path / "take.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    with pytest.raises(RuntimeError, match="flow_media") as said:
        asyncio.run(uploads.upload(session, "p", clip))

    assert "safe" not in str(said.value) and "never measured" in str(said.value)
