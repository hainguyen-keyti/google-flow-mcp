import asyncio

import pytest

from video.flow import download


def test_image_asset_is_the_full_size_rendition():
    assert download.asset_urls("image", "https://lh3.googleusercontent.com/asb/X") == [
        "https://lh3.googleusercontent.com/asb/X=s0"
    ]


def test_video_assets_are_tried_from_best_to_worst_rendition():
    urls = download.asset_urls("video", "https://lh3.googleusercontent.com/asb/X")
    assert urls[0].endswith("=m22")
    assert urls[-1].endswith("=m18")
    assert all(u.startswith("https://lh3.googleusercontent.com/asb/X=") for u in urls)


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        download.asset_urls(None, "https://x/y")


class FakeResponse:
    def __init__(self, body: bytes, content_type: str, status: int = 200):
        self._body = body
        self.headers = {"content-type": content_type}
        self.status = status
        self.ok = status < 300

    async def body(self) -> bytes:
        return self._body


class FakeRequest:
    def __init__(self, table):
        self.table = table
        self.urls = []

    async def get(self, url: str, **kwargs):
        self.urls.append(url)
        return self.table.get(url, FakeResponse(b"<html>", "text/html", 500))


MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8


def test_fetch_asset_takes_the_first_url_that_is_really_an_mp4(tmp_path):
    base = "https://lh3.googleusercontent.com/asb/X"
    request = FakeRequest({base + "=m18": FakeResponse(MP4, "video/mp4")})
    path = asyncio.run(download.fetch_asset(request, "video", base, tmp_path / "vid"))
    assert path == tmp_path / "vid.mp4"
    assert path.read_bytes() == MP4
    assert request.urls[-1] == base + "=m18"
    assert len(request.urls) >= 2


def test_fetch_asset_rejects_a_jpeg_posing_as_video(tmp_path):
    base = "https://lh3.googleusercontent.com/asb/X"
    request = FakeRequest(
        {u: FakeResponse(b"\xff\xd8\xff\xe0poster", "image/jpeg") for u in download.asset_urls("video", base)}
    )
    with pytest.raises(RuntimeError):
        asyncio.run(download.fetch_asset(request, "video", base, tmp_path / "vid"))
    assert list(tmp_path.iterdir()) == []


def test_fetch_asset_image_uses_s0_and_keeps_jpeg_suffix(tmp_path):
    base = "https://lh3.googleusercontent.com/asb/Y"
    request = FakeRequest({base + "=s0": FakeResponse(b"\xff\xd8\xff\xe0full", "image/jpeg")})
    path = asyncio.run(download.fetch_asset(request, "image", base, tmp_path / "img"))
    assert path == tmp_path / "img.jpg"
    assert request.urls == [base + "=s0"]
