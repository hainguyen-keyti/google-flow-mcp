"""Download a project's media through the page's own request context, so cookies ride along.

Listing URLs point at posters and thumbnails. The lh3 suffixes below select the real asset
(measured 2026-09-12: =s0 is the full image, =m22 then =m18 are mp4 renditions, =m37 answers 404).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from video.flow import reader
from video.session import FlowSession

_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_VIDEO_RENDITIONS = ("=m22", "=m18")
_IMAGE_RENDITION = "=s0"
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"RIFF")


def extension_for(content_type: str | None, url: str) -> str:
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind in _EXTENSIONS:
        return _EXTENSIONS[kind]
    tail = url.split("?")[0].rsplit("/", 1)[-1]
    if "." in tail:
        ext = "." + tail.rsplit(".", 1)[-1].lower()
        if 1 < len(ext) <= 5:
            return ext
    return ".bin"


def select_media(media: list[dict[str, Any]], media_id: str) -> dict[str, Any]:
    for item in media:
        if item.get("id") == media_id:
            if not item.get("url"):
                raise ValueError(f"media {media_id} has no download url in the listing")
            return item
    raise LookupError(f"media {media_id} is not in the project listing")


MOVED_HOSTS = ("contribution.fife.usercontent.google.com",)
FLOW_HOST = "flow.google.com"


def asset_base(url: str) -> str:
    """Measured 2026-09-29: the listing's media moved to contribution.fife.usercontent.google.com, which answers 400 to
    every suffix, while the page loads the same /asb/ token from flow.google.com, where the suffixes below still work."""
    parts = urlsplit(url)
    if parts.netloc in MOVED_HOSTS:
        return urlunsplit((parts.scheme or "https", FLOW_HOST, parts.path, parts.query, parts.fragment))
    return url


def asset_urls(kind: str | None, url: str) -> list[str]:
    url = asset_base(url)
    if kind == "image":
        return [url + _IMAGE_RENDITION]
    if kind == "video":
        return [url + rendition for rendition in _VIDEO_RENDITIONS]
    raise ValueError(f"unknown media kind: {kind!r}")


def _is_asset(kind: str, body: bytes) -> bool:
    if kind == "video":
        return body[4:8] == b"ftyp"
    return body.startswith(_IMAGE_MAGIC)


def _write_new(stem: Path, suffix: str, body: bytes) -> Path:
    path = stem.with_name(stem.name + suffix)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


async def fetch_to_file(request: Any, url: str, stem: Path) -> Path:
    response = await request.get(url)
    if not response.ok:
        raise RuntimeError(f"download failed: HTTP {response.status}")
    suffix = extension_for(response.headers.get("content-type"), url)
    path = stem.with_name(stem.name + suffix)
    if path.exists():
        raise FileExistsError(path)
    return _write_new(stem, suffix, await response.body())


async def fetch_asset(request: Any, kind: str, base_url: str, stem: Path) -> Path:
    seen: list[str] = []
    for url in asset_urls(kind, base_url):
        response = await request.get(url, max_redirects=5)
        if not response.ok:
            seen.append(f"{url[-5:]}: HTTP {response.status}")
            continue
        body = await response.body()
        if not _is_asset(kind, body):
            seen.append(f"{url[-5:]}: {response.headers.get('content-type')} {len(body)} B")
            continue
        return _write_new(stem, extension_for(response.headers.get("content-type"), base_url), body)
    raise RuntimeError(f"no rendition of {kind} returned a real asset: {'; '.join(seen)}")


def is_upsampled(row: dict[str, Any]) -> bool:
    """A 1080p Download adds `<workflow>_upsampled` (model veo_3_1_upsampler_1080p, measured 2026-10-01): a rendition
    of a version, which the editor history does not list as a version of its own."""
    return str(row.get("workflow_id") or "").endswith("_upsampled") or "upsampler" in str(
        row.get("model") or ""
    )


def latest_version(rows: list[dict[str, Any]], media_id: str) -> dict[str, Any]:
    """The newest finished version of a media id among generation records (an edit adds a version)."""
    versions = sorted(
        (r for r in rows if r.get("id") == media_id and not is_upsampled(r)),
        key=lambda r: r.get("created") or 0,
    )
    if not versions:
        raise LookupError(f"media {media_id} is not in the project listing")
    for item in reversed(versions):
        if item.get("url"):
            return item
    raise ValueError(f"media {media_id} has no download url in the listing")


async def download(session: FlowSession, project_id: str, media_id: str, out_dir: Path = Path("out")) -> Path:
    """Any generation record downloads, listed on the grid or not (clips inside scenes, extensions);
    an edited media downloads its newest finished version."""
    item = latest_version(await reader.records(session, project_id), media_id)
    return await fetch_asset(session.page.request, item["kind"], item["url"], out_dir / media_id)
