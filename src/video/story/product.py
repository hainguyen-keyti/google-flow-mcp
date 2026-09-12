"""The garment being sold, as a real object rather than a sentence.

Plan 2 proved that describing the dress in words lets Veo invent a different one in every shot. The
owner's own photo of the set is uploaded into the project once and then attached as an ingredient, so the
piece shown and the piece worn are the same object.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ASSETS = Path(__file__).resolve().parents[3] / "assets" / "product"
IMAGE = ASSETS / "pink_floral_set.png"
DESCRIPTION = (
    "the blush pink ditsy floral camisole and matching ruffled shorts set from the reference image: "
    "cream fabric printed with small dusty pink flowers, scalloped lace trim across the neckline, thin "
    "adjustable spaghetti straps, a small satin bow at the centre front, and ruffled frill hems on both "
    "the camisole and the shorts"
)


def check_image() -> Path:
    if not IMAGE.is_file():
        raise FileNotFoundError(f"product image missing: {IMAGE}")
    return IMAGE


def find(media: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The uploaded product image inside a project listing, matched by filename."""
    for item in media:
        if item.get("kind") == "image" and (item.get("title") or "") == IMAGE.name:
            return item
    return None


async def ensure(session, project_id: str) -> dict[str, Any]:
    """Upload the product image once; reuse it on every later run. Free."""
    from video.flow import reader
    from video.flow import uploads as uploads_mod

    check_image()
    info = await reader.project(session, project_id)
    existing = find(info["media"])
    if existing is not None:
        return {"media_id": existing["id"], "title": existing["title"], "uploaded": False}
    record = await uploads_mod.upload(session, project_id, IMAGE)
    return {"media_id": record["media_id"], "title": IMAGE.name, "uploaded": True, "rpcids": record["rpcids"]}
