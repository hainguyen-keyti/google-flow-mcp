"""One Flow browser session on the gflow profile.

gflow's FlowApiClient already holds the cross-process profile lease (client.py:733), so this
class never takes a second one; the module guard serializes sessions inside one process.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Self

from gflow_cli import auth as _auth
from gflow_cli.api.client import FlowApiClient
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import video  # noqa: F401
from video import bodies, extensions, gen, offscreen
from video.flow import overlays, version

_GUARD = threading.Lock()
_GUARD_POLL_S = 0.05
# A browser kept between calls holds gflow's profile lease while it idles, and gflow fails a second launch at once
# unless told to wait: every process of this repo waits for the holder, which yields within WANT_POLL_S once asked.
os.environ.setdefault("GFLOW_CLI_LEASE_WAIT_SECONDS", "180")
KEEP_ENV = "VIDEO_KEEP_BROWSER"
IDLE_S = 120.0
WANT_POLL_S = 1.0
WANT_DIR = Path(tempfile.gettempdir()) / "video-keeper"
BACKGROUND_PAGE_MS = 5_000
PAGE_CLOSE_TIMEOUT_S = 10.0
# Above the 7 s the cookie notice's handler can take (overlays.READ_MS + PRESS_MS + GONE_MS).
NOTICE_SETTLE_S = 10.0

MIGRATED_ROOT = "https://flow.google.com/"
# An account with no projects renders only this button, inside the signed-in projects page (measured 2026-09-28).
NEW_PROJECT_BUTTON = "flow-projects-page button.new-project-button"
GRID_READY = f'a[href*="/project/"], {NEW_PROJECT_BUTTON}'
PROJECT_READY = "flow-project-page"
NOT_FOUND = "flow.google.com/404"
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
        self.profile_name = profile
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
        await _take_guard()
        try:
            # Holding the guard, any kept browser of this process is idle: a second one cannot launch on its profile.
            await _close_kept(self.profile_dir)
            announce(self.profile_dir)
            await self._open()
            await self._begin_call()
        except BaseException:
            await self._teardown()
            raise
        return self

    async def _open(self) -> None:
        """Launch the browser on the profile; no page of a call yet."""
        offscreen.install()
        bodies.install()
        extensions.install()
        self.client = self._factory(self.profile_dir, headless=self.headless)
        await self.client.__aenter__()
        self._entered = True

    async def _begin_call(self) -> None:
        """A fresh page for one call, with its own record of the notices it met: nothing of an earlier call's page
        (composer state, listeners, routes) reaches this one."""
        self.notices, self.unpressed = [], []
        self.page = await self._background_page()
        self._notice_idle = await overlays.watch_cookie_notice(self.page, self.notices, self.unpressed)

    async def _background_page(self) -> Any:
        """A new tab that does not take the keyboard focus. Measured 2026-11-12: `context.new_page()` brought Chrome to
        the front, a target created through DevTools with background=true did not, stayed `visible` and read the
        same. Any failure falls back to the plain new page."""
        context = self.client._context
        try:
            anchor = context.pages[0]
            cdp = await context.new_cdp_session(anchor)
            try:
                async with context.expect_page(timeout=BACKGROUND_PAGE_MS) as made:
                    await cdp.send("Target.createTarget", {"url": "about:blank", "background": True})
                return await made.value
            finally:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(cdp.detach(), 1.0)
        except Exception:  # noqa: BLE001
            return await context.new_page()

    async def _end_call(self) -> None:
        """Close the call's page; raises when it would not close, so the keeper drops the browser."""
        await self.notices_settled()
        page, self.page = self.page, None
        if page is not None:
            await asyncio.wait_for(page.close(), PAGE_CLOSE_TIMEOUT_S)

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
            await self._close()
        finally:
            _GUARD.release()

    async def _close(self) -> None:
        try:
            if self.page is not None:
                # Bounded, and never raised: a close that never returns would keep the lease and the guard
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

    async def goto(self, url: str, *, ready: str | None = None, timeout_ms: int = 60_000) -> None:
        await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if ready:
            try:
                await self.page.locator(ready).first.wait_for(state="visible", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                self._not_found(url)
                raise
        self._not_found(url)
        # Every Flow page names the build the ledger rows and paid answers then carry: read on each navigation, so a
        # server living across a Flow deploy does not stamp the old build (technical review of plan AQ, S1).
        await version.read(self.page)
        await overlays.keep_oldest_creations(self.page, self.notices, self.unpressed)

    def _not_found(self, url: str) -> None:
        # Measured 2026-11-09: a project of another account, or one deleted, sends the page to /404?reason=project.
        if NOT_FOUND in str(getattr(self.page, "url", "")):
            raise LookupError(
                f"Flow says this was not found ({self.page.url}) when opening {url}: the project is not on this "
                f"account (profile {self.profile_name!r}) or was deleted; flow_projects lists the ones that are"
            )

    @staticmethod
    def project_url(project_id: str) -> str:
        return f"{MIGRATED_ROOT}project/{project_id}"


async def _take_guard() -> None:
    # Never `to_thread(_GUARD.acquire)`: that thread outlives a cancelled caller, takes the lock later and
    # never gives it back, which hung every later browser tool (review 2026-09-15). A poll holds nothing.
    while not _GUARD.acquire(blocking=False):
        await asyncio.sleep(_GUARD_POLL_S)


def _want_file(profile_dir: Path) -> Path:
    return WANT_DIR / f"{Path(profile_dir).name}.want"


def announce(profile_dir: Path, pid: int | None = None) -> None:
    """Say this process is about to launch on the profile, so a browser another process keeps idle on it yields."""
    with contextlib.suppress(OSError):
        WANT_DIR.mkdir(parents=True, exist_ok=True)
        _want_file(profile_dir).write_text(str(os.getpid() if pid is None else pid), encoding="utf-8")


def _wanted_by_another(profile_dir: Path, since: float) -> bool:
    try:
        file = _want_file(profile_dir)
        return file.stat().st_mtime > since and file.read_text(encoding="utf-8").strip() != str(os.getpid())
    except OSError:
        return False


_KEPT: dict[str, BrowserKeeper] = {}


async def _close_kept(profile_dir: Path) -> None:
    """Close this process's kept browser on the profile; the caller holds the guard."""
    keeper = _KEPT.get(str(profile_dir))
    if keeper is not None:
        await keeper._drop()


async def release_kept(profile_dir: Path) -> None:
    """Close this process's kept browser on the profile, waiting out a call that is using it."""
    if str(profile_dir) not in _KEPT:
        return
    await _take_guard()
    try:
        await _close_kept(profile_dir)
    finally:
        _GUARD.release()


class BrowserKeeper:
    """One browser on a profile kept open between the calls of one process (plan AX, 2026-11-12: a launch took
    7.5-8.7 s and the keyboard focus on every call). Each call gets a fresh page; a call that fails, an idle period,
    another process asking for the profile, or a one-shot session of this process closes the browser, which gives
    gflow's profile lease back. The module guard is held only while a call runs."""

    def __init__(
        self,
        profile: str,
        *,
        idle_s: float | None = None,
        session_factory: Callable[[str], Any] | None = None,
        profile_dir: Path | None = None,
    ) -> None:
        self.profile = profile
        self.idle_s = IDLE_S if idle_s is None else idle_s
        self._factory = session_factory or FlowSession
        self._profile_dir = profile_dir
        self._session: Any = None
        self._watch: asyncio.Task[None] | None = None
        self._used = 0.0
        self._used_wall = 0.0

    @property
    def profile_dir(self) -> Path:
        if self._profile_dir is None:
            self._profile_dir = profile_dir_for(self.profile)
        return self._profile_dir

    @property
    def open(self) -> bool:
        return self._session is not None

    async def run(self, fn: Callable[[Any], Awaitable[Any]]) -> Any:
        await _take_guard()
        try:
            self._stop_watch()
            if self._session is None:
                announce(self.profile_dir)
                session = self._factory(self.profile)
                try:
                    await session._open()
                except BaseException:
                    await session._close()
                    raise
                self._session = session
                _KEPT[str(self.profile_dir)] = self
            session = self._session
            try:
                await session._begin_call()
                result = await fn(session)
            except BaseException:
                # The browser is in doubt after any failure: it is never handed to the next call.
                await self._drop()
                raise
            try:
                await session._end_call()
            except Exception:  # noqa: BLE001
                await self._drop()
            else:
                self._used, self._used_wall = time.monotonic(), time.time()
                self._watch = asyncio.ensure_future(self._idle())
            return result
        finally:
            _GUARD.release()

    async def close(self) -> None:
        await _take_guard()
        try:
            await self._drop()
        finally:
            _GUARD.release()

    def _stop_watch(self) -> None:
        watch, self._watch = self._watch, None
        if watch is not None and watch is not asyncio.current_task():
            watch.cancel()

    async def _drop(self) -> None:
        """Close the browser; the caller holds the guard."""
        self._stop_watch()
        session, self._session = self._session, None
        _KEPT.pop(str(self._profile_dir), None)
        if session is not None:
            await session._close()

    async def _idle(self) -> None:
        while self._session is not None:
            await asyncio.sleep(WANT_POLL_S)
            idle = time.monotonic() - self._used >= self.idle_s
            if not idle and not _wanted_by_another(self.profile_dir, self._used_wall):
                continue
            if not _GUARD.acquire(blocking=False):
                continue
            try:
                await self._drop()
            finally:
                _GUARD.release()
            return
