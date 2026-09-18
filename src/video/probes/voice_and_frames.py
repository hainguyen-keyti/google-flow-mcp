"""Plan J T1: what Flow still offers for VOICE and for saving a frame as a project asset. $0, read only.

Three questions this has to answer with measurements, because every design choice downstream depends on them
and the repo has paid before for guessing (CLAUDE.md rule 10):

1. Where does a voice live? gflow 0.78.0 ships a preset list for "Character TTS" (api/character.py:78) and its
   migrated composer notes that attaching a character chip makes "Flow load the character's voice sample"
   (api/transports/migrated_composer.py:428). So a voice is probably a property of a CHARACTER, not a chip of
   its own. Measured here: what the New character page shows, what an existing character's page shows, and
   whether the prompt box's `@` menu lists anything that is not a character or a media.
2. Where does dialogue go, if anywhere: a field of its own, or just words in the prompt.
3. Where "Save frame" and "Save to Project" live, and what they leave behind. The scene timeline's right-click
   menu was measured on 2026-09-17 to hold Copy, Paste, Save to Project, Download, Delete; the clip editor has
   not been read for this.

    uv run python -m video.probes.voice_and_frames --project <id> [--character <entity_id>] [--media <id>]
        --do voices --do character --do mention --do clipmenu --do editor

NOTHING here clicks a control whose label carries a paid word (scenes.PAID_WORDS), and nothing clicks Save to
Project or Save frame in this pass: this run only READS the surfaces and photographs them. Whether saving costs
anything is decided in the plan's own acceptance, after the owner sees these numbers.

MEASURED 2026-09-18 on project 118aece2, five runs, $0:

- A voice belongs to a CHARACTER, and only on its EDIT page: `/project/<id>/character/<entity>` carries a button
  reading `voice_selection Select a voice`, and the page's own CSS names the whole feature (voice-card-name,
  voice-card-description, voice-play-btn, voice-remove-btn). The NEW character page has none of it, so a voice
  is chosen after the character exists.
- The selector holds **30 preset voices**: Achernar ... Zubenelgenubi, every one of gflow's 29
  (api/character.py:78) plus one more, each with a one-line description, a `play_arrow Preview` and one
  `Add to character`.
- **A voice IS customised, by writing, and the first pass of this probe missed it** because it scraped only
  `[role=option]` and `button` while the controls that matter are TEXTAREAS. Read again, and visible in
  `voice_picker.png`: the dialog carries `Sample dialogue` (textarea, maxlength **120**, placeholder "Hi there!
  We're introducing a new feature where I can read this aloud...") and **`Customize performance`** (textarea, NO
  maxlength, placeholder "Describe the voice performance style..."). So "a young cheerful Saigon woman, soft and
  playful" is expressed as a preset PLUS that description, not as a voice built from scratch.
- What is NOT offered: making a voice from your own recording. The `Search assets` input beside the list, with
  its project dropdown, only FILTERS the same 30 presets (typing "a" returned voices whose text holds an "a"),
  so the asset-picker chrome there leads nowhere else.
- The prompt box's `@` menu lists media and characters only (19 rows on this project), no voices, which matches
  gflow's note that the voice rides with the character chip rather than being mentioned on its own.
- `Save frame` EXISTS, in the clip editor, as an ICON-ONLY button: aria-label "Save frame", icon
  add_photo_alternate. Reading button TEXT alone missed it entirely in the first pass.
- `Save to Project` is a right-click item on a scene timeline clip: Copy, Paste, Save to Project, Download,
  Delete (unchanged since 2026-09-17).
MEASURED 2026-09-18, second pass, the clicks themselves, still $0 (balance 119 before and after):

- **Attaching a preset to a character** fires rpc `rzMKMb` with
  `[[<project id>, <entity id>, None, [1, None, [None, [[None, '<voice, lowercased>']]]]], [['entity_info...`,
  a field-masked character update. The character page then reads `voice_selection leda` and grows a
  `play_arrow` and a `Remove` button, which is the clear-voice path.
- **Typing a performance turns the picker into a voice MAKER**: a `Voice name` field appears, prefilled
  `<Preset> custom`, and the footer swaps `Add to character` for `Reset` + `Save new voice`. Selecting the voice
  with text in those boxes fires rpc `no0P6`,
  `[[[<sample>, [[<preset>, <name>]], 'gemini_v4s_tts_flow', <performance>, 2]], [..., <project id>]]`, which is
  the synthesis behind Preview. **Open question**: the `Save new voice` click itself produced NO batchexecute
  traffic in the captured window, the reopened picker still lists exactly the 30 presets, and the character page
  still read `Select a voice`, so where a saved voice lives is not established. The dialog's own project
  dropdown points at another project, which is the first thing to try next.
- **`Save frame`** fires rpc `maseQ` and leaves an IMAGE on the grid titled `Saved frame from <clip title>`
  (measured: `d1d71a58...` at 15:36:21). It does not appear in a listing read seconds later: Flow indexed it
  about 40 s after the click, so a driver must poll rather than read once.
- **`Save to Project`** fires rpc `Sc7aEb` with `[<clip id>, None, None, <project id>]` and puts the scene's clip
  on the grid as a new media (measured: `d0e23b3b...`, video, listed).
- Still unmeasured: what a generation with a voice costs, which needs a paid run the owner has approved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from typing import Any

from video.flow import characters as characters_mod
from video.flow import clips as clips_mod
from video.flow import reader, scenes
from video.probes._common import OUT_DIR
from video.probes.scene_editor import Traffic
from video.session import PROJECT_READY, FlowSession

BOX = "flow-prompt-box [contenteditable='true']"
OVERLAY = ".cdk-overlay-pane"
MENU_ITEM = "[role=menuitem]"
NEW_CHARACTER = "flow-character-creator, flow-new-character, [class*=character-creator]"

_TEXT_JS = """
(selector) => [...document.querySelectorAll(selector)]
  .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' '))
  .filter(Boolean)
  .slice(0, 60)
