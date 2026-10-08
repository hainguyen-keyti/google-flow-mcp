"""The shape of the replies this server parses, kept as a signature and compared, so a change in Flow's wire format
is found by a free read instead of by a paid run that named no workflow (from 2026-10-05 the version field of every
generation record was null, and nothing noticed until 2026-10-08).

A payload is folded into a skeleton: a scalar becomes its kind (null, bool, int, float, uuid, url, str), a list of
lists collapses into one merged record under "*", and any other list stays positional. A merged record keeps, per
position, the kinds seen there, and a structure one fixture showed as null keeps its inside under "opt", so a
listing of 3 clips and one of 40 fold to the same skeleton while a field that moved or changed kind does not. The
baseline, flow_wire.json beside this file, is written by scripts/acceptance/wire_baseline.py from the test fixtures,
which are replies Flow sent (redacted); `video flow check` folds the live free reads and compares. A field null in
some records is the same field; a scalar null in every live record where the baseline always had a value (the
version field of 2026-10-05), a kind the baseline never saw at that position, a position the baseline had that is
gone, a renamed rpc, or a record list where a tuple stood, is drift; a new position, a value appearing where the
baseline had null, or a whole structure gone null (a project with no scenes) is said and is not drift.

Raw replies are written to the folder VIDEO_CAPTURE_REPLIES names by the composer's reader (the paid path), so a
fixture is always a reply Flow sent and never one typed (a typed "CAE" in every fixture hid the null for three days);
the free reads do not capture yet (reader.py sits outside plan AQ's radius), so their fixtures are refreshed by hand.
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
DRIFT = frozenset({"kind changed", "position gone", "shape changed", "rpc not heard", "kind emptied"})


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
    # Two or more tuples side by side are a record list. One alone stays a tuple (a recipe arm holds the model
    # tuple alone in some records and beside a row list in others), and `_rows` bridges it to a record list when
    # the other side is one (a listing's field held one record in the fixture and three live, 2026-10-08).
    if len(items) >= 2 and all(isinstance(item, list) for item in items):
        merged = items[0]
        for item in items[1:]:
            merged = merge(merged, item)
        return {"*": merged}
    return items


def _rows(shape: Any) -> Any:
    """A tuple whose parts are all tuples, read as the one record shape they share; None for anything else."""
    if isinstance(shape, list) and shape and all(isinstance(part, list) for part in shape):
        merged = shape[0]
        for part in shape[1:]:
            merged = merge(merged, part)
        return merged
    return None


def _is_records(shape: Any) -> bool:
    return isinstance(shape, dict) and "*" in shape


STRUCTURAL = frozenset({"list", "records", "object"})


def _is_opt(shape: Any) -> bool:
    return isinstance(shape, dict) and "opt" in shape


def _structural(shape: Any) -> bool:
    return isinstance(shape, list) or (isinstance(shape, dict) and ("*" in shape or "opt" in shape))


def _kinds(shape: Any) -> set[str]:
    if isinstance(shape, str):
        return {shape}
    if isinstance(shape, dict):
        if "any" in shape:
            return set(shape["any"])
        if "opt" in shape:
            return _kinds(shape["opt"]) | {"null"}
        return {"records"} if "*" in shape else {"object"}
    return {"list"}


def merge(a: Any, b: Any) -> Any:
    """One skeleton standing for both: equal parts stay, positions merge pairwise (a shorter tuple reads absent
    where the longer goes on), a structure one side showed as null or absent keeps its inside under "opt" (the
    status cell, the character entry and the recipe arm were thrown away as {"any": [...]} before the technical
    review of plan AQ, B1), and parts of different shapes become the set of kinds seen."""
    if a == b:
        return a
    if isinstance(a, list) and isinstance(b, list):
        longer = max(len(a), len(b))
        return [
            merge(a[i] if i < len(a) else "absent", b[i] if i < len(b) else "absent") for i in range(longer)
        ]
    if isinstance(a, dict) and isinstance(b, dict) and "*" in a and "*" in b:
        return {"*": merge(a["*"], b["*"])}
    for records, other in ((a, b), (b, a)):
        if _is_records(records) and _rows(other) is not None:
            return {"*": merge(records["*"], _rows(other))}
    for soft, other in ((a, b), (b, a)):
        if isinstance(soft, str) and soft in SOFT and _structural(other):
            return other if _is_opt(other) else {"opt": other}
    if (_is_opt(a) and _structural(b)) or (_is_opt(b) and _structural(a)):
        inner_a = a["opt"] if _is_opt(a) else a
        inner_b = b["opt"] if _is_opt(b) else b
        return {"opt": merge(inner_a, inner_b)}
    return {"any": sorted(_kinds(a) | _kinds(b))}


def compare(base: Any, now: Any, path: str = "") -> list[tuple[str, str, str]]:
    """What changed between the baseline's skeleton and a live one, as (kind, position, detail) rows."""
    if now == base:
        return []
    now_kinds = _kinds(now)
    base_kinds = _kinds(base)
    if now_kinds <= SOFT:
        # Null everywhere where the baseline always had a value: the version field turned null on 2026-10-05 and
        # was read by nobody for three days. A structure gone null (a project with no scenes) is said, not drift.
        if base_kinds <= SOFT or base_kinds & SOFT or _is_opt(base):
            return []
        kind = "structure emptied" if base_kinds <= STRUCTURAL else "kind emptied"
        return [(kind, path, f"{sorted(base_kinds)} -> {sorted(now_kinds)}")]
    if base_kinds <= SOFT:
        return [("kind appeared", path, f"{sorted(base_kinds)} -> {sorted(now_kinds)}")]
    if _is_opt(base):
        return compare(base["opt"], now["opt"] if _is_opt(now) else now, path)
    if _is_opt(now):
        return compare(base, now["opt"], path)
    if isinstance(now, list) and not now and "records" in base_kinds:
        # A fresh project lists nothing: an empty record list says nothing about the shape of a record.
        return []
    if _is_records(base) and _rows(now) is not None:
        return compare(base["*"], _rows(now), f"{path}[*]")
    if _is_records(now) and _rows(base) is not None:
        return compare(_rows(base), now["*"], f"{path}[*]")
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
    kind = "shape changed" if (extra | base_kinds) & STRUCTURAL else "kind changed"
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
