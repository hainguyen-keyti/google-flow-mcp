import asyncio
from pathlib import Path
from typing import ClassVar

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

    async def count(self):
        return 1

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

    def __init__(self):
        # A real page carries listeners, and the editor path attaches one to hear what Flow replies about the
        # job it just paid for, so the fake has to carry them too. `attached` keeps the history, because
        # "nothing is left behind" is also true of a driver that never listened at all.
        self.listeners: list[tuple[str, object]] = []
        self.attached: list[str] = []

    def on(self, event, handler):
        self.listeners.append((event, handler))
        self.attached.append(event)

    def remove_listener(self, event, handler):
        self.listeners = [row for row in self.listeners if row != (event, handler)]

    async def wait_for_timeout(self, ms):
        return None

    def locator(self, selector):
        return _Clickable()

    def get_by_role(self, role, name=None):
        return _Clickable()


class _Session:
    def __init__(self):
        self.page = _Page()


class _Download:
    suggested_filename = "clip.mp4"

    async def save_as(self, path):
        Path(path).write_bytes(b"x")


class _Countable(_Clickable):
    """A locator that reports one match, so version selection can tell present from absent."""

    async def count(self):
        return 1


class _LateButton:
    """Playwright's click waits for its element up to its own timeout, then gives up."""

    first = property(lambda self: self)

    def __init__(self, page):
        self.page = page

    async def count(self):
        return 1 if self.page.elapsed_ms >= self.page.appears_after_ms else 0

    async def click(self, timeout=None):
        deadline = self.page.elapsed_ms + (timeout or 0)
        if self.page.appears_after_ms > deadline:
            self.page.elapsed_ms = deadline
            raise PlaywrightTimeoutError(f"Locator.click: Timeout {timeout}ms exceeded.")
        self.page.elapsed_ms = max(self.page.elapsed_ms, self.page.appears_after_ms)
        self.page.clicked.append("toolbar")
        self.page.overlay = True


class _MenuItems:
    first = property(lambda self: self)

    def __init__(self, page):
        self.page = page

    def filter(self, has_text=None):
        return self

    async def wait_for(self, state=None, timeout=None):
        if not self.page.overlay:
            raise PlaywrightTimeoutError(f"Locator.wait_for: Timeout {timeout}ms exceeded")

    async def is_enabled(self):
        return self.page.item_enabled

    async def click(self, timeout=None):
        if not self.page.item_enabled:
            raise PlaywrightTimeoutError(f"Locator.click: Timeout {timeout}ms exceeded.")
        self.page.clicked.append("item")


class _LateToolbarPage:
    """The clip editor builds its toolbar in stages.

    Measured 2026-09-16 by the first real clip_extend: the driver opened the editor, waited a fixed 3 s and clicked
    a button that was not there yet, so the call died with `Locator.click: Timeout 8000ms exceeded` before anything
    could be spent. The scene driver had the same bug and was fixed the same day.
    """

    keyboard = _Keyboard()

    def __init__(self, appears_after_ms, item_enabled=True):
        self.appears_after_ms = appears_after_ms
        self.elapsed_ms = 0
        self.clicked = []
        self.overlay = False
        self.item_enabled = item_enabled

    async def wait_for_timeout(self, ms):
        self.elapsed_ms += ms

    def get_by_role(self, role, name=None):
        return _LateButton(self)

    def locator(self, selector):
        return _MenuItems(self)


class _LateSession:
    def __init__(self, page):
        self.page = page


def test_menu_item_waits_for_a_control_that_arrives_after_the_rest_of_the_toolbar():
    # The editor's own Add clip button arrives late on a page that already holds a clip, and Playwright's click only
    # waits its own 8 s. The first real clip_extend died there, before the spend, with the intent row already written.
    page = _LateToolbarPage(appears_after_ms=12_000)

    item = asyncio.run(clips._menu_item(_LateSession(page), "Add clip", "Extend"))

    assert page.clicked == ["toolbar"]
    assert page.elapsed_ms >= 12_000
    assert item is not None


def test_menu_item_says_so_when_flow_greys_the_item_out():
    # Measured 2026-09-16 on a clip made by omni-flash: `Extend (Veo 3.1 - Lite)` is rendered disabled, and clicking
    # it produced only `Locator.click: Timeout 8000ms exceeded`, which tells an agent nothing about why.
    page = _LateToolbarPage(appears_after_ms=0, item_enabled=False)

    with pytest.raises(LookupError, match="greyed out"):
        asyncio.run(clips._menu_item(_LateSession(page), "Add clip", "Extend"))
    assert "item" not in page.clicked


def test_menu_item_gives_up_when_the_control_never_arrives():
    page = _LateToolbarPage(appears_after_ms=10_000_000)

    with pytest.raises(LookupError, match="never appeared"):
        asyncio.run(clips._menu_item(_LateSession(page), "Add clip", "Extend"))
    assert page.clicked == []


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
    # Plan C (DECISIONS 2026-09-15): the prompt too, the only mark telling the job's own record from an upscale.
    assert rows[0]["prompt"] == "keep going"


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


JOB_PROMPT = "make it night"


def _record(media_id: str, created: float, prompt: str = "whatever") -> dict:
    return {
        "id": media_id,
        "workflow_id": f"w-{created:.0f}",
        "created": created,
        "status": 3,
        "url": "https://x/n",
        "prompt": prompt,
    }


def _opening(ledger: gen.Ledger, job_id: str, media_id: str, **fields) -> float:
    """An editor job's opening row as the driver writes it (project "p", one workflow listed, JOB_PROMPT typed)."""
    row = {
        "kind": "edit",
        "credits_before": 295,
        "project": "p",
        "workflows_before": ["w-100"],
        "prompt": JOB_PROMPT,
        **fields,
    }
    ledger.append(job_id, "opening", source_media_id=media_id, **row)
    return ledger.rows(job_id)[0]["ts"]


