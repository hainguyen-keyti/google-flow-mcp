"""Flow's replies read through a route (plan AT, 2026-10-22).

Measured 2026-10-21: Chrome 155 with Playwright 1.61.0 answers `Network.getResponseBody` with "No data found for
resource with given identifier" for some of Flow's replies (11 of 41 on one project load), so `response.text()` on a
page listener loses replies at random. A route fetches each reply once, keeps the bytes and gives the page what Flow
sent; `Response.body` then answers from those bytes.
"""

import asyncio
import importlib.util
import os
import re
from collections import OrderedDict
from pathlib import Path

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
    def __init__(self, data, fail=False, log=None):
        self.data, self.fail, self.log = data, fail, log if log is not None else []

    async def body(self):
        if self.fail:
            raise RuntimeError("the reply could not be read")
        return self.data

    async def dispose(self):
        self.log.append(("dispose", None))


class _Route:
    def __init__(self, guid, reply, fetch_fails=False, fulfill_fails=False, method="POST", post=b"f.req=x"):
        self.request = _Wrapped(guid)
        self.request.method, self.request.post_data_buffer = method, post
        self.reply, self.fetch_fails, self.fulfill_fails = reply, fetch_fails, fulfill_fails
        self.log = reply.log
        self.cached_at_fulfill = None

    async def fetch(self, **kwargs):
        self.log.append(("fetch", kwargs))
        if self.fetch_fails:
            raise RuntimeError("net::ERR_FAILED")
        return self.reply

    async def fulfill(self, **kwargs):
        self.log.append(("fulfill", kwargs))
        self.cached_at_fulfill = dict(bodies._bodies)
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
    monkeypatch.setattr(bodies, "counts", {"routed": 0, "served": 0, "fallen_back": 0})
    monkeypatch.delenv(bodies.ENV, raising=False)


