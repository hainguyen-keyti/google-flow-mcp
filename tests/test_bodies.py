"""Flow's replies read through a route (plan AT, 2026-10-22).

Measured 2026-10-21: Chrome 155 with Playwright 1.61.0 answers `Network.getResponseBody` with "No data found for
resource with given identifier" for some of Flow's replies (11 of 41 on one project load), so `response.text()` on a
page listener loses replies at random. A route fetches each reply once, keeps the bytes and gives the page what Flow
sent; `Response.body` then answers from those bytes.
"""

import asyncio
from collections import OrderedDict

import pytest
from playwright._impl._network import Response
from playwright.async_api import BrowserType

from video import bodies

NO_DATA = "Response.body: Protocol error (Network.getResponseBody): No data found for resource with given identifier"


class _Impl:
    def __init__(self, guid):
        self._guid = guid


class _Wrapped:
    """An async_api object: the guid lives on its _impl_obj, as Route.request's does."""

    def __init__(self, guid):
        self._impl_obj = _Impl(guid)


class _Reply:
    def __init__(self, data, fail=False):
        self.data, self.fail = data, fail

    async def body(self):
        if self.fail:
            raise RuntimeError("the reply could not be read")
        return self.data


class _Route:
    def __init__(self, guid, reply, fetch_fails=False, fulfill_fails=False):
        self.request = _Wrapped(guid)
        self.reply, self.fetch_fails, self.fulfill_fails = reply, fetch_fails, fulfill_fails
        self.log = []

    async def fetch(self, **kwargs):
        self.log.append(("fetch", kwargs))
        if self.fetch_fails:
            raise RuntimeError("net::ERR_FAILED")
        return self.reply

    async def fulfill(self, **kwargs):
        self.log.append(("fulfill", kwargs))
        if self.fulfill_fails:
            raise RuntimeError("Route is already handled")

    async def abort(self, error_code=None):
        self.log.append(("abort", error_code))

    async def continue_(self, **kwargs):
        self.log.append(("continue", kwargs))


class _Response:
    """What the patched impl `Response.body` sees: `self.request` is the impl Request with its guid."""

    def __init__(self, guid):
        self.request = _Impl(guid)


def _original(data=b"from chrome", fail=False):
    calls = []

    async def body(response):
        calls.append(response)
        if fail:
            raise RuntimeError(NO_DATA)
        return data

    return body, calls


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(bodies, "_installed", False)
    monkeypatch.setattr(bodies, "_bodies", OrderedDict())
    monkeypatch.delenv(bodies.ENV, raising=False)


def test_the_handler_fetches_once_without_retries_keeps_the_body_and_fulfills_what_it_fetched(fresh):
    route = _Route("r1", _Reply(b'[["wrb.fr","Zzl0ze","[]"]]'))

    asyncio.run(bodies.handle(route))

    assert route.log == [
        ("fetch", {"max_retries": 0}),
        ("fulfill", {"response": route.reply, "body": b'[["wrb.fr","Zzl0ze","[]"]]'}),
    ], route.log
    assert bodies._bodies == {"r1": b'[["wrb.fr","Zzl0ze","[]"]]'}


@pytest.mark.parametrize("where", ["fetch", "fulfill"])
def test_a_failure_anywhere_aborts_the_route_and_never_continues(fresh, where):
    # A continue_ after a fetch that reached Flow would send a paid submit a second time (I3).
    route = _Route("r1", _Reply(b"x"), fetch_fails=where == "fetch", fulfill_fails=where == "fulfill")

    asyncio.run(bodies.handle(route))

    assert [step for step, _ in route.log if step in ("abort", "continue")] == ["abort"], route.log


def test_an_unreadable_fetched_reply_aborts_too(fresh):
    route = _Route("r1", _Reply(b"", fail=True))

    asyncio.run(bodies.handle(route))

    assert [step for step, _ in route.log] == ["fetch", "abort"] and bodies._bodies == {}


def test_the_patched_body_answers_from_the_cache_without_asking_chrome(fresh):
    original, calls = _original(fail=True)
    bodies.remember("r1", b"kept")

    assert asyncio.run(bodies.read(_Response("r1"), original)) == b"kept"
    assert calls == []


def test_a_response_the_route_never_saw_is_read_by_chrome(fresh):
    original, calls = _original(b"from chrome")
    bodies.remember("other", b"kept")

    response = _Response("r9")
    assert asyncio.run(bodies.read(response, original)) == b"from chrome"
    assert calls == [response]


def test_the_cache_keeps_at_most_its_bound_and_drops_the_oldest(fresh):
    for index in range(bodies.KEPT + 1):
        bodies.remember(f"r{index}", b"%d" % index)

    assert len(bodies._bodies) == bodies.KEPT
    assert "r0" not in bodies._bodies and f"r{bodies.KEPT}" in bodies._bodies


class _Context:
    def __init__(self):
        self.routes = []

    async def route(self, pattern, handler):
        self.routes.append((pattern, handler))


@pytest.fixture
def launch(monkeypatch, fresh):
    contexts = []

    async def original(self, user_data_dir, **kwargs):
        context = _Context()
        contexts.append(context)
        return context

    monkeypatch.setattr(BrowserType, "launch_persistent_context", original)
    return contexts


def _launch():
    return asyncio.run(BrowserType.launch_persistent_context(object(), "/tmp/profile", headless=False))


def test_install_routes_every_context_the_launch_returns_and_patches_the_body_read(launch):
    before = Response.body

    bodies.install()
    context = _launch()

    assert context.routes == [(bodies.PATTERN, bodies.handle)]
    assert Response.body is not before and bodies._installed is True


def test_the_patched_body_read_is_the_cache_first_read(launch):
    bodies.install()
    bodies.remember("r1", b"kept")

    assert asyncio.run(Response.body(_Response("r1"))) == b"kept"


def test_install_runs_once(launch):
    bodies.install()
    patched = Response.body
    bodies.install()
    context = _launch()

    assert Response.body is patched and len(context.routes) == 1


def test_the_switch_leaves_playwright_alone(launch, monkeypatch):
    monkeypatch.setenv(bodies.ENV, "0")
    before = Response.body

    bodies.install()
    context = _launch()

    assert context.routes == [] and Response.body is before and bodies._installed is False


def test_the_repos_own_browser_session_installs_it_before_it_launches(monkeypatch, fresh):
    import pathlib

    from video import offscreen
    from video import session as session_mod

    order = []
    monkeypatch.setattr(offscreen, "install", lambda: order.append("offscreen"))
    monkeypatch.setattr(session_mod.bodies, "install", lambda: order.append("bodies"))

    class _Client:
        async def __aenter__(self):
            order.append("launch")
            raise RuntimeError("stop here")

        async def __aexit__(self, *exc):
            return False

    def factory(profile_dir, *, headless):
        order.append("factory")
        return _Client()

    session = session_mod.FlowSession(profile_dir=pathlib.Path("/tmp/profile"), client_factory=factory)
    with pytest.raises(RuntimeError, match="stop here"):
        asyncio.run(session.__aenter__())

    assert order[:3] == ["offscreen", "bodies", "factory"], order
