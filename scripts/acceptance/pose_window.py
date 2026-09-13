"""Acceptance: does a still-stance base clip widen the window where two Omni edits share one pose?

    uv run python scripts/acceptance/pose_window.py --out out/still

Exit code is 1 when any row is FAIL. Costs nothing: it only reads clips already on disk.

The `baseline` row re-measures the OLD base clip `tryon2-04` every run: its two Omni edits share a pose
over a 3.42s span on a bare-thigh crop, under the current ceiling rule. A ruler that cannot reproduce
something already agreed is never allowed to speak about the new clip, so instrument drift shows up as
a failure here rather than as a plausible-looking number about the material under test.

That figure is rule-bound, not eternal. The same pair measured 2.70s under the earlier fixed ceiling of
2.9; the ceiling then gained a scale term and a re-render floor (see `ceiling_for`), and the number it
reproduces moved with it. Change the rule again and this constant must be re-derived, not retuned until
it passes.

Both a span and the longest unbroken run are reported. Only the span gates, but a cut can only use
continuous frames, and the two can differ sharply: on 2026-09-13 the still-stance pair spanned 5.04s
while its longest unbroken run was 1.46s.

Every check is a module-level function taking explicit arguments, so a mutation can be pointed at it
(the pattern of scripts/acceptance/ledger_integrity.py).

Rule 8 of the repo, paid for three times on 2026-09-13: a crop that self-tests clean can still be
pointing at the wrong thing. So `window` also renders the crop it measured to a JPEG and names it in
the row, because a number nobody looked at is how a band of floral fabric got mistaken for a thigh.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from video import framediff, gen

FPS = 24
NOISE_RATIO = 1.8
# Each base clip gets its own band, because each generation frames her differently: the still-stance
# base stands further back, so the old band lands on her shin and the floor instead of her thigh.
THIGH_OLD: framediff.Crop = (100, 40, 250, 1125)
# y 1020-1110 is the corridor between the lowest reach of the floral set's frill hem (~1016) and the
# knees (~1116), measured across the whole clip by brightness: pale fabric reads 150-180, skin 85-100.
# The first attempt at y 985-1065 had its top third inside hem territory at some timestamps.
THIGH_NEW: framediff.Crop = (150, 90, 295, 1020)
# A region that CANNOT move in either clip: mirror frame and wall in the still-stance pair, photo wall
# in the older one. Whatever it differs by is pure Omni re-render, never pose.
BG: framediff.Crop = (120, 160, 10, 230)
SELF_TEST_CROPS: list[framediff.Crop] = [THIGH_OLD, (200, 200, 260, 200), (300, 300, 200, 600)]

OLD_BASE = Path("out/story2/tryon2-04.mp4")
OLD_PAIR = (
    Path("out/story2/tryon2-04_worn.mp4"),
    Path("out/story2/d69766c5-260c-4e73-bc90-1081ac023e7a_a76d30a2.mp4"),
)
# Re-derived 2026-09-13 when the ceiling gained its re-render floor. Under the earlier fixed ceiling of
# 2.9 this same pair measured 2.70s; the rule changed, so the number it reproduces changed with it. The
# row still catches instrument drift, it just pins the current rule instead of the retired one.
BASELINE_WIDTH = 3.42
BASELINE_TOLERANCE = 0.12
TARGET_WIDTH = 4.00
NEW_EDIT_JOBS = ("still-edit-1", "still-edit-2")
BUDGET = 50


def ceiling_for(
    left: list[bytes],
    right: list[bytes],
    bg_left: list[bytes],
    bg_right: list[bytes],
    ratio: float = NOISE_RATIO,
) -> float:
    """How far two frames may differ and still count as the same pose, in this material's own units.

    Two corrections, both measured rather than chosen. First, a fixed number cannot travel between
    clips: the still-stance base frames her further away, so the same movement shifts fewer pixels.
    Scaling by each pair's own median frame-to-frame motion takes the framing out of it.

    Second, that scaling alone fails on very still material. Omni re-renders the whole frame for every
    edit, which leaves a difference that does NOT shrink as the subject holds stiller. Measured
    2026-09-13: on the still pair the scaled ceiling fell to 0.65 while a patch of wall that cannot
    move differed by 1.18 to 2.17, so nothing could pass, whatever it was pointed at. Adding the
    background's own median difference puts the floor back where the material says it is.
    """
    medians = [
        statistics.median([framediff.diff(f[i], f[i + 1]) for i in range(len(f) - 1)]) for f in (left, right)
    ]
    n = min(len(bg_left), len(bg_right))
    floor = statistics.median([framediff.diff(bg_left[i], bg_right[i]) for i in range(n)])
    return ratio * (sum(medians) / len(medians)) + floor


def longest_run(flags: list[bool], fps: int = FPS) -> tuple[float, float]:
    """The longest UNBROKEN stretch, which is what a cut can actually use; a span may have holes."""
    best = start = run = begin = 0
    for i, ok in enumerate(flags):
        if not ok:
            run = 0
            continue
        begin = i if run == 0 else begin
        run += 1
        if run > best:
            best, start = run, begin
    return best / fps, start / fps


def window(left, right, bg_left, bg_right, ratio: float = NOISE_RATIO, fps: int = FPS):
    """First and last second holding the same pose, the ceiling that decided it, and the longest run."""
    ceiling = ceiling_for(left, right, bg_left, bg_right, ratio)
    flags = [framediff.diff(left[i], right[i]) <= ceiling for i in range(min(len(left), len(right)))]
    good = [i for i, ok in enumerate(flags) if ok]
    if not good:
        return None
    run, _ = longest_run(flags, fps)
    return min(good) / fps, max(good) / fps, (max(good) - min(good)) / fps, ceiling, run


def measure_pair(first: Path, second: Path, crop: framediff.Crop):
    return window(
        framediff.frames(first, FPS, crop),
        framediff.frames(second, FPS, crop),
        framediff.frames(first, FPS, BG),
        framediff.frames(second, FPS, BG),
    )


def render_crop(clips: list[Path], crop: framediff.Crop, out: Path, at: float = 2.0) -> Path | None:
    """Write the measured band to a JPEG so a human can see whether it is skin or fabric."""
    present = [c for c in clips if c.is_file()]
    if not present:
        return None
    args = ["ffmpeg", "-v", "error", "-y"]
    for clip in present:
        args += ["-ss", f"{at:.2f}", "-i", str(clip)]
    chain = "".join(
        f"[{i}:v]crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]},scale=300:-1[c{i}];"
        for i in range(len(present))
    )
    chain += "".join(f"[c{i}]" for i in range(len(present))) + f"vstack=inputs={len(present)}"
    out.parent.mkdir(parents=True, exist_ok=True)
    args += ["-filter_complex", chain, "-frames:v", "1", str(out)]
    if subprocess.run(args, capture_output=True, check=False).returncode:
        return None
    return out


def harness_check(clip: Path) -> tuple[tuple[str, str], tuple[str, str]]:
    """Identity and crop, reported as two rows because they fail for different reasons."""
    if not clip.is_file():
        missing = ("FAIL", f"{clip} is missing, the harness cannot prove anything")
        return missing, missing
    try:
        report = framediff.self_test(clip, SELF_TEST_CROPS)
    except framediff.HarnessBroken as broken:
        message = str(broken)
        if message.startswith("identity"):
            return ("FAIL", message), ("FAIL", "not reached, identity failed first")
        return ("PASS", "identity 0.000"), ("FAIL", message)
    spread = ", ".join(f"{value:.2f}" for value in report["crop_spread"])
    return ("PASS", f"identity {report['identity']:.3f}"), ("PASS", f"crops differ by {spread}")


def baseline_check(base: Path, pair: tuple[Path, Path]) -> tuple[str, str]:
    missing = [p for p in (base, *pair) if not p.is_file()]
    if missing:
        return "FAIL", f"old material missing: {[p.name for p in missing]}"
    found = measure_pair(pair[0], pair[1], THIGH_OLD)
    if found is None:
        return "FAIL", "the ruler found no matching frame at all in the pair it already measured"
    low, high, width, ceiling, run = found
    ok = abs(width - BASELINE_WIDTH) <= BASELINE_TOLERANCE
    return (
        "PASS" if ok else "FAIL",
        (
            f"old base re-measures {low:.2f}s to {high:.2f}s = {width:.2f}s at ceiling {ceiling:.2f}, "
            f"longest unbroken {run:.2f}s (expected {BASELINE_WIDTH:.2f}s +/- {BASELINE_TOLERANCE:.2f})"
        ),
    )


def paths_for(rows: list[dict], job_id: str) -> Path | None:
    """Resolve a job's output the way provenance_check does: a `path`, or an entry in `outputs`."""
    for row in rows:
        if row.get("job_id") != job_id or row.get("status") != "done":
            continue
        for candidate in [row.get("path"), *[o.get("path") for o in (row.get("outputs") or [])]]:
            if candidate:
                return Path(candidate)
    return None


