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


@pytest.mark.parametrize("kind", ["extend", "edit"])
def test_editor_jobs_refuse_a_multiline_prompt_before_the_ledger_or_the_page(tmp_path: Path, kind: str):
    # Review 2026-09-15: _prompt_ready types with keyboard.type, which presses Enter for a newline, before the
    # `submitted` row exists; measured offline, 5 Enter presses in 4.5 s for a two-line prompt.
    with pytest.raises(ValueError, match="one line"):
        asyncio.run(
            clips._generate_from_editor(
                _NoSession(),
                "p",
                "m",
                "red shirt\nstill camera",
                kind=kind,
                out_dir=tmp_path,
                job_id="j",
                wait=1.0,
            )
        )
    assert not (tmp_path / "ledger.jsonl").exists()


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


class _Download:
    suggested_filename = "clip.mp4"

    async def save_as(self, path):
        Path(path).write_bytes(b"x")


class _Countable(_Clickable):
    """A locator that reports one match, so version selection can tell present from absent."""

    async def count(self):
        return 1


async def _none(*args, **kwargs):
    return None


async def _clickable_menu_item(session, button, item):
    return _Clickable()


class _VersionPage:
    """A page that remembers every selector it was asked for, so a test can prove which version was picked."""

    def __init__(self):
        self.selectors = []
        self.request = object()
        self.keyboard = _Keyboard()

    def locator(self, selector):
        self.selectors.append(selector)
        return _Countable()

    def get_by_role(self, role, name=None):
        return _Clickable()

    async def wait_for_timeout(self, ms):
        return None

    def expect_download(self, timeout=None):
        class _Ctx:
            async def __aenter__(inner):
                return inner

            async def __aexit__(inner, *exc):
                return False

            @property
            def value(inner):
                async def wait():
                    return _Download()

                return wait()

        return _Ctx()


def test_download_rendition_picks_the_version_before_it_reads_the_screen(monkeypatch, tmp_path):
    # `_open` lands on the pre-edit version ON PURPOSE: the generation path needs every Omni edit to
    # start from the same base. So "Download media" hands back the BASE clip unless a version is chosen
    # first. Measured 2026-09-13: three separate download attempts all returned the base while looking
    # like clean successes, and the clip that had actually been paid for was never fetched.
    records = [
        {"id": "m", "workflow_id": "w-base", "created": 1, "url": "https://lh3/xxxxxxxxxxxxxxxxxxxxbase"},
        {"id": "m", "workflow_id": "w-edit", "created": 2, "url": "https://lh3/yyyyyyyyyyyyyyyyyyyyedit"},
    ]

    async def fake_snapshot(session, project_id):
        return (records, set())

    async def fake_open(session, project_id, media_id):
        return None

    async def fake_menu_item(session, button, item):
        return _Clickable()

    monkeypatch.setattr(clips, "_snapshot", fake_snapshot)
    monkeypatch.setattr(clips, "_open", fake_open)
    monkeypatch.setattr(clips, "_menu_item", fake_menu_item)

    page = _VersionPage()
    session = type("_S", (), {"page": page})()
    out = asyncio.run(clips.download_rendition(session, "p", "m", "720p", tmp_path))

    assert out.exists()
    aimed = [s for s in page.selectors if "yyyyyyyy" in s]
    assert aimed, f"nothing selected the newest version before downloading: {page.selectors}"


def test_download_rendition_can_be_aimed_at_one_version_by_workflow(monkeypatch, tmp_path):
    records = [
        {"id": "m", "workflow_id": "w-base", "created": 1, "url": "https://lh3/xxxxxxxxxxxxxxxxxxxxbase"},
        {"id": "m", "workflow_id": "w-edit", "created": 2, "url": "https://lh3/yyyyyyyyyyyyyyyyyyyyedit"},
    ]

    async def fake_snapshot(session, project_id):
        return (records, set())

    monkeypatch.setattr(clips, "_snapshot", fake_snapshot)
    monkeypatch.setattr(clips, "_open", _none)
    monkeypatch.setattr(clips, "_menu_item", _clickable_menu_item)

    page = _VersionPage()
    session = type("_S", (), {"page": page})()
    asyncio.run(clips.download_rendition(session, "p", "m", "720p", tmp_path, workflow_id="w-base"))

    # Asking for the base must aim at the base, not at the newest version.
    assert [s for s in page.selectors if "xxxxxxxx" in s]
    assert not [s for s in page.selectors if "yyyyyyyy" in s]


