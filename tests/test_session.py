import asyncio
import logging
import os
import threading
import time
from typing import ClassVar

import pytest
from gflow_cli.errors import ProfileLockedError

from video import session as session_mod
from video.flow import overlays
from video.session import FlowSession


class FakeLocator:
    def __init__(self, selector):
        self.selector = selector


class FakePage:
    def __init__(self, log):
        self.log = log
        self.handlers = []

    def locator(self, selector):
        return FakeLocator(selector)

    async def add_locator_handler(self, locator, handler, **kwargs):
        self.handlers.append((locator.selector, handler))

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


def test_a_session_watches_its_page_for_flows_cookie_notice_and_for_nothing_else(tmp_path):
    """2026-10-01: the notice covered the composer's bottom row and every click there timed out. The owner lets the
    driver press that one notice's own button; no other overlay is ever handed to a handler."""

    async def run():
        async with FlowSession(profile_dir=tmp_path, client_factory=factory([])) as session:
            return [selector for selector, _ in session.page.handlers], session.notices, session.unpressed

    watched, notices, unpressed = asyncio.run(run())

    assert watched == [overlays.COOKIE_BAR]
    # What it pressed and what it could not press are kept apart: the second is a reason a later click may fail.
    assert notices == [] and unpressed == []


class _SlowNotice:
    """Flow's cookie notice, whose words are read only once `gate` opens: a press caught in flight."""

    def __init__(self):
        self.gate = asyncio.Event()
        self.pressed = False

    async def inner_text(self, timeout=None):
        await self.gate.wait()
        return "uses cookies OK, got it"

    def locator(self, selector):
        return self

    async def count(self):
        return 1

    async def click(self, timeout=None):
        self.pressed = True

    async def is_visible(self):
        return not self.pressed


def test_a_session_waits_out_a_press_of_the_notice_before_what_it_left_is_read(tmp_path):
    # Scoped re-review of plan AL, 2026-10-02: the action that met the notice can time out while the handler is
    # still pressing, and the call that failed then read the session's lists before the handler wrote to them.
    async def run():
        async with FlowSession(profile_dir=tmp_path, client_factory=factory([])) as session:
            await asyncio.wait_for(session.notices_settled(), 1)
            _, handler = session.page.handlers[0]
            notice = _SlowNotice()
            press = asyncio.create_task(handler(notice))
            await asyncio.sleep(0)
            settled = asyncio.create_task(session.notices_settled())
            await asyncio.sleep(0.02)
            early = settled.done(), list(session.notices)
            notice.gate.set()
            await asyncio.wait_for(settled, 1)
            late = list(session.notices)
            await press
            return early, late

    early, late = asyncio.run(run())

    assert early == (False, []) and late == ["uses cookies OK, got it"]


def test_a_press_that_never_ends_cannot_hold_the_session_for_good(tmp_path, monkeypatch):
    # The wait sits before the teardown, which gives back gflow's profile lease and the session guard: unbounded,
    # one wedged press would lock every later tool out.
    monkeypatch.setattr(session_mod, "NOTICE_SETTLE_S", 0.05)

    async def run():
        async with FlowSession(profile_dir=tmp_path, client_factory=factory([])) as session:
            _, handler = session.page.handlers[0]
            press = asyncio.create_task(handler(_SlowNotice()))
            await asyncio.sleep(0)
            await asyncio.wait_for(session.notices_settled(), 1)
            press.cancel()
            return list(session.notices)

    assert asyncio.run(run()) == []


def test_the_wait_for_a_press_outlasts_the_longest_the_handler_can_take():
    longest_ms = overlays.READ_MS + overlays.PRESS_MS + overlays.GONE_MS
    assert session_mod.NOTICE_SETTLE_S * 1000 > longest_ms


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


def test_the_first_navigation_reads_the_flow_build_once_for_the_process(monkeypatch, tmp_path):
    # Plan AQ (I-drift-1): the build every ledger row and paid answer carries comes off the first Flow page.
    from video.flow import version

    monkeypatch.setattr(version, "current", None)

    class _Locator:
        first = None

        async def wait_for(self, **kwargs):
            return None

    class _Page:
        def __init__(self):
            self.evaluated = 0
            locator = _Locator()
            locator.first = locator
            self._locator = locator

        async def goto(self, url, **kwargs):
            return None

        def locator(self, selector):
            return self._locator

        async def evaluate(self, js):
            self.evaluated += 1
            return ["https://g/k=boq-labs-ai-sandbox.AiSandboxAngularFrontend.en.Zz9.1.O/"]

    session = FlowSession(profile_dir=tmp_path)
    session.page = _Page()

    asyncio.run(session.goto("https://flow.google.com/", ready="flow-projects-page"))
    asyncio.run(session.goto("https://flow.google.com/project/x"))

    assert version.current == "Zz9.1.O"
    # Every navigation, one evaluate each: a server living across a Flow deploy would otherwise stamp the old build
    # on every row and answer until a flow_check happened to run (technical review of plan AQ, S1).
    assert session.page.evaluated == 2, "read on every navigation, so a new build is seen when it lands"


