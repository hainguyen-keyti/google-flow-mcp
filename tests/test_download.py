import asyncio

import pytest

from video.flow import download


def test_extension_prefers_content_type_then_url():
    assert download.extension_for("video/mp4", "https://x/y") == ".mp4"
    assert download.extension_for("image/jpeg", "https://x/y") == ".jpg"
    assert download.extension_for("image/png", "https://x/y") == ".png"
    assert download.extension_for(None, "https://x/y.webp?sig=1") == ".webp"
    assert download.extension_for(None, "https://x/y") == ".bin"


def test_latest_version_prefers_the_newest_finished_version_of_a_media():
    rows = [
        {"id": "m", "workflow_id": "orig", "created": 10, "url": "https://x/orig"},
        {"id": "m", "workflow_id": "edit-pending", "created": 30, "url": None},
        {"id": "m", "workflow_id": "edit-done", "created": 20, "url": "https://x/edit"},
        {"id": "other", "workflow_id": "o", "created": 40, "url": "https://x/o"},
    ]
    assert download.latest_version(rows, "m")["workflow_id"] == "edit-done"
    with pytest.raises(LookupError):
        download.latest_version(rows, "zzz")
    with pytest.raises(ValueError):
        download.latest_version([{"id": "m", "created": 1, "url": None}], "m")


def test_select_media_finds_the_item_or_fails_loud():
    media = [{"id": "a", "url": "https://x/a"}, {"id": "b", "url": None}]
    assert download.select_media(media, "a")["url"] == "https://x/a"
    with pytest.raises(LookupError):
        download.select_media(media, "zzz")
    with pytest.raises(ValueError):
        download.select_media(media, "b")


class FakeResponse:
    def __init__(self, body: bytes, content_type: str, ok: bool = True):
        self._body = body
        self.headers = {"content-type": content_type}
        self.ok = ok
        self.status = 200 if ok else 403

    async def body(self) -> bytes:
        return self._body


class FakeRequest:
    def __init__(self, response):
        self.response = response
        self.urls = []

    async def get(self, url: str):
        self.urls.append(url)
        return self.response


def test_fetch_to_file_writes_bytes_with_the_right_suffix(tmp_path):
    request = FakeRequest(FakeResponse(b"\x00\x00\x00\x18ftypmp42", "video/mp4"))
    path = asyncio.run(download.fetch_to_file(request, "https://x/clip", tmp_path / "abc"))
    assert path == tmp_path / "abc.mp4"
    assert path.read_bytes().startswith(b"\x00\x00\x00\x18ftyp")
    assert request.urls == ["https://x/clip"]


def test_fetch_to_file_refuses_a_failed_response(tmp_path):
    request = FakeRequest(FakeResponse(b"", "text/html", ok=False))
    with pytest.raises(RuntimeError):
        asyncio.run(download.fetch_to_file(request, "https://x/clip", tmp_path / "abc"))
    assert list(tmp_path.iterdir()) == []


def test_fetch_to_file_never_overwrites_an_existing_file(tmp_path):
    (tmp_path / "abc.mp4").write_bytes(b"old")
    request = FakeRequest(FakeResponse(b"new", "video/mp4"))
    with pytest.raises(FileExistsError):
        asyncio.run(download.fetch_to_file(request, "https://x/clip", tmp_path / "abc"))
    assert (tmp_path / "abc.mp4").read_bytes() == b"old"


# Plan AD, measured 2026-09-29: the listing moved its media to contribution.fife.usercontent.google.com, which answers
# 400 to every suffix; the page itself loads the same /asb/ token from flow.google.com, where every suffix still works.
MOVED = "https://contribution.fife.usercontent.google.com/asb/ANqvLOtoken123"


def test_a_moved_listing_url_is_fetched_from_flow_with_the_same_path():
    assert download.asset_urls("video", MOVED) == [
        "https://flow.google.com/asb/ANqvLOtoken123=m22",
        "https://flow.google.com/asb/ANqvLOtoken123=m18",
    ]
    assert download.asset_urls("image", MOVED) == ["https://flow.google.com/asb/ANqvLOtoken123=s0"]


def test_an_old_lh3_url_is_left_as_it_was():
    assert download.asset_urls("image", "https://lh3.googleusercontent.com/abc") == [
        "https://lh3.googleusercontent.com/abc=s0"
    ]


def test_an_upsampled_rendition_is_never_taken_for_the_latest_version():
    """Measured 2026-10-01: a 1080p Download adds a `<workflow>_upsampled` record (type CAI, model
    veo_3_1_upsampler_1080p, with a url); taken as the newest version, the editor history had no entry for it."""
    rows = [
        {"id": "m", "workflow_id": "w", "created": 10, "url": "https://x/w"},
        {
            "id": "m",
            "workflow_id": "w_upsampled",
            "created": 20,
            "url": "https://x/w_up",
            "model": "veo_3_1_upsampler_1080p",
        },
    ]
    assert download.latest_version(rows, "m")["workflow_id"] == "w"
    edited = rows + [
        {"id": "m", "workflow_id": "w2", "created": 30, "url": "https://x/w2"},
        {
            "id": "m",
            "workflow_id": "w2_upsampled",
            "created": 40,
            "url": "https://x/w2_up",
            "model": "veo_3_1_upsampler_1080p",
        },
    ]
    assert download.latest_version(edited, "m")["workflow_id"] == "w2"
