"""Walk Flow's pages and compare what they show with what this repo was built against (plan AC, 2026-09-29).

    uv run video flow survey --project <id> [--write]

$0 and read only: it opens pages, sidebar sections, the composer's settings (clicking every option to read the live
price line) and the toolbar menus of the clip editor, then presses Escape; it never clicks an item that spends, saves or
deletes. For each page it saves a screenshot under out/survey/<time>/, the UI labels (buttons, menu items, radios,
tabs; never media titles, the project title, an email or an id), and how many elements each selector the source relies
on matches. The labels and selector counts are compared with flow_ui.json, the options and prices with
flow_options.json; --write makes the current survey the new baseline.
"""

from __future__ import annotations

import asyncio
import importlib
import itertools
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from video.flow import agent, clips, composer, parsers, reader
from video.flow.reader import capture, one
from video.session import PROJECT_READY, FlowSession

HERE = Path(__file__).parent
UI_BASELINE = HERE / "flow_ui.json"
OPTIONS_BASELINE = HERE / "flow_options.json"

SOURCE_MODULES = (
    "session",
    "flow.composer",
    "flow.clips",
    "flow.agent",
    "flow.ingredients",
    "flow.video",
    "flow.scenes",
    "flow.characters",
    "flow.projects",
    "flow.uploads",
    "flow.lane",
    "flow.reader",
)
_SELECTOR_LIKE = re.compile(r"(\[|flow-|mat-|\.cdk|button|^#|^\.[a-z])")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[a-z]{2,}", re.IGNORECASE)
MAX_LABEL = 60
SIDEBAR = "mat-nav-list .mat-mdc-list-item"
KNOWN_MODELS = {
    "Omni 1.1 Flash": "omni-flash",
    "Veo 3.1 - Lite": "veo-lite",
    "Veo 3.1 - Fast": "veo-fast",
    "Veo 3.1 - Quality": "veo-quality",
}


class UnsafeClick(Exception):
    """The survey was about to click something whose label reads like spending, saving or deleting."""


# The survey clicks views, menu openers, radios and model names; never these. Labels are read with their material
# icon name stripped ("delete Trash" is the Trash view, "delete Move to trash" is an action).
UNSAFE = re.compile(
    r"start generation|generate|move to trash|delete|save|extend|restore|remove|empty|create|new project|upscale"
    r"|publish|upload media|reuse prompt|clear",
    re.IGNORECASE,
)
_ICON_WORD = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*\s+(?=\S)")


async def safe_click(element: Any) -> None:
    """The one click the survey makes itself: read the element's label first and refuse an unsafe one."""
    label = (await element.get_attribute("aria-label")) or (await element.inner_text()) or ""
    shown = _ICON_WORD.sub("", re.sub(r"\s+", " ", label).strip())
    if UNSAFE.search(shown):
        raise UnsafeClick(f"the survey never clicks {shown!r}")
    await element.click(timeout=8_000)


class SurveyIncomplete(Exception):
    """A walk that must not become the baseline: flow_options.json feeds gen_video's money guard."""


class OptionLayoutChanged(Exception):
    """The settings pane shows rows or a split between modes that options_from_walk cannot express yet."""


_RESOLUTION = re.compile(r"^\d+p$")
_LENGTH = re.compile(r"^\d+s$")
_COUNT = re.compile(r"^x\d+$")
# Sidebar items the walk never clicks: a toggle, or anything that reads like an action rather than a view.
SIDEBAR_ACTIONS = re.compile(r"collapse|expand|new|create|delete|empty|clear|remove", re.IGNORECASE)


def route_name(prefix: str, text: str, private: set[str], index: int) -> str:
    """A route named from page text goes through the label filter too; what it drops is named by position."""
    kept = clean_labels([text], private)
    return f"{prefix} {kept[0]}" if kept else f"{prefix} {index}"


