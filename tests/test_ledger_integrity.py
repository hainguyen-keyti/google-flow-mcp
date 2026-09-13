import importlib.util
from pathlib import Path

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
    assert len(findings) == 6


def test_every_row_has_a_stable_name_and_its_own_room(tmp_path):
    gate = load()
    names = [name for name, _ in gate.CHECKS]
    assert len(names) == len(set(names)), names
    gate.run_all(tmp_path)
    # One ledger per row, so a spend written by one check can never satisfy another.
    rooms = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    assert len(rooms) == len(names)


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
