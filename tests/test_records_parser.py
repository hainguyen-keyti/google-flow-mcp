import pytest

from video.flow import parsers

PROJECT = "c5d1301b-07fe-4485-86d9-49a47449a494"
POSTER = "https://lh3.googleusercontent.com/asb/POSTER"
PROMPT = "the scene continues with the same objects, camera holds still"

# Measured 2026-09-12 23:00 after "Extend (Veo 3.1 - Lite)": the extension is a generation record in
# Zzl0ze[2] with no descriptor in Zzl0ze[1] (it lives inside the scene the extend created).
EXTENSION_RECORD = [
    "513526c6-5ef1-46e6-a185-0e47210089f1",
    PROJECT,
    "c1be1764-ee48-4530-b51c-9818fce873bb",
    "CAE",
    None,
    [
        [1789228805, 587756000],
        PROMPT,
        None,
        None,
        None,
        POSTER,
        [
            None,
            [["models/veo-3.1-lite-generate-002;backend_beyond", 5, [4], None, 2, 1]],
            [[None, None, [[[PROMPT]]]]],
            None,
            1,
        ],
        None,
        [3],
        1,
        POSTER,
        None,
        None,
        2063687,
    ],
    None,
    [[None, 593039, None, None, None, None, None, "<root/>", POSTER + "-full"], [None, None, [8]], []],
]
PORTRAIT_RECORD = [
    "08aba765-75cb-4588-9032-c21a247c2334",
    PROJECT,
    "4341f5b1-fd8f-4242-8a67-b8207899dfc9",
    "CAE",
    None,
    [
        [1789226962, 723149000],
        None,
        None,
        None,
        None,
        None,
        [None, None, [["young man, studio portrait", None, [[["young man, studio portrait"]]]]], [], 1],
        None,
        None,
        1,
        None,
        None,
        None,
        109977,
    ],
    [
        [
            None,
            825508887,
            None,
            None,
            None,
            None,
            None,
            "young man, studio portrait",
            29,
            "",
            None,
            "4341f5b1-fd8f-4242-8a67-b8207899dfc9",
            None,
            None,
            3,
        ],
        None,
        [1376, 768],
    ],
]
LISTED_RECORD = [
    "923b7b19-4b47-4324-89fb-1557d636742f",
    PROJECT,
    "01f70107-75de-417e-89eb-1c76567877f4",
    "CAE",
    None,
    [
        [1789227781, 408703000],
        "a ceramic teacup",
        None,
        None,
        None,
        POSTER,
        [None, [["veo_3_1_t2v_lite", 1]]],
        None,
        [3],
        1,
        POSTER,
        None,
        None,
        1682015,
    ],
    None,
    [[None, 1, None, None, None, None, None, "<root/>", POSTER + "-listed"], [None, None, [8]], []],
]
DESCRIPTOR = [
    "01f70107-75de-417e-89eb-1c76567877f4",
    None,
    None,
    [
        "Ceramic teacup on wooden table",
        [1789227781, 408703000],
        None,
        None,
        "923b7b19-4b47-4324-89fb-1557d636742f",
    ],
    PROJECT,
]
LISTING = [None, [DESCRIPTOR], [LISTED_RECORD, EXTENSION_RECORD, PORTRAIT_RECORD], [], [], None, None, []]


def test_records_include_generations_without_a_descriptor():
    rows = {r["id"]: r for r in parsers.records(LISTING)}
    assert set(rows) == {
        "01f70107-75de-417e-89eb-1c76567877f4",
        "c1be1764-ee48-4530-b51c-9818fce873bb",
        "4341f5b1-fd8f-4242-8a67-b8207899dfc9",
    }
    extension = rows["c1be1764-ee48-4530-b51c-9818fce873bb"]
    assert extension == {
        "id": "c1be1764-ee48-4530-b51c-9818fce873bb",
        "project_id": PROJECT,
        "workflow_id": "513526c6-5ef1-46e6-a185-0e47210089f1",
        "type": "CAE",
        "created": 1789228805,
        "kind": "video",
        "status": 3,
        "prompt": PROMPT,
        "model": "models/veo-3.1-lite-generate-002;backend_beyond",
        "size_bytes": 2063687,
        "url": POSTER + "-full",
        "listed": False,
    }
    assert rows["01f70107-75de-417e-89eb-1c76567877f4"]["listed"] is True
    portrait = rows["4341f5b1-fd8f-4242-8a67-b8207899dfc9"]
    assert portrait["kind"] == "image" and portrait["listed"] is False and portrait["url"] is None


EDIT_VERSION_RECORD = [
    "44127014-8b33-442c-89e1-d212ddb8e3e3",
    PROJECT,
    "01f70107-75de-417e-89eb-1c76567877f4",
    "CAI",
    None,
    [
        [1789230719, 756808000],
        "make it night time with warm lamp light",
        None,
        None,
        None,
        POSTER + "-edit",
        [None, [["abra_edit", 4, [10], None, 2, 1]]],
        None,
        [3],
        1,
        POSTER + "-edit",
        None,
        None,
        900000,
    ],
    None,
    [[None, 1, None, None, None, None, None, "<root/>", POSTER + "-edit-full"], [None, None, [8]], []],
]


def test_records_keep_every_version_of_an_edited_media():
    # Measured 2026-09-12 23:50: an Omni edit adds a second record ("CAI", model abra_edit) for the SAME
    # media id; the grid keeps one tile, "Show history" lists both steps.
    listing = [None, [DESCRIPTOR], [LISTED_RECORD, EDIT_VERSION_RECORD], [], [], None, None, []]
    rows = parsers.records(listing)
    assert [r["workflow_id"] for r in rows] == [
        "923b7b19-4b47-4324-89fb-1557d636742f",
        "44127014-8b33-442c-89e1-d212ddb8e3e3",
    ]
    assert [r["id"] for r in rows] == ["01f70107-75de-417e-89eb-1c76567877f4"] * 2
    assert [r["type"] for r in rows] == ["CAE", "CAI"]
    assert rows[1]["prompt"] == "make it night time with warm lamp light"
    assert rows[1]["url"] == POSTER + "-edit-full"
    assert rows[1]["listed"] is True
    assert len(parsers.media(listing)) == 1


def test_records_keep_the_media_count_untouched():
    assert len(parsers.media(LISTING)) == 1


def test_records_rejects_a_bad_listing():
    with pytest.raises(TypeError):
        parsers.records([None, [], 5])
