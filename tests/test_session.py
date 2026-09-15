import asyncio
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
