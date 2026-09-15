"""Flow Agent mode on the migrated host (measured 2026-09-12): the agent chip on the composer toggles
the prompt box into the agent box (rpc DA4VGb, Kcr7Ub on enable). After a message the page keeps an agent
session panel (flow-agent-panel) open: the chip is not rendered and the composer settings are hidden until
the panel's Close button is clicked, so set_mode closes it first and send restores the mode it found.
Sending a message may make the agent generate media, which spends credits: every send is ledgered with
credits before and after (I1, I5)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from video import gen
from video.flow import reader
from video.flow.reader import capture
from video.session import PROJECT_READY, FlowSession

CHIP = "button.agent-mode-chip, flow-agent-mode-toggle-chip button"
PANEL = "flow-agent-panel"
_TEXT_JS = "() => (document.body.innerText || '').trim().replace(/\\s+/g, ' ')"


async def _open(session: FlowSession, project_id: str) -> None:
    await session.goto(session.project_url(project_id), ready=PROJECT_READY)
    await session.page.wait_for_timeout(2_000)


async def _close_panel(page: Any) -> bool:
    panel = page.locator(PANEL).first
    if await panel.count() == 0:
        return False
    close = panel.get_by_role("button", name=re.compile("^Close$", re.IGNORECASE)).first
    if await close.count() == 0:
        return False
    await close.click(timeout=8_000)
    await page.wait_for_timeout(1_500)
    return True


async def _settle_composer(page: Any) -> bool:
    """Wait for the chip or the agent session panel, whichever renders first; close the panel."""
    for _ in range(20):
        if await page.locator(CHIP).first.count():
            return False
        if await _close_panel(page):
            return True
        await page.wait_for_timeout(1_000)
    return False


async def set_mode(session: FlowSession, project_id: str, enabled: bool) -> dict[str, Any]:
    page = session.page
    await _open(session, project_id)
    panel_closed = await _settle_composer(page)
    chip = page.locator(CHIP).first
    await chip.wait_for(state="visible", timeout=15_000)
    was = (await chip.get_attribute("aria-pressed")) == "true"
    pressed = was
    frames: dict[str, list[Any]] = {}
    if pressed != enabled:
        frames = await capture(session, lambda: chip.click(timeout=8_000), settle=4.0)
        pressed = (await chip.get_attribute("aria-pressed")) == "true"
    return {"enabled": pressed, "was": was, "panel_closed": panel_closed, "rpcids": sorted(frames)}


async def send(
    session: FlowSession,
    project_id: str,
    message: str,
    wait: float = 60.0,
    *,
    out_dir: Path = Path("out"),
    job_id: str | None = None,
) -> dict[str, Any]:
    # The message is typed with keyboard.type, which presses Enter for a newline before the `submitted` row.
    if "\n" in message or "\r" in message:
        raise ValueError("agent send: the message must be one line; the agent box takes a newline as Enter")
    ledger = gen.Ledger(out_dir / "ledger.jsonl")
    job_id = job_id or str(uuid.uuid4())
    if ledger.has_submitted(job_id):
        raise gen.AlreadySubmitted(f"job {job_id} already has a submitted row; use a new job id")
    page = session.page
    credits_before = (await reader.credits(session))["balance"]
    state = await set_mode(session, project_id, True)
    if not state["enabled"]:
        raise RuntimeError("agent mode did not turn on")
    box = page.locator("flow-prompt-box [contenteditable='true']").first
    await box.click(timeout=8_000)
    await page.keyboard.type(message)
    before = await page.evaluate(_TEXT_JS)
    send_button = page.get_by_role("button", name=re.compile("Start generation|Send", re.IGNORECASE)).last
    ledger.append(
        job_id, "submitted", kind="agent", project=project_id, message=message, credits_before=credits_before
    )
    frames = await capture(session, lambda: send_button.click(timeout=8_000), settle=wait)
    after = await page.evaluate(_TEXT_JS)
    reply = after[len(before) - 200 if len(before) > 200 else 0 :][:800]
    restored = await set_mode(session, project_id, state["was"])
    credits_after = (await reader.credits(session))["balance"]
    ledger.append(
        job_id,
        "done",
        credits_before=credits_before,
        credits_after=credits_after,
        spent=credits_before - credits_after,
        rpcids=sorted(frames),
    )
    return {
        "job_id": job_id,
        "rpcids": sorted(frames),
        "reply_excerpt": reply,
        "mode_restored": restored["enabled"] == state["was"],
        "credits_before": credits_before,
        "credits_after": credits_after,
    }
