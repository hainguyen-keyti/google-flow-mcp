"""Create, rename and delete projects on the migrated grid (measured 2026-09-12: create fires jHPbke,
rename fires o8DA4; delete goes through the card's own 'Delete project' button and a confirm dialog)."""

from __future__ import annotations

import re
from typing import Any

from video.flow import parsers
from video.flow.reader import capture, one
from video.session import GRID_READY, MIGRATED_ROOT, PROJECT_READY, FlowSession

_PROJECT_RE = re.compile(r"/project/([A-Za-z0-9_-]+)")


def project_id_from_url(url: str) -> str:
    match = _PROJECT_RE.search(url)
    if not match:
        raise ValueError(f"not a project url: {url}")
    return match.group(1)


def may_delete(project_id: str, *, created_ids: set[str], explicit: bool) -> bool:
    return explicit or project_id in created_ids


async def list_ids(session: FlowSession) -> list[str]:
    frames = await capture(session, lambda: session.goto(MIGRATED_ROOT, ready=GRID_READY), settle=6.0)
    return [p["id"] for p in parsers.projects(one(frames, "UpteDb"))]


async def create(session: FlowSession, title: str | None = None) -> dict[str, Any]:
    page = session.page
    await session.goto(MIGRATED_ROOT, ready=GRID_READY)
    button = page.get_by_role("button", name=re.compile("New project", re.IGNORECASE)).first
    frames = await capture(session, lambda: button.click(timeout=10_000), settle=8.0)
    project_id = project_id_from_url(page.url)
    result = {"id": project_id, "rpcids": sorted(frames), "title": None}
    if title:
        try:
            result["title"] = await rename(session, project_id, title, navigate=False)
        except (LookupError, RuntimeError) as exc:
            # The project exists by now: an error without its id sends a retry off to create a second one.
            raise RuntimeError(f"project {project_id} was created, but naming it failed: {exc}") from exc
    return result


async def rename(session: FlowSession, project_id: str, title: str, *, navigate: bool = True) -> str:
    page = session.page
    if navigate:
        await session.goto(session.project_url(project_id), ready=PROJECT_READY)
    box = page.locator("flow-editable-text").first

    async def edit() -> None:
        await box.click(timeout=8_000)
        await page.keyboard.press("Meta+A")
        await page.keyboard.type(title)
        await page.keyboard.press("Enter")

    frames = await capture(session, edit, settle=5.0)
    if "o8DA4" not in frames:
        raise RuntimeError(f"rename did not fire o8DA4; saw {sorted(frames)}")
    # A fired rpc is not a landed rename: confirm on the grid, whose first read already shows it (2026-09-15).
    grid = await capture(session, lambda: session.goto(MIGRATED_ROOT, ready=GRID_READY), settle=6.0)
    listed = {p["id"]: p["title"] for p in parsers.projects(one(grid, "UpteDb"))}
    if project_id not in listed:
        raise LookupError(f"rename: project {project_id} is not on the grid")
    if listed[project_id] != title:
        raise RuntimeError(f"rename fired o8DA4 but the grid lists {listed[project_id]!r}, not {title!r}")
    return listed[project_id]


async def delete(session: FlowSession, project_id: str) -> dict[str, Any]:
    page = session.page
    await session.goto(MIGRATED_ROOT, ready=GRID_READY)
    card = page.locator("flow-project-card", has=page.locator(f"a[href*='/project/{project_id}']")).first
    if await card.count() == 0:
        raise LookupError(f"project {project_id} is not on the grid")
    await card.hover(timeout=8_000)
    await page.wait_for_timeout(600)
    await card.get_by_role("button", name=re.compile("Delete project", re.IGNORECASE)).first.click(
        timeout=8_000, force=True
    )
    await page.wait_for_timeout(1_200)
    confirm = (
        page.locator("[role=dialog], mat-dialog-container")
        .get_by_role("button", name=re.compile("delete|remove|confirm|ok", re.IGNORECASE))
        .first
    )
    if await confirm.count() == 0:
        raise RuntimeError("delete: no confirm dialog button found")
    frames = await capture(session, lambda: confirm.click(timeout=8_000), settle=6.0)
    remaining = await list_ids(session)
    if project_id in remaining:
        raise RuntimeError(f"delete: project {project_id} still on the grid; rpcids {sorted(frames)}")
    return {"id": project_id, "rpcids": sorted(frames), "remaining": len(remaining)}
