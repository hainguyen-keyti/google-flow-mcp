import json
from pathlib import Path

import pytest

from video.flow import parsers

FIXTURES = Path(__file__).parent / "fixtures" / "rpc"
PROJECT = "c5d1301b-07fe-4485-86d9-49a47449a494"


def payload(rpcid: str):
    return json.loads((FIXTURES / f"{rpcid}.json").read_text(encoding="utf-8"))["payload"]


def test_projects_lists_every_card_with_id_title_and_created():
    projects = parsers.projects(payload("UpteDb"))
    assert len(projects) == 16
    first = projects[0]
    assert first["id"] == "5c8f84a5-55ed-4fa8-abef-a77d9ae5e040"
    assert first["title"] == "Sep 12 - 15:31"
    assert first["created"] == 1789201863
    assert all(p["id"] for p in projects)
    assert "8822142b-ca75-46b7-aac8-03d2831_backfill" in {p["id"] for p in projects}


def test_project_meta_returns_id_and_title():
    meta = parsers.project_meta(payload("ngNC2"))
    assert meta == {"id": PROJECT, "title": "Sep 08 - 17:27"}


def test_credits_reads_the_balance():
    assert parsers.credits(payload("nzlxg")) == {"balance": 840, "raw": [840, 1, 2, 2, None, 840]}


def test_video_models_lists_the_available_arms():
    assert parsers.video_models(payload("yBhWQ")) == [
        "veo_3_1_quality",
        "abra",
        "veo_3_1_fast",
        "veo_3_1_lite",
    ]


def test_media_joins_descriptors_with_generation_records():
    media = parsers.media(payload("Zzl0ze"))
    assert len(media) == 15
    kinds = {m["kind"] for m in media}
    assert kinds == {"video", "image"}
    assert sum(m["kind"] == "video" for m in media) == 12
    assert sum(m["kind"] == "image" for m in media) == 3
    first = media[0]
    assert first["id"] == "1480bbf8-ebdc-41d1-ba7d-60163ccf1d4f"
    assert first["project_id"] == PROJECT
    assert first["title"] == "Red paper boat circling"
    assert first["created"] == 1789219778
    image = next(m for m in media if m["id"] == "28111df1-26fa-47a0-9863-55045340e8cf")
    assert image["kind"] == "image"
    assert image["size_bytes"] == 76766
    assert image["prompt"].startswith("a small red paper boat on a calm pond")
    assert image["url"].startswith("https://lh3.googleusercontent.com/")
    video = next(m for m in media if m["id"] == "1c2f26c6-b362-4bae-96d9-ae84c8adf96c")
    assert video["kind"] == "video"
    assert video["model"] == "veo_2_1_fast_d_15_with_start_image_and_end_image_interpolation"


def test_media_rejects_a_payload_that_is_not_a_listing():
    with pytest.raises(TypeError):
        parsers.media([840, 1, 2, 2, None, 840])


def test_media_of_an_empty_project_is_empty_rather_than_an_error():
    # A project with nothing in it simply has no descriptor and no record section, and `_at` answers
    # None for an absent index. Measured 2026-09-13: flow_media raised TypeError against a fresh
    # project, so an agent that created a project could not then look inside it.
    assert parsers.media([None, None, None]) == []
    assert parsers.media([None]) == []


def test_media_still_refuses_a_listing_whose_sections_are_the_wrong_type():
    # The empty case above must not become a licence to swallow corruption: absent is an empty project,
    # present-but-not-a-list is a broken payload and has to keep raising. The credits reply used by the
    # test above is exactly that shape, ints where the lists belong.
    with pytest.raises(TypeError):
        parsers.media([None, 1, [], None])
    with pytest.raises(TypeError):
        parsers.media([None, [], "nope", None])