"""

_ELEMENTS_JS = """() => [...new Set([...document.querySelectorAll('*')]
  .map(e => e.tagName.toLowerCase()).filter(t => t.includes('-')))].slice(0, 200)"""

_AUDIO_JS = """
() => ({
  audio_tags: document.querySelectorAll('audio').length,
  play_buttons: [...document.querySelectorAll('button')]
    .map(b => (b.getAttribute('aria-label') || b.innerText || '').trim())
    .filter(t => /play|preview|listen|nghe/i.test(t)).slice(0, 20),
  // innerText of a leaf also catches <style>, which dumped 6 KB of CSS into the first report.
  voice_words: [...document.querySelectorAll('*')]
    .filter(e => e.childElementCount === 0 && !['STYLE', 'SCRIPT'].includes(e.tagName))
    .map(e => (e.innerText || '').trim())
    .filter(t => t && t.length < 120 && /voice|giọng|speech|dialogue|narrat/i.test(t)).slice(0, 30),
})
"""


async def _shot(page: Any, stamp: str, name: str) -> str:
    path = OUT_DIR / f"voice_frames_{stamp}_{name}.png"
    await page.screenshot(path=str(path))
    return str(path)


def _safe(label: str, what: str) -> str:
    if any(word in label.lower() for word in scenes.PAID_WORDS):
        raise RuntimeError(f"refusing to click {label!r} for {what}: that control spends credits")
    return label


async def act_voices(session: FlowSession, project: str, stamp: str) -> dict[str, Any]:
    """Does this account's Flow name voices anywhere at all, and does gflow's preset list still match?"""
    from gflow_cli.api.character import VOICES

    page = session.page
    await session.goto(session.project_url(project), ready=PROJECT_READY)
    await page.wait_for_timeout(3_000)
    found = await page.evaluate(_AUDIO_JS)
    return {
        "gflow_preset_voices": [v.name for v in VOICES],
        "gflow_preset_count": len(VOICES),
        "project_page": found,
        "shot": await _shot(page, stamp, "project_page"),
    }


async def act_character(session: FlowSession, project: str, entity: str | None, stamp: str) -> dict[str, Any]:
    """The New character page, and an existing character's page: is a voice chosen there?"""
    page = session.page
    result: dict[str, Any] = {}
    listed = await characters_mod.list_characters(session, project)
    result["characters"] = [{"entity_id": c["entity_id"], "name": c["name"]} for c in listed][:10]
    # The real driver's path, not a guess: /character with flow-character-page (characters.py:96). The guessed
    # /character/new never rendered a project page and timed out for 60 s in the first pass.
    await session.goto(f"{session.project_url(project)}/character", ready=characters_mod.NEW_PAGE)
    await page.wait_for_timeout(4_000)
    result["new_character"] = {
        "elements": await page.evaluate(_ELEMENTS_JS),
        "audio": await page.evaluate(_AUDIO_JS),
        "buttons": await page.evaluate(_TEXT_JS, "button"),
        "url": page.url,
        "shot": await _shot(page, stamp, "character_new"),
    }
    target = entity or (listed[0]["entity_id"] if listed else None)
    if target:
        await session.goto(
            f"{session.project_url(project)}/character/{target}", ready=characters_mod.EDIT_PAGE
        )
        await page.wait_for_timeout(4_000)
        result["existing_character"] = {
            "entity_id": target,
            "audio": await page.evaluate(_AUDIO_JS),
            "buttons": await page.evaluate(_TEXT_JS, "button"),
            "url": page.url,
            "shot": await _shot(page, stamp, "character_existing"),
        }
    return result