EDITOR_KINDS = ("edit", "extend")


def ledger_check(rows: list[dict], budget: int = BUDGET) -> tuple[str, str]:
    """Money is only trusted when the books were opened before it could move.

    The two paths book differently and the gate has to know which is which. The clip editor
    (video/flow/clips.py) writes an `opening` row first, because merely opening the editor and
    choosing Extend already creates a paid job: 20 credits left the account that way on 2026-09-13
    while the ledger held nothing. The generation path (composer._submit) has no such gap, it writes
    `submitted` immediately before its single click, so `submitted` is that path's proof. Demanding
    `opening` of both would fail a correct generation run, which is what this gate did until it was
    measured against the real ledger.
    """
    if not rows:
        return "FAIL", "no ledger yet, nothing has been generated"
    problems = []
    per_job: dict[str, int] = {}
    for job in sorted({r.get("job_id") for r in rows if r.get("job_id")}):
        mine = [r for r in rows if r.get("job_id") == job]
        statuses = [r.get("status") for r in mine]
        editor = any(r.get("kind") in EDITOR_KINDS for r in mine)
        if editor and "opening" not in statuses:
            problems.append(f"{job} is an editor job with no opening row")
        if not editor and "submitted" not in statuses:
            problems.append(f"{job} moved without a submitted row")
        if any(r.get("status") == "failed" and (r.get("spent") or 0) for r in mine):
            problems.append(f"{job} has a failed row that still spent credits")
        # One job is charged once. `flow clip reconcile` appends a terminal row that REPEATS the spend
        # already written by the `pending` row, so adding every row bills the same clip twice: on
        # 2026-09-13 still-edit-2 summed to 40 while the balance only moved 20, and the gate would have
        # reported an overspend that never happened.
        per_job[job] = max((r.get("spent") or 0) for r in mine)
    spent = sum(per_job.values())
    if spent > budget:
        problems.append(f"spent {spent} over the {budget} approved")
    booked = f"spent {spent} of {budget}, every job booked before it could spend"
    return ("FAIL", "; ".join(problems)) if problems else ("PASS", booked)


