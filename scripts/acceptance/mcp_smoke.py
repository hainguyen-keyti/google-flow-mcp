"""MCP smoke: start the server in-memory and actually USE it, the way an agent would.

    uv run python scripts/acceptance/mcp_smoke.py

Exit code is 1 when any row is FAIL. Costs nothing, but it does drive a real browser: every read-only
tool is called against the live account, so a run takes roughly 2 to 3 minutes (`Backend._with` opens one
FlowSession per call, measured 7 to 14 seconds each on 2026-09-13).

Why it calls tools for real instead of counting them. Until 2026-09-13 this script called exactly ONE of
26 tools and accepted `len(names) >= 15`, so two capabilities (`flow clip reconcile` and `flow uploads`)
were missing from the MCP surface for weeks with every gate green. A tool nobody calls is a tool nobody
knows is broken.

Why a plausible payload is checked and not just `is_error`. Also measured 2026-09-13: `flow_media` used
to accept an `all_versions` argument it did not implement, drop it silently, and answer as if nothing had
been asked. That returns `is_error=False` and looks perfect, so `is_error` alone proves very little.

MUTATION TOOLS ARE NEVER CALLED (invariant I4). `agent_mode`, `scene_create`, `scene_delete`, `scene_restore`,
`project_create`, `project_delete` and `flow_upload` cost nothing but change the owner's real project;
a gate that ran them would quietly litter it on every run. The roster check below asserts they exist and
the run asserts they stayed untouched.

ONE spending tool is called, on its refusal path only (Plan H T5): `gen_character` with an out_dir outside out/ is
turned down inside the server before a browser opens and before anything can be spent, and the row fails unless the
reply says exactly that. The unit test that keeps the refusal ahead of the browser
(`test_an_editor_out_dir_outside_the_out_folder_is_refused_before_a_browser_opens`) goes red first if that order
ever changes, so this row cannot start costing money quietly.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from video import gen, mcp_server
from video.flow import parsers

SECRET = re.compile(r"SAPISID=|__Secure-|Authorization:")

# What the filtered row asks for and what it refuses to accept back (Plan H T3): 20 newest rows have to fit well
# under the size a client chokes on, the whole point of having filters at all.
MEDIA_LIMIT = 20
MEDIA_CEILING = 20_000

# Free AND side-effect free: safe to call on the owner's live account on every run.
READ_ONLY_NO_ARGS = ("flow_lane", "flow_projects", "flow_credits", "flow_tools", "flow_capabilities")
# The surveyed table itself, read here off the file and not through the code under test.
OPTIONS_FILE = Path(mcp_server.__file__).with_name("flow") / "flow_options.json"
READ_ONLY_PER_PROJECT = ("flow_media", "flow_characters", "flow_uploads", "scene_list")
# Read-only too, but it needs a scene id, which only scene_list's own answer can supply.
READ_ONLY_PER_SCENE = ("scene_clips",)
# Reads a character's own page, so it needs an entity id the way scene_clips needs a scene id.
READ_ONLY_PER_CHARACTER = ("flow_voices",)
# Reads what one clip was made from, so it needs a video the project's own listing named.
READ_ONLY_PER_MEDIA = ("clip_recipe",)
# The kinds the parser itself can answer, read from it so the two never drift apart.
RECIPE_KINDS = (*parsers._RECIPE_KINDS.values(), "unknown")
# Reads where one job stands. Called here on a job the ledger already settled, which is answered from its row.
READ_ONLY_PER_JOB = ("job_status",)

# Free but they CHANGE things. Never called here; see I4 in the plan.
MUTATING = (
    "agent_mode",
    "scene_create",
    "scene_delete",
    "scene_restore",
    "scene_add_clip",
    "scene_rename",
    "scene_set_aspect",
    "scene_remove_clip",
    "scene_move_clip",
    "project_create",
    "project_rename",
    "project_delete",
    "character_create",
    "character_delete",
    "character_set_voice",
    "character_make_voice",
    "character_clear_voice",
    "scene_save_clip",
    "clip_save_frame",
    "flow_upload",
    "clip_reconcile",
    "gen_t2i",
    "gen_i2i",
)

# Free for Flow but they write a file on this machine, so they are not called here either. job_collect also writes
# a job's settled row.
DOWNLOADING = ("flow_download", "scene_download", "job_collect")

# clip_download at 1080p was measured free, but its 4k rendition is a Flow upscale whose price has never been
# measured, and its own description says so. Filing it with the plainly free ones was a claim the tool contradicts
# (review 2026-09-16); a test in tests/test_mcp_server.py keeps the two from drifting apart again.
MAYBE_SPENDING = ("clip_download",)

# Real money. Never called here, and the roster row insists they stay declared rather than quietly vanish.
SPENDING = (
    "gen_t2v",
    "gen_i2v",
    "gen_r2v",
    "gen_character",
    "gen_video",
    "job_submit",
    "clip_extend",
    "clip_edit",
    "agent_send",
)

# One source for the roster contract: a tool that exists must be classified above, and a name declared above must
# still be served. The count used to be typed here as well, so adding three tools left this gate red while the unit
# test was green (review of plan scene-timeline-tools, 2026-09-16).
CLASSIFIED = (
    *READ_ONLY_NO_ARGS,
    *READ_ONLY_PER_PROJECT,
    *READ_ONLY_PER_SCENE,
    *READ_ONLY_PER_CHARACTER,
    *READ_ONLY_PER_MEDIA,
    *READ_ONLY_PER_JOB,
    *MUTATING,
    *DOWNLOADING,
    *MAYBE_SPENDING,
    *SPENDING,
)


def payload_of(result):
    text = "".join(getattr(chunk, "text", "") for chunk in result.content)
    try:
        return text, json.loads(text)
    except ValueError:
        return text, None


async def call(session, name, arguments, expect):
    """Call one tool and judge the answer, not merely the absence of an error.

    Returns the parsed payload too, so a caller never has to spend a second browser session re-asking
    for something it has already been told.
    """
    result = await session.call_tool(name, arguments)
    text, payload = payload_of(result)
    if result.is_error:
        return "FAIL", f"{name}: is_error, {text[:90]}", payload
    if SECRET.search(text):
        return "FAIL", f"{name}: session material leaked into the reply", payload
    if payload is None:
        return "FAIL", f"{name}: reply is not JSON, {text[:90]}", payload
    detail = expect(payload)
    if detail is not None:
        return "FAIL", f"{name}: {detail}", payload
    return "PASS", f"{name}: {text[:70].replace(chr(10), ' ')}", payload


def _each_has(items, *keys):
    return all(isinstance(item, dict) and all(item.get(key) for key in keys) for item in items)


def a_lane(payload):
    # Every other row depends on this account being on the migrated host (CLAUDE.md rule 1).
    if not isinstance(payload, dict):
        return f"expected an object, got {type(payload).__name__}"
    if payload.get("verdict") != "MIGRATED":
        return f"verdict is {payload.get('verdict')!r}, expected 'MIGRATED'"
    return None


def a_project_list(payload):
    # Ids are NOT all UUIDs: measured 2026-09-14, one project is `8822142b-ca75-46b7-aac8-03d2831_backfill`.
    if not isinstance(payload, list) or not payload:
        return "expected a non-empty list of projects"
    ids = [project.get("id") if isinstance(project, dict) else None for project in payload]
    if not all(isinstance(project_id, str) and project_id for project_id in ids):
        return "a project has no id"
    if len(set(ids)) != len(ids):
        return f"project ids repeat: {len(ids)} projects, {len(set(ids))} distinct ids"
    return None


def a_balance(payload):
    balance = payload.get("balance") if isinstance(payload, dict) else None
    if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
        return f"balance is {balance!r}, expected an int of 0 or more"
    return None


def a_capability_map(payload):
    """An agent budgets a film from this answer, so its video cells have to be the surveyed file's, which is read
    here off the file itself: an answer of the right shape with one price a credit off is a wrong budget."""
    if not isinstance(payload, dict) or not isinstance(payload.get("video"), dict):
        return f"expected an object holding a video table, got {str(payload)[:60]}"
    surveyed = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
    models = payload["video"].get("models")
    if not isinstance(models, dict) or sorted(models) != sorted(surveyed["video"]["models"]):
        return f"the models are {sorted(models) if isinstance(models, dict) else models!r}, the surveyed file holds others"
    for name, entry in surveyed["video"]["models"].items():
        said = models[name] if isinstance(models[name], dict) else {}
        cells = said.get("credits_x1")
        if not isinstance(cells, list) or not all(isinstance(cell, dict) for cell in cells):
            return f"{name} has no list of cells: {cells!r}"
        # Cell by cell, under the surveyed file's own keys: a bag of the right prices on the wrong cells is wrong.
        keys = [
            f"{cell.get('resolution')} {cell.get('seconds')}s" if entry["durations"] else "" for cell in cells
        ]
        priced = dict(zip(keys, (cell.get("credits") for cell in cells), strict=True))
        if len(priced) != len(cells) or priced != entry["price_x1"]:
            return f"{name}'s cells are {priced}, the surveyed file says {entry['price_x1']}"
        for key in ("resolutions", "durations"):
            held = said.get("seconds" if key == "durations" else key)
            if entry[key] and held != entry[key]:
                return f"{name}'s {key} are {held!r}, the surveyed file says {entry[key]}"
        if said.get("modes") != entry["modes"]:
            return f"{name}'s modes are {said.get('modes')!r}, the surveyed file says {entry['modes']}"
        for cap in ("image_ingredients", "voice_ingredients"):
            held = said.get(cap)
            if isinstance(held, bool) or not isinstance(held, int) or held < 0:
                return f"{name} has no {cap} cap: {held!r}"
    for key in ("modes", "aspects", "counts"):
        if payload["video"].get(key) != surveyed["video"][key]:
            return f"video {key} are {payload['video'].get(key)!r}, the surveyed file says {surveyed['video'][key]}"
    if payload["video"].get("measured") != surveyed["measured"]:
        return (
            f"dated {payload['video'].get('measured')!r}, the surveyed file is dated {surveyed['measured']!r}"
        )
    image = payload.get("image") if isinstance(payload.get("image"), dict) else {}
    composer = {
        "measured": surveyed["measured"],
        "models": surveyed["image"]["models"],
        "aspects": surveyed["image"]["aspects"],
        "counts": surveyed["image"]["counts"],
        "price_line_x1": surveyed["image"]["price_x1"],
    }
    if image.get("composer") != composer:
        return f"the image composer is {image.get('composer')!r}, the surveyed file says {composer}"
    return None


def media_of(project_id):
    def check(payload):
        if not isinstance(payload, dict):
            return f"expected an object, got {type(payload).__name__}"
        meta = payload.get("meta")
        meta_id = meta.get("id") if isinstance(meta, dict) else None
        if meta_id != project_id:
            return f"meta.id is {meta_id!r}, but the smoke asked for {project_id}"
        media = payload.get("media")
        if not isinstance(media, list):
            return f"media is {type(media).__name__}, expected a list"
        if any(not isinstance(item, dict) or item.get("project_id") != project_id for item in media):
            return "a media record belongs to another project"
        models = payload.get("models")
        if not models or not isinstance(models, list) or not all(isinstance(name, str) for name in models):
            return f"models is {models!r}, expected a non-empty list of names"
        return None

    return check


def versions_of(project_id):
    # all_versions keeps the object and adds `versions` (2026-09-15); a bare list is the old, broken shape.
    # Grid rows carry no `type` and version records always do, so a swallowed all_versions shows up here.
    def check(payload):
        if not isinstance(payload, dict):
            return f"expected an object, got {type(payload).__name__}"
        versions = payload.get("versions")
        if not isinstance(versions, list):
            return f"versions is {type(versions).__name__}, expected a list"
        for record in versions:
            if not isinstance(record, dict) or record.get("project_id") != project_id:
                return "a version record belongs to another project"
            if not isinstance(record.get("type"), str) or not record.get("workflow_id"):
                return "a record has no type or workflow_id: grid rows came back instead of versions"
        return None

    return check


def filtered_media(project_id, limit, ceiling):
    """The filters have to make the answer SMALLER and say what they cut, or an agent is back to a reply its
    client cannot hold: measured 2026-09-17, a full listing with versions weighs 122,919 characters."""

    def check(payload):
        if not isinstance(payload, dict):
            return f"expected an object, got {type(payload).__name__}"
        rows = []
        for key in ("media", "versions"):
            if not isinstance(payload.get(key), list):
                return f"{key} is {type(payload.get(key)).__name__}, expected a list"
            rows += payload[key]
            total = payload.get(f"{key}_total")
            if not isinstance(total, int) or total < len(payload[key]):
                return f"{key}_total is {total!r} for {len(payload[key])} rows: the answer hides what it cut"
            if len(payload[key]) > limit:
                return f"{key} holds {len(payload[key])} rows, more than the limit of {limit}"
        kinds = {row.get("kind") for row in rows}
        if kinds - {"video"}:
            return f"kind=video still answered {sorted(kinds)}"
        if any("url" in row for row in rows):
            return "brief=true still carries a url"
        long_prompt = [len(row["prompt"]) for row in rows if len(row.get("prompt") or "") > 121]
        if long_prompt:
            return f"brief=true left prompts of {long_prompt} characters"
        dropped = any(payload[f"{key}_total"] > len(payload[key]) for key in ("media", "versions"))
        if payload.get("truncated") is not dropped:
            return (
                f"truncated is {payload.get('truncated')!r} while "
                f"{'rows were' if dropped else 'no row was'} dropped"
            )
        size = len(json.dumps(payload, ensure_ascii=False, default=str))
        if size > ceiling:
            return f"the filtered answer is {size} characters, over the {ceiling} an agent can hold"
        return None

    return check


def a_voice_list(payload):
    """Measured 2026-09-18: 30 presets plus any voice saved on this account, each a name and a description like
    'Female, youthful, mid-high pitch'. The selector renders a window at a time, so a short answer means the
    sweep stopped early.

    This row is the only check that reads the REAL rows. A row's textContent runs the icon, the name and the
    description together with no whitespace ('voice_selectionAchernarFemale, soft, high pitch'), and a driver
    that reads that instead of the row's own elements answers names like that, which is why the name is checked
    for shape here rather than merely for being non-empty (review of this plan, 2026-09-18).
    """
    if not isinstance(payload, list):
        return f"expected a list, got {type(payload).__name__}"
    if len(payload) < 20:
        return f"only {len(payload)} voices came back; the selector held 30 when it was measured"
    if not _each_has(payload, "name", "description"):
        return "a voice has no name or description"
    for voice in payload:
        name = voice["name"]
        if name.split() != [name]:
            return (
                f"the voice name {name[:60]!r} carries whitespace, so the row was read as one run-on string"
            )
        if "voice_selection" in name or "settings_2" in name:
            return f"the voice name {name[:60]!r} is an icon ligature, not a name"
        if not isinstance(voice.get("custom"), bool):
            return f"voice {name!r} does not say whether it is one of your own"
    if all(voice["custom"] for voice in payload):
        # A flag read off the wrong element passes every other check here: 30 of these are Flow's own presets.
        return "every voice claims to be one of your own, so the custom flag is not being read"
    return None


def a_character_list(payload):
    if not isinstance(payload, list):
        return f"expected a list, got {type(payload).__name__}"
    return None if _each_has(payload, "entity_id", "name") else "a character has no entity_id or name"


def a_tool_list(payload):
    if not isinstance(payload, list) or not payload:
        return "expected a non-empty list of tools"
    return None if _each_has(payload, "id", "name") else "a tool has no id or name"


def a_scene_list(payload):
    # Called without include_trashed. Measured on c5d1301b: 10 scenes, none trashed; the flag adds 6 more.
    if not isinstance(payload, list):
        return f"expected a list, got {type(payload).__name__}"
    if not _each_has(payload, "scene_id"):
        return "a scene has no scene_id"
    trashed = sum(1 for scene in payload if scene.get("trashed") is True)
    return f"{trashed} trashed scene(s) in a listing that did not ask for them" if trashed else None


def timeline_of(scene_id):
    def check(payload):
        if not isinstance(payload, dict):
            return f"expected an object, got {type(payload).__name__}"
        if payload.get("scene_id") != scene_id:
            return f"scene_id is {payload.get('scene_id')!r}, asked for {scene_id!r}"
        clips = payload.get("clips")
        if not isinstance(clips, list) or not _each_has(clips, "clip_id"):
            return "clips is not a list of clips that each carry a clip_id"
        positions = [clip.get("position") for clip in clips]
        if positions != list(range(len(clips))):
            return f"clip positions read {positions}, not the film's order from 0"
        if payload.get("aspect") not in ("9:16", "16:9", None):
            return f"aspect is {payload.get('aspect')!r}"
        return None

    return check


def recipe_of(media_id):
    def check(payload):
        if not isinstance(payload, dict):
            return f"expected an object, got {type(payload).__name__}"
        if payload.get("media_id") != media_id:
            return f"asked for the recipe of {media_id}, got the one of {payload.get('media_id')!r}"
        if not isinstance(payload.get("model_key"), str) or not payload["model_key"]:
            return f"no model key: {payload.get('model_key')!r}"
        if payload.get("kind") not in RECIPE_KINDS:
            return f"kind is {payload.get('kind')!r}, not one of {RECIPE_KINDS}"
        for key, fields in (
            ("frames", ("slot", "workflow_id")),
            ("reference_images", ("workflow_id",)),
            ("voices", ("voice",)),
            ("characters", ("entity_id",)),
        ):
            rows = payload.get(key)
            if not isinstance(rows, list) or not _each_has(rows, *fields):
                return f"{key} is not a list of rows carrying {fields}: {str(rows)[:80]}"
        # A preset and a voice made on this account are told apart by this flag, and False is a real answer.
        if not all(isinstance(voice.get("custom"), bool) for voice in payload["voices"]):
            return f"a voice does not say whether it is custom: {str(payload['voices'])[:80]}"
        return None

    return check


def newest_settled_job(out_dir):
    """The job whose `done` row is the newest, among those one ledger alone holds and whose last row it is: the kind
    of job job_status answers from the ledger, with no browser. None when no ledger under the out folder has one."""
    last, held = {}, {}
    for path in sorted(out_dir.rglob("ledger.jsonl")):
        for row in gen.Ledger(path).rows():
            job_id = row.get("job_id")
            if isinstance(job_id, str) and job_id:
                last[job_id] = row
                held.setdefault(job_id, set()).add(path.resolve())
    done = [
        (row.get("ts") or 0, job_id)
        for job_id, row in last.items()
        if row.get("status") == "done" and len(held[job_id]) == 1
    ]
    return max(done)[1] if done else None


def settled_job(job_id):
    def check(payload):
        if not isinstance(payload, dict):
            return f"expected an object, got {type(payload).__name__}"
        if payload.get("job_id") != job_id:
            return f"asked where {job_id} stands, got the answer of {payload.get('job_id')!r}"
        if (payload.get("state"), payload.get("status")) != ("settled", "done"):
            return (
                f"state is {payload.get('state')!r} and status {payload.get('status')!r} for a job whose last "
                "row is done"
            )
        outcome = payload.get("outcome")
        if not isinstance(outcome, dict) or outcome.get("code") != "DONE":
            return f"the outcome of a done job is {str(outcome)[:80]}"
        return None

    return check


def an_upload_count(payload):
    """Why `a_dict` was not enough here. Measured 2026-09-14: flow_uploads was answering `count: null`,
    a doubled tile figure and an empty rpcid list on a project holding 4 uploads, and this gate stayed
    green through all of it, because every one of those replies is still a dict.
    """
    if not isinstance(payload, dict):
        return f"expected an object, got {type(payload).__name__}"
    count = payload.get("count")
    if isinstance(count, bool) or not isinstance(count, int):
        return f"count is {count!r}, expected an int"
    if count < 0:
        return f"count is {count}, expected 0 or more"
    return None


async def run(findings):
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        low = mcp_server.server._lowlevel_server
        serve = asyncio.create_task(
            low.run(
                server_streams[0],
                server_streams[1],
                low.create_initialization_options(),
                raise_exceptions=True,
            )
        )
        try:
            async with ClientSession(client_streams[0], client_streams[1]) as session:
                await session.initialize()
                names = sorted(tool.name for tool in (await session.list_tools()).tools)
                unclassified = sorted(set(names) - set(CLASSIFIED))
                vanished = sorted(set(CLASSIFIED) - set(names))
                findings.append(
                    {
                        "name": "roster",
                        "status": "PASS" if not unclassified and not vanished else "FAIL",
                        "detail": (
                            f"{len(names)} tools served, every one classified"
                            if not unclassified and not vanished
                            else f"unclassified {unclassified}; declared but not served {vanished}"
                        ),
                    }
                )
                missing = [tool for tool in MUTATING if tool not in names]
                findings.append(
                    {
                        "name": "mutating present",
                        "status": "PASS" if not missing else "FAIL",
                        "detail": f"declared but deliberately not called: {len(MUTATING)}"
                        if not missing
                        else f"missing from the roster: {missing}",
                    }
                )

                expectations = {
                    "flow_lane": a_lane,
                    "flow_projects": a_project_list,
                    "flow_credits": a_balance,
                    # Called with no project on purpose: that is the path that opens the first project itself.
                    "flow_tools": a_tool_list,
                    "flow_capabilities": a_capability_map,
                }
                projects = None
                for name in READ_ONLY_NO_ARGS:
                    status, detail, payload = await call(session, name, {}, expectations[name])
                    findings.append({"name": name, "status": status, "detail": detail})
                    if name == "flow_projects":
                        projects = payload

                project_id = None
                if isinstance(projects, list) and projects and isinstance(projects[0], dict):
                    project_id = projects[0].get("id")
                if not project_id:
                    findings.append(
                        {
                            "name": "project id",
                            "status": "FAIL",
                            "detail": "flow_projects gave no id, cannot exercise the per-project tools",
                        }
                    )
                    return
                findings.append(
                    {"name": "project id", "status": "PASS", "detail": f"exercising against {project_id}"}
                )

                per_project = {
                    "flow_media": media_of(project_id),
                    "flow_characters": a_character_list,
                    "flow_uploads": an_upload_count,
                    "scene_list": a_scene_list,
                }
                scenes = None
                characters = None
                listed = None
                for name in READ_ONLY_PER_PROJECT:
                    status, detail, payload = await call(
                        session, name, {"project_id": project_id}, per_project[name]
                    )
                    findings.append({"name": name, "status": status, "detail": detail})
                    if name == "scene_list":
                        scenes = payload
                    if name == "flow_characters":
                        characters = payload
                    if name == "flow_media":
                        listed = payload

                scene_id = None
                if isinstance(scenes, list) and scenes and isinstance(scenes[0], dict):
                    scene_id = scenes[0].get("scene_id")
                if scene_id:
                    arguments = {"project_id": project_id, "scene_id": scene_id}
                    status, detail, _ = await call(session, "scene_clips", arguments, timeline_of(scene_id))
                    findings.append({"name": "scene_clips", "status": status, "detail": detail})
                else:
                    findings.append(
                        {
                            "name": "scene_clips",
                            "status": "SKIP",
                            "detail": "the project holds no scene to read",
                        }
                    )

                entity = None
                if isinstance(characters, list) and characters and isinstance(characters[0], dict):
                    entity = characters[0].get("entity_id")
                if entity:
                    status, detail, _ = await call(
                        session, "flow_voices", {"project_id": project_id, "entity_id": entity}, a_voice_list
                    )
                    findings.append({"name": "flow_voices", "status": status, "detail": detail})
                else:
                    findings.append(
                        {
                            "name": "flow_voices",
                            "status": "SKIP",
                            "detail": "the project holds no character to read voices from",
                        }
                    )

                status, detail, _ = await call(
                    session,
                    "flow_media",
                    {"project_id": project_id, "all_versions": True},
                    versions_of(project_id),
                )
                findings.append({"name": "flow_media all", "status": status, "detail": detail})

                status, detail, filtered = await call(
                    session,
                    "flow_media",
                    {
                        "project_id": project_id,
                        "all_versions": True,
                        "kind": "video",
                        "limit": MEDIA_LIMIT,
                        "brief": True,
                    },
                    filtered_media(project_id, MEDIA_LIMIT, MEDIA_CEILING),
                )
                if status == "PASS":
                    # The size IS the point of this row, so it goes in the table rather than in a truncated echo.
                    size = len(json.dumps(filtered, ensure_ascii=False, default=str))
                    detail = (
                        f"{size} characters under a ceiling of {MEDIA_CEILING}: "
                        f"{len(filtered['media'])} of {filtered['media_total']} media, "
                        f"{len(filtered['versions'])} of {filtered['versions_total']} versions"
                    )
                findings.append({"name": "flow_media filtered", "status": status, "detail": detail})

                # Taken from the plain listing, so a broken versions read fails its own rows and not this one. A voice
                # saved on the account is listed as a video too, with no model (measured 2026-10-01), and has no recipe.
                grid = listed.get("media") if isinstance(listed, dict) else None
                video_id = next(
                    (
                        row.get("id")
                        for row in grid or []
                        if isinstance(row, dict)
                        and row.get("kind") == "video"
                        and row.get("model")
                        and row.get("id")
                    ),
                    None,
                )
                if video_id:
                    status, detail, _ = await call(
                        session,
                        "clip_recipe",
                        {"project_id": project_id, "media_id": video_id},
                        recipe_of(video_id),
                    )
                    findings.append({"name": "clip_recipe", "status": status, "detail": detail})
                else:
                    findings.append(
                        {
                            "name": "clip_recipe",
                            "status": "SKIP",
                            "detail": "the project holds no generated video to read a recipe from",
                        }
                    )

                # A settled job is answered from its ledger row, so this row reads nothing off Flow and changes nothing.
                job_id = newest_settled_job(mcp_server.backend.out_dir)
                if job_id:
                    status, detail, _ = await call(
                        session, "job_status", {"job_id": job_id}, settled_job(job_id)
                    )
                    findings.append({"name": "job_status", "status": status, "detail": detail})
                else:
                    findings.append(
                        {
                            "name": "job_status",
                            "status": "SKIP",
                            "detail": "no ledger under the out folder holds a settled job to ask about",
                        }
                    )

                # The one spending tool this gate may touch, because the refusal happens before a browser opens and
                # before a single credit can move: an out_dir outside out/ has to be turned down, not obeyed.
                refusal = await session.call_tool(
                    "gen_character",
                    {
                        "project": project_id,
                        "prompt": "never submitted",
                        "characters": ["not-an-entity"],
                        "job_id": "smoke-out-dir-refusal",
                        "out_dir": "/tmp/outside-the-out-folder",
                    },
                )
                text, _ = payload_of(refusal)
                accepted = not refusal.is_error or "out_dir must be inside" not in text
                findings.append(
                    {
                        "name": "gen_character out_dir",
                        "status": "FAIL" if accepted else "PASS",
                        "detail": f"an out_dir outside out/ was not refused: {text[:90]}"
                        if accepted
                        else "an out_dir outside out/ is refused before a browser opens",
                    }
                )
        finally:
            serve.cancel()


async def main() -> int:
    findings: list[dict[str, str]] = []
    try:
        await run(findings)
    except Exception as exc:  # noqa: BLE001
        findings.append({"name": "run", "status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"})
    print(f"{'STATUS':7s} {'ROW':18s} DETAIL")
    for finding in findings:
        print(f"{finding['status']:7s} {finding['name']:18s} {finding['detail']}")
    failed = [f for f in findings if f["status"] == "FAIL"]
    print(f"\nrows={len(findings)} pass={len(findings) - len(failed)} fail={len(failed)}")
    return 1 if failed or not findings else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
