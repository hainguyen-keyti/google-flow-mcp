import json

from click.testing import CliRunner

from video import cli
from video.story import shots2


def test_six_shots_in_two_continuous_blocks():
    keys = [s.key for s in shots2.SHOTS]
    assert keys == ["hook", "wardrobe", "fabric", "reveal", "pose", "closing"]
    assert len(set(keys)) == 6


def test_only_the_two_block_openers_are_generated_from_scratch():
    # Frames mode has no ingredients button, so the character entity can only be attached on a base shot.
    bases = [s for s in shots2.SHOTS if s.mode == "ingredients"]
    assert [s.key for s in bases] == ["hook", "reveal"]
    assert all(s.start_from is None for s in bases)


def test_every_other_shot_continues_the_one_before_it():
    keys = [s.key for s in shots2.SHOTS]
    for index, shot in enumerate(shots2.SHOTS):
        if shot.mode == "frames":
            assert shot.start_from == keys[index - 1], f"{shot.key} must continue {keys[index - 1]}"
    assert [s.key for s in shots2.SHOTS if s.mode == "frames"] == ["wardrobe", "fabric", "pose", "closing"]


def test_the_product_image_is_attached_where_the_garment_is_sold():
    # The dress has to be the same object in the shot that shows it and the shot that wears it.
    assert shots2.by_key("reveal").product is True
    assert shots2.by_key("hook").product is False


def test_hands_risk_marks_exactly_the_shots_that_touch_things():
    risky = [s.key for s in shots2.SHOTS if s.hands_risk]
    assert risky == ["wardrobe", "pose"], risky
    assert shots2.EXTRA_TAKES == len(risky)
    assert shots2.expected_credits() == (len(shots2.SHOTS) + len(risky)) * shots2.PRICE


def test_the_fabric_shot_keeps_hands_out_of_frame():
    fabric = shots2.by_key("fabric")
    assert "no hands" in fabric.scene.pose.lower() or "without hands" in fabric.scene.pose.lower()
    assert fabric.hands_risk is False


def test_every_shot_declares_its_duration_for_acceptance_to_read():
    assert all(s.duration == 8 for s in shots2.SHOTS)
    assert shots2.total_seconds() == 6 * 8


def test_captions_run_back_to_back_over_the_whole_cut():
    windows = shots2.captions()
    assert len(windows) == 6
    assert windows[0][0] == 0.0 and windows[-1][1] == float(shots2.total_seconds())
    for index, (start, end, text) in enumerate(windows):
        assert text.strip()
        if index:
            assert start == windows[index - 1][1]


def test_plan2_carries_locked_prompts_and_stable_job_ids():
    rows = shots2.plan()
    assert [r["job_id"] for r in rows] == [f"tryon2-{i:02d}" for i in range(1, 7)]
    for row in rows:
        low = row["prompt"].lower()
        assert "mid-back" in low or "below the shoulders" in low
        assert "butterfly" in low and "below the necklace" in low
        assert "mirror" in low and "wardrobe" in low
        assert row["beat"] and row["caption"]


def test_story_plan2_command_prints_every_shot():
    result = CliRunner().invoke(cli.main, ["story", "plan2", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert len(rows) == 6
    assert rows[0]["job_id"] == "tryon2-01"
    assert rows[3]["mode"] == "ingredients"


def test_story_plan2_is_free_and_never_opens_a_browser(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("story plan2 must not open a Flow session")

    monkeypatch.setattr(cli, "_read", explode)
    result = CliRunner().invoke(cli.main, ["story", "plan2"])
    assert result.exit_code == 0, result.output
    assert "tryon2-01" in result.output