def test_download_rendition_refuses_rather_than_fetching_whatever_is_on_screen(monkeypatch, tmp_path):
    # A miss used to mean downloading the displayed clip and calling it a success. That is how the same
    # base clip came back three times on 2026-09-13 under three different filenames.
    records = [{"id": "m", "workflow_id": "w", "created": 1, "url": "https://lh3/zzzzzzzzzzzzzzzzzzzzzzzz"}]

    async def fake_snapshot(session, project_id):
        return (records, set())

    class _Empty(_Clickable):
        async def count(self):
            return 0

    class _EmptyPage(_VersionPage):
        def locator(self, selector):
            self.selectors.append(selector)
            return _Empty()

    monkeypatch.setattr(clips, "_snapshot", fake_snapshot)
    monkeypatch.setattr(clips, "_open", _none)
    monkeypatch.setattr(clips, "_menu_item", _clickable_menu_item)

    session = type("_S", (), {"page": _EmptyPage()})()
    with pytest.raises(LookupError, match="history"):
        asyncio.run(clips.download_rendition(session, "p", "m", "720p", tmp_path))

    async def missing_snapshot(session, project_id):
        return ([{"id": "m", "workflow_id": "w", "created": 1, "url": "u"}], set())

    monkeypatch.setattr(clips, "_snapshot", missing_snapshot)
    session = type("_S", (), {"page": _VersionPage()})()
    with pytest.raises(LookupError, match="workflow"):
        asyncio.run(clips.download_rendition(session, "p", "m", "720p", tmp_path, workflow_id="nope"))


def _editor_reads(monkeypatch, balance: int = 295, records: list | None = None):
    """The two reads that run before anything can spend: the listing and the balance."""

    async def fake_snapshot(session, project_id):
        return (list(records or []), set())

    async def fake_credits(session):
        return {"balance": balance}

    monkeypatch.setattr(clips, "_snapshot", fake_snapshot)
    monkeypatch.setattr(clips.reader, "credits", fake_credits)


def test_an_extend_that_dies_on_the_menu_still_leaves_the_spend_written_down(monkeypatch, tmp_path):
    # Measured 2026-09-13: choosing "Extend" from the menu is itself enough to create a paid job. The
    # driver died right after that click and wrote no ledger row at all, so 20 credits left the account
    # with nothing pointing at them and the job had to be found by hand in Flow's listing.
    _editor_reads(monkeypatch)

    async def fake_open(session, project_id, media_id):
        return None

    async def dying_menu_item(session, button, item):
        raise LookupError(f"menu {button!r} has no item matching {item!r}")

    monkeypatch.setattr(clips, "_open", fake_open)
    monkeypatch.setattr(clips, "_menu_item", dying_menu_item)

    with pytest.raises(LookupError):
        asyncio.run(
            clips._generate_from_editor(
                _Session(), "p", "src-1", "keep going", kind="extend", out_dir=tmp_path, job_id="j", wait=1.0
            )
        )

    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows("j")
    assert [r["status"] for r in rows] == ["opening"], rows
    assert rows[0]["kind"] == "extend"
    assert rows[0]["source_media_id"] == "src-1"
    assert rows[0]["credits_before"] == 295


def test_an_edit_that_dies_opening_the_editor_still_leaves_the_spend_written_down(monkeypatch, tmp_path):
    # The edit path has the same hole: everything between opening the editor and the submit row is a
    # stretch where Flow can take money while the ledger stays empty.
    _editor_reads(monkeypatch)

    async def dying_open(session, project_id, media_id):
        raise RuntimeError("editor never rendered")

    monkeypatch.setattr(clips, "_open", dying_open)

    with pytest.raises(RuntimeError, match="editor never rendered"):
        asyncio.run(
            clips._generate_from_editor(
                _Session(), "p", "src-2", "dress her", kind="edit", out_dir=tmp_path, job_id="k", wait=1.0
            )
        )

    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows("k")
    assert [r["status"] for r in rows] == ["opening"], rows
    assert rows[0]["kind"] == "edit"
    assert rows[0]["source_media_id"] == "src-2"
    assert rows[0]["credits_before"] == 295


@pytest.mark.parametrize("kind", ["extend", "edit"])
def test_the_opening_row_names_the_project_and_every_workflow_the_listing_held(monkeypatch, tmp_path, kind):
    # Re-review 2026-09-15 (finding 3): reconcile guessed "new since the job opened" from two clocks, so a job opened
    # right after its clip was made stayed unknown forever. The row now carries what the driver itself read.
    _editor_reads(monkeypatch, records=[_record("src-1", 100), _record("other", 200)])

    async def dying_open(session, project_id, media_id):
        raise RuntimeError("editor never rendered")

    monkeypatch.setattr(clips, "_open", dying_open)

    with pytest.raises(RuntimeError, match="editor never rendered"):
        asyncio.run(
            clips._generate_from_editor(
                _Session(), "p", "src-1", "keep going", kind=kind, out_dir=tmp_path, job_id="j", wait=1.0
            )
        )

    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows("j")
    assert [r["status"] for r in rows] == ["opening"], rows
    assert rows[0]["project"] == "p"
    assert rows[0]["workflows_before"] == ["w-100", "w-200"]


