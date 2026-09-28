"""Which lane the profile is on: LABS, MIGRATED, or SIGNED_OUT. $0: two navigations, two DOM reads."""

from __future__ import annotations

from typing import Any

LABS_ROOT = "https://labs.google/fx/tools/flow"
MIGRATED_ROOT = "https://flow.google.com/"

PROBE_JS = """
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
    new_project_button: count('button.new-project-button'),
  };
}
"""


def verdict(labs: dict[str, Any], migrated: dict[str, Any]) -> str:
    def grid(r: dict[str, Any]) -> bool:
        # A signed-in account with no projects shows only the New project button (measured 2026-09-28).
        return int(r.get("project_links") or 0) > 0 or int(r.get("new_project_button") or 0) > 0

    if not grid(labs) and not grid(migrated):
        return "SIGNED_OUT"
    if grid(migrated):
        return "MIGRATED"
    if grid(labs):
        return "LABS"
    return "INDETERMINATE"


async def run(session: Any, settle: float = 8.0) -> dict[str, Any]:
    results: dict[str, Any] = {"roots": {}}
    for lane, url in (("labs", LABS_ROOT), ("migrated", MIGRATED_ROOT)):
        try:
            await session.goto(url)
            await session.page.wait_for_timeout(int(settle * 1000))
            results["roots"][lane] = await session.page.evaluate(PROBE_JS)
        except Exception as exc:  # noqa: BLE001
            results["roots"][lane] = {"nav_error": str(exc)[:300]}
    results["verdict"] = verdict(results["roots"].get("labs", {}), results["roots"].get("migrated", {}))
    results["projects"] = int(results["roots"].get("migrated", {}).get("project_links") or 0)
    return results
