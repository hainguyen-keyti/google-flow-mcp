"""Find which rpcid carries a project's character list by loading /project/<id>/character on every
project until one returns a non-empty candidate. $0: navigation only.

uv run python -m video.probes.find_characters --profile default [--max 16]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from video.flow import reader
from video.probes.fixtures import FIXTURE_DIR, redact
from video.session import FlowSession

CANDIDATES = ("WuwhI", "LPzVkd", "qJcgMc", "DTaVef", "mrlkwd")
KNOWN = {
    "DTaVef",
    "HTrJv",
    "KV2T2d",
    "LPzVkd",
    "NfrxTb",
    "Yizz8d",
    "Zzl0ze",
    "cPZSdc",
    "ngNC2",
    "nzlxg",
    "o30O0e",
    "qJcgMc",
    "tRARke",
    "ve2Lsc",
    "yBhWQ",
    "mrlkwd",
    "xI9TVb",
    "UpteDb",
    "WuwhI",
}

_DOM_JS = """
() => ({
  url: location.href,
  elements: [...new Set([...document.querySelectorAll('flow-project-shell *')]
    .map(e => e.tagName.toLowerCase()).filter(t => t.includes('-')))].slice(0, 60),
  text: (document.querySelector('flow-project-shell')?.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 400),
})
"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--max", type=int, default=16)
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        projects = await reader.projects(session)
        print(f"projects={len(projects)}")
        for project in projects[: args.max]:
            url = f"{session.project_url(project['id'])}/character"
            frames = await reader.capture(session, lambda u=url: session.goto(u), settle=8.0)
            sizes = {
                r: len(frames[r][0]) if isinstance(frames[r][0], list) else -1
                for r in CANDIDATES
                if r in frames
            }
            new = sorted(set(frames) - KNOWN)
            print(f"{project['id']}  {project['title'] or '':16s} candidates={sizes} new={new}")
            hit = next((r for r in CANDIDATES if r in frames and sizes.get(r, 0) > 0), None)
            hit = hit or next((r for r in new if isinstance(frames[r][0], list) and frames[r][0]), None)
            if hit:
                dom = await session.page.evaluate(_DOM_JS)
                FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
                path = FIXTURE_DIR / f"{hit}_nonempty.json"
                path.write_text(
                    json.dumps(
                        redact({"view": "character", "project": project["id"], "payload": frames[hit][0]}),
                        indent=1,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                Path("out").mkdir(exist_ok=True)
                Path("out/character_view_dom.json").write_text(
                    json.dumps(dom, indent=1, ensure_ascii=False), encoding="utf-8"
                )
                print(f"HIT rpcid={hit} project={project['id']} -> {path}")
                print("payload head:", json.dumps(redact(frames[hit][0]))[:500])
                print("dom:", json.dumps(dom, ensure_ascii=False)[:500])
                return
        print("NO HIT: no project returned a non-empty candidate list")


if __name__ == "__main__":
    asyncio.run(main())
