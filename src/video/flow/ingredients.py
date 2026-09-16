"""Characters and project images put into the composer prompt with Flow's @ mention, and a video generated from them.

gflow drives the same gesture but still refuses to generate with a character on this host (`_unported_form` in
migrated_composer.py, v0.76.0), so the repo drives it and spends through the composer's one money path.

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

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlsplit

from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel, reference_cap_for
from gflow_cli.errors import UiSelectorDriftError

from video.flow import agent, composer, parsers
from video.flow.reader import capture, one
from video.session import PROJECT_READY, FlowSession

SECONDS = 8
PRICES = {"omni-flash": 12, "veo-lite": 10, "veo-fast": 20}
ASPECTS = {"9:16": Aspect.PORTRAIT, "16:9": Aspect.LANDSCAPE}
LABELS = {"entity": "Character", "media": "Image"}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
BOX = "flow-prompt-box [contenteditable='true']"
OPTION = "button.asset-item[role=option]"
CHIP = "flow-prompt-box .mention-chip"
RADIO = ".cdk-overlay-pane [role=radio]"
OPTION_WAIT_MS = 12_000
CHIP_WAIT_MS = 5_000
RADIO_WAIT_MS = 10_000

_OPTIONS_JS = """(sel) => [...document.querySelectorAll(sel)].map(o => ({
  title: ((o.querySelector('.asset-title') || {}).textContent || '').trim(),
  kind: ((o.querySelector('.type-subtitle') || {}).textContent || '').trim(),
  visible: !!(o.offsetWidth || o.offsetHeight),
}))"""

_CHIPS_JS = """(sel) => [...document.querySelectorAll(sel)].map(c => ({
  kind: c.getAttribute('data-reference-type') || '',
  id: c.getAttribute('data-mention-id') || '',
  entity: c.getAttribute('data-entity-id') || '',
  text: (c.textContent || '').trim(),
}))"""


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


@dataclass(frozen=True)
class Reference:
    kind: str
    id: str
    title: str
    mention_ids: frozenset[str]

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
) -> list[Reference]:
    """Name every requested character and image from one listing, refusing anything the picker cannot single out."""
    wanted = [*characters, *media_ids]
    if not characters:
        raise ValueError("at least one character entity id is required")
    if len(set(wanted)) != len(wanted):
        raise ValueError(f"each character and image may be named once, got {wanted}")
    cap = reference_cap_for(VideoModel.from_cli(model))
    if len(wanted) > cap:
        raise ValueError(f"{model} takes at most {cap} references, got {len(wanted)}")

    references: list[Reference] = []
    by_entity = {each["entity_id"]: each for each in listed_characters}
    names = [_norm(each.get("name")) for each in listed_characters]
    for entity in characters:
        found = by_entity.get(entity)
        if found is None:
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
    titles = [_norm(each.get("title")) for each in media]
    for media_id in media_ids:
        found = by_media.get(media_id)
        if found is None:
            raise LookupError(f"{media_id} is not a media of this project; flow_media lists them")
        if found.get("kind") != "image":
            raise ValueError(f"{media_id} is a {found.get('kind')}; only images are measured as references")
        title = found.get("title") or ""
        if not _norm(title):
            raise LookupError(f"image {media_id} has no title to mention")
        if titles.count(_norm(title)) > 1:
            raise LookupError(
                f"{titles.count(_norm(title))} images are titled {title!r}, which the picker cannot tell apart"
            )
        workflows = frozenset(
            each["workflow_id"] for each in records if each.get("id") == media_id and each.get("workflow_id")
        )
        if not workflows:
            raise LookupError(
                f"image {media_id} has no workflow id in the listing, so its chip cannot be checked"
            )
        references.append(Reference("media", media_id, title, workflows))

    for reference in references:
        if not reference.query:
            raise LookupError(f"{reference.title!r} leaves nothing to type after '@'")
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


async def attach(page: Any, reference: Reference) -> dict[str, str]:
    """Mention one reference by clicking its one option, then prove the chip that landed is that reference.

    Never Enter: with no picker open, Enter in the prompt box may send the prompt (gflow types prompts with
    insert_text for that reason), and a submit here would spend with no ledger row.
    """
    label = LABELS[reference.kind]
    before = await chips_on(page)
    query = reference.query
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2_200)
    await page.keyboard.type(query, delay=100)
    options = await _options(page)
    matches = [
        index
        for index, option in enumerate(options)
        if option.get("visible")
        and _norm(option.get("title")) == _norm(reference.title)
        and _norm(option.get("kind")) == _norm(label)
    ]
    if len(matches) != 1:
        for _ in range(len(query) + 1):
            await page.keyboard.press("Backspace")
        offered = ", ".join(f"{o.get('title')} ({o.get('kind')})" for o in options[:8]) or "nothing"
        raise LookupError(
            f"{len(matches)} picker options are the {label.lower()} {reference.title!r} (offered: {offered}); "
            "not guessing"
        )
    await page.locator(OPTION).nth(matches[0]).click(timeout=8_000)

    after = await chips_on(page)
    waited = 0
    while len(after) <= len(before) and waited < CHIP_WAIT_MS:
        await page.wait_for_timeout(500)
        waited += 500
        after = await chips_on(page)
    added = list(after)
    for chip in before:
        if chip in added:
            added.remove(chip)
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


async def apply_settings(page: Any, model: str, aspect: str, references: list[Reference]) -> None:
    """References mode, model and aspect through gflow's own radios, which read every choice back.

    Measured 2026-09-16 (L2 run 1, then a $0 diagnosis): right after the composer's own settings pass the pane was
    closed, yet gflow's first open of it showed no option groups while a second open worked, the toggle the
    composer's `_open_settings` already retries once. Opening the pane spends nothing, so that one failure gets one
    more open.
    """
    entities = [reference for reference in references if reference.kind == "entity"]
    request = GenerateVideoRequest(
        prompt="character references",
        mode=Mode.R2V,
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


async def pin_duration(page: Any) -> str:
    """Check the 8 s radio once it renders; a model whose pane shows no duration row answers 'absent'."""
    await composer._open_settings(page, "duration")
    try:
        radio = page.locator(RADIO).filter(has_text=re.compile(rf"^\s*{SECONDS}s\s*$"))
        waited = 0
        while await radio.count() == 0 and waited < RADIO_WAIT_MS:
            await page.wait_for_timeout(500)
            waited += 500
        count = await radio.count()
        if count == 0:
            return "absent"
        if count > 1:
            raise LookupError(f"{count} duration radios read {SECONDS}s; not guessing")
        if await radio.first.get_attribute("aria-checked") != "true":
            await radio.first.click(timeout=4_000)
            await page.wait_for_timeout(1_200)
        if await radio.first.get_attribute("aria-checked") != "true":
            raise LookupError(f"the {SECONDS}s duration radio did not stay checked")
        return "pinned"
    finally:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(1_000)


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
) -> dict[str, Any]:
    """One 8 s video from characters and project images, or with dry_run the quote and the chips, never a click."""
    if model not in PRICES:
        raise ValueError(f"model must be one of {sorted(PRICES)}, got {model!r}")
    if aspect not in ASPECTS:
        raise ValueError(f"aspect must be one of {sorted(ASPECTS)}, got {aspect!r}")
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
    )

    async def setup(active: FlowSession) -> dict[str, Any]:
        page = active.page
        await apply_settings(page, model, aspect, references)
        duration = await pin_duration(page)
        await page.locator(BOX).first.click(timeout=8_000)
        attached = [await attach(page, reference) for reference in references]
        on_page = await chips_on(page)
        if len(on_page) != len(references):
            raise LookupError(
                f"the prompt holds {len(on_page)} chips for {len(references)} references; refusing to generate"
            )
        return {
            "model": model,
            "aspect": aspect,
            "seconds": SECONDS,
            "duration_row": duration,
            "chips": attached,
        }

    found = await agent.set_mode(session, project_id, False)
    try:
        return await composer._submit(
            session,
            project_id,
            prompt=prompt,
            setup=setup,
            kind="character",
            job_id=job_id,
            expected_credits=PRICES[model],
            out_dir=out_dir,
            aspect=aspect,
            mode="Ingredients",
            wait=wait,
            dry_run=dry_run,
            watch=SubmitBodyCheck(references),
            strict_output=True,
        )
    finally:
        if found.get("was"):
            await agent.set_mode(session, project_id, True)
