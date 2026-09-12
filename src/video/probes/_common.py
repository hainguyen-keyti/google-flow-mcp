"""Shared bootstrap for the $0 read-only probes: profile resolution, client context, output paths."""

from __future__ import annotations

import sys
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from gflow_cli import auth as _auth
from gflow_cli.api.client import FlowApiClient

import video  # noqa: F401

OUT_DIR = Path("out")


def out_path(stem: str, suffix: str = ".json") -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR / f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}{suffix}"


def resolve_profile_dir(profile: str) -> Path:
    profile_dir = _auth.profile_dir(profile)
    if not profile_dir.exists():
        print(f"[probe] no profile dir for '{profile}': {profile_dir}", file=sys.stderr, flush=True)
        sys.exit(2)
    return profile_dir


@asynccontextmanager
async def build_client(profile_dir: Path, *, headless: bool = False) -> AsyncGenerator[FlowApiClient, None]:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        yield client


def step(tag: str, msg: str) -> None:
    print(f"[probe] {tag}  {msg}", file=sys.stderr, flush=True)
