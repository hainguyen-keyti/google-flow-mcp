"""The Flow survey (plan AC): walk Flow's pages, read their UI labels and the selectors this repo steers by, and compare
with the baselines in the repo, so a Flow update is found by a machine instead of by a failed paid run."""

import ast
import asyncio
import inspect
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
    current["video"]["models"]["omni-1.2-flash"]["price_x1"]["720p 10s"] = 16
    current["video"]["models"]["veo-ultra"] = {
        "label": "Veo 3.1 - Ultra",
        "resolutions": [],
        "durations": [],
        "price_x1": {"": 200},
        "modes": ["Frames"],
    }
    del current["video"]["models"]["veo-lite"]
    current["image"]["aspects"].append("21:9")

    found = survey.compare_options(base, current)

    assert ("price changed", "omni-1.2-flash", "720p 10s: 30 -> 16") in found
    assert ("model added", "veo-ultra", "Veo 3.1 - Ultra") in found
    assert ("model removed", "veo-lite", "Veo 3.1 - Lite") in found
    assert ("image option changed", "aspects", "added ['21:9']") in found
    assert survey.compare_options(base, json.loads(json.dumps(base))) == []


def test_the_option_walk_becomes_the_options_file_gen_video_reads():
    """The walk's raw shape as the survey of 2026-11-09 measured it on build YyfdEJNu4Zk: Omni 1.2 Flash's Length
    slider walked into a length row (3-10 s, 1 credit a second at 360p and 3 at 720p), and the three models Flow marks
    "Leaving Dec 5" left out of the file gen_video reads (decided 2026-11-09)."""

    def per_second(lengths, per_s):
        # Past 10 s each second costs twice the base rate (survey of 2026-11-09 on the default account).
        prices = {
            f"{r} {d}s x{c}": per_s[r] * (min(d, 10) + 2 * max(d - 10, 0)) * c
            for r in per_s
            for d in lengths
            for c in (1, 2, 3, 4)
        }
        return {
            "rows": [list(per_s), [f"{d}s" for d in lengths], ["x1", "x2", "x3", "x4"]],
            "prices": prices,
        }

    def veo(x1):
        return {"rows": [["x1", "x2", "x3", "x4"]], "prices": {f"x{c}": x1 * c for c in (1, 2, 3, 4)}}

    old_omni = {
        "rows": [["360p", "720p"], ["4s", "6s", "8s", "10s"], ["x1", "x2", "x3", "x4"]],
        "prices": {"360p 4s x1": 4, "720p 10s x1": 15},
    }
    per_mode = {
        "Omni 1.2 Flash": per_second(range(3, 16), {"360p": 1, "720p": 3}),
        "Omni 1.1 Flash Leaving Dec 5": old_omni,
        "Veo 3.1 - Lite": veo(10),
        "Veo 3.1 - Fast Leaving Dec 5": veo(20),
        "Veo 3.1 - Quality Leaving Dec 5": veo(100),
    }
    walk = {
        "measured": video.OPTIONS["measured"],
        "video": {"aspects": ["16:9", "9:16"], "modes": {"Frames": per_mode, "Ingredients": per_mode}},
        "image": {
            "models": ["Nano Banana Pro", "Nano Banana 2 Lite", "Nano Banana 2.1"],
            "aspects": ["16:9", "4:3", "1:1", "3:4", "9:16"],
            "counts": [1, 2, 3, 4],
            "price_x1": 0,
            # The default account's Image tab, 2026-11-09.
            "rows": [["Low", "Medium", "High"]],
        },
    }

    options = survey.options_from_walk(walk)

    assert sorted(options["video"]["models"]) == ["omni-1.2-flash", "veo-lite"]
    assert options["video"] == video.OPTIONS["video"]
    assert options["image"] == video.OPTIONS["image"]


def test_a_model_flow_marks_as_leaving_is_left_out_whatever_its_name():
    walk = _walk_with(
        video={
            "aspects": ["16:9"],
            "modes": {
                "Frames": {
                    "Veo 3.1 - Lite": {"rows": [["x1"]], "prices": {"x1": 10}},
                    "Veo 4 - Preview Leaving Jan 9": {"rows": [["x1"]], "prices": {"x1": 50}},
                }
            },
        }
    )
    assert list(survey.options_from_walk(walk)["video"]["models"]) == ["veo-lite"]


def test_a_model_nobody_has_named_yet_gets_a_name_from_its_label():
    assert survey.slug("Veo 3.2 - Ultra") == "veo-3-2-ultra"
    assert survey.slug("Omni 1.2 Flash") == "omni-1.2-flash"


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