def check_writable(ui: dict[str, Any], options: dict[str, Any] | None) -> None:
    """Refuse a baseline a broken walk would leave: a failed route, no model, no count, or a cell with no price."""
    problems = [
        f"route {name!r} failed: {r['error']}"
        for name, r in sorted(ui.get("routes", {}).items())
        if r.get("error")
    ]
    if options is None:
        problems.append("the options walk could not be read")
    else:
        models = options["video"]["models"]
        if not models:
            problems.append("no video model was read")
        if not options["video"]["counts"] or not options["video"]["aspects"]:
            problems.append("no count or aspect was read")
        for name, entry in sorted(models.items()):
            missing = [cell or "x1" for cell, value in entry["price_x1"].items() if value is None]
            if missing or not entry["price_x1"]:
                problems.append(f"{name}: no price for {missing or 'any cell'}")
        if not options["image"]["models"]:
            problems.append("no image model was read")
    if problems:
        raise SurveyIncomplete("; ".join(problems))


def collect_selectors() -> dict[str, str]:
    """Every module-level constant in the source that reads like a CSS selector, by `module.NAME`."""
    found: dict[str, str] = {}
    for name in SOURCE_MODULES:
        module = importlib.import_module(f"video.{name}")
        for key, value in vars(module).items():
            if not (key.isupper() and isinstance(value, str)):
                continue
            text = value.strip()
            if "\n" in text or len(text) > 200 or text.startswith(("(", "async", "http")):
                continue
            if _SELECTOR_LIKE.search(text):
                found[f"{name}.{key}"] = value
    return found


def clean_labels(raw: list[str], private: set[str]) -> list[str]:
    """UI labels only: whitespace collapsed; nothing that names the account, the project, a media, or an id."""
    hidden = {re.sub(r"\s+", " ", p).strip().casefold() for p in private if p}
    out = set()
    for label in raw:
        text = re.sub(r"\s+", " ", (label or "").replace("\\n", " ")).strip()
        if not text or len(text) > MAX_LABEL or EMAIL.search(text) or UUID.search(text):
            continue
        if text.casefold() in hidden:
            continue
        out.add(text)
    return sorted(out)


def compare_ui(base: dict[str, Any], current: dict[str, Any]) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for route, was in sorted(base.get("routes", {}).items()):
        now = current.get("routes", {}).get(route)
        if now is None:
            found.append(("route missing", route, ""))
            continue
        if now.get("error"):
            found.append(("route error", route, now["error"]))
            continue
        before, after = set(was.get("labels", [])), set(now.get("labels", []))
        found += [("label added", route, label) for label in sorted(after - before)]
        found += [("label removed", route, label) for label in sorted(before - after)]
        for name, count in sorted(was.get("selectors", {}).items()):
            if count and not now.get("selectors", {}).get(name):
                found.append(("selector lost", route, name))
    for route in sorted(set(current.get("routes", {})) - set(base.get("routes", {}))):
        found.append(("route added", route, ""))
    return found


def compare_options(base: dict[str, Any], current: dict[str, Any]) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    was, now = base["video"], current["video"]
    for key in ("modes", "aspects", "counts"):
        if was[key] != now[key]:
            found.append(("video option changed", key, f"{was[key]} -> {now[key]}"))
    for name in sorted(set(now["models"]) - set(was["models"])):
        found.append(("model added", name, now["models"][name]["label"]))
    for name in sorted(set(was["models"]) - set(now["models"])):
        found.append(("model removed", name, was["models"][name]["label"]))
    for name in sorted(set(was["models"]) & set(now["models"])):
        a, b = was["models"][name], now["models"][name]
        for key in ("label", "resolutions", "durations", "modes"):
            if a[key] != b[key]:
                found.append(("model changed", name, f"{key}: {a[key]} -> {b[key]}"))
        for cell in sorted(set(a["price_x1"]) | set(b["price_x1"])):
            if a["price_x1"].get(cell) != b["price_x1"].get(cell):
                found.append(
                    ("price changed", name, f"{cell}: {a['price_x1'].get(cell)} -> {b['price_x1'].get(cell)}")
                )
    for key in ("models", "aspects", "counts", "price_x1"):
        a, b = base["image"][key], current["image"][key]
        if a == b:
            continue
        if isinstance(a, list):
            added, removed = [x for x in b if x not in a], [x for x in a if x not in b]
            detail = "; ".join(
                p for p in (f"added {added}" if added else "", f"removed {removed}" if removed else "") if p
            )
            found.append(("image option changed", key, detail or f"order {a} -> {b}"))
        else:
            found.append(("image option changed", key, f"{a} -> {b}"))
    return found


