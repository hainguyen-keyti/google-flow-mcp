"""What a spending job came to, read off the rows its driver already wrote in the ledger (plan AM, G5).

Nothing here decides anything about a click: the drivers write `submitted` or `opening` before they can spend and one
of `done`, `pending`, `unknown`, `failed` after, with the balance on both sides and, on the composer path, what Flow's
own replies said. This module only names that outcome, so an agent can tell a refusal Flow did not charge for from a
job whose money may be gone.

Measured 2026-10-02 over the 39 ledgers of this machine (519 rows): Flow's refusals carried
PUBLIC_ERROR_AUDIO_FILTERED (5), PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED (2), PUBLIC_ERROR_UNSAFE_GENERATION (2)
and, once, no reason at all (`ak-v3`, which passed on an identical retry). All ten charged nothing.
"""

from __future__ import annotations

from typing import Any

INTENT = ("submitted", "opening")
SETTLED = ("done", "pending", "unknown", "failed")
# composer.STATUS_FAILED; kept apart so this module imports nothing of the drivers (a test holds the two together).
FLOW_FAILED = 4
# The kinds `composer._submit` writes for gen_video and gen_character, the only tools that take `retry_of`.
RETRY_KINDS = ("video", "character")
REASONS = {
    "PUBLIC_ERROR_AUDIO_FILTERED": "AUDIO_FILTERED",
    "PUBLIC_ERROR_UNSAFE_GENERATION": "UNSAFE_GENERATION",
    "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED": "PROMINENT_PEOPLE",
}
RETRYABLE = ("AUDIO_FILTERED", "NO_REASON")
MAX_RETRIES = 2
WAF_EXIT, WAF_CLASS = 10, "WafRejectionError"

_NEVER = "never run it again under a new job_id"
CODES = {
    "DONE": "the job finished; nothing to do",
    "PENDING": "the clip exists and its file was not fetched yet: fetch it with flow_download or clip_download once "
    f"it has finished, and {_NEVER}",
    "NOT_SUBMITTED": "nothing was clicked and no ledger row was written: fix the request and call again with the "
    "same job_id",
    "UNKNOWN": "the job was started and no settled outcome is on record, so credits may be gone: check flow_media and "
    f"flow_credits (clip_reconcile for an editor job) and {_NEVER}",
    "CHARGED_NO_OUTPUT": "the balance moved and no output was taken: look for the clip with flow_media, tell the "
    f"owner, and {_NEVER}",
    "AUDIO_FILTERED": "Flow's audio filter refused the clip and charged nothing; it is random on Veo",
    "NO_REASON": "Flow failed the job with no reason and charged nothing",
    "UNSAFE_GENERATION": "Flow refused the inputs as unsafe and charged nothing: never send the same inputs again; "
    "change the prompt or the images, or tell the owner",
    "PROMINENT_PEOPLE": "Flow refused the inputs as showing a prominent person and charged nothing: never send the "
    "same inputs again; change the image or the prompt, or tell the owner",
    "REFUSED": "Flow refused the job with a reason nobody has measured here and charged nothing: do not retry it on a "
    "guess, tell the owner",
    "NOTHING_GENERATED": "nothing was generated and the balance had not moved when it was read, but Flow never said "
    f"it failed the job, so it may still finish and bill: check flow_media in a few minutes and {_NEVER} before then",
    "TOOL_ERROR": "the driver stopped before a result and the balance had not moved when it was read: read the error, "
    "check flow_media, and start it again under a new job_id only if nothing was made",
    "UNUSUAL_ACTIVITY": "Google flagged unusual activity. Stop: no retry, no new job, no sign-in; tell the owner, the "
    "browser profile has to cool down",
}
_RETRY_HOW = (
    ": call the same tool again with the same project and prompt, a new job_id and retry_of set to this job_id, at "
    f"most {MAX_RETRIES} times for one original job"
)
_NO_RETRY = ": this tool has no retry the server can vouch for, so tell the owner before starting it again"


def _charged(row: dict[str, Any]) -> int | None:
    """What the row's own balance bracket says left the account; None when it cannot say."""
    if isinstance(row.get("spent"), int) and not isinstance(row.get("spent"), bool):
        return row["spent"]
    before, after = row.get("credits_before"), row.get("credits_after")
    if isinstance(before, int) and isinstance(after, int):
        return before - after
    return None


