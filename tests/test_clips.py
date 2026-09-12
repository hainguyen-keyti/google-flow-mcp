import asyncio
from pathlib import Path

import pytest
from click.testing import CliRunner
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

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


class _Clickable:
    first = property(lambda self: self)
    text = "keep going"

    async def click(self, timeout=None):
        return None

    async def inner_text(self):
        return self.text

    async def is_disabled(self):
        return False


class _Keyboard:
    async def type(self, text):
        return None


class _Page:
    request = object()
    keyboard = _Keyboard()

    async def wait_for_timeout(self, ms):
        return None

    def locator(self, selector):
        return _Clickable()

    def get_by_role(self, role, name=None):
        return _Clickable()


class _Session:
    page = _Page()


def test_editor_job_keeps_polling_past_the_scene_copy_until_its_own_record_is_done(monkeypatch, tmp_path):
    # Measured 2026-09-13 00:11: the scene's copy of the source is listed (status 3) seconds after Extend,
    # the extension itself only a minute later; stopping at the first done record lost the extension.
    prompt = "keep going"
    copy = {
        "id": "src",
        "workflow_id": "w-copy",
        "created": 1,
        "status": 3,
        "url": "https://x/c",
        "prompt": "orig",
    }
    ours = {
        "id": "new",
        "workflow_id": "w-new",
        "created": 2,
        "status": 3,
        "url": "https://x/n",
        "prompt": prompt,
    }
    snapshots = iter([([], set()), ([copy], set()), ([copy, ours], {"scene-1"})])
    fetched = []

    async def fake_snapshot(session, project_id):
        return next(snapshots)

    async def fake_credits(session):
        return {"balance": 100}

    async def fake_open(session, project_id, media_id):
        return None

    async def fake_menu_item(session, button, item):
        return _Clickable()

    async def fake_capture(session, action, *, settle):
        await action()
        return {"uwAyfb": [[]]}

    async def fake_fetch(request, row, stem, attempts=6):
        fetched.append(row["workflow_id"])
        return stem.with_suffix(".mp4")

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(clips, "_snapshot", fake_snapshot)
    monkeypatch.setattr(clips, "_open", fake_open)
    monkeypatch.setattr(clips, "_menu_item", fake_menu_item)
    monkeypatch.setattr(clips, "capture", fake_capture)
    monkeypatch.setattr(clips, "_fetch_with_retry", fake_fetch)
    monkeypatch.setattr(clips.reader, "credits", fake_credits)
    monkeypatch.setattr(clips.asyncio, "sleep", no_sleep)
    result = asyncio.run(
        clips._generate_from_editor(
            _Session(), "p", "src", prompt, kind="extend", out_dir=tmp_path, job_id="job-9", wait=60.0
        )
    )
    assert result["status"] == "done" and result["scene_id"] == "scene-1"
    assert fetched == ["w-new"]
    assert [o["role"] for o in result["outputs"]] == ["copy", "generated"]
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows("job-9")
    assert [r["status"] for r in rows] == ["submitted", "done"]


class _MenuPage:
    """Toolbar button plus a menu item that becomes visible after `appears_after` wait_for calls."""

    def __init__(self, appears_after: int):
        self.appears_after = appears_after
        self.waits = 0
        self.clicks: list[str] = []
        self.escapes = 0
        page = self

        class Item:
            async def wait_for(self, state=None, timeout=None):
                page.waits += 1
                if page.waits <= page.appears_after:
                    raise PlaywrightTimeoutError("not visible")

        class Chain:
            def filter(self, has_text=None):
                return self

            @property
            def first(self):
                return Item()

        class Button(Chain):
            @property
            def first(self):
                return self

            async def click(self, timeout=None):
                page.clicks.append("toolbar")

        class Keyboard:
            async def press(self, key):
                page.escapes += 1

        self._chain, self._button, self.keyboard = Chain(), Button(), Keyboard()

    def get_by_role(self, role, name=None):
        return self._button

    def locator(self, selector):
        return self._chain

    async def wait_for_timeout(self, ms):
        return None


def test_menu_item_opens_the_toolbar_menu_once_when_the_item_renders():
    # A second "Add clip" click appends a copy of the clip, so the helper must wait, not re-click.
    page = _MenuPage(appears_after=0)
    session = type("S", (), {"page": page})()
    assert asyncio.run(clips._menu_item(session, "Add clip", "Extend")) is not None
    assert page.clicks == ["toolbar"]
    assert page.escapes == 0