def test_every_navigation_opts_in_through_flows_expiry_banner(monkeypatch, tmp_path):
    """Plan AV, I6: the owner signs in and uses the MCP; a banner asking to keep the oldest creations is opted in
    through by the driver itself (DECISIONS 2026-11-09), right after a page loads and before any other action."""
    from video.flow import overlays, version

    monkeypatch.setattr(version, "current", None)
    seen = []

    async def keep(page, notices, unpressed):
        seen.append((page, notices, unpressed))
        return True

    monkeypatch.setattr(overlays, "keep_oldest_creations", keep)

    class _Locator:
        async def wait_for(self, **kwargs):
            return None

        @property
        def first(self):
            return self

    class _Page:
        async def goto(self, url, **kwargs):
            seen.append(url)

        def locator(self, selector):
            return _Locator()

        async def evaluate(self, js):
            return []

    session = FlowSession(profile_dir=tmp_path)
    session.page = _Page()

    asyncio.run(session.goto("https://flow.google.com/project/x", ready="flow-project-page"))

    assert seen[0] == "https://flow.google.com/project/x"
    assert seen[1] == (session.page, session.notices, session.unpressed)


@pytest.mark.parametrize("ready_times_out", [True, False])
def test_a_project_flow_answers_not_found_is_said_in_those_words(monkeypatch, tmp_path, ready_times_out):
    """Plan AW, D5, measured 2026-11-09: a project of another account sends the page to /404?reason=project,
    "Project not found."; the tools said "the sidebar never rendered" or timed out instead."""
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    from video.flow import overlays, version

    monkeypatch.setattr(version, "current", None)

    async def nothing(*args):
        return False

    monkeypatch.setattr(overlays, "keep_oldest_creations", nothing)

    class _Locator:
        @property
        def first(self):
            return self

        async def wait_for(self, **kwargs):
            if ready_times_out:
                raise PlaywrightTimeoutError("Timeout 60000ms exceeded")

    class _Page:
        url = "https://flow.google.com/404?reason=project"

        async def goto(self, url, **kwargs):
            return None

        def locator(self, selector):
            return _Locator()

        async def evaluate(self, js):
            return []

    session = FlowSession("someone", profile_dir=tmp_path)
    session.page = _Page()

    with pytest.raises(LookupError, match="not found") as refused:
        asyncio.run(session.goto("https://flow.google.com/project/P", ready="flow-project-page"))
    assert "this account" in str(refused.value), refused.value


# Plan AX: one browser kept between the calls of an MCP server, a fresh page per call.


class _KeptFake:
    """What BrowserKeeper drives: a session it opens once, begins and ends a call on, and closes."""

    made: ClassVar[list] = []

    def __init__(self, profile):
        self.profile = profile
        self.log = []
        self.fail_end = False
        _KeptFake.made.append(self)

    async def _open(self):
        self.log.append("open")

    async def _begin_call(self):
        self.log.append("begin")

    async def _end_call(self):
        self.log.append("end")
        if self.fail_end:
            raise RuntimeError("page would not close")

    async def _close(self):
        self.log.append("close")


@pytest.fixture
def kept(tmp_path, monkeypatch):
    from video import session as session_mod

    _KeptFake.made = []
    monkeypatch.setattr(session_mod, "WANT_DIR", tmp_path / "want")
    keeper = session_mod.BrowserKeeper(
        "p", idle_s=60.0, session_factory=_KeptFake, profile_dir=tmp_path / "p"
    )
    return session_mod, keeper


def test_five_calls_open_one_browser_and_five_pages(kept):
    _, keeper = kept

    async def go():
        async def read(session):
            return len(session.log)

        for _ in range(5):
            await keeper.run(read)
        await keeper.close()

    asyncio.run(go())

    assert len(_KeptFake.made) == 1
    assert _KeptFake.made[0].log == ["open", *["begin", "end"] * 5, "close"]


