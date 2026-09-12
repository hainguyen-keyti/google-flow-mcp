import json
from pathlib import Path

from video.flow import parsers

FIXTURES = Path(__file__).parent / "fixtures" / "rpc"


def payload(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["payload"]


def test_characters_from_listing_reads_entity_name_and_portrait():
    rows = parsers.characters_from_listing(payload("Zzl0ze_character"))
    assert rows == [
        {
            "entity_id": "a7269b1c-9dbb-4e09-9628-71993e17fdd0",
            "name": "Untitled character",
            "portrait_media_id": "17b41e47-177e-4f4a-aa87-b8d77485a589",
        }
    ]


def test_characters_from_listing_is_empty_without_characters():
    assert parsers.characters_from_listing(payload("Zzl0ze")) == []


def test_voices_from_listing_reads_the_preset_voices():
    voices = parsers.voices_from_listing(payload("Zzl0ze_character"))
    names = {v["name"] for v in voices}
    assert {"Achernar", "Achird"} <= names
    assert all(v["id"] and v["name"] for v in voices)