async def act_mention(session: FlowSession, project: str, stamp: str) -> dict[str, Any]:
    """What the prompt box's `@` menu offers: only characters and media, or voices too."""
    page = session.page
    await session.goto(session.project_url(project), ready=PROJECT_READY)
    await page.wait_for_timeout(3_000)
    box = page.locator(BOX).first
    await box.click(timeout=10_000)
    await page.keyboard.type("@")
    await page.wait_for_timeout(2_500)
    rows = await page.evaluate(_TEXT_JS, f"{OVERLAY} [role=option], {OVERLAY} button")
    shot = await _shot(page, stamp, "mention_menu")
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(500)
    # Leave the box as it was found: a stray character in the composer is how a later run submits the wrong text.
    for _ in range(3):
        await page.keyboard.press("Backspace")
    return {
        "rows": rows,
        "row_count": len(rows),
        "shot": shot,
        "box_after": await page.evaluate("(s) => (document.querySelector(s)?.innerText || '').trim()", BOX),
    }


async def act_voicepicker(
    session: FlowSession, project: str, entity: str | None, stamp: str
) -> dict[str, Any]:
    """Open the character's voice selector and read what Flow offers: preset names, whether a sample plays, and
    whether anything there makes a voice of your own. Clicking 'Select a voice' spends nothing, and the probe
    still refuses any label carrying a paid word."""
    page = session.page
    listed = await characters_mod.list_characters(session, project)
    target = entity or (listed[0]["entity_id"] if listed else None)
    if not target:
        return {"note": "the project holds no character to open"}
    await session.goto(f"{session.project_url(project)}/character/{target}", ready=characters_mod.EDIT_PAGE)
    await page.wait_for_timeout(3_000)
    opener = page.get_by_role("button", name=re.compile("select a voice|voice", re.IGNORECASE)).first
    label = (await opener.inner_text()).strip()
    _safe(label, "opening the voice selector")
    await opener.click(timeout=10_000)
    await page.wait_for_timeout(2_500)
    # The list scrolls: the first read showed 15 of gflow's 29 presets, and whether anything at the BOTTOM makes
    # a voice of your own is exactly the question this probe exists to answer.
    dialog_text: list[str] = []
    for _ in range(12):
        rows = await page.evaluate(
            _TEXT_JS, f"{OVERLAY} [role=option], {OVERLAY} button, {OVERLAY} h1, {OVERLAY} h2"
        )
        dialog_text += [row for row in rows if row not in dialog_text]
        moved = await page.evaluate(
            "(sel) => { const p = document.querySelector(sel);"
            "  const box = p && [...p.querySelectorAll('*')].find(e => e.scrollHeight > e.clientHeight + 20);"
            "  if (!box) return null; const was = box.scrollTop; box.scrollTop = was + box.clientHeight;"
            "  return [was, Math.round(box.scrollTop), box.scrollHeight]; }",
            OVERLAY,
        )
        await page.wait_for_timeout(700)
        if not moved or moved[0] == moved[1]:
            break
    # The first pass read only options and buttons, so it missed the two TEXTAREAS this dialog is really about:
    # "Sample dialogue" and "Customize performance" (seen in the screenshot, not in the scrape). Read every field.
    fields = await page.evaluate(
        "(sel) => [...document.querySelector(sel).querySelectorAll('input, textarea, select, [contenteditable]')]"
        "  .map(e => ({tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '',"
        "             label: (e.getAttribute('aria-label') || e.getAttribute('name') || '').trim(),"
        "             placeholder: (e.getAttribute('placeholder') || '').trim(),"
        "             maxlength: e.getAttribute('maxlength') || '',"
        "             value: (e.value || e.innerText || '').trim().slice(0, 80)}))",
        OVERLAY,
    )
    headings = await page.evaluate(
        "(sel) => [...document.querySelector(sel).querySelectorAll('*')]"
        "  .filter(e => e.childElementCount === 0 && !['STYLE','SCRIPT'].includes(e.tagName))"
        "  .map(e => (e.innerText || '').trim()).filter(t => t && t.length < 80).slice(0, 60)",
        OVERLAY,
    )
    cards = await page.evaluate(
        "() => [...document.querySelectorAll('[class*=voice-card-name], [class*=voice-card-description]')]"
        "  .map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 80)"
    )
    shot = await _shot(page, stamp, "voice_picker")
    # "Search assets" with a project dropdown next to a voice list is the one control that could mean a voice of
    # your own, made from something you uploaded. Type one letter and read what it offers.
    searched: dict[str, Any] = {}
    box = page.locator(f"{OVERLAY} input[aria-label='Search assets']").first
    if await box.count():
        await box.click(timeout=8_000)
        await box.type("a", delay=60)
        await page.wait_for_timeout(2_500)
        searched["rows"] = await page.evaluate(
            "(sel) => [...document.querySelector(sel).querySelectorAll('[role=option], [class*=asset], li')]"
            "  .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ')).filter(Boolean).slice(0, 25)",
            OVERLAY,
        )
        searched["shot"] = await _shot(page, stamp, "voice_search")
        for _ in range(3):
            await page.keyboard.press("Backspace")
    await page.keyboard.press("Escape")
    return {
        "entity_id": target,
        "opened_with": label,
        "dialog_rows": dialog_text,
        "voice_cards": cards,
        "fields": fields,
        "dialog_leaf_text": headings,
        "asset_search": searched,
        "audio_after_open": await page.evaluate(_AUDIO_JS),
        "shot": shot,
    }


