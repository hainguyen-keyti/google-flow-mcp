"""Plan AO (G8): what Flow offers and costs, answered as data. The one rule (I-read-3): every number is read from the
file or constant the tools themselves refuse and charge by, so each expectation here is generated from that source
and one test changes a source to see the answer follow."""

import json

from gflow_cli import cli_image
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


def _image_params(kind):
    return {param.name: param for param in cli_image.image.commands[kind].params}


def test_the_image_table_names_what_the_image_tools_take():
    # Found in review: the table answered Flow's composer labels ("Nano Banana 2") beside gen_t2i and gen_i2i, which
    # take gflow's names and refuse every one of those labels after the job id is spent.
    image = capabilities.capabilities()["image"]

    for kind in ("t2i", "i2i"):
        params = _image_params(kind)
        assert image["models"] == list(params["model"].type.choices), kind
        assert image["default_model"] == params["model"].default, kind
        assert image["aspects"] == list(params["aspect"].type.choices), kind
        assert image["counts"] == list(range(params["count"].type.min, params["count"].type.max + 1)), kind
    surveyed = video_mod.OPTIONS["image"]
    assert image["composer"] == {
        "measured": video_mod.OPTIONS["measured"],
        "models": surveyed["models"],
        "aspects": surveyed["aspects"],
        "counts": surveyed["counts"],
        "price_line_x1": surveyed["price_x1"],
    }
    assert not set(image["models"]) & set(image["composer"]["models"]), "two vocabularies, said apart"
    # One price line was read in the survey, for whichever model the composer held: no model is given a price here.
    assert "credits_x1" not in image and image["default_model"] in image["credits_note"]


def test_a_price_never_paid_here_is_marked_and_a_caveat_is_carried():
    answer = capabilities.capabilities()

    models = answer["character_video"]["models"]
    assert {name for name, said in models.items() if not said["measured"]} == set(
        capabilities.CHARACTER_UNMEASURED
    )
    assert set(capabilities.CHARACTER_UNMEASURED) < set(models), "some are measured, or the mark says nothing"
    assert answer["editor"]["notes"] == {"agent_send": clips._WHY["agent"]}
    # The guard counts characters and images together (ingredients.resolve), which the number alone does not say.
    assert "together" in answer["video"]["caps_note"] and "character" in answer["video"]["caps_note"]
    # No date is claimed for a part that carries none.
    assert "each part with its date" not in answer["note"]
    for part in ("character_video", "editor"):
        assert "measured" not in answer[part] and answer[part]["basis"]


def test_the_answer_follows_its_sources(monkeypatch):
    # A number typed a second time would stay where it is when the source moves: every source is moved here.
    monkeypatch.setitem(video_mod.VIDEO["models"]["veo-lite"]["price_x1"], "", 11)
    monkeypatch.setitem(video_mod.VIDEO, "counts", [1, 2])
    monkeypatch.setitem(video_mod.VIDEO, "aspects", ["1:1"])
    monkeypatch.setitem(video_mod.FIXED, "duration", 9)
    monkeypatch.setitem(ingredients.VOICE_CAPS, "veo-fast", 2)
    monkeypatch.setattr(capabilities, "reference_cap_for", lambda model: 9)
    monkeypatch.setitem(clips.MEASURED_PRICE, "edit", 40)
    monkeypatch.setitem(clips.MEASURED_PRICE, "extend", 11)
    monkeypatch.setitem(clips.MEASURED_PRICE, "agent", 3)
    monkeypatch.setitem(clips._WHY, "agent", "another caveat")
    monkeypatch.setitem(ingredients.PRICES, "omni-flash", 13)
    monkeypatch.setitem(ingredients.LONGER_PRICES, ("omni-flash", 10), 16)
    monkeypatch.setitem(ingredients.LENGTHS, "veo-lite", (8, 10))
    monkeypatch.setitem(video_mod.OPTIONS, "measured", "2027-01-01")
    monkeypatch.setattr(ingredients, "CAPS_MEASURED", "2027-01-02")
    monkeypatch.setitem(video_mod.OPTIONS["image"], "price_x1", 1)
    monkeypatch.setitem(video_mod.OPTIONS["image"], "models", ["Another Banana"])
    monkeypatch.setitem(video_mod.OPTIONS["image"], "aspects", ["2:1"])
    monkeypatch.setitem(video_mod.OPTIONS["image"], "counts", [1])
    params = _image_params("t2i")
    monkeypatch.setattr(params["model"].type, "choices", ("nano2", "another"))
    monkeypatch.setattr(params["aspect"].type, "choices", ("2:1",))
    monkeypatch.setattr(params["count"].type, "max", 2)
    monkeypatch.setattr(params["model"], "default", "another")

    answer = capabilities.capabilities()

    video = answer["video"]
    assert video["models"]["veo-lite"]["credits_x1"][0]["credits"] == 11
    assert (video["counts"], video["aspects"]) == ([1, 2], ["1:1"])
    assert video["models"]["veo-lite"]["seconds"] == [9]
    assert video["models"]["veo-fast"]["voice_ingredients"] == 2
    assert {said["image_ingredients"] for said in video["models"].values()} == {9}
    assert (video["measured"], video["caps_measured"]) == ("2027-01-01", "2027-01-02")
    assert answer["editor"]["credits"] == {"clip_extend": 11, "clip_edit": 40, "agent_send": 3}
    assert answer["editor"]["notes"]["agent_send"] == "another caveat"
    character = answer["character_video"]["models"]
    assert character["omni-flash"]["credits_x1"] == [
        {"seconds": 8, "credits": 13},
        {"seconds": 10, "credits": 16},
    ]
    assert [cell["seconds"] for cell in character["veo-lite"]["credits_x1"]] == [8, 10]
    image = answer["image"]
    assert (image["models"], image["default_model"]) == (["nano2", "another"], "another")
    assert (image["aspects"], image["counts"]) == (["2:1"], [1, 2])
    assert image["composer"] == {
        "measured": "2027-01-01",
        "models": ["Another Banana"],
        "aspects": ["2:1"],
        "counts": [1],
        "price_line_x1": 1,
    }


def test_the_answer_is_plain_data_and_says_what_it_is_not():
    answer = capabilities.capabilities()

    assert json.loads(json.dumps(answer)) == answer
    # It is the surveyed table, not a live read: the answer says so and names what is live.
    assert "dry_run" in answer["note"] and "survey" in answer["note"] and "not read live" in answer["note"]