def test_an_orphaned_opening_row_does_not_block_the_same_job_from_running_again(monkeypatch, tmp_path):
    # The point of the row is to record a spend, not to burn the job id. If it blocked, every failure
    # would force a fresh id and the ledger would drift away from the shot it belongs to.
    prompt = "keep going"
    gen.Ledger(tmp_path / "ledger.jsonl").append("j", "opening", kind="extend", source_media_id="src")
    ours = {
        "id": "new",
        "workflow_id": "w-new",
        "created": 2,
        "status": 3,
        "url": "https://x/n",
        "prompt": prompt,
    }
    snapshots = iter([([], set()), ([ours], {"scene-1"})])

    async def fake_snapshot(session, project_id):
        return next(snapshots)

    async def fake_credits(session):
        return {"balance": 295}

    async def fake_open(session, project_id, media_id):
        return None

    async def fake_menu_item(session, button, item):
        return _Clickable()

    async def fake_capture(session, action, *, settle):
        await action()
        return {"uwAyfb": [[]]}

    async def fake_fetch(request, row, stem, attempts=6):
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
            _Session(), "p", "src", prompt, kind="extend", out_dir=tmp_path, job_id="j", wait=60.0
        )
    )

    assert result["status"] == "done"
    rows = gen.Ledger(tmp_path / "ledger.jsonl").rows("j")
    assert [r["status"] for r in rows] == ["opening", "opening", "submitted", "done"], rows


def test_has_submitted_still_ignores_an_opening_row_and_blocks_the_settled_ones(tmp_path):
    # Pins the exact set the guard blocks on. Adding "opening" to it would turn every recorded spend
    # into a dead job id, which is the opposite of what the row is for.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("a", "opening", kind="edit")
    assert not ledger.has_submitted("a")

    for job_id, status in (("b", "submitted"), ("c", "done"), ("d", "failed")):
        ledger.append(job_id, status)
        assert ledger.has_submitted(job_id), status


def _reconcile_world(monkeypatch, records, balance):
    async def fake_snapshot(session, project_id):
        return (records, set())

    async def fake_credits(session):
        return {"balance": balance}

    monkeypatch.setattr(clips, "_snapshot", fake_snapshot)
    monkeypatch.setattr(clips.reader, "credits", fake_credits)


def _record(media_id: str, created: float) -> dict:
    return {
        "id": media_id,
        "workflow_id": f"w-{created:.0f}",
        "created": created,
        "status": 3,
        "url": "https://x/n",
        "prompt": "whatever",
    }


def _opening(ledger: gen.Ledger, job_id: str, media_id: str, **fields) -> float:
    """An editor job's opening row as the driver writes it (project "p", one workflow listed); returns its time."""
    row = {"kind": "edit", "credits_before": 295, "project": "p", "workflows_before": ["w-100"], **fields}
    ledger.append(job_id, "opening", source_media_id=media_id, **row)
    return ledger.rows(job_id)[0]["ts"]


