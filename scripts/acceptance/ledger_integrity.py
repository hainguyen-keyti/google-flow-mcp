"""Acceptance: no credit can leave the account through the clip editor without a row pointing at it.

    uv run python scripts/acceptance/ledger_integrity.py

Exit code is 1 when any row is FAIL. Runs entirely offline: no browser, no Flow, no credits. The editor
is driven against stubs that fail exactly where the real one failed on 2026-09-13, when 20 credits were
spent and the ledger held nothing, so the job had to be found by hand in Flow's listing.

Every check is a callable so a mutation can be pointed at it. The mutations this gate must catch: drop the
`opening` row or the project and workflows it lists, teach `has_submitted` to block on `opening`, let reconcile
call a job done while the balance never moved, write a guess while the project holds a record the job did not
list, or judge another project's job against this listing.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from video import gen
from video.flow import clips

SECRET = re.compile(r"SAPISID=|__Secure-|Authorization:")
PROMPT = "keep going"
JOB_PROMPT = "make it night"
START_BALANCE = 295
AFTER_BALANCE = 275


class _Clickable:
    first = property(lambda self: self)

    def __init__(self, text: str = PROMPT) -> None:
        self.text = text

    async def click(self, timeout=None):
        return None

    async def inner_text(self):
        return self.text

    async def is_disabled(self):
        return False

    async def wait_for(self, state=None, timeout=None):
        return None

    def filter(self, **kwargs):
        return self


class _Keyboard:
    async def type(self, text, delay=None):
        return None

    async def press(self, key):
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


_CREDITS = "reader.credits"


def _patch(**fakes):
    """Swap module attributes on clips, returning the originals so the check can put them back.

    The balance reader is saved too, because every check assigns clips.reader.credits by hand right after
    calling this. That attribute lives on the shared video.flow.reader module, so leaving a fake behind fed
    a fake balance to every later test in the same pytest run (measured 2026-09-14: it broke
    tests/test_reader.py, which was the first test to read a real balance after this gate).
    """
    original = {name: getattr(clips, name) for name in fakes}
    original[_CREDITS] = clips.reader.credits
    for name, value in fakes.items():
        setattr(clips, name, value)
    return original


def _restore(original):
    for name, value in original.items():
        if name == _CREDITS:
            clips.reader.credits = value
        else:
            setattr(clips, name, value)


async def _credits(balance):
    async def fake(session):
        return {"balance": balance}

    return fake


def _reads(balance=START_BALANCE, records=None, scenes=None):
    async def fake_snapshot(session, project_id):
        return (list(records or []), set(scenes or ()))

    async def fake_credits(session):
        return {"balance": balance}

    return fake_snapshot, fake_credits


def _record(media_id: str, created: float, prompt: str = PROMPT) -> dict:
    return {
        "id": media_id,
        "workflow_id": f"w-{created:.0f}",
        "created": created,
        "status": 3,
        "url": "https://example/n",
        "prompt": prompt,
    }


def _opening(ledger: gen.Ledger, job_id: str, media_id: str, **fields) -> None:
    """An editor job's opening row as the driver writes it: project "p", the one workflow `_record(_, 1)` makes, and
    JOB_PROMPT, which only the job's own record carries."""
    row = {"kind": "edit", "credits_before": START_BALANCE, "project": "p", "workflows_before": ["w-1"]}
    ledger.append(job_id, "opening", source_media_id=media_id, **{**row, "prompt": JOB_PROMPT, **fields})