def slug(label: str) -> str:
    if label in KNOWN_MODELS:
        return KNOWN_MODELS[label]
    return re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")


def _entry(mode: str, label: str, cell: dict[str, Any]) -> dict[str, Any]:
    rows, prices = cell["rows"], cell["prices"]
    if not rows or not all(_COUNT.match(c) for c in rows[-1]):
        raise OptionLayoutChanged(f"{label} in {mode}: the last row is not the count row: {rows}")
    counts = [int(c[1:]) for c in rows[-1]]
    if len(rows) == 1:
        return {"resolutions": [], "durations": [], "price_x1": {"": prices.get("x1")}, "counts": counts}
    if (
        len(rows) == 3
        and all(_RESOLUTION.match(r) for r in rows[0])
        and all(_LENGTH.match(d) for d in rows[1])
    ):
        resolutions, durations = list(rows[0]), [int(d[:-1]) for d in rows[1]]
        price_x1 = {
            f"{r} {d}s": prices.get(f"{r} {d}s x1") for r, d in itertools.product(resolutions, durations)
        }
        return {"resolutions": resolutions, "durations": durations, "price_x1": price_x1, "counts": counts}
    raise OptionLayoutChanged(f"{label} in {mode}: rows nobody has mapped yet: {rows}")


def options_from_walk(walk: dict[str, Any]) -> dict[str, Any]:
    """The raw settings walk turned into flow_options.json, the file gen_video checks and prices from."""
    models: dict[str, dict[str, Any]] = {}
    counts: list[int] = []
    for mode, per_model in walk["video"]["modes"].items():
        for label, cell in per_model.items():
            got = _entry(mode, label, cell)
            counts = got.pop("counts")
            entry = models.get(slug(label))
            if entry is None:
                models[slug(label)] = {"label": label, **got, "modes": [mode]}
                continue
            if {k: entry[k] for k in got} != got:
                raise OptionLayoutChanged(
                    f"{label} differs between modes ({entry['modes']} and {mode}); flow_options.json keeps one set"
                )
            entry["modes"].append(mode)
    return {
        "measured": walk["measured"],
        "source": "a $0 walk of flow.google.com's composer settings: every option clicked, the live price line read, "
        "Start never clicked",
        "video": {
            "modes": list(walk["video"]["modes"]),
            "aspects": walk["video"]["aspects"],
            "counts": counts,
            "models": models,
        },
        "image": walk["image"],
    }


# The walk. Everything below drives a real page; the functions above are what the tests hold.
_LABELS_JS = """(scope) => {
  const inTile = (e) => !!e.closest('flow-tile-container, flow-image-tile, flow-video-tile, .asset-item, [class*=tile], [class*=card], .timeline-contents');
  const roots = scope ? [...document.querySelectorAll(scope)] : [document];
  const out = [];
  for (const root of roots) {
    root.querySelectorAll('button, [role=button], [role=menuitem], [role=menuitemradio], [role=radio], [role=tab], mat-list-item, a.mat-mdc-list-item').forEach(e => {
      if (!(e.offsetWidth || e.offsetHeight) || inTile(e)) return;
      out.push((e.getAttribute('aria-label') || e.innerText || '').trim().split('\\n').join(' '));
    });
  }
  return out;
}"""
_COUNT_JS = "(sel) => { try { return document.querySelectorAll(sel).length } catch (e) { return -1 } }"
_MENU_BUTTONS = "flow-scene-builder button[aria-haspopup=menu], flow-scene-builder button[aria-haspopup=true]"
_PANE = ".cdk-overlay-pane"
_GROUPS_JS = """() => { const p=[...document.querySelectorAll('.cdk-overlay-pane')].find(e=>/generating will use/i.test(e.innerText||''));
 if(!p) return null;
 const groups=[...p.querySelectorAll('[role=radiogroup], mat-button-toggle-group, [role=group]')].map(g=>
   [...g.querySelectorAll('[role=radio]')].map(o=>({t:(o.textContent||'').trim().replace(/\\s+/g,' '), on:o.getAttribute('aria-checked')==='true'})));
 const m=(p.innerText||'').match(/Generating will use\\s+(\\d+)\\s+credits?/i);
 return {groups, price: m?+m[1]:null}; }"""
