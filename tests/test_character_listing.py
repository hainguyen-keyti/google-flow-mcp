import json
from pathlib import Path

from video.flow import parsers

FIXTURES = Path(__file__).parent / "fixtures" / "rpc"


def payload(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["payload"]


def test_characters_from_listing_reads_entity_name_and_portrait():
    # Zzl0ze[5] carries the portrait's WORKFLOW id (17b41e47 is a record's workflow_id, never a record's id).
    # Measured 2026-09-15: flow_download rejects that id and accepts the record id it maps to.
    rows = parsers.characters_from_listing(payload("Zzl0ze_character"))
    assert rows == [
        {
            "entity_id": "a7269b1c-9dbb-4e09-9628-71993e17fdd0",
            "name": "Untitled character",
            "portrait_media_id": "6449882e-e8e4-476e-b069-ee09e6c35a06",
            "portrait_workflow_id": "17b41e47-177e-4f4a-aa87-b8d77485a589",
        }
    ]


def test_a_portrait_missing_from_the_listing_has_no_media_id_rather_than_a_wrong_one():
    listing = payload("Zzl0ze_character")
    listing[2] = [record for record in listing[2] if record[0] != "17b41e47-177e-4f4a-aa87-b8d77485a589"]

    rows = parsers.characters_from_listing(listing)

    assert rows[0]["portrait_media_id"] is None
    assert rows[0]["portrait_workflow_id"] == "17b41e47-177e-4f4a-aa87-b8d77485a589"


def test_characters_from_listing_is_empty_without_characters():
    assert parsers.characters_from_listing(payload("Zzl0ze")) == []


def test_voices_from_listing_reads_the_preset_voices():
    voices = parsers.voices_from_listing(payload("Zzl0ze_character"))
    names = {v["name"] for v in voices}
    assert {"Achernar", "Achird"} <= names
    assert all(v["id"] and v["name"] for v in voices)
