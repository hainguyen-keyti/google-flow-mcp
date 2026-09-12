import pytest

from video.flow import projects


def test_project_id_from_url_accepts_uuid_and_backfill_ids():
    assert projects.project_id_from_url(
        "https://flow.google.com/project/5bcbfdd7-0c50-4bd3-b902-57d0c173f683"
    ) == ("5bcbfdd7-0c50-4bd3-b902-57d0c173f683")
    assert projects.project_id_from_url(
        "https://flow.google.com/project/8822142b-ca75-46b7-aac8-03d2831_backfill/tools"
    ) == ("8822142b-ca75-46b7-aac8-03d2831_backfill")


def test_project_id_from_url_rejects_non_project_pages():
    with pytest.raises(ValueError):
        projects.project_id_from_url("https://flow.google.com/")
    with pytest.raises(ValueError):
        projects.project_id_from_url("https://flow.google.com/about")


def test_delete_refuses_ids_not_created_by_this_pipeline_unless_explicit():
    assert projects.may_delete("abc", created_ids={"abc"}, explicit=False) is True
    assert projects.may_delete("xyz", created_ids={"abc"}, explicit=False) is False
    assert projects.may_delete("xyz", created_ids=set(), explicit=True) is True
