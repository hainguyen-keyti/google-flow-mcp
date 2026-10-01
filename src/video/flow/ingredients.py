"""Characters put into the composer prompt with Flow's @ mention, project images added through the composer's "+"
dialog, and a video generated from them.

gflow drives the same gesture but still refuses to generate with a character on this host (`_unported_form` in
migrated_composer.py, v0.76.0), so the repo drives it and spends through the composer's one money path.

Measured 2026-10-01 (plan AL T4, $0 probes out/al/t4.json and t4b.json):
- the "+" button opens a dialog of categories (Images, Voices, Characters); under Images a search box takes a whole
  title and narrows a window of rows, each `button.asset-item[role=option]` with a title, a thumbnail and no id, so
  a row is told by its thumbnail's url, which ends like the image's listing url (before a size suffix);
- a click adds the ACTIVE row (the first one) and the dialog closes itself; any other row only becomes active, and
  'Add to prompt' adds it;
- every ingredient is a chip on `flow-ingredient-bar`, a mentioned character included; an image chip's thumbnail is a
  signed url whose path ends with the image's WORKFLOW id; a chip Flow refuses carries `.disabled-error-icon`
  whatever its kind, and its hover card says why ("Maximum image ingredients reached (3 allowed)"), while the price
  line does not move;
- the '@' picker is that same dialog and keeps the category it last showed, under which it offers no character, so
  characters are mentioned before the dialog is opened;
- one listed image (lily_black_face.png) is offered by no row of the dialog;
- under Voices (out/al/t5.json) the search finds a voice whatever its letter case and a voice of your own carries a
  badge; a voice chip carries no name, and its hover card names the voice after 'voice_selection'; a voice with no
  image or character beside it is greyed out with "An audio ingredient requires other ingredients to function.".

Measured 2026-09-16 (plan character-generation T1, out/character_mentions_20260916_175616.json):
- '@' and a name list `button.asset-item[role=option]`, each carrying only `.asset-title` and a `.type-subtitle`
  ('Character', 'Image'): no id, so an option is chosen by title AND kind, and clicking it inserts the chip at once;
- Flow lists characters and media under one search, so a file named like a character is offered beside it;
- a character chip carries data-reference-type="entity" and the entity id in data-entity-id and data-mention-id; an
  image chip carries data-reference-type="media" and the image's WORKFLOW id in data-mention-id;
- the submit (MZZa6b) body carries the model key abra_r2v_8s, the entity id and the image's workflow id, never the
  image's media id;
- gflow's apply_video_settings looked for the duration row 8 ms after switching the model and skipped the 8 s pin on
  one run of two, leaving the 10 s the composer remembered, so 8 s is pinned here once its radio renders.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlsplit

from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel, reference_cap_for
from gflow_cli.errors import UiSelectorDriftError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import agent, composer, overlays, parsers, reader
from video.flow.reader import capture, one
from video.session import PROJECT_READY, FlowSession

SECONDS = 8
PRICES = {"omni-flash": 12, "veo-lite": 10, "veo-fast": 20}
# Lengths the Ingredients composer offers per model (measured 2026-09-28: 4s 6s 8s 10s under Omni 1.1 Flash, no length
# group at all under Veo 3.1 Lite), limited to the ones this tool takes.
LENGTHS = {"omni-flash": (8, 10), "veo-lite": (8,), "veo-fast": (8,)}
# A longer length's price, entered only once measured: until then dry_run quotes it and a real run is refused.
# omni-flash 10 s: quoted 15 by the composer's price line on a dry run (2026-09-28, plan U), the same 15 the owner's
# own abra_r2v_10s cost that day.
LONGER_PRICES: dict[tuple[str, int], int] = {("omni-flash", 10): 15}


def price_for(model: str, seconds: int) -> int | None:
    return PRICES.get(model) if seconds == SECONDS else LONGER_PRICES.get((model, seconds))


ASPECTS = {"9:16": Aspect.PORTRAIT, "16:9": Aspect.LANDSCAPE}
LABELS = {"entity": "Character", "media": "Image"}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
BOX = "flow-prompt-box [contenteditable='true']"
OPTION = "button.asset-item[role=option]"
CONFIRM = "button.detail-add-to-prompt-btn"
CHIP = "flow-prompt-box .mention-chip"
RADIO = ".cdk-overlay-pane [role=radio]"
# The exact label: `aria-label*='ngredient'` also matches a bar chip, whose label is "Ingredient", and a click on a chip
# removes it ($0 probe 2026-10-01).
ADD = "flow-prompt-box button[aria-label='Add ingredients to the prompt box']"
DIALOG = ".cdk-overlay-pane, [role=dialog]"
SEARCH = "input[aria-label='Search assets']"
BAR = "flow-prompt-box flow-ingredient-bar button.chip-container"
BACKDROP = ".cdk-overlay-backdrop-showing"
OPTION_WAIT_MS = 12_000
COMMIT_WAIT_MS = 2_500
CHIP_WAIT_MS = 5_000
RADIO_WAIT_MS = 10_000
DIALOG_WAIT_MS = 20_000
ROW_WAIT_MS = 30_000
ROW_POLL_MS = 1_000
BAR_WAIT_MS = 12_000
CARD_WAIT_MS = 3_200
CARD_GONE_MS = 3_000
ESCAPES = 5
# The rows the "+" dialog renders at a time (measured 2026-10-01: 15 of the project's 67 images).
ROWS_WINDOW = 15
# The icon a bar chip carries, by the ingredient it stands for; an image chip carries a thumbnail instead.
ICON_KINDS = {"voice_selection": "voice", "accessibility_new": "character"}
# Flow's own refusal of an image over a model's cap, read off the composer's hover cards on 2026-10-01
# (out/flow_research/log_caps2.txt): Omni 1.1 Flash 7, Veo 3.1 Lite and Fast 3, Veo 3.1 Quality none.
CAP_WORDS = "Maximum image ingredients reached ({cap} allowed)"
NO_IMAGES_WORDS = "You cannot use image ingredients with this model."
CAPS_MEASURED = "2026-10-01"
# The voices each model takes as audio ingredients, and Flow's own refusals, read off the composer on 2026-10-01
# (log_caps2.txt; a voice alone out/al/t5.json). A character that has a voice takes one of them (t1b.json).
VOICE_CAPS = {"omni-flash": 5, "veo-lite": 1, "veo-fast": 1, "veo-quality": 0}
AUDIO_CAP_WORDS = "Maximum audio ingredients reached ({cap} allowed)"
NO_AUDIO_WORDS = "You cannot use audio ingredients with this model."
ALONE_WORDS = "An audio ingredient requires other ingredients to function."


def voice_caps_text() -> str:
    """The voice caps in words, for a tool's description, written from the table the driver refuses by."""
    caps = ", ".join(f"{model} {cap or 'none'}" for model, cap in VOICE_CAPS.items())
    return f"Voices each model takes (read off the composer on {CAPS_MEASURED}): {caps}."


