"""Read Flow's replies through a route, so a body Chrome no longer hands to DevTools is still read.

Measured 2026-10-21: Chrome 155 (it updated itself on 2026-10-16 and 2026-10-20) driven by Playwright 1.61.0 answers
`Network.getResponseBody` with "No data found for resource with given identifier" for some of Flow's replies (11 of
41 on one project load, a 146-byte reply among them), since every reply carries `cache-control: no-store` and the
page reads it as a stream (microsoft/playwright#42742, a Chromium bug no Playwright release fixes). Every listener in
this repo and in gflow reads a reply with `response.text()`, which goes through `Response.body`.

A route in front of every batchexecute call of Flow's host fetches the reply itself, keeps the bytes, and gives the
page the status, headers and body it fetched; `Response.body` is then answered from those bytes (170 of 170 replies
over five page loads, against 118 of 139 without it). With the route, Playwright's driver is the client that sends
every call, paid submits included (the owner's decision of 2026-10-22, measured on one paid clip). The route never
sends a request twice: it fetches with no retry, follows no redirect, waits as long as the page would (replies of 90 s
were measured on a scene add), and aborts the route when the fetch fails, since a `continue_` after a fetch that
reached Flow would submit a paid job again; a POST whose body Chrome withholds from the route (a blob, a file, a
stream) is left to Chrome, since a fetch would send it empty.

Installed once per process like `offscreen`, on the launch gflow and this repo share. VIDEO_REPLY_BODIES=0 leaves
Playwright alone.
"""

from __future__ import annotations

import contextlib
import os
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

from playwright._impl._network import Response
from playwright.async_api import BrowserType

ENV = "VIDEO_REPLY_BODIES"
# Measured 2026-10-21: every reply the parsers read answers a POST to this host and path.
HOST = "flow.google.com"
PATH_END = "/batchexecute"
# Bodies kept: the largest reply measured is a 366 KB listing, so the bound caps the cache well under 200 MB.
KEPT = 512
_installed = False
_bodies: OrderedDict[str, bytes] = OrderedDict()
counts = {"routed": 0, "served": 0, "fallen_back": 0}


def covers(url: str) -> bool:
    parts = urlsplit(url)
    return parts.netloc == HOST and parts.path.endswith(PATH_END)


def _guid(request: Any) -> str:
    """The id of a request as Playwright holds it: on the impl object itself, or under an async_api wrapper."""
    return getattr(request, "_impl_obj", request)._guid


def remember(guid: str, body: bytes) -> None:
    _bodies[guid] = body
    _bodies.move_to_end(guid)
    while len(_bodies) > KEPT:
        _bodies.popitem(last=False)


async def handle(route: Any) -> None:
    """Fetch the reply once, keep its bytes, give the page what was fetched; a failed fetch aborts, never continues."""
    request = route.request
    if request.method == "POST" and request.post_data_buffer is None:
        await route.continue_()
        return
    reply = None
    try:
        reply = await route.fetch(max_retries=0, max_redirects=0, timeout=0)
        body = await reply.body()
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            await route.abort()
        if reply is not None:
            with contextlib.suppress(Exception):
                await reply.dispose()
        return
    counts["routed"] += 1
    with contextlib.suppress(Exception):
        remember(_guid(request), body)
    try:
        await route.fulfill(response=reply, body=body)
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            await route.abort()
    finally:
        with contextlib.suppress(Exception):
            await reply.dispose()


async def read(response: Any, original: Any) -> bytes:
    """A response's body: the bytes the route kept for its request, else what Chrome still holds."""
    kept = _bodies.get(_guid(response.request))
    if kept is not None:
        counts["served"] += 1
        return kept
    counts["fallen_back"] += 1
    return await original(response)


def install() -> None:
    """Route every context the launch returns and answer `Response.body` from the route's bytes; once per process."""
    global _installed
    if _installed or os.environ.get(ENV, "1") == "0":
        return
    original_launch = BrowserType.launch_persistent_context
    original_body = Response.body

    async def launch(self: Any, user_data_dir: Any, **kwargs: Any) -> Any:
        context = await original_launch(self, user_data_dir, **kwargs)
        await context.route(covers, handle)
        return context

    async def body(self: Any) -> bytes:
        return await read(self, original_body)

    BrowserType.launch_persistent_context = launch  # type: ignore[method-assign]
    Response.body = body  # type: ignore[method-assign]
    _installed = True
