import pytest

from video.story import composer


def test_price_is_read_from_the_composer_settings_line():
    assert composer.price_from("crop_9_16 9:16 Veo 3.1 - Lite x1 Generating will use 10 credits") == 10
    assert composer.price_from("Generating will use 20 credits") == 20
    assert composer.price_from("Generating will use 1 credit") == 1
    assert composer.price_from("no price here") is None


def test_ensure_price_blocks_a_submit_that_would_cost_more_than_planned():
    # The composer remembers the last run: leaving it on x2 silently doubles the bill.
    composer.ensure_price(10, 10)
    with pytest.raises(RuntimeError, match="20"):
        composer.ensure_price(20, 10)
    with pytest.raises(RuntimeError, match="could not read"):
        composer.ensure_price(None, 10)


def test_pick_output_prefers_the_record_carrying_our_prompt():
    prompt = "SHOT: full-body mirror selfie. turning slowly. Wearing cream lace slip dress."
    fresh = [
        {"workflow_id": "w1", "kind": "video", "created": 10, "prompt": "something else", "status": 3},
        {"workflow_id": "w2", "kind": "video", "created": 20, "prompt": prompt, "status": 3},
    ]
    assert composer.pick_output(fresh, prompt)["workflow_id"] == "w2"


def test_pick_output_falls_back_to_the_newest_video_and_ignores_images():
    prompt = "a prompt that no record echoes"
    fresh = [
        {"workflow_id": "img", "kind": "image", "created": 99, "prompt": None, "status": 3},
        {"workflow_id": "old", "kind": "video", "created": 10, "prompt": None, "status": 3},
        {"workflow_id": "new", "kind": "video", "created": 20, "prompt": None, "status": 3},
    ]
    assert composer.pick_output(fresh, prompt)["workflow_id"] == "new"
    assert composer.pick_output([], prompt) is None
