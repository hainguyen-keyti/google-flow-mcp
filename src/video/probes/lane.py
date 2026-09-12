"""Which lane is the profile on: LABS, MIGRATED, or SIGNED_OUT. $0: two navigations, two DOM reads.

uv run python -m video.probes.lane --profile default
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from video.flow.lane import LABS_ROOT, MIGRATED_ROOT, PROBE_JS, verdict
from video.probes._common import build_client, out_path, resolve_profile_dir, step


async def probe(profile: str, settle: float = 8.0) -> dict[str, Any]:
    results: dict[str, Any] = {"profile": profile, "roots": {}}
    async with build_client(resolve_profile_dir(profile)) as client:
        page = await client._context.new_page()
        for lane, url in (("labs", LABS_ROOT), ("migrated", MIGRATED_ROOT)):
            step(lane, f"probing {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(int(settle * 1000))
                results["roots"][lane] = await page.evaluate(PROBE_JS)
            except Exception as exc:  # noqa: BLE001
                results["roots"][lane] = {"nav_error": str(exc)[:300]}
    results["verdict"] = verdict(results["roots"].get("labs", {}), results["roots"].get("migrated", {}))
    results["projects"] = int(results["roots"].get("migrated", {}).get("project_links") or 0)
    return results


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--settle", type=float, default=8.0)
    args = ap.parse_args()
    results = await probe(args.profile, args.settle)
    out = out_path("lane")
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"VERDICT {results['verdict']} projects={results['projects']} ({out})")


if __name__ == "__main__":
    asyncio.run(main())
