"""Plan character-generation T1: how the composer's `@` mention binds a character and an image, and what one blocked
submit carries. $0, on a draft project.

1. One listing read gives the characters and the media. A character whose name shares no substring with any other
   character or media title is reused, otherwise one is created ($0: its portrait comes from Nano Banana 2). A fresh
   image with a unique file name is uploaded ($0), so the image mention has exactly one owner.
2. The abort that step 6 relies on is proven first: a project load with every xhr and fetch request aborted must count
   at least one aborted batchexecute request, or step 6 is skipped.
3. The project opens with agent mode off and an empty composer; gflow's own apply_video_settings sets references mode,
   omni-flash and 9:16, the price line is read, then 8 s is pinned here once its radio renders and the price is read
   again. Measured on the first run: gflow looks for the duration row 8 ms after switching the model, logs it absent
   and leaves the 10 s the composer remembered, so the line read 15 credits.
4. '@' and the character's name are typed and Enter is never pressed: every picker option is dumped, the one option
   matching the name is clicked, and every mention chip is dumped. The same for the uploaded image.
5. The prompt is typed after the chips; the order of chips and text in the box is recorded with the price line.
6. Start generation is clicked exactly once while every xhr and fetch request is aborted, and only when the character
   chip landed and the price line reads the omni-flash references price. A ledger row is written before the click,
   the aborted bodies are kept, the page is left before the abort is lifted, and the balance and the listing are read
   again: the balance must not move and no record may appear.
7. The composer is emptied and agent mode is put back the way it was found.

    uv run python -m video.probes.character_mentions --project <id> [--no-submit]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlsplit

from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel

from video import gen
from video.flow import agent, characters, composer, parsers, reader, uploads
from video.flow.reader import capture, one
from video.probes._common import OUT_DIR, out_path
from video.session import PROJECT_READY, FlowSession

BOX = "flow-prompt-box [contenteditable='true']"
PICKER_CONFIRM = "button.detail-add-to-prompt-btn"
PORTRAIT_PROMPT = (
    "A cheerful baker in his fifties with curly grey hair, round glasses and a flour-dusted blue apron, "
    "studio portrait, plain light background"
)
PROMPT = "stands in a sunny bakery and smiles at the camera while holding the object"
OMNI_REFERENCES_PRICE = 12
PAID = ("extend", "generate", "generation", "upscale")
# Page resources keep loading while the click is blocked; everything that could carry a request to Flow is aborted.
KEPT_TYPES = ("document", "stylesheet", "image", "media", "font", "script", "texttrack", "manifest")

_OPTIONS_JS = """(sel) => [...document.querySelectorAll(sel)].map(o => ({
  text: (o.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 160),
  title: ((o.querySelector('.asset-title') || {}).textContent || '').trim(),
  subtitle: ((o.querySelector('.type-subtitle') || {}).textContent || '').trim(),
  visible: !!(o.offsetWidth || o.offsetHeight),
  attrs: Object.fromEntries([...o.attributes].map(a => [a.name, a.value.slice(0, 200)])),
  inner: [...o.querySelectorAll('*')].map(e => ({
    tag: e.tagName.toLowerCase(),
    attrs: Object.fromEntries([...e.attributes].filter(a => a.name !== 'src').map(a => [a.name, a.value.slice(0, 120)])),
    text: e.children.length ? '' : (e.textContent || '').trim().slice(0, 80),
  })).filter(x => x.text || Object.keys(x.attrs).length).slice(0, 30),
}))"""

_CHIPS_JS = """(sel) => [...document.querySelectorAll(sel)].map(c => ({
  text: (c.textContent || '').trim().slice(0, 160),
  attrs: Object.fromEntries([...c.attributes].map(a => [a.name, a.value.slice(0, 200)])),
}))"""

_BOX_JS = """(sel) => {
  const box = document.querySelector(sel);
  if (!box) return null;
  const out = [];
  const walk = (n) => {
    if (n.nodeType === 3) { if (n.textContent.trim()) out.push({text: n.textContent.slice(0, 120)}); return; }
    if (n.nodeType !== 1) return;
    if (n.classList.contains('mention-chip')) { out.push({chip: (n.textContent || '').trim().slice(0, 120)}); return; }
    for (const c of n.childNodes) walk(c);
  };
  walk(box);
  return {sequence: out.slice(0, 40), text: (box.innerText || '').slice(0, 400)};
}"""

_OVERLAY_JS = """() => [...document.querySelectorAll('.cdk-overlay-pane, [role=dialog], [role=listbox]')]
  .filter(e => e.offsetParent !== null)
  .map(e => ({
    tag: e.tagName.toLowerCase(),
    text: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 300),
    buttons: [...e.querySelectorAll('button')].map(b => ({
      cls: (b.className || '').toString().slice(0, 80),
      aria: (b.getAttribute('aria-label') || '').slice(0, 80),
      text: (b.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80),
    })).slice(0, 20),
  }))"""


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc).split('Call log:')[0][:300]}"


def _rpcids(url: str) -> list[str]:
    return [rpc for rpc in parse_qs(urlsplit(url).query).get("rpcids", [""])[0].split(",") if rpc]


def _unique_name(name: str | None, others: list[str]) -> bool:
    wanted = _norm(name)
    return bool(wanted) and not any(
        wanted in _norm(other) or _norm(other) in wanted for other in others if other
    )


async def _listing(session: FlowSession, project_id: str) -> Any:
    frames = await capture(
        session, lambda: session.goto(session.project_url(project_id), ready=PROJECT_READY), settle=8.0
    )
    return one(frames, "Zzl0ze")


def _image(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x2f6fb5:s=512x512",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
    )
    return path


async def _prove_abort(session: FlowSession, project_id: str) -> dict[str, Any]:
    aborted: list[str] = []

    async def block(route: Any) -> None:
        if route.request.resource_type in KEPT_TYPES:
            await route.continue_()
            return
        aborted.append(route.request.url)
        await route.abort()

    page = session.page
    await page.route("**/*", block)
    try:
        await page.goto(session.project_url(project_id), wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(8_000)
    finally:
        await page.goto("about:blank")
        await page.unroute("**/*", block)
    batch = [url for url in aborted if "batchexecute" in url]
    return {
        "aborted": len(aborted),
        "batchexecute": len(batch),
        "rpcids": sorted({rpc for url in batch for rpc in _rpcids(url)}),
    }


async def _wait_options(page: Any, timeout_ms: int = 12_000) -> int:
    waited = 0
    while (count := await page.locator(mc.PICKER_OPTION).count()) == 0 and waited < timeout_ms:
        await page.wait_for_timeout(500)
        waited += 500
    return count


async def _mention(page: Any, query: str, title: str, kind: str, shot: Path) -> dict[str, Any]:
    """Type '@' and the query, dump what the picker offers, click the one matching option, dump the chips.

    Measured on the first run: an option carries no id, only `.asset-title` and a `.type-subtitle` naming its kind
    ('Character', 'Image'), and clicking it inserts the chip at once with no 'Add to prompt' confirm.
    """
    step: dict[str, Any] = {"query": query, "title": title, "kind": kind}
    chips_before = await page.evaluate(_CHIPS_JS, mc.MENTION_CHIP)
    step["chips_before"] = len(chips_before)
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2_200)
    await page.keyboard.type(query, delay=100)
    step["options_count"] = await _wait_options(page)
    await page.wait_for_timeout(1_500)
    options = await page.evaluate(_OPTIONS_JS, mc.PICKER_OPTION)
    step["options"] = options
    step["overlays"] = await page.evaluate(_OVERLAY_JS)
    await page.screenshot(path=str(shot))
    step["screenshot"] = str(shot)
    exact = [
        i
        for i, option in enumerate(options)
        if option["visible"]
        and _norm(option["title"]) == _norm(title)
        and _norm(option["subtitle"]) == _norm(kind)
    ]
    loose = [
        i for i, option in enumerate(options) if option["visible"] and _norm(query) in _norm(option["text"])
    ]
    step["exact"], step["loose"] = exact, loose
    chosen = exact if len(exact) == 1 else loose if len(loose) == 1 else []
    if not chosen:
        step["clicked"] = None
        for _ in range(len(query) + 1):
            await page.keyboard.press("Backspace")
        step["chips_after"] = await page.evaluate(_CHIPS_JS, mc.MENTION_CHIP)
        return step
    label = options[chosen[0]]["text"]
    if any(word in label.casefold() for word in PAID):
        raise RuntimeError(f"refusing to click picker option {label!r}: it reads like a control that spends")
    await page.locator(mc.PICKER_OPTION).nth(chosen[0]).click(timeout=8_000)
    await page.wait_for_timeout(2_500)
    step["clicked"] = {"index": chosen[0], "text": label, "rule": "exact" if exact else "contains"}
    step["overlays_after_click"] = await page.evaluate(_OVERLAY_JS)
    chips = await page.evaluate(_CHIPS_JS, mc.MENTION_CHIP)
    confirm = page.locator(PICKER_CONFIRM)
    step["confirm_buttons"] = await confirm.count()
    if len(chips) == len(chips_before) and step["confirm_buttons"] == 1:
        await confirm.first.click(timeout=8_000)
        await page.wait_for_timeout(2_500)
        step["confirm_clicked"] = True
        chips = await page.evaluate(_CHIPS_JS, mc.MENTION_CHIP)
    step["chips_after"] = chips
    return step


async def _price(page: Any, label: str) -> dict[str, Any]:
    text = await composer._open_settings(page, label)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1_000)
    return {"price": composer.price_from(text), "settings_text": text[:300]}


_RADIOS_JS = """() => [...document.querySelectorAll('.cdk-overlay-pane [role=radio]')].map(r => ({
  text: (r.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40),
  checked: r.getAttribute('aria-checked'),
  visible: !!(r.offsetWidth || r.offsetHeight),
}))"""


async def _pin_duration(page: Any, wanted: str, timeout_ms: int = 10_000) -> dict[str, Any]:
    """Select the duration radio once the row has rendered: gflow checks for it 8 ms after the model switch and skips."""
    result: dict[str, Any] = {"wanted": wanted}
    await composer._open_settings(page, "t1-duration")
    radio = page.locator(".cdk-overlay-pane [role=radio]").filter(has_text=re.compile(rf"^\s*{wanted}\s*$"))
    waited = 0
    while await radio.count() == 0 and waited < timeout_ms:
        await page.wait_for_timeout(500)
        waited += 500
    result["waited_ms"] = waited
    result["radios_before"] = await page.evaluate(_RADIOS_JS)
    if await radio.count() == 1:
        if await radio.first.get_attribute("aria-checked") != "true":
            await radio.first.click(timeout=4_000)
            await page.wait_for_timeout(1_200)
        result["checked"] = await radio.first.get_attribute("aria-checked")
    result["price_text"] = await page.evaluate(composer._OVERLAY_TEXT_JS)
    result["price"] = composer.price_from(result["price_text"])
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1_000)
    return result


async def _blocked_submit(
    session: FlowSession, project_id: str, ledger: gen.Ledger, job_id: str, ids: dict[str, str | None]
) -> dict[str, Any]:
    """Click Start generation once with every request that could reach Flow aborted, and keep what was aborted."""
    page = session.page
    result: dict[str, Any] = {"job_id": job_id}
    start = page.get_by_role("button", name=re.compile("Start generation", re.IGNORECASE))
    result["start_buttons"] = await start.count()
    if result["start_buttons"] != 1 or not await start.first.is_enabled():
        result["skipped"] = "not exactly one enabled Start generation button"
        return result
    blocked: list[dict[str, Any]] = []

    async def block(route: Any) -> None:
        request = route.request
        if request.resource_type in KEPT_TYPES:
            await route.continue_()
            return
        try:
            try:
                body = request.post_data or ""
            except Exception:  # noqa: BLE001
                body = ""
            decoded = unquote_plus(body)
            blocked.append(
                {
                    "url": request.url[:300],
                    "type": request.resource_type,
                    "rpcids": _rpcids(request.url),
                    "bytes": len(body),
                    "model_keys": sorted(set(mc.MODEL_KEY.findall(decoded)))[:6],
                    "has_r2v": "_r2v_" in decoded,
                    "entity_in_body": bool(ids["entity_id"]) and ids["entity_id"] in decoded,
                    "media_in_body": bool(ids["media_id"]) and ids["media_id"] in decoded,
                    "workflow_in_body": bool(ids.get("workflow_id")) and ids["workflow_id"] in decoded,
                    "body": decoded[:20_000],
                }
            )
        finally:
            await route.abort()

    await page.route("**/*", block)
    try:
        await start.first.click(timeout=8_000)
        result["clicked_at"] = time.time()
        await page.wait_for_timeout(25_000)
        result["notice"] = await composer._notice(page)
        shot = OUT_DIR / f"character_mentions_{job_id}_after_click.png"
        await page.screenshot(path=str(shot))
        result["screenshot"] = str(shot)
    finally:
        await page.goto("about:blank")
        await page.unroute("**/*", block)
    result["blocked"] = blocked
    result["blocked_rpcids"] = sorted({rpc for item in blocked for rpc in item["rpcids"]})
    return result


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--no-submit", action="store_true", help="skip the blocked Start generation click")
    args = ap.parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    report: dict[str, Any] = {"project": args.project, "stamp": stamp}
    ledger = gen.Ledger(OUT_DIR / "ledger.jsonl")
    job_id = f"probe-mention-{stamp}"
    was_agent: bool | None = None
    clicked = False
    workflows_before: set[str] = set()
    async with FlowSession(args.profile) as session:
        page = session.page
        try:
            listing = await _listing(session, args.project)
            chars = parsers.characters_from_listing(listing)
            media = parsers.media(listing)
            report["characters_found"] = chars
            report["media_titles"] = [{"id": m["id"], "title": m["title"], "kind": m["kind"]} for m in media]
            titles = [m["title"] or "" for m in media]
            names = [c["name"] or "" for c in chars]
            reusable = [
                c
                for index, c in enumerate(chars)
                if _unique_name(c["name"], titles + names[:index] + names[index + 1 :])
            ]
            if reusable:
                character = reusable[0]
                report["character"] = {"reused": True, **character}
            else:
                name = f"Pemira{stamp[-6:]}"
                report["character_create"] = await characters.create(
                    session, args.project, PORTRAIT_PROMPT, name=name
                )
                character = {"entity_id": report["character_create"]["entity_id"], "name": name}
                report["character"] = {"reused": False, **character}

            image = _image(OUT_DIR / f"peobj{stamp.replace('_', '')}.png")
            report["upload"] = await uploads.upload(session, args.project, image)

            listing = await _listing(session, args.project)
            workflows_before = {r["workflow_id"] for r in parsers.records(listing)}
            report["workflows_before"] = len(workflows_before)
            listed = {c["entity_id"]: c for c in parsers.characters_from_listing(listing)}
            report["character_listed"] = listed.get(character["entity_id"])
            uploaded = [m for m in parsers.media(listing) if m["id"] == report["upload"].get("media_id")]
            report["image_listed"] = uploaded[0] if uploaded else None
            ids = {
                "entity_id": character["entity_id"],
                "media_id": report["upload"].get("media_id"),
                "workflow_id": report["upload"].get("workflow_id"),
            }

            report["abort_proof"] = await _prove_abort(session, args.project)
            report["credits_before"] = (await reader.credits(session))["balance"]

            report["agent_mode"] = await agent.set_mode(session, args.project, False)
            was_agent = report["agent_mode"]["was"]
            report["left_over"] = await composer.clear_prompt(session)
            request = GenerateVideoRequest(
                prompt=PROMPT,
                mode=Mode.R2V,
                aspect=Aspect.PORTRAIT,
                model=VideoModel.from_cli("omni-flash"),
                reference_entities=(character["entity_id"],),
                reference_entity_names=(character["name"],),
            )
            try:
                await mc.MigratedComposer().apply_video_settings(page, request)
                report["settings"] = "applied"
            except Exception as exc:  # noqa: BLE001
                report["settings"] = _error(exc)
            report["price_after_settings"] = await _price(page, "t1-settings")
            report["duration_pin"] = await _pin_duration(page, "8s")

            await page.locator(BOX).first.click(timeout=8_000)
            report["mention_character"] = await _mention(
                page,
                character["name"],
                character["name"],
                "Character",
                OUT_DIR / f"character_mentions_{stamp}_character.png",
            )
            report["mention_image"] = await _mention(
                page, image.stem, image.name, "Image", OUT_DIR / f"character_mentions_{stamp}_image.png"
            )
            report["prompt_landed"] = await composer._type_prompt(page, page.locator(BOX).first, PROMPT)
            report["box"] = await page.evaluate(_BOX_JS, BOX)
            report["chips_final"] = await page.evaluate(_CHIPS_JS, mc.MENTION_CHIP)
            report["price_with_chips"] = await _price(page, "t1-chips")
            await page.screenshot(path=str(OUT_DIR / f"character_mentions_{stamp}_composer.png"))

            entity_chips = [
                chip
                for chip in report["chips_final"]
                if chip["attrs"].get("data-reference-type") == "entity"
                and chip["attrs"].get("data-entity-id") == character["entity_id"]
            ]
            reasons = []
            if args.no_submit:
                reasons.append("--no-submit")
            if report["abort_proof"]["batchexecute"] < 1:
                reasons.append("the abort was not proven on a page load")
            if not entity_chips:
                reasons.append("no chip carries the character entity")
            if report["price_with_chips"]["price"] != OMNI_REFERENCES_PRICE:
                reasons.append(f"price line reads {report['price_with_chips']['price']}")
            if reasons:
                report["blocked_submit"] = {"skipped": "; ".join(reasons)}
            else:
                ledger.append(
                    job_id,
                    "submitted",
                    kind="probe-blocked-submit",
                    project=args.project,
                    prompt=PROMPT,
                    quoted_credits=report["price_with_chips"]["price"],
                    credits_before=report["credits_before"],
                    note="every xhr and fetch request is aborted during this click",
                )
                clicked = True
                report["blocked_submit"] = await _blocked_submit(session, args.project, ledger, job_id, ids)
        except Exception as exc:  # noqa: BLE001
            report["run_error"] = _error(exc)
        finally:
            try:
                await session.goto(session.project_url(args.project), ready=PROJECT_READY)
                await page.wait_for_timeout(2_500)
                report["cleanup"] = await composer.clear_prompt(session)
                report["cleanup_mentions"] = await page.evaluate(_CHIPS_JS, mc.MENTION_CHIP)
                report["cleanup_box"] = await page.evaluate(_BOX_JS, BOX)
                if was_agent:
                    report["agent_restored"] = await agent.set_mode(session, args.project, True)
            except Exception as exc:  # noqa: BLE001
                report["cleanup"] = {"error": _error(exc)}
            if clicked:
                # Read after the project was opened again with nothing aborted, so a submit Flow kept and resent shows.
                credits_after = (await reader.credits(session))["balance"]
                after = await _listing(session, args.project)
                fresh = [r for r in parsers.records(after) if r["workflow_id"] not in workflows_before]
                report["credits_after"] = credits_after
                report["new_records"] = fresh
                clean = credits_after == report["credits_before"] and not fresh
                ledger.append(
                    job_id,
                    "failed" if clean else "unknown",
                    credits_before=report["credits_before"],
                    credits_after=credits_after,
                    spent=report["credits_before"] - credits_after,
                    new_records=len(fresh),
                    blocked_rpcids=(report.get("blocked_submit") or {}).get("blocked_rpcids"),
                )

    path = out_path("character_mentions")
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    character = report.get("character", {})
    print(
        f"character={character.get('entity_id')} name={character.get('name')!r} reused={character.get('reused')}"
    )
    print(f"upload={report.get('upload', {}).get('media_id')} abort_proof={report.get('abort_proof')}")
    print(f"settings={report.get('settings')} price={report.get('price_after_settings', {}).get('price')}")
    for key in ("mention_character", "mention_image"):
        step = report.get(key, {})
        print(
            f"{key}: options={step.get('options_count')} exact={step.get('exact')} loose={step.get('loose')} "
            f"clicked={step.get('clicked')} confirm={step.get('confirm_buttons')} "
            f"chips={[c['attrs'] for c in step.get('chips_after', [])]}"
        )
    print(f"box={report.get('box')}")
    print(f"price_with_chips={report.get('price_with_chips', {}).get('price')}")
    submit = report.get("blocked_submit", {})
    print(f"blocked_submit: skipped={submit.get('skipped')} rpcids={submit.get('blocked_rpcids')}")
    for item in submit.get("blocked", []):
        print(
            f"  {item['type']} {item['rpcids']} bytes={item['bytes']} keys={item['model_keys']} "
            f"r2v={item['has_r2v']} entity={item['entity_in_body']} media={item['media_in_body']}"
        )
    print(
        f"credits {report.get('credits_before')} -> {report.get('credits_after')} "
        f"new_records={len(report.get('new_records') or [])}"
    )
    print(f"run_error={report.get('run_error')} cleanup={report.get('cleanup')}")
    print(f"report={path}")


if __name__ == "__main__":
    asyncio.run(main())
