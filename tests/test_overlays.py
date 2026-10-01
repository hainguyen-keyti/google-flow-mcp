"""Flow lays things over its own page. The driver may press ONE of them, the cookie notice's own button (the owner's
decision of 2026-10-01); anything else that covers a control is named and never clicked."""

import asyncio

import pytest

from video.flow import overlays

NOTICE = (
    "flow.google.com uses cookies from Google to deliver and enhance the quality of its services "
    "and to analyze traffic. Learn more OK, got it"
)


class _Button:
    def __init__(self, page, selector, hits):
        self.page, self.selector, self.hits = page, selector, hits

    async def count(self):
        return self.hits

    async def click(self, **_):
        if self.page.press_fails:
            raise TimeoutError("Locator.click: Timeout 8000ms exceeded.")
        self.page.clicks.append(self.selector)
        self.page.steps.append("press")


class _Bar:
    def __init__(self, page, text, accepts):
        self.page, self.selector, self.text, self.accepts = page, overlays.COOKIE_BAR, text, accepts

    async def inner_text(self):
        return self.text

    def locator(self, selector):
        return _Button(self.page, selector, self.accepts if selector == overlays.COOKIE_ACCEPT else 1)

    async def wait_for(self, state=None, timeout=None):
        assert state == "hidden", state
        self.page.steps.append("wait hidden")
        if self.page.stays:
            raise TimeoutError("Locator.wait_for: Timeout 8000ms exceeded.")


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

    def __init__(self, text=NOTICE, accepts=1, covering=None, press_fails=False, stays=False):
        self.clicks, self.handlers, self.asked, self.steps = [], [], [], []
        self.bar = _Bar(self, text, accepts)
        self.cover = covering
        self.press_fails, self.stays = press_fails, stays

    def locator(self, selector):
        self.asked.append(selector)
        return self.bar if selector == overlays.COOKIE_BAR else _Button(self, selector, 1)

    async def add_locator_handler(self, locator, handler, **kwargs):
        self.handlers.append((locator.selector, handler, kwargs))

    async def evaluate(self, js, *args):
        self.asked.append(("evaluate", *args))
        return self.cover


def _watched(**page_options):
    page = _Page(**page_options)
    notices, unpressed = _Notices(page), []
    asyncio.run(overlays.watch_cookie_notice(page, notices, unpressed))
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
    assert page.steps == ["press", "wait hidden", "record"]


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


@pytest.mark.parametrize("failure", ["press_fails", "stays"])
def test_a_notice_that_did_not_go_away_is_never_recorded_as_dismissed(failure):
    page, notices, unpressed = _watched(**{failure: True})
    _, handler, _ = page.handlers[0]

    asyncio.run(handler(page.bar))

    assert notices == []
    assert len(unpressed) == 1 and "did not go away" in unpressed[0] and "uses cookies" in unpressed[0]


def test_a_notice_met_again_in_one_session_is_named_once():
    page, _, unpressed = _watched(accepts=0)
    _, handler, _ = page.handlers[0]

    asyncio.run(handler(page.bar))
    asyncio.run(handler(page.bar))

    assert len(unpressed) == 1


def test_what_covers_a_control_is_reported_with_its_own_words():
    page = _Page(covering={"tag": "div", "id": "promo", "text": "Try the new Flow agent  Dismiss"})

    cover = asyncio.run(overlays.covering(page, "button.settings-trigger-button"))

    assert cover == {"tag": "div", "id": "promo", "text": "Try the new Flow agent  Dismiss"}
    assert ("evaluate", "button.settings-trigger-button") in page.asked
    assert page.clicks == [], "looking at what covers a control must click nothing"


def test_an_uncovered_control_reports_nothing():
    assert asyncio.run(overlays.covering(_Page(covering=None), "button.settings-trigger-button")) is None
