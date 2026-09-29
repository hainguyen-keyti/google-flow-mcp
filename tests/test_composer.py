import asyncio

import pytest

from video.flow import composer


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


def test_frames_mode_is_recognised_from_the_buttons_the_composer_really_renders():
    # Measured 2026-09-13 on tryon2-02: the composer WAS in Frames mode and the driver still refused,
    # because the marker asked for an exact aria-label. Judge by the slot buttons instead.
    empty = ["Start", "Swap first and last frames", "End", "Agent", "Settings trigger", "Start generation"]
    assert composer.mode_visible(empty, "Frames")
    assert composer.mode_visible(["  Start\n", "End", "Start generation"], "Frames")
    filled = ["Image ingredient, tryon2-02_start.png", "Swap first and last frames", "End", "Agent"]
    assert composer.mode_visible(filled, "Frames")


def test_a_composer_with_no_slots_is_not_in_frames_mode():
    # "Start generation" is the submit button: counting it as the Start slot would hide a real failure.
    assert not composer.mode_visible(["Agent", "Settings trigger", "Start generation"], "Frames")
    assert not composer.mode_visible([], "Frames")


def test_ingredients_mode_is_recognised_and_an_unchecked_mode_passes():
    assert composer.mode_visible(["Add ingredient", "Agent", "Start generation"], "Ingredients")
    assert not composer.mode_visible(["Start", "End", "Start generation"], "Ingredients")
    assert composer.mode_visible(["Agent"], "Video")


def test_start_slot_is_only_filled_once_it_carries_an_image():
    assert not composer.start_slot_filled(["Start", "Swap first and last frames", "End"])
    assert composer.start_slot_filled(["Image ingredient, tryon2-02_start.png", "End"])


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


def _fetch_world(monkeypatch, tmp_path):
    urls = []

    async def fetch(request, url, stem):
        urls.append(url)
        return tmp_path / "clip.mp4"

    async def fallback(request, record, stem, attempts=6):
        urls.append(f"fallback {record['url']}")
        return tmp_path / "sd.mp4"

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(composer.download_mod, "fetch_to_file", fetch)
    monkeypatch.setattr(composer.clips, "_fetch_with_retry", fallback)
    monkeypatch.setattr(composer.asyncio, "sleep", no_sleep)
    return urls


class _FetchSession:
    page = type("P", (), {"request": object()})()


def test_fetch_720_asks_flow_for_a_moved_url(monkeypatch, tmp_path):
    """Plan AD: the listing's new host answers 400 to every suffix; flow.google.com serves the same token."""
    urls = _fetch_world(monkeypatch, tmp_path)
    record = {
        "kind": "video",
        "url": "https://contribution.fife.usercontent.google.com/asb/T",
        "model": "veo_3_1_t2v_lite",
    }

    asyncio.run(composer.fetch_720(_FetchSession(), record, tmp_path / "shot"))

    assert urls == ["https://flow.google.com/asb/T=m22"], urls


def test_a_360p_clip_is_not_held_waiting_for_a_720p_file_it_never_gets(monkeypatch, tmp_path):
    """Measured 2026-09-29 (job ab-1, key abra_t2v_4s_360p): =m22 answers 404 and =m18 is the 360p file."""
    urls = _fetch_world(monkeypatch, tmp_path)
    record = {
        "kind": "video",
        "url": "https://contribution.fife.usercontent.google.com/asb/T",
        "model": "abra_t2v_4s_360p",
    }

    out = asyncio.run(composer.fetch_720(_FetchSession(), record, tmp_path / "shot"))

    assert out == tmp_path / "sd.mp4"
    assert not any(u.endswith("=m22") for u in urls), urls