async def act_clipmenu(session: FlowSession, project: str, scene_id: str, stamp: str) -> dict[str, Any]:
    """The scene timeline's right-click menu, read again for the Save items and what they say."""
    page = session.page
    await scenes._open_scene(session, project, scene_id)
    await page.wait_for_timeout(2_000)
    clips = page.locator(scenes.CLIPS)
    count = await clips.count()
    if not count:
        return {"clips": 0, "note": "scene holds no clip to right-click"}
    await clips.first.click(button="right", timeout=10_000)
    await page.wait_for_timeout(1_500)
    items = await page.evaluate(_TEXT_JS, MENU_ITEM)
    shot = await _shot(page, stamp, "clip_menu")
    await page.keyboard.press("Escape")
    return {"clips": count, "menu_items": items, "shot": shot}


async def act_editor(session: FlowSession, project: str, media: str, stamp: str) -> dict[str, Any]:
    """The clip editor page for one finished video: does it offer a frame to save."""
    page = session.page
    await session.goto(f"{session.project_url(project)}/edit/{media}", ready=clips_mod.EDITOR)
    await page.wait_for_timeout(4_000)
    buttons = await page.evaluate(_TEXT_JS, "button")
    # Icon-only controls carry no text at all, and a frame grab is exactly the kind of control that would be one.
    labelled = await page.evaluate(
        "() => [...document.querySelectorAll('button, [role=button], mat-icon')]"
        "  .map(e => [(e.getAttribute('aria-label') || '').trim(), (e.getAttribute('title') || '').trim(),"
        "            (e.innerText || '').trim().slice(0, 40)].filter(Boolean).join(' | '))"
        "  .filter(Boolean).slice(0, 120)"
    )
    return {
        "media": media,
        "buttons": buttons,
        "labelled_controls": labelled,
        "frame_like": [
            c for c in labelled if re.search(r"frame|save|khung|lưu|extract|still", c, re.IGNORECASE)
        ],
        "save_like": [b for b in buttons if re.search(r"save|frame|lưu|khung", b, re.IGNORECASE)],
        "elements": await page.evaluate(_ELEMENTS_JS),
        "audio": await page.evaluate(_AUDIO_JS),
        "shot": await _shot(page, stamp, "clip_editor"),
    }


async def _media_ids(session: FlowSession, project: str) -> set[str]:
    listing = await reader.project(session, project, versions=True)
    return {row["id"] for row in listing["media"]} | {row["id"] for row in listing.get("versions") or []}


