"""Plan AO (G8): what Flow offers and costs, answered as data. The one rule (I-read-3): every number is read from the
file or constant the tools themselves refuse and charge by, so each expectation here is generated from that source
and one test changes a source to see the answer follow."""

import json

from gflow_cli.api.video import VideoModel, reference_cap_for

from video.flow import capabilities, clips, ingredients
from video.flow import video as video_mod


def test_every_video_cell_is_the_cell_gen_video_charges_by():
    answer = capabilities.capabilities()["video"]

    assert sorted(answer["models"]) == sorted(video_mod.VIDEO["models"])
    for name, entry in video_mod.VIDEO["models"].items():
        said = answer["models"][name]
        resolutions = entry["resolutions"] or [video_mod.FIXED["resolution"]]
        seconds = entry["durations"] or [video_mod.FIXED["duration"]]
        cells = [(r, s) for r in resolutions for s in seconds]
        assert [(c["resolution"], c["seconds"]) for c in said["credits_x1"]] == cells, name
        for cell in said["credits_x1"]:
            asked = (cell["resolution"], cell["seconds"]) if entry["durations"] else (None, None)
            assert cell["credits"] == video_mod.price(name, *asked, 1), (name, cell)
        assert (said["resolutions"], said["seconds"]) == (resolutions, seconds), name
        assert said["chooses_length"] is bool(entry["durations"]) and said["label"] == entry["label"]
        assert said["modes"] == entry["modes"]
    assert (answer["aspects"], answer["counts"]) == (video_mod.VIDEO["aspects"], video_mod.VIDEO["counts"])
    assert answer["measured"] == video_mod.OPTIONS["measured"]


def test_the_caps_are_the_ones_the_drivers_refuse_by():
    answer = capabilities.capabilities()["video"]

    for name, said in answer["models"].items():
        assert said["voice_ingredients"] == ingredients.VOICE_CAPS[name], name
        assert said["image_ingredients"] == reference_cap_for(VideoModel.from_cli(name)), name
    assert answer["caps_measured"] == ingredients.CAPS_MEASURED
    # Not all alike, or a cap read off one model for every model would pass.
    assert len({said["voice_ingredients"] for said in answer["models"].values()}) > 1
    assert len({said["image_ingredients"] for said in answer["models"].values()}) > 1


def test_the_prices_with_no_cell_are_the_measured_constants():
    answer = capabilities.capabilities()

    assert answer["editor"]["credits"] == {
        "clip_extend": clips.MEASURED_PRICE["extend"],
        "clip_edit": clips.MEASURED_PRICE["edit"],
        "agent_send": clips.MEASURED_PRICE["agent"],
    }
    character = answer["character_video"]
    assert sorted(character["models"]) == sorted(ingredients.LENGTHS)
    for name, lengths in ingredients.LENGTHS.items():
        cells = character["models"][name]["credits_x1"]
        assert [cell["seconds"] for cell in cells] == list(lengths), name
        assert [cell["credits"] for cell in cells] == [ingredients.price_for(name, s) for s in lengths], name
    image = answer["image"]
    assert image["models"] == video_mod.OPTIONS["image"]["models"]
    assert image["credits_x1"] == video_mod.OPTIONS["image"]["price_x1"]
    assert (image["aspects"], image["counts"]) == (
        video_mod.OPTIONS["image"]["aspects"],
        video_mod.OPTIONS["image"]["counts"],
    )


def test_the_answer_follows_its_sources(monkeypatch):
    # A number typed a second time would stay where it is when the source moves.
    monkeypatch.setitem(video_mod.VIDEO["models"]["veo-lite"]["price_x1"], "", 11)
    monkeypatch.setitem(ingredients.VOICE_CAPS, "veo-fast", 2)
    monkeypatch.setitem(clips.MEASURED_PRICE, "edit", 40)
    monkeypatch.setitem(ingredients.LONGER_PRICES, ("omni-flash", 10), 16)
    monkeypatch.setitem(video_mod.OPTIONS, "measured", "2027-01-01")
    monkeypatch.setattr(ingredients, "CAPS_MEASURED", "2027-01-02")
    monkeypatch.setitem(video_mod.OPTIONS["image"], "price_x1", 1)

    answer = capabilities.capabilities()

    assert answer["image"]["credits_x1"] == 1

    assert answer["video"]["models"]["veo-lite"]["credits_x1"][0]["credits"] == 11
    assert answer["video"]["models"]["veo-fast"]["voice_ingredients"] == 2
    assert answer["editor"]["credits"]["clip_edit"] == 40
    assert answer["character_video"]["models"]["omni-flash"]["credits_x1"][-1] == {
        "seconds": 10,
        "credits": 16,
    }
    assert (answer["video"]["measured"], answer["video"]["caps_measured"]) == ("2027-01-01", "2027-01-02")


def test_the_answer_is_plain_data_and_says_what_it_is_not():
    answer = capabilities.capabilities()

    assert json.loads(json.dumps(answer)) == answer
    # It is the surveyed table, not a live read: the answer says so and names what is live.
    assert "dry_run" in answer["note"] and "survey" in answer["note"] and "not read live" in answer["note"]