def test_a_call_that_fails_closes_the_browser_and_the_next_opens_a_new_one(kept):
    """I2: after an error the browser is in doubt, so it is not handed to the next call."""
    _, keeper = kept

    async def go():
        async def boom(session):
            raise LookupError("Flow said no")

        async def fine(session):
            return "ok"

        with pytest.raises(LookupError):
            await keeper.run(boom)
        assert await keeper.run(fine) == "ok"
        await keeper.close()

    asyncio.run(go())

    assert [fake.log for fake in _KeptFake.made] == [
        ["open", "begin", "close"],
        ["open", "begin", "end", "close"],
    ]


def test_a_page_that_will_not_close_costs_the_browser_not_the_answer(kept):
    _, keeper = kept

    async def go():
        async def fine(session):
            session.fail_end = True
            return "ok"

        assert await keeper.run(fine) == "ok"
        assert keeper.open is False

    asyncio.run(go())
    assert _KeptFake.made[0].log == ["open", "begin", "end", "close"]


def test_two_calls_never_share_the_browser_at_once(kept):
    """I3: the guard that kept two sessions apart keeps two calls on one browser apart."""
    _, keeper = kept
    inside = []

    async def go():
        async def slow(session):
            inside.append("in")
            assert inside.count("in") - inside.count("out") == 1, inside
            await asyncio.sleep(0.05)
            inside.append("out")

        await asyncio.gather(keeper.run(slow), keeper.run(slow), keeper.run(slow))
        await keeper.close()

    asyncio.run(go())
    assert inside == ["in", "out"] * 3


def test_an_idle_browser_is_closed_and_the_guard_is_free_between_calls(kept, monkeypatch):
    """I2: the lease goes back after the idle period, and nothing holds the guard while the browser idles."""
    session_mod, keeper = kept
    keeper.idle_s = 0.05
    monkeypatch.setattr(session_mod, "WANT_POLL_S", 0.01)

    async def go():
        async def fine(session):
            return "ok"

        await keeper.run(fine)
        assert session_mod._GUARD.acquire(blocking=False), "the guard is held while the browser idles"
        session_mod._GUARD.release()
        await asyncio.sleep(0.3)
        assert keeper.open is False

    asyncio.run(go())
    assert _KeptFake.made[0].log == ["open", "begin", "end", "close"]


def test_an_idle_browser_yields_at_once_when_another_process_wants_the_profile(kept, monkeypatch):
    """Several MCP servers share one account: a kept browser would lock the others out for the whole idle period."""
    session_mod, keeper = kept
    monkeypatch.setattr(session_mod, "WANT_POLL_S", 0.01)

    async def go():
        async def fine(session):
            return "ok"

        await keeper.run(fine)
        await asyncio.sleep(0.05)
        assert keeper.open is True, "its own announcement must not close it"
        session_mod.announce(keeper.profile_dir, pid=1)
        await asyncio.sleep(0.2)
        assert keeper.open is False

    asyncio.run(go())


def test_a_one_shot_session_closes_a_kept_browser_of_its_profile_first(kept, tmp_path):
    """A second browser on one profile cannot launch: gen's balance read and the CLI open their own session."""
    session_mod, keeper = kept

    async def go():
        async def fine(session):
            return "ok"

        await keeper.run(fine)
        await session_mod.release_kept(tmp_path / "p")
        assert keeper.open is False
        await session_mod.release_kept(tmp_path / "other")

    asyncio.run(go())
    assert _KeptFake.made[0].log == ["open", "begin", "end", "close"]


# Tier 1 review of plan AX.


def test_a_call_cancelled_while_its_page_closes_gives_the_browser_back(kept):
    """B2: a cancel landing in _end_call skipped the drop and the idle watch, so the browser and its lease stayed."""
    session_mod, keeper = kept

    async def go():
        async def fine(session):
            async def cancelled():
                raise asyncio.CancelledError

            session._end_call = cancelled
            return "ok"

        with pytest.raises(asyncio.CancelledError):
            await keeper.run(fine)
        assert keeper.open is False
        assert session_mod._GUARD.acquire(blocking=False)
        session_mod._GUARD.release()

    asyncio.run(go())
    assert _KeptFake.made[0].log[-1] == "close"


