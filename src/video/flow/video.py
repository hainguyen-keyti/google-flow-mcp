"""A video from Flow's composer with any option it offers: gen_video (plan AB, 2026-09-29).

The options and their prices are not typed here: they live in flow_options.json, written from a $0 walk of the
composer's settings pane (every option clicked, the live price line read, Start never clicked). When Flow changes its
options the survey writes that file again, and this driver takes what the file says.

The money guard is Flow's own price line, read right before the click and held against the caller's max_credits
(DECISIONS 2026-09-29): a cell nobody has paid for yet runs when its live price fits the cap.
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
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import agent, composer, ingredients, parsers
from video.flow.ingredients import ALONE_WORDS, CAPS_MEASURED
from video.session import FlowSession

OPTIONS: dict[str, Any] = json.loads(
    (Path(__file__).with_name("flow_options.json")).read_text(encoding="utf-8")
)
VIDEO = OPTIONS["video"]
# A model whose pane shows no length or resolution row still makes one: every Veo clip measured here was 8 s at
# 1280x720 (2026-09-13 to 2026-09-29), so naming that default is accepted and anything else is refused.
FIXED = {"duration": 8, "resolution": "720p"}


def _model(model: str) -> dict[str, Any]:
    models = VIDEO["models"]
    if model not in models:
        raise ValueError(f"model must be one of {sorted(models)}, got {model!r}")
    return models[model]


def _cell(resolution: str | None, duration: int | None) -> str:
    return "" if resolution is None and duration is None else f"{resolution} {duration}s"


def defaults(model: str, resolution: str | None, duration: int | None) -> tuple[str | None, int | None]:
    """A model with a length row takes 720p and 8 s when none is named; the others keep what was passed."""
    if _model(model)["durations"]:
        return resolution or FIXED["resolution"], duration or FIXED["duration"]
    return resolution, duration


def check_settings(
    *, model: str, resolution: str | None, duration: int | None, count: int, aspect: str
) -> None:
    """Refuse, before any browser opens, a setting the composer did not offer when it was last surveyed."""
    entry = _model(model)
    if aspect not in VIDEO["aspects"]:
        raise ValueError(f"aspect must be one of {VIDEO['aspects']}, got {aspect!r}")
    if count not in VIDEO["counts"]:
        raise ValueError(f"count must be one of {VIDEO['counts']}, got {count!r}")
    if entry["durations"]:
        if duration not in entry["durations"]:
            raise ValueError(f"duration must be one of {entry['durations']} for {model}, got {duration!r}")
    elif duration not in (None, FIXED["duration"]):
        raise ValueError(f"{model} offers no length choice in the composer; it runs {FIXED['duration']} s")
    if entry["resolutions"]:
        if resolution not in entry["resolutions"]:
            raise ValueError(
                f"resolution must be one of {entry['resolutions']} for {model}, got {resolution!r}"
            )
    elif resolution not in (None, FIXED["resolution"]):
        raise ValueError(
            f"{model} offers no resolution choice in the composer; it runs {FIXED['resolution']}"
        )


def price(model: str, resolution: str | None, duration: int | None, count: int) -> int | None:
    """What the composer's price line showed for this cell on the survey; the live line decides at the click."""
    entry = _model(model)
    cell = _cell(resolution, duration) if entry["durations"] else ""
    x1 = entry["price_x1"].get(cell)
    return None if x1 is None else x1 * count


def mode_for(
    *,
    start_frame: str | None = None,
    end_frame: str | None = None,
    characters: list[str] | None = None,
    media_ids: list[str] | None = None,
    voices: list[str] | None = None,
) -> str:
    """Frames for text or a first and last frame, Ingredients for characters, images and voices; never both at once.
    A voice rides only beside an image or a character: Flow refuses it alone (measured 2026-10-01, out/al/t5.json)."""
    frames = bool(start_frame or end_frame)
    ingredients = bool(characters or media_ids or voices)
    if frames and ingredients:
        raise ValueError(
            "pass either frames or ingredients, not both: frames are start_frame and end_frame, ingredients are "
            "characters, media_ids and voices, and the composer runs one mode per video"
        )
    if end_frame and not start_frame:
        raise ValueError("end_frame needs a start_frame: the composer fills Start first")
    if voices and not (characters or media_ids):
        raise ValueError(
            f"a voice needs an image or a character beside it: Flow greys a voice alone out with {ALONE_WORDS!r} "
            f"(read off the composer on {CAPS_MEASURED})"
        )
    return "Ingredients" if ingredients else "Frames"