_MODEL_OPTIONS_JS = """() => [...document.querySelectorAll('[role=option], [role=menuitem]')].map(o => (o.innerText||'').trim().replace(/\\s+/g,' ').replace(/^volume_up\\s*/,'').replace(/^\\W+\\s*/,''))"""
_ICONS = re.compile(
    r"^(image|videocam|crop_free|chrome_extension|crop_16_9|crop_9_16|crop_landscape|crop_square|crop_portrait)\s*"
)


def _plain(text: str) -> str:
    return re.sub(r"info$", "", _ICONS.sub("", text)).strip()


class Walker:
    def __init__(self, session: FlowSession, project_id: str, folder: Path) -> None:
        self.session, self.project_id, self.folder = session, project_id, folder
        self.selectors = collect_selectors()
        self.private: set[str] = set()
        self.routes: dict[str, dict[str, Any]] = {}

    @property
    def page(self) -> Any:
        return self.session.page

    async def record(self, route: str, scope: str | None = None) -> None:
        await self.page.wait_for_timeout(1_500)
        raw = await self.page.evaluate(_LABELS_JS, scope)
        counts = {name: await self.page.evaluate(_COUNT_JS, sel) for name, sel in self.selectors.items()}
        self.routes[route] = {"labels": clean_labels(raw, self.private), "selectors": counts}
        await self.page.screenshot(
            path=str(self.folder / f"{re.sub(r'[^a-z0-9]+', '_', route.casefold())}.png")
        )

    async def guarded(self, route: str, step: Any) -> None:
        """One route that fails is written down as that route's error, and the walk goes on to the next."""
        try:
            await step
        except Exception as exc:  # noqa: BLE001
            self.routes[route] = {
                "labels": [],
                "selectors": {},
                "error": f"{type(exc).__name__}: {str(exc)[:160]}",
            }

    async def sidebar(self, index: int, route: str) -> None:
        await self.session.goto(self.session.project_url(self.project_id), ready=PROJECT_READY)
        await self.page.wait_for_timeout(2_000)
        await safe_click(self.page.locator(SIDEBAR).nth(index))
        await self.record(route)

    async def open_and_record(self, route: str, path: str, ready: str) -> None:
        await self.session.goto(f"{self.session.project_url(self.project_id)}/{path}", ready=ready)
        await self.record(route)

    async def clip_editor(self, media_id: str) -> None:
        """The editor and every toolbar menu, opened and closed with Escape; no item inside a menu is clicked."""
        page = self.page
        await clips._open(self.session, self.project_id, media_id)
        # The toolbar builds in stages and Add clip arrives late (clips.CONTROL_WAIT_MS), so wait before counting.
        await page.wait_for_timeout(clips.CONTROL_WAIT_MS)
        await self.record("clip editor")
        buttons = page.locator(_MENU_BUTTONS)
        for index in range(await buttons.count()):
            button = buttons.nth(index)
            if not await button.is_visible():
                continue
            name = (await button.get_attribute("aria-label")) or (await button.inner_text()) or ""
            route = route_name("clip editor menu", _plain(name.strip()), self.private, index)
            await self.guarded(route, self.menu(button, route))

    async def menu(self, button: Any, route: str) -> None:
        await safe_click(button)
        await self.page.wait_for_timeout(1_500)
        try:
            await self.record(route, _PANE)
        finally:
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(800)

    async def radio(self, text: str) -> bool:
        option = self.page.locator(f"{_PANE} button[role=radio]").filter(
            has_text=re.compile(rf"^\W*[a-z_0-9]*\s*{re.escape(text)}(info)?\s*$")
        )
        if await option.count() == 0:
            return False
        await safe_click(option.first)
        await self.page.wait_for_timeout(900)
        return True

    async def pane(self) -> dict[str, Any]:
        state = await self.page.evaluate(_GROUPS_JS)
        if state is None:
            await composer._open_settings(self.page, "survey")
            state = await self.page.evaluate(_GROUPS_JS)
        return state

    async def model_names(self) -> tuple[Any, list[str]]:
        dropdown = self.page.locator(f"{_PANE} button").filter(has_text=re.compile("arrow_drop_down")).first
        await safe_click(dropdown)
        await self.page.wait_for_timeout(1_500)
        names = await self.page.evaluate(_MODEL_OPTIONS_JS)
        return dropdown, names

    async def pick_model(self, name: str) -> None:
        await self.model_names()
        await safe_click(self.page.locator("[role=option], [role=menuitem]").filter(has_text=name).first)
        await self.page.wait_for_timeout(1_500)

    async def walk_settings(self) -> dict[str, Any]:
        await composer._open_settings(self.page, "survey")
        # The panel opens on the tab the last run left: Image after gen_i2i, which hides Frames and Ingredients.
        await self.radio("Video")
        await self.record("composer settings", _PANE)
        video: dict[str, Any] = {"aspects": [], "modes": {}}
        first_model = None
        groups = (await self.pane())["groups"]
        modes = [_plain(o["t"]) for o in groups[1]]
        for mode in modes:
            await self.radio(mode)
            _, names = await self.model_names()
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(600)
            await self.pane()
            video["modes"][mode] = {}
            first_model = first_model or (names[0] if names else None)
            for name in names:
                await self.pick_model(name)
                state = await self.pane()
                video["aspects"] = [_plain(o["t"]) for o in state["groups"][2]]
                rows = [[_plain(o["t"]) for o in g] for g in state["groups"][3:]]
                prices: dict[str, int | None] = {}
                for combo in itertools.product(*rows):
                    for text in combo:
                        await self.radio(text)
                    state = await self.pane()
                    checked = {_plain(o["t"]) for g in state["groups"] for o in g if o["on"]}
                    # A radio that did not take would file the previous cell's price under this one: no price instead.
                    prices[" ".join(combo)] = state["price"] if set(combo) <= checked else None
                video["modes"][mode][name] = {"rows": rows, "prices": prices}
        await self.radio("Image")
        state = await self.pane()
        _, image_models = await self.model_names()
        await self.page.keyboard.press("Escape")
        await self.page.wait_for_timeout(600)
        state = await self.pane()
        image = {
            "models": image_models,
            "aspects": [_plain(o["t"]) for o in state["groups"][1]],
            "counts": [int(_plain(o["t"])[1:]) for o in state["groups"][2]],
            "price_x1": state["price"],
        }
        await self.radio("Video")
        # The last cell priced (x4 of the dearest model) can outrun the balance and put Flow's insufficient-credits
        # warning where Start generation stands in the composer pages recorded next (survey 2026-09-30).
        if modes:
            await self.radio(modes[0])
        if first_model:
            await self.pick_model(first_model)
        await self.radio("x1")
        await self.page.keyboard.press("Escape")
        return {"measured": datetime.now().astimezone().strftime("%Y-%m-%d"), "video": video, "image": image}

    async def run(self) -> dict[str, Any]:
        session, page = self.session, self.page
        home = await reader.grid(session)
        self.private |= {str(p.get("title") or "") for p in parsers.projects(one(home, "UpteDb"))}
        await self.record("home")
        frames = await capture(
            session,
            lambda: session.goto(session.project_url(self.project_id), ready=PROJECT_READY),
            settle=8.0,
        )
        listing = one(frames, "Zzl0ze")
        records = parsers.records(listing)
        self.private |= {str(m.get("title") or "") for m in parsers.media(listing)}
        self.private |= {str(r.get("prompt") or "")[:MAX_LABEL] for r in records}
        self.private |= {str(c.get("name") or "") for c in parsers.characters_from_listing(listing)}
        self.private |= {str(s.get("title") or "") for s in parsers.scenes_from_listing(listing)}
        self.agent_was_on = bool((await agent.set_mode(session, self.project_id, False)).get("was"))
        await session.goto(session.project_url(self.project_id), ready=PROJECT_READY)
        await self.record("project")
        items = await page.evaluate(
            "(sel) => [...document.querySelectorAll(sel)].map(e => (e.innerText||'').trim().split('\\n').pop().trim())",
            SIDEBAR,
        )
        for index, item in enumerate(items):
            # Collapse folds the sidebar away; it is a toggle, not a page.
            if not item or SIDEBAR_ACTIONS.search(item):
                continue
            route = route_name("sidebar", item, self.private, index)
            await self.guarded(route, self.sidebar(index, route))
        await session.goto(session.project_url(self.project_id), ready=PROJECT_READY)
        await composer.clear_prompt(session)
        options = await self.walk_settings()
        for mode in options["video"]["modes"]:
            await composer._open_settings(page, "survey")
            await self.radio(mode)
            await page.keyboard.press("Escape")
            await self.record(f"composer {mode}", "flow-prompt-box, flow-base-prompt-box")
        videos = [
            r
            for r in records
            if r.get("kind") == "video" and r.get("listed") and r.get("status") == clips.DONE_STATUS
        ]
        if videos:
            await self.guarded("clip editor", self.clip_editor(videos[0]["id"]))
        scenes = [x for x in parsers.scenes_from_listing(listing) if not x.get("trashed")]
        if scenes:
            await self.guarded(
                "scene editor",
                self.open_and_record("scene editor", f"scene/{scenes[0]['scene_id']}", "flow-scene-builder"),
            )
        people = parsers.characters_from_listing(listing)
        if people:
            await self.guarded(
                "character page",
                self.open_and_record(
                    "character page", f"character/{people[0]['entity_id']}", "flow-character-edit-page"
                ),
            )
        if self.agent_was_on:
            await agent.set_mode(session, self.project_id, True)
        return {"ui": {"routes": self.routes}, "walk": options}


