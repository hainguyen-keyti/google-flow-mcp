"""gen_video's driver (plan AB): every video option Flow's composer offers, read from the surveyed options file."""

import pytest

from video.flow import video


def test_the_options_file_holds_what_the_composer_showed_on_2026_09_29():
    # The owner's screenshot and the $0 walk agree: Omni 1.1 Flash 720p 10 s x1 is 15 credits.
    assert set(video.OPTIONS["video"]["models"]) == {"omni-flash", "veo-lite", "veo-fast", "veo-quality"}
    assert video.price("omni-flash", "720p", 10, 1) == 15
    assert video.price("omni-flash", "360p", 4, 4) == 16
    assert video.price("veo-quality", None, None, 1) == 100
    assert video.OPTIONS["video"]["aspects"] == ["16:9", "9:16"]


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"model": "veo-ultra"}, "model must be one of"),
        ({"model": "veo-lite", "duration": 10}, "veo-lite offers no length choice"),
        ({"model": "veo-lite", "resolution": "360p"}, "veo-lite offers no resolution choice"),
        ({"model": "omni-flash", "duration": 5}, "duration must be one of [4, 6, 8, 10]"),
        ({"model": "omni-flash", "resolution": "1080p"}, "resolution must be one of ['360p', '720p']"),
        ({"count": 5}, "count must be one of [1, 2, 3, 4]"),
        ({"aspect": "1:1"}, "aspect must be one of ['16:9', '9:16']"),
    ],
)
def test_a_setting_the_composer_does_not_offer_is_refused(settings, message):
    base = {"model": "omni-flash", "resolution": "720p", "duration": 8, "count": 1, "aspect": "9:16"}
    with pytest.raises(ValueError) as refused:
        video.check_settings(**(base | settings))
    assert message in str(refused.value)


def test_omni_takes_every_offered_cell_and_veo_takes_its_defaults():
    video.check_settings(model="omni-flash", resolution="360p", duration=4, count=4, aspect="16:9")
    video.check_settings(model="veo-fast", resolution=None, duration=None, count=2, aspect="9:16")


@pytest.mark.parametrize(
    ("refs", "mode"),
    [
        ({}, "Frames"),
        ({"start_frame": "s"}, "Frames"),
        ({"start_frame": "s", "end_frame": "e"}, "Frames"),
        ({"media_ids": ["m"]}, "Ingredients"),
        ({"characters": ["c"]}, "Ingredients"),
    ],
)
def test_the_mode_follows_what_was_passed(refs, mode):
    assert video.mode_for(**refs) == mode


def test_frames_and_ingredients_cannot_be_mixed_and_an_end_needs_a_start():
    with pytest.raises(ValueError, match="either frames or ingredients"):
        video.mode_for(start_frame="s", media_ids=["m"])
    with pytest.raises(ValueError, match="end_frame needs a start_frame"):
        video.mode_for(end_frame="e")
