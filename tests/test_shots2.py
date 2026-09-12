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
        "pose": "frames",
        "closing": "frames",
    }
    assert all(s.start_from is None for s in shots2.SHOTS if s.mode != "frames")


def test_only_the_reveal_is_edited_into_the_product():
    edited = [s.key for s in shots2.SHOTS if s.edit_to_product]
    assert edited == ["reveal"], "one paid Omni edit; the later shots inherit the outfit through frames"


def test_every_other_shot_continues_the_one_before_it():
    keys = [s.key for s in shots2.SHOTS]
    for index, shot in enumerate(shots2.SHOTS):
        if shot.mode == "frames":
            assert shot.start_from == keys[index - 1], f"{shot.key} must continue {keys[index - 1]}"
    assert [s.key for s in shots2.SHOTS if s.mode == "frames"] == ["wardrobe", "pose", "closing"]


def test_the_product_photo_is_used_only_where_no_face_is_needed():
    # The product photo wins over the face, so it is only attached to the shot with nobody in it.
    assert shots2.by_key("fabric").product is True
    assert shots2.by_key("reveal").product is False
    assert shots2.by_key("hook").product is False


def test_expected_credits_counts_the_omni_edit_and_the_extra_takes():
    # 6 shots at 10, plus 2 extra takes for the hands-risky shots, plus 20 for the one Omni edit.
    assert shots2.expected_credits() == 6 * 10 + 2 * 10 + 20


def test_the_garment_is_named_once_and_then_inherited_not_described_again():
    # The owner rejected v1 because the outfit changed between clips. Words cannot hold a garment: the
    # Omni edit puts it on and the start frame carries it forward, so only the shot with no frame to
    # inherit from names it.
    before = shots2.by_key("hook").scene.outfit.lower()
    assert "t-shirt" in before or "tee" in before
    assert "pink" not in before
    assert "pink" not in shots2.by_key("reveal").scene.outfit.lower()
    assert "pink" in shots2.by_key("fabric").scene.outfit.lower()
    for key in ("pose", "closing"):
        outfit = shots2.by_key(key).scene.outfit.lower()
        assert "pink" not in outfit, key
        assert "same outfit" in outfit and "starting frame" in outfit, key


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
