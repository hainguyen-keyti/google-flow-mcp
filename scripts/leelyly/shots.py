"""Which picture plays under which line.

Two decisions written down here rather than left to taste:

1. **Nobody speaks on camera.** The voice is hers either way, but generated Vietnamese lip sync is the single
   most recognisable AI tell, and storytime over b-roll is how this genre is actually shot. It also means a
   line can be re-recorded for nothing without re-paying for a clip.
2. **Everything happens in her room.** The folder holds clean studio outfit sheets, and they look like a
   catalogue, which is exactly the "this is an ad" feeling the owner asked to avoid. The wardrobe shots in the
   bedroom carry the same clothes inside the story.
"""

from __future__ import annotations

from pathlib import Path

from timeline import Segment

ONSCREEN = "clip_onscreen"
IMAGES = Path("/Users/keyti/Sources/kt_tools/Character/LeeLyLy/images")
CLIPS = Path("out/leelyly/clips")

# Six frames were dropped on 2026-09-18 after looking at a contact sheet of the chosen stills: they read as
# lingerie selfies rather than as the story, which is the "this is an ad" register the owner asked to avoid.
STILL = {
    "desk": "a_cozy_indoor_bedroom_desk_scene_soft_warm_lighti_13_batch_3.png",
    "desk_night": "indoor_cozy_bedroom_desk_scene_warm_dim_evening_l_22_batch_2.png",
    "desk_selfie": "indoor_bedroom_desk_selfie_style_portrait_scene_a_23_batch_3.png",
    "room_night": "a_cozy_bedroom_room_interior_scene_shot_like_a_po_28_batch_8.png",
    "closet_sitting": "a_cozy_intimate_bedroom_closet_scene_softly_lit_1.png",
    "closet_warm": "indoor_bedroom_closet_scene_cozy_warm_lighting_s_4_batch_3.png",
    "closet_pastel": "indoor_bedroom_closet_scene_with_a_cozy_pastel_aes_22_batch_2.png",
    "closet_moody": "indoor_bedroom_closet_scene_in_a_moody_slightly_d_15_batch_5.png",
    "wardrobe": "a_cozy_indoor_bedroom_wardrobe_scene_soft_warm_li_6_batch_5.png",
    "standing_pink": "a_cozy_indoor_bedroom_scene_softly_lit_and_moody_25_batch_5.png",
    "ruffle_cream": "a_cozy_indoor_bedroom_scene_with_warm_moody_light_22_batch_2.png",
    "vanity": "indoor_bedroom_vanity_scene_cozy_softly_lit_aesth_2_batch_1.png",
    "vanity_back": "a_cozy_intimate_bedroom_vanity_scene_with_a_young_28_batch_8.png",
    "bed_portrait": "indoor_bedroom_portrait_scene_softly_lit_and_cozy_7_batch_6.png",
    "bed_candid": "indoor_bedroom_scene_cozy_candid_portrait_photogr_23_batch_3.png",
    "room_moody": "indoor_bedroom_scene_moody_warm_ambient_lighting_28_batch_8.png",
    "room_soft": "indoor_bedroom_scene_soft_warm_ambient_lighting_8_batch_7.png",
    "room_full": "indoor_bedroom_scene_softly_lit_full_body_portra_3_batch_2.png",
    "sitting_green": "a_cozy_indoor_bedroom_portrait_scene_softly_lit_27_batch_7.png",
    "portrait": "02_portrait_natural.png",
}


def still(key: str) -> str:
    return str(IMAGES / STILL[key])


def clip(name: str) -> str:
    return str(CLIPS / f"{name}.mp4")


