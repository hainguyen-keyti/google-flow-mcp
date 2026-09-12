from click.testing import CliRunner

from video import cli


def test_flow_characters_prints_entity_id_name_and_count(monkeypatch):
    rows = [
        {
            "entity_id": "0bd7fdbf-cf4b-4b0d-afbb-df584d49ca01",
            "name": "Probe Lan",
            "portrait_media_id": "b21cbb12-d042-4e41-b868-777b3c3a20bd",
        }
    ]
    monkeypatch.setattr(cli, "_read", lambda profile, fn: rows)
    result = CliRunner().invoke(cli.main, ["flow", "characters", "c5d1301b-07fe-4485-86d9-49a47449a494"])
    assert result.exit_code == 0, result.output
    assert "0bd7fdbf-cf4b-4b0d-afbb-df584d49ca01  Probe Lan" in result.output
    assert "characters=1" in result.output


def test_flow_characters_json_passes_rows_through(monkeypatch):
    rows = [{"entity_id": "e", "name": "N", "portrait_media_id": "m"}]
    monkeypatch.setattr(cli, "_read", lambda profile, fn: rows)
    result = CliRunner().invoke(cli.main, ["flow", "characters", "p", "--json"])
    assert result.exit_code == 0, result.output
    assert '"entity_id": "e"' in result.output
