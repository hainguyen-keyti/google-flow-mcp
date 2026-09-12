from click.testing import CliRunner

from video import cli


def test_flow_media_all_lists_every_record_with_its_listed_flag(monkeypatch):
    rows = [
        {
            "id": "c1be1764-ee48-4530-b51c-9818fce873bb",
            "kind": "video",
            "model": "veo-lite",
            "size_bytes": 5,
            "created": 1789228805,
            "status": 3,
            "listed": False,
            "prompt": "keep going",
            "url": "https://x/a",
        },
        {
            "id": "01f70107-75de-417e-89eb-1c76567877f4",
            "kind": "video",
            "model": "veo_3_1_t2v_lite",
            "size_bytes": 7,
            "created": 1789227781,
            "status": 3,
            "listed": True,
            "prompt": "teacup",
            "url": "https://x/b",
        },
    ]
    seen = {}

    def fake_read(profile, fn):
        seen["called"] = True
        return rows

    monkeypatch.setattr(cli, "_read", fake_read)
    result = CliRunner().invoke(cli.main, ["flow", "media", "p", "--all"])
    assert result.exit_code == 0, result.output
    assert "c1be1764-ee48-4530-b51c-9818fce873bb" in result.output
    assert "unlisted" in result.output
    assert "records=2" in result.output


def test_flow_media_all_json_passes_records_through(monkeypatch):
    rows = [{"id": "r", "kind": "video", "listed": False}]
    monkeypatch.setattr(cli, "_read", lambda profile, fn: rows)
    result = CliRunner().invoke(cli.main, ["flow", "media", "p", "--all", "--json"])
    assert result.exit_code == 0, result.output
    assert '"listed": false' in result.output
