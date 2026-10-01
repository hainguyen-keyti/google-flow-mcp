"""What a clip was made from, read back off the project listing.

Shapes measured 2026-10-01 on the 169 generation records of project 102445f4 (plan AL, T1): the recipe sits at
record[5][6][1] as [model key, family, [mode], ...], then the input images as [None, role, workflow id, crop] with
role 1 the start frame, 2 the end frame and 4 a reference, the source clip of an edit, an upscale or an extend at
[2][0][3], the voices at [7] (a custom voice's workflow id, or a preset's lowercase name) and the characters at [8].
"""

import pytest

from video.flow import parsers, reader

PROJECT = "102445f4-c824-429f-ad48-33c7ec696a41"
IMAGE_WF, IMAGE_MEDIA = "1ca0ae90-e3a6-4d62-905e-bc041f228f86", "de297e39-b0b6-4210-a497-77ebdffd5087"
END_WF, END_MEDIA = "7a3a055b-4221-47d0-abd0-a3b72b1d8a2b", "2f34be19-eeb1-4583-978c-1fa2c4578ec6"
VOICE_WF, VOICE_MEDIA = "17b8dafb-7c04-441e-9239-36feaf27eb65", "9712ce82-97a4-4d2c-9e22-9ad1188c6654"
ENTITY = "adf5f119-a3b0-47ab-9948-364e858fc5ff"
CROP = [0.0003, None, 0.9996, 1]


def record(workflow, media, arm, *, version="CAE", at=1790850000, prompt="a line"):
    """A video generation record with the recipe arm in its measured place."""
    return [
        workflow,
        PROJECT,
        media,
        version,
        None,
        [
            [at, 0],
            prompt,
            None,
            None,
            None,
            "URL",
            [None, arm, [[None, None, [[[prompt]]]]], None, 1],
            None,
            [3],
            1,
            "URL",
            None,
            None,
            2000000,
        ],
        None,
        [
            [
                None,
                1,
                None,
                None,
                None,
                None,
                None,
                prompt,
                "URL",
                None,
                None,
                None,
                arm[0][0],
                "",
                None,
                False,
                1,
            ]
        ],
    ]


def image_record(workflow, media):
    return [
        workflow,
        PROJECT,
        media,
        "CAE",
        None,
        [[1790800000, 0], None, None, None, None, "URL", None],
        None,
    ]


def descriptor(media, title, workflow, at=1790800000):
    return [media, None, None, [title, [at, 0], None, None, workflow, None, [at, 5]], PROJECT]


TWO_VOICES = record(
    "ecdf288e-ea9b-43c8-9348-407e7c6bb754",
    "707d4739-8cb5-4b10-bd4e-35290605aa90",
    [
        ["abra_r2v_8s", 3, [5], None, 1, 1],
        [[None, 4, IMAGE_WF]],
        None,
        None,
        None,
        None,
        None,
        [[VOICE_WF], ["achird"]],
    ],
)
FIRST_AND_LAST = record(
    "1149e511-403e-4ed1-8f59-c3d1fe1d1185",
    "05f516e3-fc67-4f9e-bfb4-69847be22606",
    [
        ["veo_3_1_interpolation_lite", 2, [2], None, 1, 1],
        [[None, 1, IMAGE_WF, CROP], [None, 2, END_WF, CROP]],
    ],
)
START_ONLY = record(
    "5af5afa6-34d0-4049-81c0-45642ea23f89",
    "f90caadc-937e-482f-bc5c-449d7d8665b8",
    [["veo_3_1_i2v_lite", 2, [1], None, 1, 1], [[None, 1, IMAGE_WF, [None, None, 1, 1]]]],
)
WITH_CHARACTER = record(
    "58fbf6ae-3203-4f6e-ac9f-61b71bb45d7a",
    "c1147639-b494-4e0b-aae1-dc4de4214c8a",
    [
        ["abra_r2v_8s", 3, [5], None, 1, 1],
        [[None, 4, IMAGE_WF]],
        None,
        None,
        None,
        None,
        None,
        None,
        [[ENTITY]],
    ],
)
EDIT = record(
    "5e74044b-6fba-4fbc-95ab-464a19e31f58",
    "a6d376e3-f5d1-47d8-9654-89dc634399a8",
    [["abra_edit", 4, [10], None, 1, 1], None, [[None, None, None, "36e923f0-7bd7-4366-af95-c258e0a59089"]]],
    version="CAI",
    at=1790846165,
)
EDITED_SOURCE = record(
    "36e923f0-7bd7-4366-af95-c258e0a59089",
    "a6d376e3-f5d1-47d8-9654-89dc634399a8",
    [["abra_i2v_6s", 2, [1], None, 1, 1], [[None, 1, IMAGE_WF, [None, None, 1, 1]]]],
    at=1790845459,
)
EXTEND = record(
    "61555bc8-124b-4740-ba5e-963c5e8955a1",
    "2e032860-eea7-454b-b759-a9a543638cdc",
    [
        ["models/veo-3.1-lite-generate-002;backend_beyond", 5, [4], None, 1, 1],
        None,
        [[None, None, None, "42cc7879-0000-4000-8000-000000000001"]],
    ],
)
UNKNOWN_FAMILY = record(
    "aaaaaaaa-0000-4000-8000-000000000009",
    "bbbbbbbb-0000-4000-8000-000000000009",
    [["some_future_model", 9, [42], None, 1, 1]],
)
# A 1080p download leaves this beside the clip it upscaled (measured 2026-10-01): a rendition, not a version.
UPSCALE = record(
    START_ONLY[0] + "_upsampled",
    START_ONLY[2],
    [["veo_3_1_upsampler_1080p", 4, [6], None, 1, 2], None, [[START_ONLY[0], None, None, START_ONLY[0]]]],
    version="CAI",
    at=1790859999,
)
IMAGE = image_record(IMAGE_WF, IMAGE_MEDIA)
END_IMAGE = image_record(END_WF, END_MEDIA)

