"""What Flow lays over its own page.

Two are known and allowed: the cookie notice, whose single button the owner lets the driver press (DECISIONS
2026-10-01), and the banner asking to opt in to keeping the oldest creations, which the owner lets the driver opt in
through (DECISIONS 2026-11-09). No other overlay is given a handler; `covering` names whatever sits on a control for the callers that ask
(the composer's Settings trigger and its "+" button), and nothing of it is clicked.
"""

from __future__ import annotations

import asyncio
import re
import sys
from typing import Any

# Measured 2026-10-01 16:57: the notice sits at 0,654 to 1280,720 of the 1280x720 viewport, over the composer's
# bottom row, and every click there timed out with nothing spent.
COOKIE_BAR = ".glue-cookie-notification-bar"
COOKIE_ACCEPT = ".glue-cookie-notification-bar__accept"
# Together under the 8 s most of the driver's actions allow themselves: the action that met the notice waits for the
# handler under its OWN deadline (playwright's coreBundle.js:21719-21725), so a slower handler fails that action even
# when the control it wants is not under the notice. Measured 2026-10-02 by the page's own clock: the bar is gone in
# the same tick as the click, 1.5 s after it first showed.
READ_MS = 1_000
PRESS_MS = 3_000
GONE_MS = 3_000
GONE_POLL_MS = 100

_COVERING_JS = """(selector) => {
  const el = document.querySelector(selector);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  if (!top || top === el || el.contains(top) || top.contains(el)) return null;
  const box = top.closest('[role=region], [role=dialog], [role=alertdialog], .cdk-overlay-pane') || top;
  return {tag: box.tagName.toLowerCase(), id: box.id || '',
    text: (box.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 160)};
}"""


async def watch_cookie_notice(page: Any, notices: list[str], unpressed: list[str]) -> asyncio.Event:
    """Have Playwright press the cookie notice's own button whenever the notice stands in the way of an action.

    `notices` gets what a notice said once it was pressed AND gone; `unpressed` gets, once, why a notice was left
    standing. The handler never raises: Playwright runs it in a task nobody awaits (playwright/_impl/_page.py:219-223),
    so its error would reach no caller, and by default the action that met the notice would then wait for the notice
    to hide until its own deadline, which is why the wait is done here instead (review of plan AL, 2026-10-02).

    The event returned is set whenever no press is in flight. The action that met the notice can fail, or be given
    up, while the press runs on, so whoever reads the two lists waits for it first (FlowSession.notices_settled)."""
    idle = asyncio.Event()
    idle.set()
    gave_up = False

    def left(why: str) -> None:
        if why not in unpressed:
            unpressed.append(why)
            sys.stderr.write(f"[video] {why}\n")

    async def dismiss(bar: Any) -> None:
        nonlocal gave_up
        # A notice still standing is met again by every later action: one failed press is not tried again, or each
        # of those actions would be held for seconds.
        if gave_up:
            return
        idle.clear()
        text, pressed = "", False
        try:
            text = " ".join((await bar.inner_text(timeout=READ_MS)).split())[:200]
            accept = bar.locator(COOKIE_ACCEPT)
            found = await accept.count()
            if found != 1:
                left(
                    f"Flow's cookie notice shows {found} accept buttons, not one; nothing was clicked: {text!r}"
                )
                return
            await accept.click(timeout=PRESS_MS)
            pressed = True
            # Never Locator.wait_for: it runs Playwright's action pre-checks (coreBundle.js:22812-22813), which wait
            # on this very handler once the action that met the notice has run out of time.
            waited = 0
            while await bar.is_visible():
                if waited >= GONE_MS:
                    gave_up = True
                    left(
                        f"Flow's cookie notice did not go away within {GONE_MS / 1000:g} s of its button being "
                        f"pressed: {text!r}"
                    )
                    return
                await page.wait_for_timeout(GONE_POLL_MS)
                waited += GONE_POLL_MS
        except Exception as exc:  # noqa: BLE001
            gave_up = True
            how = "after its button was pressed" if pressed else "its button was not pressed"
            left(f"Flow's cookie notice did not go away ({type(exc).__name__}, {how}): {text!r}")
            return
        finally:
            idle.set()
        notices.append(text)
        sys.stderr.write(f"[video] pressed Flow's cookie notice: {text}\n")

    await page.add_locator_handler(page.locator(COOKIE_BAR), dismiss, no_wait_after=True)
    return idle


# Measured 2026-11-09 on harleychina0 (out/av/banner.json): the banner's "Opt in" opens a dialog "Keep your oldest
# creations in Flow" (Cancel, Opt in), and its Opt in answers "You're opted-in!"; the banner is gone on reload.
EXPIRY = "text=/oldest creations will expire soon/i"
OPTED = "text=/opted-in/i"
KEEP_DIALOG = "[role=dialog], [role=alertdialog]"
OPT_IN = re.compile(r"^\s*Opt in\s*$", re.IGNORECASE)
DIALOG_MS = 5_000
OPTED_MS = 8_000
STEP_MS = 250


async def keep_oldest_creations(page: Any, notices: list[str], unpressed: list[str]) -> bool:
    """Opt in to keeping the creations Flow would otherwise expire, when its banner shows; never raises.

    `notices` gets what the dialog said once Flow confirmed the opt-in; `unpressed` gets why it was left, once."""

    def left(why: str) -> bool:
        if why not in unpressed:
            unpressed.append(why)
            sys.stderr.write(f"[video] {why}\n")
        return False

    async def shows(locator: Any, budget_ms: int) -> bool:
        waited = 0
        while not await locator.count():
            if waited >= budget_ms:
                return False
            await page.wait_for_timeout(STEP_MS)
            waited += STEP_MS
        return True

    try:
        if not await page.locator(EXPIRY).count():
            return False
        offer = page.get_by_role("button", name=OPT_IN)
        if (found := await offer.count()) != 1:
            return left(f"Flow's expiry banner shows {found} Opt in buttons, not one; nothing was clicked")
        await offer.first.click(timeout=PRESS_MS)
        dialog = page.locator(KEEP_DIALOG).filter(has_text="Keep your oldest creations")
        confirm = dialog.get_by_role("button", name=OPT_IN)
        if not await shows(dialog, DIALOG_MS) or (found := await confirm.count()) != 1:
            await page.keyboard.press("Escape")
            return left(
                f"Flow's expiry banner showed {found} Opt in buttons in its dialog, not one; left as it was"
            )
        text = " ".join((await dialog.first.inner_text(timeout=READ_MS)).split())[:200]
        await confirm.first.click(timeout=PRESS_MS)
        if not await shows(page.locator(OPTED), OPTED_MS):
            return left(f"Flow did not say it opted in after its dialog's Opt in was pressed: {text!r}")
    except Exception as exc:  # noqa: BLE001
        return left(f"Flow's expiry banner was not opted in ({type(exc).__name__})")
    notices.append(text)
    sys.stderr.write(f"[video] opted in to keep the oldest creations: {text}\n")
    return True


async def covering(page: Any, selector: str) -> dict[str, Any] | None:
    """What sits on top of the middle of a control, when it is neither the control nor one of its ancestors."""
    return await page.evaluate(_COVERING_JS, selector)
