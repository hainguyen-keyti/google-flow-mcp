"""Spend-side wrappers around gflow with an append-only ledger.

I1: the "submitted" row is written before gflow is spawned and a job id that already has one is
refused. I5: credits are read before and after every job. I2: rows are scrubbed before they touch disk.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VIDEO_KINDS = ("t2v", "i2v", "r2v")
IMAGE_KINDS = ("t2i", "i2i")
KINDS = VIDEO_KINDS + IMAGE_KINDS
_SCRUB = re.compile(r"(SAPISID=\S*|__Secure-\S*|Authorization:\s*\S+(\s+\S+)?)")

Runner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]
CreditsReader = Callable[[], Awaitable[int]]


class AlreadySubmitted(RuntimeError):
    pass


@dataclass
class Job:
    job_id: str
    kind: str
    prompt: str
    project: str
    model: str | None = None
    aspect: str | None = None
    count: int = 1
    duration: int | None = None
    initial_frame: Path | None = None
    end_frame: Path | None = None
    refs: list[Path] = field(default_factory=list)


def build_argv(job: Job, out_dir: Path) -> list[str]:
    if job.kind not in KINDS:
        raise ValueError(f"unknown kind {job.kind!r}; expected one of {KINDS}")
    if not job.project:
        raise ValueError("project is required: on this account gflow cannot create projects (labs lane)")
    if job.kind == "i2v" and job.initial_frame is None:
        raise ValueError("i2v needs initial_frame")
    if job.kind in ("r2v", "i2i") and not job.refs:
        raise ValueError(f"{job.kind} needs at least one ref image")
    argv = ["video" if job.kind in VIDEO_KINDS else "image", job.kind]
    if job.initial_frame is not None:
        argv += ["--initial-frame", str(job.initial_frame)]
    if job.end_frame is not None:
        argv += ["--end-frame", str(job.end_frame)]
    for ref in job.refs:
        argv += ["--ref", str(ref)]
    argv += [job.prompt, "--project", job.project, "--json"]
    if job.model:
        argv += ["--model", job.model]
    if job.aspect:
        argv += ["--aspect", job.aspect]
    if job.kind in VIDEO_KINDS:
        argv += ["--out-dir", str(out_dir)]
        if job.count > 1:
            argv += ["--count", str(job.count)]
        if job.duration:
            argv += ["--duration", str(job.duration)]
    else:
        argv += ["--out", str(out_dir), "-n", str(job.count)]
    return argv


def parse_result(kind: str, stdout: str) -> list[dict[str, Any]]:
    data = json.loads(stdout)
    if data.get("status") != "ok":
        detail = data.get("error") or data.get("error_message") or data.get("failure_reasons")
        raise RuntimeError(f"gflow {kind} reported failure: {detail}")
    if kind in IMAGE_KINDS:
        return [
            {
                "media_id": image["media_name"],
                "path": image["local_path"],
                "width": image["dimensions"]["width"],
                "height": image["dimensions"]["height"],
            }
            for image in data["images"]
        ]
    return [{"media_id": data["media_id"], "path": data["local_path"]}]


def _scrub(text: str) -> str:
    return _SCRUB.sub("[redacted]", text)


def gflow_problem(stderr: str) -> dict[str, Any]:
    """The last structured error gflow printed: error_class, title, detail, route, incident id."""
    for line in reversed(stderr.splitlines()):
        start = line.find("{")
        if start < 0 or '"error_class"' not in line:
            continue
        try:
            data = json.loads(line[start:])
        except ValueError:
            continue
        problem = data.get("problem") or {}
        incident = data.get("incident") or {}
        return {
            "error_class": data.get("error_class"),
            "title": problem.get("title"),
            "detail": problem.get("detail"),
            "route": problem.get("route"),
            "incident": incident.get("id") if isinstance(incident, dict) else None,
        }
    return {"stderr_tail": stderr[-300:]}


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, job_id: str, status: str, **fields: Any) -> None:
        row = {"ts": time.time(), "job_id": job_id, "status": status, **fields}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_scrub(json.dumps(row, ensure_ascii=False, default=str)) + "\n")

    def rows(self, job_id: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = [
            json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        return [r for r in rows if job_id is None or r.get("job_id") == job_id]

    def has_submitted(self, job_id: str) -> bool:
        return any(r.get("status") in ("submitted", "done", "failed") for r in self.rows(job_id))


async def run_gflow(argv: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "video.gflow_cli",
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def read_credits_live(profile: str = "default") -> int:
    from video.flow import reader
    from video.session import FlowSession

    async with FlowSession(profile) as session:
        return (await reader.credits(session))["balance"]


async def run_job(
    job: Job,
    out_dir: Path,
    *,
    ledger: Ledger | None = None,
    runner: Runner | None = None,
    read_credits: CreditsReader | None = None,
) -> dict[str, Any]:
    ledger = ledger or Ledger(out_dir / "ledger.jsonl")
    runner = runner or run_gflow
    read_credits = read_credits or read_credits_live
    if ledger.has_submitted(job.job_id):
        raise AlreadySubmitted(f"job {job.job_id} already has a submitted row; use a new job id")
    argv = build_argv(job, out_dir)
    before = await read_credits()
    ledger.append(job.job_id, "submitted", kind=job.kind, argv=argv, credits_before=before)
    code, stdout, stderr = await runner(argv)
    if code != 0:
        after = await read_credits()
        problem = gflow_problem(stderr)
        ledger.append(
            job.job_id,
            "failed",
            exit_code=code,
            credits_before=before,
            credits_after=after,
            **problem,
        )
        summary = problem.get("detail") or problem.get("title") or stderr.strip()[-300:]
        raise RuntimeError(f"gflow {job.kind} exit {code} ({problem.get('error_class', '?')}): {summary}")
    outputs = parse_result(job.kind, stdout)
    after = await read_credits()
    ledger.append(
        job.job_id, "done", outputs=outputs, credits_before=before, credits_after=after, spent=before - after
    )
    return {"job_id": job.job_id, "outputs": outputs, "credits_before": before, "credits_after": after}
