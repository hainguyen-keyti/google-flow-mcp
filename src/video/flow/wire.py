"""The shape of the replies this server parses, kept as a signature and compared, so a change in Flow's wire format
is found by a free read instead of by a paid run that named no workflow (from 2026-10-05 the version field of every
generation record was null, and nothing noticed until 2026-10-08).

A payload is folded into a skeleton: a scalar becomes its kind (null, bool, int, float, uuid, url, str), a list of
two or more lists collapses into one merged record under "*", and any other list stays positional. A merged record
keeps, per position, the kinds seen there, so a listing of 3 clips and one of 40 fold to the same skeleton while a
field that moved or changed kind does not. The baseline, flow_wire.json beside this file, is written by
scripts/acceptance/wire_baseline.py from the test fixtures, which are replies Flow sent (redacted); `video flow check`
folds the live free reads and compares. null and absent are compatible with every kind, since Flow leaves a field
empty as often as it fills it; a kind seen now that the baseline never saw at that position, a position the baseline
had that is gone, or a record list where a tuple stood, is drift; a new position is reported and is not drift.

Raw replies are also written to the folder VIDEO_CAPTURE_REPLIES names, by the composer and by the free reads, so a
fixture is always a reply Flow sent and never one typed (a typed "CAE" in every fixture hid the null for three days).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

CAPTURE_ENV = "VIDEO_CAPTURE_REPLIES"
BASELINE = Path(__file__).with_name("flow_wire.json")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SOFT = frozenset({"null", "absent"})
DRIFT = frozenset({"kind changed", "position gone", "shape changed", "rpc not heard"})


def capture(rpcids: str, text: str) -> None:
    """Write a reply Flow sent, raw, into the capture folder when one is named; never raises."""
    folder = os.environ.get(CAPTURE_ENV)
    if not folder or not text:
        return
    try:
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{rpcids.replace(',', '+')}_{time.time_ns()}.txt").write_text(text, encoding="utf-8")
    except OSError:
        return


def skeleton(node: Any) -> Any:
    if node is None:
        return "null"
    if isinstance(node, bool):
        return "bool"
    if isinstance(node, int):
        return "int"
    if isinstance(node, float):
        return "float"
    if isinstance(node, str):
        if UUID_RE.match(node):
            return "uuid"
        if node.startswith(("http://", "https://")):
            return "url"
        return "str"
    if isinstance(node, dict):
        return {key: skeleton(value) for key, value in node.items()}
    items = [skeleton(item) for item in node]
    # A list of lists is a record list, one record included: measured 2026-10-08, a listing's field held one
    # record in the fixture and three live, and a rule that collapsed two or more called that a changed shape.
    if items and all(isinstance(item, list) for item in items):
        merged = items[0]
        for item in items[1:]:
            merged = merge(merged, item)
        return {"*": merged}
    return items


def _kinds(shape: Any) -> set[str]:
    if isinstance(shape, str):
        return {shape}
    if isinstance(shape, dict):
        if "any" in shape:
            return set(shape["any"])
        return {"records"} if "*" in shape else {"object"}
    return {"list"}


def merge(a: Any, b: Any) -> Any:
    """One skeleton standing for both: equal parts stay, positions merge pairwise (a shorter tuple reads absent
    where the longer goes on), and parts of different shapes become the set of kinds seen."""
    if a == b:
        return a
    if isinstance(a, list) and isinstance(b, list):
        longer = max(len(a), len(b))
        return [
            merge(a[i] if i < len(a) else "absent", b[i] if i < len(b) else "absent") for i in range(longer)
        ]
    if isinstance(a, dict) and isinstance(b, dict) and "*" in a and "*" in b:
        return {"*": merge(a["*"], b["*"])}
    return {"any": sorted(_kinds(a) | _kinds(b))}


def compare(base: Any, now: Any, path: str = "") -> list[tuple[str, str, str]]:
    """What changed between the baseline's skeleton and a live one, as (kind, position, detail) rows."""
    if now == base:
        return []
    now_kinds = _kinds(now)
    if now_kinds <= SOFT:
        return []
    base_kinds = _kinds(base)
    if base_kinds <= SOFT:
        return [("kind appeared", path, f"{sorted(base_kinds)} -> {sorted(now_kinds)}")]
    if isinstance(base, list) and isinstance(now, list):
        found: list[tuple[str, str, str]] = []
        for index, part in enumerate(base):
            where = f"{path}[{index}]"
            if index >= len(now):
                # Gone only where the baseline always saw it: a position it saw absent or null is optional.
                if not _kinds(part) & SOFT:
                    found.append(("position gone", where, f"was {_said(part)}"))
                continue
            found += compare(part, now[index], where)
        for index in range(len(base), len(now)):
            if not _kinds(now[index]) <= SOFT:
                found.append(("position added", f"{path}[{index}]", _said(now[index])))
        return found
    if isinstance(base, dict) and isinstance(now, dict) and "*" in base and "*" in now:
        return compare(base["*"], now["*"], f"{path}[*]")
    # Kinds, not shapes, decide the rest: a field the baseline saw as one kind and the live read as a mix of that
    # kind and null is the same field (measured 2026-10-08, 23 findings of a clean read were of that sort).
    extra = now_kinds - base_kinds - SOFT
    if not extra:
        return []
    structural = {"list", "records", "object"}
    kind = "shape changed" if (extra | base_kinds) & structural else "kind changed"
    return [(kind, path, f"{sorted(base_kinds)} -> {sorted(now_kinds)}")]


def _said(shape: Any) -> str:
    return json.dumps(shape)[:60]


def baseline(path: Path = BASELINE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"rpcs": {}}


def compare_frames(
    base: dict[str, Any], frames: dict[str, list[Any]], views: tuple[str, ...]
) -> list[tuple[str, str, str]]:
    """The live frames of the views read against the baseline: every rpc the baseline holds for those views must
    be heard and fold to a compatible skeleton."""
    found: list[tuple[str, str, str]] = []
    for rpcid, entry in sorted(base.get("rpcs", {}).items()):
        if entry.get("view") not in views:
            continue
        if rpcid not in frames:
            found.append(("rpc not heard", rpcid, f"expected on the {entry['view']} view"))
            continue
        live = skeleton(frames[rpcid][0])
        for payload in frames[rpcid][1:]:
            live = merge(live, skeleton(payload))
        found += [(kind, f"{rpcid}{where}", what) for kind, where, what in compare(entry["shape"], live)]
    return found


def drifted(findings: list[tuple[str, str, str]]) -> bool:
    return any(kind in DRIFT for kind, _, _ in findings)