LISTING = [
    None,
    [
        descriptor(IMAGE_MEDIA, "v5_talk_t1_57.png", IMAGE_WF),
        descriptor(END_MEDIA, "v7_key_back57.png", END_WF),
        descriptor(VOICE_MEDIA, "LilyVoice", VOICE_WF),
        descriptor(TWO_VOICES[2], "Woman and man talking", TWO_VOICES[0]),
        descriptor(FIRST_AND_LAST[2], "Woman turning", FIRST_AND_LAST[0]),
        descriptor(WITH_CHARACTER[2], "Woman in a bakery", WITH_CHARACTER[0]),
        descriptor(EDIT[2], "Woman talking to camera", EDITED_SOURCE[0]),
    ],
    [
        TWO_VOICES,
        FIRST_AND_LAST,
        START_ONLY,
        UPSCALE,
        WITH_CHARACTER,
        EDIT,
        EDITED_SOURCE,
        EXTEND,
        UNKNOWN_FAMILY,
        IMAGE,
        END_IMAGE,
    ],
    [
        [
            "achird",
            3,
            "Achird",
            [
                "achird",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                [["Achird", "Male, friendly, mid pitch", True, "URL"]],
            ],
        ],
        [
            "leda",
            3,
            "Leda",
            [
                "leda",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                [["Leda", "Female, youthful, mid-high pitch", True, "URL"]],
            ],
        ],
    ],
]


def test_a_reference_run_keeps_its_images_and_both_kinds_of_voice():
    recipe = parsers.recipes(LISTING)[TWO_VOICES[0]]

    assert recipe["model_key"] == "abra_r2v_8s" and recipe["kind"] == "ingredients"
    assert recipe["reference_images"] == [IMAGE_WF] and recipe["frames"] == []
    assert recipe["voices"] == [VOICE_WF, "achird"], (
        "a custom voice by its id, a preset by its lowercase name"
    )
    assert recipe["characters"] == [] and recipe["source_workflow_id"] is None


def test_frames_keep_which_image_started_and_which_ended():
    both = parsers.recipes(LISTING)[FIRST_AND_LAST[0]]
    one = parsers.recipes(LISTING)[START_ONLY[0]]

    assert both["kind"] == "frames"
    assert both["frames"] == [
        {"slot": "start", "workflow_id": IMAGE_WF},
        {"slot": "end", "workflow_id": END_WF},
    ]
    assert both["reference_images"] == [] and both["voices"] == []
    assert one["frames"] == [{"slot": "start", "workflow_id": IMAGE_WF}]


