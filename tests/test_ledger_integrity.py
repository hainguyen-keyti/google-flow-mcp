import importlib.util
from pathlib import Path

import pytest

from video.flow import reader

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "acceptance" / "ledger_integrity.py"


def load():
    spec = importlib.util.spec_from_file_location("ledger_integrity", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_gate_passes_on_the_code_as_it_stands(tmp_path):
    findings = load().run_all(tmp_path)
    failed = [f for f in findings if f["status"] == "FAIL"]
    assert failed == [], failed
    assert len(findings) == 9


def test_every_row_that_writes_has_a_stable_name_and_its_own_room(tmp_path):
    """One ledger per row, so a spend written by one check can never satisfy another.

    Changed on purpose 2026-09-27: the rows in WHOLE_RUN read the entire run instead of writing a ledger of
    their own, which is the only way "nothing this run wrote leaked" can be true rather than vacuous. They are
    exempt from the room rule by name, and every other row still gets its own.
    """
    gate = load()
    names = [name for name, _ in gate.CHECKS]
    assert len(names) == len(set(names)), names
    gate.run_all(tmp_path)
    rooms = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    writers = [name for name in names if name not in gate.WHOLE_RUN]
    assert len(rooms) == len(writers)
    assert gate.WHOLE_RUN, "a row that judges the whole run has to be named, not guessed at"


def test_a_broken_check_is_reported_not_swallowed(tmp_path):
    gate = load()

    def explode(room):
        raise RuntimeError("stub drifted")

    gate.CHECKS = (("boom", explode),)
    findings = gate.run_all(tmp_path)
    assert findings[0]["status"] == "FAIL"
    assert "stub drifted" in findings[0]["detail"]


def test_exit_code_is_one_only_when_a_row_failed():
    gate = load()
    assert gate.exit_code([{"name": "a", "status": "PASS", "detail": ""}]) == 0
    assert gate.exit_code([{"name": "a", "status": "FAIL", "detail": ""}]) == 1
    assert gate.exit_code([]) == 0


def test_the_gate_puts_the_balance_reader_back(tmp_path):
    # Measured 2026-09-14: the checks swapped a fake balance into reader.credits and never put it back, so
    # every later test in the same pytest run read a fake balance. tests/test_reader.py failed only when it
    # ran after this file, and after run_all reader.credits was _reads.<locals>.fake_credits.
    before = reader.credits

    load().run_all(tmp_path)

    assert reader.credits is before, reader.credits
    assert reader.credits.__qualname__ == "credits"


def test_the_leak_row_reads_what_the_other_checks_actually_wrote(tmp_path):
    """The row claims "nothing this run wrote may carry session material", and until 2026-09-27 it read its own
    empty room, so it passed a run in which every other check had leaked. A check has to read the thing it says
    it covers (CLAUDE.md rule 10)."""
    gate = load()

    def leaky(room):
        (room / "ledger.jsonl").write_text('{"job_id": "x", "note": "SAPISID=abc"}\n', encoding="utf-8")
        return "PASS", "wrote a row carrying session material"

    original = gate.CHECKS
    gate.CHECKS = (("leaks on purpose", leaky), *original)
    try:
        findings = gate.run_all(tmp_path)
    finally:
        gate.CHECKS = original

    leak_row = next(row for row in findings if row["name"] == "I2 no leak")
    assert leak_row["status"] == "FAIL", f"the leak row passed a run that leaked: {leak_row}"
    assert "ledger.jsonl" in leak_row["detail"], leak_row


def test_the_leak_row_runs_last_even_when_a_check_is_added_after_it(tmp_path):
    """Reading the whole run only works if it reads it LAST. Asserting that the constant happens to end with
    that row proves nothing: the next person appends a check and the guarantee quietly dies. So a check is
    appended AFTER it here, and the row still has to see what that check wrote."""
    gate = load()

    def late_leak(room):
        (room / "ledger.jsonl").write_text('{"job_id": "late", "note": "SAPISID=xyz"}\n', encoding="utf-8")
        return "PASS", "wrote a row carrying session material, after the leak row in CHECKS"

    original = gate.CHECKS
    gate.CHECKS = (*original, ("leaks last", late_leak))
    try:
        findings = gate.run_all(tmp_path)
    finally:
        gate.CHECKS = original

    assert findings[-1]["name"] == "I2 no leak", [row["name"] for row in findings]
    assert findings[-1]["status"] == "FAIL", findings[-1]


def test_no_acceptance_script_reads_a_ledger_by_hand():
    """`Ledger.rows` splits on the newline ALONE because `splitlines()` also breaks on U+2028, U+2029 and
    U+0085, which json keeps inside strings: one prompt holding one of them once made every later read of that
    ledger fail (review of plan D). Two acceptance scripts kept their own splitlines() copy long after that
    was known, so the rule is checked rather than remembered."""
    acceptance = SCRIPT.parent
    offenders = {}
    for script in sorted(acceptance.glob("*.py")):
        lines = [
            line.strip()
            for line in script.read_text(encoding="utf-8").splitlines()
            if "splitlines()" in line and ("json.loads" in line or "ledger" in line.lower())
        ]
        if lines:
            offenders[script.name] = lines

    assert offenders == {}, f"use gen.Ledger(path).rows() instead of splitlines(): {offenders}"


def test_the_shared_reader_survives_the_separator_that_breaks_a_hand_rolled_one(tmp_path):
    """The control for the rule above: a prompt holding U+2028 is ONE line of json, and the reader agrees
    while a hand-rolled splitlines() sees two and dies on the first half."""
    import json

    from video import gen

    path = tmp_path / "ledger.jsonl"
    row = {"job_id": "sep-1", "status": "done", "prompt": f"a line{chr(0x2028)}and its other half"}
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    assert gen.Ledger(path).rows() == [row]
    with pytest.raises(json.JSONDecodeError):
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