# The driver. Measured 2026-09-29 (/tmp/probe_frames.py, $0): Frames shows buttons Start, "Swap first and last frames",
# End; a slot's picker has a search box, and its tiles carry the image's listing url, so the tile is chosen by that
# url's tail rather than taken first; a filled slot renames itself "Image ingredient" on its side of Swap; clearing
# the prompt empties both slots.
SWAP = "Swap first and last frames"
FILLED = "image ingredient"
TAIL = 24
SLOT_WAIT_MS = 20_000
PICK_WAIT_MS = 30_000
PICK_POLL_MS = 2_000
ADD_TO_PROMPT = "Add to prompt"
# The picker lists its results and then shows the selected one large, last (measured 2026-09-30).
_PICKER_PREVIEW_JS = """() => { const all = [...document.querySelectorAll('.cdk-overlay-pane img, [role=dialog] img')];
  return all.length ? (all[all.length - 1].getAttribute('src') || '') : ''; }"""
# Since 2026-10-01 the picker's img src carries a size suffix after the token ('...RpDlNcxn=s512-rw'), while the
# listing url still ends on the bare token.
_RENDITION_SUFFIX = re.compile(r"=[A-Za-z0-9_-]*$")


def _bare_src(src: str | None) -> str:
    return _RENDITION_SUFFIX.sub("", src or "")


async def _add_previewed(page: Any, slot: str, ref: FrameRef) -> None:
    """With several results a click only previews the image and 'Add to prompt' pins it (measured 2026-09-30, a
    title three images share); it is pressed only once the preview shows the asked image's url."""
    add = page.locator(".cdk-overlay-pane button, [role=dialog] button").filter(has_text=ADD_TO_PROMPT)
    if await add.count() == 0:
        return
    shown = await page.evaluate(_PICKER_PREVIEW_JS)
    if not _bare_src(str(shown)).endswith(ref.tail):
        raise LookupError(
            f"the {slot} picker's preview shows another image than {ref.title!r} ({ref.id}); nothing was pinned"
        )
    await add.first.click(timeout=8_000)
    await page.wait_for_timeout(3_000)


@dataclass(frozen=True)
class FrameRef:
    id: str
    title: str
    tail: str
    workflows: frozenset[str]

    @property
    def mention_ids(self) -> frozenset[str]:
        return self.workflows | {self.id}


def _same_title(a: str | None, b: str) -> bool:
    return re.sub(r"\s+", " ", a or "").strip().casefold() == b.casefold()


def frame_references(
    media: list[dict[str, Any]], records: list[dict[str, Any]], start: str | None, end: str | None
) -> list[FrameRef]:
    """The start and end images, by media id. The title is only the picker's search text; the tile is told by its url,
    so images sharing a title (gen_i2i's own titles repeat, measured 2026-09-30) are refused only when their urls end
    alike."""
    by_id = {each["id"]: each for each in media}
    out = []
    for media_id in (start, end):
        if not media_id:
            continue
        found = by_id.get(media_id)
        if found is None:
            raise LookupError(f"{media_id} is not a media of this project; flow_media lists them")
        if found.get("kind") != "image":
            raise ValueError(f"{media_id} is a {found.get('kind')}; a frame must be an image")
        title = re.sub(r"\s+", " ", found.get("title") or "").strip()
        if not title:
            raise LookupError(f"image {media_id} has no title for the frame picker's search")
        mine = [r for r in records if r.get("id") == media_id]
        urls = [str(r.get("url") or "") for r in mine if r.get("url")]
        if not urls:
            raise LookupError(
                f"image {media_id} has no url in the listing, so its picker tile cannot be checked"
            )
        twins = {
            each["id"] for each in media if each["id"] != media_id and _same_title(each.get("title"), title)
        }
        if any(str(r.get("url") or "").endswith(urls[0][-TAIL:]) for r in records if r.get("id") in twins):
            raise LookupError(
                f"another image titled {title!r} ends with the same url, so the picker cannot tell them apart: "
                "rename the file (a unique name) and flow_upload it again, then pass the new media id"
            )
        out.append(
            FrameRef(
                media_id,
                title,
                urls[0][-TAIL:],
                frozenset(r["workflow_id"] for r in mine if r.get("workflow_id")),
            )
        )
    return out


