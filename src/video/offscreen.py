"""Open the tool's Chrome off to the side of the screen instead of in front of the owner.

Headless is not an option: reCAPTCHA Enterprise answers headless Chromium with a 403 (gflow
_docs/AUTHENTICATION.md:490), so the window stays real and headed and is only placed off screen. macOS keeps a strip of
about 40 px on screen whatever is asked (measured 2026-09-28: left=-4000 came back as -1242 for a 1282-wide window), and
a page placed there still renders (visibilityState stayed "visible"), unlike a minimized one.

gflow hard-codes its launch flags (gflow_cli/api/client.py:516-520) and opens every browser with
`launch_persistent_context`, so the flag is added by wrapping that one Playwright call in the processes this repo runs,
never by editing gflow. Set VIDEO_BROWSER_OFFSCREEN=0 to see the windows again.
"""

from __future__ import annotations

import os
from typing import Any

from playwright.async_api import BrowserType

ENV = "VIDEO_BROWSER_OFFSCREEN"
FLAG = "--window-position=-4000,60"
_installed = False


def install() -> None:
    """Wrap Playwright's launch once per process; a no-op when switched off."""
    global _installed
    if _installed or os.environ.get(ENV, "1") == "0":
        return
    original = BrowserType.launch_persistent_context

    async def launch(self: Any, user_data_dir: Any, **kwargs: Any) -> Any:
        if not kwargs.get("headless"):
            args = [arg for arg in (kwargs.get("args") or []) if not str(arg).startswith("--window-position")]
            kwargs["args"] = [*args, FLAG]
        return await original(self, user_data_dir, **kwargs)

    BrowserType.launch_persistent_context = launch  # type: ignore[method-assign]
    _installed = True