def test_reconcile_closes_an_orphan_as_done_when_the_media_gained_a_record(monkeypatch, tmp_path):
    # This is the case that cost 20 credits: the editor spent, the driver died before its submitted row,
    # and only Flow's listing knew the job existed. An edit, because only an edit puts its version on the source
    # clip: every extend on record got a new media id (review 2026-09-15).
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-1")
    _reconcile_world(monkeypatch, [_record("src-1", 100), _record("src-1", 200, JOB_PROMPT)], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["done"]
    rows = ledger.rows("j")
    assert [r["status"] for r in rows] == ["opening", "done"]
    assert rows[-1]["spent"] == 20
    assert rows[-1]["credits_after"] == 275
    # Written into the done row so no other job can take the same record (DECISIONS 2026-09-15).
    assert rows[-1]["outputs"] == [
        {"media_id": "src-1", "workflow_id": "w-200", "role": "generated", "status": 3}
    ]


def test_reconcile_leaves_an_extend_open_even_when_its_prompt_shows_up_on_a_new_clip(monkeypatch, tmp_path):
    # Review of plan C (2026-09-15): an extend's clip lands on a new media id beside copies of every version of the
    # source, which keep their prompts on new workflows, so nothing ties a new clip to this job: a person closes
    # extends (DECISIONS 2026-09-15). The new records it left still keep `failed` away.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "x", "src-15", kind="extend", prompt="keep going")
    listing = [
        _record("src-15", 100),
        _record("copy-15", 300, "the source prompt"),
        _record("new-15", 400, "keep going"),
    ]
    _reconcile_world(monkeypatch, listing, balance=295)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("x")] == ["opening"]


def test_reconcile_never_closes_an_extend_with_a_version_made_on_its_own_clip(monkeypatch, tmp_path):
    # An extend never adds a version to its source clip; one carrying the same prompt there came from some edit.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "x", "src-28", kind="extend")
    _reconcile_world(monkeypatch, [_record("src-28", 100), _record("src-28", 200, JOB_PROMPT)], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("x")] == ["opening"]


@pytest.mark.parametrize(
    "listing",
    [
        # Another job's edit on a different clip with the same prompt, closed by hand or ledgered elsewhere.
        [_record("src-20", 100), _record("other-clip", 200, JOB_PROMPT)],
        # An extend's copy of an earlier edit of this clip: new media, new workflow, the same prompt and time.
        [
            _record("src-20", 100),
            _record("src-20", 150, JOB_PROMPT),
            {**_record("copy-20", 150, JOB_PROMPT), "workflow_id": "w-copy"},
        ],
    ],
)
def test_reconcile_never_gives_an_edit_a_record_that_is_not_on_its_own_clip(monkeypatch, tmp_path, listing):
    # Review of plan C (2026-09-15): story sends one prompt to every edit and extends copy edited versions onto new
    # media, so matching by prompt alone gave other clips' records to this job; 10 of 10 real edits landed on their
    # own clip.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    listed = [record["workflow_id"] for record in listing if record["id"] == "src-20"]
    _opening(ledger, "a", "src-20", workflows_before=listed)
    _reconcile_world(monkeypatch, listing, balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("a")] == ["opening"]


@pytest.mark.parametrize("second_prompt", [JOB_PROMPT, f"  {JOB_PROMPT} "])
def test_reconcile_leaves_two_open_edits_of_the_same_clip_and_prompt_to_a_person(
    monkeypatch, tmp_path, second_prompt
):
    # Review of plan C (2026-09-15): with two open jobs that could both own the new version, ledger order picked the
    # winner, and it could be the job that never submitted. Prompts compare as the driver does, or both jobs take the
    # one version in a single pass.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "never-submitted", "src-21")
    _opening(ledger, "submitted", "src-21", prompt=second_prompt)
    _reconcile_world(monkeypatch, [_record("src-21", 100), _record("src-21", 200, JOB_PROMPT)], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [(r["job_id"], r["verdict"]) for r in out] == [
        ("never-submitted", "unknown"),
        ("submitted", "unknown"),
    ]
    assert [r["status"] for r in ledger.rows()] == ["opening", "opening"]


@pytest.mark.parametrize(
    ("clip", "prompt"),
    [
        # Story sends one prompt to every shot's edit, each shot on its own clip.
        ("src-31", JOB_PROMPT),
        # Two edits of one clip that typed different prompts.
        ("src-30", "make it rain"),
    ],
)
def test_reconcile_closes_two_open_edits_that_differ_in_clip_or_prompt(monkeypatch, tmp_path, clip, prompt):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "a", "src-30", workflows_before=["w-100", "w-101"])
    _opening(ledger, "b", clip, prompt=prompt, workflows_before=["w-100", "w-101"])
    listing = [
        _record("src-30", 100),
        _record("src-31", 101),
        _record("src-30", 200, JOB_PROMPT),
        _record(clip, 300, prompt),
    ]
    _reconcile_world(monkeypatch, listing, balance=255)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [(r["job_id"], r["verdict"]) for r in out] == [("a", "done"), ("b", "done")]
    closed = [row for row in ledger.rows() if row["status"] == "done"]
    assert [(row["job_id"], row["outputs"][0]["workflow_id"]) for row in closed] == [
        ("a", "w-200"),
        ("b", "w-300"),
    ]


RECONCILED = "checked the listing and the credit balance"