def check_intent_extend(tmp: Path) -> tuple[str, str]:
    """Extend dies choosing the menu item, which is itself enough to create a paid job.

    The row must also name the project and every workflow the listing held, or reconcile can never judge the job.
    """
    snapshot, credits = _reads(records=[_record("src-1", 1), _record("other", 2)])

    async def dying_menu_item(session, button, item):
        raise LookupError("menu gone")

    async def fake_open(session, project_id, media_id):
        return None

    original = _patch(_snapshot=snapshot, _open=fake_open, _menu_item=dying_menu_item)
    clips.reader.credits = credits
    try:
        try:
            asyncio.run(
                clips._generate_from_editor(
                    _Session(), "p", "src-1", PROMPT, kind="extend", out_dir=tmp, job_id="a", wait=1.0
                )
            )
        except LookupError:
            pass
    finally:
        _restore(original)
    rows = gen.Ledger(tmp / "ledger.jsonl").rows("a")
    ok = (
        [r["status"] for r in rows] == ["opening"]
        and rows[0].get("source_media_id") == "src-1"
        and rows[0].get("credits_before") == START_BALANCE
        and rows[0].get("project") == "p"
        and rows[0].get("workflows_before") == ["w-1", "w-2"]
        and rows[0].get("prompt") == PROMPT
    )
    return ("PASS" if ok else "FAIL"), f"rows={[r['status'] for r in rows]} first={rows[0] if rows else None}"


def check_intent_edit(tmp: Path) -> tuple[str, str]:
    """The edit path has the same stretch between opening the editor and the submit row."""
    snapshot, credits = _reads(records=[_record("src-2", 1)])

    async def dying_open(session, project_id, media_id):
        raise RuntimeError("editor never rendered")

    original = _patch(_snapshot=snapshot, _open=dying_open)
    clips.reader.credits = credits
    try:
        try:
            asyncio.run(
                clips._generate_from_editor(
                    _Session(), "p", "src-2", PROMPT, kind="edit", out_dir=tmp, job_id="b", wait=1.0
                )
            )
        except RuntimeError:
            pass
    finally:
        _restore(original)
    rows = gen.Ledger(tmp / "ledger.jsonl").rows("b")
    ok = (
        [r["status"] for r in rows] == ["opening"]
        and rows[0].get("source_media_id") == "src-2"
        and rows[0].get("project") == "p"
        and rows[0].get("workflows_before") == ["w-1"]
        and rows[0].get("prompt") == PROMPT
    )
    return ("PASS" if ok else "FAIL"), f"rows={[r['status'] for r in rows]} first={rows[0] if rows else None}"


def check_retry_not_blocked(tmp: Path) -> tuple[str, str]:
    """An orphaned row records a spend; it must not burn the job id it belongs to."""
    ledger = gen.Ledger(tmp / "ledger.jsonl")
    ledger.append("c", "opening", kind="extend", source_media_id="src-3", credits_before=START_BALANCE)
    ours = _record("new", 2)
    snapshots = iter([([], set()), ([ours], {"scene-1"})])

    async def fake_snapshot(session, project_id):
        return next(snapshots)

    async def fake_credits(session):
        return {"balance": AFTER_BALANCE}

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

    original = _patch(
        _snapshot=fake_snapshot,
        _open=fake_open,
        _menu_item=fake_menu_item,
        capture=fake_capture,
        _fetch_with_retry=fake_fetch,
    )
    sleep_was = clips.asyncio.sleep
    clips.asyncio.sleep = no_sleep
    clips.reader.credits = fake_credits
    blocked = ""
    try:
        asyncio.run(
            clips._generate_from_editor(
                _Session(), "p", "src-3", PROMPT, kind="extend", out_dir=tmp, job_id="c", wait=5.0
            )
        )
    except gen.AlreadySubmitted as exc:
        blocked = str(exc)
    finally:
        clips.asyncio.sleep = sleep_was
        _restore(original)
    statuses = [r["status"] for r in gen.Ledger(tmp / "ledger.jsonl").rows("c")]
    ok = not blocked and statuses == ["opening", "opening", "submitted", "done"]
    detail = f"rows={statuses}" + (f", refused: {blocked}" if blocked else "")
    return ("PASS" if ok else "FAIL"), detail


