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
        self.page.clicks.append(self.selector)


class _Bar:
    def __init__(self, page, text, accepts):
        self.page, self.selector, self.text, self.accepts = page, overlays.COOKIE_BAR, text, accepts

    async def inner_text(self):
        return self.text

    def locator(self, selector):
        return _Button(self.page, selector, self.accepts if selector == overlays.COOKIE_ACCEPT else 1)


class _Page:
    """A page whose only overlay handling is what the code under test registers."""

    def __init__(self, text=NOTICE, accepts=1, covering=None):
        self.clicks, self.handlers, self.asked = [], [], []
        self.bar = _Bar(self, text, accepts)
        self.cover = covering

    def locator(self, selector):
        self.asked.append(selector)
        return self.bar if selector == overlays.COOKIE_BAR else _Button(self, selector, 1)

    async def add_locator_handler(self, locator, handler, **kwargs):
        self.handlers.append((locator.selector, handler, kwargs))

    async def evaluate(self, js, *args):
        self.asked.append(("evaluate", *args))
        return self.cover


def test_only_flows_cookie_notice_is_handed_to_the_overlay_handler():
    page, notices = _Page(), []

    asyncio.run(overlays.watch_cookie_notice(page, notices))

    assert [selector for selector, _, _ in page.handlers] == [overlays.COOKIE_BAR]
    assert page.clicks == [] and notices == [], "registering must click nothing"


def test_the_handler_presses_the_notices_own_button_once_and_keeps_what_it_said():
    page, notices = _Page(), []
    asyncio.run(overlays.watch_cookie_notice(page, notices))
    _, handler, _ = page.handlers[0]

    asyncio.run(handler(page.bar))

    assert page.clicks == [overlays.COOKIE_ACCEPT]
    assert notices == [NOTICE]


@pytest.mark.parametrize("accepts", [0, 2])
def test_a_notice_without_exactly_one_accept_button_is_named_and_left_alone(accepts):
    page, notices = _Page(accepts=accepts), []
    asyncio.run(overlays.watch_cookie_notice(page, notices))
    _, handler, _ = page.handlers[0]

    with pytest.raises(LookupError, match="uses cookies"):
        asyncio.run(handler(page.bar))

    assert page.clicks == [] and notices == []


def test_what_covers_a_control_is_reported_with_its_own_words():
    page = _Page(covering={"tag": "div", "id": "promo", "text": "Try the new Flow agent  Dismiss"})

    cover = asyncio.run(overlays.covering(page, "button.settings-trigger-button"))

    assert cover == {"tag": "div", "id": "promo", "text": "Try the new Flow agent  Dismiss"}
    assert ("evaluate", "button.settings-trigger-button") in page.asked
    assert page.clicks == [], "looking at what covers a control must click nothing"


def test_an_uncovered_control_reports_nothing():
    assert asyncio.run(overlays.covering(_Page(covering=None), "button.settings-trigger-button")) is None
