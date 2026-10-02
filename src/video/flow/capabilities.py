"""What Flow offers and what it costs, as data (plan AO, G8).

Nothing is measured here and no number is typed here a second time: each is read from the file or the constant the
tools themselves refuse and charge by (`flow_options.json` through `video`, the caps in `ingredients`, the editor's
measured prices in `clips`, gflow's own choices for the image tools), so this answer and what a tool does cannot
drift apart. It opens no browser: the live price of one cell is `gen_video(dry_run=true)`, and the surveyed file is
refreshed by `video flow survey`.
"""

from __future__ import annotations

from typing import Any

from gflow_cli import cli_image
from gflow_cli.api.video import VideoModel, reference_cap_for

from video.flow import clips, ingredients
from video.flow import video as video_mod

NOTE = (
    "What this repo surveyed and measured on its own account; it is not read live. The video table and the image "
    "composer's table carry the date of the survey, and the caps the date they were read off the composer. "
    "gen_character's and the editor's prices were read off the balance on both sides of real runs, except a row "
    "marked measured false, which is Flow's own price table and was never paid here. The live price of one cell is "
    "gen_video with dry_run=true, read off Flow's price line, and a paid call is refused when that line is over its "
    "max_credits. The repo's owner refreshes the table with `video flow survey`."
)
# What the two cap numbers do not say by themselves (ingredients.resolve, read off the composer with the caps).
CAPS_NOTE = (
    "image_ingredients counts characters and images together: a character takes one of the image places, and a "
    "character that has a voice takes one of the voice places as well."
)
# gen_character's models whose price was never paid on this account; the tool's own description says the same.
CHARACTER_UNMEASURED = ("veo-fast",)


def _video_model(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    resolutions = list(entry["resolutions"]) or [video_mod.FIXED["resolution"]]
    seconds = list(entry["durations"]) or [video_mod.FIXED["duration"]]
    chooses = bool(entry["durations"])
    return {
        "label": entry["label"],
        "modes": list(entry["modes"]),
        "resolutions": resolutions,
        "seconds": seconds,
        # False: the composer shows no length or resolution row for this model, and it made the one cell below.
        "chooses_length": chooses,
        "credits_x1": [
            {
                "resolution": resolution,
                "seconds": length,
                "credits": video_mod.price(name, *((resolution, length) if chooses else (None, None)), 1),
            }
            for resolution in resolutions
            for length in seconds
        ],
        "image_ingredients": reference_cap_for(VideoModel.from_cli(name)),
        "voice_ingredients": ingredients.VOICE_CAPS.get(name),
    }


def _image() -> dict[str, Any]:
    """gen_t2i and gen_i2i hand their model, aspect and count to gflow, so what they take is gflow's own choices;
    Flow's composer names its models otherwise, and the survey read one price line for the model it then held."""
    params = {param.name: param for param in cli_image.image.commands["t2i"].params}
    surveyed = video_mod.OPTIONS["image"]
    default = params["model"].default
    return {
        "tools": ["gen_t2i", "gen_i2i"],
        "models": list(params["model"].type.choices),
        "default_model": default,
        "aspects": list(params["aspect"].type.choices),
        "counts": list(range(params["count"].type.min, params["count"].type.max + 1)),
        "credits_note": (
            f"{default}, the default, measured 0 credits and draws on a daily image quota; no other model was paid "
            "for here, and the composer's one price line below was read for whichever model it then held"
        ),
        "composer": {
            "measured": video_mod.OPTIONS["measured"],
            "models": list(surveyed["models"]),
            "aspects": list(surveyed["aspects"]),
            "counts": list(surveyed["counts"]),
            "price_line_x1": surveyed["price_x1"],
        },
    }


def capabilities() -> dict[str, Any]:
    video = video_mod.VIDEO
    return {
        "note": NOTE,
        "video": {
            "tools": ["gen_video", "job_submit"],
            "measured": video_mod.OPTIONS["measured"],
            "source": video_mod.OPTIONS["source"],
            "modes": list(video["modes"]),
            "aspects": list(video["aspects"]),
            "counts": list(video["counts"]),
            "models": {name: _video_model(name, entry) for name, entry in video["models"].items()},
            "caps_measured": ingredients.CAPS_MEASURED,
            "caps_note": CAPS_NOTE,
        },
        "character_video": {
            "tools": ["gen_character"],
            "basis": "the balance on both sides of real runs; measured false is Flow's own price table, never paid",
            "models": {
                name: {
                    "measured": name not in CHARACTER_UNMEASURED,
                    "credits_x1": [
                        {"seconds": length, "credits": ingredients.price_for(name, length)}
                        for length in lengths
                    ],
                }
                for name, lengths in ingredients.LENGTHS.items()
            },
        },
        "image": _image(),
        "editor": {
            "basis": "the balance on both sides of real calls; the editor publishes no price before the click",
            "credits": {
                "clip_extend": clips.MEASURED_PRICE["extend"],
                "clip_edit": clips.MEASURED_PRICE["edit"],
                "agent_send": clips.MEASURED_PRICE["agent"],
            },
            "notes": {"agent_send": clips._WHY["agent"]},
        },
    }
