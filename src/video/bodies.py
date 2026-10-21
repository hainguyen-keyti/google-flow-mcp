"""Read Flow's replies through a route, so a body Chrome no longer hands to DevTools is still read.

Measured 2026-10-21: Chrome 155 (it updated itself on 2026-10-16 and 2026-10-20) driven by Playwright 1.61.0 answers
`Network.getResponseBody` with "No data found for resource with given identifier" for some of Flow's replies (11 of
41 on one project load, a 146-byte reply among them), since every reply carries `cache-control: no-store` and the
page reads it as a stream (microsoft/playwright#42742, a Chromium bug no Playwright release fixes). Every listener in
this repo and in gflow reads a reply with `response.text()`, which goes through `Response.body`.

A route in front of every batchexecute request fetches the reply itself, keeps the bytes, and gives the page the
status, headers and body it fetched; `Response.body` is then answered from those bytes (133 of 133 replies over four
page loads, against 30 of 41 without it). The route never sends a request twice: it fetches with no retry and aborts
the route on any failure, since a `continue_` after a fetch that reached Flow would submit a paid job again.

Installed once per process like `offscreen`, on the launch gflow and this repo share. VIDEO_REPLY_BODIES=0 leaves
Playwright alone.
"""

from __future__ import annotations

import contextlib
import os
from collections import OrderedDict
from typing import Any

from playwright._impl._network import Response
from playwright.async_api import BrowserType

ENV = "VIDEO_REPLY_BODIES"
PATTERN = "**/batchexecute*"
# Bodies kept: the largest reply measured is a 366 KB listing, so the bound caps the cache well under 200 MB.
KEPT = 512
_installed = False
_bodies: OrderedDict[str, bytes] = OrderedDict()


def _guid(request: Any) -> str:
    """The id of a request as Playwright holds it: on the impl object itself, or under an async_api wrapper."""
    return getattr(request, "_impl_obj", request)._guid


def remember(guid: str, body: bytes) -> None:
    _bodies[guid] = body
    _bodies.move_to_end(guid)
    while len(_bodies) > KEPT:
        _bodies.popitem(last=False)


async def handle(route: Any) -> None:
    """Fetch the reply once, keep its bytes, give the page what was fetched; on any failure abort, never continue."""
    try:
        reply = await route.fetch(max_retries=0)
        body = await reply.body()
        remember(_guid(route.request), body)
        await route.fulfill(response=reply, body=body)
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            await route.abort()


async def read(response: Any, original: Any) -> bytes:
    """A response's body: the bytes the route kept for its request, else what Chrome still holds."""
    kept = _bodies.get(_guid(response.request))
    if kept is not None:
        return kept
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
        await context.route(PATTERN, handle)
        return context

    async def body(self: Any) -> bytes:
        return await read(self, original_body)

    BrowserType.launch_persistent_context = launch  # type: ignore[method-assign]
    Response.body = body  # type: ignore[method-assign]
    _installed = True
