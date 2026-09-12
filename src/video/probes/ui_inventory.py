"""Screenshot and DOM inventory of the migrated Flow UI. $0: no clicks, no typing.

uv run python -m video.probes.ui_inventory --profile default --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json

from video.probes._common import OUT_DIR, build_client, out_path, resolve_profile_dir, step

_JS = """
() => {
  const txt = (e) => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g,' ').slice(0,70);
  const uniq = (arr) => [...new Set(arr.filter(Boolean))];
  return {
    url: location.href,
    title: document.title,
    buttons: uniq([...document.querySelectorAll('button')].map(txt)).slice(0,150),
    links: uniq([...document.querySelectorAll('a')].map(a => txt(a) + ' -> ' + (a.getAttribute('href')||''))).slice(0,60),
    icons: uniq([...document.querySelectorAll('mat-icon')].map(e => (e.getAttribute('data-mat-icon-name') || e.getAttribute('fonticon') || e.innerText || '').trim())).slice(0,150),
    placeholders: uniq([...document.querySelectorAll('textarea,input')].map(e => e.getAttribute('placeholder')||e.getAttribute('aria-label')||'')).slice(0,30),
    custom_elements: uniq([...document.querySelectorAll('*')].map(e => e.tagName.toLowerCase()).filter(t => t.includes('-'))).slice(0,120),
    roles: uniq([...document.querySelectorAll('[role=menuitem],[role=tab],[role=option],[role=radio]')].map(txt)).slice(0,80),
    credit_banner: [...document.querySelectorAll('flow-credit-banner')].map(e => (e.innerText||'').trim()),
    tier_chip: [...document.querySelectorAll('flow-user-tier-chip')].map(e => (e.innerText||'').trim()),
    settings_chip: [...document.querySelectorAll('.settings-trigger-button')].map(e => (e.innerText||'').trim()),
  };
}
"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    OUT_DIR.mkdir(exist_ok=True)
    pages = [
        ("grid", "https://flow.google.com/"),
        ("project", f"https://flow.google.com/project/{args.project}"),
    ]
    result = {}
    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = await client._context.new_page()
        await page.set_viewport_size({"width": 1600, "height": 1000})
        for name, url in pages:
            step(name, url)
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(12_000)
            await page.screenshot(path=str(OUT_DIR / f"ui_{name}.png"))
            result[name] = await page.evaluate(_JS)
    out = out_path("ui_inventory")
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    for name, r in result.items():
        print(
            f"{name}: {r['url']}  buttons={len(r['buttons'])} links={len(r['links'])} "
            f"icons={len(r['icons'])} elems={len(r['custom_elements'])} tier={r['tier_chip']}"
        )
    print(f"({out})")


if __name__ == "__main__":
    asyncio.run(main())
