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
VOICE_ROW = ".cdk-overlay-pane [role=option]"
VOICE_TITLE = ".asset-title"
VOICE_ROWS_JS = (
    "() => [...document.querySelectorAll('.cdk-overlay-pane [role=option]')].map(e => ["
    "  ((e.querySelector('.asset-title') || {}).textContent || '').trim(),"
    "  ((e.querySelector('.asset-description') || {}).textContent || '').trim(),"
    "  !!e.querySelector('.custom-voice-badge-icon')]).filter(row => row[0])"
)
VOICE_SCROLL_JS = (
    "() => { const p = document.querySelector('.cdk-overlay-pane');"
    "  const box = p && [...p.querySelectorAll('*')].find(e => e.scrollHeight > e.clientHeight + 20);"
    "  if (!box) return null; const was = box.scrollTop; box.scrollTop = was + box.clientHeight;"
    "  return was === Math.round(box.scrollTop) ? null : [was, Math.round(box.scrollTop)]; }"
)
VOICE_SWEEP_STEPS = 14
VOICE_SAVE = re.compile("save new voice", re.IGNORECASE)
VOICE_BOXES = ".cdk-overlay-pane textarea"
VOICE_NAME_INPUT = ".cdk-overlay-pane input:not([aria-label='Search assets'])"
VOICE_SAMPLE_MAX = 120
VOICE_READY_WAIT_S = 90.0
VOICE_READY_STEP_S = 2.0
VOICE_PREVIEW_RPC = "no0P6"
# The field-masked character update Flow sends when a voice is attached or removed (measured 2026-09-18 on both
# paths). Hearing it is the only proof the click landed: a click Flow ignores changes nothing on the page.
VOICE_UPDATE_RPC = "rzMKMb"
# Measured 2026-09-18: the control reads "autorenew Preview" but its aria-label, which is what the role query
# sees, is "Play preview", so an anchored pattern finds nothing.
VOICE_PREVIEW = re.compile("preview", re.IGNORECASE)


def _watch_rpc(page: Any, rpcid: str) -> Any:
    """Count Flow's answers to one rpc, so a wait can end on the answer rather than on a guess about timing."""

    class Watch:
        count = 0

        def note(self, response: Any) -> None:
            if rpcid in response.request.url:
                self.count += 1

        def stop(self) -> None:
            page.remove_listener("response", self.note)

    watch = Watch()
    page.on("response", watch.note)
    return watch


def _voice_of(row: list[Any]) -> dict[str, Any]:
    """A row is a button[role=option] carrying span.asset-title, span.asset-description and, for a voice saved
    on this account, mat-icon.custom-voice-badge-icon. Its textContent runs all of them together with no
    whitespace, so the parts are read from their own elements (measured 2026-09-18)."""
    name, description, custom = row
    return {"name": name.strip(), "description": description.strip(), "custom": bool(custom)}


async def _open_voice_selector(session: FlowSession, project_id: str, entity_id: str) -> Any:
    page = session.page
    await session.goto(f"{session.project_url(project_id)}/character/{entity_id}", ready=EDIT_PAGE)
    await page.wait_for_timeout(2_500)
    opener = page.get_by_role("button", name=VOICE_OPENER).first
    if not await opener.count():
        # With a voice attached the control's accessible name is only the voice: the words voice_selection are a
        # Material icon ligature Flow hides from the tree, and filtering on text content sees it (2026-09-18).
        opener = page.locator("button").filter(has_text=VOICE_OPENER).first
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
    seen: dict[str, dict[str, Any]] = {}
    for _ in range(VOICE_SWEEP_STEPS):
        for row in await page.evaluate(VOICE_ROWS_JS):
            voice = _voice_of(row)
            seen.setdefault(voice["name"], voice)
        if not await page.evaluate(VOICE_SCROLL_JS):
            break
        await page.wait_for_timeout(700)
    return list(seen.values())


async def _pick_voice(session: FlowSession, page: Any, project_id: str, entity_id: str, voice: str) -> Any:
    """Scroll the virtual list to the row whose OWN title is that voice and select it."""
    wanted = re.compile(rf"^\s*{re.escape(voice)}\s*$", re.IGNORECASE)
    row = page.locator(VOICE_ROW).filter(has=page.locator(VOICE_TITLE, has_text=wanted))
    for _ in range(VOICE_SWEEP_STEPS):
        if await row.count():
            break
        if not await page.evaluate(VOICE_SCROLL_JS):
            break
        await page.wait_for_timeout(700)
    if not await row.count():
        names = [v["name"] for v in await list_voices(session, project_id, entity_id)]
        raise LookupError(f"no voice named {voice!r} in the selector; it offers {names}")
    # The dialog opens with the voice it last edited already selected. Clicking that row again deselects it and
    # empties the dialog, the footer button included, so the click only happens when it is needed (2026-09-18).
    if await row.first.get_attribute("aria-selected") != "true":
        await row.first.click(timeout=8_000)
        await page.wait_for_timeout(1_200)
    return row


async def set_voice(session: FlowSession, project_id: str, entity_id: str, voice: str) -> dict[str, Any]:
    """Give a character one of Flow's preset voices, so a generation of that character can speak. Free.

    Measured 2026-09-18: picking a row and clicking 'Add to character' fires rpc rzMKMb, a field-masked update of
    the character, and the page then reads 'voice_selection <voice lowercased>' beside a play button and Remove.
    The list is virtual, so the wanted row is scrolled to rather than assumed rendered.
    """
    page = await _open_voice_selector(session, project_id, entity_id)
    await _pick_voice(session, page, project_id, entity_id, voice)
    commit = page.get_by_role("button", name=VOICE_COMMIT).first
    if not await commit.count():
        raise LookupError(
            "the selector no longer offers 'Add to character'; typing a performance turns it into a voice maker"
        )
    frames = await capture(session, lambda: commit.click(timeout=10_000), settle=6.0)
    if VOICE_UPDATE_RPC not in frames:
        raise RuntimeError(
            f"the 'Add to character' click did not update the character ({VOICE_UPDATE_RPC} never came back, "
            f"heard {sorted(frames)}); read flow_voices and the character page before trying again"
        )
    return {"entity_id": entity_id, "voice": voice, "rpcids": sorted(frames)}


