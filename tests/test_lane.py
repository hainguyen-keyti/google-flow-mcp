from video.flow.lane import verdict


def test_signed_out_when_no_grid_anywhere():
    assert verdict({"project_links": 0}, {"project_links": 0}) == "SIGNED_OUT"


def test_migrated_when_flow_google_com_renders_the_grid():
    assert verdict({"project_links": 0}, {"project_links": 16}) == "MIGRATED"


def test_labs_when_only_labs_renders_the_grid():
    assert verdict({"project_links": 3}, {"project_links": 0}) == "LABS"


def test_migrated_wins_when_both_render():
    assert verdict({"project_links": 3}, {"project_links": 16}) == "MIGRATED"


def test_missing_keys_count_as_no_grid():
    assert verdict({}, {"nav_error": "boom"}) == "SIGNED_OUT"


def test_a_signed_in_account_with_no_projects_is_migrated_not_signed_out():
    """Measured 2026-09-28 on a brand-new PRO account: flow.google.com showed "Google Account: <the account holder>", the PRO
    badge and a lone "New project" button, zero project links, and this verdict said SIGNED_OUT. An empty grid is
    an account that has not made anything yet, not one that is logged out."""
    empty = {"project_links": 0, "new_project_button": 1}

    assert verdict({"project_links": 0}, empty) == "MIGRATED"


def test_no_grid_and_no_new_project_button_is_still_signed_out():
    """The button is what separates an empty account from a logged-out one, so its absence keeps the old answer."""
    assert verdict(
        {"project_links": 0, "new_project_button": 0}, {"project_links": 0, "new_project_button": 0}
    ) == ("SIGNED_OUT")


def test_the_probe_counts_the_button_the_verdict_reads():
    """The verdict can only see what the page script counts."""
    from video.flow import lane

    assert "new-project-button" in lane.PROBE_JS and "new_project_button" in lane.PROBE_JS