def window_check(rows: list[dict], out: Path) -> tuple[str, str]:
    clips = [paths_for(rows, job) for job in NEW_EDIT_JOBS]
    if any(clip is None or not clip.is_file() for clip in clips):
        return "FAIL", f"{' and '.join(NEW_EDIT_JOBS)} have no finished clip yet in {out}"
    found = measure_pair(clips[0], clips[1], THIGH_NEW)
    picture = render_crop(list(clips), THIGH_NEW, out / "review" / "window_crop.jpg")
    seen = f"; look at {picture} before trusting this" if picture else "; crop render failed"
    if found is None:
        return "FAIL", f"the two edits never hold the same pose at any timestamp{seen}"
    low, high, width, ceiling, run = found
    ok = width >= TARGET_WIDTH
    return (
        "PASS" if ok else "FAIL",
        (
            f"{low:.2f}s to {high:.2f}s = {width:.2f}s at ceiling {ceiling:.2f}, longest unbroken "
            f"{run:.2f}s (need {TARGET_WIDTH:.2f}s span, old base was {BASELINE_WIDTH:.2f}s){seen}"
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acceptance for the still-stance base experiment.")
    parser.add_argument("--out", default="out/still")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    out = Path(args.out)
    rows = gen.Ledger(out / "ledger.jsonl").rows()

    identity_row, crop_row = harness_check(OLD_PAIR[0])
    findings = [
        {"name": "harness_identity", "status": identity_row[0], "detail": identity_row[1]},
        {"name": "harness_crop", "status": crop_row[0], "detail": crop_row[1]},
    ]
    for name, (status, detail) in (
        ("baseline", baseline_check(OLD_BASE, OLD_PAIR)),
        ("ledger", ledger_check(rows)),
        ("window", window_check(rows, out)),
    ):
        findings.append({"name": name, "status": status, "detail": detail})

    print(f"{'STATUS':7s} {'ROW':17s} DETAIL")
    for finding in findings:
        print(f"{finding['status']:7s} {finding['name']:17s} {finding['detail']}")
    failed = [f for f in findings if f["status"] == "FAIL"]
    print(f"\nrows={len(findings)} pass={len(findings) - len(failed)} fail={len(failed)}")
    if args.json:
        print(json.dumps(findings, indent=2, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