async def survey(
    session: FlowSession, project_id: str, out_root: Path = Path("out/survey")
) -> dict[str, Any]:
    folder = out_root / datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    result = await Walker(session, project_id, folder).run()
    (folder / "survey.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    result["folder"] = str(folder)
    return result


def report(
    result: dict[str, Any], *, write: bool
) -> tuple[list[tuple[str, str, str]], dict[str, Any] | None]:
    """Compare a survey with the baselines; with write, and only for a complete walk, make it the new baseline."""
    base_ui = json.loads(UI_BASELINE.read_text(encoding="utf-8")) if UI_BASELINE.exists() else {"routes": {}}
    base_options = json.loads(OPTIONS_BASELINE.read_text(encoding="utf-8"))
    findings = compare_ui(base_ui, result["ui"])
    try:
        options: dict[str, Any] | None = options_from_walk(result["walk"])
    except OptionLayoutChanged as exc:
        options = None
        findings.append(("option layout changed", "", str(exc)))
    if options is not None:
        findings += compare_options(base_options, options)
    if write:
        check_writable(result["ui"], options)
        UI_BASELINE.write_text(
            json.dumps(result["ui"], indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
        )
        OPTIONS_BASELINE.write_text(json.dumps(options, indent=1, ensure_ascii=False), encoding="utf-8")
    return findings, options


def run_sync(session_factory: Any, project_id: str, write: bool) -> tuple[list[tuple[str, str, str]], str]:
    async def go() -> dict[str, Any]:
        async with session_factory() as session:
            return await survey(session, project_id)

    result = asyncio.run(go())
    findings, _ = report(result, write=write)
    return findings, result["folder"]
