"""What Flow lays over its own page.

One overlay is known and allowed: the cookie notice, whose single button the owner lets the driver press (DECISIONS
2026-10-01). No other overlay is given a handler; `covering` names whatever sits on a control for the callers that ask
(the composer's Settings trigger and its "+" button), and nothing of it is clicked.
"""

from __future__ import annotations

import sys
from typing import Any

# Measured 2026-10-01 16:57: the notice sits at 0,654 to 1280,720 of the 1280x720 viewport, over the composer's
# bottom row, and every click there timed out with nothing spent.
COOKIE_BAR = ".glue-cookie-notification-bar"
COOKIE_ACCEPT = ".glue-cookie-notification-bar__accept"

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


async def watch_cookie_notice(page: Any, notices: list[str], unpressed: list[str]) -> None:
    """Have Playwright press the cookie notice's own button whenever the notice stands in the way of an action.

    `notices` gets what a notice said once it was pressed AND gone; `unpressed` gets, once, why a notice was left
    standing. The handler never raises: Playwright runs it in a task nobody awaits (playwright/_impl/_page.py:219-223),
    so its error would reach no caller, and by default the action that met the notice would then wait for the notice
    to hide until its own deadline, which is why the wait is done here instead (review of plan AL, 2026-10-02)."""

    def left(why: str) -> None:
        if why not in unpressed:
            unpressed.append(why)
            sys.stderr.write(f"[video] {why}\n")

    async def dismiss(bar: Any) -> None:
        text = " ".join((await bar.inner_text()).split())[:200]
        accept = bar.locator(COOKIE_ACCEPT)
        found = await accept.count()
        if found != 1:
            left(f"Flow's cookie notice shows {found} accept buttons, not one; nothing was clicked: {text!r}")
            return
        try:
            await accept.click(timeout=8_000)
            await bar.wait_for(state="hidden", timeout=8_000)
        except Exception as exc:  # noqa: BLE001
            left(
                f"Flow's cookie notice did not go away when its button was pressed ({type(exc).__name__}): {text!r}"
            )
            return
        notices.append(text)
        sys.stderr.write(f"[video] pressed Flow's cookie notice: {text}\n")

    await page.add_locator_handler(page.locator(COOKIE_BAR), dismiss, no_wait_after=True)


async def covering(page: Any, selector: str) -> dict[str, Any] | None:
    """What sits on top of the middle of a control, when it is neither the control nor one of its ancestors."""
    return await page.evaluate(_COVERING_JS, selector)