def test_a_want_announced_while_a_call_ran_is_honoured_when_the_call_ends(kept, monkeypatch):
    """B3: the want was compared with the END of the last call, so one announced during it was never seen."""
    session_mod, keeper = kept
    monkeypatch.setattr(session_mod, "WANT_POLL_S", 0.01)

    async def go():
        async def slow(session):
            session_mod.announce(keeper.profile_dir, pid=1)
            await asyncio.sleep(0.05)
            return "ok"

        await keeper.run(slow)
        await asyncio.sleep(0.2)
        assert keeper.open is False

    asyncio.run(go())


def test_a_want_of_another_process_is_not_lost_when_this_one_announces(kept, monkeypatch):
    session_mod, keeper = kept

    session_mod.announce(keeper.profile_dir, pid=1)
    session_mod.announce(keeper.profile_dir)

    assert session_mod._wanted_by_another(keeper.profile_dir)


def test_a_want_that_was_withdrawn_is_not_a_reason_to_close(kept, monkeypatch):
    session_mod, keeper = kept
    monkeypatch.setattr(session_mod, "WANT_POLL_S", 0.01)
    session_mod.announce(keeper.profile_dir, pid=1)
    session_mod.withdraw(keeper.profile_dir, pid=1)

    async def go():
        async def fine(session):
            return "ok"

        await keeper.run(fine)
        await asyncio.sleep(0.1)
        assert keeper.open is True
        await keeper.close()

    asyncio.run(go())


def test_a_process_that_asked_first_goes_first_when_the_browser_reopens(kept, monkeypatch):
    """Scoped re-review of plan AX, N-a: A yielded to B's want, then A's next call reopened before B had the lease,
    and B's want, older than A's new browser, was never seen again. A now waits for an earlier want to be withdrawn,
    which B does once it holds the lease."""
    session_mod, keeper = kept
    monkeypatch.setattr(session_mod, "WANT_POLL_S", 0.01)
    session_mod.announce(keeper.profile_dir, pid=1)
    # B asked a second earlier. Two announcements inside one tick of the filesystem's clock carry the same time
    # (seen on the Linux CI runner, 2026-11-12), and then neither waits and gflow's lease decides.
    earlier = time.time() - 1
    os.utime(session_mod._want_file(keeper.profile_dir, 1), (earlier, earlier))
    order = []

    async def go():
        async def fine(session):
            order.append("A runs")
            return "ok"

        async def b_gets_the_lease():
            await asyncio.sleep(0.1)
            order.append("B has the lease")
            session_mod.withdraw(keeper.profile_dir, pid=1)

        await asyncio.gather(keeper.run(fine), b_gets_the_lease())
        await keeper.close()

    asyncio.run(go())
    assert order == ["B has the lease", "A runs"]


def test_waiting_for_an_earlier_want_is_bounded(kept, monkeypatch):
    session_mod, keeper = kept
    monkeypatch.setattr(session_mod, "WANT_POLL_S", 0.01)
    monkeypatch.setattr(session_mod, "WANT_WAIT_S", 0.05)
    session_mod.announce(keeper.profile_dir, pid=1)

    async def go():
        async def fine(session):
            return "ok"

        assert await keeper.run(fine) == "ok"
        await keeper.close()

    asyncio.run(go())


def test_the_want_of_a_process_that_died_is_ignored_and_cleared(kept):
    session_mod, keeper = kept
    dead = 2_000_000_000
    session_mod.announce(keeper.profile_dir, pid=dead)

    assert session_mod._wanted_by_another(keeper.profile_dir) is False
    assert not session_mod._want_file(keeper.profile_dir, dead).exists()


def test_a_process_withdraws_its_want_once_its_browser_is_open(kept):
    import os

    session_mod, keeper = kept

    async def go():
        async def fine(session):
            assert not session_mod._want_file(keeper.profile_dir, os.getpid()).exists()
            return "ok"

        await keeper.run(fine)
        await keeper.close()

    asyncio.run(go())


def test_a_browser_that_will_not_close_never_replaces_the_calls_own_error(kept):
    """N1: the call's error can carry `charged=yes`; a close error must not stand in for it."""
    _, keeper = kept

    async def go():
        async def boom(session):
            async def broken():
                raise RuntimeError("the client exit failed")

            session._close = broken
            raise LookupError("outcome code=CHARGED_NO_OUTPUT charged=yes")

        with pytest.raises(LookupError, match="charged=yes"):
            await keeper.run(boom)
        assert keeper.open is False

    asyncio.run(go())


