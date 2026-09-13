import json

from click.testing import CliRunner

from video import cli
from video.story import shots2


def test_six_shots_in_two_continuous_blocks():
    keys = [s.key for s in shots2.SHOTS]
    assert keys == ["hook", "wardrobe", "fabric", "reveal", "pose", "closing"]
    assert len(set(keys)) == 6


def test_each_shot_uses_the_route_measured_for_its_job():
    # Measured 2026-09-13: a character chip keeps the face but not the garment; r2v with the product
    # photo keeps the garment but not the face; Omni edit keeps the face AND changes the clothes.
    routes = {s.key: s.mode for s in shots2.SHOTS}
    assert routes == {
        "hook": "character",
        "wardrobe": "frames",
        "fabric": "product",
        "reveal": "character",
        "pose": "edit",
        "closing": "edit",
    }
    assert all(s.start_from is None for s in shots2.SHOTS if s.mode in ("character", "product"))


def test_every_shot_that_shows_the_set_pays_for_its_own_edit():
    # Words cannot put the set on her and a frame of her wearing it is refused, so the only way in is the
    # Omni edit, once per shot that shows it.
    edited = [s.key for s in shots2.SHOTS if s.edit_to_product]
    assert edited == ["reveal", "pose", "closing"]


def test_every_continued_shot_follows_the_one_before_it():
    keys = [s.key for s in shots2.SHOTS]
    for index, shot in enumerate(shots2.SHOTS):
        if shot.start_from:
            assert shot.start_from == keys[index - 1], f"{shot.key} must continue {keys[index - 1]}"
    # Frames is refused from the mirror still, whatever the wording: 4 submits, no record, no charge.
    assert [s.key for s in shots2.SHOTS if s.mode == "frames"] == ["wardrobe"]
    assert [s.key for s in shots2.SHOTS if s.mode == "edit"] == ["pose", "closing"]


def test_the_product_photo_is_used_only_where_no_face_is_needed():
    # The product photo wins over the face, so it is only attached to the shot with nobody in it.
    assert shots2.by_key("fabric").product is True
    assert shots2.by_key("reveal").product is False
    assert shots2.by_key("hook").product is False


def test_expected_credits_prices_each_take_by_the_route_it_takes():
    # Generated takes cost 10: hook, wardrobe x2, fabric, reveal. The reveal also pays 20 to be dressed.
    # Pose (x2 takes) and closing ARE Omni edits, 20 each, and pay nothing on top.
    assert shots2.expected_credits() == 5 * 10 + 20 + 3 * 20
    assert shots2.expected_credits() == 130


def test_only_a_generated_take_is_wearing_her_own_clothes():
    # One rule, no exceptions: the base take always describes the everyday outfit and the Omni edit is
    # what puts the set on. Measured 2026-09-13, three shots in a row: the two takes that said "wearing
    # the set" or "the same outfit as the starting frame" were refused with no record and no charge,
    # while the same shot with the everyday outfit generated first time.
    worn = shots2.by_key("fabric").scene.outfit.lower()
    assert "pink" in worn, "only the shot with nobody in it names the garment"
    for shot in shots2.SHOTS:
        if shot.key == "fabric" or shot.mode == "edit":
            continue
        outfit = shot.scene.outfit.lower()
        assert "pink" not in outfit, shot.key
        assert "t-shirt" in outfit and "everyday" in outfit, shot.key


def test_hands_risk_marks_exactly_the_shots_that_touch_things():
    risky = [s.key for s in shots2.SHOTS if s.hands_risk]
    assert risky == ["wardrobe", "pose"], risky
    assert shots2.EXTRA_TAKES == len(risky)


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
    assert rows[3]["mode"] == "character" and rows[3]["edit_to_product"] is True


def test_story_plan2_is_free_and_never_opens_a_browser(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("story plan2 must not open a Flow session")

    monkeypatch.setattr(cli, "_read", explode)
    result = CliRunner().invoke(cli.main, ["story", "plan2"])
    assert result.exit_code == 0, result.output
    assert "tryon2-01" in result.output


def test_each_join_says_whether_it_is_a_cut_or_a_dissolve():
    # A dissolve between two different shots double-exposes them, which is what the owner saw. Cut those.
    # The three mirror shots are re-renders of the same moment, so cutting them would jump her back to the
    # start pose; those keep a dissolve to cover the restart.
    joins = shots2.joins()
    assert len(joins) == len(shots2.SHOTS) - 1
    # Every join is a cut. A dissolve reads as slow on a phone, and on TikTok the jump cut IS the
    # grammar: the three mirror shots cutting straight into each other is what the format expects.
    assert joins == ["cut"] * 5
    assert shots2.by_key("hook").join == "cut"