_OPTIONS_JS = """(sel) => [...document.querySelectorAll(sel)].map(o => ({
  title: ((o.querySelector('.asset-title') || {}).textContent || '').trim(),
  kind: ((o.querySelector('.type-subtitle') || {}).textContent || '').trim(),
  visible: !!(o.offsetWidth || o.offsetHeight),
  active: o.classList.contains('asset-item-active'),
}))"""

_CHIPS_JS = """(sel) => [...document.querySelectorAll(sel)].map(c => ({
  kind: c.getAttribute('data-reference-type') || '',
  id: c.getAttribute('data-mention-id') || '',
  entity: c.getAttribute('data-entity-id') || '',
  text: (c.textContent || '').trim(),
}))"""

_CARET_END_JS = """(sel) => {
  const box = document.querySelector(sel);
  if (!box) return false;
  box.focus();
  const range = document.createRange();
  range.selectNodeContents(box);
  range.collapse(false);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  return document.activeElement === box || box.contains(document.activeElement);
}"""

_BOX_TEXT_JS = "(sel) => ((document.querySelector(sel) || {}).innerText || '').trim()"

_ROWS_JS = """(sel) => [...document.querySelectorAll(sel)].map(o => ({
  title: ((o.querySelector('.asset-title') || {}).textContent || '').trim(),
  src: (o.querySelector('img') || {getAttribute: () => ''}).getAttribute('src') || '',
  visible: !!(o.offsetWidth || o.offsetHeight),
  active: o.classList.contains('asset-item-active'),
  custom: !!o.querySelector('.custom-voice-badge-icon'),
}))"""

_BAR_JS = """(sel) => [...document.querySelectorAll(sel)].map(c => ({
  cls: String(c.className),
  text: (c.innerText || '').replace(/\\s+/g, ' ').trim(),
  error: !!c.querySelector('.disabled-error-icon'),
  src: (c.querySelector('img') || {getAttribute: () => ''}).getAttribute('src') || '',
}))"""

_CARD_JS = """() => [...document.querySelectorAll('.cdk-overlay-pane')].filter(e => e.offsetParent !== null)
  .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 200)).filter(Boolean)"""


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


@dataclass(frozen=True)
class Reference:
    kind: str
    id: str
    title: str
    mention_ids: frozenset[str]
    # An image only: the end of its listing url, which its row in the "+" dialog carries.
    tail: str = ""
    # A voice only: saved on this account (its row carries a badge), not one of Flow's presets.
    custom: bool = False

    @property
    def query(self) -> str:
        """What is typed after '@': no whitespace, which a mention query was never measured to survive."""
        text = self.title
        suffix = Path(text).suffix
        if self.kind == "media" and suffix.lower() in IMAGE_SUFFIXES:
            text = text[: -len(suffix)]
        words = text.split()
        return words[0] if words else ""