def test_a_kept_browser_that_died_between_calls_is_opened_again_before_the_call_runs(kept):
    """N2: the owner quit Chrome, or it crashed. Nothing of the call has run yet, so a fresh browser is safe."""
    _, keeper = kept
    ran = []

    async def go():
        async def fine(session):
            ran.append(session)
            return "ok"

        await keeper.run(fine)

        async def dead():
            raise RuntimeError("Target page, context or browser has been closed")

        _KeptFake.made[0]._begin_call = dead
        assert await keeper.run(fine) == "ok"
        await keeper.close()

    asyncio.run(go())
    assert len(_KeptFake.made) == 2 and ran == _KeptFake.made
    assert _KeptFake.made[0].log[-1] == "close"


class _RealPage:
    def __init__(self, log, name):
        self.log, self.name = log, name

    async def close(self):
        self.log.append(f"close {self.name}")

    async def add_locator_handler(self, *args, **kwargs):
        return None

    def locator(self, selector):
        return selector


class _RealClient:
    def __init__(self, log):
        self.log = log
        made = iter(range(1, 99))
        client = self

        class _Context:
            async def new_page(self):
                page = _RealPage(log, f"page-{next(made)}")
                log.append(f"new {page.name}")
                return page

        self._context = _Context()
        del client

    async def __aenter__(self):
        self.log.append("launch")

    async def __aexit__(self, *exc):
        self.log.append("exit")


def test_each_call_of_a_real_session_gets_its_own_page_and_its_own_notices(tmp_path, monkeypatch):
    """Review gaps M1 and M11: the keeper tests drive a fake, so the real begin and end of a call are held here."""
    from video import session as session_mod

    log = []
    monkeypatch.setattr(session_mod.offscreen, "install", lambda: None)
    monkeypatch.setattr(session_mod.bodies, "install", lambda: None)
    monkeypatch.setattr(session_mod.extensions, "install", lambda: None)
    session = FlowSession("p", profile_dir=tmp_path, client_factory=lambda *a, **k: _RealClient(log))

    async def go():
        await session._open()
        await session._begin_call()
        first = session.page
        session.notices.append("pressed in call one")
        session.unpressed.append("left in call one")
        await session._end_call()
        assert session.page is None
        await session._begin_call()
        assert session.page is not first
        assert session.notices == [] and session.unpressed == []
        await session._end_call()
        await session._close()

    asyncio.run(go())
    assert log == ["launch", "new page-1", "close page-1", "new page-2", "close page-2", "exit"]


def test_a_one_shot_session_closes_the_kept_browser_itself_when_it_opens(kept, tmp_path, monkeypatch):
    """Review gap M5: the one-shot session's own __aenter__ is what closes it; release_kept alone was tested."""
    session_mod, keeper = kept
    log = []
    monkeypatch.setattr(session_mod.offscreen, "install", lambda: None)
    monkeypatch.setattr(session_mod.bodies, "install", lambda: None)
    monkeypatch.setattr(session_mod.extensions, "install", lambda: None)

    async def go():
        async def fine(session):
            return "ok"

        await keeper.run(fine)
        one_shot = FlowSession(
            "p", profile_dir=tmp_path / "p", client_factory=lambda *a, **k: _RealClient(log)
        )
        async with one_shot:
            assert keeper.open is False

    asyncio.run(go())
    assert _KeptFake.made[0].log[-1] == "close" and log[0] == "launch"


def test_a_want_older_than_any_lease_wait_is_treated_as_dead(kept):
    """Last scoped pass of plan AX, N-1: a pid reused by an unrelated process kept its want alive for good, and every
    call then relaunched after waiting. A process waits at most 180 s for the lease, so an older want is nobody's."""
    import os

    session_mod, keeper = kept
    session_mod.announce(keeper.profile_dir, pid=1)
    file = session_mod._want_file(keeper.profile_dir, 1)
    old = file.stat().st_mtime - session_mod.WANT_MAX_AGE_S - 5
    os.utime(file, (old, old))

    assert session_mod._wanted_by_another(keeper.profile_dir) is False
    assert not file.exists()


@pytest.mark.parametrize("name", ["p.99999999999999999999.want", "p.0.want", "p.x.want"])
def test_a_want_file_with_no_usable_pid_never_breaks_the_watch(kept, name):
    """N-2: an absurd pid raised OverflowError inside the idle watch, which then died and kept the browser."""
    session_mod, keeper = kept
    session_mod.WANT_DIR.mkdir(parents=True, exist_ok=True)
    (session_mod.WANT_DIR / name).touch()

    assert session_mod._wanted_by_another(keeper.profile_dir) is False
