"""The Flow survey (plan AC): walk Flow's pages, read their UI labels and the selectors this repo steers by, and compare
with the baselines in the repo, so a Flow update is found by a machine instead of by a failed paid run."""

import ast
import asyncio
import json
from pathlib import Path

import pytest

from video.flow import survey, video


def test_the_selectors_are_gathered_from_the_source_not_typed_here():
    found = survey.collect_selectors()
    assert found["flow.agent.CHIP"] == "button.agent-mode-chip, flow-agent-mode-toggle-chip button"
    assert (
        "flow.composer.SETTINGS" in found and "flow.clips.EDITOR" in found and "session.GRID_READY" in found
    )
    assert all(not value.startswith(("(", "async")) for value in found.values())


@pytest.mark.parametrize(
    "label",
    [
        # Spelled in two pieces so that no tracked file holds an address at a personal mail host (test_public_hygiene).
        "someone.private" + "@gmail.com",
        "Google Account: Keyti (keyti@example.com)",
        "3f5ce5c7-4a65-4211-b846-8a1db0cb1174",
        "mcp draft",
        "Green teapot on wooden table",
        "",
        "x" * 90,
    ],
)
def test_private_or_account_specific_text_never_becomes_a_label(label):
    assert survey.clean_labels([label], private={"mcp draft", "Green teapot on wooden table"}) == []


def test_labels_are_normalized_sorted_and_unique():
    assert survey.clean_labels(["  Add   clip ", "Add clip", "videocam\\nVideo"], private=set()) == [
        "Add clip",
        "videocam Video",
    ]


BASE_UI = {
    "routes": {
        "project": {
            "labels": ["Agent", "Settings trigger"],
            "selectors": {"flow.agent.CHIP": 1, "flow.clips.EDITOR": 0},
        },
        "clip editor": {"labels": ["Add clip"], "selectors": {"flow.agent.CHIP": 0, "flow.clips.EDITOR": 1}},
    }
}


def test_the_comparison_names_every_label_added_or_removed_and_every_selector_that_stopped_matching():
    current = json.loads(json.dumps(BASE_UI))
    current["routes"]["project"]["labels"] = ["Agent", "Remix"]
    current["routes"]["clip editor"]["selectors"]["flow.clips.EDITOR"] = 0

    found = survey.compare_ui(BASE_UI, current)

    assert ("label added", "project", "Remix") in found
    assert ("label removed", "project", "Settings trigger") in found
    assert ("selector lost", "clip editor", "flow.clips.EDITOR") in found
    assert len(found) == 3, found


def test_a_route_that_went_missing_is_reported():
    current = {"routes": {"project": BASE_UI["routes"]["project"]}}
    assert ("route missing", "clip editor", "") in survey.compare_ui(BASE_UI, current)


def test_no_change_means_no_findings():
    assert survey.compare_ui(BASE_UI, json.loads(json.dumps(BASE_UI))) == []


def test_the_option_comparison_names_models_cells_and_prices_that_changed():
    base = video.OPTIONS
    current = json.loads(json.dumps(base))
    current["video"]["models"]["omni-flash"]["price_x1"]["720p 10s"] = 16
    current["video"]["models"]["veo-ultra"] = {
        "label": "Veo 3.1 - Ultra",
        "resolutions": [],
        "durations": [],
        "price_x1": {"": 200},
        "modes": ["Frames"],
    }
    del current["video"]["models"]["veo-quality"]
    current["image"]["aspects"].append("21:9")

    found = survey.compare_options(base, current)

    assert ("price changed", "omni-flash", "720p 10s: 15 -> 16") in found
    assert ("model added", "veo-ultra", "Veo 3.1 - Ultra") in found
    assert ("model removed", "veo-quality", "Veo 3.1 - Quality") in found
    assert ("image option changed", "aspects", "added ['21:9']") in found
    assert survey.compare_options(base, json.loads(json.dumps(base))) == []


def test_the_option_walk_becomes_the_options_file_gen_video_reads():
    """The walk's raw shape (as /tmp/option_matrix.py measured it on 2026-09-29) turns into flow_options.json."""

    def omni(price720, price360):
        prices = {}
        for d, p7, p3 in zip((4, 6, 8, 10), price720, price360):
            for c in (1, 2, 3, 4):
                prices[f"720p {d}s x{c}"] = p7 * c
                prices[f"360p {d}s x{c}"] = p3 * c
        return {
            "rows": [["360p", "720p"], ["4s", "6s", "8s", "10s"], ["x1", "x2", "x3", "x4"]],
            "prices": prices,
        }

    def veo(x1):
        return {"rows": [["x1", "x2", "x3", "x4"]], "prices": {f"x{c}": x1 * c for c in (1, 2, 3, 4)}}

    per_mode = {
        "Omni 1.1 Flash": omni((7, 10, 12, 15), (4, 5, 6, 7)),
        "Veo 3.1 - Lite": veo(10),
        "Veo 3.1 - Fast": veo(20),
        "Veo 3.1 - Quality": veo(100),
    }
    walk = {
        "measured": video.OPTIONS["measured"],
        "video": {"aspects": ["16:9", "9:16"], "modes": {"Frames": per_mode, "Ingredients": per_mode}},
        "image": {
            "models": ["Nano Banana Pro", "Nano Banana 2", "Nano Banana 2 Lite"],
            "aspects": ["16:9", "4:3", "1:1", "3:4", "9:16"],
            "counts": [1, 2, 3, 4],
            "price_x1": 0,
        },
    }

    options = survey.options_from_walk(walk)

    assert options["video"] == video.OPTIONS["video"]
    assert options["image"] == video.OPTIONS["image"]