def test_the_scene_editor_is_recorded_as_the_scene_tools_open_it(monkeypatch, tmp_path):
    # Recorded as its "Loading..." shell twice on 2026-10-08: the title box, the clips and the composer read as lost.
    log = []

    async def opened(session, project_id, scene_id):
        log.append(("open", project_id, scene_id))

    async def agreed(page, expected, scene_id):
        log.append(("clips", expected, scene_id))

    async def recorded(self, route, scope=None):
        log.append(("record", route))

    monkeypatch.setattr(survey.scenes, "_open_scene", opened)
    monkeypatch.setattr(survey.scenes, "_page_agrees", agreed)
    monkeypatch.setattr(
        survey.scenes, "clips_from_listing", lambda listing, scene_id: [{"id": "c1"}, {"id": "c2"}]
    )
    monkeypatch.setattr(survey.Walker, "record", recorded)
    walker = survey.Walker(type("S", (), {"page": None})(), "P", tmp_path)

    asyncio.run(walker.scene_editor({}, "S1"))

    assert log == [("open", "P", "S1"), ("clips", 2, "S1"), ("record", "scene editor")], log
    source = inspect.getsource(survey.Walker._walk)
    assert "scene_editor(" in source and "scene/{" not in source, (
        "the walk must open a scene through the driver"
    )


def test_the_walk_settles_one_composer_mode_before_recording_the_project_and_the_sidebar():
    # Review 2026-10-08 (B5): the composer's leftover mode made 31 of the 72 differences of the survey.
    import inspect

    source = inspect.getsource(survey.Walker._walk)
    assert source.index("settle_mode()") < source.index('record("project")'), (
        "the mode is settled after the project"
    )


def test_settling_puts_the_composer_on_video_the_first_mode_the_first_model_and_x1(monkeypatch, tmp_path):
    # Survey 2026-10-12: the day's runs left the account's composer on Image (Nano Banana 2.1), where the mode radio
    # is not shown, so a settle that clicked the mode alone recorded the project and six sidebar pages with the Image
    # composer (32 of the 34 differences).
    opened = []

    async def opened_settings(page, label="settings"):
        opened.append(label)
        return ""

    monkeypatch.setattr(survey.composer, "_open_settings", opened_settings)
    walker = _SettingsWalker(tmp_path)

    asyncio.run(walker.settle_mode())

    first = json.loads(survey.OPTIONS_BASELINE.read_text(encoding="utf-8"))["video"]["modes"][0]
    assert opened == ["survey"], opened
    assert walker.log == [
        ("radio", "Video"),
        ("radio", first),
        ("model", "Omni 1.1 Flash"),
        ("radio", "x1"),
    ], walker.log


class _NoVideoTabWalker:
    """A panel left on Image whose Video tab cannot be found (a renamed tab, say)."""

    @staticmethod
    def make(tmp_path):
        walker = _SettingsWalker(tmp_path)
        clicked = walker.radio

        async def radio(text):
            if text == "Video":
                walker.log.append(("radio", text))
                return False
            return await clicked(text)

        walker.radio = radio
        return walker


def test_a_composer_that_cannot_reach_the_video_tab_is_not_settled_and_its_model_is_left_alone(tmp_path):
    # Review of plan AS (D2): the settle went on after a Video tab it could not find and picked the first model of
    # the Image tab's menu, a lasting change to the account's image model.
    walker = _NoVideoTabWalker.make(tmp_path)

    settled = asyncio.run(walker.settle())

    assert settled is False
    assert not [entry for entry in walker.log if entry[0] == "model"], walker.log
    assert walker.tab == "Image"


def test_the_settings_walk_stops_when_the_composer_cannot_be_settled(monkeypatch, tmp_path):
    async def opened(page, label="settings"):
        return ""

    monkeypatch.setattr(survey.composer, "_open_settings", opened)
    walker = _NoVideoTabWalker.make(tmp_path)

    with pytest.raises(survey.SurveyIncomplete, match="Video tab"):
        asyncio.run(walker.walk_settings())
    assert not [entry for entry in walker.log if entry[0] == "record"], walker.log


