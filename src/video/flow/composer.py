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
import base64
import contextlib
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.api.transports.batchexecute import STATUS_DONE, STATUS_RUNNING, STATUS_SUBMITTED, parse_frames
from gflow_cli.data.redaction import redact_error_detail
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video import gen, outcome
from video.flow import agent, clips, overlays, reader
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


def ensure_within(actual: int | None, cap: int) -> None:
    """gen_video's guard (DECISIONS 2026-09-29): Flow's own price line, read right before the click, against a cap."""
    if actual is None:
        raise RuntimeError(f"refusing to submit: could not read the composer price line (max_credits {cap})")
    if actual > cap:
        raise RuntimeError(
            f"refusing to submit: the composer says {actual} credits, over max_credits {cap}; nothing was clicked"
        )


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
# Measured 2026-09-17 (plan E, L4): statuses 6, 2, 4 with PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED, no charge.
STATUS_FAILED = 4
MEASURED_STATUSES = (STATUS_DONE, STATUS_RUNNING, STATUS_SUBMITTED, STATUS_FAILED)
_REASON_RE = re.compile(r"PUBLIC_ERROR_[A-Z0-9_]+")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_ANY_UUID_RE = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{120,}")
# gflow's data/redaction.py signed-query rule, tested token by token: its \S*(...)\S* is quadratic on unspaced text.
_SIGNED_KEY_RE = re.compile(r"signature=|x-goog-signature=|x-goog-credential=|expires=", re.IGNORECASE)
_WORD_RE = re.compile(r"\S+")


def _record_version(token: Any) -> int | None:
    """The fourth field of a generation record: base64 of the protobuf {1: version} of its media, "CAE" for the media's
    own generation (1), "CAI" for its first edit (2), "CAM" for its second (3), measured 2026-09-28. It is not a type:
    reading it as one heard a clip's second edit as nothing (plan-t-edit-1, 20 credits)."""
    if not isinstance(token, str) or not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except ValueError:
        return None
    if len(raw) < 2 or raw[0] != 0x08:
        return None
    value, shift = 0, 0
    for byte in raw[1:]:
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value or None
        shift += 7
    return None


def _records_in(node: Any, edits: bool = False) -> list[list[Any]]:
    """Every generation record in a reply, shaped [workflow_id, project_id, media_id, <version>, ...] (gflow
    batchexecute.py), where gflow's own parser returns only the first. A generation is always its media's version 1,
    which Flow wrote as "CAE" until 2026-10-05 and as null since (every started row from 2026-10-06 heard the submit
    rpc and named no workflow until this read null); an edit is a later version of its source clip, heard only when
    `edits` is asked for, and with null versions an edit cannot be told from its source's generation, so the edit
    reader keeps to versions it can read."""
    if not isinstance(node, list):
        return []
    if len(node) >= 6 and all(isinstance(node[i], str) and _UUID_RE.match(node[i]) for i in (0, 1, 2)):
        version = _record_version(node[3])
        if version == 1 or (not edits and node[3] is None) or (edits and version is not None and version > 1):
            return [node]
    return [record for child in node for record in _records_in(child, edits)]


def _status_of(record: list[Any]) -> int | None:
    details = record[5] if isinstance(record[5], list) else []
    cell = details[8] if len(details) > 8 and isinstance(details[8], list) and details[8] else None
    return cell[0] if cell and isinstance(cell[0], int) else None


def _redacted(text: str) -> str:
    return _WORD_RE.sub(
        lambda word: "<redacted:url>" if _SIGNED_KEY_RE.search(word.group()) else word.group(),
        _LONG_TOKEN_RE.sub("<token>", text),
    )


def _head(text: str) -> str:
    return redact_error_detail(_redacted(text))


def _decoded(text: str) -> str:
    """A reply as its decoded frames, so an escape like \\u003d cannot hide a signed query from the redaction."""
    frames = parse_frames(text)
    if frames:
        return " ".join(json.dumps(payload) for _, payload in frames)
    return text.replace("\\u003d", "=").replace("\\u0026", "&")


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


