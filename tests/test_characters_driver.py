import pytest

from video.flow import characters

PROJECT = "c5d1301b-07fe-4485-86d9-49a47449a494"


def test_entity_id_from_url_reads_the_character_route():
    url = f"https://flow.google.com/project/{PROJECT}/character/a7269b1c-9dbb-4e09-9628-71993e17fdd0"
    assert characters.entity_id_from_url(url) == "a7269b1c-9dbb-4e09-9628-71993e17fdd0"


def test_entity_id_from_url_rejects_the_new_character_page():
    with pytest.raises(ValueError):
        characters.entity_id_from_url(f"https://flow.google.com/project/{PROJECT}/character")


def test_portrait_from_ogiz0b_uses_gflow_image_records(monkeypatch):
    calls = []

    def fake_image_records(rpcid, payload):
        calls.append((rpcid, payload))

        class Record:
            media_id = "17b41e47-177e-4f4a-aa87-b8d77485a589"
            image_url = "https://flow-content.google/image/17b41e47?sig"
            dimensions = (1024, 1024)
            prompt = "young woman"

        return [Record()]

    monkeypatch.setattr(characters, "image_records", fake_image_records)
    portrait = characters.portrait_from_frames({"ogiZ0b": [["payload"]]})
    # gflow's image record calls it media_id, but in this repo it is the WORKFLOW id: measured 2026-09-15,
    # flow_download rejects 5ab4f13a (what character_create reported) and accepts the listing's 750b6fd2.
    assert portrait == {
        "workflow_id": "17b41e47-177e-4f4a-aa87-b8d77485a589",
        "url": "https://flow-content.google/image/17b41e47?sig",
        "width": 1024,
        "height": 1024,
        "prompt": "young woman",
    }
    assert calls == [("ogiZ0b", ["payload"])]


def test_portrait_from_frames_without_ogiz0b_is_none():
    assert characters.portrait_from_frames({"WuwhI": [[]]}) is None