# act 1 is the hook, act 2 her and the room, act 3 the love, act 4 the fading, act 5 what she keeps.
# `line` names the narration file that starts under that segment; a line longer than its segment simply runs
# on under the next one, which is how the picture cuts faster than the voice.
SEGMENTS: list[Segment] = [
    Segment("s01", "clip", clip("p1"), 0.4, 2.4, 0.0, 1, "L01", "hands lift the white lace dress"),
    Segment("s02", "clip", clip("p1"), 3.2, 2.8, 0.0, 1, None, "her face behind the dress, soft focus"),
    Segment("s03", "still", still("room_moody"), 0.0, 2.0, 0.0, 2, "L02", "the room, lamps on"),
    Segment("s04", "clip", clip("p2"), 0.5, 3.0, 0.0, 2, "L03", "she comes in and drops the bag"),
    Segment("s05", "still", still("room_night"), 0.0, 2.6, 0.0, 2, None, "city lights at the window"),
    Segment("s06", "still", still("desk_night"), 0.0, 2.4, 0.0, 2, "L04", "the desk at night"),
    Segment("s07", "still", still("desk_selfie"), 0.0, 2.2, 0.0, 2, None, "packing orders"),
    Segment("s08", "clip", clip("p3"), 0.6, 3.2, 0.0, 2, "L05", "her hand along the hangers"),
    Segment("s09", "still", still("wardrobe"), 0.0, 2.2, 0.0, 2, None, "the open wardrobe"),
    # act 3: the love story
    Segment("s10", "still", still("closet_warm"), 0.0, 2.4, 0.5, 3, "L06", "the white dress folded"),
    Segment("s11", "still", still("desk_selfie"), 0.0, 2.2, 0.0, 3, "L07", "a message on the phone"),
    Segment("s12", "clip", clip("c4"), 0.5, 2.6, 0.0, 3, None, "she reads it and smiles"),
    Segment("s13", "still", still("bed_candid"), 0.0, 2.4, 0.0, 3, "L08", "lying on the bed, phone in hand"),
    Segment("s14", "clip", clip("c4"), 3.6, 2.4, 0.0, 3, None, "she laughs"),
    Segment(
        "s15",
        "still",
        still("bed_portrait"),
        0.0,
        2.0,
        0.0,
        3,
        "L09",
        "quiet after laughing",
        pause_after=0.4,
    ),
    Segment("s16", "clip", clip("c5"), 0.4, 3.0, 0.0, 3, "L10", "mirror, she fixes the floral top"),
    Segment("s17", "still", still("standing_pink"), 0.0, 2.2, 0.0, 3, None, "mirror selfie"),
    Segment(
        "s18", "clip", clip("c5"), 4.2, 2.6, 0.0, 3, "L11", "she turns and smiles for real", pause_after=0.4
    ),
    Segment("s18b", "still", still("vanity"), 0.0, 2.2, 0.0, 3, None, "she looks down, still smiling"),
    Segment("s19", "still", still("closet_pastel"), 0.0, 1.8, 0.0, 3, "L12", "outfit one"),
    Segment("s20", "still", still("closet_sitting"), 0.0, 1.6, 0.0, 3, None, "outfit two"),
    Segment("s21", "still", still("sitting_green"), 0.0, 1.6, 0.0, 3, "L13", "outfit three"),
    Segment("s22", "still", still("vanity"), 0.0, 1.8, 0.0, 3, None, "getting ready"),
    # act 4: it fades
    Segment("s23", "clip", clip("c6"), 0.5, 3.0, 0.5, 4, "L14", "the black dress on the rail"),
    Segment("s24", "still", still("closet_moody"), 0.0, 2.4, 0.0, 4, None, "the closet, darker now"),
    Segment(
        "s25", "clip", clip("c7"), 0.4, 2.8, 0.0, 4, "L15", "phone face down on the desk", pause_after=1.4
    ),
    Segment("s26", "still", still("room_moody"), 0.0, 2.6, 0.0, 4, None, "the room with one lamp"),
    Segment("s27", "clip", clip("c8"), 0.5, 3.0, 0.0, 4, "L16", "she photographs herself, sends nothing"),
    Segment("s28", "still", still("vanity_back"), 0.0, 2.4, 0.0, 4, None, "vanity, half lit"),
    Segment(
        "s29",
        "clip",
        clip("c9"),
        0.4,
        3.2,
        0.0,
        4,
        "L17",
        "sitting on the floor facing the wardrobe",
    ),
    Segment("s30", "still", still("ruffle_cream"), 0.0, 2.2, 0.0, 4, None, "the wardrobe from the floor"),
    # act 5: what she keeps
    Segment("s31", "clip", clip("c10"), 0.5, 2.8, 0.5, 5, "L18", "her hand touching each hanger"),
    Segment("s32", "still", still("closet_warm"), 0.0, 2.0, 0.0, 5, None, "the clothes, warm again"),
    Segment("s33", "still", still("closet_pastel"), 0.0, 1.6, 0.0, 5, "L19", "memory one"),
    Segment("s34", "still", still("standing_pink"), 0.0, 1.6, 0.0, 5, None, "memory two"),
    Segment("s35", "still", still("room_full"), 0.0, 1.8, 0.0, 5, None, "memory three"),
    Segment(
        "s36",
        "clip",
        clip("c11"),
        0.5,
        3.0,
        0.0,
        5,
        "L20",
        "she opens the window in a new outfit",
    ),
    Segment("s37", "still", still("room_soft"), 0.0, 2.2, 0.0, 5, None, "light across the bed"),
    Segment(
        "s38",
        "clip",
        clip("c12"),
        0.6,
        3.0,
        0.0,
        5,
        "L21",
        "the white dress again, she looks at it",
        pause_after=1.4,
    ),
    Segment("s39", "still", still("portrait"), 0.0, 2.4, 0.0, 5, None, "her, close, end frame"),
]


# Clips a human looked at and turned down. p2 and p3 were paid for and are on disk, but Flow built a different
# room and a different wardrobe from the ones in her photographs, which is the continuity failure the owner
# rejected a whole video for on 2026-09-13. A rejected clip is replaced by the photograph it should have been
# anchored to, so the cut keeps its shape instead of losing a beat.
REJECTED: dict[str, str] = {
    "p2": "room_night",
    "p3": "wardrobe",
}


def segments_for(*, pilot: bool) -> list[Segment]:
    chosen = [segment for segment in SEGMENTS if segment.act in (1, 2)] if pilot else list(SEGMENTS)
    out = []
    for segment in chosen:
        name = Path(segment.source).stem
        if segment.kind != "still" and name in REJECTED:
            out.append(segment._replace(kind="still", source=still(REJECTED[name]), start_in_source=0.0))
        else:
            out.append(segment)
    return out


