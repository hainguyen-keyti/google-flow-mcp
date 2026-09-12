import asyncio

import pytest

from video.flow import download


def test_extension_prefers_content_type_then_url():
    assert download.extension_for("video/mp4", "https://x/y") == ".mp4"
    assert download.extension_for("image/jpeg", "https://x/y") == ".jpg"
    assert download.extension_for("image/png", "https://x/y") == ".png"
    assert download.extension_for(None, "https://x/y.webp?sig=1") == ".webp"
    assert download.extension_for(None, "https://x/y") == ".bin"


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
