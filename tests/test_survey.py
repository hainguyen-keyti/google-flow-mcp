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


def _complete_routes():
    base = json.loads(survey.UI_BASELINE.read_text(encoding="utf-8"))["routes"]
    return {name: {"labels": ["x"], "selectors": {}} for name in base}


def test_a_walk_that_skipped_or_never_reached_a_baseline_route_is_never_written():
    # Review 2026-10-08 (B5): a project without a scene walked without the scene editor and `--write` would have
    # dropped that route from the baseline, and the character page with it.
    routes = _complete_routes()
    del routes["scene editor"]
    ui = {"routes": routes, "skipped": {"scene editor": "the project holds no scene"}}
    with pytest.raises(survey.SurveyIncomplete, match="scene editor"):
        survey.check_writable(ui, survey.options_from_walk(_walk_with()))

    with pytest.raises(survey.SurveyIncomplete, match="character page"):
        survey.check_writable(
            {"routes": {k: v for k, v in _complete_routes().items() if k != "character page"}},
            survey.options_from_walk(_walk_with()),
        )


def test_a_complete_walk_is_written_with_the_build_and_the_day_it_ran(monkeypatch, tmp_path):
    monkeypatch.setattr(survey, "UI_BASELINE", tmp_path / "flow_ui.json")
    monkeypatch.setattr(survey, "OPTIONS_BASELINE", tmp_path / "flow_options.json")
    (tmp_path / "flow_ui.json").write_text(json.dumps({"routes": {"home": {"labels": [], "selectors": {}}}}))
    (tmp_path / "flow_options.json").write_text(
        json.dumps(survey.options_from_walk(_walk_with())), encoding="utf-8"
    )
    result = {
        "ui": {"routes": {"home": {"labels": ["Home"], "selectors": {}}}, "skipped": {}},
        "walk": _walk_with(),
        "build": "Ab12.3.O",
    }

    findings, _ = survey.report(result, write=True)

    written = json.loads((tmp_path / "flow_ui.json").read_text(encoding="utf-8"))
    assert written["build"] == "Ab12.3.O" and written["measured"][:2] == "20", written
    assert written["routes"]["home"]["labels"] == ["Home"]
    assert ("route skipped", "x", "") not in findings


def test_a_skipped_route_is_reported_with_its_reason():
    result = {
        "ui": {"routes": {}, "skipped": {"clip editor": "the project holds no finished video"}},
        "walk": _walk_with(),
    }
    findings, _ = survey.report(result, write=False)
    assert ("route skipped", "clip editor", "the project holds no finished video") in findings


def test_the_walk_settles_one_composer_mode_before_recording_the_project_and_the_sidebar():
    # Review 2026-10-08 (B5): the composer's leftover mode made 31 of the 72 differences of the survey.
    import inspect

    source = inspect.getsource(survey.Walker.run)
    assert source.index("settle_mode()") < source.index('record("project")'), (
        "the mode is settled after the project"
    )


def test_settling_the_mode_opens_the_settings_and_picks_the_baseline_first_mode(monkeypatch, tmp_path):
    opened = []

    async def opened_settings(page, label="settings"):
        opened.append(label)
        return ""

    monkeypatch.setattr(survey.composer, "_open_settings", opened_settings)
    walker = _SettingsWalker(tmp_path)

    asyncio.run(walker.settle_mode())

    first = json.loads(survey.OPTIONS_BASELINE.read_text(encoding="utf-8"))["video"]["modes"][0]
    assert opened == ["survey"] and walker.log == [("radio", first)], (opened, walker.log)


def test_what_a_project_lacks_for_a_full_walk_is_named():
    listing = json.loads((Path(__file__).parent / "fixtures" / "rpc" / "Zzl0ze_character.json").read_text())[
        "payload"
    ]
    # The parsers are the authority on the listing; this checks the three questions are asked of it.
    expected = [
        name
        for name, present in (
            (
                "finished video",
                any(
                    r.get("kind") == "video"
                    and r.get("listed")
                    and r.get("status") == survey.clips.DONE_STATUS
                    for r in survey.parsers.records(listing)
                ),
            ),
            ("scene", any(not s.get("trashed") for s in survey.parsers.scenes_from_listing(listing))),
            ("character", bool(survey.parsers.characters_from_listing(listing))),
        )
        if not present
    ]
    assert survey.missing_for_a_walk(listing) == expected
    assert survey.missing_for_a_walk([]) == ["finished video", "scene", "character"]


def _check_report(**change):
    return {
        "build": {"live": "Ab12.3.O", "baseline": "Ab12.3.O", "changed": False},
        "ui": [],
        "wire": [],
        "drift": False,
        "folder": "out/check/x",
    } | change


def _run_check(monkeypatch, report, args):
    from click.testing import CliRunner

    from video import cli

    asked = []

    async def fake_check(session, project_id=None):
        asked.append(project_id)
        return report

    monkeypatch.setattr(survey, "check", fake_check)
    monkeypatch.setattr(cli, "_read", lambda profile, fn: asyncio.run(fn(object())))
    return CliRunner().invoke(cli.main, ["flow", "check", *args]), asked