def test_a_character_is_kept_by_its_entity_id():
    assert parsers.recipes(LISTING)[WITH_CHARACTER[0]]["characters"] == [ENTITY]


def test_an_edit_and_an_extend_name_the_clip_they_came_from():
    recipes = parsers.recipes(LISTING)

    assert recipes[EDIT[0]]["kind"] == "derived"
    assert recipes[EDIT[0]]["source_workflow_id"] == "36e923f0-7bd7-4366-af95-c258e0a59089"
    assert recipes[EXTEND[0]]["kind"] == "extend"
    assert recipes[EXTEND[0]]["source_workflow_id"] == "42cc7879-0000-4000-8000-000000000001"


def test_an_image_has_no_recipe_and_an_unknown_family_is_named_unknown_not_guessed():
    recipes = parsers.recipes(LISTING)

    assert IMAGE_WF not in recipes
    assert recipes[UNKNOWN_FAMILY[0]]["kind"] == "unknown"
    assert recipes[UNKNOWN_FAMILY[0]]["model_key"] == "some_future_model"


def test_the_recipe_of_a_clip_names_its_inputs_the_way_the_other_tools_do():
    answer = reader.recipe_from(LISTING, TWO_VOICES[2])

    assert answer["media_id"] == TWO_VOICES[2] and answer["workflow_id"] == TWO_VOICES[0]
    assert answer["model_key"] == "abra_r2v_8s" and answer["kind"] == "ingredients"
    assert answer["reference_images"] == [
        {"workflow_id": IMAGE_WF, "media_id": IMAGE_MEDIA, "title": "v5_talk_t1_57.png"}
    ]
    assert answer["voices"] == [
        {"voice": VOICE_WF, "name": "LilyVoice", "custom": True},
        {"voice": "achird", "name": "Achird", "custom": False},
    ]


def test_frames_and_characters_are_named_too():
    turn = reader.recipe_from(LISTING, FIRST_AND_LAST[2])
    bakery = reader.recipe_from(
        LISTING, WITH_CHARACTER[2], characters=[{"entity_id": ENTITY, "name": "LilyNight"}]
    )

    assert turn["frames"] == [
        {"slot": "start", "workflow_id": IMAGE_WF, "media_id": IMAGE_MEDIA, "title": "v5_talk_t1_57.png"},
        {"slot": "end", "workflow_id": END_WF, "media_id": END_MEDIA, "title": "v7_key_back57.png"},
    ]
    assert bakery["characters"] == [{"entity_id": ENTITY, "name": "LilyNight"}]


def test_the_newest_version_is_read_unless_one_is_asked_for():
    newest = reader.recipe_from(LISTING, EDIT[2])
    first = reader.recipe_from(LISTING, EDIT[2], workflow_id=EDITED_SOURCE[0])

    assert newest["workflow_id"] == EDIT[0] and newest["kind"] == "derived"
    assert newest["source"] == {"workflow_id": EDITED_SOURCE[0], "media_id": EDIT[2]}
    assert first["workflow_id"] == EDITED_SOURCE[0] and first["kind"] == "frames"


def test_an_upscale_left_by_a_download_is_not_taken_for_the_newest_version():
    answer = reader.recipe_from(LISTING, START_ONLY[2])

    assert answer["workflow_id"] == START_ONLY[0] and answer["kind"] == "frames", answer
    assert (
        reader.recipe_from(LISTING, START_ONLY[2], workflow_id=UPSCALE[0])["model_key"]
        == "veo_3_1_upsampler_1080p"
    )


def test_an_input_the_listing_no_longer_names_keeps_its_id_and_says_so():
    answer = reader.recipe_from(LISTING, EXTEND[2])

    assert answer["source"] == {"workflow_id": "42cc7879-0000-4000-8000-000000000001", "media_id": None}


@pytest.mark.parametrize(
    ("media_id", "workflow_id", "says"),
    [
        ("00000000-0000-4000-8000-000000000000", None, "no generation record"),
        (IMAGE_MEDIA, None, "an image"),
        (EDIT[2], "ffffffff-0000-4000-8000-000000000000", "no version"),
    ],
)
def test_a_clip_that_cannot_be_read_is_refused_with_the_reason(media_id, workflow_id, says):
    with pytest.raises(LookupError, match=says):
        reader.recipe_from(LISTING, media_id, workflow_id=workflow_id)
