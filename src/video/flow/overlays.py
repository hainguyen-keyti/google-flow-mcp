"""What Flow lays over its own page.

One overlay is known and allowed: the cookie notice, whose single button the owner lets the driver press (DECISIONS
2026-10-01). Anything else that covers a control is named for the caller and never clicked.
"""

from __future__ import annotations

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


async def watch_cookie_notice(page: Any, notices: list[str]) -> None:
    """Have Playwright press the cookie notice's own button whenever the notice stands in the way of an action,
    and keep what the notice said. No other overlay is given a handler."""

    async def dismiss(bar: Any) -> None:
        text = " ".join((await bar.inner_text()).split())[:200]
        accept = bar.locator(COOKIE_ACCEPT)
        found = await accept.count()
        if found != 1:
            raise LookupError(
                f"Flow's cookie notice shows {found} accept buttons, not one; nothing was clicked: {text!r}"
            )
        await accept.click(timeout=8_000)
        notices.append(text)

    await page.add_locator_handler(page.locator(COOKIE_BAR), dismiss)


async def covering(page: Any, selector: str) -> dict[str, Any] | None:
    """What sits on top of the middle of a control, when it is neither the control nor one of its ancestors."""
    return await page.evaluate(_COVERING_JS, selector)
