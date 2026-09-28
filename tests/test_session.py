import asyncio
import logging
import threading

import pytest
from gflow_cli.errors import ProfileLockedError

from video import session as session_mod
from video.session import FlowSession


class FakePage:
    def __init__(self, log):
        self.log = log

    async def close(self):
        self.log.append("page.close")


class FakeContext:
    def __init__(self, log):
        self.log = log

    async def new_page(self):
        self.log.append("new_page")
        return FakePage(self.log)


class FakeClient:
    def __init__(self, log, *, fail=None):
        self.log = log
        self.fail = fail
        self._context = FakeContext(log)

    async def __aenter__(self):
        if self.fail is not None:
            raise self.fail
        self.log.append("client.enter")
        return self

    async def __aexit__(self, *exc):
        self.log.append("client.exit")


def factory(log, fail=None):
    def make(profile_dir, *, headless):
        return FakeClient(log, fail=fail)

    return make


def test_enter_opens_client_then_page_and_exit_closes_in_reverse(tmp_path):
    log = []

    async def run():
        async with FlowSession(profile_dir=tmp_path, client_factory=factory(log)) as session:
            assert session.page is not None

    asyncio.run(run())
    assert log == ["client.enter", "new_page", "page.close", "client.exit"]


def test_second_session_waits_until_first_exits(tmp_path):
    log = []

    async def run():
        entered = asyncio.Event()

        async def second():
            async with FlowSession(profile_dir=tmp_path, client_factory=factory(log)):
                entered.set()

        async with FlowSession(profile_dir=tmp_path, client_factory=factory(log)):
            task = asyncio.create_task(second())
            await asyncio.sleep(0.1)
            assert not entered.is_set()
        await asyncio.wait_for(task, 2)
        assert entered.is_set()

    asyncio.run(run())
    assert log.count("client.enter") == 2


async def free_guard_left_behind(guard):
    """On the old code a cancelled waiter's thread keeps the lock: free it, or threads blocked on it outlive the test."""
    while guard.locked():
        guard.release()
        await asyncio.sleep(0.05)


def test_a_session_cancelled_while_it_waits_never_keeps_the_guard(tmp_path, monkeypatch):
    # Review 2026-09-15: `await asyncio.to_thread(_GUARD.acquire)` sat before the try, so cancelling a waiter left its
    # thread blocked on the lock; it took the lock once the holder let go, and nothing ever released it again.
    guard = threading.Lock()
    monkeypatch.setattr(session_mod, "_GUARD", guard)
    log = []

    async def run():
        release = asyncio.Event()

        async def hold():
            async with FlowSession(profile_dir=tmp_path, client_factory=factory(log)):
                await release.wait()

        async def third():
            async with FlowSession(profile_dir=tmp_path, client_factory=factory(log)):
                return "entered"

        holder = asyncio.create_task(hold())
        await asyncio.sleep(0.1)
        waiter = asyncio.create_task(
            FlowSession(profile_dir=tmp_path, client_factory=factory(log)).__aenter__()
        )
        await asyncio.sleep(0.1)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        await holder
        try:
            return await asyncio.wait_for(third(), 2)
        finally:
            await free_guard_left_behind(guard)

    assert asyncio.run(run()) == "entered"


class ClosedTargetPage(FakePage):
    async def close(self):
        self.log.append("page.close")
        raise RuntimeError("Target page, context or browser has been closed")


class DriverGonePage(FakePage):
    async def close(self):
        self.log.append("page.close")
        # Playwright raises a bare Exception once its driver connection is gone.
        raise Exception("Connection closed while reading from the driver")  # noqa: TRY002


class ChattyClosePage(FakePage):
    async def close(self):
        self.log.append("page.close")
        # Playwright appends a call log to its errors, and a call log can quote request headers.
        raise RuntimeError(
            "Target page, context or browser has been closed\nCall log:\n  - cookie: SAPISID=abc123"
        )


class LeasedClient(FakeClient):
    """gflow's in-process profile lease: until a client exits, the next one cannot enter."""

    def __init__(self, log, lease, page_cls):
        super().__init__(log)
        self.lease = lease
        self.page_cls = page_cls
        self._context = self

    async def new_page(self):
        self.log.append("new_page")
        return self.page_cls(self.log)

    async def __aenter__(self):
        if self.lease["held"]:
            raise ProfileLockedError("already held in this process")
        self.lease["held"] = True
        self.log.append("client.enter")
        return self

    async def __aexit__(self, *exc):
        self.lease["held"] = False
        self.log.append("client.exit")


def leased(log, lease, page_cls):
    def make(profile_dir, *, headless):
        return LeasedClient(log, lease, page_cls)

    return make


def test_a_page_that_fails_to_close_still_gives_the_profile_back_and_raises_nothing(tmp_path, caplog):
    # Review 2026-09-15: a failed or cancelled page.close() skipped client.__aexit__, the step that returns gflow's
    # profile lease, so every later session failed with ProfileLockedError until the server restarted. Plan D (DECISIONS
    # 2026-09-15): the close error is logged, never raised, since the client exit closes the browser anyway.
    log = []
    lease = {"held": False}

    async def run():
        async with FlowSession(profile_dir=tmp_path, client_factory=leased(log, lease, ClosedTargetPage)):
            pass
        async with FlowSession(profile_dir=tmp_path, client_factory=leased(log, lease, FakePage)):
            pass

    with caplog.at_level(logging.WARNING, logger="video.session"):
        asyncio.run(run())
    assert log.count("client.exit") == 2
    assert "Target page, context or browser has been closed" in caplog.text
    assert not session_mod._GUARD.locked()


