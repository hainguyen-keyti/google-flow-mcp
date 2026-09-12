import asyncio

from video.story import composer


class _Session:
    page = type("P", (), {"request": object()})()


def test_fetch_720_insists_on_the_hd_rendition_before_falling_back(monkeypatch, tmp_path):
    # =m22 can 404 for a minute after the record says done; taking =m18 straight away left one shot at
    # 360x640 inside a 720x1280 cut (measured 2026-09-13).
    urls = []

    async def flaky(request, url, stem):
        urls.append(url)
        if len(urls) < 3:
            raise RuntimeError("download failed: HTTP 404")
        return tmp_path / "hd.mp4"

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(composer.download_mod, "fetch_to_file", flaky)
    monkeypatch.setattr(composer.asyncio, "sleep", no_sleep)
    record = {"kind": "video", "url": "https://lh3/x"}
    out = asyncio.run(composer.fetch_720(_Session(), record, tmp_path / "shot"))
    assert out == tmp_path / "hd.mp4"
    assert urls == ["https://lh3/x=m22"] * 3, "it must keep asking for 720p, not drop to =m18"


def test_fetch_720_falls_back_to_any_rendition_once_patience_runs_out(monkeypatch, tmp_path):
    async def always_404(request, url, stem):
        raise RuntimeError("download failed: HTTP 404")

    async def fallback(request, record, stem, attempts=6):
        return tmp_path / "sd.mp4"

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(composer.download_mod, "fetch_to_file", always_404)
    monkeypatch.setattr(composer.clips, "_fetch_with_retry", fallback)
    monkeypatch.setattr(composer.asyncio, "sleep", no_sleep)
    record = {"kind": "video", "url": "https://lh3/x"}
    out = asyncio.run(composer.fetch_720(_Session(), record, tmp_path / "shot", attempts=2))
    assert out == tmp_path / "sd.mp4"