def resolve(
    listed_characters: list[dict[str, Any]],
    media: list[dict[str, Any]],
    records: list[dict[str, Any]],
    characters: list[str],
    media_ids: list[str],
    model: str,
    *,
    voices: list[str] | tuple[str, ...] = (),
    presets: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    customs: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> list[Reference]:
    """Name every requested character, image and voice from one listing, refusing anything the picker cannot single
    out. A voice is named the way the request carries it: a preset by its lowercase id, a voice of your own by its
    workflow id (measured 2026-10-01)."""
    from video.flow.video import TAIL

    wanted = [*characters, *media_ids]
    if not wanted:
        raise ValueError("at least one character or image is required")
    if len(set(wanted)) != len(wanted):
        raise ValueError(f"each character and image may be named once, got {wanted}")
    if len({_norm(voice) for voice in voices}) != len(voices):
        raise ValueError(f"each voice may be named once, got {list(voices)}")
    if voices:
        voice_cap = VOICE_CAPS.get(model)
        if voice_cap is None:
            raise ValueError(
                f"no voice cap was read for {model}; voices are measured on {sorted(VOICE_CAPS)}"
            )
        if len(voices) > voice_cap:
            words = AUDIO_CAP_WORDS.format(cap=voice_cap) if voice_cap else NO_AUDIO_WORDS
            raise ValueError(
                f"{model} takes at most {voice_cap} voice{'' if voice_cap == 1 else 's'}, got {len(voices)}: Flow's composer refuses the next "
                f"voice with {words!r} (read off the composer on {CAPS_MEASURED})"
            )
    cap = reference_cap_for(VideoModel.from_cli(model))
    if len(wanted) > cap and media_ids:
        # A mentioned character takes one of the image slots: on Veo 3.1 Lite a character and two images filled it,
        # and a third image was refused in these words (out/al/t4_live.json, 2026-10-01).
        words = CAP_WORDS.format(cap=cap) if cap else NO_IMAGES_WORDS
        raise ValueError(
            f"{model} takes at most {cap} references, characters and images together, got {len(wanted)}: Flow's "
            f"composer refuses the next image with {words!r} (read off the composer on {CAPS_MEASURED})"
        )
    if len(wanted) > cap:
        raise ValueError(f"{model} takes at most {cap} references, got {len(wanted)}")

    references: list[Reference] = []
    by_entity = {each["entity_id"]: each for each in listed_characters}
    names = [_norm(each.get("name")) for each in listed_characters]
    for entity in characters:
        found = by_entity.get(entity)
        if found is None:
            # A name passed for an id read as "not a character" about one that was (measured 2026-09-28).
            named = [
                each["entity_id"] for each in listed_characters if _norm(each.get("name")) == _norm(entity)
            ]
            if len(named) > 1:
                raise LookupError(
                    f"{entity!r} is the name of {len(named)} characters, which the picker cannot tell apart; rename "
                    f"one, then pass its entity id ({' or '.join(named)})"
                )
            if named:
                raise LookupError(
                    f"{entity!r} is a character's name, not its entity id; pass {named[0]} "
                    "(flow_characters lists both)"
                )
            raise LookupError(f"{entity} is not a character of this project; flow_characters lists them")
        name = found.get("name") or ""
        if not _norm(name):
            raise LookupError(f"character {entity} has no name to mention")
        if names.count(_norm(name)) > 1:
            raise LookupError(
                f"{names.count(_norm(name))} characters are named {name!r}, which the picker cannot tell apart; "
                "rename one"
            )
        references.append(Reference("entity", entity, name, frozenset({entity})))

    by_media = {each["id"]: each for each in media}
    for media_id in media_ids:
        found = by_media.get(media_id)
        if found is None:
            raise LookupError(f"{media_id} is not a media of this project; flow_media lists them")
        if found.get("kind") != "image":
            raise ValueError(f"{media_id} is a {found.get('kind')}; only images are measured as references")
        title = found.get("title") or ""
        if not _norm(title):
            raise LookupError(f"image {media_id} has no title for the ingredients dialog's search")
        mine = [each for each in records if each.get("id") == media_id]
        workflows = frozenset(each["workflow_id"] for each in mine if each.get("workflow_id"))
        if not workflows:
            raise LookupError(
                f"image {media_id} has no workflow id in the listing, so its chip cannot be checked"
            )
        urls = [str(each["url"]) for each in mine if each.get("url")]
        if not urls:
            raise LookupError(
                f"image {media_id} has no url in the listing, so its row in the ingredients dialog cannot be told"
            )
        tail = urls[0][-TAIL:]
        # The title is only the dialog's search text; the row is told by its url, so images sharing a title
        # (gen_i2i's own titles repeat, measured 2026-09-30) are refused only when their urls end alike.
        twins = {
            each["id"]
            for each in media
            if each["id"] != media_id and _norm(each.get("title")) == _norm(title)
        }
        if any(str(each.get("url") or "").endswith(tail) for each in records if each.get("id") in twins):
            raise LookupError(
                f"another image titled {title!r} ends with the same url, so the dialog cannot tell them apart: "
                "rename the file (a unique name) and flow_upload it again, then pass the new media id"
            )
        references.append(Reference("media", media_id, title, workflows, tail))

    offered = [(each["name"], each["id"], False) for each in presets] + [
        (each["name"], each["workflow_id"], True) for each in customs
    ]
    for voice in voices:
        named = [(name, token, custom) for name, token, custom in offered if _norm(name) == _norm(voice)]
        if len(named) > 1:
            raise LookupError(
                f"{len(named)} voices are named {voice!r} (preset or your own: "
                f"{['your own' if custom else 'preset' for _, _, custom in named]}); not guessing which one is meant"
            )
        if not named:
            raise LookupError(
                f"no voice named {voice!r} in this project; it offers {sorted(name for name, _, _ in offered)}"
            )
        name, token, custom = named[0]
        references.append(Reference("voice", token, name, frozenset({token}), custom=custom))
    return references


async def chips_on(page: Any) -> list[dict[str, str]]:
    return await page.evaluate(_CHIPS_JS, CHIP)


async def _options(page: Any) -> list[dict[str, Any]]:
    waited = 0
    while await page.locator(OPTION).count() == 0:
        if waited >= OPTION_WAIT_MS:
            return []
        await page.wait_for_timeout(500)
        waited += 500
    # T1 read a settled list 1.5 s after the first option showed; the search can still narrow before that.
    await page.wait_for_timeout(1_500)
    return await page.evaluate(_OPTIONS_JS, OPTION)


def _is(option: dict[str, Any], reference: Reference) -> bool:
    return (
        bool(option.get("visible"))
        and _norm(option.get("title")) == _norm(reference.title)
        and _norm(option.get("kind")) == _norm(LABELS[reference.kind])
    )


def _added(before: list[dict[str, str]], after: list[dict[str, str]]) -> list[dict[str, str]]:
    added = list(after)
    for chip in before:
        if chip in added:
            added.remove(chip)
    return added


async def _chips_after(page: Any, before: list[dict[str, str]], wait_ms: int) -> list[dict[str, str]]:
    after = await chips_on(page)
    waited = 0
    while len(after) <= len(before) and waited < wait_ms:
        await page.wait_for_timeout(500)
        waited += 500
        after = await chips_on(page)
    return after


async def attach(page: Any, reference: Reference) -> dict[str, str]:
    """Mention one reference by clicking its one option, then prove the chip that landed is that reference.

    Never Enter: with no picker open, Enter in the prompt box may send the prompt (gflow types prompts with
    insert_text for that reason), and a submit here would spend with no ledger row.

    Measured 2026-09-16 (L2 run 2 and a $0 diagnosis): a click commits only the ACTIVE option, which is the first one
    until something else is chosen; any other option just becomes active, and the picker's 'Add to prompt' commits
    it. So when a click adds no chip, 'Add to prompt' is pressed only once the wanted option is the active one.
    """
    label = LABELS[reference.kind]
    before = await chips_on(page)
    query = reference.query
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2_200)
    await page.keyboard.type(query, delay=100)
    options = await _options(page)
    matches = [index for index, option in enumerate(options) if _is(option, reference)]
    if len(matches) != 1:
        for _ in range(len(query) + 1):
            await page.keyboard.press("Backspace")
        offered = ", ".join(f"{o.get('title')} ({o.get('kind')})" for o in options[:8]) or "nothing"
        raise LookupError(
            f"{len(matches)} picker options are the {label.lower()} {reference.title!r} (offered: {offered}); "
            "not guessing"
        )
    await page.locator(OPTION).nth(matches[0]).click(timeout=8_000)

    after = await _chips_after(page, before, COMMIT_WAIT_MS)
    if len(after) <= len(before):
        now = [option for option in await page.evaluate(_OPTIONS_JS, OPTION) if option.get("visible")]
        if not now:
            raise LookupError(
                f"clicking the {label.lower()} {reference.title!r} added no chip and closed the picker; "
                "refusing to generate with the wrong reference"
            )
        active = [option for option in now if option.get("active")]
        if len(active) != 1 or not _is(active[0], reference):
            shown = [f"{o.get('title')} ({o.get('kind')})" for o in active]
            raise LookupError(
                f"clicking the {label.lower()} {reference.title!r} left {shown} active; refusing to commit another "
                "option"
            )
        confirm = page.locator(CONFIRM)
        visible = [index for index in range(await confirm.count()) if await confirm.nth(index).is_visible()]
        if len(visible) != 1:
            raise LookupError(
                f"{len(visible)} visible 'Add to prompt' buttons while {reference.title!r} is active; not guessing"
            )
        await confirm.nth(visible[0]).click(timeout=8_000)
        after = await _chips_after(page, before, CHIP_WAIT_MS)
    added = _added(before, after)
    # L2 run 3 (2026-09-16): a character chip landed with its data-mention-id while data-entity-id was still empty,
    # and T1 read it filled 2.5 s after the click; wait for it rather than judge a chip still being built.
    waited = 0
    while (
        reference.kind == "entity"
        and len(added) == 1
        and not added[0].get("entity")
        and waited < CHIP_WAIT_MS
    ):
        await page.wait_for_timeout(500)
        waited += 500
        added = _added(before, await chips_on(page))
    chip = added[0] if len(added) == 1 else None
    if (
        chip is None
        or chip.get("kind") != reference.kind
        or chip.get("id") not in reference.mention_ids
        or (reference.kind == "entity" and chip.get("entity") != reference.id)
    ):
        raise LookupError(
            f"clicking the {label.lower()} {reference.title!r} added {added}, not a {reference.kind} chip naming "
            f"{sorted(reference.mention_ids)}; refusing to generate with the wrong reference"
        )
    return {"kind": chip["kind"], "id": chip["id"], "text": chip.get("text", "")}