def test_a_call_that_failed_keeps_its_own_error_when_the_page_fails_to_close_too(tmp_path, caplog):
    # Review of plan B (HANDOFF ngã rẽ 4): a dead driver makes page.close() raise as well, and that error replaced the
    # call's own one, such as a driver error saying credits were spent.
    log = []
    lease = {"held": False}

    async def run():
        with pytest.raises(ValueError, match="spent 20 credits"):
            async with FlowSession(profile_dir=tmp_path, client_factory=leased(log, lease, DriverGonePage)):
                raise ValueError("edit: nothing was generated within 240s, spent 20 credits")
        async with FlowSession(profile_dir=tmp_path, client_factory=leased(log, lease, FakePage)):
            pass

    with caplog.at_level(logging.WARNING, logger="video.session"):
        asyncio.run(run())
    assert log.count("client.exit") == 2
    assert "Connection closed while reading from the driver" in caplog.text
    assert not session_mod._GUARD.locked()


def test_a_close_error_is_logged_without_its_call_log_or_session_material(tmp_path, caplog):
    # Review of plan D (2026-09-16, finding 6): TellingServer cuts "Call log:" and scrubs before logging; this warning did
    # neither, so a call log quoting a request header would reach the log raw.
    log = []
    lease = {"held": False}

    async def run():
        async with FlowSession(profile_dir=tmp_path, client_factory=leased(log, lease, ChattyClosePage)):
            pass

    with caplog.at_level(logging.WARNING, logger="video.session"):
        asyncio.run(run())
    assert "Target page" in caplog.text
    assert "SAPISID=" not in caplog.text and "Call log:" not in caplog.text
    assert log.count("client.exit") == 1


class HangingPage(FakePage):
    async def close(self):
        self.log.append("page.close")
        await asyncio.Event().wait()


def test_a_page_close_that_never_returns_still_gives_the_profile_and_the_guard_back(tmp_path, monkeypatch):
    # Review 2026-09-15 risk: teardown awaited page.close() with no bound, so a close that never returned kept gflow's
    # profile lease and the session guard, and every later browser tool waited forever.
    # raising=False so the old code fails by hanging, not by lacking the name; a renamed bound still hangs past 5 s.
    monkeypatch.setattr(session_mod, "PAGE_CLOSE_TIMEOUT_S", 0.2, raising=False)
    log = []
    lease = {"held": False}

    async def run():
        # Review of plan B: the bound's own TimeoutError replaced what the call inside had returned or raised, such as
        # a driver error saying credits were spent. The client exit closes the browser anyway, so the call keeps its
        # own outcome.
        async with FlowSession(profile_dir=tmp_path, client_factory=leased(log, lease, HangingPage)):
            pass
        with pytest.raises(ValueError, match="spent 20 credits"):
            async with FlowSession(profile_dir=tmp_path, client_factory=leased(log, lease, HangingPage)):
                raise ValueError("edit: nothing was generated within 240s, spent 20 credits")
        async with FlowSession(profile_dir=tmp_path, client_factory=leased(log, lease, FakePage)):
            pass

    asyncio.run(asyncio.wait_for(run(), 5))
    assert log.count("client.exit") == 3
    assert not session_mod._GUARD.locked()


def test_a_call_cancelled_while_its_page_close_hangs_is_not_swallowed(tmp_path, monkeypatch):
    # Re-review 2026-09-15: only the close's own timeout is quiet; a cancellation arriving during the close must still
    # end the call, after the client exit and the guard release.
    monkeypatch.setattr(session_mod, "PAGE_CLOSE_TIMEOUT_S", 10.0, raising=False)
    log = []
    lease = {"held": False}

    async def hold():
        async with FlowSession(profile_dir=tmp_path, client_factory=leased(log, lease, HangingPage)):
            pass
        return "finished"

    async def run():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(hold(), 0.2)

    asyncio.run(asyncio.wait_for(run(), 5))
    assert log.count("client.exit") == 1
    assert not session_mod._GUARD.locked()


def test_locked_profile_error_propagates_and_guard_is_released(tmp_path):
    log = []

    async def run():
        with pytest.raises(ProfileLockedError):
            async with FlowSession(
                profile_dir=tmp_path, client_factory=factory(log, fail=ProfileLockedError("held"))
            ):
                pass
        async with FlowSession(profile_dir=tmp_path, client_factory=factory(log)):
            pass

    asyncio.run(run())
    assert log == ["client.enter", "new_page", "page.close", "client.exit"]


def test_the_home_page_is_ready_on_an_account_with_no_projects():
    """Measured 2026-09-28: an account with no projects renders `button.new-project-button` inside
    `flow-projects-page` and no project link, so a readiness that waits for a project link times out after 60 s on
    every home read, including the one before the click that would create the first project."""
    selectors = [part.strip() for part in session_mod.GRID_READY.split(",")]

    assert 'a[href*="/project/"]' in selectors, "an account with projects must still be ready on its links"
    assert session_mod.NEW_PROJECT_BUTTON in selectors, selectors
    # Scoped to the signed-in projects page, so no other page's button can pass for a signed-in grid.
    assert session_mod.NEW_PROJECT_BUTTON == "flow-projects-page button.new-project-button"
