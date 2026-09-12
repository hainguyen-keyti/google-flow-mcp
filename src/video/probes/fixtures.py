"""Capture full batchexecute payloads as redacted test fixtures, and read the credit figure
from the composer settings overlay. $0: navigation, one sidebar click, one chip click.

uv run python -m video.probes.fixtures --profile default --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
from pathlib import Path
from typing import Any

from video.probes._common import build_client, resolve_profile_dir, step
from video.probes.rpc_map import decode

WANTED = ("UpteDb", "ngNC2", "Zzl0ze", "tRARke", "WuwhI", "nzlxg", "Yizz8d", "yBhWQ", "HTrJv")
FIXTURE_DIR = Path("tests/fixtures/rpc")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def redact(obj: Any) -> Any:
    if isinstance(obj, str):
        if obj.startswith(("https://lh3.googleusercontent.com/", "https://flow-content.google/")):
            return "https://" + obj.split("/")[2] + "/REDACTED"
        return _EMAIL.sub("user@example.com", obj)
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    return obj


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    captured: dict[str, dict[str, Any]] = {}
    pending: list[str] = []

    async def on_response(resp) -> None:
        if "batchexecute" in resp.request.url:
            with contextlib.suppress(Exception):
                pending.append(await resp.text())

    def flush(view: str) -> None:
        for body in pending:
            for rpcid, payload in decode(body):
                captured.setdefault(rpcid, {"view": view, "payload": payload})
        pending.clear()

    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = await client._context.new_page()
        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        step("view", "grid")
        await page.goto("https://flow.google.com/", wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(10_000)
        flush("grid")
        step("view", "project")
        await page.goto(
            f"https://flow.google.com/project/{args.project}", wait_until="domcontentloaded", timeout=60_000
        )
        await page.wait_for_timeout(12_000)
        flush("project")
        step("click", "Characters")
        try:
            await page.locator("mat-list-item", has_text="Characters").first.click(timeout=8_000)
            await page.wait_for_timeout(6_000)
        except Exception as exc:  # noqa: BLE001
            step("click", f"Characters failed: {str(exc)[:80]}")
        flush("characters")
        step("click", "settings chip")
        overlay = ""
        try:
            await page.locator(".settings-trigger-button").first.click(timeout=8_000)
            await page.wait_for_timeout(2_500)
            overlay = await page.locator(".cdk-overlay-pane").first.inner_text(timeout=5_000)
            await page.keyboard.press("Escape")
        except Exception as exc:  # noqa: BLE001
            overlay = f"(settings overlay failed: {str(exc)[:120]})"
        flush("settings")

    Path("out").mkdir(exist_ok=True)
    Path("out/settings_overlay.txt").write_text(overlay, encoding="utf-8")
    for rpcid in WANTED:
        if rpcid not in captured:
            print(f"{rpcid:8s} MISSING")
            continue
        entry = captured[rpcid]
        path = FIXTURE_DIR / f"{rpcid}.json"
        path.write_text(json.dumps(redact(entry), indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{rpcid:8s} view={entry['view']:10s} bytes={path.stat().st_size:7d} -> {path}")
    print("--- settings overlay lines mentioning credit ---")
    for line in overlay.splitlines():
        if "credit" in line.lower():
            print("  " + line.strip())
    print("--- nzlxg head ---")
    print(json.dumps(captured.get("nzlxg", {}).get("payload"))[:200])


if __name__ == "__main__":
    asyncio.run(main())