def _bar_chip(raw: dict[str, Any]) -> dict[str, Any]:
    """One chip of the ingredient bar from what the page shows of it, the signed url itself left behind.

    Measured 2026-10-01: a voice and a character chip carry their icon, an image chip a thumbnail whose url path ends
    with the image's workflow id (the thumbnail can land late, and until then the chip is of no known kind); a chip
    Flow refuses carries the error icon whatever its kind, while its class differs by kind (`chip-container-disabled`
    on an image, `disabled` on a voice, nothing on a character), so the icon is what is read."""
    seen = str(raw.get("text") or "")
    src = str(raw.get("src") or "")
    kind = next((kind for icon, kind in ICON_KINDS.items() if icon in seen), "image" if src else "other")
    return {
        "kind": kind,
        "id": urlsplit(src).path.rsplit("/", 1)[-1] if kind == "image" else "",
        "refused": bool(raw.get("error")),
        "seen": seen,
    }


async def bar_chips(page: Any) -> list[dict[str, Any]]:
    return [_bar_chip(raw) for raw in await page.evaluate(_BAR_JS, BAR)]


async def _settled_bar(page: Any) -> list[dict[str, Any]]:
    """The bar once every chip shows what it is: an image chip read before its thumbnail lands is of no known kind."""
    chips = await bar_chips(page)
    waited = 0
    while any(chip["kind"] == "other" for chip in chips) and waited < BAR_WAIT_MS:
        await page.wait_for_timeout(1_000)
        waited += 1_000
        chips = await bar_chips(page)
    return chips


def bar_problems(bar: list[dict[str, Any]], references: list[Reference]) -> list[str]:
    """Everything that sets the ingredient bar apart from what was asked for: each image by the workflow id its chip
    names, each voice by the name its hover card gives (`name`, read by check_bar), one chip for each character, no
    chip of any other kind, and none that Flow refuses."""
    problems = []
    images = [chip for chip in bar if chip["kind"] == "image"]
    asked = [reference for reference in references if reference.kind == "media"]
    for reference in asked:
        mine = [chip for chip in images if chip["id"] in reference.mention_ids]
        if len(mine) != 1:
            problems.append(f"the image {reference.title!r} ({reference.id}) has {len(mine)} chips, not one")
    known = {mention for reference in asked for mention in reference.mention_ids}
    problems += [
        f"an image chip names {chip['id'] or 'nothing'}, which was not asked for"
        for chip in images
        if chip["id"] not in known
    ]
    characters = sum(chip["kind"] == "character" for chip in bar)
    entities = sum(reference.kind == "entity" for reference in references)
    if characters != entities:
        problems.append(f"{characters} character chips for {entities} characters")
    voices = [chip for chip in bar if chip["kind"] == "voice"]
    spoken = [reference for reference in references if reference.kind == "voice"]
    for reference in spoken:
        mine = [chip for chip in voices if _norm(chip.get("name")) == _norm(reference.title)]
        if len(mine) != 1:
            problems.append(f"the voice {reference.title!r} has {len(mine)} chips, not one")
    names = {_norm(reference.title) for reference in spoken}
    problems += [
        f"a voice chip names {chip.get('name') or 'nothing'}, which was not asked for"
        for chip in voices
        if _norm(chip.get("name")) not in names
    ]
    problems += [
        f"a chip showing {chip['seen']!r} is none of image, character, voice"
        for chip in bar
        if chip["kind"] == "other"
    ]
    problems += [f"Flow refuses the {chip['kind']} chip" for chip in bar if chip["refused"]]
    return problems


async def _card(page: Any, index: int) -> list[str]:
    """What the bar chip at `index` shows on hover: the panes that came up with the pointer on it, none when no card
    shows. A pane already on the page (a toast is a `.cdk-overlay-pane` too) is not the chip's card.

    Measured 2026-10-02 (out/al/t7_card.json): no pane shows before a hover, a voice's card is up within 0.4 s, an
    image chip that is not refused shows none, and a card is gone 0.2 s after the pointer leaves. A card that stays
    is refused here, before any click: left over the composer it could take the click on Start generation."""
    standing = await page.evaluate(_CARD_JS)
    await page.locator(BAR).nth(index).hover(timeout=3_000, force=True)
    cards: list[str] = []
    waited = 0
    while not cards and waited < CARD_WAIT_MS:
        await page.wait_for_timeout(400)
        waited += 400
        cards = [pane for pane in await page.evaluate(_CARD_JS) if pane not in standing]
    await page.mouse.move(5, 5)
    waited = 0
    while cards and waited < CARD_GONE_MS:
        await page.wait_for_timeout(200)
        waited += 200
        if not [pane for pane in await page.evaluate(_CARD_JS) if pane in cards]:
            return cards
    if cards:
        raise LookupError(
            f"the hover card of an ingredient chip stayed open after the pointer left it ({cards[0][:80]!r}); "
            "refusing to go on with it over the composer"
        )
    return cards


def _card_voice(cards: list[str]) -> str:
    """The voice a chip's card names: 'play_arrow 0:06 voice_selection Achird' (measured 2026-10-01). Only a pane
    holding `voice_selection` is a voice's card, and two of them name nothing."""
    named = [
        found.group(1).strip() for pane in cards if (found := re.search(r"voice_selection\s+(.+)$", pane))
    ]
    return named[0] if len(named) == 1 else ""


def _card_refusal(cards: list[str]) -> str:
    """Flow's refusal on a chip's card, which comes before a voice's player when the chip is a voice (measured
    2026-10-01)."""
    said = [re.split(r"\s*\bplay_arrow\b", pane, maxsplit=1)[0].strip() for pane in cards]
    return " / ".join(each for each in said if each)


