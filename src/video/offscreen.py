"""Move the tool's Chrome off to the side of the screen instead of in front of the owner.

Headless is not an option: reCAPTCHA Enterprise answers headless Chromium with a 403 (gflow
_docs/AUTHENTICATION.md:490), so the window stays real and headed and is only moved off screen. Measured 2026-09-28 on
one monitor: a `--window-position` flag is pulled back on screen by Chrome at launch (it came up at left=0), while the
DevTools call `Browser.setWindowBounds` sent after launch moves it (left=-4000 came back as -1242 for a 1282-wide window,
so about 40 px stay visible), and a page there kept rendering (visibilityState "visible"). With a second monitor to the
left the window may land on that monitor instead; that is unmeasured.

gflow opens every browser with `launch_persistent_context` and hard-codes its flags (gflow_cli/api/client.py:516-520),
so that one Playwright call is wrapped in the processes this repo runs, never by editing gflow. Set
VIDEO_BROWSER_OFFSCREEN=0 to see the windows again.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import Any

from playwright.async_api import BrowserType

ENV = "VIDEO_BROWSER_OFFSCREEN"
LEFT = -4000
# A DevTools call has no timeout of its own; this bounds the whole move so it can never stall a launch (review, F3).
MOVE_TIMEOUT_S = 5.0
_installed = False


def headed(kwargs: dict[str, Any]) -> bool:
    """Playwright launches headless when the flag is omitted, so only an explicit False has a window to move."""
    return kwargs.get("headless") is False


async def _move(context: Any) -> None:
    pages = list(getattr(context, "pages", []) or [])
    if not pages:
        return
    cdp = await context.new_cdp_session(pages[0])
    try:
        window = await cdp.send("Browser.getWindowForTarget")
        await cdp.send(
            "Browser.setWindowBounds",
            {"windowId": window["windowId"], "bounds": {"left": LEFT, "windowState": "normal"}},
        )
        moved = await cdp.send("Browser.getWindowBounds", {"windowId": window["windowId"]})
        sys.stderr.write(f"[video] browser window moved aside (left={moved.get('bounds', {}).get('left')})\n")
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(cdp.detach(), 1.0)


async def push_aside(context: Any) -> None:
    """Move the context's window off the left edge; never raises, since hiding a window must not break a paid call."""
    try:
        await asyncio.wait_for(_move(context), MOVE_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        return


def install() -> None:
    """Wrap Playwright's launch once per process; a no-op when switched off."""
    global _installed
    if _installed or os.environ.get(ENV, "1") == "0":
        return
    original = BrowserType.launch_persistent_context

    async def launch(self: Any, user_data_dir: Any, **kwargs: Any) -> Any:
        context = await original(self, user_data_dir, **kwargs)
        if headed(kwargs):
            await push_aside(context)
        return context

    BrowserType.launch_persistent_context = launch  # type: ignore[method-assign]
    _installed = True