def test_menu_item_retries_once_then_reports_the_missing_item():
    page = _MenuPage(appears_after=99)
    session = type("S", (), {"page": page})()
    with pytest.raises(LookupError, match="Extend"):
        asyncio.run(clips._menu_item(session, "Add clip", "Extend"))
    assert page.clicks == ["toolbar", "toolbar"]
    assert page.escapes == 1


def test_prompt_ready_types_the_prompt_when_the_editor_box_came_back_empty():
    typed = []

    class Box(_Clickable):
        text = ""

        async def click(self, timeout=None):
            typed.append("click")

        async def inner_text(self):
            return Box.text

    class Keyboard:
        async def type(self, text):
            typed.append(text)
            Box.text = text

    page = type("P", (), {"keyboard": Keyboard()})()
    box, start = Box(), _Clickable()
    try:
        assert asyncio.run(clips._prompt_ready(page, box, start, "keep going", timeout=5.0)) is True
        assert typed == ["click", "keep going"]
    finally:
        Box.text = ""


def test_editor_job_clicks_generate_once_and_fails_loudly_when_nothing_was_generated(monkeypatch, tmp_path):
    # Measured 2026-09-13 00:49: a click on a stale editor submitted nothing, rpcids [] and 0 credits,
    # yet the job reported "pending" as if it were still generating. Clicking again is not the answer:
    # at 00:56 a second click added an Omni edit worth 20 credits on top of the 10-credit extend.
    clicks = []

    async def fake_snapshot(session, project_id):
        return [], set()

    async def fake_credits(session):
        return {"balance": 650}

    async def fake_open(session, project_id, media_id):
        return None

    async def fake_menu_item(session, button, item):
        return _Clickable()

    async def silent_capture(session, action, *, settle):
        await action()
        return {}

    async def no_sleep(seconds):
        return None

    class RecordingPage(_Page):
        def get_by_role(self, role, name=None):
            class Button(_Clickable):
                async def click(self, timeout=None):
                    clicks.append("generate")

            return Button()

    monkeypatch.setattr(clips, "_snapshot", fake_snapshot)
    monkeypatch.setattr(clips, "_open", fake_open)
    monkeypatch.setattr(clips, "_menu_item", fake_menu_item)
    monkeypatch.setattr(clips, "capture", silent_capture)
    monkeypatch.setattr(clips.reader, "credits", fake_credits)
    monkeypatch.setattr(clips.asyncio, "sleep", no_sleep)
    session = type("S", (), {"page": RecordingPage()})()
    with pytest.raises(RuntimeError, match="spent 0 credits"):
        asyncio.run(
            clips._generate_from_editor(
                session, "p", "src", "keep going", kind="extend", out_dir=tmp_path, job_id="j", wait=5.0
            )
        )
    assert clicks == ["generate"], "Start generation must be clicked exactly once, never retried"
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows("j")
    assert [r["status"] for r in rows] == ["submitted", "failed"]
    assert rows[-1]["spent"] == 0


def test_await_submit_stays_on_the_editor_until_a_request_goes_out(monkeypatch):
    # Measured 2026-09-13 01:05: the submit leaves ~20s after the click, so polling (which navigates
    # away) 15s later cancelled it: 0 credits, rpcids [], and no generation at all.
    calls = []
    replies = iter([{}, {}, {"uwAyfb": [[]]}])

    async def fake_capture(session, action, *, settle):
        calls.append(settle)
        await action()
        return next(replies)

    async def wait_for_timeout(ms):
        calls.append(f"wait{ms}")

    async def click():
        calls.append("click")

    monkeypatch.setattr(clips, "capture", fake_capture)
    session = type("S", (), {"page": type("P", (), {"wait_for_timeout": staticmethod(wait_for_timeout)})()})()
    frames = asyncio.run(clips._await_submit(session, click, settle=30.0, patience=90.0))
    assert frames == {"uwAyfb": [[]]}
    assert calls.count("click") == 1, "the click must never be repeated while waiting"
    assert calls.count("wait15000") == 2


def test_await_submit_gives_up_after_its_patience(monkeypatch):
    async def always_silent(session, action, *, settle):
        await action()
        return {}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(clips, "capture", always_silent)
    session = type("S", (), {"page": type("P", (), {"wait_for_timeout": staticmethod(noop)})()})()
    assert asyncio.run(clips._await_submit(session, noop, settle=30.0, patience=60.0)) == {}


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