async def check_bar(
    page: Any, references: list[Reference], *, at_click: bool = False
) -> list[dict[str, Any]]:
    """The settled bar, or a refusal naming what is wrong with it, in Flow's own words for a chip Flow refuses.

    A voice chip carries no name, so its hover card is read for one. The price line reads the same with a refused
    chip on the bar (10 on Veo 3.1 Lite beside a refused fourth image, measured 2026-10-01), so this read is what
    keeps a refused or missing ingredient from being paid for."""
    bar = await _settled_bar(page)
    for index, chip in enumerate(bar):
        if chip["kind"] == "voice" or chip["refused"]:
            cards = await _card(page, index)
            chip["name"] = _card_voice(cards) if chip["kind"] == "voice" else ""
            chip["said"] = _card_refusal(cards) if chip["refused"] else ""
    problems = bar_problems(bar, references)
    if not problems:
        return bar
    said = sorted({chip["said"] for chip in bar if chip.get("said")})
    flow = "; Flow says: " + " / ".join(said) if said else ""
    raise LookupError(
        f"{'right before the click ' if at_click else ''}the ingredient bar is not what was asked for: "
        f"{'; '.join(problems)}{flow}; {'refusing to spend' if at_click else 'refusing to generate'}"
    )


async def close_dialog(page: Any) -> None:
    """Escape until no overlay backdrop is left: with a search typed, the first Escape only empties the search box
    (measured 2026-10-01). Escape is pressed only while a backdrop shows."""
    for _ in range(ESCAPES):
        if not await page.locator(BACKDROP).count():
            return
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(900)
    if await page.locator(BACKDROP).count():
        raise LookupError(f"the ingredients dialog did not close after {ESCAPES} Escapes; refusing to go on")


async def _bar_after(page: Any, before: list[dict[str, Any]], wait_ms: int) -> list[dict[str, Any]]:
    after = await bar_chips(page)
    waited = 0
    while len(after) <= len(before) and waited < wait_ms:
        await page.wait_for_timeout(500)
        waited += 500
        after = await bar_chips(page)
    return after


async def _add_from_dialog(
    page: Any, reference: Reference, category: str, is_mine: Any, what: str, unseen: str
) -> tuple[dict[str, Any], int]:
    """Add the one row of `category` that `is_mine` tells, through the composer's "+" dialog, and return the chip that
    landed on the bar with its place there.

    The title is the search text. The rows are read twice alike before an index is trusted, since the first read after
    typing can still show the unfiltered rows; rows that never come to rest are refused. A click adds only the ACTIVE
    row, so when it adds no chip 'Add to prompt' is pressed only once the wanted row is the active one. Never Enter.
    An overlay this driver did not open is named and left alone (plan AL, I5): Escape would dismiss it unnamed.
    """

    def mine_in(rows: list[dict[str, Any]]) -> list[int]:
        return [index for index, row in enumerate(rows) if row.get("visible") and is_mine(row)]

    before = await _settled_bar(page)
    if await page.locator(BACKDROP).count():
        if not await page.locator(DIALOG).locator(SEARCH).count():
            said = " ".join(str(await page.evaluate(composer._OVERLAY_TEXT_JS)).split())[:200]
            raise LookupError(
                f"an overlay this driver did not open stands over the composer, saying {said!r}; nothing was pressed"
            )
        await close_dialog(page)
    try:
        await page.locator(ADD).first.click(timeout=8_000)
    except PlaywrightTimeoutError:
        cover = await overlays.covering(page, ADD)
        if cover is None:
            raise
        where = cover["tag"] + (f"#{cover['id']}" if cover.get("id") else "")
        raise LookupError(
            f"the composer's '+' button is covered by {where}, which says {cover['text']!r}; nothing was clicked"
        ) from None
    tab = page.locator(DIALOG).get_by_text(category, exact=True).first
    try:
        await tab.wait_for(state="visible", timeout=DIALOG_WAIT_MS)
    except PlaywrightTimeoutError:
        await close_dialog(page)
        raise LookupError(
            f"the ingredients dialog showed no {category} category; nothing was attached"
        ) from None
    await tab.click(timeout=8_000)
    search = page.locator(DIALOG).locator(SEARCH).first
    await search.wait_for(state="visible", timeout=DIALOG_WAIT_MS)
    await search.fill(reference.title)
    # An image uploaded a moment ago reaches a picker's results late (measured 2026-09-29), so look for a while.
    rows: list[dict[str, Any]] = []
    previous: list[dict[str, Any]] | None = None
    waited = 0
    while True:
        await page.wait_for_timeout(ROW_POLL_MS)
        waited += ROW_POLL_MS
        rows = await page.evaluate(_ROWS_JS, OPTION)
        if (mine_in(rows) and rows == previous) or waited >= ROW_WAIT_MS:
            break
        previous = rows
    mine = mine_in(rows)
    if mine and rows != previous:
        await close_dialog(page)
        raise LookupError(
            f"the rows of the ingredients dialog never settled while looking for the {what} {reference.title!r} "
            f"({reference.id}); nothing was attached"
        )
    if len(mine) != 1:
        shown = [row.get("title") for row in rows if row.get("visible")]
        await close_dialog(page)
        if mine:
            raise LookupError(
                f"{len(mine)} rows of the ingredients dialog are the {what} {reference.title!r} ({reference.id}); "
                "not guessing"
            )
        if len(shown) >= ROWS_WINDOW:
            # Whether a search can match more rows than the dialog renders was never measured.
            unseen = (
                f"and the dialog renders a window of {ROWS_WINDOW} rows at a time, so this one may sit below them: "
                "give it a title of its own (rename the file and flow_upload it again)"
            )
        raise LookupError(
            f"the ingredients dialog offers no {what} {reference.title!r} ({reference.id}): it shows "
            f"{len(shown)} row{'' if len(shown) == 1 else 's'} for that title ({shown[:8]}), {unseen}; nothing was "
            "attached"
        )
    await page.locator(OPTION).nth(mine[0]).click(timeout=8_000)
    after = await _bar_after(page, before, COMMIT_WAIT_MS)
    if len(after) <= len(before):
        now = await page.evaluate(_ROWS_JS, OPTION)
        if any(row.get("visible") for row in now):
            active = [index for index, row in enumerate(now) if row.get("visible") and row.get("active")]
            if len(active) != 1 or active != mine_in(now):
                await close_dialog(page)
                raise LookupError(
                    f"clicking the {what} {reference.title!r} left another row active; refusing to add another "
                    f"{what}"
                )
            confirm = page.locator(CONFIRM)
            visible = [
                index for index in range(await confirm.count()) if await confirm.nth(index).is_visible()
            ]
            if len(visible) != 1:
                await close_dialog(page)
                raise LookupError(
                    f"{len(visible)} visible 'Add to prompt' buttons while {reference.title!r} is active; not guessing"
                )
            await confirm.nth(visible[0]).click(timeout=8_000)
            after = await _bar_after(page, before, CHIP_WAIT_MS)
    await close_dialog(page)
    after = await _settled_bar(page)
    added = _added(before, after)
    if len(added) != 1:
        raise LookupError(
            f"adding the {what} {reference.title!r} added {len(added)} ingredient chips, not one; refusing to "
            "generate"
        )
    return added[0], len(after) - 1 - after[::-1].index(added[0])