def test_the_walk_stops_before_any_page_when_the_composer_cannot_be_settled(monkeypatch, tmp_path):
    class _Session:
        page = _SettingsPage()

        def project_url(self, project_id):
            return f"https://flow/project/{project_id}"

        async def goto(self, url, ready=None):
            return None

    async def unsettled(self):
        return False

    recorded = []

    async def record(self, route, scope=None):
        recorded.append(route)

    monkeypatch.setattr(survey.Walker, "settle_mode", unsettled)
    monkeypatch.setattr(survey.Walker, "record", record)
    walker = survey.Walker(_Session(), "P", tmp_path)

    with pytest.raises(survey.SurveyIncomplete, match="Video tab"):
        asyncio.run(walker._walk([], []))
    assert recorded == []


def test_the_check_settles_the_composer_before_it_records_the_project_page():
    # Review of plan AS (D4): the check tests fake the settle away, so only the order in the source pins it.
    import inspect

    source = inspect.getsource(survey.check)
    assert source.index("settle_mode()") < source.index('record("project")')


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
            (
                "scene",
                any(
                    not s.get("trashed") and survey.scenes.clips_from_listing(listing, s["scene_id"])
                    for s in survey.parsers.scenes_from_listing(listing)
                ),
            ),
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


def test_a_route_is_recorded_only_once_flows_loading_placeholder_is_gone(tmp_path):
    # The scene editor recorded on 2026-10-08 was its "Loading..." shell: the title box, the clips and the composer
    # were counted as lost and the baseline would have kept that.
    class _Page:
        keyboard = _CheckKeys()

        def __init__(self):
            self.loading = 2
            self.waited = []

        async def wait_for_timeout(self, ms):
            self.waited.append(ms)

        async def evaluate(self, js, *args):
            if js == survey._LOADING_JS:
                self.loading -= 1
                return self.loading >= 0
            return 1 if js == survey._COUNT_JS else []

        async def screenshot(self, path):
            Path(path).write_bytes(b"")

    page = _Page()
    walker = survey.Walker(type("S", (), {"page": page})(), "P", tmp_path)

    asyncio.run(walker.record("scene editor"))

    assert page.waited[:2] == [1_000, 1_000] and "scene editor" in walker.routes, page.waited


def test_labels_that_came_or_went_are_said_but_a_lost_selector_is_drift():
    assert (
        survey.ui_drifted([("label added", "home", "Opt in"), ("label removed", "home", "Explore Tools")])
        is False
    )
    assert (
        survey.ui_drifted([("label added", "home", "x"), ("selector lost", "project", "flow.agent.CHIP")])
        is True
    )


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
    project = {name: [_fixture(name)] for name in ("ngNC2", "yBhWQ", "HTrJv", "tRARke")}
    # Plan AU: a listing of the record shape Flow sends since 2026-10-22; Zzl0ze.json is superseded in the baseline.
    project["Zzl0ze"] = [_fixture("Zzl0ze_20261022_test_project")]
    modes = []

    async def fake_grid(session, settle=6.0, **kwargs):
        return grid

    async def fake_capture(session, action, *, settle, wants=()):
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
    monkeypatch.setattr(survey.Walker, "model_names", lambda self: _no_models())
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


async def _no_models():
    return None, []


def test_check_with_a_project_compares_the_listing_and_a_moved_status_cell_is_drift(monkeypatch, tmp_path):
    # Pins the path from a real wire finding to `drift` (technical review of plan AQ: six mutants in wire.py and
    # survey.check survived with the whole report faked).
    # Plan AU review (B1): a listing of the live shape, which is not drift by itself, so the finding is the move.
    moved = _fixture("Zzl0ze_20261022_test_project")
    for record in moved[2]:
        if record[5][8]:
            record[5][8] = [None, *record[5][8]]
    grid = {"UpteDb": [_fixture("UpteDb")], "nzlxg": [_fixture("nzlxg")], "Yizz8d": [_fixture("Yizz8d")]}
    project = {
        "Zzl0ze": [moved],
        **{name: [_fixture(name)] for name in ("ngNC2", "yBhWQ", "HTrJv", "tRARke")},
    }

    async def fake_grid(session, settle=6.0, **kwargs):
        return grid

    async def fake_capture(session, action, *, settle, wants=()):
        await action()
        return project

    async def fake_set_mode(session, project_id, enabled):
        return {"enabled": enabled, "was": False}

    async def opened(page, label="settings"):
        return ""

    monkeypatch.setattr(survey.reader, "grid", fake_grid)
    monkeypatch.setattr(survey, "capture", fake_capture)
    monkeypatch.setattr(survey.agent, "set_mode", fake_set_mode)
    monkeypatch.setattr(survey.composer, "_open_settings", opened)
    monkeypatch.setattr(survey.Walker, "radio", lambda self, text: _settled())
    monkeypatch.setattr(survey.Walker, "model_names", lambda self: _no_models())
    monkeypatch.setattr(survey.version, "current", None)
    base = json.loads(survey.UI_BASELINE.read_text(encoding="utf-8"))
    for route in base["routes"].values():
        route["labels"] = []
        route["selectors"] = {name: 1 for name in route["selectors"]}
    monkeypatch.setattr(survey, "_baseline_ui", lambda: base | {"build": "Zz9.1.O"})

    report = asyncio.run(survey.check(_CheckSession(), "P1", out_root=tmp_path))

    assert report["drift"] is True, report
    assert any(
        where.startswith("Zzl0ze[2][*][5][8]") and kind in survey.wire.DRIFT
        for kind, where, _ in report["wire"]
    ), report["wire"]


