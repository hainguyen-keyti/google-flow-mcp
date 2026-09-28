import json

import pytest

from video.story import bible


def test_reference_images_are_looked_for_in_the_local_assets_folder():
    # The owner's reference photos stay on this machine and out of the public repo (DECISIONS 2026-09-28).
    paths = bible.reference_images()
    assert set(paths) == {"portrait", "angles", "wardrobe"}
    for name, path in paths.items():
        assert path.parent == bible.ASSETS and path.suffix == ".png", (name, path)


def test_load_reads_the_owner_bible():
    data = bible.load()
    assert data["version"] == "1.0"
    assert data["character"]["hair"]["length"].startswith("long")


def test_load_fails_loudly_when_the_bible_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        bible.load(tmp_path / "nope.json")


def test_compose_locks_identity_room_and_negatives():
    scene = bible.Scene(
        camera="full-body mirror selfie, phone selfie perspective",
        pose="turning slowly to show the outfit",
        outfit="ivory lace-trimmed slip dress",
        expression="calm, slightly shy, small smile",
    )
    prompt = bible.compose(scene)
    low = prompt.lower()

    # identity locks that must survive every shot
    assert "mid-back" in low or "below the shoulders" in low, "hair length lock missing"
    assert "pores" in low, "skin texture lock missing"
    assert "dimple" in low, "dimple lock missing"
    assert "butterfly" in low and "below the necklace" in low, "chest tattoo lock missing"
    # room locks
    assert "mirror" in low and "wardrobe" in low and "photo" in low, "room lock missing"
    # scene variables carried through verbatim
    assert scene.outfit in prompt
    assert scene.pose in prompt
    assert scene.camera in prompt
    # negatives come from the owner's json, not from a hardcoded list
    negatives = json.loads(bible.BIBLE_JSON.read_text(encoding="utf-8"))["negative_constraints"]
    assert negatives[0].rstrip(".").lower() in low


def test_compose_rejects_an_empty_scene_variable():
    with pytest.raises(ValueError):
        bible.compose(bible.Scene(camera="", pose="p", outfit="o", expression="e"))
