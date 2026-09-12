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


def test_new_records_are_the_unseen_workflows_oldest_first():
    rows = [
        {"id": "m", "workflow_id": "old", "created": 1, "status": 3, "url": "https://x/old"},
        {"id": "b", "workflow_id": "wb", "created": 20, "status": 3, "url": "https://x/b"},
        {"id": "m", "workflow_id": "wa", "created": 10, "status": 1, "url": None},
    ]
    assert [r["workflow_id"] for r in clips.new_records({"old"}, rows)] == ["wa", "wb"]
    assert clips.is_done(rows[1]) is True
    assert clips.is_done(rows[2]) is False
    assert clips.is_done({"id": "c", "status": 3, "url": None}) is False


def test_role_of_tells_the_generated_clip_from_the_scene_copy():
    prompt = "the sailboat keeps rocking gently, camera holds still"
    assert clips.role_of({"prompt": prompt, "size_bytes": 1786375}, prompt) == "generated"
    assert (
        clips.role_of({"prompt": "a small wooden sailboat model on a desk", "size_bytes": None}, prompt)
        == "copy"
    )
    assert clips.role_of({"prompt": None}, prompt) == "copy"


def test_fetch_with_retry_waits_out_404_renditions(monkeypatch, tmp_path: Path):
    calls = []

    async def flaky(request, kind, url, stem):
        calls.append(url)
        if calls.count(url) < 3:
            raise RuntimeError(
                "no rendition of video returned a real asset: w=m22: HTTP 404; w=m18: HTTP 404"
            )
        return tmp_path / "x.mp4"

    async def no_sleep(seconds):
        calls.append(f"sleep {seconds}")

    monkeypatch.setattr(clips.download_mod, "fetch_asset", flaky)
    monkeypatch.setattr(clips.asyncio, "sleep", no_sleep)
    row = {"kind": "video", "url": "https://x/a"}
    assert asyncio.run(clips._fetch_with_retry(None, row, tmp_path / "a")) == tmp_path / "x.mp4"
    assert calls == ["https://x/a", "sleep 15", "https://x/a", "sleep 15", "https://x/a"]


def test_fetch_with_retry_gives_up_on_other_errors(monkeypatch, tmp_path: Path):
    async def broken(request, kind, url, stem):
        raise RuntimeError("download failed: HTTP 403")

    monkeypatch.setattr(clips.download_mod, "fetch_asset", broken)
    with pytest.raises(RuntimeError, match="403"):
        asyncio.run(clips._fetch_with_retry(None, {"kind": "video", "url": "u"}, tmp_path / "a"))


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