def check_reconcile_done(tmp: Path) -> tuple[str, str]:
    """A finished version on the source clip that the opening row did not list, carrying the edit's own prompt, means
    the edit really ran; an extend is never closed as done (DECISIONS 2026-09-15).

    The record goes into the done row's outputs, so no other job can be given it later.
    """
    cases = {
        "edit": ([_record("src-4", 1), _record("src-4", 2, JOB_PROMPT)], ("src-4", "w-2")),
    }
    seen = {}
    for kind, (records, expected) in cases.items():
        room = tmp / kind
        room.mkdir(parents=True, exist_ok=True)
        _opening(gen.Ledger(room / "ledger.jsonl"), "d", "src-4", kind=kind)
        snapshot, credits = _reads(balance=AFTER_BALANCE, records=records)
        original = _patch(_snapshot=snapshot)
        clips.reader.credits = credits
        try:
            out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=room))
        finally:
            _restore(original)
        rows = gen.Ledger(room / "ledger.jsonl").rows("d")
        taken = [(o.get("media_id"), o.get("workflow_id")) for o in rows[-1].get("outputs") or []]
        seen[kind] = ([r["verdict"] for r in out], [r["status"] for r in rows], rows[-1].get("spent"), taken)
    ok = all(
        verdicts == ["done"]
        and statuses == ["opening", "done"]
        and spent == START_BALANCE - AFTER_BALANCE
        and taken == [cases[kind][1]]
        for kind, (verdicts, statuses, spent, taken) in seen.items()
    )
    return ("PASS" if ok else "FAIL"), str(seen)


def check_reconcile_failed(tmp: Path) -> tuple[str, str]:
    """The job's clip is listed, every record was already in its opening row, and the balance never moved.

    That is the only case where nothing was bought (DECISIONS 2026-09-15): a listing without the clip holds no
    evidence about the job, and a record the row did not list may be what the job bought.
    """
    ledger = gen.Ledger(tmp / "ledger.jsonl")
    _opening(ledger, "e", "src-5")
    snapshot, credits = _reads(balance=START_BALANCE, records=[_record("src-5", 1)])
    original = _patch(_snapshot=snapshot)
    clips.reader.credits = credits
    try:
        out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp))
    finally:
        _restore(original)
    rows = gen.Ledger(tmp / "ledger.jsonl").rows("e")
    ok = (
        [r["verdict"] for r in out] == ["failed"]
        and [r["status"] for r in rows] == ["opening", "failed"]
        and rows[-1].get("spent") == 0
    )
    return ("PASS" if ok else "FAIL"), f"verdicts={[r['verdict'] for r in out]} spent={rows[-1].get('spent')}"