async def act_setvoice(session: FlowSession, project: str, spec: str, stamp: str) -> dict[str, Any]:
    """Give a character a voice the way the page does it: pick a preset, write the performance, listen-free, and
    commit with 'Add to character'. Records the rpc traffic so the driver is written against what Flow stores.

    spec is `<entity_id>:<VoiceName>[:<performance text>]`. Nothing here carries a paid word.
    """
    entity, _, rest = spec.partition(":")
    voice, _, rest2 = rest.partition(":")
    performance, _, wanted_name = rest2.partition("|")
    performance = performance or "giọng nữ trẻ miền Nam, nhỏ nhẹ, nhí nhảnh, khoảng 20 tuổi"
    page = session.page
    await session.goto(f"{session.project_url(project)}/character/{entity}", ready=characters_mod.EDIT_PAGE)
    await page.wait_for_timeout(2_500)
    opener = page.get_by_role("button", name=re.compile("select a voice|voice", re.IGNORECASE)).first
    _safe((await opener.inner_text()).strip(), "opening the voice selector")
    await opener.click(timeout=10_000)
    await page.wait_for_timeout(2_000)
    # The voice list renders a window at a time, exactly like the project grid: Leda sat below the fold and the
    # first attempt reported "no voice row named 'Leda'". Scroll until the wanted row renders.
    row = page.locator(f"{OVERLAY} [role=option], {OVERLAY} button").filter(has_text=voice).first
    steps = 0
    while not await row.count() and steps < 12:
        moved = await page.evaluate(
            "(sel) => { const p = document.querySelector(sel);"
            "  const box = p && [...p.querySelectorAll('*')].find(e => e.scrollHeight > e.clientHeight + 20);"
            "  if (!box) return null; const was = box.scrollTop; box.scrollTop = was + box.clientHeight;"
            "  return [was, Math.round(box.scrollTop)]; }",
            OVERLAY,
        )
        await page.wait_for_timeout(700)
        steps += 1
        if not moved or moved[0] == moved[1]:
            break
    if not await row.count():
        return {"error": f"no voice row named {voice!r} after scrolling the selector {steps} times"}
    traffic = Traffic(session, None).start()
    await row.click(timeout=8_000)
    await page.wait_for_timeout(1_200)
    boxes = page.locator(f"{OVERLAY} textarea")
    filled = {}
    for index in range(await boxes.count()):
        box = boxes.nth(index)
        placeholder = (await box.get_attribute("placeholder")) or ""
        text = performance if "performance" in placeholder.lower() else "Xin chào, mình là người dẫn nhé!"
        await box.fill(text)
        filled[placeholder[:40]] = text
    await page.wait_for_timeout(600)
    before = await _shot(page, stamp, "voice_filled")
    # Typing a performance turns the dialog into a voice MAKER: a "Voice name" field appears (prefilled
    # "<Preset> custom") and the footer swaps "Add to character" for "Reset" + "Save new voice". So the commit
    # button depends on what was typed, and the name field is part of the answer.
    named = page.locator(f"{OVERLAY} input").filter(has_not=page.locator("[aria-label='Search assets']"))
    voice_name = None
    for index in range(await named.count()):
        value = await named.nth(index).input_value()
        if value and "custom" in value.lower():
            if wanted_name:
                await named.nth(index).fill(wanted_name)
                value = wanted_name
            voice_name = value
    commit = page.get_by_role(
        "button", name=re.compile("save new voice|add to character", re.IGNORECASE)
    ).first
    label = (await commit.inner_text()).strip()
    _safe(label, "committing the voice")
    await commit.click(timeout=10_000)
    await page.wait_for_timeout(4_000)
    seen = await traffic.stop()
    await session.goto(f"{session.project_url(project)}/character/{entity}", ready=characters_mod.EDIT_PAGE)
    await page.wait_for_timeout(3_000)
    return {
        "entity_id": entity,
        "voice": voice,
        "filled": filled,
        "voice_name": voice_name,
        "committed_with": label,
        "rpcids": sorted({r["rpcids"] for r in seen["requests"] if r["rpcids"]}),
        "requests": seen["requests"][:6],
        "replies": seen["replies"][:6],
        "buttons_after": await page.evaluate(_TEXT_JS, "button"),
        "shot_before_commit": before,
        "shot_after": await _shot(page, stamp, "voice_after"),
    }


