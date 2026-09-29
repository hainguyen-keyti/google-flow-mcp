"""A video from Flow's composer with any option it offers: gen_video (plan AB, 2026-09-29).

The options and their prices are not typed here: they live in flow_options.json, written from a $0 walk of the
composer's settings pane (every option clicked, the live price line read, Start never clicked). When Flow changes its
options the survey writes that file again, and this driver takes what the file says.

The money guard is Flow's own price line, read right before the click and held against the caller's max_credits
(DECISIONS 2026-09-29): a cell nobody has paid for yet runs when its live price fits the cap.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
) -> str:
    """Frames for text or a first and last frame, Ingredients for characters and images; never both at once."""
    frames = bool(start_frame or end_frame)
    ingredients = bool(characters or media_ids)
    if frames and ingredients:
        raise ValueError(
            "pass either frames or ingredients, not both: frames are start_frame and end_frame, ingredients are "
            "characters and media_ids, and the composer runs one mode per video"
        )
    if end_frame and not start_frame:
        raise ValueError("end_frame needs a start_frame: the composer fills Start first")
    return "Ingredients" if ingredients else "Frames"
