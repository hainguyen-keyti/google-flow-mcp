"""Watch Flow for the anchors this repo steers by. $0: two navigations and one settings click.

    uv run python -m video.probes.canary --project <id>

Exit code is 1 when an anchor the driver depends on is gone. New things on the page are never drift:
Flow adds buttons all the time, and only the ones we reach for can break us.

Every check reuses the driver's own constant where there is one, so the canary cannot pass while the
driver fails. The price line is the clearest case: it is matched with `composer.PRICE_RE`, the same
regex that decides whether a shot is allowed to spend credits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from video.flow import clips, composer, parsers, reader
from video.probes._common import out_path
from video.session import GRID_READY, MIGRATED_ROOT, PROJECT_READY, FlowSession

Observed = dict[str, Any]

# Modes the driver actually clicks. Flow may offer more; losing one of these stops a route.
REQUIRED_MODES = ("Frames", "Ingredients")
REQUIRED_CHIPS = ("9:16", "x1")


def _norm(values: list[str]) -> list[str]:
    return [re.sub(r"\s+", " ", (value or "")).strip().lower() for value in values]


def _has(values: list[str], needle: str) -> bool:
    return any(needle.lower() in value for value in _norm(values))


@dataclass(frozen=True)
class Check:
    name: str
    reason: str
    run: Callable[[Observed], tuple[bool, str]]
    applies: Callable[[Observed], bool] | None = None
    skipped: str = ""


def _element(tag: str, reason: str, **extra: Any) -> Check:
    def run(observed: Observed) -> tuple[bool, str]:
        elements = _norm(observed.get("custom_elements") or [])
        return (tag in elements), f"<{tag}> " + ("present" if tag in elements else "MISSING")

    return Check(name=f"{tag} element", reason=reason, run=run, **extra)


def editor_target(records: list[dict[str, Any]]) -> str | None:
    """Newest finished video to open the clip editor on.

    A picture or a job still rendering opens an editor that cannot run extend or an Omni edit, so it
    would report drift that is really just the wrong media.
    """
    ready = [
        record
        for record in records
        if record.get("kind") == "video" and record.get("status") == 3 and record.get("id")
    ]
    if not ready:
        return None
    return max(ready, key=lambda record: record.get("created") or 0)["id"]


def _button(name: str, needle: str, reason: str) -> Check:
    def run(observed: Observed) -> tuple[bool, str]:
        buttons = observed.get("composer_buttons") or []
        found = _has(buttons, needle)
        return found, f"{needle!r} " + ("present" if found else f"MISSING, saw {buttons[:8]}")

    return Check(name=name, reason=reason, run=run)


def _rpc(name: str, key: str, rpcid: str, reason: str) -> Check:
    def run(observed: Observed) -> tuple[bool, str]:
        seen = observed.get(key) or []
        return (rpcid in seen), f"{rpcid} " + ("observed" if rpcid in seen else f"MISSING, saw {seen[:8]}")

    return Check(name=name, reason=reason, run=run)


def _price_line(observed: Observed) -> tuple[bool, str]:
    text = observed.get("settings_text") or ""
    match = composer.PRICE_RE.search(text)
    if match:
        return True, f"driver regex matched {match.group(0)!r}"
    return False, f"composer.PRICE_RE matched nothing in {text[:90]!r}"


def _modes(observed: Observed) -> tuple[bool, str]:
    text = (observed.get("settings_text") or "").lower()
    missing = [mode for mode in REQUIRED_MODES if mode.lower() not in text]
    return (not missing), ("all present" if not missing else f"MISSING {missing}")


def _chips(observed: Observed) -> tuple[bool, str]:
    text = (observed.get("settings_text") or "").lower()
    missing = [chip for chip in REQUIRED_CHIPS if chip.lower() not in text]
    return (not missing), ("all present" if not missing else f"MISSING {missing}")


CHECKS: tuple[Check, ...] = (
    _element(PROJECT_READY, "every project navigation waits on this; losing it times out every command"),
    _element("flow-prompt-box", "the composer the story pipeline types into and submits from"),
    _element(
        clips.EDITOR,
        "the clip editor that runs extend and the Omni edit that dresses a shot",
        applies=lambda observed: observed.get("editor") is not None,
        skipped="no finished video in this project, so there was no editor page to open",
    ),
    _button("settings trigger", "settings trigger", "opens the panel holding the mode, aspect and price"),
    _button("submit button", "start generation", "the one button that spends credits"),
    Check(
        name="price line",
        reason="the driver refuses to submit when it cannot read this line, so losing it stops all work",
        run=_price_line,
    ),
    Check(
        name="composer modes",
        reason="Frames pins a start frame and Ingredients attaches the character; a route dies without one",
        run=_modes,
    ),
    Check(
        name="aspect and count chips",
        reason="the driver writes 9:16 and x1 every run; a missing chip means it cannot hold the price",
        run=_chips,
    ),
    _rpc(
        "listing rpc", "project_rpcids", "Zzl0ze", "every result is judged by this listing, never by the UI"
    ),
    _rpc("credits rpc", "grid_rpcids", "nzlxg", "the balance before and after is how spending is proven"),
    _rpc("projects rpc", "grid_rpcids", "UpteDb", "the project list every command starts from"),
)


def compare(observed: Observed) -> list[dict[str, str]]:
    """Judge one observation against the anchors the driver needs. Pure: no browser, no network."""
    findings = []
    for check in CHECKS:
        if check.applies is not None and not check.applies(observed):
            status, detail = "SKIP", check.skipped or "not applicable to what was observed"
        else:
            ok, detail = check.run(observed)
            status = "PASS" if ok else "FAIL"
        findings.append({"name": check.name, "status": status, "detail": detail, "reason": check.reason})
    return findings


def exit_code(findings: list[dict[str, str]]) -> int:
    return 1 if any(f["status"] == "FAIL" for f in findings) else 0


_ELEMENTS_JS = """() => [...new Set([...document.querySelectorAll('*')]
  .map(e => e.tagName.toLowerCase()).filter(t => t.includes('-')))].slice(0, 200)"""

_BUTTONS_JS = """() => {
  const label = (e) => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g,' ').slice(0,40);
  const box = [...document.querySelectorAll('flow-prompt-box button, flow-base-prompt-box button')];
  const all = box.length ? box : [...document.querySelectorAll('button')];
  return [...new Set(all.map(label).filter(Boolean))].slice(0, 60);
}"""

_OVERLAY_JS = """() => [...document.querySelectorAll('.cdk-overlay-pane, [role=menu], [role=dialog]')]
  .filter(e => e.offsetParent !== null)
  .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ')).join(' | ')"""


