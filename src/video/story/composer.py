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

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video import gen
from video.flow import agent, clips, reader
from video.flow import download as download_mod
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


_PRICE_VISIBLE_JS = (
    "() => [...document.querySelectorAll('.cdk-overlay-pane, [role=menu], [role=dialog]')]"
    ".some(e => e.offsetParent !== null && /generating will use/i.test(e.innerText || ''))"
)


async def _open_settings(page: Any, label: str = "settings") -> str:
    """Open the settings panel and wait for its live price line, whatever overlay was open before.

    The trigger toggles, so a panel left open from an earlier step would be closed by the click; Escape
    first, then click, then wait for the price line to actually render. Retries the toggle once.
    """
    for attempt in range(2):
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
        trigger = page.locator(SETTINGS).first
        await trigger.wait_for(state="visible", timeout=20_000)
        await trigger.click(timeout=8_000)
        try:
            await page.wait_for_function(_PRICE_VISIBLE_JS, timeout=12_000)
            return await page.evaluate(_OVERLAY_TEXT_JS)
        except PlaywrightTimeoutError:
            if attempt == 0:
                continue
            visible = await page.evaluate(_OVERLAY_TEXT_JS)
            await page.screenshot(path=f"out/t4_settings_fail_{label}.png")
            raise RuntimeError(
                f"composer settings never showed a price line at step {label!r}; "
                f"overlays said {visible[:200]!r} (screenshot out/t4_settings_fail_{label}.png)"
            ) from None
    raise RuntimeError(f"composer settings unreachable at step {label!r}")


async def configure(
    session: FlowSession,
    *,
    mode: str = "Ingredients",
    aspect: str = "9:16",
    count: str = "x1",
    label: str = "settings",
) -> dict[str, Any]:
    """Write every composer setting and report the price the UI now quotes."""
    page = session.page
    await _open_settings(page, label)
    applied = {name: await _click_option(page, name) for name in (mode, aspect, count)}
    text = await page.evaluate(_OVERLAY_TEXT_JS)
    price = price_from(text)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1_000)
    return {"applied": applied, "price": price, "settings_text": text[:300]}


_NOTICE_JS = """() => [...document.querySelectorAll(
  '[role=alert], [role=status], .mat-mdc-snack-bar-label, mat-snack-bar-container, [class*=snack], [class*=toast], [class*=error-message], [class*=rejection]')]
  .filter(e => e.offsetParent !== null)
  .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' '))
  .filter(t => t.length > 3).slice(0, 4).join(' | ')"""