def test_flow_check_exits_0_on_a_clean_read_and_says_a_new_build(monkeypatch):
    result, asked = _run_check(
        monkeypatch, _check_report(build={"live": "Cd34.5.O", "baseline": "Ab12.3.O", "changed": True}), []
    )

    assert result.exit_code == 0, result.output
    assert "build Cd34.5.O" in result.output and "changed since the baseline" in result.output
    assert asked == [None]


def test_flow_check_exits_1_on_drift_and_names_each_finding(monkeypatch):
    report = _check_report(
        wire=[["kind changed", "Zzl0ze[2][*][3]", "str -> int"]],
        ui=[["selector lost", "project", "flow.composer.SETTINGS"]],
        drift=True,
    )

    result, asked = _run_check(monkeypatch, report, ["--project", "P1"])

    assert result.exit_code == 1, result.output
    assert "kind changed" in result.output and "selector lost" in result.output and "DRIFT" in result.output
    assert asked == ["P1"]


class _CheckSession:
    """A session for `check`: the grid and the listing answer with the fixtures, the page records nothing."""

    def __init__(self):
        self.page = _CheckPage()
        self.urls = []

    async def goto(self, url, *, ready=None, timeout_ms=60_000):
        self.urls.append(url)

    @staticmethod
    def project_url(project_id):
        return f"https://flow.google.com/project/{project_id}"


class _CheckKeys:
    async def press(self, key):
        return None


class _CheckPage:
    keyboard = _CheckKeys()

    async def wait_for_timeout(self, ms):
        return None

    async def evaluate(self, js, *args):
        if js == survey.version.SCRIPTS_JS:
            return ["https://g/k=boq-labs-ai-sandbox.AiSandboxAngularFrontend.en.Zz9.1.O/"]
        if js == survey._COUNT_JS:
            return 1
        return []

    async def screenshot(self, path):
        Path(path).write_bytes(b"")


def _fixture(name):
    return json.loads((Path(__file__).parent / "fixtures" / "rpc" / f"{name}.json").read_text("utf-8"))[
        "payload"
    ]


def test_check_reads_the_build_the_free_replies_and_the_pages_and_reports_no_drift_on_the_fixtures(
    monkeypatch, tmp_path
):
    grid = {"UpteDb": [_fixture("UpteDb")], "nzlxg": [_fixture("nzlxg")], "Yizz8d": [_fixture("Yizz8d")]}
    project = {name: [_fixture(name)] for name in ("Zzl0ze", "ngNC2", "yBhWQ", "HTrJv", "tRARke")}
    modes = []

    async def fake_grid(session, settle=6.0):
        return grid

    async def fake_capture(session, action, *, settle):
        await action()
        return project

    async def fake_set_mode(session, project_id, enabled):
        modes.append(enabled)
        return {"enabled": enabled, "was": True}

    async def opened(page, label="settings"):
        return ""

    monkeypatch.setattr(survey.reader, "grid", fake_grid)
    monkeypatch.setattr(survey, "capture", fake_capture)
    monkeypatch.setattr(survey.agent, "set_mode", fake_set_mode)
    monkeypatch.setattr(survey.composer, "_open_settings", opened)
    monkeypatch.setattr(survey.Walker, "radio", lambda self, text: _settled())
    monkeypatch.setattr(survey.version, "current", None)
    base = json.loads(survey.UI_BASELINE.read_text(encoding="utf-8"))
    # The labels the page answers are empty, so the baseline's labels are made empty too: this is a wire and build
    # test, the label comparison has its own tests above.
    for route in base["routes"].values():
        route["labels"] = []
        route["selectors"] = {name: 1 for name in route["selectors"]}
    monkeypatch.setattr(survey, "_baseline_ui", lambda: base | {"build": "Zz9.1.O"})

    report = asyncio.run(survey.check(_CheckSession(), "P1", out_root=tmp_path))

    assert report["build"] == {"live": "Zz9.1.O", "baseline": "Zz9.1.O", "changed": False}, report["build"]
    assert report["wire"] == [] and report["drift"] is False, report
    assert modes == [False, True], modes


async def _settled():
    return True


def test_check_without_a_project_reads_the_grid_only_and_touches_no_project(monkeypatch, tmp_path):
    async def fake_grid(session, settle=6.0):
        return {"UpteDb": [_fixture("UpteDb")], "nzlxg": [_fixture("nzlxg")], "Yizz8d": [_fixture("Yizz8d")]}

    async def must_not(*args, **kwargs):
        raise AssertionError("a project was opened without being asked for")

    monkeypatch.setattr(survey.reader, "grid", fake_grid)
    monkeypatch.setattr(survey, "capture", must_not)
    monkeypatch.setattr(survey.agent, "set_mode", must_not)
    monkeypatch.setattr(survey.version, "current", None)
    base = json.loads(survey.UI_BASELINE.read_text(encoding="utf-8"))
    base["routes"]["home"] = {"labels": [], "selectors": {}}
    monkeypatch.setattr(survey, "_baseline_ui", lambda: base)

    report = asyncio.run(survey.check(_CheckSession(), None, out_root=tmp_path))

    assert report["build"]["baseline"] is None and report["build"]["changed"] is False
    assert report["ui"] == [] and report["wire"] == [] and report["drift"] is False, report


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
