"""Acceptance for Plan 1: run the CLI against the real account and print a PASS/FAIL table with numbers.

    uv run python scripts/acceptance/flow_coverage.py --project <id>            # $0 matrix
    uv run python scripts/acceptance/flow_coverage.py --project <id> --spend    # adds the credit-spending rows

Exit code is 1 when any row is FAIL. PARTIAL rows carry a measured reason and do not fail the run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

# A second session on the same profile (a running MCP server) waits for the lease instead of failing at once.
os.environ.setdefault("GFLOW_CLI_LEASE_WAIT_SECONDS", "900")
from pathlib import Path

ROWS: list[tuple[str, str, str]] = []
ONLY: set[str] = set()
LOG_DIR: Path | None = None
SECRET = re.compile(r"SAPISID=|__Secure-|Authorization:")


def _keep_log(args: tuple[str, ...], stdout: str, stderr: str) -> None:
    """Every command's full output lands in <out>/logs so a FAIL row can be explained afterwards."""
    if LOG_DIR is None:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9]+", "_", " ".join(args[:4]))[:60]
    path = LOG_DIR / f"{int(time.time())}_{stem}.log"
    path.write_text(
        f"$ video {' '.join(args)}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}", encoding="utf-8"
    )


def wanted(*names: str) -> bool:
    """With --rows, run only the listed rows (and the reads they depend on)."""
    return not ONLY or any(name in ONLY for name in names)


