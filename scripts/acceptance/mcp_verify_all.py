"""Live verification of every MCP tool, one call at a time, on a gflow profile of its own (first run 2026-10-08:
65 PASS, 3 FAIL, 66 credits over 49 tools).

usage: uv run python scripts/acceptance/mcp_verify_all.py --profile <name> --ceiling <credits> [--image <png>]
       [--from STEP] [--only A,B] [--fresh] [--list]

Starts the server in memory with a Backend on the given profile, runs the steps below in order, reads the balance
around every step that can spend, and refuses to start a paid step that could pass the ceiling. Every answer is
written whole to out/verify/results.jsonl; ids an earlier step answered are kept in out/verify/ctx.json so a rerun
with --from continues the same objects; the steps that upload or frame an image need --image (a png, jpg, jpeg or
webp). Nothing here retries a paid call: a paid step that errors is recorded and the run goes on to the next step
that does not depend on it. A second session on the same profile waits for the lease (GFLOW_CLI_LEASE_WAIT_SECONDS,
900 s unless set).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("GFLOW_CLI_LEASE_WAIT_SECONDS", "900")
os.environ.setdefault("GFLOW_CLI_LOG_LEVEL", "WARNING")

from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from video import mcp_server

BASE = Path("out") / "verify"
OUT = BASE / "out"
RESULTS = BASE / "results.jsonl"
CTX = BASE / "ctx.json"
RUN = time.strftime("%Y%m%d-%H%M%S")
PROMPT_STILL = "A small yellow rubber duck floating in a white bathtub full of foam, soft daylight, static camera, no people"


def _ids(answer: Any, *keys: str) -> Any:
    if isinstance(answer, dict):
        for key in keys:
            if answer.get(key):
                return answer[key]
    return None


def _outputs_media(answer: Any) -> str | None:
    outs = answer.get("outputs") if isinstance(answer, dict) else None
    if isinstance(outs, list) and outs and isinstance(outs[0], dict):
        return outs[0].get("media_id")
    return _ids(answer, "media_id")


def need(ctx: dict[str, Any], *keys: str) -> str | None:
    missing = [k for k in keys if not ctx.get(k)]
    return f"needs {missing} from an earlier step" if missing else None


# Each step: name, tool, args(ctx) -> dict, check(answer, ctx) -> list[str] (problems; may store ids into ctx),
# price (expected credits, 0 for free; a paid step reads the balance on both sides), needs (ctx keys).
STEPS: list[dict[str, Any]] = []


def step(name, tool, args, check=None, price=0, needs=(), poll=None):
    STEPS.append(
        {
            "name": name,
            "tool": tool,
            "args": args,
            "check": check,
            "price": price,
            "needs": needs,
            "poll": poll,
        }
    )


def ck_lane(a, ctx):
    return [] if a.get("verdict") == "MIGRATED" else [f"verdict {a.get('verdict')!r}"]


def ck_credits(a, ctx):
    b = a.get("balance")
    if isinstance(b, bool) or not isinstance(b, int):
        return [f"balance {b!r}"]
    ctx.setdefault("balance0", b)
    ctx["balance_last"] = b
    return []


def ck_projects(a, ctx):
    if not isinstance(a, list) or not a or not all(isinstance(p, dict) and p.get("id") for p in a):
        return ["not a list of projects with ids"]
    ctx["project_count"] = len(a)
    return []


def ck_caps(a, ctx):
    models = (a.get("video") or {}).get("models") or {}
    return [] if "omni-flash" in models else [f"video models {list(models)}"]


def ck_project_create(a, ctx):
    pid = _ids(a, "id", "project_id")
    if not pid:
        return [f"no id in {json.dumps(a)[:200]}"]
    ctx["P"] = pid
    return []


def ck_project_rename(a, ctx):
    return [] if "renamed" in str(a.get("title", "")) else [f"title {a.get('title')!r}"]


def ck_projects_has_P(a, ctx):
    ids = [p.get("id") for p in a] if isinstance(a, list) else []
    if ctx["P"] not in ids:
        return [f"{ctx['P']} not on the grid ({len(ids)} projects)"]
    title = next(p.get("title") for p in a if p.get("id") == ctx["P"])
    return [] if "renamed" in str(title) else [f"grid title {title!r}"]


def ck_upload(a, ctx):
    mid = _ids(a, "media_id")
    if not mid:
        return [f"no media_id in {json.dumps(a)[:200]}"]
    ctx["U"] = mid
    ctx["U_workflow"] = a.get("workflow_id")
    return [] if "found_by" not in a else ["settled by the listing, not by Flow's reply"]


def ck_media_has_U(a, ctx):
    rows = a.get("media") if isinstance(a, dict) else None
    row = next((r for r in rows or [] if r.get("id") == ctx["U"]), None)
    if row is None:
        return [f"upload {ctx['U']} not listed among {len(rows or [])} media"]
    return [] if row.get("kind") == "image" else [f"kind {row.get('kind')!r}"]


def ck_uploads_count(a, ctx):
    return [] if isinstance(a.get("count"), int) and a["count"] >= 1 else [f"count {a.get('count')!r}"]


def ck_download(a, ctx):
    path = a.get("path") if isinstance(a, dict) else a
    p = Path(str(path))
    if not p.is_file() or p.stat().st_size == 0:
        return [f"no file at {path}"]
    ctx["U_file"] = str(p)
    return []


def ck_image_out(key):
    def check(a, ctx):
        mid = _outputs_media(a)
        if not mid:
            return [f"no output media_id in {json.dumps(a)[:200]}"]
        ctx[key] = mid
        return []

    return check


def ck_tools(a, ctx):
    return [] if isinstance(a, list) and a else ["no tools listed"]


def ck_character_create(a, ctx):
    eid = _ids(a, "entity_id")
    if not eid:
        return [f"no entity_id in {json.dumps(a)[:200]}"]
    ctx["E"] = eid
    return []


def ck_characters_has_E(a, ctx):
    row = (
        next((r for r in a if isinstance(r, dict) and r.get("entity_id") == ctx["E"]), None)
        if isinstance(a, list)
        else None
    )
    if row is None:
        return [f"{ctx['E']} not listed in {json.dumps(a)[:200]}"]
    ctx["E_portrait"] = row.get("portrait_media_id")
    return []


def ck_voices(a, ctx):
    if not isinstance(a, list) or not a or not a[0].get("name"):
        return [f"no voices in {json.dumps(a)[:200]}"]
    ctx["voice"] = a[0]["name"]
    ctx["voice_count"] = len(a)
    return []


def ck_voice_is(a, ctx):
    return [] if a.get("voice") == ctx["voice"] else [f"voice {a.get('voice')!r} wanted {ctx['voice']!r}"]


def ck_voice_none(a, ctx):
    return [] if a.get("voice") is None else [f"voice {a.get('voice')!r} wanted None"]


def ck_no_error(a, ctx):
    return []


def ck_agent(enabled):
    def check(a, ctx):
        got = a.get("enabled") if isinstance(a, dict) else None
        return [] if got is enabled or got is None else [f"enabled {got!r} wanted {enabled}"]

    return check


def ck_scene_create(a, ctx):
    sid = _ids(a, "scene_id", "id")
    if not sid:
        return [f"no scene_id in {json.dumps(a)[:200]}"]
    ctx["S"] = sid
    return []


def ck_scene_rename(a, ctx):
    return [] if "renamed" in str(a.get("title", "")) else [f"title {a.get('title')!r}"]


def ck_scene_list_has_S(trashed):
    def check(a, ctx):
        row = (
            next(
                (s for s in a if isinstance(s, dict) and (s.get("scene_id") or s.get("id")) == ctx["S"]), None
            )
            if isinstance(a, list)
            else None
        )
        if row is None:
            return [f"scene {ctx['S']} not listed ({json.dumps(a)[:160]})"]
        if trashed is not None and bool(row.get("trashed")) is not trashed:
            return [f"trashed {row.get('trashed')!r} wanted {trashed}"]
        return []

    return check


def ck_quote(key, expect=None):
    def check(a, ctx):
        q = a.get("quoted_credits")
        if not isinstance(q, int):
            return [f"no quoted_credits in {json.dumps(a)[:200]}"]
        ctx[key] = q
        return [] if expect is None or q == expect else [f"quoted {q}, table says {expect}"]

    return check


def ck_video_done(key):
    def check(a, ctx):
        code = (a.get("outcome") or {}).get("code")
        mid = _outputs_media(a)
        if mid:
            ctx[key] = mid
        return [] if code == "DONE" and mid else [f"outcome {code!r}, media {mid!r}"]

    return check


def ck_refused_job_id(a, ctx):
    return ["was not refused"]


def ck_submit(a, ctx):
    if a.get("state") != "started":
        return [f"state {a.get('state')!r}"]
    ctx["J_workflow"] = a.get("workflow_id")
    return []


def ck_status_ready(a, ctx):
    return [] if a.get("state") == "ready" else [f"state {a.get('state')!r}"]


def ck_collect(a, ctx):
    mid = a.get("media_id")
    if a.get("state") != "collected" or not mid:
        return [f"state {a.get('state')!r} media {mid!r}"]
    ctx["V3"] = mid
    return []


def ck_scene_clips(count, key_prefix="C"):
    def check(a, ctx):
        clips = a.get("clips") if isinstance(a, dict) else None
        if not isinstance(clips, list) or len(clips) != count:
            return [f"{len(clips) if isinstance(clips, list) else clips!r} clips, wanted {count}"]
        for n, c in enumerate(clips, 1):
            ctx[f"{key_prefix}{n}"] = c.get("clip_id")
        ctx["scene_seconds"] = a.get("seconds")
        return [] if all(c.get("clip_id") for c in clips) else ["a clip without clip_id"]

    return check


def ck_moved(a, ctx):
    clips = a.get("clips") if isinstance(a, dict) else None
    if not isinstance(clips, list) or len(clips) != 2:
        return [f"{clips!r}"]
    return [] if clips[0].get("clip_id") == ctx.get("C2") else ["clip 2 is not first after the move"]


def ck_scene_download(a, ctx):
    path = a.get("path") if isinstance(a, dict) else None
    p = Path(str(path))
    if not p.is_file() or p.stat().st_size == 0:
        return [f"no film at {path}"]
    ctx["S_film"] = str(p)
    return []


def ck_file(a, ctx):
    path = a.get("path") if isinstance(a, dict) else a
    p = Path(str(path))
    return [] if p.is_file() and p.stat().st_size > 0 else [f"no file at {path}"]


def ck_error_mentions(word):
    def check(a, ctx):
        return [f"expected an error mentioning {word!r}, got an answer"]

    return check


def ck_recipe(a, ctx):
    kind = a.get("kind") if isinstance(a, dict) else None
    return [] if kind else [f"no kind in {json.dumps(a)[:200]}"]


def ck_save_frame(a, ctx):
    mid = _ids(a, "media_id")
    if not mid:
        return [f"no media_id in {json.dumps(a)[:200]}"]
    ctx["F"] = mid
    return []


def ck_extend(a, ctx):
    sid = a.get("scene_id")
    code = (a.get("outcome") or {}).get("code")
    if not sid:
        return [f"no scene_id, outcome {code!r}"]
    ctx["S_ext"] = sid
    return []


def ck_edit(a, ctx):
    code = (a.get("outcome") or {}).get("code")
    outs = a.get("outputs") or []
    return [] if code == "DONE" and outs else [f"outcome {code!r}, outputs {len(outs)}"]


def ck_reconcile(a, ctx):
    return [] if isinstance(a.get("jobs"), list) else [f"jobs {a.get('jobs')!r}"]


def ck_agent_send(a, ctx):
    # A reply is a request heard: an answer with rpcids [] was a send still being answered, never DONE (AQ11).
    if not isinstance(a, dict):
        return ["no object answered"]
    return (
        [] if a.get("rpcids") else ["no request heard (rpcids []), yet the tool answered instead of erring"]
    )


def ck_job_settled(a, ctx):
    return [] if a.get("state") in ("settled", "collected") else [f"state {a.get('state')!r}"]


def ck_characters_empty(a, ctx):
    ids = [r.get("entity_id") for r in a] if isinstance(a, list) else None
    return [] if ids is not None and ctx["E"] not in ids else [f"{ctx['E']} still listed"]


def ck_projects_lacks_P(a, ctx):
    ids = [p.get("id") for p in a] if isinstance(a, list) else []
    return [] if ctx["P"] not in ids else [f"{ctx['P']} still on the grid"]


def ck_final_credits(a, ctx):
    ctx["balance_end"] = a.get("balance")
    return []


# ---- the sequence ----
step("F01 lane", "flow_lane", lambda c: {}, ck_lane)
step("F02 credits", "flow_credits", lambda c: {}, ck_credits)
step("F03 projects", "flow_projects", lambda c: {}, ck_projects)
step("F04 capabilities", "flow_capabilities", lambda c: {}, ck_caps)
step("F05 project_create", "project_create", lambda c: {"title": "verify-20261008"}, ck_project_create)
step(
    "F06 project_rename",
    "project_rename",
    lambda c: {"project_id": c["P"], "title": "verify-20261008 renamed"},
    ck_project_rename,
    needs=("P",),
)
step("F07 projects has P", "flow_projects", lambda c: {}, ck_projects_has_P, needs=("P",))
step(
    "F08 flow_upload",
    "flow_upload",
    lambda c: {"project_id": c["P"], "path": c["IMG"]},
    ck_upload,
    needs=("P", "IMG"),
)
step(
    "F09 flow_media has U",
    "flow_media",
    lambda c: {"project_id": c["P"], "kind": "image"},
    ck_media_has_U,
    needs=("P", "U"),
)
step("F10 flow_uploads", "flow_uploads", lambda c: {"project_id": c["P"]}, ck_uploads_count, needs=("P",))
step(
    "F11 flow_download U",
    "flow_download",
    lambda c: {"project_id": c["P"], "media_id": c["U"], "out_dir": str(OUT / "dl")},
    ck_download,
    needs=("P", "U"),
)
step(
    "F12 gen_t2i",
    "gen_t2i",
    lambda c: {"project": c["P"], "prompt": PROMPT_STILL, "aspect": "9:16", "job_id": f"v8-t2i-{RUN}"},
    ck_image_out("I"),
    price=0,
    needs=("P",),
)
step(
    "F13 gen_i2i",
    "gen_i2i",
    lambda c: {
        "project": c["P"],
        "refs": [c["IMG"]],
        "prompt": "The same subject, photographed at golden hour, same framing",
        "aspect": "9:16",
        "job_id": f"v8-i2i-{RUN}",
    },
    ck_image_out("I2"),
    price=0,
    needs=("P", "IMG"),
)
step("F14 flow_tools", "flow_tools", lambda c: {"project_id": c["P"]}, ck_tools, needs=("P",))
step(
    "F15 character_create",
    "character_create",
    lambda c: {
        "project_id": c["P"],
        "prompt": "A friendly young woman with short black hair and round glasses, plain white t-shirt, neutral studio portrait, looking at the camera",
        "name": "Verify Bot",
        "personality": "calm and curious",
        "wait": True,
    },
    ck_character_create,
    needs=("P",),
)
step(
    "F16 flow_characters",
    "flow_characters",
    lambda c: {"project_id": c["P"]},
    ck_characters_has_E,
    needs=("P", "E"),
)
step(
    "F17 flow_voices",
    "flow_voices",
    lambda c: {"project_id": c["P"], "entity_id": c["E"]},
    ck_voices,
    needs=("P", "E"),
)
step(
    "F18 character_set_voice",
    "character_set_voice",
    lambda c: {"project_id": c["P"], "entity_id": c["E"], "voice": c["voice"]},
    ck_voice_is,
    needs=("P", "E", "voice"),
)
step(
    "F19 character_clear_voice",
    "character_clear_voice",
    lambda c: {"project_id": c["P"], "entity_id": c["E"]},
    ck_voice_none,
    needs=("P", "E"),
)
step(
    "F20 character_make_voice",
    "character_make_voice",
    lambda c: {
        "project_id": c["P"],
        "entity_id": c["E"],
        "preset": c["voice"],
        "performance": "giọng nữ Sài Gòn, nhỏ nhẹ, khoảng 25 tuổi",
        "name": f"VerifyVoice {RUN[-6:]}",
        "sample": "Xin chào mọi người, hôm nay mình thử giọng nè.",
        "attach": False,
    },
    ck_no_error,
    needs=("P", "E", "voice"),
)
step(
    "F21 agent_mode on",
    "agent_mode",
    lambda c: {"project_id": c["P"], "enabled": True},
    ck_agent(True),
    needs=("P",),
)
step(
    "F22 agent_mode off",
    "agent_mode",
    lambda c: {"project_id": c["P"], "enabled": False},
    ck_agent(False),
    needs=("P",),
)
step(
    "F23 scene_create",
    "scene_create",
    lambda c: {"project_id": c["P"], "title": "verify scene"},
    ck_scene_create,
    needs=("P",),
)
step(
    "F24 scene_rename",
    "scene_rename",
    lambda c: {"project_id": c["P"], "scene_id": c["S"], "title": "verify scene renamed"},
    ck_scene_rename,
    needs=("P", "S"),
)
step(
    "F25 scene_list has S",
    "scene_list",
    lambda c: {"project_id": c["P"]},
    ck_scene_list_has_S(False),
    needs=("P", "S"),
)
# paid
step(
    "P01 gen_video dry Frames",
    "gen_video",
    lambda c: {
        "project": c["P"],
        "prompt": PROMPT_STILL + ", the duck bobs gently",
        "model": "omni-flash",
        "resolution": "360p",
        "duration": 4,
        "aspect": "9:16",
        "start_frame": c["U"],
        "dry_run": True,
    },
    ck_quote("q_frames", 4),
    needs=("P", "U"),
)
step(
    "P02 gen_video Frames",
    "gen_video",
    lambda c: {
        "project": c["P"],
        "prompt": PROMPT_STILL + ", the duck bobs gently",
        "model": "omni-flash",
        "resolution": "360p",
        "duration": 4,
        "aspect": "9:16",
        "start_frame": c["U"],
        "job_id": f"v8-frames-{RUN}",
        "max_credits": c.get("q_frames", 4),
    },
    ck_video_done("V1"),
    price=4,
    needs=("P", "U", "q_frames"),
)
step(
    "P03 gen_video same job_id refused",
    "gen_video",
    lambda c: {"project": c["P"], "prompt": "x", "job_id": f"v8-frames-{RUN}", "max_credits": 4},
    ck_refused_job_id,
    needs=("P", "V1"),
)
step(
    "P04 gen_video dry Ingredients",
    "gen_video",
    lambda c: {
        "project": c["P"],
        "prompt": "The duck from the reference image floats in a bathtub of foam, static camera",
        "model": "omni-flash",
        "resolution": "360p",
        "duration": 4,
        "aspect": "9:16",
        "media_ids": [c["U"]],
        "dry_run": True,
    },
    ck_quote("q_ingr"),
    needs=("P", "U"),
)
step(
    "P05 gen_video Ingredients",
    "gen_video",
    lambda c: {
        "project": c["P"],
        "prompt": "The duck from the reference image floats in a bathtub of foam, static camera",
        "model": "omni-flash",
        "resolution": "360p",
        "duration": 4,
        "aspect": "9:16",
        "media_ids": [c["U"]],
        "job_id": f"v8-ingr-{RUN}",
        "max_credits": c.get("q_ingr", 4),
    },
    ck_video_done("V2"),
    price=4,
    needs=("P", "U", "q_ingr"),
)
step(
    "P06 job_submit",
    "job_submit",
    lambda c: {
        "project": c["P"],
        "prompt": "A paper boat drifting on a calm pond at sunrise, static camera, no people",
        "model": "omni-flash",
        "resolution": "360p",
        "duration": 4,
        "aspect": "9:16",
        "job_id": f"v8-sub-{RUN}",
        "max_credits": 4,
    },
    ck_submit,
    price=4,
    needs=("P",),
)
step(
    "P07 job_status until ready",
    "job_status",
    lambda c: {"job_id": c.get("sub_job") or f"v8-sub-{RUN}"},
    ck_status_ready,
    needs=("P",),
    poll={"every": 45, "limit": 900, "until": "ready"},
)
step(
    "P08 job_collect",
    "job_collect",
    lambda c: {"job_id": c.get("sub_job") or f"v8-sub-{RUN}"},
    ck_collect,
    needs=("P",),
)
step(
    "P09 gen_character dry",
    "gen_character",
    lambda c: {
        "project": c["P"],
        "prompt": "She smiles and waves at the camera in a bright room",
        "characters": [c["E"]],
        "model": "veo-lite",
        "aspect": "9:16",
        "dry_run": True,
    },
    ck_quote("q_char", 10),
    needs=("P", "E"),
)
step(
    "P10 gen_character",
    "gen_character",
    lambda c: {
        "project": c["P"],
        "prompt": "She smiles and waves at the camera in a bright room",
        "characters": [c["E"]],
        "model": "veo-lite",
        "aspect": "9:16",
        "job_id": f"v8-char-{RUN}",
        "out_dir": str(OUT / "dl"),
    },
    ck_video_done("V4"),
    price=10,
    needs=("P", "E", "q_char"),
)
step(
    "P11 gen_t2v veo-lite",
    "gen_t2v",
    lambda c: {
        "project": c["P"],
        "prompt": "A red kite flying over a green hill under a blue sky, static camera, no people",
        "model": "veo-lite",
        "aspect": "9:16",
        "job_id": f"v8-t2v-{RUN}",
    },
    ck_video_done("V5"),
    price=10,
    needs=("P",),
)
step(
    "P12 gen_i2v omni 4s",
    "gen_i2v",
    lambda c: {
        "project": c["P"],
        "initial_frame": c["U_file"],
        "prompt": "The kite drifts slowly to the right, static camera",
        "model": "omni-flash",
        "duration": 4,
        "aspect": "9:16",
        "job_id": f"v8-i2v-{RUN}",
    },
    ck_video_done("V6"),
    price=7,
    needs=("P", "U_file"),
)
step(
    "P13 gen_r2v veo-lite",
    "gen_r2v",
    lambda c: {
        "project": c["P"],
        "refs": [c["IMG"]],
        "prompt": "The subject of the reference image over the sea, static camera",
        "model": "veo-lite",
        "aspect": "9:16",
        "job_id": f"v8-r2v-{RUN}",
    },
    ck_video_done("V7"),
    price=10,
    needs=("P", "IMG"),
)
# scenes with the clips made above
step(
    "S01 scene_add_clip V1",
    "scene_add_clip",
    lambda c: {"project_id": c["P"], "scene_id": c["S"], "media_id": c["V1"]},
    ck_no_error,
    needs=("P", "S", "V1"),
)
step(
    "S02 scene_add_clip V5",
    "scene_add_clip",
    lambda c: {"project_id": c["P"], "scene_id": c["S"], "media_id": c["V5"]},
    ck_no_error,
    needs=("P", "S", "V5"),
)
step(
    "S02b scene_add_clip V7",
    "scene_add_clip",
    lambda c: {"project_id": c["P"], "scene_id": c["S"], "media_id": c["V7"]},
    ck_no_error,
    needs=("P", "S", "V7"),
)
step(
    "S03 scene_clips 2",
    "scene_clips",
    lambda c: {"project_id": c["P"], "scene_id": c["S"]},
    ck_scene_clips(2),
    needs=("P", "S", "V1", "V5"),
)
step(
    "S04 scene_move_clip",
    "scene_move_clip",
    lambda c: {"project_id": c["P"], "scene_id": c["S"], "clip_id": c["C2"], "position": 0},
    ck_no_error,
    needs=("P", "S", "C2"),
)
step(
    "S05 scene_clips moved",
    "scene_clips",
    lambda c: {"project_id": c["P"], "scene_id": c["S"]},
    ck_moved,
    needs=("P", "S", "C2"),
)
step(
    "S06 scene_set_aspect",
    "scene_set_aspect",
    lambda c: {"project_id": c["P"], "scene_id": c["S"], "aspect": "9:16"},
    ck_no_error,
    needs=("P", "S"),
)
step(
    "S07 scene_save_clip",
    "scene_save_clip",
    lambda c: {"project_id": c["P"], "scene_id": c["S"], "clip_id": c["C1"]},
    ck_no_error,
    needs=("P", "S", "C1"),
)
step(
    "S08 scene_download",
    "scene_download",
    lambda c: {"project_id": c["P"], "scene_id": c["S"], "out_dir": str(OUT / "dl")},
    ck_scene_download,
    needs=("P", "S", "C1"),
)
step(
    "S09 scene_remove_clip",
    "scene_remove_clip",
    lambda c: {"project_id": c["P"], "scene_id": c["S"], "clip_id": c["C2"]},
    ck_no_error,
    needs=("P", "S", "C2"),
)
step(
    "S10 scene_clips 1",
    "scene_clips",
    lambda c: {"project_id": c["P"], "scene_id": c["S"]},
    ck_scene_clips(1, "D"),
    needs=("P", "S", "C2"),
)
step(
    "S11 scene_delete",
    "scene_delete",
    lambda c: {"project_id": c["P"], "scene_id": c["S"]},
    ck_no_error,
    needs=("P", "S"),
)
step(
    "S12 scene_list trashed",
    "scene_list",
    lambda c: {"project_id": c["P"], "include_trashed": True},
    ck_scene_list_has_S(True),
    needs=("P", "S"),
)
step(
    "S13 scene_restore",
    "scene_restore",
    lambda c: {"project_id": c["P"], "scene_id": c["S"]},
    ck_no_error,
    needs=("P", "S"),
)
step(
    "S14 scene_list restored",
    "scene_list",
    lambda c: {"project_id": c["P"]},
    ck_scene_list_has_S(False),
    needs=("P", "S"),
)
# clip tools
step(
    "C01 clip_recipe V1",
    "clip_recipe",
    lambda c: {"project_id": c["P"], "media_id": c["V1"]},
    ck_recipe,
    needs=("P", "V1"),
)
step(
    "C02 clip_download 1080p",
    "clip_download",
    lambda c: {"project_id": c["P"], "media_id": c["V5"], "quality": "1080p", "out_dir": str(OUT / "dl")},
    ck_file,
    needs=("P", "V5"),
)
step(
    "C03 clip_download 4k refused",
    "clip_download",
    lambda c: {"project_id": c["P"], "media_id": c["V5"], "quality": "4k", "out_dir": str(OUT / "dl")},
    ck_error_mentions("greyed"),
    needs=("P", "V5"),
)
step(
    "C04 clip_save_frame",
    "clip_save_frame",
    lambda c: {"project_id": c["P"], "media_id": c["V5"]},
    ck_save_frame,
    needs=("P", "V5"),
)
step(
    "C05 clip_extend V5",
    "clip_extend",
    lambda c: {
        "project_id": c["P"],
        "media_id": c["V5"],
        "prompt": "The kite keeps flying, the camera holds still",
        "job_id": f"v8-ext-{RUN}",
        "out_dir": str(OUT / "dl"),
    },
    ck_extend,
    price=10,
    needs=("P", "V5"),
)
step(
    "C06 scene_download extended",
    "scene_download",
    lambda c: {"project_id": c["P"], "scene_id": c["S_ext"], "out_dir": str(OUT / "dl")},
    ck_scene_download,
    needs=("P", "S_ext"),
)
step(
    "C07 clip_edit V1",
    "clip_edit",
    lambda c: {
        "project_id": c["P"],
        "media_id": c["V1"],
        "prompt": "Make the water a deep blue",
        "job_id": f"v8-edit-{RUN}",
        "out_dir": str(OUT / "dl"),
    },
    ck_edit,
    price=20,
    needs=("P", "V1"),
)
step(
    "C08 clip_reconcile",
    "clip_reconcile",
    lambda c: {"project_id": c["P"], "out_dir": str(OUT)},
    ck_reconcile,
    needs=("P",),
)
step(
    "C09 agent_send",
    "agent_send",
    lambda c: {
        "project_id": c["P"],
        "message": "Chào bạn. Chỉ trả lời bằng chữ, đừng tạo ảnh hay video: bạn có thể làm gì trong project này?",
        "job_id": f"v8-agent-{RUN}",
        # Seconds, not a flag: True read as 1 s on 2026-10-08 and no request was heard before the window closed.
        "wait": 60,
    },
    ck_agent_send,
    price=0,
    needs=("P",),
)
step(
    "C10 job_status settled",
    "job_status",
    lambda c: {"job_id": c.get("sub_job") or f"v8-sub-{RUN}"},
    ck_job_settled,
    needs=("P",),
)
# deletions of what this run made
step(
    "D01 character_delete",
    "character_delete",
    lambda c: {"project_id": c["P"], "entity_id": c["E"]},
    ck_no_error,
    needs=("P", "E"),
)
step(
    "D02 flow_characters empty",
    "flow_characters",
    lambda c: {"project_id": c["P"]},
    ck_characters_empty,
    needs=("P", "E"),
)
step("D03 project_delete", "project_delete", lambda c: {"project_id": c["P"]}, ck_no_error, needs=("P",))
step("D04 projects lacks P", "flow_projects", lambda c: {}, ck_projects_lacks_P, needs=("P",))
step("Z01 credits end", "flow_credits", lambda c: {}, ck_final_credits)


async def call(session: ClientSession, name: str, arguments: dict[str, Any]) -> tuple[Any, str | None, float]:
    started = time.monotonic()
    result = await session.call_tool(name, arguments)
    took = time.monotonic() - started
    text = "".join(getattr(chunk, "text", "") for chunk in result.content)
    if result.is_error:
        return None, text, took
    try:
        return json.loads(text), None, took
    except ValueError:
        return text, None, took


def record(row: dict[str, Any]) -> None:
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        f"{row['verdict']:6} {row['step']:34} {row.get('took_s', 0):6.1f}s  {row.get('note', '')[:150]}",
        flush=True,
    )


async def run(args: argparse.Namespace) -> int:
    mcp_server.backend = mcp_server.Backend(profile=args.profile, out_dir=OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    ctx: dict[str, Any] = json.loads(CTX.read_text()) if CTX.exists() and not args.fresh else {}
    if args.image:
        ctx["IMG"] = str(Path(args.image).resolve())
    ctx["run"] = RUN
    chosen = [s for s in STEPS if (not args.only or s["name"].split()[0] in args.only)]
    if args.start:
        names = [s["name"].split()[0] for s in chosen]
        chosen = chosen[names.index(args.start) :]
    spent = ctx.get("spent_total", 0)
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
                for s in chosen:
                    row: dict[str, Any] = {
                        "ts": time.time(),
                        "run": RUN,
                        "step": s["name"],
                        "tool": s["tool"],
                    }
                    gap = need(ctx, *s["needs"])
                    if gap:
                        row.update(verdict="SKIP", note=gap)
                        record(row)
                        continue
                    arguments = s["args"](ctx)
                    row["arguments"] = arguments
                    if s["price"] and spent + s["price"] > args.ceiling:
                        row.update(
                            verdict="STOP",
                            note=f"would pass the ceiling: {spent} spent + {s['price']} > {args.ceiling}",
                        )
                        record(row)
                        break
                    paid = bool(s["price"]) or s["tool"] in ("gen_t2i", "gen_i2i", "agent_send")
                    if paid:
                        before, err, _ = await call(session, "flow_credits", {})
                        row["balance_before"] = (before or {}).get("balance") if not err else None
                    if s["poll"]:
                        deadline = time.monotonic() + s["poll"]["limit"]
                        while True:
                            answer, error, took = await call(session, s["tool"], arguments)
                            state = (answer or {}).get("state") if isinstance(answer, dict) else None
                            if error or state == s["poll"]["until"] or time.monotonic() > deadline:
                                break
                            print(
                                f"       {s['name']}: state {state!r}, waiting {s['poll']['every']}s",
                                flush=True,
                            )
                            await asyncio.sleep(s["poll"]["every"])
                    else:
                        answer, error, took = await call(session, s["tool"], arguments)
                    row.update(took_s=round(took, 1), error=error, answer=answer)
                    if paid:
                        after, err2, _ = await call(session, "flow_credits", {})
                        row["balance_after"] = (after or {}).get("balance") if not err2 else None
                        if isinstance(row.get("balance_before"), int) and isinstance(
                            row.get("balance_after"), int
                        ):
                            delta = row["balance_before"] - row["balance_after"]
                            row["spent"] = delta
                            spent += max(delta, 0)
                            ctx["spent_total"] = spent
                    if error:
                        expects_error = s["check"] in (ck_refused_job_id,) or getattr(
                            s["check"], "__qualname__", ""
                        ).startswith("ck_error_mentions")
                        if expects_error:
                            word = "job" if s["check"] is ck_refused_job_id else "greyed"
                            ok = word in error.lower()
                            row.update(verdict="PASS" if ok else "FAIL", note=f"refused: {error[:300]}")
                        else:
                            row.update(verdict="FAIL", note=f"ERROR {error[:300]}")
                    else:
                        try:
                            problems = s["check"](answer, ctx) if s["check"] else []
                        except Exception as exc:  # noqa: BLE001
                            problems = [f"check raised {type(exc).__name__}: {exc}"]
                        row.update(
                            verdict="PASS" if not problems else "FAIL",
                            note="; ".join(problems) or json.dumps(answer, ensure_ascii=False)[:150],
                        )
                    if paid and "spent" in row:
                        row["note"] = f"spent {row['spent']} (expected {s['price']}); " + row["note"]
                        if row["spent"] != s["price"] and row["verdict"] == "PASS":
                            row["note"] = "PRICE DIFFERS; " + row["note"]
                    record(row)
                    CTX.write_text(json.dumps(ctx, ensure_ascii=False, indent=1), encoding="utf-8")
                    if "ProfileLockedError" in (error or ""):
                        print("profile locked: stopping; rerun with --from", flush=True)
                        break
        finally:
            serve.cancel()
    print(
        f"\nrun {RUN}: spent_total {ctx.get('spent_total', 0)} of ceiling {args.ceiling}; balance {ctx.get('balance0')} -> {ctx.get('balance_end')}",
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, help="the gflow profile (account) the run spends on")
    parser.add_argument("--ceiling", type=int, required=True, help="credits the whole run may spend, at most")
    parser.add_argument("--image", help="a png, jpg, jpeg or webp for the upload, i2i, frames and r2v steps")
    parser.add_argument("--from", dest="start")
    parser.add_argument("--only", type=lambda s: s.split(","))
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for s in STEPS:
            print(f"{s['name']:34} {s['tool']:22} price {s['price']:3} needs {list(s['needs'])}")
        return 0
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