def test_the_handler_fetches_once_without_retries_keeps_the_body_and_fulfills_what_it_fetched(fresh):
    route = _Route("r1", _Reply(b'[["wrb.fr","Zzl0ze","[]"]]'))

    asyncio.run(bodies.handle(route))

    assert route.log == [
        ("fetch", {"max_retries": 0, "max_redirects": 0, "timeout": 0}),
        ("fulfill", {"response": route.reply, "body": b'[["wrb.fr","Zzl0ze","[]"]]'}),
        ("dispose", None),
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

    assert [step for step, _ in route.log] == ["fetch", "abort", "dispose"] and bodies._bodies == {}


def test_the_body_is_kept_before_the_page_gets_it(fresh):
    # A listener that reads the reply the moment the page gets it must find it in the cache already (R12).
    route = _Route("r1", _Reply(b"x"))

    asyncio.run(bodies.handle(route))

    assert route.cached_at_fulfill == {"r1": b"x"}


def test_a_cache_that_cannot_keep_the_body_still_lets_the_reply_through(fresh, monkeypatch):
    # Review D2: the cache write sat inside the abort-on-any-failure block, so a cache error withheld a reply Flow
    # had processed.
    def broken(guid, body):
        raise RuntimeError("no room")

    monkeypatch.setattr(bodies, "remember", broken)
    route = _Route("r1", _Reply(b"x"))

    asyncio.run(bodies.handle(route))

    assert [step for step, _ in route.log] == ["fetch", "fulfill", "dispose"], route.log


def test_a_post_whose_body_chrome_withholds_is_sent_by_chrome(fresh):
    # Review D6: a blob, file or stream body is not shown to the route, so a fetch would POST it empty; Chrome sends
    # it and nothing is fetched (I3 holds, one request).
    route = _Route("r1", _Reply(b"x"), post=None)

    asyncio.run(bodies.handle(route))

    assert route.log == [("continue", {})] and bodies._bodies == {}


def test_a_get_without_a_body_is_still_routed(fresh):
    route = _Route("r1", _Reply(b"x"), method="GET", post=None)

    asyncio.run(bodies.handle(route))

    assert [step for step, _ in route.log] == ["fetch", "fulfill", "dispose"]


@pytest.mark.parametrize(
    ("url", "covered"),
    [
        (
            (
                "https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=Zzl0ze"
                "&source-path=%2Fproject%2F118aece2-f6f3-4121-8d11-2b36037d9b36&f.sid=-6428967243721632145"
                "&bl=boq_assistant_20261021.03_p0&hl=en&rt=c"
            ),
            True,
        ),
        ("https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=UpteDb&a=b/c", True),
        ("https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute", True),
        ("https://labs.google/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=Zzl0ze", False),
        ("https://play.google.com/log?format=json", False),
        ("https://flow.google.com/project/118aece2-f6f3-4121-8d11-2b36037d9b36", False),
    ],
    ids=["a listing call", "a slash in the query", "no query", "another host", "another path", "a page"],
)
def test_the_route_covers_flows_batchexecute_calls_and_nothing_else(url, covered):
    # Review D4: a glob is not host-anchored and `*` stops at a slash; the route is a predicate on Flow's own host
    # and path (measured 2026-10-21: every call is `/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=...`).
    assert bodies.covers(url) is covered


def test_the_reads_are_counted_so_a_run_can_tell_served_from_fallen_back(fresh):
    original, _ = _original(b"from chrome")
    bodies.remember("r1", b"kept")

    asyncio.run(bodies.read(_Response("r1"), original))
    asyncio.run(bodies.read(_Response("r2"), original))

    assert bodies.counts == {"routed": 0, "served": 1, "fallen_back": 1}


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


def test_a_routed_request_is_counted(fresh):
    asyncio.run(bodies.handle(_Route("r1", _Reply(b"x"))))

    assert bodies.counts["routed"] == 1


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

    # Re-review of plan AT: a callable makes Playwright route `**/*`, every script and image of the page waiting on
    # Python's loop (`_helper.py:510-516`); a compiled regex is matched by the driver itself.
    assert context.routes == [(bodies.ROUTE, bodies.handle)]
    assert isinstance(bodies.ROUTE, re.Pattern)
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


@pytest.fixture(scope="module")
def reply_bodies():
    """The acceptance script, imported the way tests/test_mcp_smoke.py imports the smoke; the environment defaults it
    sets on import must not reach the tests after this module."""
    saved = dict(os.environ)
    path = Path(__file__).resolve().parents[1] / "scripts" / "acceptance" / "reply_bodies.py"
    spec = importlib.util.spec_from_file_location("reply_bodies", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return module


class _Seen:
    def __init__(self, url):
        self.request = type("R", (), {"url": url})()

    async def text(self):
        return "x"


def test_the_acceptance_listener_reads_only_the_calls_the_route_covers(reply_bodies, fresh):
    # Re-review of plan AT (D9): the listener counted batchexecute replies of any host while the route covers Flow's
    # only, so a call to another Google host read by Chrome would fail a read for the wrong reason.
    page = type("P", (), {"on": lambda self, event, fn: None})()

    async def flow():
        reads = reply_bodies.Reads(page)
        reads.on_response(_Seen("https://play.google.com/log?batchexecute=1"))
        reads.on_response(
            _Seen("https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=nzlxg")
        )
        return await reads.settle()

    assert asyncio.run(flow()) == (1, 0)


@pytest.mark.parametrize(
    ("switch", "fallen", "status"), [("1", 1, "FAIL"), ("1", 0, "PASS"), ("0", 1, "PASS")]
)
def test_a_read_the_route_did_not_serve_fails_only_with_the_route_on(
    reply_bodies, fresh, monkeypatch, switch, fallen, status
):
    monkeypatch.setenv("VIDEO_REPLY_BODIES", switch)
    bodies.counts.update(served=3, fallen_back=fallen)
    reads = reply_bodies.Reads(type("P", (), {"on": lambda self, event, fn: None})())

    row = asyncio.run(
        reply_bodies.judge(
            "project",
            1,
            {rpc: [None] for rpc in reply_bodies.expected("project")},
            reads,
            "b",
        )
    )

    assert row["status"] == status, row