def test_reconcile_closes_an_orphan_as_done_when_the_media_gained_a_record(monkeypatch, tmp_path):
    # This is the case that cost 20 credits: the editor spent, the driver died before its submitted row,
    # and only Flow's listing knew the job existed. An edit, because only an edit puts its version on the source
    # clip: every extend on record got a new media id (review 2026-09-15).
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-1")
    _reconcile_world(monkeypatch, [_record("src-1", 100), _record("src-1", 200)], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["done"]
    rows = ledger.rows("j")
    assert [r["status"] for r in rows] == ["opening", "done"]
    assert rows[-1]["spent"] == 20
    assert rows[-1]["credits_after"] == 275


def test_reconcile_closes_an_orphan_as_failed_when_the_balance_never_moved(monkeypatch, tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-2")
    _reconcile_world(monkeypatch, [_record("src-2", 100)], balance=295)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["failed"]
    rows = ledger.rows("j")
    assert [r["status"] for r in rows] == ["opening", "failed"]
    assert rows[-1]["spent"] == 0


def test_reconcile_fails_a_job_opened_right_after_its_clip_was_made_when_nothing_new_appeared(
    monkeypatch, tmp_path
):
    # Re-review 2026-09-15 (finding 3): a clip's `created` is stamped at submit, so an edit opened right after the clip
    # finished fell inside the old 300 s clock margin, and a job that died before submitting stayed unknown forever.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    stamp = _opening(ledger, "j", "src-10", workflows_before=["w-src"])
    _reconcile_world(monkeypatch, [{**_record("src-10", stamp - 30), "workflow_id": "w-src"}], balance=295)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["failed"]
    rows = ledger.rows("j")
    assert [r["status"] for r in rows] == ["opening", "failed"]
    assert rows[-1]["spent"] == 0


def test_reconcile_leaves_a_job_alone_when_the_evidence_does_not_agree(monkeypatch, tmp_path):
    # Money moved but nothing new is on that media: guessing either way would put a lie in the ledger.
    # The listing must hold the clip's older record, or the verdict stops at "clip not listed" before the balance.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-3")
    _reconcile_world(monkeypatch, [_record("src-3", 100)], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("j")] == ["opening"]


def test_reconcile_ignores_jobs_that_already_have_an_outcome(monkeypatch, tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("settled", "opening", kind="edit", source_media_id="src-4", credits_before=295)
    ledger.append("settled", "submitted", kind="edit", source_media_id="src-4")
    ledger.append("settled", "done", spent=20)
    _reconcile_world(monkeypatch, [], balance=275)

    assert asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path)) == []
    assert len(ledger.rows("settled")) == 3


def test_reconcile_judges_a_reused_job_id_by_its_first_opening_row(monkeypatch, tmp_path):
    # Review 2026-09-15: story and the CLI may run a job id again after an orphaned opening row. Only the first row
    # holds the balance and workflows from before any attempt spent; judging the last one would call a 20-credit
    # spend `failed, spent: 0`, which story reads as free to run again.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-12", credits_before=295, workflows_before=["w-100"])
    _opening(ledger, "j", "src-12", credits_before=275, workflows_before=["w-100", "w-200"])
    _reconcile_world(monkeypatch, [_record("src-12", 100), _record("src-12", 200)], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["done"]
    rows = ledger.rows("j")
    assert [r["status"] for r in rows] == ["opening", "opening", "done"]
    assert rows[-1]["spent"] == 20


def _no_flow_reads(monkeypatch):
    async def must_not_read(*args, **kwargs):
        raise AssertionError("reconcile read Flow with nothing it can judge")

    monkeypatch.setattr(clips, "_snapshot", must_not_read)
    monkeypatch.setattr(clips.reader, "credits", must_not_read)


def test_reconcile_reports_gen_and_agent_jobs_as_skipped_and_never_writes_them(monkeypatch, tmp_path):
    # Review 2026-09-15: gen and agent rows name no clip, so the editor's rule could never find them done and wrote
    # "failed" from the balance alone, a balance measured to move with no spend (195 to 245 overnight).
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("g", "submitted", kind="t2v", argv=["video", "t2v"], credits_before=295)
    ledger.append("a", "submitted", kind="agent", project="p", message="hi", credits_before=295)
    _no_flow_reads(monkeypatch)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert out == [
        {"job_id": "g", "kind": "t2v", "verdict": "skipped"},
        {"job_id": "a", "kind": "agent", "verdict": "skipped"},
    ]
    assert [r["status"] for r in ledger.rows()] == ["submitted", "submitted"]


def test_reconcile_judges_editor_rows_and_skips_the_rest_in_one_pass(monkeypatch, tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "e", "src-6")
    ledger.append("g", "submitted", kind="r2v", argv=["video", "r2v"], credits_before=295)
    _reconcile_world(monkeypatch, [_record("src-6", 100), _record("src-6", 200)], balance=285)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [(r["job_id"], r["verdict"]) for r in out] == [("e", "done"), ("g", "skipped")]
    assert [r["status"] for r in ledger.rows("e")] == ["opening", "done"]
    assert [r["status"] for r in ledger.rows("g")] == ["submitted"]


def test_reconcile_leaves_an_editor_job_open_when_the_balance_matches_but_the_project_gained_a_record(
    monkeypatch, tmp_path
):
    # An equal balance is not proof on its own that nothing was bought: failed also needs a project with no record
    # the job's opening row did not already list (DECISIONS 2026-09-15).
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-7")
    records = [_record("src-7", 100), _record("another-media", 200)]
    _reconcile_world(monkeypatch, records, balance=295)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("j")] == ["opening"]


def test_reconcile_never_judges_a_job_whose_clip_is_not_in_the_listing_it_read(monkeypatch, tmp_path):
    # Review 2026-09-15: a listing without the job's own clip (deleted, or never this project's) holds no evidence
    # about the job, yet nothing new there and an unchanged balance once wrote `failed, spent: 0`, which story reads
    # as retryable.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "gone-clip", project="a")
    _reconcile_world(monkeypatch, [_record("still-listed", 100)], balance=295)

    out = asyncio.run(clips.reconcile_editor(_Session(), "a", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("j")] == ["opening"]


@pytest.mark.parametrize("listed", [[], ["w-50"]])
def test_reconcile_never_calls_a_job_done_when_its_opening_row_never_saw_the_clip(
    monkeypatch, tmp_path, listed
):
    # Review 2026-09-15: a set missing the source clip (a partial first listing frame, say) made every record of the
    # clip look new, so a job that spent nothing was written `done, spent: 0`; the old clock rule said failed.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-13", workflows_before=listed)
    _reconcile_world(monkeypatch, [_record("src-13", 100), _record("other", 50)], balance=295)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("j")] == ["opening"]


def test_reconcile_skips_another_projects_job_and_names_that_project(monkeypatch, tmp_path):
    # Review 2026-09-15: reconciling project A judged project B's job against A's listing and wrote `failed, spent:
    # 0`, which story reads as retryable. The row names its project now: reported, never judged or written here.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "b-job", "clip-b", project="b")
    _no_flow_reads(monkeypatch)

    out = asyncio.run(clips.reconcile_editor(_Session(), "a", out_dir=tmp_path))

    assert out == [{"job_id": "b-job", "kind": "edit", "verdict": "skipped", "project": "b"}]
    assert [r["status"] for r in ledger.rows("b-job")] == ["opening"]


@pytest.mark.parametrize("named", [{}, {"project": "p"}])
def test_reconcile_never_judges_an_opening_row_that_lists_no_workflows(monkeypatch, tmp_path, named):
    # Rows written before opening rows listed their workflows cannot tell a new record from an old one (DECISIONS
    # 2026-09-15): they stay unknown for a person, and Flow is not even read for them.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("old", "opening", kind="extend", source_media_id="src-11", credits_before=295, **named)
    _no_flow_reads(monkeypatch)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert out == [{"job_id": "old", "verdict": "unknown", "credits_now": None}]
    assert [r["status"] for r in ledger.rows("old")] == ["opening"]


@pytest.mark.parametrize("created", [None, 1])
def test_a_record_the_opening_row_did_not_list_counts_as_new_whatever_its_time(
    monkeypatch, tmp_path, created
):
    # Flow's clock and this Mac's can disagree and a time may not parse (review 2026-09-15), so "new since the job
    # opened" is decided by the workflows the row listed, never by comparing times.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-9", kind="extend")
    unlisted = {**_record("fresh", 200), "created": created}
    _reconcile_world(monkeypatch, [_record("src-9", 100), unlisted], balance=295)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("j")] == ["opening"]


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
    assert [r["status"] for r in rows] == ["opening", "submitted", "done"]


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
    assert [r["status"] for r in rows] == ["opening", "submitted", "failed"]
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