def test_a_model_nobody_has_named_yet_gets_a_name_from_its_label():
    assert survey.slug("Veo 3.2 - Ultra") == "veo-3-2-ultra"
    assert survey.slug("Omni 1.1 Flash") == "omni-flash"


def test_every_click_of_the_survey_goes_through_the_one_guarded_click():
    """I-no-money, review of plan AC: a scan for button names proved nothing about clicks by position or by names read
    off the page. Every click the survey makes itself is safe_click's, which reads the element's label first."""
    source = Path(survey.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    clicks = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "click"
    ]
    guard = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "safe_click")
    inside = {id(n) for n in ast.walk(guard)}
    assert len(clicks) == 1 and id(clicks[0]) in inside, [ast.unparse(c) for c in clicks]


class _Labelled:
    def __init__(self, label):
        self.label, self.clicked = label, False

    async def get_attribute(self, name):
        return self.label if name == "aria-label" else None

    async def inner_text(self):
        return self.label

    async def click(self, timeout=None):
        self.clicked = True


@pytest.mark.parametrize(
    "label",
    [
        "Start generation",
        "delete Move to trash",
        "Delete image",
        "Save frame",
        "Extend (Veo 3.1 - Lite)",
        "Restore all",
    ],
)
def test_safe_click_refuses_anything_that_spends_saves_or_deletes(label):
    import asyncio

    element = _Labelled(label)
    with pytest.raises(survey.UnsafeClick):
        asyncio.run(survey.safe_click(element))
    assert element.clicked is False


@pytest.mark.parametrize(
    "label", ["delete Trash", "videocam Video", "Download media", "More options", "x4", "360p info"]
)
def test_safe_click_opens_views_menus_and_radios(label):
    import asyncio

    element = _Labelled(label)
    asyncio.run(survey.safe_click(element))
    assert element.clicked is True


def test_the_baseline_in_the_repo_holds_no_private_text():
    ui = json.loads((Path(survey.__file__).with_name("flow_ui.json")).read_text(encoding="utf-8"))
    text = json.dumps(ui)
    assert "@" not in text.replace("@ ", "")
    assert not survey.UUID.search(text)


# Review of plan AC: --write feeds gen_video's money guard, so a broken walk must never become the baseline.
def _walk_with(**change):
    per_mode = {
        "Veo 3.1 - Lite": {"rows": [["x1", "x2"]], "prices": {"x1": 10, "x2": 20}},
    }
    walk = {
        "measured": "2026-09-29",
        "video": {"aspects": ["16:9", "9:16"], "modes": {"Frames": per_mode, "Ingredients": per_mode}},
        "image": dict(video.OPTIONS["image"]),
    }
    walk.update(change)
    return walk


@pytest.mark.parametrize(
    ("walk", "problem"),
    [
        (_walk_with(video={"aspects": [], "modes": {"Frames": {}, "Ingredients": {}}}), "no video model"),
        (
            _walk_with(
                video={
                    "aspects": ["16:9"],
                    "modes": {"Frames": {"Veo 3.1 - Lite": {"rows": [["x1"]], "prices": {"x1": None}}}},
                }
            ),
            "no price",
        ),
    ],
)
def test_a_walk_that_read_nothing_or_no_price_is_never_written(walk, problem):
    with pytest.raises(survey.SurveyIncomplete, match=problem):
        survey.check_writable(
            {"routes": {"home": {"labels": ["Home"], "selectors": {}}}}, survey.options_from_walk(walk)
        )


def test_a_route_that_failed_is_never_written():
    ui = {"routes": {"clip editor": {"labels": [], "selectors": {}, "error": "TimeoutError: x"}}}
    with pytest.raises(survey.SurveyIncomplete, match="clip editor"):
        survey.check_writable(ui, survey.options_from_walk(_walk_with()))


def test_a_route_that_failed_is_reported_as_its_error_not_as_every_label_removed():
    base = {"routes": {"clip editor": {"labels": ["Add clip"], "selectors": {"flow.clips.EDITOR": 1}}}}
    now = {"routes": {"clip editor": {"labels": [], "selectors": {}, "error": "TimeoutError: toolbar"}}}
    assert survey.compare_ui(base, now) == [("route error", "clip editor", "TimeoutError: toolbar")]


