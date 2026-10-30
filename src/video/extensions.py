"""Keep the profile's browser extensions on in the Chrome this repo launches (opt-in, VIDEO_KEEP_EXTENSIONS=1).

Playwright adds `--disable-extensions` to every launch; listing it in `ignore_default_args` drops it, so a VPN
extension installed in the profile (profile_acc2, Windscribe) runs during automated Flow work. gflow opens every
browser with `launch_persistent_context` (gflow_cli/api/client.py:483), so that one call is wrapped here, never gflow.
"""

from __future__ import annotations

import os
from typing import Any

ENV = "VIDEO_KEEP_EXTENSIONS"
FLAG = "--disable-extensions"
_installed = False


def install() -> None:
    """Wrap Playwright's launch once per process; a no-op unless the env var is "1"."""
    global _installed
    if _installed or os.environ.get(ENV) != "1":
        return
    from playwright.async_api import BrowserType

    original = BrowserType.launch_persistent_context

    async def launch(self: Any, user_data_dir: Any, **kwargs: Any) -> Any:
        ignored = kwargs.get("ignore_default_args")
        if ignored is not True:
            kwargs["ignore_default_args"] = (
                [*(ignored or []), FLAG] if FLAG not in (ignored or []) else ignored
            )
        return await original(self, user_data_dir, **kwargs)

    BrowserType.launch_persistent_context = launch  # type: ignore[method-assign]
    _installed = True