def last_run(rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The last intent row of a job and the settled row that follows it, None where there is none. A job id that was
    run again keeps its earlier rows; only the last run speaks."""
    intent = settled = None
    for row in rows:
        if row.get("status") in INTENT:
            # An editor job writes `opening` and then `submitted`: one run, whose kind and project `opening` holds.
            if not (intent is not None and settled is None and row.get("status") == "submitted"):
                intent = row
            settled = None
        elif row.get("status") in SETTLED:
            settled = row
    return intent, settled


def _said(code: str, charged: int | None, *, retryable: bool = False, reasons: Any = None) -> dict[str, Any]:
    advice = CODES[code]
    if code in RETRYABLE:
        advice += _RETRY_HOW if retryable else _NO_RETRY
    return {
        "code": code,
        "charged": charged,
        "retryable": retryable,
        "advice": advice,
        **({"reasons": list(reasons)} if reasons else {}),
    }


def classify(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """`{code, charged, retryable, advice}` for the rows of ONE job, oldest first."""
    intent, settled = last_run(rows)
    if intent is None and settled is None:
        return _said("NOT_SUBMITTED", 0)
    if settled is None:
        return _said("UNKNOWN", None)
    status, charged = settled.get("status"), _charged(settled)
    if status == "done":
        return _said("DONE", charged)
    if status == "pending":
        return _said("PENDING", charged)
    if status == "unknown" or charged is None:
        return _said("UNKNOWN", charged)
    if charged != 0:
        return _said("CHARGED_NO_OUTPUT", charged)
    if settled.get("exit_code") == WAF_EXIT or settled.get("error_class") == WAF_CLASS:
        return _said("UNUSUAL_ACTIVITY", 0)
    if "exit_code" in settled:
        return _said("TOOL_ERROR", 0)
    flow = settled.get("flow") if isinstance(settled.get("flow"), dict) else {}
    statuses, reasons = flow.get("statuses") or [], flow.get("reasons") or []
    if not statuses or statuses[-1] != FLOW_FAILED:
        return _said("NOTHING_GENERATED", 0, reasons=reasons)
    codes = [REASONS.get(reason, "REFUSED") for reason in reasons] or ["NO_REASON"]
    # One reason that must not be retried decides, wherever it stands among the others.
    final = [code for code in codes if code not in RETRYABLE]
    code = final[0] if final else codes[0]
    kind = (intent or {}).get("kind")
    return _said(code, 0, retryable=code in RETRYABLE and kind in RETRY_KINDS, reasons=reasons)


# What `composer._submit` keeps of a prompt on the `submitted` row.
PROMPT_KEPT = 200


def _retried(rows: list[dict[str, Any]]) -> str | None:
    """The job a job's click was a retry of: the link sits on the `submitted` row, written right before the click."""
    links = [r.get("retry_of") for r in rows if r.get("status") == "submitted" and r.get("retry_of")]
    return links[-1] if links else None


def retry_refusal(
    target: str, jobs: dict[str, list[dict[str, Any]]], *, project: str, prompt: str
) -> str | None:
    """Why job `target` may not be retried with this project and prompt, or None when it may (I-money-4).

    `jobs` holds the rows of every job of every ledger under the out folder, oldest first. A retry is vouched for
    only when the job is a refusal Flow did not charge for and may be retried (`classify`), the request is the one
    that job sent, no other submitted job already retried it, and it is not past the second retry of the original.
    """
    rows = jobs.get(target) or []
    if not rows:
        return (
            f"retry_of names job {target!r}, which has no ledger row under the out folder: there is nothing to "
            "retry; a job that never started runs again under its own job_id, anything else is a new job with no "
            "retry_of"
        )
    said = classify(rows)
    if not said["retryable"]:
        charged = "unknown" if said["charged"] is None else said["charged"]
        return (
            f"job {target!r} may not be retried: its outcome is {said['code']} (charged {charged}); "
            f"{said['advice']}"
        )
    intent, _ = last_run(rows)
    sent = intent or {}
    if sent.get("project") != project or sent.get("prompt") != prompt[:PROMPT_KEPT]:
        return (
            f"a retry sends the same project and prompt: job {target!r} went to project {sent.get('project')} with "
            f"a prompt starting {str(sent.get('prompt'))[:60]!r}; anything else is a new job with no retry_of"
        )
    for other, theirs in jobs.items():
        if other != target and _retried(theirs) == target:
            return (
                f"job {target!r} was already retried by job {other!r}: read that job's outcome and, if Flow "
                f"refused it too without charging, retry it by naming {other!r}"
            )
    # Walked no further than the bound, so links that loop end here as well.
    chain = [target]
    while len(chain) <= MAX_RETRIES and (link := _retried(jobs.get(chain[-1]) or [])) is not None:
        chain.append(link)
    if len(chain) > MAX_RETRIES:
        return (
            f"job {target!r} is already retry {len(chain) - 1} of {MAX_RETRIES} of job {chain[-1]!r}: Flow refused "
            "this request every time, so tell the owner instead of sending it again"
        )
    return None


def unreadable() -> dict[str, Any]:
    """A job that ran and whose rows could not be read back: it may have clicked, so nothing about it is known."""
    return _said("UNKNOWN", None)


def head(said: dict[str, Any]) -> str:
    """The one line that leads an error an agent reads: it sees at most 500 characters of it."""
    charged = "unknown" if said["charged"] is None else said["charged"]
    return f"outcome code={said['code']} charged={charged} retryable={'yes' if said['retryable'] else 'no'}"