def test_a_settings_row_nobody_has_seen_is_reported_not_crashed_on():
    walk = _walk_with(
        video={
            "aspects": ["16:9"],
            "modes": {
                "Frames": {"Omni 1.1 Flash": {"rows": [["4s", "8s"], ["On", "Off"], ["x1"]], "prices": {}}}
            },
        }
    )
    with pytest.raises(survey.OptionLayoutChanged, match="Omni 1.1 Flash"):
        survey.options_from_walk(walk)


def test_a_model_that_differs_between_frames_and_ingredients_is_reported_not_merged():
    lite = {"rows": [["x1"]], "prices": {"x1": 10}}
    walk = _walk_with(
        video={
            "aspects": ["16:9"],
            "modes": {
                "Frames": {"Veo 3.1 - Lite": lite},
                "Ingredients": {"Veo 3.1 - Lite": {"rows": [["x1"]], "prices": {"x1": 12}}},
            },
        }
    )
    with pytest.raises(survey.OptionLayoutChanged, match="differs between modes"):
        survey.options_from_walk(walk)


def test_a_route_name_read_off_the_page_goes_through_the_same_privacy_filter():
    assert survey.route_name(
        "clip editor menu", "Green teapot on wooden table", {"Green teapot on wooden table"}, 2
    ) == ("clip editor menu 2")
    assert (
        survey.route_name("clip editor menu", "Download media", set(), 0) == "clip editor menu Download media"
    )


class _Keys:
    async def press(self, key):
        return None


class _SettingsPage:
    keyboard = _Keys()

    async def wait_for_timeout(self, ms):
        return None


class _SettingsWalker(survey.Walker):
    """The settings walk with Flow's panel faked: it opens on whatever tab the last run left, Image after gen_i2i."""

    def __init__(self, tmp_path):
        super().__init__(type("S", (), {"page": _SettingsPage()})(), "P", tmp_path)
        self.log, self.tab, self.model, self.count = [], "Image", "Veo 3.1 - Quality", "x4"

    async def record(self, route, scope=None):
        self.log.append(("record", route, self.tab))

    async def radio(self, text):
        self.log.append(("radio", text))
        if text in ("Image", "Video"):
            self.tab = text
        elif text.startswith("x"):
            self.count = text
        return True

    async def pane(self):
        tabs = [
            {"t": "image Image", "on": self.tab == "Image"},
            {"t": "videocam Video", "on": self.tab == "Video"},
        ]
        aspects = [{"t": "crop_9_16 9:16", "on": True}]
        counts = [{"t": c, "on": c == self.count} for c in ("x1", "x4")]
        if self.tab == "Image":
            return {"groups": [tabs, aspects, counts], "price": 0}
        return {"groups": [tabs, [{"t": "crop_free Frames", "on": True}], aspects, counts], "price": 12}

    async def model_names(self):
        return None, (["Nano Banana 2"] if self.tab == "Image" else ["Omni 1.1 Flash", "Veo 3.1 - Quality"])

    async def pick_model(self, name):
        self.log.append(("model", name))
        self.model = name


def _walk_settings(monkeypatch, tmp_path):
    async def opened(page, label="settings"):
        return ""

    monkeypatch.setattr(survey.composer, "_open_settings", opened)
    walker = _SettingsWalker(tmp_path)
    asyncio.run(walker.walk_settings())
    return walker


def test_the_settings_panel_is_recorded_on_the_video_tab_whatever_tab_it_opened_on(monkeypatch, tmp_path):
    """Survey 2026-09-30 reported Frames and Ingredients gone and three new ratios: the panel had opened on Image."""
    walker = _walk_settings(monkeypatch, tmp_path)

    recorded = [entry for entry in walker.log if entry[0] == "record"]
    assert recorded == [("record", "composer settings", "Video")], walker.log


def test_the_walk_leaves_the_composer_on_the_first_model_at_x1(monkeypatch, tmp_path):
    """The last priced cell (x4 of the dearest model) outran the balance, so the composer pages that follow were
    recorded with Flow's insufficient-credits warning in place of Start generation."""
    walker = _walk_settings(monkeypatch, tmp_path)

    assert (walker.tab, walker.model, walker.count) == ("Video", "Omni 1.1 Flash", "x1"), walker.log


def test_the_first_model_is_picked_back_inside_the_first_mode(monkeypatch, tmp_path):
    """Review of plan AF: the walk ends inside the last mode, which need not offer the first mode's first model."""
    walker = _walk_settings(monkeypatch, tmp_path)

    assert walker.log[-4:] == [
        ("radio", "Video"),
        ("radio", "Frames"),
        ("model", "Omni 1.1 Flash"),
        ("radio", "x1"),
    ], walker.log