def test_a_check_whose_composer_cannot_be_settled_reports_the_project_page_as_a_route_error(
    monkeypatch, tmp_path
):
    # Review of plan AS (D2): a project page recorded with an unsettled composer reads as a label change, and a
    # settle that raised would lose the wire report; a route error is drift and keeps the rest of the check.
    grid = {"UpteDb": [_fixture("UpteDb")], "nzlxg": [_fixture("nzlxg")], "Yizz8d": [_fixture("Yizz8d")]}
    project = {name: [_fixture(name)] for name in ("ngNC2", "yBhWQ", "HTrJv", "tRARke")}
    # Plan AU review (B1): a live-shape listing, so the only drift left to find is the route error.
    project["Zzl0ze"] = [_fixture("Zzl0ze_20261022_test_project")]
    recorded = []

    async def fake_grid(session, settle=6.0, **kwargs):
        return grid

    async def fake_capture(session, action, *, settle, wants=()):
        await action()
        return project

    async def fake_set_mode(session, project_id, enabled):
        return {"enabled": enabled, "was": False}

    async def unsettled(self):
        return False

    async def record(self, route, scope=None):
        recorded.append(route)

    monkeypatch.setattr(survey.reader, "grid", fake_grid)
    monkeypatch.setattr(survey, "capture", fake_capture)
    monkeypatch.setattr(survey.agent, "set_mode", fake_set_mode)
    monkeypatch.setattr(survey.Walker, "settle_mode", unsettled)
    monkeypatch.setattr(survey.Walker, "record", record)
    monkeypatch.setattr(survey.version, "current", None)
    monkeypatch.setattr(survey, "_baseline_ui", lambda: {"routes": {}, "build": "Zz9.1.O"})

    report = asyncio.run(survey.check(_CheckSession(), "P1", out_root=tmp_path))

    assert "project" not in recorded, recorded
    assert [kind for kind, route, _ in report["ui"] if route == "project"] == ["route error"], report["ui"]
    assert report["drift"] is True, report


def test_a_check_whose_grid_lacks_the_project_list_reports_it_instead_of_raising(monkeypatch, tmp_path):
    # Review of plan AT (D8): UpteDb was lost on the default account's grid on 2026-10-21, and `check` raised at
    # `one(frames, "UpteDb")` before any report.
    # Re-review: without the project list the titles cannot be filtered from the home page's labels (a title leaked
    # as "label added"), so the home page is not recorded either and says so as a route error.
    grid = {"nzlxg": [_fixture("nzlxg")], "Yizz8d": [_fixture("Yizz8d")]}
    recorded = []

    async def fake_grid(session, settle=6.0, **kwargs):
        return grid

    async def record(self, route, scope=None):
        recorded.append(route)

    monkeypatch.setattr(survey.reader, "grid", fake_grid)
    monkeypatch.setattr(survey.Walker, "record", record)
    monkeypatch.setattr(survey.version, "current", None)
    monkeypatch.setattr(survey, "_baseline_ui", lambda: {"routes": {}, "build": "Zz9.1.O"})

    report = asyncio.run(survey.check(_CheckSession(), None, out_root=tmp_path))

    assert recorded == [], recorded
    assert ["rpc not heard", "UpteDb", "expected on the grid view"] in report["wire"], report["wire"]
    assert [kind for kind, route, _ in report["ui"] if route == "home"] == ["route error"], report["ui"]
    assert report["drift"] is True