async def make_voice(
    session: FlowSession,
    project_id: str,
    entity_id: str,
    preset: str,
    performance: str,
    *,
    name: str,
    sample: str = "Xin chào",
    attach: bool = True,
    wait_s: float | None = None,
    step_s: float | None = None,
) -> dict[str, Any]:
    """Make a voice of your own: a preset plus a written performance, saved under a name. Free.

    Measured 2026-09-18 on project 118aece2: writing into the performance box turns the picker into a voice
    MAKER, but typing sends NOTHING: clicking Preview is what makes Flow synthesise the voice (rpc no0P6,
    'gemini_v4s_tts_flow', about 24 s), and 'Save new voice' stays a no-op until that answer lands even though
    it looks enabled the whole time. A run that trusted the flag clicked at once and left no voice behind.
    The save fires lt8g5 (the sample becomes visible media) and mYWVGd (its display name), after which the
    voice is listed above the presets and attaches like one.
    """
    if len(sample) > VOICE_SAMPLE_MAX:
        raise ValueError(f"the sample dialogue box holds {VOICE_SAMPLE_MAX} characters, got {len(sample)}")
    if not name.strip() or not performance.strip():
        raise ValueError("a voice of your own needs both a name and a performance description")
    wait_s = VOICE_READY_WAIT_S if wait_s is None else wait_s
    step_s = VOICE_READY_STEP_S if step_s is None else step_s
    page = await _open_voice_selector(session, project_id, entity_id)
    await _pick_voice(session, page, project_id, entity_id, preset)
    boxes = page.locator(VOICE_BOXES)
    if await boxes.count() < 2:
        raise LookupError("the voice dialog shows no sample dialogue and performance boxes")
    watch = _watch_rpc(page, VOICE_PREVIEW_RPC)
    try:
        await boxes.nth(0).fill(sample)
        await boxes.nth(1).fill(performance)
        await page.wait_for_timeout(600)
        await page.locator(VOICE_NAME_INPUT).first.fill(name)
        await page.wait_for_timeout(600)
        # Typing sends nothing at all: the footer only swaps the Preview icon for autorenew. Clicking Preview is
        # what makes Flow synthesise the voice, and the save stays a no-op until that answer lands (2026-09-18).
        preview = page.get_by_role("button", name=VOICE_PREVIEW).first
        if not await preview.count():
            raise LookupError("the voice dialog offers no Preview, so the synthesis cannot be started")
        await preview.click(timeout=10_000)
        commit = page.get_by_role("button", name=VOICE_SAVE).first
        if not await commit.count():
            raise LookupError(
                "the dialog offers no 'Save new voice'; a performance is what turns it into a maker"
            )
        attempts = max(1, round(wait_s / step_s))
        for _ in range(attempts):
            if watch.count:
                break
            await page.wait_for_timeout(int(step_s * 1_000))
        else:
            raise TimeoutError(
                f"Flow never answered the preview synthesis ({VOICE_PREVIEW_RPC}) within {wait_s:.0f} s; "
                "clicking save before that answer does nothing at all"
            )
    finally:
        watch.stop()
    polls = 0
    for _ in range(attempts):
        polls += 1
        if await commit.get_attribute("disabled") is None:
            break
        await page.wait_for_timeout(int(step_s * 1_000))
    else:
        raise TimeoutError(f"'Save new voice' was still disabled {wait_s:.0f} s after the preview answer")
    frames = await capture(session, lambda: commit.click(timeout=10_000), settle=8.0)
    if not frames:
        # rpcids [] is what the failed live run returned: the click landed on a button that did nothing, and
        # reporting it as a save would send an agent hunting a voice that is not there.
        raise RuntimeError("the 'Save new voice' click fired no rpc at all, so no voice was saved")
    result: dict[str, Any] = {
        "entity_id": entity_id,
        "voice": name,
        "preset": preset,
        "performance": performance,
        "sample": sample,
        "polls": polls,
        "attached": False,
        "rpcids": sorted(frames),
    }
    if attach:
        await set_voice(session, project_id, entity_id, name)
        result["attached"] = True
    return result


async def clear_voice(session: FlowSession, project_id: str, entity_id: str) -> dict[str, Any]:
    """Take the voice off a character. Free. The page grows a Remove button once a voice is attached."""
    page = session.page
    await session.goto(f"{session.project_url(project_id)}/character/{entity_id}", ready=EDIT_PAGE)
    await page.wait_for_timeout(2_500)
    remove = page.get_by_role("button", name=VOICE_REMOVE).first
    if not await remove.count():
        raise LookupError(f"character {entity_id} has no voice to remove")
    frames = await capture(session, lambda: remove.click(timeout=10_000), settle=6.0)
    if VOICE_UPDATE_RPC not in frames:
        raise RuntimeError(
            f"the Remove click did not update the character ({VOICE_UPDATE_RPC} never came back, heard "
            f"{sorted(frames)}); read the character page before trying again"
        )
    return {"entity_id": entity_id, "voice": None, "rpcids": sorted(frames)}
