"""Characters on the migrated host (measured 2026-09-12): the New-character page generates a portrait
(rpc ogiZ0b), creates the entity (C4BZMd) and lands on /project/<id>/character/<entity>, where the
editor exposes name, personality, voice, Create body, Done and Delete."""

from __future__ import annotations

import re
from typing import Any

from gflow_cli.api.transports.batchexecute import image_records
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import parsers
from video.flow.reader import capture, one
from video.session import FlowSession

_ENTITY_RE = re.compile(r"/character/([A-Za-z0-9-]+)")
NEW_PAGE = "flow-character-page"
EDIT_PAGE = "flow-character-edit-page"


def entity_id_from_url(url: str) -> str:
    match = _ENTITY_RE.search(url)
    if not match:
        raise ValueError(f"not a character editor url: {url}")
    return match.group(1)


def portrait_from_frames(frames: dict[str, list[Any]]) -> dict[str, Any] | None:
    payloads = frames.get("ogiZ0b")
    if not payloads:
        return None
    records = image_records("ogiZ0b", payloads[0])
    record = records[0]
    return {
        "media_id": record.media_id,
        "url": record.image_url,
        "width": record.dimensions[0],
        "height": record.dimensions[1],
        "prompt": record.prompt,
    }


async def _type_prompt(session: FlowSession, prompt: str) -> None:
    box = session.page.locator("flow-character-prompt-box [contenteditable='true']").first
    await box.click(timeout=8_000)
    await session.page.keyboard.type(prompt)
    await session.page.wait_for_timeout(500)


async def create(
    session: FlowSession,
    project_id: str,
    prompt: str,
    *,
    name: str | None = None,
    personality: str | None = None,
    wait: float = 90.0,
) -> dict[str, Any]:
    page = session.page
    await session.goto(f"{session.project_url(project_id)}/character", ready=NEW_PAGE)
    await page.wait_for_timeout(2_000)
    await _type_prompt(session, prompt)
    start = page.get_by_role("button", name=re.compile("Start generation", re.IGNORECASE)).first
    frames = await capture(session, lambda: start.click(timeout=8_000), settle=wait)
    entity_id = entity_id_from_url(page.url)
    result: dict[str, Any] = {
        "entity_id": entity_id,
        "project_id": project_id,
        "portrait": portrait_from_frames(frames),
        "rpcids": sorted(frames),
        "name": None,
        "personality": None,
    }
    if name:
        result["name"] = await rename(session, entity_id, name, navigate=False)
    if personality:
        result["personality"] = await set_personality(session, entity_id, personality, navigate=False)
    done = page.get_by_role("button", name=re.compile("^Done$", re.IGNORECASE)).first
    done_frames = await capture(session, lambda: done.click(timeout=8_000), settle=5.0)
    result["done_rpcids"] = sorted(done_frames)
    return result


async def _open_editor(session: FlowSession, project_id: str, entity_id: str) -> None:
    await session.goto(f"{session.project_url(project_id)}/character/{entity_id}", ready=EDIT_PAGE)
    await session.page.wait_for_timeout(2_000)


async def rename(
    session: FlowSession, entity_id: str, name: str, *, navigate: bool = True, project_id: str | None = None
) -> str:
    page = session.page
    if navigate:
        if not project_id:
            raise ValueError("project_id is required to navigate to the editor")
        await _open_editor(session, project_id, entity_id)
    edit = page.get_by_role("button", name=re.compile("Edit name", re.IGNORECASE)).first
    await edit.click(timeout=8_000)
    box = page.locator("input.name-input, flow-editable-text input").first
    await box.click(timeout=8_000)
    await page.keyboard.press("Meta+A")
    await page.keyboard.type(name)
    frames = await capture(session, lambda: page.keyboard.press("Enter"), settle=4.0)
    if not frames:
        raise RuntimeError("rename: no rpc fired after Enter")
    return name


async def set_personality(
    session: FlowSession,
    entity_id: str,
    personality: str,
    *,
    navigate: bool = True,
    project_id: str | None = None,
) -> str:
    page = session.page
    if navigate:
        if not project_id:
            raise ValueError("project_id is required to navigate to the editor")
        await _open_editor(session, project_id, entity_id)
    box = page.locator("textarea.personality-textarea").first
    await box.click(timeout=8_000)
    await page.keyboard.press("Meta+A")
    await page.keyboard.type(personality)
    frames = await capture(session, lambda: page.keyboard.press("Tab"), settle=4.0)
    if not frames:
        raise RuntimeError("set_personality: no rpc fired after leaving the field")
    return personality


async def delete(session: FlowSession, project_id: str, entity_id: str) -> dict[str, Any]:
    page = session.page
    await _open_editor(session, project_id, entity_id)
    trash = page.get_by_role("button", name=re.compile("^Delete$", re.IGNORECASE)).first
    await trash.click(timeout=8_000)
    dialog = page.locator("[role=dialog], mat-dialog-container").first
    frames: dict[str, list[Any]] = {}
    dialog_seen = False
    try:
        await dialog.wait_for(state="visible", timeout=8_000)
        dialog_seen = True
        confirm = dialog.get_by_role("button", name=re.compile("delete|remove|confirm", re.IGNORECASE)).first
        frames = await capture(session, lambda: confirm.click(timeout=8_000), settle=5.0)
    except PlaywrightTimeoutError:
        await page.wait_for_timeout(3_000)
    remaining = await list_characters(session, project_id)
    if any(c["entity_id"] == entity_id for c in remaining):
        raise RuntimeError(
            f"delete: character {entity_id} is still listed; dialog_seen={dialog_seen} rpcids {sorted(frames)}"
        )
    return {"entity_id": entity_id, "rpcids": sorted(frames), "remaining": len(remaining)}


async def list_characters(session: FlowSession, project_id: str) -> list[dict[str, Any]]:
    frames = await capture(
        session, lambda: session.goto(session.project_url(project_id), ready="flow-project-page"), settle=8.0
    )
    return parsers.characters_from_listing(one(frames, "Zzl0ze"))
