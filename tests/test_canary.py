from video.probes import canary

HEALTHY = {
    "custom_elements": ["flow-project-page", "flow-prompt-box", "flow-scene-builder", "flow-credit-banner"],
    "composer_buttons": ["Settings trigger", "Start generation", "Agent", "Add ingredients"],
    "settings_text": "Image Video Frames Ingredients 9:16 16:9 x1 Generating will use 10 credits",
    "grid_rpcids": ["UpteDb", "nzlxg", "other"],
    "project_rpcids": ["Zzl0ze", "ngNC2"],
}


def test_a_healthy_page_reports_no_drift():
    findings = canary.compare(HEALTHY)
    assert [f["name"] for f in findings if f["status"] == "FAIL"] == []
    assert len(findings) >= 8, "every anchor the driver depends on gets its own row"


def test_new_things_on_the_page_are_not_drift():
    observed = {
        **HEALTHY,
        "composer_buttons": [*HEALTHY["composer_buttons"], "Brand new button", "Another one"],
        "custom_elements": [*HEALTHY["custom_elements"], "flow-something-new"],
    }
    assert [f for f in canary.compare(observed) if f["status"] == "FAIL"] == []


def test_a_missing_shell_element_fails_and_names_itself():
    observed = {**HEALTHY, "custom_elements": ["flow-prompt-box", "flow-scene-builder"]}
    failed = [f for f in canary.compare(observed) if f["status"] == "FAIL"]
    assert len(failed) == 1
    assert "flow-project-page" in failed[0]["detail"]
    assert failed[0]["reason"], "a failing row has to say what breaks"


def test_a_renamed_submit_button_fails():
    observed = {**HEALTHY, "composer_buttons": ["Settings trigger", "Create video", "Agent"]}
    failed = [f["name"] for f in canary.compare(observed) if f["status"] == "FAIL"]
    assert "submit button" in failed


def test_a_price_line_that_stops_matching_the_driver_regex_fails():
    # The driver refuses to submit when it cannot read this line, so losing it stops all spending.
    observed = {**HEALTHY, "settings_text": "Image Video Frames Ingredients 9:16 x1 Costs 10 tokens"}
    failed = [f["name"] for f in canary.compare(observed) if f["status"] == "FAIL"]
    assert "price line" in failed


def test_a_dropped_composer_mode_fails_and_says_which():
    observed = {**HEALTHY, "settings_text": "Image Video Ingredients 9:16 x1 Generating will use 10 credits"}
    failed = [f for f in canary.compare(observed) if f["status"] == "FAIL"]
    assert [f["name"] for f in failed] == ["composer modes"]
    assert "Frames" in failed[0]["detail"]


def test_a_missing_rpc_fails_per_view():
    observed = {**HEALTHY, "project_rpcids": ["ngNC2"], "grid_rpcids": ["UpteDb"]}
    failed = [f["name"] for f in canary.compare(observed) if f["status"] == "FAIL"]
    assert "listing rpc" in failed and "credits rpc" in failed


def test_every_check_carries_a_reason_and_a_stable_name():
    names = [check.name for check in canary.CHECKS]
    assert len(names) == len(set(names)), names
    assert all(check.reason.strip() for check in canary.CHECKS)


def test_the_editor_target_is_the_newest_finished_video():
    # flow-scene-builder only exists on /edit/<media>, so the canary has to pick something to open.
    # A picture or an unfinished job would open an editor that cannot run extend or an Omni edit.
    records = [
        {"id": "old", "kind": "video", "status": 3, "created": 10},
        {"id": "new", "kind": "video", "status": 3, "created": 40},
        {"id": "img", "kind": "image", "status": 3, "created": 99},
        {"id": "busy", "kind": "video", "status": 1, "created": 50},
    ]
    assert canary.editor_target(records) == "new"
    assert canary.editor_target([{"id": "img", "kind": "image", "status": 3, "created": 99}]) is None
    assert canary.editor_target([]) is None


def test_a_project_with_no_video_skips_the_editor_check_instead_of_failing_it():
    observed = {**HEALTHY, "custom_elements": ["flow-project-page", "flow-prompt-box"], "editor": None}
    editor = [f for f in canary.compare(observed) if f["name"] == "flow-scene-builder element"]
    assert editor[0]["status"] == "SKIP"
    assert canary.exit_code(canary.compare(observed)) == 0


def test_the_report_exits_non_zero_only_on_drift():
    assert canary.exit_code(canary.compare(HEALTHY)) == 0
    broken = canary.compare({**HEALTHY, "composer_buttons": []})
    assert canary.exit_code(broken) == 1