GFLOW_RPCS = (*mc.SUBMIT_RPCS, *mc.STATUS_RPCS)
# Measured 2026-09-28 (plan N, a paid clip_edit): the editor submits with jIps6, which gflow's rpc sets never name.
EDITOR_SUBMIT = "jIps6"


def _about_the_job(rpcids: str, job_rpcs: tuple[str, ...] = GFLOW_RPCS) -> bool:
    return any(rpcid in job_rpcs for rpcid in rpcids.split(","))


CAPTURE_ENV = "VIDEO_CAPTURE_REPLIES"


def _capture(rpcids: str, text: str) -> None:
    """Write a reply Flow sent, raw, into the folder VIDEO_CAPTURE_REPLIES names: the fixtures that pin the reader are
    built from replies Flow sent, never typed (a typed "CAE" in every fixture hid a week of unread replies)."""
    folder = os.environ.get(CAPTURE_ENV)
    if not folder:
        return
    try:
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{rpcids.replace(',', '+')}_{time.time_ns()}.txt").write_text(text, encoding="utf-8")
    except OSError:
        return


def _reply_read(flow: dict[str, Any], frames: Any) -> bool | None:
    """Whether Flow's submit reply was read: None when no submit rpc was heard, else whether a workflow was named."""
    if not any(rpcid in mc.SUBMIT_RPCS for rpcid in (frames or ())):
        return None
    return bool(flow.get("workflow_id"))


REPLY_UNREAD = (
    "Flow's submit reply was heard but named no workflow, so the shape of its replies may have changed: the job is "
    "settled by the listing and its clip found by its prompt; run `video flow check` and refresh the reply fixtures"
)


def _reply_note(flow: dict[str, Any], frames: Any) -> str:
    return f"; {REPLY_UNREAD}" if _reply_read(flow, frames) is False else ""


def _reply_fields(flow: dict[str, Any], frames: Any) -> dict[str, Any]:
    read = _reply_read(flow, frames)
    return {"flow_reply_read": read, **({"flow_reply_note": REPLY_UNREAD} if read is False else {})}


