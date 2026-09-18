"""Characters on the migrated host (measured 2026-09-12): the New-character page generates a portrait
(rpc ogiZ0b), creates the entity (C4BZMd) and lands on /project/<id>/character/<entity>, where the
editor exposes name, personality, voice, Create body, Done and Delete."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from gflow_cli.api.transports.batchexecute import image_records
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import parsers
from video.flow.reader import capture, one
from video.session import FlowSession

_ENTITY_RE = re.compile(r"/character/([A-Za-z0-9-]+)")
NEW_PAGE = "flow-character-page"
EDIT_PAGE = "flow-character-edit-page"
CONFIRM_DIALOG = "flow-confirmation-dialog"


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
        # gflow calls it media_id; in this repo it is the workflow id, which flow_download rejects.
        "workflow_id": record.media_id,
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


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


async def _upload_portrait(session: FlowSession, image: Path) -> dict[str, list[Any]]:
    """The New character page's one Upload button (measured 2026-09-16): the chosen file becomes the portrait, and
    Flow creates the entity (C4BZMd, maseQ) and moves the page to /character/<entity>."""
    page = session.page
    upload = page.get_by_role("button", name=re.compile("Upload", re.IGNORECASE))
    count = await upload.count()
    if count != 1:
        raise LookupError(f"{count} Upload buttons on the New character page; not guessing")

    async def pick() -> None:
        async with page.expect_file_chooser(timeout=15_000) as chooser:
            await upload.first.click(timeout=8_000)
        picked = await chooser.value
        await picked.set_files(str(image.resolve()))

    return await capture(session, pick, settle=25.0)


async def create(
    session: FlowSession,
    project_id: str,
    prompt: str | None = None,
    *,
    image: Path | None = None,
    name: str | None = None,
    personality: str | None = None,
    wait: float = 90.0,
) -> dict[str, Any]:
    """A character from a face prompt (portrait by Nano Banana 2) or from a local face image (the upload is the
    portrait); exactly one of the two."""
    if bool(prompt and prompt.strip()) == (image is not None):
        raise ValueError("give exactly one of a prompt and an image")
    if image is not None:
        image = Path(image)
        if not image.is_file():
            raise FileNotFoundError(f"no image at {image}")
        if image.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError(f"the image must be a png, jpg, jpeg or webp file, got {image.name}")
    page = session.page
    await session.goto(f"{session.project_url(project_id)}/character", ready=NEW_PAGE)
    await page.wait_for_timeout(2_000)
    if image is None:
        await _type_prompt(session, prompt)
        start = page.get_by_role("button", name=re.compile("Start generation", re.IGNORECASE)).first
        frames = await capture(session, lambda: start.click(timeout=8_000), settle=wait)
        portrait = portrait_from_frames(frames)
    else:
        frames = await _upload_portrait(session, image)
        uploaded = frames.get("maseQ")
        portrait = (
            {"workflow_id": parsers.upload_record(uploaded[0])["workflow_id"], "source": "upload"}
            if uploaded
            else None
        )
    try:
        entity_id = entity_id_from_url(page.url)
    except ValueError:
        if image is None:
            raise
        raise RuntimeError(
            f"uploading {image.name} created no character (rpcids {sorted(frames)}); Flow has refused photos without "
            "a message before, for example people wearing lace (measured 2026-09-13)"
        ) from None
    result: dict[str, Any] = {
        "entity_id": entity_id,
        "project_id": project_id,
        "portrait": portrait,
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
    """Trash the character and confirm. Measured 2026-09-13 00:39: the trash icon opens a
    `flow-confirmation-dialog` ("This character will be permanently deleted.") with Cancel and Delete.
    Matching overlay panes generically instead picked up the trash button's own tooltip pane."""
    page = session.page
    await _open_editor(session, project_id, entity_id)
    frames: dict[str, list[Any]] = {}
    dialog_seen = False
    for attempt in range(2):
        trash = page.get_by_role("button", name=re.compile("^Delete$", re.IGNORECASE)).first
        await trash.click(timeout=8_000)
        dialog = page.locator(CONFIRM_DIALOG).last
        try:
            await dialog.wait_for(state="visible", timeout=15_000)
        except PlaywrightTimeoutError:
            if attempt == 0:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(2_000)
            continue
        dialog_seen = True
        confirm = dialog.get_by_role("button", name=re.compile("delete|remove|confirm", re.IGNORECASE)).last
        frames = await capture(session, lambda c=confirm: c.click(timeout=8_000), settle=5.0)
        break
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