def slots_filled(labels: list[str]) -> dict[str, bool]:
    """Read Start and End off the buttons either side of Swap."""
    names = [re.sub(r"\s+", " ", label or "").strip().casefold() for label in labels]
    if SWAP.casefold() not in names:
        return {"Start": False, "End": False}
    swap = names.index(SWAP.casefold())
    start = names[swap - 1] if swap > 0 else ""
    end = names[swap + 1] if swap + 1 < len(names) else ""
    return {"Start": start == FILLED, "End": end == FILLED}


async def pin_frame(page: Any, slot: str, ref: FrameRef) -> None:
    """Open the slot's picker, search the image's title, and click only a tile carrying that image's url."""
    button = (
        page.locator("flow-prompt-box, flow-base-prompt-box")
        .get_by_role("button", name=re.compile(rf"^{slot}$", re.IGNORECASE))
        .first
    )
    try:
        await button.wait_for(state="visible", timeout=SLOT_WAIT_MS)
    except PlaywrightTimeoutError as exc:
        raise LookupError(f"the composer shows no empty {slot} slot; is it in Frames mode?") from exc
    await button.click(timeout=8_000)
    await page.wait_for_timeout(3_000)
    search = page.locator(".cdk-overlay-pane input, [role=dialog] input").first
    if await search.count():
        await search.click(timeout=8_000)
        await page.keyboard.insert_text(ref.title)
        await page.wait_for_timeout(3_000)
    # An image uploaded a moment ago reaches the picker's results late (measured 2026-09-29), so look again for a while.
    waited = 0
    while True:
        tiles = page.locator(".cdk-overlay-pane img, [role=dialog] img")
        for index in range(await tiles.count()):
            tile = tiles.nth(index)
            if _bare_src(await tile.get_attribute("src")).endswith(ref.tail):
                await tile.click(timeout=8_000)
                await page.wait_for_timeout(3_000)
                await _add_previewed(page, slot, ref)
                return
        if waited >= PICK_WAIT_MS:
            break
        await page.wait_for_timeout(PICK_POLL_MS)
        waited += PICK_POLL_MS
    raise LookupError(f"the {slot} picker showed no tile of {ref.title!r} ({ref.id}); nothing was pinned")


KEY_LENGTH = re.compile(r"_(\d+)s(?:_|$)")


def _resolution_matches(resolution: str | None, keys: list[str]) -> bool:
    """Measured 2026-09-29 (jobs ab-1, ab-4): a 360p submit's key ends in _360p and a 720p one names no resolution."""
    if resolution is None:
        return True
    low = any(key.endswith("_360p") for key in keys)
    return low if resolution == "360p" else not low


OTHER_MODES = ("_i2v", "_r2v", "interpolation", "extension")


def _mode_matches(kind: str, keys: list[str]) -> bool:
    """t2v may send a key with no mode in it (gflow: `veo_3_1_lite_lower_priority`), so text is any key naming no
    other mode; i2v is an i2v or interpolation key; r2v an r2v key."""
    if not keys:
        return False
    if kind == "t2v":
        return not any(mode in key for key in keys for mode in OTHER_MODES)
    wanted = ("_i2v", "interpolation") if kind == "i2v" else (f"_{kind}",)
    return any(mode in key for key in keys for mode in wanted)


