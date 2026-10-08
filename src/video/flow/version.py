"""Which Flow build a session runs on, read for $0 off the page itself.

Measured 2026-10-08 (out/verify-20261008/build_probe.py): the home page loads one bundle whose src carries
`k=boq-labs-ai-sandbox.AiSandboxAngularFrontend.en.Lt87BHY7SpE.2018.O`, and nothing else on the page names a build
(no meta, no window global). The part after the language is the build label. The first page a session reaches sets
`current` for the process, so every ledger row and paid answer can say which build it ran on, and `flow check`
compares the live label with the one the baselines were walked on.
"""

from __future__ import annotations

import re
from typing import Any

BUILD_RE = re.compile(r"boq-labs-ai-sandbox\.AiSandboxAngularFrontend\.[a-z-]+\.([A-Za-z0-9_-]+\.\d+\.[A-Z])")
SCRIPTS_JS = "() => [...document.querySelectorAll('script[src]')].map(s => s.getAttribute('src') || '')"

current: str | None = None


def label_in(sources: list[str]) -> str | None:
    """The build label among a page's script sources, or None when no Flow bundle is loaded (a login or 404 page)."""
    for src in sources:
        found = BUILD_RE.search(str(src or ""))
        if found:
            return found.group(1)
    return None


async def read(page: Any) -> str | None:
    """The label of the page the session is on, kept as `current` once found. Never raises: it runs inside every
    navigation, where an exception would replace the driver's own result."""
    global current
    try:
        found = label_in(await page.evaluate(SCRIPTS_JS))
    except Exception:  # noqa: BLE001
        return None
    if found:
        current = found
    return found