async def attach_image(page: Any, reference: Reference) -> dict[str, str]:
    """Add one project image through the composer's "+" dialog and prove the chip that landed is that image: its row
    is the one whose thumbnail ends like the image's listing url, and the chip must name the image's workflow id and
    must not be refused."""
    from video.flow.video import _bare_src

    if reference.kind != "media" or not reference.tail:
        raise ValueError(f"{reference.title!r} carries no listing url to tell its row in the dialog by")
    chip, index = await _add_from_dialog(
        page,
        reference,
        "Images",
        lambda row: _bare_src(row.get("src")).endswith(reference.tail),
        "image",
        "none carrying this image's url. Flow lists the image in the project and still does not offer it as an "
        "ingredient",
    )
    if chip["kind"] != "image" or chip["id"] not in reference.mention_ids:
        raise LookupError(
            f"adding the image {reference.title!r} put a {chip['kind']} chip naming {chip['id'] or 'nothing'} on "
            f"the bar, not an image chip naming {sorted(reference.mention_ids)}; refusing to generate with the "
            "wrong image"
        )
    if chip["refused"]:
        words = _card_refusal(await _card(page, index))
        raise LookupError(
            f"Flow refuses the image {reference.title!r} ({reference.id}) on this model"
            f"{': ' + words if words else ''}; refusing to generate"
        )
    return {"kind": "media", "id": chip["id"], "text": reference.title}


async def attach_voice(page: Any, reference: Reference) -> dict[str, str]:
    """Add one voice through the composer's "+" dialog and prove the chip that landed is that voice.

    Measured 2026-10-01 (out/al/t5.json): the Voices search finds a voice whatever its letter case; a voice of your
    own carries a badge, a preset none; the chip carries no name, and its hover card names the voice after
    `voice_selection`, behind Flow's refusal when there is one."""
    if reference.kind != "voice":
        raise ValueError(f"{reference.title!r} is a {reference.kind}, not a voice")
    chip, index = await _add_from_dialog(
        page,
        reference,
        "Voices",
        lambda row: (
            _norm(row.get("title")) == _norm(reference.title) and bool(row.get("custom")) == reference.custom
        ),
        "voice",
        f"none of that name {'with' if reference.custom else 'without'} the badge of a voice of your own",
    )
    cards = await _card(page, index)
    # A refused chip stops the run whatever it names, and Flow's own words are what the caller can act on.
    if chip["refused"]:
        raise LookupError(
            f"Flow refuses the voice {reference.title!r} on this model: {_card_refusal(cards) or 'no reason shown'}; "
            "refusing to generate"
        )
    named = _card_voice(cards)
    if chip["kind"] != "voice" or _norm(named) != _norm(reference.title):
        raise LookupError(
            f"adding the voice {reference.title!r} put a {chip['kind']} chip whose card names {named or 'nothing'!r} "
            "on the bar; refusing to generate with the wrong voice"
        )
    return {"kind": "voice", "id": reference.id, "text": reference.title}


async def apply_settings(page: Any, model: str, aspect: str, references: list[Reference]) -> None:
    """References mode, model and aspect through gflow's own radios, which read every choice back.

    Measured 2026-09-16 (L2 run 1, then a $0 diagnosis): right after the composer's own settings pass the pane was
    closed, yet gflow's first open of it showed no option groups while a second open worked, the toggle the
    composer's `_open_settings` already retries once. Opening the pane spends nothing, so that one failure gets one
    more open.

    The request is t2v carrying the characters, not r2v: its r2v duration pin raised on veo-lite in L2 run 2, having
    counted the Omni 8s radio just before the model switch removed that row, so pin_duration sets the length instead.
    gflow selects Ingredients only for a request with characters; a run from images alone gets it from the composer's
    own mode argument, set by generate and read back at the confirm step (plan X, 2026-09-28).
    """
    entities = [reference for reference in references if reference.kind == "entity"]
    request = GenerateVideoRequest(
        prompt="character references",
        mode=Mode.T2V,
        aspect=ASPECTS[aspect],
        model=VideoModel.from_cli(model),
        reference_entities=tuple(reference.id for reference in entities),
        reference_entity_names=tuple(reference.title for reference in entities),
    )
    settings = mc.MigratedComposer()
    try:
        await settings.apply_video_settings(page, request)
    except UiSelectorDriftError as exc:
        if "rendered no option groups" not in str(exc):
            raise
        await settings.apply_video_settings(page, request)


async def pin_duration(page: Any, seconds: int = SECONDS) -> str:
    """Check the radio for `seconds` once it renders; a model whose pane shows no duration row answers 'absent'."""
    await composer._open_settings(page, "duration")
    try:
        radio = page.locator(RADIO).filter(has_text=re.compile(rf"^\s*{seconds}s\s*$"))
        waited = 0
        while await radio.count() == 0 and waited < RADIO_WAIT_MS:
            await page.wait_for_timeout(500)
            waited += 500
        count = await radio.count()
        if count == 0:
            return "absent"
        if count > 1:
            raise LookupError(f"{count} duration radios read {seconds}s; not guessing")
        if await radio.first.get_attribute("aria-checked") != "true":
            await radio.first.click(timeout=4_000)
            await page.wait_for_timeout(1_200)
        if await radio.first.get_attribute("aria-checked") != "true":
            raise LookupError(f"the {seconds}s duration radio did not stay checked")
        return "pinned"
    finally:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(1_000)


RESOLUTION_RADIO = RADIO


async def pin_resolution(page: Any, resolution: str) -> None:
    """Check the resolution radio (its label reads '360p' plus an info icon); refuse when it does not stay checked."""
    await composer._open_settings(page, "resolution")
    try:
        radio = page.locator(RESOLUTION_RADIO).filter(has_text=re.compile(rf"^\s*{re.escape(resolution)}"))
        if await radio.count() != 1:
            raise LookupError(f"{await radio.count()} radios read {resolution}; not guessing")
        if await radio.first.get_attribute("aria-checked") != "true":
            await radio.first.click(timeout=4_000)
            await page.wait_for_timeout(1_200)
        if await radio.first.get_attribute("aria-checked") != "true":
            raise LookupError(f"the {resolution} radio did not stay checked")
    finally:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(1_000)


def _submit_frames(node: Any) -> Any:
    if isinstance(node, list):
        if (
            len(node) >= 2
            and isinstance(node[0], str)
            and node[0] in mc.SUBMIT_RPCS
            and isinstance(node[1], str)
        ):
            yield node
            return
        for each in node:
            yield from _submit_frames(each)