class VideoBodyCheck:
    """The submit request as it leaves: its model key must name the mode asked (t2v, i2v, r2v) and, when the key
    carries a length, that length; every image's and character's id must ride in the body, and the voice field must
    hold exactly the voices asked (plan AL, I4), read off the field itself since a prompt may name a voice."""

    def __init__(
        self, kind: str, references: list[Any], duration: int | None, resolution: str | None = None
    ) -> None:
        self.kind, self.references, self.duration, self.resolution = kind, references, duration, resolution
        self.voices = [r.id for r in references if getattr(r, "kind", "") == "voice"]
        self.seen: dict[str, Any] | None = None

    def on_request(self, request: Any) -> None:
        url = str(getattr(request, "url", "") or "")
        if self.seen is not None or "batchexecute" not in url:
            return
        try:
            body = unquote_plus(request.post_data or "")
        except Exception:  # noqa: BLE001
            body = ""
        # The start+end submit (nprQif) names itself only inside f.req (review of plan AB, B1).
        rpcids = parse_qs(urlsplit(url).query).get("rpcids", [""])[0].split(",") + [
            mc._body_rpcid(body) or ""
        ]
        submit = [rpcid for rpcid in rpcids if rpcid in mc.SUBMIT_RPCS]
        if not submit:
            return
        keys = sorted(set(mc.MODEL_KEY.findall(body)))
        lengths = sorted({int(m) for key in keys for m in KEY_LENGTH.findall(key)})
        sent = ingredients.request_voices(getattr(request, "post_data", "") or "")
        missing = [
            r.id
            for r in self.references
            if getattr(r, "kind", "") != "voice" and not any(m in body for m in r.mention_ids)
        ] + [voice for voice in self.voices if voice not in (sent or [])]
        voices_ok = sorted(sent) == sorted(self.voices) if sent is not None else not self.voices
        self.seen = {
            "rpcid": submit[0],
            "model_keys": keys,
            "lengths": lengths,
            "missing": missing,
            **({"voices": sent} if sent is not None else {}),
            "ok": bool(body)
            and _mode_matches(self.kind, keys)
            and not missing
            and voices_ok
            and (self.duration is None or not lengths or lengths == [self.duration])
            and _resolution_matches(self.resolution, keys),
        }

    def report(self) -> dict[str, Any]:
        if self.seen is not None:
            return self.seen
        return {"rpcid": None, "model_keys": [], "missing": [r.id for r in self.references], "ok": False}


