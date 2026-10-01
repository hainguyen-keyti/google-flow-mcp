"""Flow lays things over its own page. The driver may press ONE of them, the cookie notice's own button (the owner's
decision of 2026-10-01); anything else that covers a control is named and never clicked."""

import asyncio

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import overlays

NOTICE = (
    "flow.google.com uses cookies from Google to deliver and enhance the quality of its services "
    "and to analyze traffic. Learn more OK, got it"
)
# What most of the driver's actions allow themselves (`timeout=8_000`): the action that meets the notice waits for the
# handler under that deadline.
ACTION_MS = 8_000


class _Button:
    def __init__(self, page, selector, hits):
        self.page, self.selector, self.hits = page, selector, hits

    async def count(self):
        return self.hits

    async def click(self, timeout=None, **_):
        self.page.click_timeouts.append(timeout)
        if self.page.idle is not None:
            self.page.idle_during_press.append(self.page.idle.is_set())
        # Playwright's own error class, which is no subclass of the builtin one (scoped re-review, 2026-10-02).
        if self.page.press_fails:
            raise PlaywrightTimeoutError("Locator.click: Timeout 3000ms exceeded.")
        self.page.clicks.append(self.selector)
        self.page.steps.append("press")


class _Bar:
    def __init__(self, page, text, accepts):
        self.page, self.selector, self.text, self.accepts = page, overlays.COOKIE_BAR, text, accepts

    async def inner_text(self, timeout=None):
        self.page.read_timeouts.append(timeout)
        if self.page.idle is not None:
            self.page.idle_during_read.append(self.page.idle.is_set())
        if self.page.unreadable:
            raise PlaywrightTimeoutError("Locator.inner_text: Timeout 1000ms exceeded.")
        return self.text

    def locator(self, selector):
        return _Button(self.page, selector, self.accepts if selector == overlays.COOKIE_ACCEPT else 1)

    async def is_visible(self):
        if self.page.closes:
            raise PlaywrightError("Target page, context or browser has been closed")
        shows = self.page.stays or not self.page.clicks or self.page.waited < self.page.gone_after_ms
        if not shows:
            self.page.steps.append("seen gone")
        return shows

    async def wait_for(self, state=None, timeout=None):
        # Locator.wait_for runs Playwright's action pre-checks (coreBundle.js:22812-22813, 53422), which wait on this
        # very handler once the action that met the notice has run out of time.
        self.page.steps.append("wait_for")
        raise PlaywrightTimeoutError("Locator.wait_for: Timeout exceeded.")


class _Notices(list):
    """The list the handler writes to, noting WHEN a notice was recorded among the page's steps."""

    def __init__(self, page):
        super().__init__()
        self.page = page

    def append(self, text):
        self.page.steps.append("record")
        super().append(text)


class _Page:
    """A page whose only overlay handling is what the code under test registers."""

    def __init__(
        self,
        text=NOTICE,
        accepts=1,
        covering=None,
        press_fails=False,
        stays=False,
        unreadable=False,
        closes=False,
        gone_after_ms=0,
    ):
        self.clicks, self.handlers, self.asked, self.steps = [], [], [], []
        self.click_timeouts, self.read_timeouts = [], []
        self.idle_during_press, self.idle_during_read = [], []
        self.bar = _Bar(self, text, accepts)
        self.cover = covering
        self.press_fails, self.stays, self.unreadable, self.closes = press_fails, stays, unreadable, closes
        self.gone_after_ms, self.waited, self.idle = gone_after_ms, 0, None

    def locator(self, selector):
        self.asked.append(selector)
        return self.bar if selector == overlays.COOKIE_BAR else _Button(self, selector, 1)

    async def add_locator_handler(self, locator, handler, **kwargs):
        self.handlers.append((locator.selector, handler, kwargs))

    async def wait_for_timeout(self, ms):
        self.waited += ms

    async def evaluate(self, js, *args):
        self.asked.append(("evaluate", *args))
        return self.cover


