import asyncio
from pathlib import Path

import pytest
from click.testing import CliRunner

from video import cli, gen
from video.flow import clips


class _NoSession:
    """A session that must not be touched: the ledger guard has to fire first (I1)."""

    def __getattr__(self, name):
        raise AssertionError(f"session.{name} touched after a duplicate job id")


@pytest.mark.parametrize("kind", ["extend", "edit"])
def test_editor_jobs_refuse_a_job_id_that_already_has_a_submitted_row(tmp_path: Path, kind: str):
    gen.Ledger(tmp_path / "ledger.jsonl").append("job-1", "submitted", kind=kind)
    with pytest.raises(gen.AlreadySubmitted):
        asyncio.run(
            clips._generate_from_editor(
                _NoSession(), "p", "m", "prompt", kind=kind, out_dir=tmp_path, job_id="job-1", wait=1.0
            )
        )


def test_rendition_labels_match_the_download_media_menu():
    assert clips.RENDITIONS == {"gif": "270p", "720p": "720p", "1080p": "1080p", "4k": "4K"}


def test_clip_download_cli_prints_the_saved_path(monkeypatch, tmp_path: Path):
    seen = {}

    def fake_read(profile, fn):
        seen["profile"] = profile
        return tmp_path / "m_1080p.mp4"

    monkeypatch.setattr(cli, "_read", fake_read)
    result = CliRunner().invoke(cli.main, ["flow", "clip", "download", "p", "m", "--quality", "1080p"])
    assert result.exit_code == 0, result.output
    assert result.output.strip().endswith("m_1080p.mp4")
    assert seen["profile"] == "default"


def test_clip_download_cli_rejects_an_unknown_quality():
    result = CliRunner().invoke(cli.main, ["flow", "clip", "download", "p", "m", "--quality", "8k"])
    assert result.exit_code != 0
    assert "8k" in result.output


def test_clip_extend_and_edit_cli_print_json(monkeypatch):
    monkeypatch.setattr(cli, "_read", lambda profile, fn: {"job_id": "j", "outputs": []})
    for verb in ("extend", "edit"):
        result = CliRunner().invoke(cli.main, ["flow", "clip", verb, "p", "m", "keep going"])
        assert result.exit_code == 0, result.output
        assert '"job_id": "j"' in result.output