def test_a_check_whose_listing_was_not_heard_reports_it_as_drift_instead_of_raising(monkeypatch, tmp_path):
    # Measured 2026-10-21: Chrome 155 lost Zzl0ze on a project load and `flow check --project` died with a traceback
    # at `one(more, "Zzl0ze")`, leaving no report at all, while the wire check already knows to say "rpc not heard".
    # Without the listing the page's titles cannot be filtered from its labels, so the page is not recorded either,
    # and the owner's Agent mode is not touched.
    grid = {"UpteDb": [_fixture("UpteDb")], "nzlxg": [_fixture("nzlxg")], "Yizz8d": [_fixture("Yizz8d")]}
    project = {name: [_fixture(name)] for name in ("ngNC2", "yBhWQ", "HTrJv", "tRARke")}
    recorded, modes = [], []

    async def fake_grid(session, settle=6.0, **kwargs):
        return grid

    async def fake_capture(session, action, *, settle, wants=()):
        await action()
        return project

    async def fake_set_mode(session, project_id, enabled):
        modes.append(enabled)
        return {"enabled": enabled, "was": True}

    async def record(self, route, scope=None):
        recorded.append(route)

    monkeypatch.setattr(survey.reader, "grid", fake_grid)
    monkeypatch.setattr(survey, "capture", fake_capture)
    monkeypatch.setattr(survey.agent, "set_mode", fake_set_mode)
    monkeypatch.setattr(survey.Walker, "record", record)
    monkeypatch.setattr(survey.version, "current", None)
    monkeypatch.setattr(survey, "_baseline_ui", lambda: {"routes": {}, "build": "Zz9.1.O"})

    report = asyncio.run(survey.check(_CheckSession(), "P1", out_root=tmp_path))

    assert recorded == ["home"], recorded
    assert ["rpc not heard", "Zzl0ze", "expected on the project view"] in report["wire"], report["wire"]
    assert [kind for kind, route, _ in report["ui"] if route == "project"] == ["route error"], report["ui"]
    assert report["drift"] is True and modes == [], report


def test_a_write_through_report_is_refused_on_a_skipped_route_and_the_picker_skips_a_project_lacking_a_scene(
    monkeypatch, tmp_path
):
    # Technical review of plan AQ (S6): the write guard was tested only by calling it directly, and the project
    # picker not at all.
    monkeypatch.setattr(survey, "UI_BASELINE", tmp_path / "flow_ui.json")
    monkeypatch.setattr(survey, "OPTIONS_BASELINE", tmp_path / "flow_options.json")
    (tmp_path / "flow_ui.json").write_text(json.dumps({"routes": {"home": {"labels": [], "selectors": {}}}}))
    (tmp_path / "flow_options.json").write_text(
        json.dumps(survey.options_from_walk(_walk_with())), encoding="utf-8"
    )
    result = {
        "ui": {"routes": {"home": {"labels": [], "selectors": {}}}, "skipped": {"scene editor": "no scene"}},
        "walk": _walk_with(),
    }
    with pytest.raises(survey.SurveyIncomplete, match="scene editor"):
        survey.report(result, write=True)
    assert "scene editor" not in json.loads((tmp_path / "flow_ui.json").read_text())["routes"]

    walked = []
    listings = {"p-no-scene": _fixture("Zzl0ze"), "p-full": _fixture("Zzl0ze_character")}

    async def fake_grid(session, settle=6.0, **kwargs):
        return {"UpteDb": [[[[pid, ["t", None, [1, 0], None, None]] for pid in listings]]]}

    async def fake_capture(session, action, *, settle, wants=()):
        await action()
        return {"Zzl0ze": [listings[session.urls[-1].rsplit("/", 1)[-1]]]}

    async def fake_survey(session, project_id, out_root=None):
        walked.append(project_id)
        return {"ui": {"routes": {}, "skipped": {}}, "walk": _walk_with(), "folder": "x"}

    monkeypatch.setattr(survey.reader, "grid", fake_grid)
    monkeypatch.setattr(survey, "capture", fake_capture)
    monkeypatch.setattr(survey, "survey", fake_survey)
    monkeypatch.setattr(
        survey, "missing_for_a_walk", lambda listing: [] if listing is listings["p-full"] else ["scene"]
    )

    class _Factory:
        async def __aenter__(self):
            return _CheckSession()

        async def __aexit__(self, *exc):
            return None

    survey.run_sync(_Factory, None, False)

    assert walked == ["p-full"], walked


