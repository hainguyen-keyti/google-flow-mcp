"""What Flow offers and what it costs, as data (plan AO, G8).

Nothing is measured here and nothing is typed here a second time: every number is read from the file or the constant
the tools themselves refuse and charge by (`flow_options.json` through `video`, the caps in `ingredients`, the
editor's measured prices in `clips`), so this answer and what a tool does cannot drift apart. It opens no browser:
the live price of one cell is `gen_video(dry_run=true)`, and the surveyed file is refreshed by `video flow survey`.
"""

from __future__ import annotations

from typing import Any

from gflow_cli.api.video import VideoModel, reference_cap_for

from video.flow import clips, ingredients
from video.flow import video as video_mod

NOTE = (
    "What this repo measured on its own account, each part with its date; it is not read live. The live price of "
    "one cell is gen_video with dry_run=true, read off Flow's price line, and a paid call is refused when that line "
    "is over its max_credits. The repo's owner refreshes the table with `video flow survey`."
)


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


def capabilities() -> dict[str, Any]:
    video, image = video_mod.VIDEO, video_mod.OPTIONS["image"]
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
            # A character that has a voice takes one of the voice places, and a character takes an image place.
            "caps_measured": ingredients.CAPS_MEASURED,
        },
        "character_video": {
            "tools": ["gen_character"],
            "models": {
                name: {
                    "credits_x1": [
                        {"seconds": length, "credits": ingredients.price_for(name, length)}
                        for length in lengths
                    ]
                }
                for name, lengths in ingredients.LENGTHS.items()
            },
        },
        "image": {
            "tools": ["gen_t2i", "gen_i2i"],
            "measured": video_mod.OPTIONS["measured"],
            "models": list(image["models"]),
            "aspects": list(image["aspects"]),
            "counts": list(image["counts"]),
            "credits_x1": image["price_x1"],
        },
        "editor": {
            # Read off the balance on both sides of real calls; the editor publishes no price before the click.
            "credits": {
                "clip_extend": clips.MEASURED_PRICE["extend"],
                "clip_edit": clips.MEASURED_PRICE["edit"],
                "agent_send": clips.MEASURED_PRICE["agent"],
            },
        },
    }