def test_clip_reconcile_cli_prints_every_verdict(monkeypatch, tmp_path):
    # The ledger holds a job of project p that can be judged, the only case that opens a browser at all.
    _opening(gen.Ledger(tmp_path / "ledger.jsonl"), "j", "m")
    seen = {}

    def fake_read(profile, fn):
        seen["profile"] = profile
        return [
            {"job_id": "j", "verdict": "done", "credits_now": 275},
            {"job_id": "k", "verdict": "unknown", "credits_now": 275},
        ]

    monkeypatch.setattr(cli, "_read", fake_read)
    result = CliRunner().invoke(cli.main, ["flow", "clip", "reconcile", "p", "--out", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert '"verdict": "done"' in result.output
    assert '"verdict": "unknown"' in result.output, "a job left open has to stay visible, not be hidden"
    assert seen["profile"] == "default"


def test_clip_reconcile_cli_opens_no_browser_when_no_job_can_be_judged_here(monkeypatch, tmp_path):
    # Review 2026-09-15 (finding 4): the CLI opened Chrome (12-17 s) before reading a ledger holding nothing it
    # could judge: a gen job and another project's editor job.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append("g", "submitted", kind="t2v", credits_before=295)
    _opening(ledger, "b", "m", project="q")

    def no_browser(profile, fn):
        raise AssertionError("flow clip reconcile opened a browser with nothing to judge")

    monkeypatch.setattr(cli, "_read", no_browser)
    result = CliRunner().invoke(cli.main, ["flow", "clip", "reconcile", "p", "--out", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert '"verdict": "skipped"' in result.output
    assert '"project": "q"' in result.output
