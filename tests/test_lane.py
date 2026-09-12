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
