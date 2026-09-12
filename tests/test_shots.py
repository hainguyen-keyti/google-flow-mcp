import json

from click.testing import CliRunner

from video import cli
from video.story import shots


def test_config_matches_the_plan_format():
    assert shots.ASPECT == "9:16"
    assert shots.MODEL == "veo-lite"
    assert shots.DURATION == 8
    assert shots.COUNT == 1


def test_five_shots_tell_a_try_on_selling_story():
    keys = [s.key for s in shots.SHOTS]
    assert len(keys) == 5, keys
    assert len(set(keys)) == 5, "shot keys must be unique, they become job ids"
    assert keys[0] == "hook" and keys[-1] == "closing"
    # the middle beats are the actual try-on: wardrobe, holding a piece up, wearing it
    assert keys[1:4] == ["wardrobe", "holdup", "tryon"]


def test_every_shot_is_a_complete_scene_with_its_own_look():
    cameras, outfits = set(), set()
    for shot in shots.SHOTS:
        assert shot.beat.strip(), f"{shot.key} has no selling beat"
        for field in ("camera", "pose", "outfit", "expression"):
            assert getattr(shot.scene, field).strip(), f"{shot.key}.{field} empty"
        cameras.add(shot.scene.camera)
        outfits.add(shot.scene.outfit)
    assert len(cameras) == 5, "every shot needs its own camera so the cut is not static"
    assert len(outfits) >= 2, "a try-on video has to show more than one outfit"


def test_plan_carries_the_locked_prompt_and_the_job_id():
    rows = shots.plan()
    assert [r["key"] for r in rows] == [s.key for s in shots.SHOTS]
    for index, row in enumerate(rows, start=1):
        assert row["job_id"] == f"tryon-{index:02d}"
        low = row["prompt"].lower()
        assert "mid-back" in low or "below the shoulders" in low, "identity lock lost"
        assert "butterfly" in low and "below the necklace" in low, "tattoo lock lost"
        assert "mirror" in low and "wardrobe" in low, "room lock lost"
        assert row["beat"]


def test_story_plan_command_prints_every_shot(monkeypatch):
    result = CliRunner().invoke(cli.main, ["story", "plan", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert len(rows) == 5
    assert rows[0]["job_id"] == "tryon-01"


def test_story_plan_command_is_free_and_never_opens_a_browser(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("story plan must not open a Flow session")

    monkeypatch.setattr(cli, "_read", explode)
    result = CliRunner().invoke(cli.main, ["story", "plan"])
    assert result.exit_code == 0, result.output
    assert "tryon-01" in result.output
