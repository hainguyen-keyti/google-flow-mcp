"""Plan D T1: what a scene tile shows on the grid and in the trash, and whether it carries the scene id. $0.

Creates one scene titled `pD-probe <stamp>`, opens the project grid and waits for its tile, dumps every scene tile (text,
links and attributes holding a known scene id, order), trashes the scene through the grid, then dumps the trash tiles the
same way. On both views it counts the tiles found by the substring pattern the driver uses today, by an anchored pattern
built from the measured text, and by an element whose whole text is the title. The scene is left in the trash.

    uv run python -m video.probes.scene_tiles --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from typing import Any

from video.flow import scenes
from video.probes._common import out_path
from video.session import PROJECT_READY, FlowSession

_TILES_JS = """
(ids) => [...document.querySelectorAll('flow-tile-container')]
  .filter(c => c.querySelector('flow-scene-tile'))
  .map((c, index) => {
    const found = new Set();
    const attrs = [];
    for (const el of [c, ...c.querySelectorAll('*')]) {
      for (const at of el.attributes) {
        for (const id of ids) {
          if (at.value.includes(id)) {
            found.add(id);
            attrs.push(el.tagName.toLowerCase() + '[' + at.name + ']=' + at.value.slice(0, 120));
          }
        }
      }
    }
    return {
      index,
      inner_text: c.innerText,
      text_content: c.textContent,
      normalized: (c.innerText || '').trim().replace(/\\s+/g, ' '),
      links: [...c.querySelectorAll('a[href]')].map(a => a.getAttribute('href')),
      ids: [...found],
      id_attrs: attrs.slice(0, 10),
    };
  })
"""


async def _view(session: FlowSession, url: str, title: str, ids: list[str]) -> dict[str, Any]:
    page = session.page
    await session.goto(url, ready=PROJECT_READY)
    tiles = page.locator("flow-tile-container", has=page.locator("flow-scene-tile"))
    substring = re.compile(re.escape(title))
    shown_ms = None
    for waited in range(0, 30_001, 500):
        if await tiles.filter(has_text=substring).count():
            shown_ms = waited
            break
        await page.wait_for_timeout(500)
    dump = await page.evaluate(_TILES_JS, ids)
    mine = next((tile for tile in dump if title in tile["normalized"]), None)
    counts: dict[str, Any] = {
        "substring": await tiles.filter(has_text=substring).count(),
        "exact_text_element": await tiles.filter(has=page.get_by_text(title, exact=True)).count(),
    }
    if mine:
        before, _, after = mine["normalized"].partition(title)
        anchored = re.compile("^" + re.escape(before) + re.escape(title) + re.escape(after) + "$")
        counts["anchored"] = await tiles.filter(has_text=anchored).count()
        counts["anchored_pattern"] = anchored.pattern
    return {
        "url": page.url,
        "shown_ms": shown_ms,
        "scene_tiles": len(dump),
        "mine": mine,
        "counts": counts,
        "tiles": dump,
    }


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    title = f"pD-probe {int(time.time())}"
    report: dict[str, Any] = {"project": args.project, "title": title}
    async with FlowSession(args.profile) as session:
        created = await scenes.create(session, args.project, title)
        report["created"] = created
        listed = await scenes.list_scenes(session, args.project, include_trashed=True)
        report["listing"] = listed
        ids = [scene["scene_id"] for scene in listed]
        report["grid"] = await _view(session, session.project_url(args.project), title, ids)
        try:
            report["deleted"] = await scenes.delete(session, args.project, created["scene_id"])
        except Exception as exc:  # noqa: BLE001
            report["delete_error"] = f"{type(exc).__name__}: {exc}"
        trash_url = f"{session.project_url(args.project)}/trash"
        report["trash"] = await _view(session, trash_url, title, ids)
    path = out_path("scene_tiles")
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"title={title!r} scene_id={created['scene_id']}")
    print(f"listing={[(scene['title'], scene['trashed']) for scene in listed]}")
    for view in ("grid", "trash"):
        data = report[view]
        mine = data["mine"] or {}
        print(
            f"{view}: shown_ms={data['shown_ms']} scene_tiles={data['scene_tiles']} counts={data['counts']}"
        )
        print(f"  normalized={mine.get('normalized')!r} inner_text={mine.get('inner_text')!r}")
        print(f"  links={mine.get('links')} ids={mine.get('ids')} id_attrs={mine.get('id_attrs')}")
        print(f"  order={[tile['normalized'] for tile in data['tiles']]}")
        print(f"  tiles_with_ids={sum(1 for tile in data['tiles'] if tile['ids'])}/{data['scene_tiles']}")
    print(f"deleted={report.get('deleted')} delete_error={report.get('delete_error')} report={path}")


if __name__ == "__main__":
    asyncio.run(main())