def check_reconcile_unknown(tmp: Path) -> tuple[str, str]:
    """Either sign alone is no proof, so nothing may be written: a record the job did not list while the balance
    never moved, or a balance that moved while nothing is new (the balance moves with no spend, 195 to 245). Nor
    may a job whose opening row never saw its clip be judged: every record of that clip would look new. A free 1080p
    upscale (new workflow `..._upsampled`, no prompt, measured 2026-09-15) is not the job's record, and neither is a
    record another job's outputs already hold, which still counts as new against `failed`. An edit is never given a
    record on another clip (an extend copies edited versions onto new media with their prompts), and an extend is
    never closed as done (DECISIONS 2026-09-15). Nor is an edit given a version while another job of its clip and
    prompt holds none there, even a closed one: its driver writes `failed` when the version shows up after the deadline.

    Each case gets its own ledger, since one reconcile reads one balance for every job.
    """
    upscale = {**_record("src-6", 1, ""), "workflow_id": "w-1_upsampled"}
    cases = {
        "new_record": (START_BALANCE, [_record("src-6", 1), _record("elsewhere", 2)], ["w-1"], []),
        "balance_moved": (AFTER_BALANCE, [_record("src-6", 1)], ["w-1"], []),
        "clip_unseen_at_open": (START_BALANCE, [_record("src-6", 1), _record("elsewhere", 2)], ["w-2"], []),
        "upscale": (START_BALANCE, [_record("src-6", 1), upscale], ["w-1"], []),
        "taken_by_another_job": (
            START_BALANCE,
            [_record("src-6", 1), _record("src-6", 2, JOB_PROMPT)],
            ["w-1"],
            ["w-2"],
        ),
        "record_on_another_clip": (
            START_BALANCE,
            [_record("src-6", 1), _record("other-clip", 2, JOB_PROMPT)],
            ["w-1"],
            [],
        ),
        "extend_with_its_prompt": (
            AFTER_BALANCE,
            [
                _record("src-6", 1),
                {**_record("copy-clip", 1), "workflow_id": "w-copy"},
                _record("new-clip", 2, JOB_PROMPT),
            ],
            ["w-1"],
            [],
        ),
        "rival_closed_without_its_version": (
            AFTER_BALANCE,
            [_record("src-6", 1), _record("src-6", 2, JOB_PROMPT)],
            ["w-1"],
            [],
        ),
        "open_rival_holding_a_version": (
            AFTER_BALANCE,
            [_record("src-6", 1), _record("src-6", 2, JOB_PROMPT), _record("src-6", 3, JOB_PROMPT)],
            ["w-1"],
            [],
        ),
    }
    seen = {}
    for name, (balance, records, listed, taken) in cases.items():
        room = tmp / name
        room.mkdir(parents=True, exist_ok=True)
        kind = "extend" if name.startswith("extend") else "edit"
        _opening(gen.Ledger(room / "ledger.jsonl"), "u", "src-6", workflows_before=listed, kind=kind)
        if name.startswith("rival"):
            _opening(gen.Ledger(room / "ledger.jsonl"), "retry", "src-6", workflows_before=listed)
            gen.Ledger(room / "ledger.jsonl").append("retry", "failed", outputs=[], spent=20)
        if name.startswith("open_rival"):
            _opening(gen.Ledger(room / "ledger.jsonl"), "b", "src-6", workflows_before=["w-1", "w-2"])
            held = [{"media_id": "src-6", "workflow_id": "w-3", "role": "generated"}]
            gen.Ledger(room / "ledger.jsonl").append("b", "pending", outputs=held)
        if taken:
            outputs = [
                {"media_id": "src-6", "workflow_id": workflow, "role": "generated"} for workflow in taken
            ]
            gen.Ledger(room / "ledger.jsonl").append("other", "done", outputs=outputs, spent=20)
        snapshot, credits = _reads(balance=balance, records=records)
        original = _patch(_snapshot=snapshot)
        clips.reader.credits = credits
        try:
            out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=room))
        finally:
            _restore(original)
        rows = gen.Ledger(room / "ledger.jsonl").rows("u")
        seen[name] = ([r["verdict"] for r in out if r["job_id"] == "u"], [r["status"] for r in rows])
    ok = all(verdicts == ["unknown"] and statuses == ["opening"] for verdicts, statuses in seen.values())
    return ("PASS" if ok else "FAIL"), str(seen)


def check_reconcile_other_project(tmp: Path) -> tuple[str, str]:
    """Another project's job is reported, never judged against this listing and never written.

    Review 2026-09-15: judging it here wrote `failed, spent: 0`, which story reads as free to run again.
    """
    ledger = gen.Ledger(tmp / "ledger.jsonl")
    _opening(ledger, "o", "src-7", project="q")
    reads = []
    snapshot, credits = _reads(balance=START_BALANCE, records=[_record("src-7", 1)])

    async def counted_snapshot(session, project_id):
        reads.append(project_id)
        return await snapshot(session, project_id)

    original = _patch(_snapshot=counted_snapshot)
    clips.reader.credits = credits
    try:
        out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp))
    finally:
        _restore(original)
    rows = gen.Ledger(tmp / "ledger.jsonl").rows("o")
    ok = (
        [(r["verdict"], r.get("project")) for r in out] == [("skipped", "q")]
        and [r["status"] for r in rows] == ["opening"]
        and reads == []
    )
    detail = f"jobs={out} rows={[r['status'] for r in rows]} listing_reads={len(reads)}"
    return ("PASS" if ok else "FAIL"), detail