def request_voices(post_data: str) -> list[list[str]] | None:
    """The voices each item of a submit carries, read off their own field and never off a word of the prompt.

    Measured 2026-10-01 on the submit bodies of plan AK (out/flow_research/body_ak-*.txt): an Ingredients item (rpc
    MZZa6b) carries the voices at [7] as [["<voice of your own: workflow id>"], ["achird"]] and leaves [7] out when no
    voice rides; a Frames item carries none. Each of them held one item; the items are kept apart so that several,
    should Flow send one per clip, each answer for themselves. None when the body is no submit this can read."""
    try:
        outer = json.loads(parse_qs(post_data).get("f.req", [""])[0])
    except ValueError:
        return None
    for frame in _submit_frames(outer):
        try:
            inner = json.loads(frame[1])
        except ValueError:
            return None
        if not (isinstance(inner, list) and inner and isinstance(inner[0], list)):
            return None
        return [
            [each[0] for each in arm if isinstance(each, list) and each and isinstance(each[0], str)]
            for arm in (
                item[7] if isinstance(item, list) and len(item) > 7 and isinstance(item[7], list) else []
                for item in inner[0]
            )
        ]
    return None


def recipe_check(recipe: dict[str, Any], references: list[Reference]) -> dict[str, Any]:
    """Whether a clip kept every reference it was given, by the recipe the listing holds for it (clip_recipe): each
    voice by its id, each image by a workflow id of its own, each character by its entity id, and nothing more."""
    kept_voices = [str(each.get("voice")) for each in recipe.get("voices") or []]
    kept_images = [str(each.get("workflow_id")) for each in recipe.get("reference_images") or []]
    kept_characters = [str(each.get("entity_id")) for each in recipe.get("characters") or []]
    missing = [
        reference.id
        for reference in references
        if not set(reference.mention_ids)
        & set({"voice": kept_voices, "media": kept_images, "entity": kept_characters}[reference.kind])
    ]
    asked = {mention for reference in references for mention in reference.mention_ids}
    unexpected = [kept for kept in [*kept_voices, *kept_images, *kept_characters] if kept not in asked]
    return {"ok": not missing and not unexpected, "missing": missing, "unexpected": unexpected}


class SubmitBodyCheck:
    """What the submit request carried, read as it leaves: a references model key and every reference's id."""

    def __init__(self, references: list[Reference]) -> None:
        self.references = references
        self.seen: dict[str, Any] | None = None

    def on_request(self, request: Any) -> None:
        url = str(getattr(request, "url", "") or "")
        if self.seen is not None or "batchexecute" not in url:
            return
        rpcids = parse_qs(urlsplit(url).query).get("rpcids", [""])[0].split(",")
        submit = [rpcid for rpcid in rpcids if rpcid in mc.SUBMIT_RPCS]
        if not submit:
            return
        try:
            body = unquote_plus(request.post_data or "")
        except Exception:  # noqa: BLE001
            body = ""
        keys = sorted(set(mc.MODEL_KEY.findall(body)))
        missing = [
            reference.id
            for reference in self.references
            if not any(mention in body for mention in reference.mention_ids)
        ]
        self.seen = {
            "rpcid": submit[0],
            "model_keys": keys,
            "missing": missing,
            "ok": bool(body) and any("_r2v_" in key for key in keys) and not missing,
        }

    def report(self) -> dict[str, Any]:
        if self.seen is not None:
            return self.seen
        return {"rpcid": None, "model_keys": [], "missing": [r.id for r in self.references], "ok": False}


async def _listing(session: FlowSession, project_id: str) -> Any:
    frames = await capture(
        session, lambda: session.goto(session.project_url(project_id), ready=PROJECT_READY), settle=8.0
    )
    return one(frames, "Zzl0ze")