async def act_addvoice(session: FlowSession, project: str, spec: str, stamp: str) -> dict[str, Any]:
    """Attach a PRESET to a character, typing nothing: the footer stays 'Add to character' only while the
    performance box is empty, so this is the plain attach path a generation needs. spec is `<entity>:<Voice>`."""
    entity, _, voice = spec.partition(":")
    page = session.page
    await session.goto(f"{session.project_url(project)}/character/{entity}", ready=characters_mod.EDIT_PAGE)
    await page.wait_for_timeout(2_500)
    before_buttons = await page.evaluate(_TEXT_JS, "button")
    opener = page.get_by_role("button", name=re.compile("select a voice|voice", re.IGNORECASE)).first
    _safe((await opener.inner_text()).strip(), "opening the voice selector")
    await opener.click(timeout=10_000)
    await page.wait_for_timeout(2_000)
    row = page.locator(f"{OVERLAY} [role=option], {OVERLAY} button").filter(has_text=voice).first
    steps = 0
    while not await row.count() and steps < 12:
        moved = await page.evaluate(
            "(sel) => { const p = document.querySelector(sel);"
            "  const box = p && [...p.querySelectorAll('*')].find(e => e.scrollHeight > e.clientHeight + 20);"
            "  if (!box) return null; const was = box.scrollTop; box.scrollTop = was + box.clientHeight;"
            "  return [was, Math.round(box.scrollTop)]; }",
            OVERLAY,
        )
        await page.wait_for_timeout(700)
        steps += 1
        if not moved or moved[0] == moved[1]:
            break
    if not await row.count():
        return {"error": f"no voice row named {voice!r} after {steps} scrolls"}
    traffic = Traffic(session, None).start()
    await row.click(timeout=8_000)
    await page.wait_for_timeout(1_500)
    commit = page.get_by_role("button", name=re.compile("add to character", re.IGNORECASE)).first
    if not await commit.count():
        return {"error": "the footer no longer offers 'Add to character'"}
    _safe((await commit.inner_text()).strip(), "attaching the voice")
    await commit.click(timeout=10_000)
    await page.wait_for_timeout(4_000)
    seen = await traffic.stop()
    await session.goto(f"{session.project_url(project)}/character/{entity}", ready=characters_mod.EDIT_PAGE)
    await page.wait_for_timeout(3_000)
    after_buttons = await page.evaluate(_TEXT_JS, "button")
    return {
        "entity_id": entity,
        "voice": voice,
        "scrolls": steps,
        "rpcids": sorted({r["rpcids"] for r in seen["requests"] if r["rpcids"]}),
        "requests": seen["requests"][:6],
        "replies": seen["replies"][:6],
        "button_changed": sorted(set(after_buttons) - set(before_buttons)),
        "shot": await _shot(page, stamp, "voice_attached"),
    }


async def act_saveframe(session: FlowSession, project: str, media: str, stamp: str) -> dict[str, Any]:
    """Click the clip editor's icon-only 'Save frame' and see what the project gains."""
    page = session.page
    before = await _media_ids(session, project)
    await session.goto(f"{session.project_url(project)}/edit/{media}", ready=clips_mod.EDITOR)
    await page.wait_for_timeout(3_500)
    button = page.get_by_role("button", name=re.compile("^save frame$", re.IGNORECASE)).first
    if not await button.count():
        return {"error": "no 'Save frame' control on this editor page"}
    _safe("save frame", "saving a frame")
    traffic = Traffic(session, None).start()
    await button.click(timeout=10_000)
    await page.wait_for_timeout(6_000)
    seen = await traffic.stop()
    shot = await _shot(page, stamp, "save_frame")
    after = await _media_ids(session, project)
    return {
        "media": media,
        "rpcids": sorted({r["rpcids"] for r in seen["requests"] if r["rpcids"]}),
        "requests": seen["requests"][:6],
        "new_ids": sorted(after - before),
        "snackbar": await page.evaluate(_TEXT_JS, "mat-snack-bar-container"),
        "shot": shot,
    }