def test_a_selector_the_baseline_counted_that_now_counts_zero_refuses_a_write():
    # Technical review of plan AQ (S8): a route recorded as a blank page would have become the baseline and
    # disarmed `selector lost` for that selector for good.
    routes = _complete_routes()
    routes["scene editor"]["selectors"] = {"flow.scenes.CLIPS": 0, "flow.scenes.TITLE_BOX": 1}
    base = {"routes": {name: {"labels": [], "selectors": {}} for name in routes}}
    base["routes"]["scene editor"]["selectors"] = {"flow.scenes.CLIPS": 2, "flow.scenes.TITLE_BOX": 1}

    with pytest.raises(survey.SurveyIncomplete, match="flow.scenes.CLIPS"):
        survey.check_writable({"routes": routes}, survey.options_from_walk(_walk_with()), base)


def test_check_without_a_project_reads_the_grid_only_and_touches_no_project(monkeypatch, tmp_path):
    async def fake_grid(session, settle=6.0, **kwargs):
        return {"UpteDb": [_fixture("UpteDb")], "nzlxg": [_fixture("nzlxg")], "Yizz8d": [_fixture("Yizz8d")]}

    async def must_not(*args, **kwargs):
        raise AssertionError("a project was opened without being asked for")

    monkeypatch.setattr(survey.reader, "grid", fake_grid)
    monkeypatch.setattr(survey, "capture", must_not)
    monkeypatch.setattr(survey.agent, "set_mode", must_not)
    monkeypatch.setattr(survey.version, "current", None)
    base = json.loads(survey.UI_BASELINE.read_text(encoding="utf-8"))
    base["routes"]["home"] = {"labels": [], "selectors": {}}
    base.pop("build", None)  # a baseline walked before builds were recorded
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

    async def slider_lengths(self):
        return []


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


def test_the_settings_panel_is_recorded_after_the_same_settle_whatever_model_the_last_run_left(
    monkeypatch, tmp_path
):
    # Survey 2026-10-12: recorded on the Veo model the last run left, the panel lacked Omni's lengths and
    # resolutions (6 of the 34 differences).
    walker = _walk_settings(monkeypatch, tmp_path)

    recorded = walker.log.index(("record", "composer settings", "Video"))
    assert walker.log[:recorded] == [
        ("radio", "Video"),
        ("radio", "Frames"),
        ("model", "Omni 1.1 Flash"),
        ("radio", "x1"),
    ], walker.log


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


class _SidebarTitle:
    def __init__(self, page, title):
        self.page, self.title = page, title

    @property
    def first(self):
        return self

    async def count(self):
        return 0 if self.title is None else 1

    async def inner_text(self):
        return self.title

    async def get_attribute(self, name):
        return None

    async def click(self, timeout=None):
        self.page.clicked.append(("title", self.title))


class _SidebarRow:
    def __init__(self, page, title, text):
        self.page, self.title, self.text = page, title, text

    def locator(self, selector):
        assert selector == survey.SIDEBAR_TITLE, selector
        return _SidebarTitle(self.page, self.title)

    async def inner_text(self):
        return self.text

    async def get_attribute(self, name):
        return None

    async def click(self, timeout=None):
        self.page.clicked.append(("row", self.text))


class _SidebarRows:
    def __init__(self, page):
        self.page = page

    async def count(self):
        return len(self.page.rows)

    def nth(self, index):
        return self.page.rows[index]


class _SidebarPage:
    """Flow's project sidebar as measured 2026-10-22 (out/at/sidebar_probe.out): each row holds its title in a
    [matlistitemtitle] span, and Library's row also holds the add button of "Create collection", so its text ends in
    that button's icon word."""

    def __init__(self):
        self.clicked = []
        self.rows = [
            _SidebarRow(self, "All media", "dashboard\nAll media"),
            _SidebarRow(self, "Library", "folder\nLibrary\nadd"),
            _SidebarRow(self, "Collapse", "left_panel_close\nCollapse"),
            _SidebarRow(self, None, "drive_folder_upload\nUploads"),
        ]

    def locator(self, selector, **kwargs):
        assert selector == survey.SIDEBAR, selector
        return _SidebarRows(self)

    async def wait_for_timeout(self, ms):
        return None


def test_a_sidebar_row_is_named_by_its_title_not_by_a_control_inside_it():
    # Survey 2026-10-22: the walk named Library's row "sidebar add", the last line of its text, the icon of the
    # create-collection button inside the row; a row without a title span keeps the last line of its text.
    page = _SidebarPage()

    assert asyncio.run(survey.sidebar_items(page)) == ["All media", "Library", "Collapse", "Uploads"]


def test_the_walk_names_the_sidebar_rows_through_their_titles():
    import inspect

    assert "sidebar_items(" in inspect.getsource(survey.Walker._walk)


