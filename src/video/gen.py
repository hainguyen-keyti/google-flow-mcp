"""Spend-side wrappers around gflow with an append-only ledger.

I1: the "submitted" row is written before gflow is spawned and a job id that already has one is
refused. I5: credits are read before and after every job. I2: every string in a row is scrubbed on its own before
the row touches disk, and a job id the scrub would change is refused, since it could never be found again.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePath
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


def _scrubbed(value: Any) -> Any:
    """Scrub each string a row carries on its own, so the scrub can never eat the JSON around it.

    Values json cannot encode on its own (paths, bytes, sets) become their text here, keys included: the encoder's
    fallback runs after this walk, and whatever it returns goes to the file as it is.
    """
    if isinstance(value, str):
        return _scrub(value)
    if isinstance(value, PurePath | bytes | bytearray):
        return _scrub(str(value))
    if isinstance(value, dict):
        return {_scrubbed(key): _scrubbed(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_scrubbed(item) for item in value]
    if isinstance(value, set | frozenset):
        return [_scrubbed(item) for item in sorted(value, key=repr)]
    return value


def _scrub_other(value: Any) -> str:
    return _scrub(str(value))


def check_job_id(job_id: str) -> None:
    """Refuse a job id the scrub would rewrite: stored as another id, no later check could ever find it."""
    if _scrub(job_id) != job_id:
        raise ValueError(
            "job_id must not hold session-like text (a cookie name or an Authorization header), since the ledger "
            f"would store it as {_scrub(job_id)!r} and never find it again"
        )


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


_REPO = Path(__file__).resolve().parents[2]


def _git(args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=10, cwd=_REPO, check=False
        )
    except Exception:  # noqa: BLE001
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


@functools.lru_cache(maxsize=1)
def code_version() -> str:
    """Which code wrote a row, so a bill is never read as proof of code that did not run it.

    Paid for on 2026-09-28: a `clip_edit` of 20 credits was taken as the acceptance of a fix, while the MCP server
    process had started before the commit and Python keeps the modules it loaded, so the tool answered with the old
    code. `-dirty` when `src/` holds uncommitted work, since one sha covered three different states of this session.
    """
    sha = _git(["rev-parse", "--short", "HEAD"])
    if not sha:
        return "unknown"
    return f"{sha}-dirty" if _git(["status", "--porcelain", "--", "src"]) else sha


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, job_id: str, status: str, **fields: Any) -> None:
        check_job_id(job_id)
        row = {"ts": time.time(), "job_id": job_id, "status": status, **fields, "code": code_version()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_scrubbed(row), ensure_ascii=False, default=_scrub_other) + "\n")

    def rows(self, job_id: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        # Split on the newline alone: `splitlines()` also breaks on U+2028, U+2029 and U+0085, which json keeps inside
        # strings, so one prompt holding one of them made every later read of this ledger fail (review of plan D).
        rows = [
            json.loads(line) for line in self.path.read_text(encoding="utf-8").split("\n") if line.strip()
        ]
        return [r for r in rows if job_id is None or r.get("job_id") == job_id]

    def has_submitted(self, job_id: str) -> bool:
        return any(r.get("status") in ("submitted", "done", "failed") for r in self.rows(job_id))


async def run_gflow(argv: list[str], profile: str = "default") -> tuple[int, str, str]:
    # gflow's generation commands take no --profile flag and, with two profiles on the machine, refuse to guess
    # (measured 2026-09-28), so the account that spends is named here, the same one the balance is read on.
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "video.gflow_cli",
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GFLOW_CLI_PROFILE": profile},
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
    profile: str = "default",
) -> dict[str, Any]:
    ledger = ledger or Ledger(out_dir / "ledger.jsonl")
    # One profile for the spend and for the balance on both sides of it, or the ledger brackets the wrong account.
    runner = runner or functools.partial(run_gflow, profile=profile)
    read_credits = read_credits or functools.partial(read_credits_live, profile)
    if ledger.has_submitted(job.job_id):
        raise AlreadySubmitted(f"job {job.job_id} already has a submitted row; use a new job id")
    argv = build_argv(job, out_dir)
    before = await read_credits()
    ledger.append(job.job_id, "submitted", kind=job.kind, argv=argv, credits_before=before)
    code, stdout, stderr = await runner(argv)
    if code != 0:
        after = await read_credits()
        problem = gflow_problem(stderr)
        # Some refusals are printed on stdout with nothing on stderr (measured 2026-09-28: "Cannot pick a default
        # profile"), and an empty reason is how a paid failure gets retried blind.
        if "error_class" not in problem and stdout.strip():
            problem["stdout_tail"] = stdout.strip()[-300:]
        ledger.append(
            job.job_id,
            "failed",
            exit_code=code,
            credits_before=before,
            credits_after=after,
            **problem,
        )
        summary = (
            problem.get("detail") or problem.get("title") or stderr.strip()[-300:] or stdout.strip()[-300:]
        )
        # gflow gives WafRejectionError exit 10 (gflow_cli/errors.py) and wrongly advises re-authenticating.
        if code == 10 or problem.get("error_class") == "WafRejectionError":
            raise RuntimeError(
                f"gflow {job.kind} exit {code} (WafRejectionError): Google Flow flagged this request as "
                "unusual activity. Stop: do not retry, and do not re-run gflow auth login on this account, "
                "whatever gflow suggests. Tell the owner; the browser profile must cool down before reuse."
            )
        # gflow 0.78.0 added FlowAccessUnavailableError, exit 39: Flow itself shows "you don't have access".
        # Through 0.73.1 that arrived as exit 23 UiSelectorDriftError, which reads like a passing UI bug and
        # invites the retry that pays twice (their own A/B, 2026-09-15).
        if code == 39 or problem.get("error_class") == "FlowAccessUnavailableError":
            raise RuntimeError(
                f"gflow {job.kind} exit {code} (FlowAccessUnavailableError): Flow showed its own no-access "
                "screen for this Google account. Nothing expired, so do not retry and do not re-run gflow auth "
                "login: tell the owner, who has to check the account's Flow subscription, age verification and "
                f"region. ({summary})"
            )
        raise RuntimeError(f"gflow {job.kind} exit {code} ({problem.get('error_class', '?')}): {summary}")
    outputs = parse_result(job.kind, stdout)
    after = await read_credits()
    ledger.append(
        job.job_id, "done", outputs=outputs, credits_before=before, credits_after=after, spent=before - after
    )
    return {"job_id": job.job_id, "outputs": outputs, "credits_before": before, "credits_after": after}