class FlowReplies:
    """What Flow's own replies said about the submitted job, heard from the click until its outcome row is written.

    The submit reply names the job's workflow and the status replies carry its status (gflow batchexecute.py: 6
    submitted, 2 running, 3 done; gflow has never captured a failure, so any other status is kept as unmeasured).
    Measured 2026-09-17 (plan E, L3): Flow rendered a job to 23%, then dropped it with no record, no charge and nothing
    left on the page, so these replies are the only place a reason can show up. A PUBLIC_ERROR code is this job's only
    inside its own record or in a reply naming it and no other workflow; every other code heard is listed apart.
    """

    def __init__(self, *, editor: bool = False, source_media: str | None = None) -> None:
        self.heard: set[str] = set()
        self.workflow: str | None = None
        # Only the editor reader hears the editor's submit rpc and edit records: on the gen path, whose replies are the
        # measured ones, neither may change a status, a reason or the wait (review F3, 2026-09-28).
        self.job_rpcs: tuple[str, ...] = (*GFLOW_RPCS, EDITOR_SUBMIT) if editor else GFLOW_RPCS
        self.edits = editor
        self.source_media = source_media
        self._reads: list[asyncio.Future[tuple[str, str]]] = []

    def about(self, workflow_id: str | None) -> None:
        """Which workflow is the job, when Flow's submit reply cannot say it: the editor submits with an rpc gflow
        never captured (jIps6, measured 2026-09-28 on a paid clip_edit, which is why that run heard 20 rpcids and
        learned nothing), so the caller names the job from its own listing instead. The name decides which records
        are read, never what they say: it is only adopted once a reply heard HERE carries a record naming it, the
        report says so in `named_by_caller`, and a real submit reply always outranks it."""
        self.workflow = workflow_id or None

    def on_response(self, response: Any) -> None:
        url = str(getattr(response, "url", "") or "")
        if "batchexecute" not in url:
            return
        rpcids = [rpcid for rpcid in parse_qs(urlsplit(url).query).get("rpcids", [""])[0].split(",") if rpcid]
        self.heard.update(rpcids)
        self._reads.append(asyncio.ensure_future(self._keep(response, ",".join(rpcids))))

    async def _keep(self, response: Any, rpcids: str) -> tuple[str, str]:
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001
            return rpcids, ""
        kept = _about_the_job(rpcids, self.job_rpcs) or "PUBLIC_ERROR" in text or len(text) <= SMALL_REPLY
        if kept and text:
            _capture(rpcids, text)
        return rpcids, text if kept else ""

    def reported_failed(self) -> bool:
        """Whether a reply already read says Flow failed the job (status 4); never waits for a body still arriving and
        never raises, since it runs inside the wait after a paid click."""
        try:
            bodies = [read.result() for read in self._reads if read.done() and not read.cancelled()]
            flow = self._judge([(rpcids, text) for rpcids, text in bodies if text])
            # A name only an edit record gave may be an earlier edit's (review of plan T, F1): it never ends a paid wait.
            if flow["named_by_editor_submit"] and not flow["named_by_caller"]:
                return False
            statuses = flow["statuses"]
            return bool(statuses) and statuses[-1] == STATUS_FAILED
        except Exception:  # noqa: BLE001
            return False

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

    def _no_reply_about(
        self, replies: list[tuple[str, Any]], bodies: list[tuple[str, str]]
    ) -> dict[str, Any]:
        """The trail left when the caller named a workflow and no reply heard here carried a record for it.

        Measured 2026-09-28 across three paid editor runs (60 credits): that is what happens on every one of them, so
        the next paid editor job answers for free which workflows those replies DO name and what the editor's own
        submit reply holds, instead of costing a run of its own to ask again.
        """
        editor = next((text for rpcids, text in bodies if EDITOR_SUBMIT in rpcids.split(",")), "")
        return {
            "workflow": self.workflow,
            "records_named": sorted({f"{rpcid}:{record[0]}" for rpcid, record in replies}),
            "editor_reply": _head(editor)[:1200],
        }

    def _judge(self, bodies: list[tuple[str, str]]) -> dict[str, Any]:
        replies = [
            (rpcid, record)
            for rpcids, text in bodies
            if _about_the_job(rpcids, self.job_rpcs)
            for rpcid, payload in parse_frames(text)
            for record in _records_in(payload, self.edits)
        ]
        submitted = next((record for rpcid, record in replies if rpcid in mc.SUBMIT_RPCS), None)
        # The edit's own record in its submit reply, trusted only when it is the one edit record on the source clip.
        edits = {
            record[0]
            for rpcid, record in replies
            if rpcid == EDITOR_SUBMIT
            and (_record_version(record[3]) or 0) > 1
            and self.source_media
            and record[2] == self.source_media
        }
        edit_named = next(iter(edits)) if len(edits) == 1 else None
        told = self.workflow if any(record[0] == self.workflow for _, record in replies) else None
        # No media id from a name: the editor's output keeps the SOURCE clip's media id (measured 2026-09-28, the
        # paid edit's source and output are both 26f0e503), so adopting it would count the source's codes as ours.
        # Two sources naming two workflows is a guess either way, so neither is adopted (review of plan T, F1).
        conflict = sorted({edit_named, told}) if edit_named and told and edit_named != told else None
        workflow = submitted[0] if submitted else None if conflict else told or edit_named
        media = submitted[2] if submitted else None
        job = {ident for ident in (workflow, media) if ident}
        # The job's own records name its project and references too (the failed L4 record held its character and image).
        own = [record for _, record in replies if record[0] == workflow]
        known = job | {ident for record in own for ident in _ANY_UUID_RE.findall(json.dumps(record))}
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
            if not _about_the_job(rpcids, self.job_rpcs):
                safe = _redacted(decoded)
                first = min((at for at in (safe.find(ident) for ident in job) if at >= 0), default=0)
                named_by.setdefault(rpcids, redact_error_detail(safe[max(0, first - 150) : first + 350]))
        heard = {code for _, text in bodies for code in _REASON_RE.findall(text)}
        return {
            "workflow_id": workflow,
            "named_by_caller": bool(told and not submitted and not conflict),
            "named_by_editor_submit": bool(edit_named and not submitted and not conflict),
            "identity_conflict": conflict,
            # What the edit's submit reply carried, for the day it holds more than the job (one sample so far).
            "edit_records": sorted(
                f"{record[0]}:v{_record_version(record[3])}:{_status_of(record)}:"
                f"{'source' if record[2] == self.source_media else 'other'}"
                for rpcid, record in replies
                if rpcid == EDITOR_SUBMIT
            ),
            "told_unmatched": self._no_reply_about(replies, bodies) if self.workflow and not told else None,
            "media_id": media,
            "statuses": statuses,
            "unmeasured": unmeasured,
            "reasons": sorted(reasons),
            "reasons_elsewhere": sorted(heard - reasons),
            "named_by": named_by,
            "heard": sorted(self.heard),
        }