@pytest.mark.parametrize(
    "closing",
    [
        # Its driver saw no version before the deadline, so it wrote failed with no generated output.
        [("submitted", {}), ("failed", {"outputs": [], "spent": 20})],
        # Closed by hand.
        [("done", {"spent": 20})],
        # Closed the way story's reconcile does, naming the media but listing no outputs.
        [("done", {"media_id": "src-40", "spent": 20})],
        # Its driver listed only a free upscale of the clip, which carries no prompt and so went in as a copy.
        [("submitted", {}), ("failed", {"outputs": [{"media_id": "src-40", "role": "copy"}], "spent": 20})],
        # Closed failed by story's reconcile, whose rule for failed is looser than clip_reconcile's.
        [("failed", {"media_id": None, "spent": 0, "reconciled": RECONCILED})],
        # Closed failed by hand, naming the clip: only clip_reconcile's own verdict shows the job made nothing.
        [("failed", {"source_media_id": "src-40", "spent": 0})],
        # Closed failed by clip_reconcile, then run again on this clip under the same job_id.
        [
            ("failed", {"source_media_id": "src-40", "outputs": [], "spent": 0, "reconciled": RECONCILED}),
            ("opening", {"kind": "edit", "source_media_id": "src-40", "prompt": JOB_PROMPT}),
        ],
        # Its driver saw neither a charge nor a version before the deadline, which does not show it made nothing.
        [("submitted", {}), ("failed", {"outputs": [], "spent": 0})],
    ],
)
def test_reconcile_leaves_an_orphan_open_when_a_closed_job_of_its_clip_and_prompt_holds_no_version(
    monkeypatch, tmp_path, closing
):
    # Re-review of plan C (2026-09-15, N1): the retry was closed without naming its version, which showed up later and
    # went to the orphan that never submitted, so the ledger counted 40 for one 20-credit edit (DECISIONS 2026-09-15).
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "dead", "src-40")
    _opening(ledger, "retry", "src-40")
    for status, fields in closing:
        ledger.append("retry", status, **fields)
    _reconcile_world(monkeypatch, [_record("src-40", 100), _record("src-40", 200, JOB_PROMPT)], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert out == [{"job_id": "dead", "verdict": "unknown", "credits_now": 275}]
    assert [r["status"] for r in ledger.rows("dead")] == ["opening"]


def test_reconcile_counts_a_job_that_clip_reconcile_closed_as_done_on_another_clip_as_a_rival(
    monkeypatch, tmp_path
):
    # Re-review of plan C (2026-09-16, G2): only a failed verdict shows a job made nothing. Job k was judged done on its
    # first clip, while its later attempt on this clip with this prompt holds no version here.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "k", "old-clip", workflows_before=["w-99", "w-100"])
    _opening(ledger, "k", "src-65", workflows_before=["w-99", "w-100"])
    _opening(ledger, "dead", "src-65", workflows_before=["w-99", "w-100"])
    elsewhere = [{"media_id": "old-clip", "workflow_id": "w-150", "role": "generated"}]
    ledger.append("k", "done", source_media_id="old-clip", outputs=elsewhere, spent=20, reconciled=RECONCILED)
    listing = [
        _record("old-clip", 99),
        _record("src-65", 100),
        _record("old-clip", 150, JOB_PROMPT),
        _record("src-65", 200, JOB_PROMPT),
    ]
    _reconcile_world(monkeypatch, listing, balance=255)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert out == [{"job_id": "dead", "verdict": "unknown", "credits_now": 255}]
    assert [r["status"] for r in ledger.rows("dead")] == ["opening"]


def test_reconcile_counts_an_open_job_as_a_rival_through_a_later_attempt_on_this_clip(monkeypatch, tmp_path):
    # Re-review of plan C (2026-09-16, G2): an open job is a rival whatever it holds (DECISIONS 2026-09-16), and it may
    # name this clip only in a later attempt under the same job_id.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "k", "old-clip", workflows_before=["w-99", "w-100"])
    _opening(ledger, "dead", "src-66", workflows_before=["w-99", "w-100"])
    _opening(ledger, "k", "src-66", workflows_before=["w-99", "w-100", "w-200"])
    held = [{"media_id": "src-66", "workflow_id": "w-300", "role": "generated"}]
    ledger.append("k", "pending", outputs=held)
    listing = [
        _record("old-clip", 99),
        _record("src-66", 100),
        _record("src-66", 200, JOB_PROMPT),
        _record("src-66", 300, JOB_PROMPT),
    ]
    _reconcile_world(monkeypatch, listing, balance=255)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [(r["job_id"], r["verdict"]) for r in out] == [("k", "unknown"), ("dead", "unknown")]
    assert [r["status"] for r in ledger.rows("dead")] == ["opening"]


def test_reconcile_closes_an_edit_whose_rival_clip_reconcile_already_found_made_nothing(
    monkeypatch, tmp_path
):
    # Re-review of plan C (2026-09-16, F2): the first call died before submitting, clip_reconcile closed it failed on an
    # equal balance and no new record, and the retry the refusal advises died too; that rival made nothing.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "first", "src-61")
    ledger.append(
        "first", "failed", kind="edit", source_media_id="src-61", outputs=[], spent=0, reconciled=RECONCILED
    )
    _opening(ledger, "retry", "src-61")
    ledger.append("retry", "submitted", kind="edit", source_media_id="src-61", prompt=JOB_PROMPT)
    _reconcile_world(monkeypatch, [_record("src-61", 100), _record("src-61", 200, JOB_PROMPT)], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert out == [{"job_id": "retry", "verdict": "done", "credits_now": 275}]
    assert ledger.rows("retry")[-1]["outputs"][0]["workflow_id"] == "w-200"


@pytest.mark.parametrize("listed", [["w-100", "w-200"], []])
def test_reconcile_leaves_an_orphan_open_while_an_open_job_of_its_clip_and_prompt_holds_a_version(
    monkeypatch, tmp_path, listed
):
    # Re-review of plan C (2026-09-16, F1): an open job stays a rival even when its pending row holds a version on the
    # clip, as decided first (DECISIONS 2026-09-15); only a closed job is cleared by the version it holds. A job whose
    # opening row lists no workflows is never judged, yet it is still open.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "a", "src-60")
    ledger.append("a", "submitted", kind="edit", source_media_id="src-60", prompt=JOB_PROMPT)
    _opening(ledger, "b", "src-60", workflows_before=listed)
    ledger.append("b", "submitted", kind="edit", source_media_id="src-60", prompt=JOB_PROMPT)
    held = [{"media_id": "src-60", "workflow_id": "w-300", "role": "generated"}]
    ledger.append("b", "pending", outputs=held)
    listing = [_record("src-60", 100), _record("src-60", 200, JOB_PROMPT), _record("src-60", 300, JOB_PROMPT)]
    _reconcile_world(monkeypatch, listing, balance=255)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [(r["job_id"], r["verdict"]) for r in out] == [("a", "unknown"), ("b", "unknown")]
    assert [r["status"] for r in ledger.rows()] == ["opening", "submitted", "opening", "submitted", "pending"]


def test_reconcile_never_gives_one_version_to_two_open_jobs_that_each_hold_another(monkeypatch, tmp_path):
    # Re-review of plan C (2026-09-16, F1): x and y each ran again on the clip with another prompt and hold that
    # version, but their first attempts typed this prompt, so each is an open rival for the one version nobody holds.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    for job in ("x", "y"):
        _opening(ledger, job, "src-63")
    for job, version in (("x", "w-300"), ("y", "w-400")):
        _opening(ledger, job, "src-63", prompt="make it rain", workflows_before=["w-100", "w-200"])
        held = [{"media_id": "src-63", "workflow_id": version, "role": "generated"}]
        ledger.append(job, "pending", outputs=held)
    listing = [
        _record("src-63", 100),
        _record("src-63", 200, JOB_PROMPT),
        _record("src-63", 300, "make it rain"),
        _record("src-63", 400, "make it rain"),
    ]
    _reconcile_world(monkeypatch, listing, balance=235)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [(r["job_id"], r["verdict"]) for r in out] == [("x", "unknown"), ("y", "unknown")]
    assert [r["status"] for r in ledger.rows() if r["status"] == "done"] == []


def test_reconcile_leaves_an_edit_open_while_a_second_version_with_its_prompt_is_still_rendering(
    monkeypatch, tmp_path
):
    # Re-review of plan C (2026-09-15, N1): two versions carry the job's prompt on its clip, one still rendering, so the
    # finished one may not be this job's (DECISIONS 2026-09-15).
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-43")
    rendering = {**_record("src-43", 300, JOB_PROMPT), "status": 1, "url": None}
    listing = [_record("src-43", 100), _record("src-43", 200, JOB_PROMPT), rendering]
    _reconcile_world(monkeypatch, listing, balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("j")] == ["opening"]


def test_reconcile_counts_every_opening_row_of_a_reused_job_id_as_a_rival(monkeypatch, tmp_path):
    # Re-review of plan C (2026-09-15, N1): job k is judged by its first row, on another clip, but its second attempt
    # ran on this clip with this prompt, so the new version may be k's.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "k", "old-clip", workflows_before=["w-99", "w-100"])
    _opening(ledger, "k", "src-46", workflows_before=["w-99", "w-100"])
    _opening(ledger, "dead", "src-46", workflows_before=["w-99", "w-100"])
    listing = [_record("old-clip", 99), _record("src-46", 100), _record("src-46", 200, JOB_PROMPT)]
    _reconcile_world(monkeypatch, listing, balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [(r["job_id"], r["verdict"]) for r in out] == [("k", "unknown"), ("dead", "unknown")]
    assert [r["status"] for r in ledger.rows()] == ["opening", "opening", "opening"]


def test_reconcile_counts_a_job_holding_versions_only_on_other_clips_as_a_rival(monkeypatch, tmp_path):
    # role_of matches prompts only (clips.py:41-44), so a driver lists a new version carrying its prompt on any clip as
    # generated; holding one on another clip tells nothing about the new version on this one.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "dead", "src-47", workflows_before=["w-100", "w-101"])
    _opening(ledger, "x", "src-47", workflows_before=["w-100", "w-101"])
    elsewhere = [{"media_id": "shot-2", "workflow_id": "w-300", "role": "generated"}]
    ledger.append("x", "done", outputs=elsewhere, spent=20)
    listing = [
        _record("src-47", 100),
        _record("shot-2", 101),
        _record("src-47", 200, JOB_PROMPT),
        _record("shot-2", 300, JOB_PROMPT),
    ]
    _reconcile_world(monkeypatch, listing, balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert out == [{"job_id": "dead", "verdict": "unknown", "credits_now": 275}]
    assert [r["status"] for r in ledger.rows("dead")] == ["opening"]


@pytest.mark.parametrize("holding_row", ["done", "pending"])
def test_reconcile_still_closes_an_edit_beside_a_job_of_its_clip_and_prompt_holding_its_own_version(
    monkeypatch, tmp_path, holding_row
):
    # A job that holds its version on the clip already has what it made, so the other new version is this job's. The
    # version may sit in a pending row its driver wrote before a person closed the job.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-48")
    _opening(ledger, "x", "src-48")
    held = [{"media_id": "src-48", "workflow_id": "w-200", "role": "generated"}]
    ledger.append("x", holding_row, outputs=held, spent=20)
    if holding_row == "pending":
        ledger.append("x", "done", spent=20)
    listing = [_record("src-48", 100), _record("src-48", 200, JOB_PROMPT), _record("src-48", 300, JOB_PROMPT)]
    _reconcile_world(monkeypatch, listing, balance=255)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["done"]
    assert ledger.rows("j")[-1]["outputs"][0]["workflow_id"] == "w-300"


def test_reconcile_never_lets_a_version_one_job_holds_clear_another_job_that_holds_none(
    monkeypatch, tmp_path
):
    # Re-review of plan C (2026-09-15, F3): holding is per job, so a third job's version on the clip says nothing about
    # a retry its driver closed failed before its own version showed up.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "dead", "src-49")
    _opening(ledger, "retry", "src-49")
    ledger.append("retry", "failed", outputs=[], spent=20)
    _opening(ledger, "third", "src-49")
    held = [{"media_id": "src-49", "workflow_id": "w-200", "role": "generated"}]
    ledger.append("third", "done", outputs=held, spent=20)
    listing = [_record("src-49", 100), _record("src-49", 200, JOB_PROMPT), _record("src-49", 300, JOB_PROMPT)]
    _reconcile_world(monkeypatch, listing, balance=255)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert out == [{"job_id": "dead", "verdict": "unknown", "credits_now": 255}]
    assert [r["status"] for r in ledger.rows("dead")] == ["opening"]


def test_reconcile_leaves_an_edit_open_when_two_new_versions_carry_its_prompt(monkeypatch, tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-22")
    listing = [_record("src-22", 100), _record("src-22", 200, JOB_PROMPT), _record("src-22", 300, JOB_PROMPT)]
    _reconcile_world(monkeypatch, listing, balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("j")] == ["opening"]


def test_reconcile_still_closes_an_edit_whose_own_pending_row_lists_the_version(monkeypatch, tmp_path):
    # The job's own rows are not another job: a driver that saw its version but died fetching it wrote `pending`.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-23")
    ledger.append(
        "j", "pending", outputs=[{"media_id": "src-23", "workflow_id": "w-200", "role": "generated"}]
    )
    _reconcile_world(monkeypatch, [_record("src-23", 100), _record("src-23", 200, JOB_PROMPT)], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["done"]


def test_reconcile_still_closes_an_edit_whose_version_another_job_listed_only_as_a_copy(
    monkeypatch, tmp_path
):
    # A driver lists every new record it sees; one not carrying its own prompt goes in as a copy, not as its output.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-24")
    _opening(ledger, "later", "src-25", prompt="something else")
    seen = [{"media_id": "src-24", "workflow_id": "w-200", "role": "copy"}]
    ledger.append("later", "done", outputs=seen, spent=20)
    _reconcile_world(monkeypatch, [_record("src-24", 100), _record("src-24", 200, JOB_PROMPT)], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["done"]


def test_reconcile_compares_prompts_as_the_driver_does_ignoring_outer_spaces(monkeypatch, tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-26", prompt=f"  {JOB_PROMPT} ")
    _reconcile_world(monkeypatch, [_record("src-26", 100), _record("src-26", 200, JOB_PROMPT)], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["done"]


def test_reconcile_never_judges_an_opening_row_whose_prompt_is_only_spaces(monkeypatch, tmp_path):
    # The driver refuses only newlines, so a prompt of spaces can reach the ledger; it would match an upscale's empty one.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-27", prompt="   ")
    _no_flow_reads(monkeypatch)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert out == [{"job_id": "j", "verdict": "unknown", "credits_now": None}]


def test_reconcile_never_takes_an_upscale_for_the_jobs_own_record(monkeypatch, tmp_path):
    # Probe 2026-09-15 (out/upscale-probe-1789471163): a free 1080p download adds a version on the same media with a
    # new workflow id ending in _upsampled and no prompt, which the old rule wrote up as `done, spent: 0`.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "u", "src-16")
    upscale = {**_record("src-16", 100, ""), "workflow_id": "w-100_upsampled"}
    _reconcile_world(monkeypatch, [_record("src-16", 100), upscale], balance=295)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("u")] == ["opening"]


@pytest.mark.parametrize("balance", [275, 295])
def test_reconcile_leaves_a_record_another_job_already_holds_to_that_job(monkeypatch, tmp_path, balance):
    # Review 2026-09-15: a job that died before submitting was retried under a new job_id; the retry spent 20 and
    # listed its record, yet reconcile gave the same record to the dead job, so the ledger counted 40 for one edit.
    # The taken record still counts as new for `failed`, whatever the balance (DECISIONS 2026-09-15).
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "dead", "src-17")
    _opening(ledger, "retry", "src-17")
    taken = [{"media_id": "src-17", "workflow_id": "w-200", "role": "generated"}]
    ledger.append("retry", "done", outputs=taken, spent=20)
    _reconcile_world(
        monkeypatch, [_record("src-17", 100), _record("src-17", 200, JOB_PROMPT)], balance=balance
    )

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert out == [{"job_id": "dead", "verdict": "unknown", "credits_now": balance}]
    assert [r["status"] for r in ledger.rows("dead")] == ["opening"]


def test_reconcile_leaves_a_job_open_while_its_record_is_still_rendering(monkeypatch, tmp_path):
    # DECISIONS 2026-09-15: `done` waits for the job's record to be finished (is_done), so a reconcile run too early
    # leaves the job open and a later run closes it.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-18")
    rendering = {**_record("src-18", 200, JOB_PROMPT), "status": 1, "url": None}
    _reconcile_world(monkeypatch, [_record("src-18", 100), rendering], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("j")] == ["opening"]


def test_reconcile_never_judges_an_opening_row_without_its_prompt(monkeypatch, tmp_path):
    # Rows written before opening rows carried the prompt (DECISIONS 2026-09-15) cannot tell the job's own record from
    # an upscale or another job's: left for a person, with no listing read.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        "old",
        "opening",
        kind="edit",
        source_media_id="src-19",
        credits_before=295,
        project="p",
        workflows_before=["w-100"],
    )
    _no_flow_reads(monkeypatch)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert out == [{"job_id": "old", "verdict": "unknown", "credits_now": None}]
    assert [r["status"] for r in ledger.rows("old")] == ["opening"]


def test_reconcile_never_judges_an_opening_row_whose_workflow_set_is_empty(monkeypatch, tmp_path):
    # Review of plan C (2026-09-15): an empty set can never show the source clip as seen, so the job is always unknown,
    # yet Chrome was opened to find that out.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-50", workflows_before=[])
    _no_flow_reads(monkeypatch)

    assert not clips.reconcile_needs_flow("p", out_dir=tmp_path)
    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert out == [{"job_id": "j", "verdict": "unknown", "credits_now": None}]
    assert [r["status"] for r in ledger.rows("j")] == ["opening"]


def test_reconcile_takes_a_listed_prompt_with_outer_spaces_for_the_jobs_own(monkeypatch, tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-51")
    listing = [_record("src-51", 100), _record("src-51", 200, f" {JOB_PROMPT}  ")]
    _reconcile_world(monkeypatch, listing, balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["done"]


def test_reconcile_leaves_a_version_without_a_url_open(monkeypatch, tmp_path):
    # `is_done` needs a url as well as status 3, the same test the driver waits on before it fetches.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-52")
    no_url = {**_record("src-52", 200, JOB_PROMPT), "url": None}
    _reconcile_world(monkeypatch, [_record("src-52", 100), no_url], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("j")] == ["opening"]


def test_reconcile_leaves_a_version_another_jobs_pending_row_lists_as_generated(monkeypatch, tmp_path):
    # Whatever clip a job worked on, a version its pending row lists as generated is held by it: the driver writes
    # pending when the download failed.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "a", "src-53", workflows_before=["w-100", "w-101"])
    _opening(ledger, "other", "src-54", workflows_before=["w-100", "w-101"])
    held = [{"media_id": "src-53", "workflow_id": "w-200", "role": "generated"}]
    ledger.append("other", "pending", outputs=held)
    listing = [_record("src-53", 100), _record("src-54", 101), _record("src-53", 200, JOB_PROMPT)]
    _reconcile_world(monkeypatch, listing, balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [(r["job_id"], r["verdict"]) for r in out] == [("a", "unknown"), ("other", "unknown")]
    assert [r["status"] for r in ledger.rows()] == ["opening", "opening", "pending"]


def test_reconcile_never_judges_a_job_its_driver_already_closed_as_failed(monkeypatch, tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "f", "src-55")
    ledger.append("f", "failed", outputs=[], spent=0)
    _no_flow_reads(monkeypatch)

    assert asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path)) == []
    assert [r["status"] for r in ledger.rows("f")] == ["opening", "failed"]


def test_reconcile_never_closes_a_row_that_does_not_name_its_kind(monkeypatch, tmp_path):
    # Only an edit puts its version on the source clip, so a row that does not say it is one is left for a person.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        "k",
        "opening",
        source_media_id="src-56",
        credits_before=295,
        project="p",
        workflows_before=["w-100"],
        prompt=JOB_PROMPT,
    )
    _reconcile_world(monkeypatch, [_record("src-56", 100), _record("src-56", 200, JOB_PROMPT)], balance=275)

    out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp_path))

    assert [r["verdict"] for r in out] == ["unknown"]
    assert [r["status"] for r in ledger.rows("k")] == ["opening"]


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
    _reconcile_world(monkeypatch, [_record("src-12", 100), _record("src-12", 200, JOB_PROMPT)], balance=275)

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
    _reconcile_world(monkeypatch, [_record("src-6", 100), _record("src-6", 200, JOB_PROMPT)], balance=285)

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
    # clip look new, so a job that spent nothing was written `done, spent: 0`; the old clock rule said failed. The clip
    # already holds a finished version with the job's prompt (story sends one prompt to every edit), so only the set
    # check keeps it from being taken as new.
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    _opening(ledger, "j", "src-13", workflows_before=listed)
    _reconcile_world(monkeypatch, [_record("src-13", 100, JOB_PROMPT), _record("other", 50)], balance=295)

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

            async def is_enabled(self):
                return True

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

            async def count(self):
                return 1

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


class _SaveFramePage:
    """The clip editor as measured 2026-09-18: one icon-only button whose accessible name is 'Save frame',
    and a listing that only shows the new image about forty seconds later."""

    def __init__(
        self,
        appears_after: int = 2,
        paints_after: int = 1,
        has_button: bool = True,
        notice: str | None = "Saving frame...",
    ):
        self.clicked = 0
        self.waited_ms = 0
        self.appears_after = appears_after
        self.keyboard = _Keyboard()
        # Measured 2026-09-18 on the live editor: the player is a CANVAS and it stays blank for about five
        # seconds after the page is ready. A click before it paints saves a completely black image.
        self.paints_after = paints_after
        self.has_button = has_button
        self.notice = notice
        self.brightness_reads = 0
        self.painted_when_clicked = None

    async def wait_for_timeout(self, ms):
        self.waited_ms += ms

    async def evaluate(self, script, arg=None):
        if "snack" in script:
            return [self.notice] if self.notice else []
        assert "canvas" in script
        self.brightness_reads += 1
        if self.paints_after is None:
            return 0.0
        return 0.0 if self.brightness_reads <= self.paints_after else 118.4

    def get_by_role(self, role, name=None):
        page = self

        class _Button:
            @property
            def first(self):
                return self

            async def count(self):
                return 1 if page.has_button else 0

            async def inner_text(self):
                return "Save frame"

            async def click(self, timeout=None):
                page.clicked += 1
                page.painted_when_clicked = page.brightness_reads > (page.paints_after or 10**9)

        assert role == "button"
        return _Button()

    def locator(self, selector):
        return _Clickable()


class _SaveFrameSession:
    def __init__(self, page):
        self.page = page
        self.urls = []

    async def goto(self, url, *, ready=None, timeout_ms=60_000):
        self.urls.append(url)

    project_url = staticmethod(lambda project_id: f"https://flow.google.com/project/{project_id}")


def _listings(pages):
    """reader.project answers each of these in turn, so a test can make the frame show up late."""
    answers = list(pages)

    async def fake_project(session, project_id, settle=10.0, *, versions=False):
        return answers.pop(0) if len(answers) > 1 else answers[0]

    return fake_project


def test_save_frame_waits_for_the_image_flow_indexes_late(monkeypatch):
    """Measured 2026-09-18: the click fires rpc maseQ and the grid shows 'Saved frame from <clip>' about 40 s
    later, so a driver that reads the listing once right after the click reports a failure that did not happen."""
    page = _SaveFramePage()
    session = _SaveFrameSession(page)
    old = {"media": [{"id": "m1", "kind": "video", "title": "Red paper boat glides"}]}
    new = {
        "media": [
            {"id": "m1", "kind": "video", "title": "Red paper boat glides"},
            {"id": "frame-1", "kind": "image", "title": "Saved frame from Red paper boat glides"},
        ]
    }
    monkeypatch.setattr(clips.reader, "project", _listings([old, old, new]))
    monkeypatch.setattr(clips, "INDEX_STEP_S", 0.01)

    result = asyncio.run(clips.save_frame(session, "P", "m1"))

    assert result["media_id"] == "frame-1"
    assert result["kind"] == "image"
    assert page.clicked == 1, "the frame button is clicked exactly once"
    assert page.waited_ms > 0, "it waits for Flow to index rather than reading once"


def test_save_frame_says_so_when_no_image_ever_appears(monkeypatch):
    page = _SaveFramePage()
    session = _SaveFrameSession(page)
    same = {"media": [{"id": "m1", "kind": "video", "title": "Red paper boat glides"}]}
    monkeypatch.setattr(clips.reader, "project", _listings([same]))
    # The real wait is 90 s; a test proving the giving-up branch must not pay for it.
    monkeypatch.setattr(clips, "INDEX_WAIT_S", 0.05)
    monkeypatch.setattr(clips, "INDEX_STEP_S", 0.01)

    with pytest.raises(RuntimeError, match="no new media"):
        asyncio.run(clips.save_frame(session, "P", "m1"))
    assert page.clicked == 1, "a save that produced nothing is never clicked twice"


def test_save_frame_waits_for_the_editor_to_paint_before_it_clicks(monkeypatch):
    """Measured 2026-09-18: the editor draws into a canvas about five seconds after the page is ready, and the
    Save frame button uploads whatever that canvas holds (the maseQ body carries the PNG itself). Clicking on
    the blind three second wait saved a 1080x1920 image whose mean luminance was 0, pure black, while the clip's
    own first frame measured 111."""
    page = _SaveFramePage(paints_after=3)
    session = _SaveFrameSession(page)
    old = {"media": [{"id": "m1", "kind": "video", "title": "Person speaking to camera"}]}
    new = {
        "media": [
            {"id": "m1", "kind": "video", "title": "Person speaking to camera"},
            {"id": "frame-1", "kind": "image", "title": "Saved frame from Person speaking to camera"},
        ]
    }
    monkeypatch.setattr(clips.reader, "project", _listings([old, new]))
    monkeypatch.setattr(clips, "INDEX_STEP_S", 0.01)
    monkeypatch.setattr(clips, "PAINT_STEP_S", 0.001)
    monkeypatch.setattr(clips, "PAINT_WAIT_S", 0.02)

    result = asyncio.run(clips.save_frame(session, "P", "m1"))

    assert page.painted_when_clicked is True, "it must not click while the canvas is still blank"
    assert result["media_id"] == "frame-1"


def test_save_frame_refuses_when_the_editor_never_paints(monkeypatch):
    """A black frame that Flow happily stores is worse than an error: the agent feeds it to the next shot as an
    initial frame and only finds out by looking at the picture."""
    page = _SaveFramePage(paints_after=None)
    session = _SaveFrameSession(page)
    monkeypatch.setattr(clips.reader, "project", _listings([{"media": []}]))
    monkeypatch.setattr(clips, "PAINT_STEP_S", 0.001)
    monkeypatch.setattr(clips, "PAINT_WAIT_S", 0.005)

    with pytest.raises(TimeoutError, match="blank"):
        asyncio.run(clips.save_frame(session, "P", "m1"))
    assert page.clicked == 0, "nothing is saved when there is no frame to save"


def test_save_frame_ignores_a_new_media_that_is_not_the_saved_frame(monkeypatch):
    """The wait is 90 s wide, so anything Flow indexes inside it (a generation landing, an upload) is new too.
    Handing that back as the saved frame sends the caller off with someone else's media id."""
    page = _SaveFramePage(paints_after=1)
    session = _SaveFrameSession(page)
    old = {"media": [{"id": "m1", "kind": "video", "title": "Person speaking to camera"}]}
    intruder = {
        "media": [
            {"id": "m1", "kind": "video", "title": "Person speaking to camera"},
            {"id": "gen-9", "kind": "video", "title": "A dancer in a studio", "created": 99},
        ]
    }
    then = {
        "media": intruder["media"]
        + [
            {
                "id": "frame-1",
                "kind": "image",
                "title": "Saved frame from Person speaking to camera",
                "created": 100,
            }
        ]
    }
    monkeypatch.setattr(clips.reader, "project", _listings([old, intruder, then]))
    monkeypatch.setattr(clips, "INDEX_STEP_S", 0.01)
    monkeypatch.setattr(clips, "PAINT_STEP_S", 0.001)
    monkeypatch.setattr(clips, "PAINT_WAIT_S", 0.02)

    result = asyncio.run(clips.save_frame(session, "P", "m1"))

    assert result["media_id"] == "frame-1", "a video that landed meanwhile is not the frame that was saved"


def test_save_frame_says_so_when_the_editor_has_no_save_control(monkeypatch):
    page = _SaveFramePage(paints_after=1, has_button=False)
    session = _SaveFrameSession(page)
    monkeypatch.setattr(clips.reader, "project", _listings([{"media": []}]))
    monkeypatch.setattr(clips, "PAINT_STEP_S", 0.001)
    monkeypatch.setattr(clips, "PAINT_WAIT_S", 0.02)

    with pytest.raises(LookupError, match="Save frame"):
        asyncio.run(clips.save_frame(session, "P", "m1"))
    assert page.clicked == 0


def test_save_frame_says_whether_flow_ever_started_the_save(monkeypatch):
    """Measured 2026-09-18: a click Flow accepts raises a 'Saving frame...' notice at once, and one run in three
    produced no image at all. The 90 s refusal is the same either way, so it has to carry which of the two
    happened: a slow save to wait out, or a click that started nothing."""
    page = _SaveFramePage(paints_after=1, notice=None)
    session = _SaveFrameSession(page)
    monkeypatch.setattr(clips.reader, "project", _listings([{"media": []}]))
    monkeypatch.setattr(clips, "INDEX_WAIT_S", 0.02)
    monkeypatch.setattr(clips, "INDEX_STEP_S", 0.01)
    monkeypatch.setattr(clips, "PAINT_STEP_S", 0.001)
    monkeypatch.setattr(clips, "PAINT_WAIT_S", 0.02)
    monkeypatch.setattr(clips, "NOTICE_STEP_S", 0.001)
    monkeypatch.setattr(clips, "NOTICE_WAIT_S", 0.005)

    with pytest.raises(RuntimeError, match="never started"):
        asyncio.run(clips.save_frame(session, "P", "m1"))

    page = _SaveFramePage(paints_after=1, notice="Saving frame...")
    session = _SaveFrameSession(page)
    monkeypatch.setattr(clips.reader, "project", _listings([{"media": []}]))

    with pytest.raises(RuntimeError, match="accepted the click"):
        asyncio.run(clips.save_frame(session, "P", "m1"))


class _ListeningSession:
    def __init__(self):
        self.page = _Page()


class _SaidFailed:
    """Stands in for FlowReplies: Flow answered that it failed the job. The interface is pinned against the
    real class by test_the_editor_path_hears_flow_through_the_same_reader_as_the_gen_path."""

    said: ClassVar[dict] = {
        "workflow_id": "wf-1",
        "statuses": [6, 4],
        "reasons": ["PUBLIC_ERROR_SOMETHING"],
    }

    def __init__(self):
        self.attached = 0

    def on_response(self, response):
        self.attached += 1

    def reported_failed(self):
        return True

    async def report(self):
        return dict(self.said)


def _spending_but_barren(monkeypatch, tmp_path, replies_class):
    """A job that submits, moves the balance, and never produces a record of its own."""
    sleeps: list[float] = []

    async def fake_snapshot(session, project_id):
        return ([], set())

    async def fake_credits(session):
        return {"balance": 300 if not sleeps else 280}

    async def fake_open(session, project_id, media_id):
        return None

    async def fake_menu_item(session, button, item):
        return _Clickable()

    async def fake_await_submit(session, click, **kwargs):
        await click()
        return {"uwAyfb": [[]]}

    async def counting_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(clips, "_snapshot", fake_snapshot)
    monkeypatch.setattr(clips.reader, "credits", fake_credits)
    monkeypatch.setattr(clips, "_open", fake_open)
    monkeypatch.setattr(clips, "_menu_item", fake_menu_item)
    monkeypatch.setattr(clips, "_await_submit", fake_await_submit)
    monkeypatch.setattr(clips.asyncio, "sleep", counting_sleep)
    monkeypatch.setattr(clips.composer_mod, "FlowReplies", replies_class)

    async def prompt_is_there(page, box, start, prompt):
        return True

    monkeypatch.setattr(clips, "_prompt_ready", prompt_is_there)
    return sleeps


def test_an_editor_job_stops_waiting_as_soon_as_flow_says_it_failed(monkeypatch, tmp_path):
    """Measured on the gen path 2026-09-13: Flow can take the click, charge, render to 23% and drop the job
    with no record and no message. The editor tools waited out the full 240 s for that and then said only
    "nothing was generated", which is the cost of not listening to what Flow itself replied."""
    sleeps = _spending_but_barren(monkeypatch, tmp_path, _SaidFailed)
    session = _ListeningSession()

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(
            clips._generate_from_editor(
                # Short on purpose: with the early exit gone this loop would spin out the whole wait, and a
                # mutant has to fail fast rather than hang the suite.
                session,
                "p",
                "src",
                "dress her",
                kind="edit",
                out_dir=tmp_path,
                job_id="j",
                wait=20.0,
            )
        )

    # 10 s is this loop's own beat; anything else in the list belongs to another wait.
    assert 10 not in sleeps, f"it kept polling after Flow said the job failed: {sleeps[:8]}"
    assert "wf-1" in str(caught.value), caught.value
    assert "PUBLIC_ERROR_SOMETHING" in str(caught.value), caught.value


def test_the_outcome_row_of_an_editor_job_carries_what_flow_said(monkeypatch, tmp_path):
    _spending_but_barren(monkeypatch, tmp_path, _SaidFailed)

    with pytest.raises(RuntimeError):
        asyncio.run(
            clips._generate_from_editor(
                _ListeningSession(),
                "p",
                "src",
                "dress her",
                kind="edit",
                out_dir=tmp_path,
                job_id="j",
                wait=5.0,
            )
        )

    last = gen.Ledger(tmp_path / "ledger.jsonl").rows("j")[-1]
    assert last["status"] == "failed", last
    assert last["flow"]["workflow_id"] == "wf-1", last
    assert last["flow"]["statuses"] == [6, 4], last


def test_the_editor_job_removes_its_listener_when_it_is_done(monkeypatch, tmp_path):
    """A listener left on the shared page keeps reading every later call's traffic."""
    _spending_but_barren(monkeypatch, tmp_path, _SaidFailed)
    session = _ListeningSession()

    with pytest.raises(RuntimeError):
        asyncio.run(
            clips._generate_from_editor(
                session, "p", "src", "dress her", kind="edit", out_dir=tmp_path, job_id="j", wait=5.0
            )
        )

    assert session.page.attached == ["response"], "it never listened to Flow at all"
    assert session.page.listeners == [], session.page.listeners


def test_the_editor_path_hears_flow_through_the_same_reader_as_the_gen_path():
    """The stub above is only honest while it matches the real class, and the gen path is where that class
    was measured against Flow."""
    from video.flow import composer

    assert clips.composer_mod is composer, "it must use the reader measured on the gen path"
    for name in ("on_response", "reported_failed", "report"):
        assert hasattr(composer.FlowReplies, name), name