def run(*args: str, timeout: int = 900) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["uv", "run", "--no-sync", "video", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _keep_log(args, "", f"timeout after {timeout}s")
        return 124, "", f"timeout after {timeout}s: video {' '.join(args)}"
    _keep_log(args, proc.stdout, proc.stderr)
    if SECRET.search(proc.stdout + proc.stderr):
        row("I2 secret leak", "FAIL", f"session material in output of {' '.join(args)}")
    return proc.returncode, proc.stdout, proc.stderr


def row(name: str, status: str, detail: str) -> None:
    ROWS.append((name, status, detail))
    print(f"{status:7s} {name:22s} {detail}", flush=True)


def last_json(text: str):
    """The JSON document ending the output: compact or pretty-printed, after any log lines."""
    lines = text.strip().splitlines()
    for start in range(len(lines)):
        tail = "\n".join(lines[start:]).strip()
        if not tail.startswith(("{", "[")):
            continue
        try:
            return json.loads(tail)
        except ValueError:
            continue
    raise ValueError(f"no JSON document in output: {text[-200:]!r}")


def ffprobe(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(out.stdout or "{}")


def free_matrix(project: str, out_dir: Path) -> None:
    lane_projects, balance, media = -1, "", []
    if wanted("lane", "projects", "project crud"):
        code, out, _ = run("flow", "lane")
        match = re.search(r"(\w+) projects=(\d+)", out)
        lane_ok = code == 0 and match and match.group(1) == "MIGRATED"
        if wanted("lane"):
            row(
                "lane",
                "PASS" if lane_ok else "FAIL",
                out.strip().splitlines()[-1] if out.strip() else f"exit {code}",
            )
        lane_projects = int(match.group(2)) if match else -1

    if wanted("projects"):
        code, out, _ = run("flow", "projects", "--json")
        projects = last_json(out) if code == 0 else []
        ok = code == 0 and len(projects) >= 1 and len(projects) == lane_projects
        row("projects", "PASS" if ok else "FAIL", f"{len(projects)} projects (lane saw {lane_projects})")

    if wanted("credits", "upscale 1080p"):
        code, out, _ = run("flow", "credits")
        balance = out.strip().splitlines()[-1] if out.strip() else ""
        if wanted("credits"):
            row("credits", "PASS" if code == 0 and balance.isdigit() else "FAIL", f"balance={balance}")

    if wanted("media", "download", "upscale 1080p"):
        code, out, _ = run("flow", "media", project, "--json")
        info = last_json(out) if code == 0 else {}
        media = info.get("media", [])
        if wanted("media"):
            row(
                "media",
                "PASS" if code == 0 and len(media) >= 1 else "FAIL",
                f"{len(media)} items, models={','.join(info.get('models', []))}",
            )

    if wanted("tools"):
        code, out, _ = run("flow", "tools", project, "--json")
        tools = last_json(out) if code == 0 else []
        row("tools", "PASS" if code == 0 and len(tools) >= 1 else "FAIL", f"{len(tools)} tools")

    if wanted("characters"):
        code, out, _ = run("flow", "characters", project, "--json")
        characters = last_json(out) if code == 0 else None
        row(
            "characters",
            "PASS" if code == 0 and isinstance(characters, list) else "FAIL",
            f"{len(characters) if isinstance(characters, list) else characters} characters",
        )

    if wanted("download", "upscale 1080p"):
        target = next((m for m in media if m.get("kind") == "video" and m.get("url")), None)
        dl_dir = out_dir / f"acceptance_{int(time.time())}"
        if not target:
            row("download", "FAIL", "no video with a url in the listing")
            row("upscale 1080p", "FAIL", "no video with a url in the listing")
        if target and wanted("download"):
            code, out, _ = run("flow", "download", project, target["id"], "--out", str(dl_dir))
            path = Path(out.strip().splitlines()[-1]) if code == 0 and out.strip() else None
            probe = ffprobe(path) if path and path.exists() else {}
            duration = float(probe.get("format", {}).get("duration", 0) or 0)
            row("download", "PASS" if duration > 0 else "FAIL", f"{path} duration={duration:.2f}s")
        if target and wanted("upscale 1080p"):
            code, out, err = run(
                "flow", "clip", "download", project, target["id"], "--quality", "1080p", "--out", str(dl_dir)
            )
            path = Path(out.strip().splitlines()[-1]) if code == 0 and out.strip() else None
            probe = ffprobe(path) if path and path.exists() else {}
            height = max((int(s.get("height") or 0) for s in probe.get("streams", [])), default=0)
            code2, out2, _ = run("flow", "credits")
            after = out2.strip().splitlines()[-1] if out2.strip() else ""
            spent = int(balance) - int(after) if balance.isdigit() and after.isdigit() else None
            row(
                "upscale 1080p",
                "PASS" if height >= 1080 else "FAIL",
                f"{path} height={height} credits_spent={spent}" if path else err.strip()[-160:],
            )

    if wanted("project crud"):
        code, out, _ = run("flow", "project", "create", "--title", "acceptance probe")
        created = last_json(out) if code == 0 else {}
        new_id = created.get("id")
        if new_id:
            code2, out2, _ = run("flow", "project", "rename", new_id, "acceptance renamed")
            code3, out3, _ = run("flow", "project", "delete", new_id, "--yes")
            deleted = last_json(out3) if code3 == 0 else {}
            ok = code2 == 0 and code3 == 0 and deleted.get("remaining") == lane_projects
            row(
                "project crud",
                "PASS" if ok else "FAIL",
                f"created {new_id}, remaining={deleted.get('remaining')}",
            )
        else:
            row("project crud", "FAIL", f"create exit {code}")

    if wanted("scene crud"):
        code, out, _ = run("flow", "scene", "create", project, "--title", "acceptance scene")
        scene = last_json(out) if code == 0 else {}
        scene_id = scene.get("scene_id")
        if scene_id:
            code2, out2, _ = run("flow", "scene", "delete", project, scene_id, "--yes")
            row(
                "scene crud",
                "PASS" if code2 == 0 else "FAIL",
                f"scene {scene_id} rpcids={scene.get('rpcids')}",
            )
        else:
            row("scene crud", "FAIL", f"create exit {code}")

    if wanted("agent mode"):
        code, out, _ = run("flow", "agent", "mode", project, "on")
        on = last_json(out) if code == 0 else {}
        code2, out2, _ = run("flow", "agent", "mode", project, "off")
        off = last_json(out2) if code2 == 0 else {}
        row(
            "agent mode",
            "PASS" if on.get("enabled") is True and off.get("enabled") is False else "FAIL",
            f"on={on} off={off}",
        )


def character_round_trip(project: str) -> None:
    code, out, _ = run(
        "flow",
        "character",
        "create",
        project,
        "young woman with short black hair and a warm smile, studio portrait, soft light",
        "--name",
        "Acceptance Probe",
        "--personality",
        "Calm and precise.",
    )
    created = last_json(out) if code == 0 else {}
    entity = created.get("entity_id")
    if not entity:
        row("character crud", "FAIL", f"create exit {code}")
        return
    code2, out2, _ = run("flow", "characters", project, "--json")
    listed = last_json(out2) if code2 == 0 else []
    seen = any(c.get("entity_id") == entity and c.get("name") == "Acceptance Probe" for c in listed)
    code3, _, err3 = run("flow", "character", "delete", project, entity, "--yes")
    why = "" if code3 == 0 else " " + err3.strip().splitlines()[-1][-200:] if err3.strip() else " (no stderr)"
    row(
        "character crud",
        "PASS" if seen and code3 == 0 else "FAIL",
        f"entity {entity} listed={seen} deleted={code3 == 0}{why}",
    )


def spend_matrix(project: str, out_dir: Path, ref_image: Path | None, source_media: str | None) -> None:
    def video_row(name: str, *args: str, expect_outputs: int = 1, group: str = "gen") -> dict:
        scope = ("--project", project) if group == "gen" else ()
        code, out, err = run(group, *args, *scope, "--out", str(out_dir))
        result = last_json(out) if code == 0 else {}
        paths = [o.get("path") for o in result.get("outputs", []) if o.get("path")]
        probe = ffprobe(Path(paths[0])) if paths and Path(paths[0]).exists() else {}
        duration = float(probe.get("format", {}).get("duration", 0) or 0)
        spent = result.get("credits_before", 0) - result.get("credits_after", 0) if result else None
        if duration > 0 and len(paths) >= expect_outputs and result.get("status", "done") == "done":
            status = "PASS"
        elif duration > 0:
            status = "PARTIAL"
        else:
            status = "PARTIAL" if "frame picker" in err else "FAIL"
        detail = (
            f"duration={duration:.2f}s spent={spent} outputs={len(paths)}/{expect_outputs} {paths[:1]}"
            if result
            else err.strip()[-160:]
        )
        row(name, status, detail)
        return result

    def image_row(name: str, *args: str) -> None:
        code, out, _ = run("gen", *args, "--project", project, "--out", str(out_dir))
        result = last_json(out) if code == 0 else {}
        outputs = result.get("outputs", [])
        dims = f"{outputs[0].get('width')}x{outputs[0].get('height')}" if outputs else ""
        row(
            name,
            "PASS" if outputs and outputs[0].get("width", 0) >= 1024 else "FAIL",
            f"{dims} {[o.get('path') for o in outputs[:1]]}",
        )

    if wanted("t2i"):
        image_row(
            "t2i",
            "t2i",
            "a ceramic teacup on a wooden table, soft light",
            "--model",
            "nano2",
            "--aspect",
            "16:9",
        )
    source = source_media
    if wanted("t2v") or (source is None and wanted("extend", "omni edit")):
        t2v = video_row(
            "t2v",
            "t2v",
            "a small wooden sailboat model on a desk, slow push in",
            "--model",
            "veo-lite",
            "--aspect",
            "16:9",
        )
        source = source or (t2v.get("outputs") or [{}])[0].get("media_id")
    if wanted("extend"):
        if source:
            video_row(
                "extend",
                "clip",
                "extend",
                project,
                source,
                "the sailboat keeps rocking gently, camera holds still",
                group="flow",
            )
        else:
            row("extend", "FAIL", "no t2v media id to extend")
    if wanted("omni edit"):
        if source:
            video_row(
                "omni edit",
                "clip",
                "edit",
                project,
                source,
                "make it night time with warm lamp light",
                group="flow",
            )
        else:
            row("omni edit", "FAIL", "no t2v media id to edit")

    if wanted("agent send"):
        code, out, err = run(
            "flow",
            "agent",
            "send",
            project,
            "Reply in one short sentence: what can you do here? Do not generate anything.",
        )
        sent = last_json(out) if code == 0 else {}
        row(
            "agent send",
            "PASS" if code == 0 and sent.get("reply_excerpt") and sent.get("mode_restored") else "FAIL",
            f"spent={sent.get('credits_before', 0) - sent.get('credits_after', 0)} rpcids={sent.get('rpcids')} "
            f"mode_restored={sent.get('mode_restored')} reply={str(sent.get('reply_excerpt', ''))[:120]!r}"
            if sent
            else err.strip()[-160:],
        )
    if ref_image and ref_image.exists():
        if wanted("i2i"):
            image_row(
                "i2i",
                "i2i",
                "the same object, painted deep blue",
                "--ref",
                str(ref_image),
                "--model",
                "nano2",
                "--aspect",
                "16:9",
            )
        if wanted("r2v"):
            video_row(
                "r2v",
                "r2v",
                "the same object from the reference, camera orbits",
                "--ref",
                str(ref_image),
                "--model",
                "veo-lite",
                "--aspect",
                "16:9",
            )
        if wanted("i2v"):
            video_row(
                "i2v",
                "i2v",
                str(ref_image),
                "the scene comes alive, gentle motion",
                "--model",
                "veo-lite",
                "--aspect",
                "16:9",
            )
    if wanted("omni 10s 9:16"):
        video_row(
            "omni 10s 9:16",
            "t2v",
            "a red paper boat drifting on a pond",
            "--model",
            "omni-flash",
            "--duration",
            "10",
            "--aspect",
            "9:16",
        )
    if wanted("count 2"):
        video_row(
            "count 2",
            "t2v",
            "a ceramic teacup on a wooden table",
            "--model",
            "veo-lite",
            "--aspect",
            "16:9",
            "--count",
            "2",
            expect_outputs=2,
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True)
    ap.add_argument("--spend", action="store_true")
    ap.add_argument(
        "--character", action="store_true", help="Also create, list and delete a character (credit-free)."
    )
    ap.add_argument("--ref-image", default=None)
    ap.add_argument("--out", default="out")
    ap.add_argument("--rows", default="", help="Comma-separated row names to run (default: every row).")
    ap.add_argument(
        "--source-media", default=None, help="Video media id for extend/omni edit instead of a fresh t2v."
    )
    args = ap.parse_args()
    ONLY.update(name.strip() for name in args.rows.split(",") if name.strip())
    if not shutil.which("ffprobe"):
        print("ffprobe missing", file=sys.stderr)
        return 1
    out_dir = Path(args.out)
    global LOG_DIR
    LOG_DIR = out_dir / "logs"
    free_matrix(args.project, out_dir)
    if args.character and wanted("character crud"):
        character_round_trip(args.project)
    if args.spend:
        spend_matrix(
            args.project, out_dir, Path(args.ref_image) if args.ref_image else None, args.source_media
        )
    print()
    print(f"{'STATUS':7s} {'ROW':22s} DETAIL")
    for name, status, detail in ROWS:
        print(f"{status:7s} {name:22s} {detail}")
    failed = [r for r in ROWS if r[1] == "FAIL"]
    print(
        f"\nrows={len(ROWS)} pass={sum(r[1] == 'PASS' for r in ROWS)} partial={sum(r[1] == 'PARTIAL' for r in ROWS)} fail={len(failed)}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