def _watched(**page_options):
    page = _Page(**page_options)
    notices, unpressed = _Notices(page), []
    page.idle = asyncio.run(overlays.watch_cookie_notice(page, notices, unpressed))
    return page, notices, unpressed


def test_only_flows_cookie_notice_is_handed_to_the_overlay_handler():
    page, notices, unpressed = _watched()

    assert [selector for selector, _, _ in page.handlers] == [overlays.COOKIE_BAR]
    assert page.clicks == [] and notices == [] and unpressed == [], "registering must click nothing"


def test_a_notice_that_cannot_be_pressed_never_holds_an_action_to_its_deadline():
    # Review of plan AL, 2026-10-02: Playwright runs a handler in a task nobody awaits and then, by default, waits
    # for the overlay to hide under the action's own deadline (playwright/_impl/_page.py:219-223, 1422-1458). A notice
    # the handler left standing turned every call, free reads included, into a bare 60 s timeout.
    page, _, _ = _watched()

    assert page.handlers[0][2] == {"no_wait_after": True}


def test_the_handler_presses_the_notices_own_button_once_and_keeps_what_it_said():
    page, notices, unpressed = _watched()
    _, handler, _ = page.handlers[0]

    asyncio.run(handler(page.bar))

    assert page.clicks == [overlays.COOKIE_ACCEPT]
    assert notices == [NOTICE] and unpressed == []
    # It is recorded as dismissed only once the press went through and the notice left the page.
    assert page.steps == ["press", "seen gone", "record"]


def test_the_notice_is_watched_until_it_leaves_without_an_action_check():
    # Scoped re-review of plan AL, 2026-10-02: the action that met the notice can run out of time while the handler is
    # still at work; Locator.wait_for then waits on the handler it is called from, until its own timeout, and a
    # notice that did leave was recorded as one that stayed. A plain look at the bar waits on nothing.
    page, notices, unpressed = _watched(gone_after_ms=300)
    _, handler, _ = page.handlers[0]

    asyncio.run(handler(page.bar))

    assert "wait_for" not in page.steps
    assert notices == [NOTICE] and unpressed == []
    # Looked at often enough that a bar gone in 0.3 s gives the action back within half a second: measured
    # 2026-10-02, the real bar is gone in the same tick as the click.
    assert 300 <= page.waited <= 500


def test_the_handler_gives_the_action_back_inside_the_eight_seconds_most_actions_allow():
    # The action that met the notice waits for the handler under its OWN deadline (coreBundle.js:21719-21725): a
    # handler slower than that fails the action even when the control it wants is not under the notice.
    page, _, _ = _watched(stays=True)
    _, handler, _ = page.handlers[0]

    asyncio.run(handler(page.bar))

    assert page.read_timeouts == [overlays.READ_MS] and page.click_timeouts == [overlays.PRESS_MS]
    assert page.waited == overlays.GONE_MS
    assert overlays.READ_MS + overlays.PRESS_MS + overlays.GONE_MS < ACTION_MS


@pytest.mark.parametrize("accepts", [0, 2])
def test_a_notice_without_exactly_one_accept_button_is_named_and_left_alone(accepts):
    page, notices, unpressed = _watched(accepts=accepts)
    _, handler, _ = page.handlers[0]

    # The handler must not raise: nobody would hear it.
    asyncio.run(handler(page.bar))

    assert page.clicks == [] and notices == []
    assert (
        len(unpressed) == 1 and "uses cookies" in unpressed[0] and f"{accepts} accept buttons" in unpressed[0]
    )


@pytest.mark.parametrize(
    ("failure", "said"),
    [("press_fails", "its button was not pressed"), ("stays", "of its button being pressed")],
)
def test_a_notice_that_did_not_go_away_is_never_recorded_as_dismissed(failure, said):
    page, notices, unpressed = _watched(**{failure: True})
    _, handler, _ = page.handlers[0]

    asyncio.run(handler(page.bar))

    assert notices == []
    assert len(unpressed) == 1 and "did not go away" in unpressed[0] and "uses cookies" in unpressed[0]
    # Whether the button was pressed is said: a press that changed nothing is still a click on the owner's page.
    assert said in unpressed[0], unpressed