async def act_saveclip(session: FlowSession, project: str, scene_id: str, stamp: str) -> dict[str, Any]:
    """Right-click a timeline clip and take 'Save to Project', then read what the listing gained."""
    page = session.page
    before = await _media_ids(session, project)
    await scenes._open_scene(session, project, scene_id)
    await page.wait_for_timeout(2_000)
    clips = page.locator(scenes.CLIPS)
    if not await clips.count():
        return {"error": "scene holds no clip"}
    await clips.first.click(button="right", timeout=10_000)
    await page.wait_for_timeout(1_500)
    item = page.locator(MENU_ITEM).filter(has_text=re.compile("save to project", re.IGNORECASE)).first
    if not await item.count():
        return {"error": "no 'Save to Project' item in the clip menu"}
    _safe("save to project", "saving a clip into the project")
    traffic = Traffic(session, None).start()
    await item.click(timeout=10_000)
    await page.wait_for_timeout(6_000)
    seen = await traffic.stop()
    shot = await _shot(page, stamp, "save_clip")
    after = await _media_ids(session, project)
    return {
        "scene": scene_id,
        "rpcids": sorted({r["rpcids"] for r in seen["requests"] if r["rpcids"]}),
        "requests": seen["requests"][:6],
        "new_ids": sorted(after - before),
        "snackbar": await page.evaluate(_TEXT_JS, "mat-snack-bar-container"),
        "shot": shot,
    }


def _newest_listed_video(records: list[dict[str, Any]]) -> str | None:
    ready = [r for r in records if r.get("kind") == "video" and r.get("status") == 3 and r.get("listed")]
    return max(ready, key=lambda r: r.get("created") or 0)["id"] if ready else None


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--character")
    ap.add_argument("--media")
    ap.add_argument("--scene")
    ap.add_argument("--do", action="append", default=[], dest="actions")
    args = ap.parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    report_path = OUT_DIR / f"voice_frames_{stamp}.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"project": args.project, "actions": []}

    def save() -> None:
        report_path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    async with FlowSession(args.profile) as session:
        media = args.media
        scene_id = args.scene
        names = {a.partition("=")[0] for a in args.actions}
        wants_media = bool(names & {"editor", "saveframe"}) and not media
        wants_scene = bool(names & {"clipmenu", "saveclip"}) and not scene_id
        if wants_media or wants_scene:
            listing = await reader.project(session, args.project, versions=True)
            media = media or _newest_listed_video(listing.get("versions") or [])
            if not scene_id:
                # A scene with no clip has nothing to right-click, and the acceptance runs leave empty ones behind.
                found = await scenes.list_scenes(session, args.project)
                listing = await scenes._listing(session, args.project)
                with_clips = [
                    s2["scene_id"] for s2 in found if scenes.clips_from_listing(listing, s2["scene_id"])
                ]
                scene_id = with_clips[0] if with_clips else (found[0]["scene_id"] if found else None)
            report["picked"] = {"media": media, "scene": scene_id}
        for action in args.actions:
            name, _, value = action.partition("=")
            record: dict[str, Any] = {"action": action, "started": time.strftime("%H:%M:%S")}
            report["actions"].append(record)
            t0 = time.monotonic()
            try:
                if action == "voices":
                    record["result"] = await act_voices(session, args.project, stamp)
                elif action == "character":
                    record["result"] = await act_character(session, args.project, args.character, stamp)
                elif action == "mention":
                    record["result"] = await act_mention(session, args.project, stamp)
                elif name == "setvoice":
                    record["result"] = await act_setvoice(session, args.project, value, stamp)
                elif name == "addvoice":
                    record["result"] = await act_addvoice(session, args.project, value, stamp)
                elif name == "saveframe":
                    record["result"] = await act_saveframe(session, args.project, value or media, stamp)
                elif name == "saveclip":
                    record["result"] = await act_saveclip(session, args.project, value or scene_id, stamp)
                elif action == "voicepicker":
                    record["result"] = await act_voicepicker(session, args.project, args.character, stamp)
                elif action == "clipmenu":
                    if not scene_id:
                        raise LookupError("no scene in this project to right-click a clip in")
                    record["result"] = await act_clipmenu(session, args.project, scene_id, stamp)
                elif action == "editor":
                    if not media:
                        raise LookupError("no finished video the grid lists, so no editor page to open")
                    record["result"] = await act_editor(session, args.project, media, stamp)
                else:
                    raise ValueError(f"unknown action {action!r}")
            except Exception as exc:  # noqa: BLE001
                record["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
            record["seconds"] = round(time.monotonic() - t0, 1)
            save()
            print(f"{action}: {record.get('error') or 'ok'} ({record['seconds']} s)", flush=True)
    print(f"report={report_path}")


if __name__ == "__main__":
    asyncio.run(main())
