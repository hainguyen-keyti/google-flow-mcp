"""One Flow browser session on the gflow profile.

gflow's FlowApiClient already holds the cross-process profile lease (client.py:733), so this
class never takes a second one; the module guard serializes sessions inside one process.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Self

from gflow_cli import auth as _auth
from gflow_cli.api.client import FlowApiClient

import video  # noqa: F401
from video import gen, offscreen
from video.flow import overlays

_GUARD = threading.Lock()
_GUARD_POLL_S = 0.05
PAGE_CLOSE_TIMEOUT_S = 10.0
# Above the 7 s the cookie notice's handler can take (overlays.READ_MS + PRESS_MS + GONE_MS).
NOTICE_SETTLE_S = 10.0

MIGRATED_ROOT = "https://flow.google.com/"
# An account with no projects renders only this button, inside the signed-in projects page (measured 2026-09-28).
NEW_PROJECT_BUTTON = "flow-projects-page button.new-project-button"
GRID_READY = f'a[href*="/project/"], {NEW_PROJECT_BUTTON}'
PROJECT_READY = "flow-project-page"
COMPOSER_READY = ".settings-trigger-button"


def profile_dir_for(profile: str) -> Path:
    path = _auth.profile_dir(profile)
    if not path.exists():
        raise FileNotFoundError(f"no gflow profile dir for '{profile}': {path}")
    return path


def _default_factory(profile_dir: Path, *, headless: bool) -> FlowApiClient:
    return FlowApiClient(profile_dir=profile_dir, headless=headless)


class FlowSession:
    def __init__(
        self,
        profile: str = "default",
        *,
        profile_dir: Path | None = None,
        headless: bool = False,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.profile_dir = profile_dir if profile_dir is not None else profile_dir_for(profile)
        self.headless = headless
        self._factory = client_factory or _default_factory
        self.client: Any = None
        self.page: Any = None
        self.notices: list[str] = []
        self.unpressed: list[str] = []
        self._notice_idle: asyncio.Event | None = None
        self._entered = False

    async def __aenter__(self) -> Self:
        # Never `to_thread(_GUARD.acquire)`: that thread outlives a cancelled caller, takes the lock later and
        # never gives it back, which hung every later browser tool (review 2026-09-15). A poll holds nothing.
        while not _GUARD.acquire(blocking=False):
            await asyncio.sleep(_GUARD_POLL_S)
        try:
            offscreen.install()
            self.client = self._factory(self.profile_dir, headless=self.headless)
            await self.client.__aenter__()
            self._entered = True
            self.page = await self.client._context.new_page()
            self._notice_idle = await overlays.watch_cookie_notice(self.page, self.notices, self.unpressed)
        except BaseException:
            await self._teardown()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._teardown()

    async def notices_settled(self) -> None:
        """Wait out a press of the cookie notice that is still in flight, so `notices` and `unpressed` are read after
        it and not before: the action that met the notice can fail, or be given up, while the press runs on. Bounded,
        since this comes before the teardown that gives back the profile lease and the guard."""
        if self._notice_idle is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._notice_idle.wait(), NOTICE_SETTLE_S)

    async def _teardown(self) -> None:
        try:
            try:
                if self.page is not None:
                    # Bounded, and never raised: a close that never returns would keep the lease and the guard below
                    # forever, and a close error would replace the call's own result or error; the client exit closes it.
                    try:
                        await asyncio.wait_for(self.page.close(), PAGE_CLOSE_TIMEOUT_S)
                    except TimeoutError:
                        logging.getLogger(__name__).warning(
                            "page.close() did not return within %s s; leaving it to the client exit",
                            PAGE_CLOSE_TIMEOUT_S,
                        )
                    except Exception as exc:  # noqa: BLE001
                        # Playwright appends a call log that can quote request headers: keep the first line, scrubbed.
                        detail = gen._scrub(str(exc).split("Call log:")[0].strip())
                        logging.getLogger(__name__).warning(
                            "page.close() failed (%s: %s); leaving it to the client exit",
                            type(exc).__name__,
                            detail,
                        )
            finally:
                # A failed or cancelled close must not skip the client's exit: that exit returns gflow's profile
                # lease (and survives cancellation itself), without which every later session is locked out.
                self.page = None
                if self.client is not None and self._entered:
                    await self.client.__aexit__(None, None, None)
                self.client = None
                self._entered = False
        finally:
            _GUARD.release()

    async def goto(self, url: str, *, ready: str | None = None, timeout_ms: int = 60_000) -> None:
        await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if ready:
            await self.page.locator(ready).first.wait_for(state="visible", timeout=timeout_ms)

    @staticmethod
    def project_url(project_id: str) -> str:
        return f"{MIGRATED_ROOT}project/{project_id}"
