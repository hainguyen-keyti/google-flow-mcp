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


class _UploadWorld:
    """The project's listing as Flow would answer it: `before` until the file has been chosen, then `after` from
    the read numbered `late` on (0 is the first read after the choice). A listing read out of order answers what
    the real one would, so a driver that reads "before" after the choice finds the upload already in it."""

    def __init__(self, monkeypatch, tmp_path, *, before, after, frames, name="shot.png", late=0):
        self.before, self.after, self.frames, self.late = before, after, frames, late
        self.chosen = False
        self.reads_before = self.reads_after = 0
        self.settle = None
        monkeypatch.setattr(uploads.reader, "project", self.project)
        monkeypatch.setattr(uploads, "capture", self.capture)
        monkeypatch.setattr(uploads, "LISTING_STEP_S", 0, raising=False)
        self.local = tmp_path / name
        self.local.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.session = _FakeSession(_FakePage(uploads_on_disk=4, all_media_tiles=4, sidebar_ready_at_ms=0))

    async def project(self, session, project_id, settle=10.0, *, versions=False):
        if not self.chosen:
            self.reads_before += 1
            return {"meta": {"id": project_id}, "media": list(self.before)}
        self.reads_after += 1
        listed = self.after if self.reads_after > self.late else self.before
        return {"meta": {"id": project_id}, "media": list(listed)}

    async def capture(self, session, action, *, settle):
        self.chosen, self.settle = True, settle
        return self.frames

    def upload(self):
        return asyncio.run(uploads.upload(self.session, "p", self.local))


def test_an_upload_flow_answered_is_told_by_its_reply(monkeypatch, tmp_path):
    world = _UploadWorld(monkeypatch, tmp_path, before=[], after=[], frames={"maseQ": [MASEQ]})

    out = world.upload()

    assert out["media_id"] == MASEQ[0][2] and "found_by" not in out
    assert (world.reads_before, world.reads_after) == (1, 0), "the reply needs no second look at the listing"
    # Flow answered 7.8 and 9.1 s after the file was chosen (2026-10-03): the wait has to cover that twice over.
    assert world.settle >= 2 * 9.1


def test_an_upload_whose_reply_was_not_seen_is_found_in_the_listing(monkeypatch, tmp_path):
    twin = _image("m-old", "shot.png")
    world = _UploadWorld(
        monkeypatch, tmp_path, before=[twin], after=[twin, _image("m-new", "shot.png")], frames={}
    )

    out = world.upload()

    assert (out["media_id"], out["workflow_id"], out["size_bytes"]) == ("m-new", "w-m-new", 5)
    assert out["found_by"] == "listing" and out["rpcids"] == []
    assert (world.reads_before, world.reads_after) == (1, 1)


def test_an_upload_the_listing_shows_late_is_waited_for(monkeypatch, tmp_path):
    # A saved frame reached the listing about 40 s after its click (2026-09-18), and the 2026-10-02 uploads landed
    # with no reply at all: one read right after the wait would call an upload on its way "not uploaded".
    world = _UploadWorld(
        monkeypatch, tmp_path, before=[], after=[_image("m-new", "shot.png")], frames={}, late=2
    )

    out = world.upload()

    assert out["media_id"] == "m-new" and out["found_by"] == "listing"
    assert world.reads_after == 3


def test_an_upload_named_in_capitals_is_judged_as_the_image_it_is(monkeypatch, tmp_path):
    world = _UploadWorld(
        monkeypatch, tmp_path, before=[], after=[_image("m-new", "SHOT.PNG")], frames={}, name="SHOT.PNG"
    )

    assert world.upload()["media_id"] == "m-new"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ([], []),
        ([_image("m-old", "shot.png")], [_image("m-old", "shot.png")]),
        ([], [{**_image("m-clip", "shot.png"), "kind": "video"}]),
    ],
    ids=["nothing new", "only the twin that was there before", "a new video"],
)
def test_an_upload_neither_answered_nor_listed_says_nothing_was_uploaded(
    monkeypatch, tmp_path, before, after
):
    world = _UploadWorld(monkeypatch, tmp_path, before=before, after=after, frames={"as29s": [[]]})

    with pytest.raises(RuntimeError, match="nothing was uploaded") as said:
        world.upload()

    assert "uploading again is safe" in str(said.value) and "as29s" in str(said.value)
    # Said only after the listing was read as many times as the driver waits, never after one look.
    assert world.reads_after == uploads.LISTING_READS > 1


@pytest.mark.parametrize("title", ["another.png", "Shot.png", "shot", "shot (1).png", "my shot.png"])
def test_a_new_image_of_another_name_is_neither_the_upload_nor_proof_of_none(monkeypatch, tmp_path, title):
    # The rule "an upload is listed under its file name" was measured on five PNG uploads. A new image under any
    # other title may be this upload renamed, so the caller is not told a second try is safe.
    world = _UploadWorld(monkeypatch, tmp_path, before=[], after=[_image("m-else", title)], frames={})

    with pytest.raises(RuntimeError, match="flow_media") as said:
        world.upload()

    assert title in str(said.value) and "m-else" in str(said.value)
    assert "safe" not in str(said.value) and world.reads_after == uploads.LISTING_READS


def test_two_new_images_of_the_name_are_not_chosen_between(monkeypatch, tmp_path):
    after = [_image("m-a", "shot.png"), _image("m-b", "shot.png")]
    world = _UploadWorld(monkeypatch, tmp_path, before=[], after=after, frames={})

    with pytest.raises(RuntimeError, match="2 new images") as said:
        world.upload()

    assert "m-a" in str(said.value) and "m-b" in str(said.value) and "flow_media" in str(said.value)
    assert "safe" not in str(said.value)


@pytest.mark.parametrize("name", ["take.mp4", "loop.gif"])
def test_an_unanswered_upload_that_is_no_image_is_not_judged_by_a_rule_measured_on_images(
    monkeypatch, tmp_path, name
):
    # How anything but an image is titled in the listing was never measured, so "no new image of that name" proves
    # nothing about it: the caller is sent to the listing, and is not told that uploading again is safe.
    world = _UploadWorld(monkeypatch, tmp_path, before=[], after=[], frames={}, name=name)

    with pytest.raises(RuntimeError, match="flow_media") as said:
        world.upload()

    assert "safe" not in str(said.value) and "never measured" in str(said.value)
    assert "video" not in str(said.value) or name.endswith(".mp4")