VOICE_OPENER = re.compile("select a voice|voice_selection", re.IGNORECASE)
VOICE_COMMIT = re.compile("add to character", re.IGNORECASE)
VOICE_REMOVE = re.compile("^remove$", re.IGNORECASE)
VOICE_ROWS_JS = (
    "() => [...document.querySelectorAll('.cdk-overlay-pane [role=option], .cdk-overlay-pane button')]"
    "  .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ')).filter(t => t.startsWith('voice_selection'))"
)
VOICE_SCROLL_JS = (
    "() => { const p = document.querySelector('.cdk-overlay-pane');"
    "  const box = p && [...p.querySelectorAll('*')].find(e => e.scrollHeight > e.clientHeight + 20);"
    "  if (!box) return null; const was = box.scrollTop; box.scrollTop = was + box.clientHeight;"
    "  return was === Math.round(box.scrollTop) ? null : [was, Math.round(box.scrollTop)]; }"
)
VOICE_SWEEP_STEPS = 14


def _voice_of(row: str) -> dict[str, str]:
    """A row reads 'voice_selection <Name> <description>', the icon ligature running into the label."""
    body = row.removeprefix("voice_selection").strip()
    name, _, description = body.partition(" ")
    return {"name": name, "description": description.strip()}


async def _open_voice_selector(session: FlowSession, project_id: str, entity_id: str) -> Any:
    page = session.page
    await session.goto(f"{session.project_url(project_id)}/character/{entity_id}", ready=EDIT_PAGE)
    await page.wait_for_timeout(2_500)
    opener = page.get_by_role("button", name=VOICE_OPENER).first
    if not await opener.count():
        raise LookupError(
            f"character {entity_id} shows no voice control; voices live on the character's own page"
        )
    await opener.click(timeout=10_000)
    await page.wait_for_timeout(2_000)
    return page


async def list_voices(session: FlowSession, project_id: str, entity_id: str) -> list[dict[str, str]]:
    """Every preset the character's voice selector offers, in its own order. Free.

    Measured 2026-09-18: 30 presets, each 'voice_selection <Name> <description>', and the list RENDERS A WINDOW
    at a time, so it is swept rather than read once.
    """
    page = await _open_voice_selector(session, project_id, entity_id)
    seen: list[str] = []
    for _ in range(VOICE_SWEEP_STEPS):
        for row in await page.evaluate(VOICE_ROWS_JS):
            if row not in seen:
                seen.append(row)
        if not await page.evaluate(VOICE_SCROLL_JS):
            break
        await page.wait_for_timeout(700)
    return [_voice_of(row) for row in seen]


async def set_voice(session: FlowSession, project_id: str, entity_id: str, voice: str) -> dict[str, Any]:
    """Give a character one of Flow's preset voices, so a generation of that character can speak. Free.

    Measured 2026-09-18: picking a row and clicking 'Add to character' fires rpc rzMKMb, a field-masked update of
    the character, and the page then reads 'voice_selection <voice lowercased>' beside a play button and Remove.
    The list is virtual, so the wanted row is scrolled to rather than assumed rendered.
    """
    page = await _open_voice_selector(session, project_id, entity_id)
    wanted = re.compile(rf"voice_selection\s+{re.escape(voice)}\b", re.IGNORECASE)
    row = page.locator(".cdk-overlay-pane [role=option], .cdk-overlay-pane button").filter(has_text=wanted)
    for _ in range(VOICE_SWEEP_STEPS):
        if await row.count():
            break
        if not await page.evaluate(VOICE_SCROLL_JS):
            break
        await page.wait_for_timeout(700)
    if not await row.count():
        names = [v["name"] for v in await list_voices(session, project_id, entity_id)]
        raise LookupError(f"no voice named {voice!r} in the selector; it offers {names}")
    await row.first.click(timeout=8_000)
    await page.wait_for_timeout(1_200)
    commit = page.get_by_role("button", name=VOICE_COMMIT).first
    if not await commit.count():
        raise LookupError(
            "the selector no longer offers 'Add to character'; typing a performance turns it into a voice maker"
        )
    await commit.click(timeout=10_000)
    await page.wait_for_timeout(3_000)
    return {"entity_id": entity_id, "voice": voice, "rpcids": ["rzMKMb"]}


async def clear_voice(session: FlowSession, project_id: str, entity_id: str) -> dict[str, Any]:
    """Take the voice off a character. Free. The page grows a Remove button once a voice is attached."""
    page = session.page
    await session.goto(f"{session.project_url(project_id)}/character/{entity_id}", ready=EDIT_PAGE)
    await page.wait_for_timeout(2_500)
    remove = page.get_by_role("button", name=VOICE_REMOVE).first
    if not await remove.count():
        raise LookupError(f"character {entity_id} has no voice to remove")
    await remove.click(timeout=10_000)
    await page.wait_for_timeout(2_500)
    return {"entity_id": entity_id, "voice": None, "rpcids": ["rzMKMb"]}