def sources(*, pilot: bool) -> dict[str, list[str]]:
    chosen = segments_for(pilot=pilot)
    return {
        "stills": sorted({s.source for s in chosen if s.kind == "still"}),
        "clips": sorted({s.source for s in chosen if s.kind != "still"}),
    }


# The room and the person are locked by the character chip and by character_design.md; a prompt only ever
# changes camera, action and light (the bible's own rule). Veo writes its own audio, so the prompts ask for
# room tone and nothing else: her voice is laid on in the edit, where a line costs nothing to redo.
# Measured 2026-09-18 on the first pilot clip: "no music" inside a long sentence is not enough, Flow scored it
# anyway. The audio ask is now its own block at the end, says what the track MAY contain, and the edit mutes
# generated audio by default (AMBIENT_OK) so a scored clip can never reach the film.
LOOK = (
    "Shot on a phone held in the hand, slight natural wobble, warm practical lamps, cozy lived-in Vietnamese "
    "bedroom with a photo wall, open wardrobe and pale patterned bedding. Realistic skin texture, no beauty "
    "filter, no text, no captions, no subtitles. "
    "AUDIO: diegetic room sound only, recorded by the phone: cloth rustling, hangers sliding, her quiet "
    "breathing, a distant street outside. NO MUSIC. No score, no soundtrack, no instruments, no humming, no "
    "singing, no melody of any kind. No speech, no dialogue, no voice over."
)

# Clips whose own audio the edit is allowed to keep under her voice. Empty until a human has listened to that
# clip and confirmed there is no music in it: the owner asked for sound and voice, not a score.
AMBIENT_OK: set[str] = set()

PROMPTS: dict[str, str] = {
    "p1": (
        "Close on her hands lifting a white lace dress off a wooden hanger, the fabric filling the frame; her "
        "face is soft and out of focus behind it. She brushes the lace with her thumb, then lowers it slowly. "
        "The camera stays close and drifts a little. " + LOOK
    ),
    "p2": (
        "She comes in through the dark wooden door, drops a tote bag on the bed and sits down heavily on the "
        "edge, shoulders falling as she breathes out. Wide shot from the corner of the room, the window with "
        "night city light behind her. The camera holds still. " + LOOK
    ),
    "p3": (
        "Her hand runs along a rail of hanging clothes in the open wardrobe, pushing the hangers one at a time. "
        "She looks back over her shoulder and gives a small, tired smile. The camera rises slowly from low. "
        + LOOK
    ),
    "c4": (
        "She lies on her stomach on the bed looking at her phone, kicks her feet once and laughs at something "
        "on the screen. High angle looking down at her. The camera drifts slightly. " + LOOK
    ),
    "c5": (
        "She stands in front of the full-length mirror in a small floral top, tugs the hem straight, turns "
        "sideways to check, then smiles at herself for real. Camera behind her shoulder, reflection in frame. "
        + LOOK
    ),
    "c6": (
        "A black lace dress hangs alone on the rail; her hand rests on it, then lets go and falls to her side. "
        "Close, static camera, the wardrobe dim behind. " + LOOK
    ),
    "c7": (
        "A phone lies face down on the dark desk beside a lamp. She reaches in, turns it over, looks, and puts "
        "it back face down. Close on the desk, the lamp the only light. " + LOOK
    ),
    "c8": (
        "She takes a photo of herself in the mirror, looks at the picture, and lowers the phone without "
        "sending it. Medium shot, mirror reflection, evening light. " + LOOK
    ),
    "c9": (
        "She sits on the floor with her back against the bed, facing the open wardrobe, knees up, looking at "
        "the clothes without moving. Low camera at floor level, wide. " + LOOK
    ),
    "c10": (
        "Her hand moves along the hangers again, slower now, stopping on one, touching the fabric between two "
        "fingers. Very close on the hand and the cloth. " + LOOK
    ),
    "c11": (
        "In a fresh outfit she pushes the window open, morning light coming in across her face, and looks out "
        "for a moment. Medium shot, the camera moves with her a little. " + LOOK
    ),
    "c12": (
        "The white lace dress hangs on the wardrobe door. She looks at it, reaches out and straightens it, then "
        "turns away out of frame. Medium close, warm light. " + LOOK
    ),
}


# Measured 2026-09-18 on pilot clip p2: the character chip locks the FACE, not the room. Asked for "her small
# warm bedroom" in words, Flow built a different room with different bedding, curtains and wardrobe, which is
# the exact failure the owner rejected a video for on 2026-09-13. Every generated shot from here carries a
# photograph of her actual room as an Ingredients reference, so the room is shown rather than described.
REFS: dict[str, str] = {
    "c4": "bed_candid",
    "c5": "standing_pink",
    "c6": "closet_moody",
    "c7": "desk_night",
    "c8": "vanity",
    "c9": "wardrobe",
    "c10": "closet_warm",
    "c11": "room_soft",
    "c12": "wardrobe",
}
