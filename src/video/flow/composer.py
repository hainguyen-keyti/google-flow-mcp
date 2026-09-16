"""Drive the project composer in Ingredients mode with a character attached.

gflow cannot do this: `@Name` and `--reference-entity` are unported on the migrated host, so the story
pipeline drives the composer itself. Reuses the Plan 1 helpers for listing, submitting and downloading
rather than rebuilding them.

Two money guards, both measured the hard way in Plan 1 and T2:
- the composer REMEMBERS the last run (mode, aspect, model, count), so every setting is written every
  time and the live "Generating will use N credits" line is checked before the click;
- the submit leaves late, so the page is held until the request goes out, and the click never repeats.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.api.transports.batchexecute import STATUS_DONE, STATUS_RUNNING, STATUS_SUBMITTED, parse_frames
from gflow_cli.data.redaction import redact_error_detail
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video import gen
from video.flow import agent, clips, reader
from video.flow import download as download_mod
from video.session import PROJECT_READY, FlowSession

PRICE_RE = re.compile(r"generating will use\s+(\d+)\s+credit", re.IGNORECASE)
SETTINGS = "button[aria-label*='Settings trigger'], .settings-trigger-button"
INGREDIENTS = (
    "flow-prompt-box button[aria-label*='ngredient'], flow-base-prompt-box button[aria-label*='ngredient']"
)
_OVERLAY_TEXT_JS = """() => [...document.querySelectorAll('.cdk-overlay-pane, [role=menu], [role=dialog]')]
  .filter(e => e.offsetParent !== null)
  .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ')).join(' | ')"""


def price_from(text: str) -> int | None:
    match = PRICE_RE.search(text or "")
    return int(match.group(1)) if match else None


def ensure_price(actual: int | None, expected: int) -> None:
    if actual is None:
        raise RuntimeError(
            f"refusing to submit: could not read the composer price line, expected {expected} credits"
        )
    if actual != expected:
        raise RuntimeError(
            f"refusing to submit: composer says {actual} credits but the shot is planned at {expected}; "
            "check the mode, aspect and count chips"
        )


def pick_output(fresh: list[dict[str, Any]], prompt: str) -> dict[str, Any] | None:
    videos = [r for r in fresh if r.get("kind") == "video"]
    if not videos:
        return None
    needle = prompt.strip()[:40].lower()
    for row in sorted(videos, key=lambda r: r.get("created") or 0, reverse=True):
        if needle and needle in (row.get("prompt") or "").lower():
            return row
    return max(videos, key=lambda r: r.get("created") or 0)


