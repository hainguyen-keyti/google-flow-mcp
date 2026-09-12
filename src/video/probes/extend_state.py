"""After a clip-editor Extend: where did the output go? $0. Prints scenes (with trash flag), media ids that
mention the source clip, then opens the newest active scene and dumps its rpcids, timeline text and any
payload path holding the source media id.

uv run python -m video.probes.extend_state --project <id> --source <media_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from video.flow import parsers
from video.flow.reader import capture, one
from video.probes.scene_state import paths_to
from video.session import PROJECT_READY, FlowSession


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--since", type=int, default=0, help="Epoch seconds: print records created at or after.")
    args = ap.parse_args()
    async with FlowSession(args.profile) as session:
        page = session.page
        frames = await capture(
            session, lambda: session.goto(session.project_url(args.project), ready=PROJECT_READY), settle=10.0
        )
        listing = one(frames, "Zzl0ze")
        scenes = parsers.scenes_from_listing(listing)
        media = parsers.media(listing)
        print("scenes:", json.dumps(scenes)[:1500])
        print(
            "media count:",
            len(media),
            "descriptors:",
            len(listing[1] or []),
            "records:",
            len(listing[2] or []),
        )
        print("newest media:", json.dumps(media[:3])[:1200])
        print("paths holding the source id:", paths_to(listing, args.source)[:12])
        for path in paths_to(listing, args.source)[:12]:
            if path[0] != 2:
                continue
            record = listing[2][path[1]]
            print(f"record at {path[:2]} len={len(record)} raw={json.dumps(record)[:700]}")
        described = {d[0] for d in listing[1] or [] if isinstance(d, list)}
        orphans = [
            r for r in listing[2] or [] if isinstance(r, list) and len(r) > 2 and r[2] not in described
        ]
        print("records without a descriptor:", len(orphans))
        for record in orphans:
            print("  orphan:", json.dumps(record)[:1000])
        active = [s for s in scenes if not s["trashed"]]
        if not active:
            print("no active scene")
            return
        scene = active[-1]
        print("opening scene:", scene)
        scene_frames = await capture(
            session,
            lambda: session.goto(
                f"{session.project_url(args.project)}/scene/{scene['scene_id']}", ready="flow-scene-builder"
            ),
            settle=12.0,
        )
        print("scene rpcids:", {k: len(v) for k, v in sorted(scene_frames.items())})
        recent = []
        for payload in scene_frames.get("as29s", []):
            for entry in payload if isinstance(payload, list) else []:
                try:
                    created = entry[5][0][0]
                except (TypeError, IndexError, KeyError):
                    continue
                if isinstance(created, int) and created >= args.since:
                    recent.append(entry)
        print(f"as29s entries created since {args.since}: {len(recent)}")
        for entry in recent[:6]:
            print("  recent:", json.dumps(entry)[:1400])
        for rpcid in (
            "DTaVef",
            "HTrJv",
            "KV2T2d",
            "LPzVkd",
            "NfrxTb",
            "Yizz8d",
            "cPZSdc",
            "o30O0e",
            "qJcgMc",
            "ve2Lsc",
        ):
            for payload in scene_frames.get(rpcid, []):
                blob = json.dumps(payload)
                if scene["scene_id"] in blob or "flow-content.google/video" in blob:
                    print(f"rpc {rpcid} (scene-related) len={len(blob)}: {blob[:1200]}")
        for rpcid, payloads in scene_frames.items():
            hits = paths_to(payloads, args.source)
            if hits:
                print(f"rpc {rpcid} holds the source id at {hits[:6]}")
                print("  payload:", json.dumps(payloads)[:1500])
        text: Any = await page.evaluate(
            "() => [...document.querySelectorAll('flow-scene-timeline')].map(t => (t.innerText||'').trim().replace(/\\s+/g,' ').slice(0,600))"
        )
        print("timeline text:", text)
        clips = await page.evaluate(
            "() => [...document.querySelectorAll('flow-scene-timeline [class*=clip], flow-scene-timeline flow-thumbnail-strip')]"
            ".map(e => ({tag: e.tagName, cls: e.className.toString().slice(0,80), text: (e.innerText||'').trim().slice(0,80),"
            " attrs: [...e.attributes].filter(a => a.name.startsWith('data-') || a.name.startsWith('aria-')).map(a => a.name+'='+a.value.slice(0,60))}))"
        )
        print("timeline clips:", json.dumps(clips, ensure_ascii=False)[:2500])
        buttons = await page.evaluate(
            "() => [...document.querySelectorAll('button')].filter(e => e.offsetParent !== null)"
            ".map(e => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g,' ').slice(0,60)).filter(Boolean)"
        )
        print("buttons:", json.dumps(buttons, ensure_ascii=False)[:1500])
        await page.screenshot(path="out/t9_extend_scene.png")


if __name__ == "__main__":
    asyncio.run(main())