async def generate(
    session: FlowSession,
    project_id: str,
    *,
    prompt: str,
    model: str = "omni-flash",
    aspect: str = "9:16",
    resolution: str | None = None,
    duration: int | None = None,
    count: int = 1,
    start_frame: str | None = None,
    end_frame: str | None = None,
    characters: list[str] | tuple[str, ...] = (),
    media_ids: list[str] | tuple[str, ...] = (),
    max_credits: int | None = None,
    job_id: str | None = None,
    out_dir: Path = Path("out"),
    dry_run: bool = False,
    wait: float = 480.0,
    voices: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """One gen_video run: every check that needs no browser first, then one of the two composer modes."""
    offers_length = bool(_model(model)["durations"])
    resolution, duration = defaults(model, resolution, duration)
    check_settings(model=model, resolution=resolution, duration=duration, count=count, aspect=aspect)
    mode = mode_for(
        start_frame=start_frame,
        end_frame=end_frame,
        characters=list(characters),
        media_ids=list(media_ids),
        voices=list(voices),
    )
    if not dry_run and max_credits is None:
        raise ValueError("max_credits is required for a real run: the most credits this call may spend")
    if not dry_run and not job_id:
        raise ValueError("job_id is required unless dry_run")
    length = duration if offers_length else None
    table = price(model, resolution if offers_length else None, length, count)

    if mode == "Ingredients":
        result = await ingredients.generate(
            session,
            project_id,
            prompt=prompt,
            characters=list(characters),
            media_ids=list(media_ids),
            voices=list(voices),
            model=model,
            aspect=aspect,
            job_id=job_id,
            out_dir=out_dir,
            dry_run=dry_run,
            wait=wait,
            duration=duration or FIXED["duration"],
            count=count,
            resolution=resolution if offers_length else None,
            max_credits=max_credits if max_credits is not None else table,
            table_credits=table or 0,
            body_check_for=lambda references: VideoBodyCheck(
                "r2v", references, length, resolution if offers_length else None
            ),
        )
        return {"mode": mode, "table_credits": table, **result}

    listing = await ingredients._listing(session, project_id)
    refs = frame_references(parsers.media(listing), parsers.records(listing), start_frame, end_frame)
    kind = "i2v" if refs else "t2v"
    slots = ["Start", "End"][: len(refs)]

    async def setup(active: FlowSession) -> dict[str, Any]:
        page = active.page
        await ingredients.apply_settings(page, model, aspect, [])
        if offers_length:
            row = await ingredients.pin_duration(page, duration)
            if row != "pinned":
                raise LookupError(
                    f"asked for {duration}s but the composer shows no {duration}s length to pin"
                )
            await ingredients.pin_resolution(page, resolution)
        for slot, ref in zip(slots, refs, strict=True):
            await pin_frame(page, slot, ref)
        filled = slots_filled(await page.evaluate(composer._COMPOSER_BUTTONS_JS))
        if any(not filled[slot] for slot in slots):
            raise LookupError(f"the frame slots read {filled} after pinning {slots}; refusing to generate")
        return {
            "model": model,
            "aspect": aspect,
            "seconds": duration if offers_length else FIXED["duration"],
            "resolution": resolution or FIXED["resolution"],
            "count": count,
            "frames": [
                {"slot": slot, "media_id": ref.id, "title": ref.title} for slot, ref in zip(slots, refs)
            ],
        }

    async def verify(active: FlowSession) -> dict[str, Any]:
        """Right before the price check: the slots still hold their images and the box reads exactly the prompt."""
        page = active.page
        filled = slots_filled(await page.evaluate(composer._COMPOSER_BUTTONS_JS))
        text = await page.evaluate(ingredients._BOX_TEXT_JS, ingredients.BOX)
        wanted = {slot: slot in slots for slot in ("Start", "End")}
        if filled != wanted or ingredients._norm(text) != ingredients._norm(prompt):
            raise LookupError(
                f"right before the click the slots read {filled} and the box {text[:160]!r}, not {wanted} and "
                f"{prompt[:160]!r}; refusing to spend"
            )
        return {"slots": filled, "prompt_text": text}

    check = VideoBodyCheck(kind, refs, length, resolution if offers_length else None)
    was = (await agent.set_mode(session, project_id, False)).get("was")
    try:
        result = await composer._submit(
            session,
            project_id,
            prompt=prompt,
            setup=setup,
            kind="video",
            job_id=job_id,
            expected_credits=table or 0,
            out_dir=out_dir,
            aspect=aspect,
            mode="Frames",
            wait=wait,
            dry_run=dry_run,
            watch=check,
            strict_output=True,
            verify=verify,
            count=count,
            max_credits=max_credits if max_credits is not None else table,
        )
    except BaseException:
        if was:
            with contextlib.suppress(Exception):
                await agent.set_mode(session, project_id, True)
        raise
    if was:
        try:
            await agent.set_mode(session, project_id, True)
        except Exception as exc:  # noqa: BLE001
            result["agent_mode_restored"] = f"no: {type(exc).__name__}"
    body = result.get("body_check") or {}
    if not dry_run and body and not body.get("ok"):
        spent = result.get("spent", (result.get("credits_before") or 0) - (result.get("credits_after") or 0))
        raise RuntimeError(
            f"{spent} credits were spent, but Flow's submit request did not match what was asked ({kind}, "
            f"{length or 'no'} s length, frames {[r.id for r in refs]}): model keys {body.get('model_keys')}, missing "
            f"{body.get('missing')}, rpc {body.get('rpcid')}. The clip ({result.get('path') or result.get('media_id')}) "
            f"may not be what was asked. Do not run this job again under a new job_id; its ledger row holds the body "
            f"check. job {job_id}"
        )
    return {"mode": mode, "table_credits": table, **result}
