import pytest

from video.flow import parsers

LISTING = [
    None,
    [],
    [],
    [],
    [
        [
            "033a5063-bfb6-4dc9-9f91-784bc1af6df0",
            "t10 acceptance",
            None,
            [1789227539, 990227000],
            [1789227548, 750861000],
            2,
            None,
            [],
        ],
        [
            "10281ce9-5201-4a3e-85dd-7d32f3c8988e",
            "Untitled Scene 09-12 15:28:13",
            None,
            [1789225693, 0],
            [1789225700, 0],
            2,
            None,
            [],
        ],
    ],
    None,
    None,
    [],
]


def test_scenes_from_listing_reads_id_title_and_timestamps():
    assert parsers.scenes_from_listing(LISTING) == [
        {
            "scene_id": "033a5063-bfb6-4dc9-9f91-784bc1af6df0",
            "title": "t10 acceptance",
            "created": 1789227539,
            "updated": 1789227548,
        },
        {
            "scene_id": "10281ce9-5201-4a3e-85dd-7d32f3c8988e",
            "title": "Untitled Scene 09-12 15:28:13",
            "created": 1789225693,
            "updated": 1789225700,
        },
    ]


def test_scenes_from_listing_is_empty_when_the_section_is_missing():
    assert parsers.scenes_from_listing([None, [], [], [], None, None, None, []]) == []


def test_scenes_from_listing_rejects_a_bad_section():
    with pytest.raises(TypeError):
        parsers.scenes_from_listing([None, [], [], [], 5, None, None, []])