async def observe(session: FlowSession, project_id: str) -> Observed:
    """Look at Flow without changing anything: navigate, open the settings panel, read, close it."""
    grid = await reader.capture(session, lambda: session.goto(MIGRATED_ROOT, ready=GRID_READY), settle=6.0)
    project = await reader.capture(
        session, lambda: session.goto(session.project_url(project_id), ready=PROJECT_READY), settle=8.0
    )
    page = session.page
    # Open the panel by hand rather than through the driver helper: that helper waits for the price line,
    # which is one of the things being tested, so using it would hide the failure it is meant to catch.
    settings_text = ""
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(600)
        trigger = page.locator(composer.SETTINGS).first
        await trigger.wait_for(state="visible", timeout=15_000)
        await trigger.click(timeout=8_000)
        await page.wait_for_timeout(2_500)
        settings_text = await page.evaluate(_OVERLAY_JS)
    except Exception as exc:  # noqa: BLE001
        settings_text = f"(settings panel unreachable: {type(exc).__name__})"
    finally:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
    elements = list(await page.evaluate(_ELEMENTS_JS))
    buttons = list(await page.evaluate(_BUTTONS_JS))

    # The clip editor lives on its own page, so its shell is only visible once we open one.
    target = None
    try:
        target = editor_target(parsers.records(reader.one(project, "Zzl0ze")))
    except (LookupError, TypeError, IndexError, KeyError):
        target = None
    if target:
        try:
            await session.goto(f"{session.project_url(project_id)}/edit/{target}", ready=clips.EDITOR)
            await page.wait_for_timeout(2_500)
            elements += list(await page.evaluate(_ELEMENTS_JS))
        except Exception as exc:  # noqa: BLE001
            elements.append(f"(editor unreachable: {type(exc).__name__})")
    return {
        "project": project_id,
        "editor": target,
        "custom_elements": sorted(set(elements)),
        "composer_buttons": buttons,
        "settings_text": settings_text,
        "grid_rpcids": sorted(grid),
        "project_rpcids": sorted(project),
    }


async def _run(project_id: str, profile: str, as_json: bool) -> int:
    async with FlowSession(profile) as session:
        observed = await observe(session, project_id)
    findings = compare(observed)
    record = out_path("canary")
    record.write_text(json.dumps({"observed": observed, "findings": findings}, indent=2, ensure_ascii=False))
    if as_json:
        print(json.dumps(findings, indent=2, ensure_ascii=False))
    else:
        print(f"{'STATUS':7s} {'ANCHOR':24s} DETAIL")
        for finding in findings:
            print(f"{finding['status']:7s} {finding['name']:24s} {finding['detail']}")
        failed = [f for f in findings if f["status"] == "FAIL"]
        print(f"\nanchors={len(findings)} pass={len(findings) - len(failed)} fail={len(failed)}")
        for finding in failed:
            print(f"  {finding['name']}: {finding['reason']}")
        print(f"\nfull observation: {record}")
    return exit_code(findings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    return asyncio.run(_run(args.project, args.profile, args.json))


if __name__ == "__main__":
    sys.exit(main())