def _page_note(url: str | None, project_id: str) -> str:
    """Where the page stood right after the click, when that was no longer the project (sg-bf-2, 2026-09-17)."""
    if not url or urlsplit(url).path.rstrip("/").endswith(f"/project/{project_id}"):
        return ""
    return f"; the page had moved to {url} after the click"


def flow_said(flow: dict[str, Any]) -> str:
    if flow.get("error"):
        return f"Flow's replies could not be read ({flow['error']})"
    workflow = flow.get("workflow_id")
    if not workflow:
        said = "no submit reply from Flow was heard"
    elif flow.get("statuses"):
        last = flow["statuses"][-1]
        odd = (flow.get("unmeasured") or {}).get("status")
        if last == STATUS_FAILED:
            said = f"Flow failed workflow {workflow} (status {last})"
        else:
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
        try:
            await trigger.click(timeout=8_000)
        except PlaywrightTimeoutError:
            # Measured 2026-10-01: a bar over the composer's bottom row left only this timeout to read.
            cover = await overlays.covering(page, SETTINGS)
            if cover is None:
                raise
            where = cover["tag"] + (f"#{cover['id']}" if cover.get("id") else "")
            raise LookupError(
                f"the composer's Settings trigger is covered by {where}, which says {cover['text']!r}; "
                "nothing was clicked"
            ) from None
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


_VIDEO_TAB = re.compile(r"^\W*[a-z_0-9]*\s*Video\s*$")
# One pattern for the click and the page-side check: the radio's textContent glues its icon name on ("videocamVideo").
_VIDEO_CHECKED_JS = (
    "() => [...document.querySelectorAll('.cdk-overlay-pane [role=radio]')].some(o => new RegExp("
    + json.dumps(_VIDEO_TAB.pattern)
    + ").test((o.textContent || '').trim()) && o.getAttribute('aria-checked') === 'true')"
)