def _flat(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def matching_outputs(
    fresh: list[dict[str, Any]], prompt: str, sent: str | None = None
) -> list[dict[str, Any]]:
    """The new videos whose prompt IS the job's prompt, as typed or as the prompt box read it.

    Flow stores a chip's title and the typed text joined by spaces (measured in out/records_now.json), so the box text
    read right before the click is what a record of a job with chips holds. A record that merely contains the prompt,
    a short prompt inside another job's or a style line scenes of one ad share, is never taken (review F3, 2026-09-16).
    """
    exact = {_flat(prompt), _flat(sent)} - {""}
    return [r for r in fresh if r.get("kind") == "video" and _flat(r.get("prompt")) in exact]


def _job_digest(job_id: str | None) -> str:
    """A job's files are named from this, never from the job_id an agent chose (review F2, 2026-09-16)."""
    return hashlib.sha1((job_id or "").encode("utf-8")).hexdigest()[:12]


REPLY_READ_S = 10.0
SMALL_REPLY = 20_000
MEASURED_STATUSES = (STATUS_DONE, STATUS_RUNNING, STATUS_SUBMITTED)
_REASON_RE = re.compile(r"PUBLIC_ERROR_[A-Z0-9_]+")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_ANY_UUID_RE = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{120,}")
# The same signed-query rule as gflow's data/redaction.py, applied to a whole text rather than its first 500 characters.
_SIGNED_RE = re.compile(r"\S*(?:signature=|x-goog-signature=|x-goog-credential=|expires=)\S*", re.IGNORECASE)


def _records_in(node: Any) -> list[list[Any]]:
    """Every generation record in a reply, shaped [workflow_id, project_id, media_id, "CAE", ...] (gflow
    batchexecute.py), where gflow's own parser returns only the first."""
    if not isinstance(node, list):
        return []
    if (
        len(node) >= 6
        and node[3] == "CAE"
        and all(isinstance(node[i], str) and _UUID_RE.match(node[i]) for i in (0, 1, 2))
    ):
        return [node]
    return [record for child in node for record in _records_in(child)]


def _status_of(record: list[Any]) -> int | None:
    details = record[5] if isinstance(record[5], list) else []
    cell = details[8] if len(details) > 8 and isinstance(details[8], list) and details[8] else None
    return cell[0] if cell and isinstance(cell[0], int) else None


def _redacted(text: str) -> str:
    return _SIGNED_RE.sub("<redacted:url>", _LONG_TOKEN_RE.sub("<token>", text))


def _head(text: str) -> str:
    return redact_error_detail(_redacted(text))


def _decoded(text: str) -> str:
    """A reply as its decoded frames, so an escape like \\u003d cannot hide a signed query from the redaction."""
    frames = parse_frames(text)
    return " ".join(json.dumps(payload) for _, payload in frames) if frames else text


def _job_codes(node: Any, job: set[str], known: set[str], mine: bool = False) -> set[str]:
    """PUBLIC_ERROR codes in the parts of a reply about the job: a list or string whose ids include the job's and no id
    beyond the job's workflow, media and project; a part with no id belongs to the nearest one that has ids."""
    if isinstance(node, str):
        ids = set(_ANY_UUID_RE.findall(node))
        if ids:
            mine = bool(ids & job) and ids <= known
        return set(_REASON_RE.findall(node)) if mine else set()
    if not isinstance(node, list):
        return set()
    ids = {item for item in node if isinstance(item, str) and _UUID_RE.match(item)}
    if ids:
        mine = bool(ids & job) and ids <= known
    return {code for child in node for code in _job_codes(child, job, known, mine)}


def _about_the_job(rpcids: str) -> bool:
    return any(rpcid in mc.SUBMIT_RPCS or rpcid in mc.STATUS_RPCS for rpcid in rpcids.split(","))


class FlowReplies:
    """What Flow's own replies said about the submitted job, heard from the click until its outcome row is written.

    The submit reply names the job's workflow and the status replies carry its status (gflow batchexecute.py: 6
    submitted, 2 running, 3 done; gflow has never captured a failure, so any other status is kept as unmeasured).
    Measured 2026-09-17 (plan E, L3): Flow rendered a job to 23%, then dropped it with no record, no charge and nothing
    left on the page, so these replies are the only place a reason can show up. A PUBLIC_ERROR code is this job's only
    inside its own record or in a reply naming it and no other workflow; every other code heard is listed apart.
    """

    def __init__(self) -> None:
        self.heard: set[str] = set()
        self._reads: list[asyncio.Future[tuple[str, str]]] = []

    def on_response(self, response: Any) -> None:
        url = str(getattr(response, "url", "") or "")
        if "batchexecute" not in url:
            return
        rpcids = [rpcid for rpcid in parse_qs(urlsplit(url).query).get("rpcids", [""])[0].split(",") if rpcid]
        self.heard.update(rpcids)
        self._reads.append(asyncio.ensure_future(self._keep(response, ",".join(rpcids))))

    @staticmethod
    async def _keep(response: Any, rpcids: str) -> tuple[str, str]:
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001
            return rpcids, ""
        kept = _about_the_job(rpcids) or "PUBLIC_ERROR" in text or len(text) <= SMALL_REPLY
        return rpcids, text if kept else ""

    async def report(self) -> dict[str, Any]:
        """Never raises: it runs after a paid click, where an exception would cost the outcome row."""
        try:
            bodies: list[tuple[str, str]] = []
            if self._reads:
                done, pending = await asyncio.wait(self._reads, timeout=REPLY_READ_S)
                for read in pending:
                    read.cancel()
                bodies = [read.result() for read in self._reads if read in done and not read.cancelled()]
            return self._judge([(rpcids, text) for rpcids, text in bodies if text])
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {str(exc)[:160]}", "heard": sorted(self.heard)}

    def _judge(self, bodies: list[tuple[str, str]]) -> dict[str, Any]:
        replies = [
            (rpcid, record)
            for rpcids, text in bodies
            if _about_the_job(rpcids)
            for rpcid, payload in parse_frames(text)
            for record in _records_in(payload)
        ]
        submitted = next((record for rpcid, record in replies if rpcid in mc.SUBMIT_RPCS), None)
        workflow, media = (submitted[0], submitted[2]) if submitted else (None, None)
        job = {ident for ident in (workflow, media) if ident}
        known = {*job, *((submitted[1],) if submitted else ())}
        statuses: list[int | None] = []
        unmeasured = None
        reasons: set[str] = set()
        for rpcid, record in replies:
            if record[0] != workflow:
                continue
            status = _status_of(record)
            if not statuses or statuses[-1] != status:
                statuses.append(status)
            dumped = json.dumps(record)
            reasons.update(_REASON_RE.findall(dumped))
            if unmeasured is None and status is not None and status not in MEASURED_STATUSES:
                unmeasured = {"rpcid": rpcid, "status": status, "head": _head(dumped)}
        named_by: dict[str, str] = {}
        for rpcids, text in bodies:
            if not any(ident in text for ident in job):
                continue
            decoded = _decoded(text)
            if set(_ANY_UUID_RE.findall(decoded)) <= known:
                reasons.update(_REASON_RE.findall(decoded))
            else:
                for _, payload in parse_frames(text):
                    reasons.update(_job_codes(payload, job, known))
            if not _about_the_job(rpcids):
                safe = _redacted(decoded)
                first = min((at for at in (safe.find(ident) for ident in job) if at >= 0), default=0)
                named_by.setdefault(rpcids, redact_error_detail(safe[max(0, first - 150) : first + 350]))
        heard = {code for _, text in bodies for code in _REASON_RE.findall(text)}
        return {
            "workflow_id": workflow,
            "media_id": media,
            "statuses": statuses,
            "unmeasured": unmeasured,
            "reasons": sorted(reasons),
            "reasons_elsewhere": sorted(heard - reasons),
            "named_by": named_by,
            "heard": sorted(self.heard),
        }


def _flow_said(flow: dict[str, Any]) -> str:
    if flow.get("error"):
        return f"Flow's replies could not be read ({flow['error']})"
    workflow = flow.get("workflow_id")
    if not workflow:
        said = "no submit reply from Flow was heard"
    elif flow.get("statuses"):
        last = flow["statuses"][-1]
        odd = (flow.get("unmeasured") or {}).get("status")
        prefix = "unmeasured " if last is not None and last not in MEASURED_STATUSES else ""
        said = f"Flow last reported {prefix}status {last} for workflow {workflow}"
        if odd is not None and odd != last:
            said += f" after unmeasured status {odd}"
    else:
        said = f"Flow named workflow {workflow} and sent no status"
    if flow.get("reasons"):
        said += f" with {', '.join(flow['reasons'])}"
    if flow.get("named_by"):
        said += f"; other replies naming it: {', '.join(sorted(flow['named_by']))}"
    if flow.get("reasons_elsewhere"):
        said += f"; codes heard in other replies: {', '.join(flow['reasons_elsewhere'])}"
    return said


async def _click_option(page: Any, label: str) -> bool:
    option = (
        page.locator("[role=menuitem], [role=option], .cdk-overlay-pane button")
        .filter(has_text=re.compile(re.escape(label), re.IGNORECASE))
        .first
    )
    if await option.count() == 0:
        return False
    await option.click(timeout=8_000)
    await page.wait_for_timeout(1_200)
    return True


_PRICE_VISIBLE_JS = (
    "() => [...document.querySelectorAll('.cdk-overlay-pane, [role=menu], [role=dialog]')]"
    ".some(e => e.offsetParent !== null && /generating will use/i.test(e.innerText || ''))"
)


async def _open_settings(page: Any, label: str = "settings") -> str:
    """Open the settings panel and wait for its live price line, whatever overlay was open before.

    The trigger toggles, so a panel left open from an earlier step would be closed by the click; Escape
    first, then click, then wait for the price line to actually render. Retries the toggle once.
    """
    for attempt in range(2):
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
        trigger = page.locator(SETTINGS).first
        await trigger.wait_for(state="visible", timeout=20_000)
        await trigger.click(timeout=8_000)
        try:
            await page.wait_for_function(_PRICE_VISIBLE_JS, timeout=12_000)
            return await page.evaluate(_OVERLAY_TEXT_JS)
        except PlaywrightTimeoutError:
            if attempt == 0:
                continue
            visible = await page.evaluate(_OVERLAY_TEXT_JS)
            await page.screenshot(path=f"out/t4_settings_fail_{label}.png")
            raise RuntimeError(
                f"composer settings never showed a price line at step {label!r}; "
                f"overlays said {visible[:200]!r} (screenshot out/t4_settings_fail_{label}.png)"
            ) from None
    raise RuntimeError(f"composer settings unreachable at step {label!r}")


async def configure(
    session: FlowSession,
    *,
    mode: str = "Ingredients",
    aspect: str = "9:16",
    count: str = "x1",
    label: str = "settings",
) -> dict[str, Any]:
    """Write every composer setting and report the price the UI now quotes."""
    page = session.page
    applied: dict[str, bool] = {}
    for attempt in range(2):
        await _open_settings(page, label)
        applied = {name: await _click_option(page, name) for name in (mode, aspect, count)}
        text = await page.evaluate(_OVERLAY_TEXT_JS)
        price = price_from(text)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(1_500)
        if await _mode_applied(page, mode):
            return {"applied": applied, "price": price, "settings_text": text[:300]}
        if attempt == 0:
            continue
        buttons = await page.evaluate(_COMPOSER_BUTTONS_JS)
        raise RuntimeError(
            f"composer did not switch to {mode!r} at step {label!r}; applied={applied}, "
            f"composer buttons={buttons}"
        )
    raise RuntimeError(f"composer unreachable at step {label!r}")


_COMPOSER_BUTTONS_JS = """() => [...document.querySelectorAll('flow-prompt-box button, flow-base-prompt-box button')]
  .map(e => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 34))
  .filter(Boolean)"""


def _names(labels: list[str]) -> set[str]:
    return {re.sub(r"\s+", " ", (label or "")).strip().lower() for label in labels}


def start_slot_filled(labels: list[str]) -> bool:
    """The Frames Start slot carries an image: its button renames itself to "Image ingredient, <file>"."""
    return any("image ingredient" in name for name in _names(labels))


def mode_visible(labels: list[str], mode: str) -> bool:
    """Read the composer mode off the buttons it renders, not off the chip that was clicked.

    Frames renders the Start and End frame slots plus the swap button; once a frame is pinned the Start
    slot renames itself. Ingredients renders the add-ingredient button. "Start generation" is the submit
    button and never counts: measured 2026-09-13, matching on an exact aria-label declared a working
    Frames composer broken, and matching loosely on "start" would hide a composer with no slots at all.
    """
    names = _names(labels)
    if mode == "Frames":
        return (
            bool(names & {"start", "end"})
            or any("swap first and last" in n for n in names)
            or start_slot_filled(labels)
        )
    if mode == "Ingredients":
        return any("ingredient" in name for name in names)
    return True


async def _mode_applied(page: Any, mode: str) -> bool:
    """Prove the mode really changed before the setup step waits 20s for a slot that will never come."""
    return mode_visible(await page.evaluate(_COMPOSER_BUTTONS_JS), mode)


_NOTICE_JS = """() => [...document.querySelectorAll(
  '[role=alert], [role=status], .mat-mdc-snack-bar-label, mat-snack-bar-container, [class*=snack], [class*=toast], [class*=error-message], [class*=rejection]')]
  .filter(e => e.offsetParent !== null)
  .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' '))
  .filter(t => t.length > 3).slice(0, 4).join(' | ')"""


async def snapshot(session: FlowSession, project_id: str, attempts: int = 4) -> tuple[list[Any], set[str]]:
    """Project listing, retried: a page load sometimes does not fire Zzl0ze at all, and crashing on that
    in the poll loop abandons a shot whose submit is already in flight."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await clips._snapshot(session, project_id)
        except LookupError as exc:
            last = exc
            if attempt < attempts - 1:
                await asyncio.sleep(10)
    raise last


async def fetch_720(session: FlowSession, record: dict[str, Any], stem: Path, attempts: int = 6) -> Path:
    """Insist on the 720p rendition before accepting anything smaller.

    `=m22` answers 404 for a while after the record says done; taking `=m18` immediately leaves a
    360x640 clip inside a 720x1280 cut (measured 2026-09-13 on tryon-05).
    """
    for attempt in range(attempts):
        try:
            return await download_mod.fetch_to_file(session.page.request, record["url"] + "=m22", stem)
        except RuntimeError:
            if attempt < attempts - 1:
                await asyncio.sleep(15)
    return await clips._fetch_with_retry(session.page.request, record, stem)


_STATE_JS = """() => ({
  chips: [...document.querySelectorAll('flow-prompt-box [class*=chip], flow-base-prompt-box [class*=chip]')]
    .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 30)).filter(Boolean).length,
  thumbs: document.querySelectorAll('flow-prompt-box img, flow-base-prompt-box img').length,
  text: [...document.querySelectorAll('flow-prompt-box [contenteditable=true]')]
    .map(e => (e.innerText || '').trim()).join('').length,
})"""


async def _type_prompt(page: Any, box: Any, prompt: str, attempts: int = 3, *, click: bool = True) -> bool:
    """Type the prompt and prove it is really in the box.

    An attached ingredient chip is enough to enable Start generation on its own, so a prompt that never
    landed submits an empty job: a request goes out, nothing is generated and nothing is billed
    (measured 2026-09-13 on tryon2-01, twice).

    click=False types once where the caret already is: a box holding chips is never clicked, since the click can
    land on a chip, and a second attempt without a click would only type the prompt twice.
    """
    needle = prompt.strip()[:40]
    for _ in range(attempts if click else 1):
        if click:
            await box.click(timeout=8_000)
        await page.keyboard.insert_text(prompt)
        await page.wait_for_timeout(2_000)
        if needle.lower() in (await box.inner_text()).lower():
            return True
    return False


async def clear_prompt(session: FlowSession) -> dict[str, Any]:
    """Empty the composer before building a shot.

    The composer keeps whatever the last run left in it: a pinned Start frame, an ingredient chip, old
    prompt text. Submitting on top of that leftover state produced a request that generated nothing and
    cost nothing (measured 2026-09-13 on tryon2-01).
    """
    page = session.page
    for _ in range(3):
        state = await page.evaluate(_STATE_JS)
        if not state["chips"] and not state["thumbs"] and not state["text"]:
            return state
        button = page.locator(
            "flow-prompt-box button[aria-label*='Clear'], flow-base-prompt-box button[aria-label*='Clear']"
        ).first
        if await button.count():
            await button.click(timeout=8_000)
            await page.wait_for_timeout(1_500)
            continue
        box = page.locator("flow-prompt-box [contenteditable='true']").first
        if await box.count():
            await box.click(timeout=8_000)
            await page.keyboard.press("Meta+A")
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(1_000)
        chip_x = page.locator(
            "flow-prompt-box [class*=chip] button, flow-base-prompt-box [class*=chip] button"
        ).first
        if await chip_x.count():
            await chip_x.click(timeout=8_000)
            await page.wait_for_timeout(1_000)
    return await page.evaluate(_STATE_JS)


async def _notice(page: Any) -> str:
    """Whatever Flow told the user (toast, snackbar, inline refusal). Transient, so read it early."""
    try:
        return (await page.evaluate(_NOTICE_JS))[:400]
    except Exception:  # noqa: BLE001
        return ""


def _in_picker(page: Any, text: str) -> Any:
    return (
        page.locator(".cdk-overlay-pane, [role=dialog], [role=menu]")
        .locator("button, [role=tab], [role=option], [role=menuitem]")
        .filter(has_text=re.compile(re.escape(text), re.IGNORECASE))
        .first
    )


async def attach_character(session: FlowSession, name: str) -> bool:
    """Open the ingredients picker, switch to Characters and add the entity to the prompt.

    The picker renders lazily (measured: several seconds), so each step waits for its own element
    instead of sleeping a fixed amount.
    """
    page = session.page
    add = page.locator(INGREDIENTS).first
    await add.wait_for(state="visible", timeout=20_000)
    await add.click(timeout=8_000)
    tab = _in_picker(page, "Characters")
    try:
        await tab.wait_for(state="visible", timeout=20_000)
    except PlaywrightTimeoutError as exc:
        raise RuntimeError("ingredients picker never showed a Characters tab") from exc
    await tab.click(timeout=8_000)
    tile = _in_picker(page, name)
    try:
        await tile.wait_for(state="visible", timeout=20_000)
    except PlaywrightTimeoutError as exc:
        raise LookupError(f"character {name!r} is not in the ingredients picker") from exc
    await tile.click(timeout=8_000)
    await page.wait_for_timeout(2_500)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1_000)
    return True


_LEFT_JS = """() => ({
  mentions: document.querySelectorAll('flow-prompt-box .mention-chip').length,
  text: [...document.querySelectorAll("flow-prompt-box [contenteditable='true']")]
    .map(e => e.innerText || '').join('').trim(),
})"""


async def _submit(
    session: FlowSession,
    project_id: str,
    *,
    prompt: str,
    setup,
    kind: str,
    job_id: str | None,
    expected_credits: int,
    out_dir: Path,
    aspect: str = "9:16",
    mode: str = "Ingredients",
    wait: float = 300.0,
    dry_run: bool = False,
    watch: Any = None,
    strict_output: bool = False,
    verify: Any = None,
    click_box: bool = True,
) -> dict[str, Any]:
    """The one money path: every mode goes through the same price guard, single click and ledger.

    dry_run stops once the price is read: no balance read, no ledger row, no click, and the composer is emptied again.
    watch hears every request of the click window and its report lands in the outcome row. strict_output takes only
    a new video whose prompt is the prompt, or the `prompt_text` verify read from the box, never picks between two,
    and leaves a job it cannot settle while a new video or a moved balance says money may be gone `unknown`, every
    new video a candidate. Whatever setup returns joins the intent row, and whatever verify returns, read right before
    the price check, replaces it. click_box=False types the prompt where the caret already is. A refusal before the click empties the composer again; anything that goes wrong
    after the click still writes an outcome row and says credits may be spent.
    """
    ledger = gen.Ledger(out_dir / "ledger.jsonl")
    before: set[str] = set()
    credits_before = None
    if not dry_run:
        if any(r.get("status") == "done" for r in ledger.rows(job_id)):
            raise gen.AlreadySubmitted(f"job {job_id} is already done; delete its output to redo it")
        rows, _ = await snapshot(session, project_id)
        before = {r["workflow_id"] for r in rows}
        credits_before = (await reader.credits(session))["balance"]

    await agent.set_mode(session, project_id, False)
    await session.goto(session.project_url(project_id), ready=PROJECT_READY)
    await session.page.wait_for_timeout(2_500)
    left_over = await clear_prompt(session)
    try:
        settings = await configure(session, mode=mode, aspect=aspect, label="pre")
        extra = await setup(session) or {}
        box = session.page.locator("flow-prompt-box [contenteditable='true']").first
        landed = await _type_prompt(session.page, box, prompt, click=click_box)
        if not landed:
            raise RuntimeError(
                f"{job_id}: the prompt never reached the composer box, refusing to submit an empty job"
            )
        confirm = await configure(session, mode=mode, aspect=aspect, label="confirm")
        checked = await verify(session) if verify is not None else {}
        if dry_run:
            await clear_prompt(session)
            return {
                "dry_run": True,
                "kind": kind,
                "quoted_credits": confirm["price"],
                "expected_credits": expected_credits,
                "price_ok": confirm["price"] == expected_credits,
                "composer_left": await session.page.evaluate(_LEFT_JS),
                **extra,
                **checked,
            }
        ensure_price(confirm["price"], expected_credits)
    except Exception:
        # Nothing was clicked: leave the shared composer as empty as this run found it (review F4, 2026-09-16).
        with contextlib.suppress(Exception):
            await clear_prompt(session)
        raise

    start = session.page.get_by_role("button", name=re.compile("Start generation", re.IGNORECASE)).first
    ledger.append(
        job_id,
        "submitted",
        kind=kind,
        project=project_id,
        prompt=prompt[:200],
        quoted_credits=confirm["price"],
        credits_before=credits_before,
        left_over=left_over,
        prompt_landed=landed,
        **{**extra, **checked},
    )
    digest = _job_digest(job_id)
    frames: dict[str, list[Any]] | None = None
    output, fresh, candidates = None, [], []
    replies = FlowReplies()
    session.page.on("response", replies.on_response)
    try:
        if watch is not None:
            session.page.on("request", watch.on_request)
        try:
            frames = await clips._await_submit(session, lambda: start.click(timeout=8_000))
        finally:
            if watch is not None:
                session.page.remove_listener("request", watch.on_request)
        notice = await _notice(session.page)
        # Flow can take the click, fire the rpcids, create nothing and charge nothing; the only way to see
        # why is to look at the screen while the refusal is still on it (measured 2026-09-13 on tryon2-04).
        with contextlib.suppress(Exception):
            await session.page.screenshot(path=str(out_dir / f"submitted_{digest}.png"))

        deadline = asyncio.get_running_loop().time() + wait
        while True:
            rows, _ = await snapshot(session, project_id)
            fresh = clips.new_records(before, rows)
            if strict_output:
                candidates = matching_outputs(fresh, prompt, checked.get("prompt_text"))
                output = candidates[0] if len(candidates) == 1 else None
                if len(candidates) > 1:
                    break
            else:
                output = pick_output(fresh, prompt)
            if output is not None and clips.is_done(output):
                break
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(15)

        credits_after = (await reader.credits(session))["balance"]
        path, fetch_error = None, None
        if output is not None and clips.is_done(output):
            try:
                path = str(await fetch_720(session, output, out_dir / f"{output['id']}_{digest[:8]}"))
            except Exception as exc:  # noqa: BLE001
                fetch_error = f"{type(exc).__name__}: {str(exc)[:160]}"
    except Exception as exc:
        session.page.remove_listener("response", replies.on_response)
        credits_now = None
        with contextlib.suppress(Exception):
            credits_now = (await reader.credits(session))["balance"]
        detail = f"{type(exc).__name__}: {str(exc).split('Call log:')[0].strip()[:200]}"
        flow = await replies.report()
        ledger.append(
            job_id,
            "unknown",
            error=detail,
            credits_before=credits_before,
            credits_after=credits_now,
            spent=None if credits_now is None else credits_before - credits_now,
            # None, not []: an empty list is how this repo reads a click that sent nothing (re-review E, 2026-09-17).
            rpcids=None if frames is None else sorted(frames),
            **({"body_check": watch.report()} if watch is not None else {}),
            flow=flow,
        )
        # The advice leads: an agent sees at most 500 characters and a job_id can be long (re-review A, 2026-09-17).
        raise RuntimeError(
            "Start generation was clicked, so credits may already be spent: check flow_media and flow_credits and "
            f"never run this job again under a new job_id; it then failed with {detail}; {_flow_said(flow)}; "
            f"job {job_id}"
        ) from exc
    session.page.remove_listener("response", replies.on_response)
    flow = await replies.report()

    spent = credits_before - credits_after
    videos = [r for r in fresh if r.get("kind") == "video"]
    unclaimed = candidates if len(candidates) > 1 else videos
    watched = {"body_check": watch.report()} if watch is not None else {}
    if path:
        status = "done"
    elif output is not None:
        status = "pending"
    elif strict_output and (unclaimed or spent):
        # A new video or a moved balance is never "nothing was generated" (re-review B, 2026-09-17).
        status = "unknown"
    else:
        status = "failed"
    ledger.append(
        job_id,
        status,
        media_id=output["id"] if output else None,
        path=path,
        credits_before=credits_before,
        credits_after=credits_after,
        spent=spent,
        rpcids=sorted(frames),
        notice=notice or (await _notice(session.page)),
        **watched,
        **({"error": fetch_error} if fetch_error else {}),
        **({"candidates": [c["id"] for c in unclaimed]} if status == "unknown" else {}),
        flow=flow,
    )
    if status == "pending":
        why = fetch_error or f"still rendering after {wait:.0f}s"
        raise RuntimeError(
            f"do not run this job again: the clip {output['id']} exists but no file came back ({why}); fetch it "
            f"with flow_download once it has finished; spent {spent} credits; job {job_id}"
        )
    if status == "unknown" and unclaimed:
        raise RuntimeError(
            "check flow_media and never run this job again under a new job_id: "
            f"{len(unclaimed)} new records could be this job's clip ({[c['id'] for c in unclaimed]}), so none is "
            f"taken; spent {spent} credits; {_flow_said(flow)}; job {job_id}"
        )
    if status == "unknown":
        raise RuntimeError(
            "check flow_media and flow_credits and never run this job again under a new job_id: no new video showed "
            f"up within {wait:.0f}s, yet the balance moved by {spent} credits; {_flow_said(flow)}; job {job_id}"
        )
    if status == "failed":
        raise RuntimeError(
            f"nothing was generated within {wait:.0f}s, spent {spent} credits; {_flow_said(flow)}; rpcids "
            f"{sorted(frames)}; settings {settings['applied']}; Flow said: {notice or '(no message captured)'}; "
            f"job {job_id}"
        )
    return {
        "job_id": job_id,
        "kind": kind,
        "media_id": output["id"],
        "path": path,
        "quoted_credits": confirm["price"],
        "credits_before": credits_before,
        "credits_after": credits_after,
        "spent": spent,
        **extra,
        **checked,
        **watched,
    }


START_SLOT_NAME = re.compile(r"^(start|image ingredient.*)$", re.IGNORECASE)


def start_slot(page: Any) -> Any:
    """The Frames Start slot, found by accessible name so a label carried in text still matches.

    The name is anchored: "Start generation" is the submit button, and clicking it here would pay for a
    shot with no start frame.
    """
    return (
        page.locator("flow-prompt-box, flow-base-prompt-box")
        .get_by_role("button", name=START_SLOT_NAME)
        .first
    )


async def pin_start_frame(session: FlowSession, name: str) -> bool:
    """Pin a project image into the Frames Start slot, found by name through the picker's search box.

    Measured 2026-09-13: the slot opens a "Select a frame image" picker listing project images with no
    text on the tiles, so the filename is typed into the search field and the first result is taken.
    """
    page = session.page
    slot = start_slot(page)
    await slot.wait_for(state="visible", timeout=20_000)
    await slot.click(timeout=8_000)
    await page.wait_for_timeout(3_000)
    search = page.locator(".cdk-overlay-pane input, [role=dialog] input").first
    if await search.count():
        await search.click(timeout=8_000)
        await page.keyboard.insert_text(name)
        await page.wait_for_timeout(3_000)
    tile = page.locator(".cdk-overlay-pane img, [role=dialog] img").first
    try:
        await tile.wait_for(state="visible", timeout=20_000)
    except PlaywrightTimeoutError as exc:
        raise LookupError(f"no frame image matched {name!r} in the picker") from exc
    await tile.click(timeout=8_000)
    await page.wait_for_timeout(3_000)
    pinned = start_slot_filled(await page.evaluate(_COMPOSER_BUTTONS_JS))
    if not pinned:
        raise RuntimeError(f"picked {name!r} but the Start slot stayed empty")
    return True


async def generate_with_character(
    session: FlowSession, project_id: str, *, character: str, **kwargs: Any
) -> dict[str, Any]:
    async def setup(s: FlowSession) -> None:
        await attach_character(s, character)

    return await _submit(session, project_id, setup=setup, kind="character", mode="Ingredients", **kwargs)


async def generate_from_frame(
    session: FlowSession, project_id: str, *, start_name: str, **kwargs: Any
) -> dict[str, Any]:
    async def setup(s: FlowSession) -> None:
        await pin_start_frame(s, start_name)

    return await _submit(session, project_id, setup=setup, kind="frames", mode="Frames", **kwargs)
