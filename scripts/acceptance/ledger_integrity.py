"""Acceptance: no credit can leave the account through the clip editor without a row pointing at it.

    uv run python scripts/acceptance/ledger_integrity.py

Exit code is 1 when any row is FAIL. Runs entirely offline: no browser, no Flow, no credits. The editor
is driven against stubs that fail exactly where the real one failed on 2026-09-13, when 20 credits were
spent and the ledger held nothing, so the job had to be found by hand in Flow's listing.

Every check is a callable so a mutation can be pointed at it. The three mutations this gate must catch:
drop the `opening` row, teach `has_submitted` to block on `opening`, or let reconcile call a job done
while the balance never moved.
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


def _record(media_id: str, created: float) -> dict:
    return {
        "id": media_id,
        "workflow_id": f"w-{created:.0f}",
        "created": created,
        "status": 3,
        "url": "https://example/n",
        "prompt": PROMPT,
    }


def check_intent_extend(tmp: Path) -> tuple[str, str]:
    """Extend dies choosing the menu item, which is itself enough to create a paid job."""
    snapshot, credits = _reads()

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
    )
    return ("PASS" if ok else "FAIL"), f"rows={[r['status'] for r in rows]} first={rows[0] if rows else None}"


def check_intent_edit(tmp: Path) -> tuple[str, str]:
    """The edit path has the same stretch between opening the editor and the submit row."""
    snapshot, credits = _reads()

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
    ok = [r["status"] for r in rows] == ["opening"] and rows[0].get("source_media_id") == "src-2"
    return ("PASS" if ok else "FAIL"), f"rows={[r['status'] for r in rows]}"


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
    """A record newer than the row plus a balance that fell means the job really ran."""
    ledger = gen.Ledger(tmp / "ledger.jsonl")
    ledger.append("d", "opening", kind="extend", source_media_id="src-4", credits_before=START_BALANCE)
    stamp = ledger.rows("d")[0]["ts"]
    snapshot, credits = _reads(balance=AFTER_BALANCE, records=[_record("src-4", stamp + 10)])
    original = _patch(_snapshot=snapshot)
    clips.reader.credits = credits
    try:
        out = asyncio.run(clips.reconcile_editor(_Session(), "p", out_dir=tmp))
    finally:
        _restore(original)
    rows = gen.Ledger(tmp / "ledger.jsonl").rows("d")
    ok = (
        [r["verdict"] for r in out] == ["done"]
        and [r["status"] for r in rows] == ["opening", "done"]
        and rows[-1].get("spent") == START_BALANCE - AFTER_BALANCE
    )
    return ("PASS" if ok else "FAIL"), f"verdicts={[r['verdict'] for r in out]} spent={rows[-1].get('spent')}"


def check_reconcile_failed(tmp: Path) -> tuple[str, str]:
    """The job's clip is in this project, nothing is new there, and the balance never moved: nothing was bought.

    The clip's own older record has to be in the listing (DECISIONS 2026-09-15): editor rows name no project, so a
    listing without it may be another project's, and reconcile answers `unknown` there.
    """
    ledger = gen.Ledger(tmp / "ledger.jsonl")
    ledger.append("e", "opening", kind="edit", source_media_id="src-5", credits_before=START_BALANCE)
    stamp = ledger.rows("e")[0]["ts"]
    snapshot, credits = _reads(balance=START_BALANCE, records=[_record("src-5", stamp - 3_600)])
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