@pytest.mark.parametrize(
    ("failure", "said"),
    [
        ("unreadable", "(TimeoutError, its button was not pressed)"),
        ("press_fails", "(TimeoutError, its button was not pressed)"),
        ("closes", "(Error, after its button was pressed)"),
    ],
)
def test_the_handler_never_raises_whatever_the_page_does(failure, said):
    # Playwright runs the handler in a task nobody awaits: an error raised here reaches no caller. Its own errors
    # are playwright.async_api.Error, of which the timeout is one and the builtin TimeoutError is not.
    page, notices, unpressed = _watched(**{failure: True})
    _, handler, _ = page.handlers[0]

    assert asyncio.run(handler(page.bar)) is None

    assert notices == [] and len(unpressed) == 1 and "did not go away" in unpressed[0], unpressed
    assert said in unpressed[0] and page.idle.is_set(), unpressed


@pytest.mark.parametrize("failure", ["press_fails", "stays"])
def test_a_press_that_failed_is_not_tried_again_at_every_later_action(failure):
    # A notice still standing is met again by every action of the session, and each new try would hold that action
    # for seconds: one try, then the reason stands and uncovered controls go on.
    page, notices, unpressed = _watched(**{failure: True})
    _, handler, _ = page.handlers[0]

    asyncio.run(handler(page.bar))
    asyncio.run(handler(page.bar))
    asyncio.run(handler(page.bar))

    assert len(page.click_timeouts) == 1 and len(unpressed) == 1 and notices == []
    # Nothing is in flight after a trigger that tried nothing: a watch left busy would hold every call's end for
    # the whole wait (scoped re-review, round two).
    assert page.idle.is_set()


def test_a_notice_whose_button_shows_late_is_still_pressed():
    # No press was tried while the button was missing, so the next action that meets the notice looks again.
    page, notices, unpressed = _watched(accepts=0)
    _, handler, _ = page.handlers[0]

    asyncio.run(handler(page.bar))
    page.bar.accepts = 1
    asyncio.run(handler(page.bar))

    assert notices == [NOTICE] and page.clicks == [overlays.COOKIE_ACCEPT]
    assert len(unpressed) == 1 and "0 accept buttons" in unpressed[0]


def test_a_notice_met_again_in_one_session_is_named_once():
    page, _, unpressed = _watched(accepts=0)
    _, handler, _ = page.handlers[0]

    asyncio.run(handler(page.bar))
    asyncio.run(handler(page.bar))

    assert len(unpressed) == 1


@pytest.mark.parametrize("failure", [None, "press_fails", "unreadable"])
def test_the_watch_tells_when_a_press_is_in_flight(failure):
    # The action that met the notice can fail, or be given up, while the press runs on: whoever reads what the
    # handler left has to be able to wait for it (Backend._with does).
    page, _, _ = _watched(**({failure: True} if failure else {}))
    _, handler, _ = page.handlers[0]
    before = page.idle.is_set()

    asyncio.run(handler(page.bar))

    assert before is True and page.idle.is_set()
    # Busy from before its first look at the page, so no reader can slip in ahead of it.
    assert page.idle_during_read == [False]
    assert page.idle_during_press == ([] if failure == "unreadable" else [False])


def test_what_covers_a_control_is_reported_with_its_own_words():
    page = _Page(covering={"tag": "div", "id": "promo", "text": "Try the new Flow agent  Dismiss"})

    cover = asyncio.run(overlays.covering(page, "button.settings-trigger-button"))

    assert cover == {"tag": "div", "id": "promo", "text": "Try the new Flow agent  Dismiss"}
    assert ("evaluate", "button.settings-trigger-button") in page.asked
    assert page.clicks == [], "looking at what covers a control must click nothing"


def test_an_uncovered_control_reports_nothing():
    assert asyncio.run(overlays.covering(_Page(covering=None), "button.settings-trigger-button")) is None
