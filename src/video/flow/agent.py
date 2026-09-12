"""Flow Agent mode on the migrated host (measured 2026-09-12): the agent chip on the composer toggles
the prompt box into the agent box (rpc DA4VGb, Kcr7Ub on enable). Sending a message may make the
agent generate media, which spends credits."""

from __future__ import annotations

import re
from typing import Any

from video.flow.reader import capture
from video.session import PROJECT_READY, FlowSession

CHIP = "button.agent-mode-chip, flow-agent-mode-toggle-chip button"
_TEXT_JS = "() => (document.body.innerText || '').trim().replace(/\\s+/g, ' ')"


async def _open(session: FlowSession, project_id: str) -> None:
    await session.goto(session.project_url(project_id), ready=PROJECT_READY)
    await session.page.wait_for_timeout(2_000)


async def set_mode(session: FlowSession, project_id: str, enabled: bool) -> dict[str, Any]:
    page = session.page
    await _open(session, project_id)
    chip = page.locator(CHIP).first
    pressed = (await chip.get_attribute("aria-pressed")) == "true"
    frames: dict[str, list[Any]] = {}
    if pressed != enabled:
        frames = await capture(session, lambda: chip.click(timeout=8_000), settle=4.0)
        pressed = (await chip.get_attribute("aria-pressed")) == "true"
    return {"enabled": pressed, "rpcids": sorted(frames)}


async def send(session: FlowSession, project_id: str, message: str, wait: float = 60.0) -> dict[str, Any]:
    page = session.page
    state = await set_mode(session, project_id, True)
    if not state["enabled"]:
        raise RuntimeError("agent mode did not turn on")
    box = page.locator("flow-prompt-box [contenteditable='true']").first
    await box.click(timeout=8_000)
    await page.keyboard.type(message)
    before = await page.evaluate(_TEXT_JS)
    send_button = page.get_by_role("button", name=re.compile("Start generation|Send", re.IGNORECASE)).last
    frames = await capture(session, lambda: send_button.click(timeout=8_000), settle=wait)
    after = await page.evaluate(_TEXT_JS)
    return {
        "rpcids": sorted(frames),
        "reply_excerpt": after[len(before) - 200 if len(before) > 200 else 0 :][:800],
    }
