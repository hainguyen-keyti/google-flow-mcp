"""Drive the project composer in Ingredients mode with a character attached.

gflow cannot do this: `@Name` and `--reference-entity` are unported on the migrated host, so the story
pipeline drives the composer itself. Reuses the Plan 1 helpers for listing, submitting and downloading
rather than rebuilding them.

Two money guards, both measured the hard way in Plan 1 and T2:
- the composer REMEMBERS the last run (mode, aspect, model, count), so every setting is written every
  time and the live "Generating will use N credits" line is checked before the click;
- the submit leaves late, so the page is held until the request goes out, and the click never repeats.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from video import gen
from video.flow import agent, clips, reader
from video.session import PROJECT_READY, FlowSession

PRICE_RE = re.compile(r"generating will use\s+(\d+)\s+credit", re.IGNORECASE)
SETTINGS = "button[aria-label*='Settings trigger'], .settings-trigger-button"
INGREDIENTS = (
    "flow-prompt-box button[aria-label*='ngredient'], flow-base-prompt-box button[aria-label*='ngredient']"
)
_OVERLAY_TEXT_JS = """() => [...document.querySelectorAll('.cdk-overlay-pane, [role=menu], [role=dialog]')]
  .filter(e => e.offsetParent !== null)
  .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ')).join(' | ')"""


def price_from(text: str) -> int | None:
    match = PRICE_RE.search(text or "")
    return int(match.group(1)) if match else None


def ensure_price(actual: int | None, expected: int) -> None:
    if actual is None:
        raise RuntimeError(
            f"refusing to submit: could not read the composer price line, expected {expected} credits"
        )
    if actual != expected:
        raise RuntimeError(
            f"refusing to submit: composer says {actual} credits but the shot is planned at {expected}; "
            "check the mode, aspect and count chips"
        )


def pick_output(fresh: list[dict[str, Any]], prompt: str) -> dict[str, Any] | None:
    videos = [r for r in fresh if r.get("kind") == "video"]
    if not videos:
        return None
    needle = prompt.strip()[:40].lower()
    for row in sorted(videos, key=lambda r: r.get("created") or 0, reverse=True):
        if needle and needle in (row.get("prompt") or "").lower():
            return row
    return max(videos, key=lambda r: r.get("created") or 0)


async def _click_option(page: Any, label: str) -> bool:
    option = (
        page.locator("[role=menuitem], [role=option], .cdk-overlay-pane button")
        .filter(has_text=re.compile(re.escape(label), re.IGNORECASE))
        .first
    )
    if await option.count() == 0:
        return False
    await option.click(timeout=8_000)
    await page.wait_for_timeout(1_200)
    return True


async def configure(
    session: FlowSession,
    *,
    mode: str = "Ingredients",
    aspect: str = "9:16",
    count: str = "x1",
) -> dict[str, Any]:
    """Write every composer setting and report the price the UI now quotes."""
    page = session.page
    trigger = page.locator(SETTINGS).first
    await trigger.wait_for(state="visible", timeout=20_000)
    await trigger.click(timeout=8_000)
    await page.wait_for_timeout(1_500)
    applied = {name: await _click_option(page, name) for name in (mode, aspect, count)}
    text = await page.evaluate(_OVERLAY_TEXT_JS)
    price = price_from(text)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1_000)
    return {"applied": applied, "price": price, "settings_text": text[:300]}


async def attach_character(session: FlowSession, name: str) -> bool:
    page = session.page
    add = page.locator(INGREDIENTS).first
    await add.wait_for(state="visible", timeout=20_000)
    await add.click(timeout=8_000)
    await page.wait_for_timeout(2_000)
    if not await _click_option(page, "Characters"):
        raise RuntimeError("ingredients picker has no Characters tab")
    await page.wait_for_timeout(1_500)
    tile = (
        page.locator(".cdk-overlay-pane, [role=dialog]")
        .locator("button, [role=option]")
        .filter(has_text=re.compile(re.escape(name), re.IGNORECASE))
        .first
    )
    if await tile.count() == 0:
        raise LookupError(f"character {name!r} is not in the ingredients picker")
    await tile.click(timeout=8_000)
    await page.wait_for_timeout(2_000)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1_000)
    return True


async def generate(
    session: FlowSession,
    project_id: str,
    *,
    prompt: str,
    character: str,
    job_id: str,
    expected_credits: int,
    out_dir: Path,
    aspect: str = "9:16",
    wait: float = 300.0,
) -> dict[str, Any]:
    ledger = gen.Ledger(out_dir / "ledger.jsonl")
    if ledger.has_submitted(job_id):
        raise gen.AlreadySubmitted(f"job {job_id} already has a submitted row; nothing was spent")
    rows, _ = await clips._snapshot(session, project_id)
    before = {r["workflow_id"] for r in rows}
    credits_before = (await reader.credits(session))["balance"]

    await agent.set_mode(session, project_id, False)
    await session.goto(session.project_url(project_id), ready=PROJECT_READY)
    await session.page.wait_for_timeout(2_500)
    settings = await configure(session, aspect=aspect)
    await attach_character(session, character)
    box = session.page.locator("flow-prompt-box [contenteditable='true']").first
    await box.click(timeout=8_000)
    await session.page.keyboard.insert_text(prompt)
    await session.page.wait_for_timeout(1_500)

    confirm = await configure(session, aspect=aspect)
    ensure_price(confirm["price"], expected_credits)
    start = session.page.get_by_role("button", name=re.compile("Start generation", re.IGNORECASE)).first
    ledger.append(
        job_id,
        "submitted",
        kind="story",
        project=project_id,
        character=character,
        prompt=prompt[:200],
        quoted_credits=confirm["price"],
        credits_before=credits_before,
    )
    frames = await clips._await_submit(session, lambda: start.click(timeout=8_000))

    output, fresh = None, []
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        rows, _ = await clips._snapshot(session, project_id)
        fresh = clips.new_records(before, rows)
        output = pick_output(fresh, prompt)
        if output is not None and clips.is_done(output):
            break
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(15)

    credits_after = (await reader.credits(session))["balance"]
    path = None
    if output is not None and clips.is_done(output):
        path = str(await clips._fetch_with_retry(session.page.request, output, out_dir / job_id))
    status = "done" if path else "failed"
    ledger.append(
        job_id,
        status,
        media_id=output["id"] if output else None,
        path=path,
        credits_before=credits_before,
        credits_after=credits_after,
        spent=credits_before - credits_after,
        rpcids=sorted(frames),
    )
    if not path:
        raise RuntimeError(
            f"{job_id}: nothing was generated within {wait:.0f}s, spent "
            f"{credits_before - credits_after} credits; rpcids {sorted(frames)}; settings {settings['applied']}"
        )
    return {
        "job_id": job_id,
        "media_id": output["id"],
        "path": path,
        "quoted_credits": confirm["price"],
        "credits_before": credits_before,
        "credits_after": credits_after,
        "spent": credits_before - credits_after,
    }
