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
    # Expectations swapped 2026-09-15 on purpose (DECISIONS): record[2] is the media id, record[0] the workflow.
    assert parsers.upload_record(MASEQ) == {
        "media_id": "3212a1b5-9919-45b5-8945-ae8d8b2d7467",
        "project_id": PROJECT,
        "workflow_id": "7a2f2075-d94b-4b77-ae6b-ca31b9168729",
        "size_bytes": 271467,
    }


def test_upload_record_rejects_a_payload_without_a_record():
    with pytest.raises(TypeError):
        parsers.upload_record([840, 1, 2, 2, None, 840])


def test_upload_record_names_the_id_flow_download_accepts_as_the_media_id():
    # Measured 2026-09-15 on project 118aece2: flow_upload reported media_id 09a080dc and workflow_id de29028c,
    # flow_download FAILED with 09a080dc and succeeded with de29028c, and flow_media lists that same file as
    # id de29028c / workflow_id 09a080dc. The listing parser reads this record shape correctly.
    record = [
        "09a080dc-ee03-4c1f-87c3-8625d0081a46",
        "118aece2-f6f3-4121-8d11-2b36037d9b36",
        "de29028c-acb0-4cd8-9f89-01a29679d551",
        "CAE",
        None,
        [None] * 13 + [5627],
    ]

    out = parsers.upload_record([record])

    assert out["media_id"] == "de29028c-acb0-4cd8-9f89-01a29679d551"
    assert out["workflow_id"] == "09a080dc-ee03-4c1f-87c3-8625d0081a46"
    assert out["size_bytes"] == 5627