async def generate(
    session: FlowSession,
    project_id: str,
    *,
    prompt: str,
    characters: list[str],
    media_ids: list[str] | tuple[str, ...] = (),
    model: str = "omni-flash",
    aspect: str = "9:16",
    job_id: str | None = None,
    out_dir: Path = Path("out"),
    dry_run: bool = False,
    wait: float = 360.0,
    duration: int = SECONDS,
    count: int = 1,
    resolution: str | None = None,
    max_credits: int | None = None,
    body_check_for: Any = None,
    table_credits: int = 0,
    voices: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """One video from characters, project images and voices, or with dry_run the quote and the chips, never a click.

    With max_credits (gen_video, plan AB) the caller has checked the settings against the surveyed options, and the
    live price line is held against that cap instead of this module's own table. A paid clip's recipe is read back
    off the listing, and a clip that dropped or gained a reference is an error though it was paid (plan AL, I4)."""
    if max_credits is not None:
        expected = table_credits
    elif model not in PRICES:
        raise ValueError(f"model must be one of {sorted(PRICES)}, got {model!r}")
    elif aspect not in ASPECTS:
        raise ValueError(f"aspect must be one of {sorted(ASPECTS)}, got {aspect!r}")
    elif duration not in LENGTHS[model]:
        raise ValueError(f"duration must be one of {list(LENGTHS[model])} for {model}, got {duration}")
    else:
        expected = price_for(model, duration)
    if max_credits is None and expected is None and not dry_run:
        raise ValueError(
            f"no measured price for {model} at {duration}s: read it with dry_run first, a real run is refused "
            "rather than priced by guess"
        )
    if not dry_run and not job_id:
        raise ValueError("job_id is required unless dry_run")
    listing = await _listing(session, project_id)
    references = resolve(
        parsers.characters_from_listing(listing),
        parsers.media(listing),
        parsers.records(listing),
        list(characters),
        list(media_ids),
        model,
        voices=list(voices),
        presets=parsers.voices_from_listing(listing),
        customs=parsers.custom_voices(listing),
    )

    entities = [reference for reference in references if reference.kind == "entity"]
    images = [reference for reference in references if reference.kind == "media"]
    spoken = [reference for reference in references if reference.kind == "voice"]
    mentioned: list[dict[str, str]] = []
    added: list[dict[str, str]] = []

    async def setup(active: FlowSession) -> dict[str, Any]:
        page = active.page
        await apply_settings(page, model, aspect, references)
        duration_row = await pin_duration(page, duration)
        # Shown no length control, a longer run would go out at whatever length the composer remembers (review of
        # plan U, F3); the default keeps its old behaviour, where the price line tells the lengths apart.
        if duration_row != "pinned" and duration != SECONDS:
            raise LookupError(
                f"asked for {duration}s but the composer shows no {duration}s length to pin; refusing"
            )
        if resolution is not None:
            await pin_resolution(page, resolution)
        await page.locator(BOX).first.click(timeout=8_000)
        # Characters first: the '@' picker is the "+" dialog and keeps the category that dialog last showed, under
        # which it offers no character (measured 2026-10-01, twice).
        mentioned[:] = [await attach(page, reference) for reference in entities]
        on_page = await chips_on(page)
        if len(on_page) != len(entities):
            raise LookupError(
                f"the prompt holds {len(on_page)} mention chips for {len(entities)} characters; refusing to generate"
            )
        # Then the images and the voices, in the order T1b measured with a quote and no refusal (2026-10-01).
        added[:] = [await attach_image(page, reference) for reference in images]
        added.extend([await attach_voice(page, reference) for reference in spoken])
        await check_bar(page, references)
        # The prompt goes in after the chips without a click, which could land on a chip (review F1, 2026-09-16).
        if not await page.evaluate(_CARET_END_JS, BOX):
            raise LookupError("could not put the caret at the end of the prompt box; refusing to generate")
        return {
            "model": model,
            "aspect": aspect,
            "seconds": duration,
            "duration_row": duration_row,
            **({"resolution": resolution} if resolution is not None else {}),
            **({"count": count} if count != 1 else {}),
            "chips": [*mentioned, *added],
        }

    async def verify(active: FlowSession) -> dict[str, Any]:
        """Read the chips, the prompt box and the ingredient bar again right before the price check, after typing and
        the confirm pass: the mention chips must be the ones attached, the box must read exactly their names, then
        the prompt, and the bar must hold every image and character asked for, nothing else, none refused."""
        page = active.page
        on_page = await chips_on(page)
        text = await page.evaluate(_BOX_TEXT_JS, BOX)
        wanted = sorted((chip["kind"], chip["id"]) for chip in mentioned)
        found = sorted((chip.get("kind", ""), chip.get("id", "")) for chip in on_page)
        unbound = [
            chip for chip in on_page if chip.get("kind") == "entity" and chip.get("entity") != chip.get("id")
        ]
        # Measured on L2 (2026-09-17): the box reads 'Untitled character stands in a sunny bakery ...'.
        expected = " ".join([*(chip.get("text", "") for chip in on_page), prompt])
        if found != wanted or unbound or _norm(text) != _norm(expected):
            raise LookupError(
                f"right before the click the prompt holds {found} and reads {text[:160]!r}, not {wanted} and "
                f"{expected[:160]!r}; refusing to spend"
            )
        bar = await check_bar(page, references, at_click=True)
        titles = {mention: reference.title for reference in images for mention in reference.mention_ids}
        by_name = {_norm(reference.title): reference for reference in spoken}
        return {
            "chips": [
                *(
                    {"kind": chip.get("kind", ""), "id": chip.get("id", ""), "text": chip.get("text", "")}
                    for chip in on_page
                ),
                *(
                    {"kind": "media", "id": chip["id"], "text": titles.get(chip["id"], "")}
                    for chip in bar
                    if chip["kind"] == "image"
                ),
                *(
                    {
                        "kind": "voice",
                        "id": by_name[_norm(chip["name"])].id,
                        "text": by_name[_norm(chip["name"])].title,
                    }
                    for chip in bar
                    if chip["kind"] == "voice"
                ),
            ],
            "prompt_text": text,
        }

    watch = body_check_for(references) if body_check_for is not None else SubmitBodyCheck(references)
    was = (await agent.set_mode(session, project_id, False)).get("was")
    try:
        result = await composer._submit(
            session,
            project_id,
            prompt=prompt,
            setup=setup,
            kind="character",
            job_id=job_id,
            # Unknown only on a dry run, which reports price_ok False next to the quote it read.
            expected_credits=expected if expected is not None else 0,
            out_dir=out_dir,
            aspect=aspect,
            mode="Ingredients",
            wait=wait,
            dry_run=dry_run,
            watch=watch,
            count=count,
            max_credits=max_credits,
            strict_output=True,
            verify=verify,
            click_box=False,
        )
    except BaseException as failed:
        # A restore that fails must never replace why the run failed, which may say credits were spent.
        if was:
            with contextlib.suppress(Exception):
                await agent.set_mode(session, project_id, True)
        # The money path raises for a clip that is paid and still rendering, and the request check below is never
        # reached: a request heard leaving without a reference is said here, after the advice the error opens with
        # (review of plan AL, 2026-10-02).
        heard = getattr(watch, "seen", None)
        if isinstance(failed, Exception) and not dry_run and heard and not heard.get("ok"):
            raise RuntimeError(
                f"{failed}; and Flow's submit request did not match what was asked: model keys "
                f"{heard.get('model_keys')}, missing {heard.get('missing')}, rpc {heard.get('rpcid')}, so the clip "
                "may not be what was asked"
            ) from failed
        raise
    if was:
        try:
            await agent.set_mode(session, project_id, True)
        except Exception as exc:  # noqa: BLE001
            result["agent_mode_restored"] = f"no: {type(exc).__name__}"
    # The request is already gone, so this cannot stop the charge; it stops a clip without its references from being
    # reported as done (review of plan U, F1: off 8 s gflow warns Flow drops them, at the same 15 credits).
    check = result.get("body_check") or {}
    if not dry_run and check and not check.get("ok"):
        spent = result.get("spent", (result.get("credits_before") or 0) - (result.get("credits_after") or 0))
        raise RuntimeError(
            f"{spent} credits were spent, but Flow's submit request did not match what was asked: model keys "
            f"{check.get('model_keys')}, missing {check.get('missing')}"
            f"{', voices sent ' + str(check['voices']) if 'voices' in check else ''}, rpc {check.get('rpcid')}. The clip "
            f"({result.get('path') or result.get('media_id')}) may not be what was asked. Do not run this job again "
            f"under a new job_id; its ledger row holds the body check. job {job_id}"
        )
    if not dry_run and result.get("media_id"):
        try:
            kept = await reader.recipe(session, project_id, result["media_id"])
        except Exception as exc:  # noqa: BLE001
            # The clip is paid for and the request carried every reference; a listing that cannot be read now is no
            # reason to call it failed.
            result["recipe_check"] = {"ok": None, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
            return result
        result["recipe_check"] = recipe_check(kept, references)
        if not result["recipe_check"]["ok"]:
            spent = result.get(
                "spent", (result.get("credits_before") or 0) - (result.get("credits_after") or 0)
            )
            raise RuntimeError(
                f"{spent} credits were spent and the request carried every reference, but the clip "
                f"{result['media_id']} kept another set: missing {result['recipe_check']['missing']}, not asked for "
                f"{result['recipe_check']['unexpected']} (clip_recipe reads it). Do not run this job again under a new "
                f"job_id. job {job_id}"
            )
    return result
