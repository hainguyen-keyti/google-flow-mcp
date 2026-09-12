"""The character entity that carries the owner's face.

Measured 2026-09-13: the New character page has an "Upload" button, so the portrait is the owner's own
reference image rather than a face Flow invents (rpc C4BZMd + maseQ, 0 credits).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from video.flow import characters
from video.flow.reader import capture
from video.story import bible

NAME = "Mai"


def _personality(data: dict[str, Any] | None = None) -> str:
    data = data if data is not None else bible.load()
    aesthetic = data["character"]["wardrobe_style"]["core_aesthetic"]
    return (
        "Calm, a little shy, natural; never a permanent exaggerated smile. "
        f"Her style is a {aesthetic}. "
        "She films short try-on videos in her own bedroom to sell the pieces she wears."
    )


PERSONALITY = _personality()


async def ensure(session, project_id: str, *, image: Path | None = None, name: str = NAME) -> dict[str, Any]:
    """Find the character by name, or create it from the reference portrait. Free either way."""
    listed = await characters.list_characters(session, project_id)
    for row in listed:
        if (row.get("name") or "").strip().lower() == name.lower():
            return {"entity_id": row["entity_id"], "name": name, "created": False}

    portrait = Path(image) if image else bible.reference_images()["portrait"]
    if not portrait.is_file():
        raise FileNotFoundError(portrait)
    page = session.page
    await session.goto(f"{session.project_url(project_id)}/character", ready=characters.NEW_PAGE)
    await page.wait_for_timeout(3_000)
    upload = page.get_by_role("button", name=re.compile("Upload", re.IGNORECASE)).first
    if await upload.count() == 0:
        raise RuntimeError("the New character page has no Upload button on this account")

    async def pick() -> None:
        async with page.expect_file_chooser(timeout=15_000) as chooser:
            await upload.click(timeout=8_000)
        picked = await chooser.value
        await picked.set_files(str(portrait.resolve()))

    frames = await capture(session, pick, settle=25.0)
    entity_id = characters.entity_id_from_url(page.url)
    await characters.rename(session, entity_id, name, navigate=False)
    await characters.set_personality(session, entity_id, PERSONALITY, navigate=False)
    done = page.get_by_role("button", name=re.compile("^Done$", re.IGNORECASE)).first
    if await done.count():
        await done.click(timeout=8_000)
        await page.wait_for_timeout(2_000)
    return {
        "entity_id": entity_id,
        "name": name,
        "created": True,
        "portrait": str(portrait),
        "rpcids": sorted(frames),
    }