def test_a_sidebar_row_is_opened_by_its_title_never_through_a_control_inside_it(monkeypatch, tmp_path):
    # Library's row holds the add button that creates a collection: clicking the row as a whole aims at its middle,
    # which is the title today, but the walk must not depend on where Flow puts that button.
    page = _SidebarPage()
    session = type("S", (), {"page": page, "project_url": lambda self, p: f"u/{p}", "goto": _async_none})()
    recorded = []

    async def record(self, route, scope=None):
        recorded.append(route)

    monkeypatch.setattr(survey.Walker, "record", record)
    walker = survey.Walker(session, "P", tmp_path)

    asyncio.run(walker.sidebar(1, "sidebar Library"))

    assert page.clicked == [("title", "Library")], page.clicked
    assert recorded == ["sidebar Library"]


def test_a_sidebar_row_without_a_title_is_opened_as_a_whole(monkeypatch, tmp_path):
    # Review of plan AU (D1): the fallback of a row whose title span is missing.
    page = _SidebarPage()
    session = type("S", (), {"page": page, "project_url": lambda self, p: f"u/{p}", "goto": _async_none})()

    async def record(self, route, scope=None):
        return None

    monkeypatch.setattr(survey.Walker, "record", record)

    asyncio.run(survey.Walker(session, "P", tmp_path).sidebar(3, "sidebar Uploads"))

    assert page.clicked == [("row", "drive_folder_upload\nUploads")], page.clicked


async def _async_none(*args, **kwargs):
    return None


class _SliderWalker(_SettingsWalker):
    """Omni 1.2 Flash's pane as measured on 2026-11-09: a resolution row and a count row, its length on a slider."""

    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.model, self.resolution, self.length = "Omni 1.2 Flash", "720p", 10

    async def radio(self, text):
        if text in ("360p", "720p"):
            self.resolution = text
        return await super().radio(text)

    async def pane(self):
        state = await super().pane()
        if self.tab == "Image":
            return state
        tabs, modes, aspects, counts = state["groups"]
        resolutions = [{"t": f"{r}info", "on": r == self.resolution} for r in ("360p", "720p")]
        per_second = {"360p": 1, "720p": 3}[self.resolution]
        return {"groups": [tabs, modes, aspects, resolutions, counts], "price": per_second * self.length}

    async def model_names(self):
        return None, (["Nano Banana 2"] if self.tab == "Image" else ["Omni 1.2 Flash"])

    async def slider_lengths(self):
        return [3, 4, 5] if self.tab == "Video" else []

    async def set_length(self, seconds):
        self.log.append(("length", seconds))
        self.length = seconds


def test_the_walk_reads_the_length_slider_into_a_length_row_priced_at_every_second(monkeypatch, tmp_path):
    """Plan AV: Omni 1.2 Flash's length is a slider, so the survey walks it a second at a time and files each price
    under its own length, the row options_from_walk already maps."""

    async def opened(page, label="settings"):
        return ""

    monkeypatch.setattr(survey.composer, "_open_settings", opened)
    walker = _SliderWalker(tmp_path)
    walk = asyncio.run(walker.walk_settings())

    cell = walk["video"]["modes"]["Frames"]["Omni 1.2 Flash"]
    assert cell["rows"] == [["360p", "720p"], ["3s", "4s", "5s"], ["x1", "x4"]]
    assert {key: cell["prices"][key] for key in ("360p 3s x1", "360p 5s x1", "720p 4s x1")} == {
        "360p 3s x1": 3,
        "360p 5s x1": 5,
        "720p 4s x1": 12,
    }
    assert ("length", 3) in walker.log and ("length", 5) in walker.log


def test_the_image_tab_is_read_by_what_each_row_holds_not_by_its_place():
    """Survey of 2026-11-09 on the default account: the Image tab grew a Low/Medium/High row between the aspects and
    the counts, and the walk read 'Low' as a count. Each row is told by its own labels, and an unknown one is kept."""
    groups = [
        [{"t": "imageImage", "on": True}, {"t": "videocamVideo", "on": False}],
        [{"t": "crop_16_916:9", "on": False}, {"t": "crop_9_169:16", "on": True}],
        [{"t": "Low", "on": False}, {"t": "Medium", "on": False}, {"t": "High", "on": True}],
        [{"t": "x1", "on": True}, {"t": "x2", "on": False}],
    ]

    image = survey.image_options(groups, ["Nano Banana Pro"], 0)

    assert image == {
        "models": ["Nano Banana Pro"],
        "aspects": ["16:9", "9:16"],
        "counts": [1, 2],
        "price_x1": 0,
        "rows": [["Low", "Medium", "High"]],
    }