def check_scrub_keeps_rows(tmp: Path) -> tuple[str, str]:
    """Session-like text in a row is redacted inside its own string, so the line stays readable JSON with its job_id, and a
    job_id the scrub would change is refused before any row is written (DECISIONS 2026-09-16).

    Review of plan B: the scrub ran over the whole JSON line, so `Authorization: denied` broke the line and every later
    read of any ledger under out/ failed, and `__Secure- x` was stored as `[redacted] x`, which the job_id guard never
    finds again.
    """
    ledger = gen.Ledger(tmp / "ledger.jsonl")
    ledger.append("scrub-ok", "submitted", prompt="Authorization: denied", argv=["video", "SAPISID=abc"])
    refused = None
    try:
        gen.Ledger(tmp / "refused.jsonl").append("__Secure- x", "submitted")
    except ValueError as exc:
        refused = str(exc)
    try:
        ids = [row["job_id"] for row in ledger.rows()]
    except ValueError as exc:
        return "FAIL", f"ledger unreadable: {type(exc).__name__}: {exc}"
    leaked = [p.name for p in tmp.iterdir() if p.is_file() and SECRET.search(p.read_text(errors="ignore"))]
    ok = ids == ["scrub-ok"] and refused is not None and not (tmp / "refused.jsonl").exists() and not leaked
    return ("PASS" if ok else "FAIL"), f"job_ids={ids} refused={refused!r} leaked={leaked}"


def check_no_leak(tmp: Path) -> tuple[str, str]:
    """Nothing this run wrote may carry session material."""
    leaked = [p.name for p in tmp.rglob("*") if p.is_file() and SECRET.search(p.read_text(errors="ignore"))]
    return ("PASS" if not leaked else "FAIL"), (f"session material in {leaked}" if leaked else "clean")


CHECKS = (
    ("intent extend", check_intent_extend),
    ("intent edit", check_intent_edit),
    ("retry", check_retry_not_blocked),
    ("reconcile done", check_reconcile_done),
    ("reconcile failed", check_reconcile_failed),
    ("reconcile unknown", check_reconcile_unknown),
    ("reconcile other project", check_reconcile_other_project),
    ("scrub keeps rows", check_scrub_keeps_rows),
    ("I2 no leak", check_no_leak),
)


def run_all(tmp: Path) -> list[dict[str, str]]:
    """Each check gets its own ledger file so one cannot mask another."""
    findings = []
    for name, check in CHECKS:
        room = tmp / name.replace(" ", "_")
        room.mkdir(parents=True, exist_ok=True)
        try:
            status, detail = check(room)
        except Exception as exc:  # noqa: BLE001
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        findings.append({"name": name, "status": status, "detail": detail})
    return findings


def exit_code(findings: list[dict[str, str]]) -> int:
    return 1 if any(f["status"] == "FAIL" for f in findings) else 0


def main(argv: list[str] | None = None) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ledger_integrity_"))
    try:
        findings = run_all(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{'STATUS':7s} {'ROW':16s} DETAIL")
    for finding in findings:
        print(f"{finding['status']:7s} {finding['name']:16s} {finding['detail']}")
    failed = [f for f in findings if f["status"] == "FAIL"]
    print(f"\nrows={len(findings)} pass={len(findings) - len(failed)} fail={len(failed)}")
    if "--json" in (argv or sys.argv[1:]):
        print(json.dumps(findings, indent=2, ensure_ascii=False))
    return exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