async def snapshot(session: FlowSession, project_id: str, attempts: int = 4) -> tuple[list[Any], set[str]]:
    """Project listing, retried: a page load sometimes does not fire Zzl0ze at all, and crashing on that
    in the poll loop abandons a shot whose submit is already in flight."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await clips._snapshot(session, project_id)
        except LookupError as exc:
            last = exc
            if attempt < attempts - 1:
                await asyncio.sleep(10)
    raise last


async def fetch_720(session: FlowSession, record: dict[str, Any], stem: Path, attempts: int = 6) -> Path:
    """Insist on the 720p rendition before accepting anything smaller.

    `=m22` answers 404 for a while after the record says done; taking `=m18` immediately leaves a
    360x640 clip inside a 720x1280 cut (measured 2026-09-13 on tryon-05).
    """
    for attempt in range(attempts):
        try:
            return await download_mod.fetch_to_file(session.page.request, record["url"] + "=m22", stem)
        except RuntimeError:
            if attempt < attempts - 1:
                await asyncio.sleep(15)
    return await clips._fetch_with_retry(session.page.request, record, stem)


async def _notice(page: Any) -> str:
    """Whatever Flow told the user (toast, snackbar, inline refusal). Transient, so read it early."""
    try:
        return (await page.evaluate(_NOTICE_JS))[:400]
    except Exception:  # noqa: BLE001
        return ""


def _in_picker(page: Any, text: str) -> Any:
    return (
        page.locator(".cdk-overlay-pane, [role=dialog], [role=menu]")
        .locator("button, [role=tab], [role=option], [role=menuitem]")
        .filter(has_text=re.compile(re.escape(text), re.IGNORECASE))
        .first
    )


async def attach_character(session: FlowSession, name: str) -> bool:
    """Open the ingredients picker, switch to Characters and add the entity to the prompt.

    The picker renders lazily (measured: several seconds), so each step waits for its own element
    instead of sleeping a fixed amount.
    """
    page = session.page
    add = page.locator(INGREDIENTS).first
    await add.wait_for(state="visible", timeout=20_000)
    await add.click(timeout=8_000)
    tab = _in_picker(page, "Characters")
    try:
        await tab.wait_for(state="visible", timeout=20_000)
    except PlaywrightTimeoutError as exc:
        raise RuntimeError("ingredients picker never showed a Characters tab") from exc
    await tab.click(timeout=8_000)
    tile = _in_picker(page, name)
    try:
        await tile.wait_for(state="visible", timeout=20_000)
    except PlaywrightTimeoutError as exc:
        raise LookupError(f"character {name!r} is not in the ingredients picker") from exc
    await tile.click(timeout=8_000)
    await page.wait_for_timeout(2_500)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1_000)
    return True


async def _submit(
    session: FlowSession,
    project_id: str,
    *,
    prompt: str,
    setup,
    kind: str,
    job_id: str,
    expected_credits: int,
    out_dir: Path,
    aspect: str = "9:16",
    mode: str = "Ingredients",
    wait: float = 300.0,
) -> dict[str, Any]:
    """The one money path: every mode goes through the same price guard, single click and ledger."""
    ledger = gen.Ledger(out_dir / "ledger.jsonl")
    if any(r.get("status") == "done" for r in ledger.rows(job_id)):
        raise gen.AlreadySubmitted(f"job {job_id} is already done; delete its output to redo it")
    rows, _ = await snapshot(session, project_id)
    before = {r["workflow_id"] for r in rows}
    credits_before = (await reader.credits(session))["balance"]

    await agent.set_mode(session, project_id, False)
    await session.goto(session.project_url(project_id), ready=PROJECT_READY)
    await session.page.wait_for_timeout(2_500)
    settings = await configure(session, mode=mode, aspect=aspect, label="pre")
    await setup(session)
    box = session.page.locator("flow-prompt-box [contenteditable='true']").first
    await box.click(timeout=8_000)
    await session.page.keyboard.insert_text(prompt)
    await session.page.wait_for_timeout(1_500)

    confirm = await configure(session, mode=mode, aspect=aspect, label="confirm")
    ensure_price(confirm["price"], expected_credits)
    start = session.page.get_by_role("button", name=re.compile("Start generation", re.IGNORECASE)).first
    ledger.append(
        job_id,
        "submitted",
        kind=kind,
        project=project_id,
        prompt=prompt[:200],
        quoted_credits=confirm["price"],
        credits_before=credits_before,
    )
    frames = await clips._await_submit(session, lambda: start.click(timeout=8_000))
    notice = await _notice(session.page)

    output, fresh = None, []
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        rows, _ = await snapshot(session, project_id)
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
        path = str(await fetch_720(session, output, out_dir / job_id))
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
        notice=notice or (await _notice(session.page)),
    )
    if not path:
        raise RuntimeError(
            f"{job_id}: nothing was generated within {wait:.0f}s, spent "
            f"{credits_before - credits_after} credits; rpcids {sorted(frames)}; "
            f"settings {settings['applied']}; Flow said: {notice or '(no message captured)'}"
        )
    return {
        "job_id": job_id,
        "kind": kind,
        "media_id": output["id"],
        "path": path,
        "quoted_credits": confirm["price"],
        "credits_before": credits_before,
        "credits_after": credits_after,
        "spent": credits_before - credits_after,
    }


START_SLOT = (
    "flow-prompt-box button[aria-label='Start'], flow-base-prompt-box button[aria-label='Start'],"
    " flow-prompt-box button[aria-label*='Image ingredient'],"
    " flow-base-prompt-box button[aria-label*='Image ingredient']"
)


async def pin_start_frame(session: FlowSession, name: str) -> bool:
    """Pin a project image into the Frames Start slot, found by name through the picker's search box.

    Measured 2026-09-13: the slot opens a "Select a frame image" picker listing project images with no
    text on the tiles, so the filename is typed into the search field and the first result is taken.
    """
    page = session.page
    slot = page.locator(START_SLOT).first
    await slot.wait_for(state="visible", timeout=20_000)
    await slot.click(timeout=8_000)
    await page.wait_for_timeout(3_000)
    search = page.locator(".cdk-overlay-pane input, [role=dialog] input").first
    if await search.count():
        await search.click(timeout=8_000)
        await page.keyboard.insert_text(name)
        await page.wait_for_timeout(3_000)
    tile = page.locator(".cdk-overlay-pane img, [role=dialog] img").first
    try:
        await tile.wait_for(state="visible", timeout=20_000)
    except PlaywrightTimeoutError as exc:
        raise LookupError(f"no frame image matched {name!r} in the picker") from exc
    await tile.click(timeout=8_000)
    await page.wait_for_timeout(3_000)
    pinned = await page.evaluate(
        "() => [...document.querySelectorAll('flow-prompt-box button, flow-base-prompt-box button')]"
        ".some(e => /image ingredient/i.test(e.getAttribute('aria-label') || ''))"
    )
    if not pinned:
        raise RuntimeError(f"picked {name!r} but the Start slot stayed empty")
    return True


async def generate_with_character(
    session: FlowSession, project_id: str, *, character: str, **kwargs: Any
) -> dict[str, Any]:
    async def setup(s: FlowSession) -> None:
        await attach_character(s, character)

    return await _submit(session, project_id, setup=setup, kind="character", mode="Ingredients", **kwargs)


async def generate_from_frame(
    session: FlowSession, project_id: str, *, start_name: str, **kwargs: Any
) -> dict[str, Any]:
    async def setup(s: FlowSession) -> None:
        await pin_start_frame(s, start_name)

    return await _submit(session, project_id, setup=setup, kind="frames", mode="Frames", **kwargs)