def test_an_image_tab_without_extra_rows_reads_as_before():
    groups = [
        [{"t": "imageImage", "on": True}],
        [{"t": "crop_square1:1", "on": True}],
        [{"t": "x1", "on": True}, {"t": "x4", "on": False}],
    ]
    assert survey.image_options(groups, ["Nano Banana 2.1"], 0) == {
        "models": ["Nano Banana 2.1"],
        "aspects": ["1:1"],
        "counts": [1, 4],
        "price_x1": 0,
    }


def test_a_new_image_row_is_named_in_the_comparison():
    base = json.loads(json.dumps(video.OPTIONS))
    base["image"].pop("rows", None)
    current = json.loads(json.dumps(base))
    current["image"]["rows"] = [["Low", "Medium", "High"]]

    found = survey.compare_options(base, current)

    assert ("image option changed", "rows", "added [['Low', 'Medium', 'High']]") in found


def test_flow_check_with_the_network_down_says_so_in_one_line(monkeypatch):
    """Plan AU debt D4: with the network down `flow check` ended in a Playwright traceback. A read that cannot reach
    Flow is one line naming the network error and an exit code, never a traceback."""
    from click.testing import CliRunner
    from playwright.async_api import Error as PlaywrightError

    from video import cli, session

    class _Down:
        def __init__(self, profile):
            self.page = None

        async def __aenter__(self):
            raise PlaywrightError(
                "Page.goto: net::ERR_INTERNET_DISCONNECTED at https://flow.google.com/\nCall log: x"
            )

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr(session, "FlowSession", _Down)

    result = CliRunner().invoke(cli.main, ["flow", "check"])

    assert result.exit_code == 1, result.output
    assert "net::ERR_INTERNET_DISCONNECTED" in result.output and "Traceback" not in result.output, (
        result.output
    )
    assert "Call log" not in result.output, result.output
    assert not isinstance(result.exception, PlaywrightError), result.exception


def test_a_read_of_a_project_flow_cannot_find_is_one_line_too(monkeypatch):
    """Plan AW, D5, measured 2026-11-09: `video flow uploads` on another account's project printed the right words
    under a traceback."""
    from click.testing import CliRunner

    from video import cli

    def not_found(profile, fn):
        raise LookupError(
            "Flow says this was not found: the project is not on this account (profile 'default')"
        )

    monkeypatch.setattr(cli, "_session_read", not_found)

    result = CliRunner().invoke(cli.main, ["flow", "uploads", "P"])

    assert result.exit_code == 1 and "not on this account" in result.output, result.output
    assert "Traceback" not in result.output and not isinstance(result.exception, LookupError), (
        result.exception
    )


def test_a_key_error_in_a_read_keeps_its_traceback(monkeypatch):
    from click.testing import CliRunner

    from video import cli

    def broken(profile, fn):
        raise KeyError("media")

    monkeypatch.setattr(cli, "_session_read", broken)

    result = CliRunner().invoke(cli.main, ["flow", "uploads", "P"])

    assert isinstance(result.exception, KeyError), result.output


def test_a_playwright_error_that_is_not_the_network_keeps_its_traceback(monkeypatch):
    """Review of plan AW, M15: only an unreachable Flow becomes the one-line exit."""
    from click.testing import CliRunner
    from playwright.async_api import Error as PlaywrightError

    from video import cli

    def broken(profile, fn):
        raise PlaywrightError("Locator.click: Element is not attached to the DOM")

    monkeypatch.setattr(cli, "_session_read", broken)

    result = CliRunner().invoke(cli.main, ["flow", "uploads", "P"])

    assert isinstance(result.exception, PlaywrightError), result.output


def test_a_network_error_never_claims_a_change_did_not_happen(monkeypatch):
    """Review of plan AW, N4: the same exit serves writes (project create, delete, upload), where 'nothing was read'
    invites a second create; it says to look before running it again."""
    from click.testing import CliRunner
    from playwright.async_api import Error as PlaywrightError

    from video import cli

    def down(profile, fn):
        raise PlaywrightError("Page.goto: net::ERR_INTERNET_DISCONNECTED at https://flow.google.com/")

    monkeypatch.setattr(cli, "_session_read", down)

    result = CliRunner().invoke(cli.main, ["flow", "uploads", "P"])

    assert result.exit_code == 1 and "nothing was read" not in result.output, result.output
    assert "before running it again" in result.output, result.output
