import pytest

from video.flow import parsers

PROJECT = "c5d1301b-07fe-4485-86d9-49a47449a494"
MASEQ = [
    [
        "7a2f2075-d94b-4b77-ae6b-ca31b9168729",
        PROJECT,
        "3212a1b5-9919-45b5-8945-ae8d8b2d7467",
        "CAE",
        None,
        [
            [1789225542, 27616000],
            None,
            None,
            None,
            None,
            None,
            [None, None, None, None, 1],
            None,
            None,
            1,
            None,
            None,
            None,
            271467,
        ],
        [None, [None, None, None, None, 0], [13]],
    ]
]


def test_upload_record_reads_media_workflow_project_and_size():
    assert parsers.upload_record(MASEQ) == {
        "media_id": "7a2f2075-d94b-4b77-ae6b-ca31b9168729",
        "project_id": PROJECT,
        "workflow_id": "3212a1b5-9919-45b5-8945-ae8d8b2d7467",
        "size_bytes": 271467,
    }


def test_upload_record_rejects_a_payload_without_a_record():
    with pytest.raises(TypeError):
        parsers.upload_record([840, 1, 2, 2, None, 840])
