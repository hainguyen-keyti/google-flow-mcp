"""Spike for T6: which clicks and rpcids create, rename and delete a project on the migrated grid.
Creates ONE project, renames it, deletes it. Free (no generation). Screenshots and rpcids per step.

uv run python -m video.probes.project_crud --profile default --title "probe delete me"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from video.flow import parsers
from video.flow.reader import capture, one
from video.session import GRID_READY, MIGRATED_ROOT, FlowSession

_PROJECT_RE = re.compile(r"/project/([A-Za-z0-9_-]+)")
_VISIBLE_BUTTONS_JS = """
() => [...document.querySelectorAll('button, [role=menuitem]')]
  .filter(e => e.offsetParent !== null)
  .map(e => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60))
  .filter(Boolean)
"""


async def shot(session: FlowSession, name: str) -> None:
    Path("out").mkdir(exist_ok=True)
    await session.page.screenshot(path=f"out/t6_{name}.png")


def rpc_summary(frames: dict[str, list[Any]]) -> str:
    return " ".join(f"{k}x{len(v)}" for k, v in sorted(frames.items()))


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--title", default="probe delete me")
    ap.add_argument("--skip-delete", action="store_true")
    args = ap.parse_args()
    report: dict[str, Any] = {}
    async with FlowSession(args.profile) as session:
        page = session.page
        before = parsers.projects(
            one(
                await capture(session, lambda: session.goto(MIGRATED_ROOT, ready=GRID_READY), settle=6.0),
                "UpteDb",
            )
        )
        report["count_before"] = len(before)

        print("--- create: click 'New project'")
        frames = await capture(
            session,
            lambda: page.get_by_role("button", name=re.compile("New project", re.IGNORECASE)).first.click(
                timeout=10_000
            ),
            settle=8.0,
        )
        await shot(session, "after_create")
        url = page.url
        match = _PROJECT_RE.search(url)
        new_id = match.group(1) if match else None
        report["create"] = {"rpcids": rpc_summary(frames), "url": url, "new_id": new_id}
        print(json.dumps(report["create"]))
        if not new_id:
            print("no project id in url; visible buttons:", await page.evaluate(_VISIBLE_BUTTONS_JS))
            return

        print("--- rename: click the editable title and type")
        title_box = page.locator("flow-editable-text").first
        try:
            frames = await capture(session, lambda: _rename(page, title_box, args.title), settle=6.0)
            report["rename"] = {
                "rpcids": rpc_summary(frames),
                "title_text": (await title_box.inner_text()).strip(),
            }
        except Exception as exc:  # noqa: BLE001
            report["rename"] = {"error": str(exc)[:200], "buttons": await page.evaluate(_VISIBLE_BUTTONS_JS)}
        await shot(session, "after_rename")
        print(json.dumps(report["rename"])[:600])

        print("--- verify on grid")
        grid = parsers.projects(
            one(
                await capture(session, lambda: session.goto(MIGRATED_ROOT, ready=GRID_READY), settle=6.0),
                "UpteDb",
            )
        )
        mine = next((p for p in grid if p["id"] == new_id), None)
        report["grid_after_create"] = {"count": len(grid), "found": mine}
        print(json.dumps(report["grid_after_create"]))

        if args.skip_delete:
            print("skip-delete set; leaving project", new_id)
            return
        print("--- delete: open the card menu and click 'Delete project'")
        card = page.locator(f"a[href*='/project/{new_id}']").first
        try:
            frames = await capture(session, lambda: _delete(page, card), settle=8.0)
            report["delete"] = {"rpcids": rpc_summary(frames)}
        except Exception as exc:  # noqa: BLE001
            report["delete"] = {"error": str(exc)[:200], "buttons": await page.evaluate(_VISIBLE_BUTTONS_JS)}
        await shot(session, "after_delete")
        print(json.dumps(report["delete"])[:600])
        grid = parsers.projects(
            one(
                await capture(session, lambda: session.goto(MIGRATED_ROOT, ready=GRID_READY), settle=6.0),
                "UpteDb",
            )
        )
        report["grid_after_delete"] = {
            "count": len(grid),
            "still_there": any(p["id"] == new_id for p in grid),
        }
        print(json.dumps(report["grid_after_delete"]))
    Path("out/t6_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")


async def _rename(page: Any, title_box: Any, title: str) -> None:
    await title_box.click(timeout=8_000)
    await page.keyboard.press("Meta+A")
    await page.keyboard.type(title)
    await page.keyboard.press("Enter")


async def _delete(page: Any, card: Any) -> None:
    await card.hover(timeout=8_000)
    await page.wait_for_timeout(500)
    kebab = card.locator("button").filter(has_text=re.compile("more_vert|More options", re.IGNORECASE)).first
    if await kebab.count() == 0:
        kebab = page.locator("flow-project-card", has=card).get_by_role("button").first
    await kebab.click(timeout=8_000)
    await page.wait_for_timeout(800)
    await page.get_by_role("menuitem", name=re.compile("Delete", re.IGNORECASE)).first.click(timeout=8_000)
    await page.wait_for_timeout(1_000)
    confirm = page.get_by_role("button", name=re.compile("^(Delete|Confirm|OK)$", re.IGNORECASE)).first
    if await confirm.count():
        await confirm.click(timeout=8_000)


if __name__ == "__main__":
    asyncio.run(main())
