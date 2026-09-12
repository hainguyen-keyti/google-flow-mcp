"""Which lane is the profile on: LABS, MIGRATED, or SIGNED_OUT. $0: two navigations, two DOM reads.

uv run python -m video.probes.lane --profile default
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from video.probes._common import build_client, out_path, resolve_profile_dir, step

LABS_ROOT = "https://labs.google/fx/tools/flow"
MIGRATED_ROOT = "https://flow.google.com/"

_PROBE_JS = """
() => {
  const count = (sel) => document.querySelectorAll(sel).length;
  return {
    url: location.href,
    title: document.title,
    carriers: { google_symbols: count('i.google-symbols'), mat_icon: count('mat-icon') },
    shells: {
      aisandbox_root: count('aisandbox-root'),
      next_route_announcer: count('next-route-announcer'),
      flow_landing_page: count('flow-landing-page'),
      router_outlet: count('router-outlet'),
    },
    project_links: count('a[href*="/project/"]'),
  };
}
"""


def verdict(labs: dict[str, Any], migrated: dict[str, Any]) -> str:
    def grid(r: dict[str, Any]) -> bool:
        return int(r.get("project_links") or 0) > 0

    if not grid(labs) and not grid(migrated):
        return "SIGNED_OUT"
    if grid(migrated):
        return "MIGRATED"
    if grid(labs):
        return "LABS"
    return "INDETERMINATE"


async def probe(profile: str, settle: float = 8.0) -> dict[str, Any]:
    results: dict[str, Any] = {"profile": profile, "roots": {}}
    async with build_client(resolve_profile_dir(profile)) as client:
        page = await client._context.new_page()
        for lane, url in (("labs", LABS_ROOT), ("migrated", MIGRATED_ROOT)):
            step(lane, f"probing {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(int(settle * 1000))
                results["roots"][lane] = await page.evaluate(_PROBE_JS)
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