async def _select_video(page: Any) -> bool:
    # gen_i2i leaves the panel on its Image tab, which offers no Frames and no Ingredients (measured 2026-09-30).
    tab = page.locator(".cdk-overlay-pane button[role=radio]").filter(has_text=_VIDEO_TAB).first
    if await tab.count() == 0:
        return False
    await tab.click(timeout=8_000)
    await page.wait_for_timeout(900)
    return True


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
        applied = {"Video": await _select_video(page)}
        applied |= {name: await _click_option(page, name) for name in (mode, aspect, count)}
        # The Ingredients composer renders the same buttons as the Image one, so only the tab tells them apart.
        on_video = bool(await page.evaluate(_VIDEO_CHECKED_JS))
        text = await page.evaluate(_OVERLAY_TEXT_JS)
        price = price_from(text)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(1_500)
        if on_video and await _mode_applied(page, mode):
            return {"applied": applied, "price": price, "settings_text": text[:300]}
        if attempt == 0:
            continue
        buttons = await page.evaluate(_COMPOSER_BUTTONS_JS)
        raise RuntimeError(
            f"composer did not switch to {mode!r} at step {label!r}; Video tab checked={on_video}, "
            f"applied={applied}, composer buttons={buttons}"
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

    Ingredients is told by its add button alone (measured 2026-10-02, out/al/t7_mode.json: "Add ingredients to the
    prompt box", absent in Frames). A chip on the bar is labelled "Ingredient" and a pinned frame "Image ingredient",
    so any label holding "ingredient" let a Frames composer with one image pass for Ingredients.
    """
    names = _names(labels)
    if mode == "Frames":
        return (
            bool(names & {"start", "end"})
            or any("swap first and last" in n for n in names)
            or start_slot_filled(labels)
        )
    if mode == "Ingredients":
        return any(name.startswith("add ingredient") for name in names)
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


async def fetch_720(
    session: FlowSession,
    record: dict[str, Any],
    stem: Path,
    attempts: int = 6,
    *,
    project_id: str | None = None,
) -> Path:
    """Insist on the 720p rendition before accepting anything smaller.

    `=m22` answers 404 for a while after the record says done; taking `=m18` immediately leaves a
    360x640 clip inside a 720x1280 cut (measured 2026-09-13 on tryon-05).
    """
    # A 360p run has no =m22 at all (measured 2026-09-29, key abra_t2v_4s_360p): =m18 IS its file.
    if str(record.get("model") or "").endswith("_360p"):
        return await clips._fetch_with_retry(session.page.request, record, stem)
    for attempt in range(attempts):
        try:
            return await download_mod.fetch_to_file(
                session.page.request, download_mod.asset_base(record["url"]) + "=m22", stem
            )
        except RuntimeError:
            if attempt < attempts - 1:
                await asyncio.sleep(15)
    try:
        return await clips._fetch_with_retry(session.page.request, record, stem)
    except RuntimeError as exc:
        # Every rendition link failed: 404 for a whole day on job lly-v5-s1c-reveal, 400 for a token the host will not
        # serve (both 2026-09-30); the editor's own Download serves the same clip for 0 credits.
        if "no rendition" not in str(exc) or project_id is None:
            raise
        # The editor names every download <media>_720p and refuses to overwrite, so each stem gets its own folder.
        return await clips.download_rendition(
            session,
            project_id,
            record["id"],
            "720p",
            stem.parent / stem.name,
            workflow_id=record.get("workflow_id"),
        )


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
  bar: document.querySelectorAll('flow-prompt-box flow-ingredient-bar button.chip-container').length,
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
    count: int = 1,
    max_credits: int | None = None,
    retry_of: str | None = None,
    detach: bool = False,
    started_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The one money path: every mode goes through the same price guard, single click and ledger.

    detach (plan AN) leaves once a submit request has been seen going out: it writes a `started` row holding what a
    later call needs to find the clip (the workflow Flow's reply named, the workflows listed before, the prompt, and
    `started_extra`, what the caller wants the clip held to) and returns; nothing is waited for, read or fetched.
    With no submit request seen, or once Flow has already failed the job in this page, it runs on as a blocking call
    does: this page is the only place Flow's reason is heard.

    dry_run stops once the price is read: no balance read, no ledger row, no click, and the composer is emptied again.
    watch hears every request of the click window and its report lands in the outcome row. strict_output takes only
    a new video whose prompt is the prompt, or the `prompt_text` verify read from the box, never picks between two,
    and leaves a job it cannot settle while a new video or a moved balance says money may be gone `unknown`, every
    new video a candidate. Whatever setup returns joins the intent row, and whatever verify returns, read right before
    the price check, replaces it. click_box=False types the prompt where the caret already is. A refusal before the click empties the composer again; anything that goes wrong
    after the click still writes an outcome row and says credits may be spent.
    """
    if detach and count != 1:
        # How Flow names the workflows of x2 to x4 was never measured, so a later call could not claim their clips.
        raise ValueError(
            f"a detached submit makes one clip, got count {count}: x2 to x4 go through gen_video"
        )
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
        settings = await configure(session, mode=mode, aspect=aspect, count=f"x{count}", label="pre")
        extra = await setup(session) or {}
        box = session.page.locator("flow-prompt-box [contenteditable='true']").first
        landed = await _type_prompt(session.page, box, prompt, click=click_box)
        if not landed:
            raise RuntimeError(
                f"{job_id}: the prompt never reached the composer box, refusing to submit an empty job"
            )
        confirm = await configure(session, mode=mode, aspect=aspect, count=f"x{count}", label="confirm")
        checked = await verify(session) if verify is not None else {}
        if dry_run:
            await clear_prompt(session)
            return {
                "dry_run": True,
                "kind": kind,
                "quoted_credits": confirm["price"],
                "expected_credits": expected_credits,
                "price_ok": (
                    confirm["price"] == expected_credits
                    if max_credits is None
                    else confirm["price"] is not None
                    and confirm["price"] <= max_credits
                    and (not expected_credits or confirm["price"] == expected_credits)
                ),
                **({"max_credits": max_credits} if max_credits is not None else {}),
                "composer_left": await session.page.evaluate(_LEFT_JS),
                **extra,
                **checked,
            }
        if max_credits is None:
            ensure_price(confirm["price"], expected_credits)
        else:
            ensure_within(confirm["price"], max_credits)
            if expected_credits and confirm["price"] != expected_credits:
                # A cell the survey priced must quote that price: less means a setting slipped (review of plan AB).
                raise RuntimeError(
                    f"refusing to submit: the composer says {confirm['price']} credits where the surveyed price for "
                    f"these settings is {expected_credits}; a setting did not take, or Flow changed its prices (then "
                    "run the Flow survey to update flow_options.json); nothing was clicked"
                )
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
        prompt=prompt[: outcome.PROMPT_KEPT],
        quoted_credits=confirm["price"],
        credits_before=credits_before,
        left_over=left_over,
        prompt_landed=landed,
        # The job Flow refused that this click retries (plan AM): on record before the click, so a second retry of
        # that job is refused off the ledger alone.
        **({"retry_of": retry_of} if retry_of else {}),
        # A detached run says so before the click (plan AN): cut off before its started row, it is still told from a
        # blocking run that crashed, and blocking runs of its prompt are held back while its clip may show.
        **({"detach": True} if detach else {}),
        **{**extra, **checked},
    )
    digest = _job_digest(job_id)
    frames: dict[str, list[Any]] | None = None
    output, fresh, candidates, page_after_click = None, [], [], None
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
        page_after_click = session.page.url
        notice = await _notice(session.page)
        # Flow can take the click, fire the rpcids, create nothing and charge nothing; the only way to see
        # why is to look at the screen while the refusal is still on it (measured 2026-09-13 on tryon2-04).
        with contextlib.suppress(Exception):
            await session.page.screenshot(path=str(out_dir / f"submitted_{digest}.png"))

        # The request is out, so the job is Flow's now and leaving the page cancels nothing (plan AN). A job Flow has
        # already failed stays: the wait below ends at once on that reply and settles it with its reason.
        flow = await replies.report() if detach and any(rpcid in mc.SUBMIT_RPCS for rpcid in frames) else None
        if flow is not None and (flow.get("statuses") or [None])[-1] != STATUS_FAILED:
            session.page.remove_listener("response", replies.on_response)
            watched = {"body_check": watch.report()} if watch is not None else {}
            ledger.append(
                job_id,
                "started",
                workflow_id=flow.get("workflow_id"),
                workflows_before=sorted(before),
                prompt=prompt,
                prompt_text=checked.get("prompt_text"),
                rpcids=sorted(frames),
                notice=notice,
                **watched,
                **(started_extra or {}),
                flow=flow,
                flow_reply_read=_reply_read(flow, frames),
                page_after_click=page_after_click,
            )
            return {
                "job_id": job_id,
                "kind": kind,
                "state": "started",
                "workflow_id": flow.get("workflow_id"),
                "quoted_credits": confirm["price"],
                "credits_before": credits_before,
                **_reply_fields(flow, frames),
                **extra,
                **checked,
                **watched,
            }

        deadline = asyncio.get_running_loop().time() + wait
        while True:
            rows, _ = await snapshot(session, project_id)
            fresh = clips.new_records(before, rows)
            if strict_output and count > 1:
                candidates = matching_outputs(fresh, prompt, checked.get("prompt_text"))
                output = candidates[0] if candidates else None
                if len(candidates) > count or (
                    len(candidates) == count and all(clips.is_done(c) for c in candidates)
                ):
                    break
                if not candidates and replies.reported_failed():
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(15)
                continue
            if strict_output:
                candidates = matching_outputs(fresh, prompt, checked.get("prompt_text"))
                output = candidates[0] if len(candidates) == 1 else None
                if len(candidates) > 1:
                    break
            else:
                output = pick_output(fresh, prompt)
            if output is not None and clips.is_done(output):
                break
            # dancer-1 (2026-09-17): Flow reported the refusal about 30 s after the click and the wait still ran 360 s.
            if output is None and replies.reported_failed():
                break
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(15)

        credits_after = (await reader.credits(session))["balance"]
        path, fetch_error = None, None
        outputs: list[dict[str, Any]] = []
        if count > 1:
            # x2-x4: done only with exactly `count` clips of this prompt, each finished and fetched; more is unknown.
            if len(candidates) == count and all(clips.is_done(c) for c in candidates):
                for clip in candidates:
                    try:
                        got = str(
                            await fetch_720(
                                session, clip, out_dir / f"{clip['id']}_{digest[:8]}", project_id=project_id
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        got, fetch_error = None, f"{type(exc).__name__}: {str(exc)[:160]}"
                    outputs.append({"media_id": clip["id"], "path": got})
                if all(o["path"] for o in outputs):
                    path = outputs[0]["path"]
        elif output is not None and clips.is_done(output):
            try:
                path = str(
                    await fetch_720(
                        session, output, out_dir / f"{output['id']}_{digest[:8]}", project_id=project_id
                    )
                )
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
            flow_reply_read=_reply_read(flow, frames),
            page_after_click=page_after_click,
        )
        # The advice leads: an agent sees at most 500 characters and a job_id can be long (re-review A, 2026-09-17).
        raise RuntimeError(
            "Start generation was clicked, so credits may already be spent: check flow_media and flow_credits and "
            f"never run this job again under a new job_id; it then failed with {detail}; {flow_said(flow)}"
            f"{_page_note(page_after_click, project_id)}; job {job_id}"
        ) from exc
    session.page.remove_listener("response", replies.on_response)
    flow = await replies.report()

    spent = credits_before - credits_after
    videos = [r for r in fresh if r.get("kind") == "video"]
    unclaimed = candidates if len(candidates) > max(count, 1) else videos
    watched = {"body_check": watch.report()} if watch is not None else {}
    # Flow's last word on the job was not "failed" (sg-bf-2, 2026-09-17: statuses 6, 2), so it may still finish and bill.
    statuses = flow.get("statuses") or []
    flow_open = bool(statuses) and statuses[-1] != STATUS_FAILED
    page_note = _page_note(page_after_click, project_id)
    if path:
        status = "done"
    elif count > 1 and len(candidates) > count:
        status = "unknown"
    elif output is not None:
        status = "pending"
    elif strict_output and (unclaimed or spent or flow_open):
        # A new video, a moved balance or a job Flow still runs is never "nothing was generated" (re-reviews B, F3).
        status = "unknown"
    else:
        status = "failed"
    ledger.append(
        job_id,
        status,
        media_id=output["id"] if output else None,
        path=path,
        **(
            {"outputs": outputs or [{"media_id": c["id"], "path": None} for c in candidates]}
            if count > 1
            else {}
        ),
        credits_before=credits_before,
        credits_after=credits_after,
        spent=spent,
        rpcids=sorted(frames),
        notice=notice or (await _notice(session.page)),
        **watched,
        **({"error": fetch_error} if fetch_error else {}),
        **({"candidates": [c["id"] for c in unclaimed]} if status == "unknown" else {}),
        flow=flow,
        flow_reply_read=_reply_read(flow, frames),
        page_after_click=page_after_click,
    )
    reply_note = _reply_note(flow, frames)
    if status == "pending" and count > 1:
        why = fetch_error or f"{len(candidates)} of {count} clips listed and finished after {wait:.0f}s"
        raise RuntimeError(
            f"do not run this job again: clips {[c['id'] for c in candidates]} exist but not all {count} came back "
            f"({why}); fetch them with flow_download once finished; spent {spent} credits{reply_note}; job {job_id}"
        )
    if status == "pending":
        why = fetch_error or f"still rendering after {wait:.0f}s"
        raise RuntimeError(
            f"do not run this job again: the clip {output['id']} exists but no file came back ({why}); fetch it "
            f"with flow_download once it has finished; spent {spent} credits{reply_note}; job {job_id}"
        )
    if status == "unknown" and unclaimed:
        raise RuntimeError(
            "check flow_media and never run this job again under a new job_id: "
            f"{len(unclaimed)} new records could be this job's clip ({[c['id'] for c in unclaimed]}), so none is "
            f"taken; spent {spent} credits; {flow_said(flow)}{page_note}{reply_note}; job {job_id}"
        )
    if status == "unknown" and spent:
        raise RuntimeError(
            "check flow_media and flow_credits and never run this job again under a new job_id: no new video showed "
            f"up within {wait:.0f}s, yet the balance moved by {spent} credits; {flow_said(flow)}{page_note}"
            f"{reply_note}; job {job_id}"
        )
    if status == "unknown":
        raise RuntimeError(
            "check flow_media and flow_credits again in a few minutes and never run this job again under a new "
            f"job_id: Flow had not finished the job when the {wait:.0f}s wait ended and nothing new was listed; "
            f"{flow_said(flow)}{page_note}{reply_note}; job {job_id}"
        )
    if status == "failed" and spent == 0 and statuses and statuses[-1] == STATUS_FAILED:
        # The advice, the reason and Flow's own words lead: an agent sees at most 500 characters (dancer-1, 2026-09-17).
        # The audio filter and a refusal with no reason may pass as they are (plan AM: `ak-v3` did), so the advice
        # is the one the job's typed outcome gives, never a "do not retry" beside a retry the server would allow.
        settled = {"status": "failed", "spent": spent, "flow": flow}
        if outcome.classify([{"status": "submitted", "kind": kind}, settled])["retryable"]:
            advice = (
                "it may pass as it is: call again with the same project and prompt, a new job_id and retry_of set "
                "to this job_id (the server allows two retries of one original job)"
            )
        else:
            advice = "do not retry the same inputs hoping they pass, tell the owner"
        raise RuntimeError(
            f"Flow refused this job and charged nothing: {advice}; "
            f"{flow_said(flow)}; Flow said: {notice or '(no message captured)'}{page_note}; rpcids {sorted(frames)}; "
            f"settings {settings['applied']}{reply_note}; job {job_id}"
        )
    if status == "failed":
        raise RuntimeError(
            f"nothing was generated within {wait:.0f}s, spent {spent} credits; {flow_said(flow)}{page_note}; rpcids "
            f"{sorted(frames)}; settings {settings['applied']}; Flow said: {notice or '(no message captured)'}"
            f"{reply_note}; job {job_id}"
        )
    return {
        "job_id": job_id,
        "kind": kind,
        "media_id": output["id"],
        "path": path,
        **({"outputs": outputs} if count > 1 else {}),
        "quoted_credits": confirm["price"],
        "credits_before": credits_before,
        "credits_after": credits_after,
        "spent": spent,
        **_reply_fields(flow, frames),
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
