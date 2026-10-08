"""Render docs/tools.md from the tools the MCP server actually serves.

    uv run python scripts/gen_tool_docs.py            # write the file
    uv run python scripts/gen_tool_docs.py --check    # fail if the file on disk is stale

A hand-written tool list in this repo has gone stale twice and cost real money both times, so the published
reference is generated and a test (tests/test_tool_docs.py) fails when it drifts.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from video import mcp_server

TARGET = ROOT / "docs" / "tools.md"
# A description that mentions none of these is one that never tells the caller what a call costs.
PRICE_WORDS = re.compile(r"\bfree\b|\bcredits?\b|\bquota\b|unmeasured", re.IGNORECASE)
# Groups in the order a newcomer needs them: read first, change second, spend last.
GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "Read, free",
        "Nothing here changes anything on the account.",
        (
            "flow_lane",
            "flow_projects",
            "flow_credits",
            "flow_capabilities",
            "flow_check",
            "flow_media",
            "flow_characters",
            "flow_voices",
            "flow_tools",
            "flow_uploads",
            "scene_list",
            "scene_clips",
            "clip_reconcile",
            "clip_recipe",
            "job_status",
        ),
    ),
    (
        "Write a file on this machine, free",
        "Free for Flow, but they put bytes on your disk, always inside `out/`.",
        ("flow_download", "clip_download", "scene_download", "job_collect"),
    ),
    (
        "Change the account, free",
        "These edit real projects, characters and scenes. Try them on a scratch project first.",
        (
            "project_create",
            "project_rename",
            "project_delete",
            "character_create",
            "character_delete",
            "character_set_voice",
            "character_make_voice",
            "character_clear_voice",
            "scene_create",
            "scene_rename",
            "scene_delete",
            "scene_restore",
            "scene_add_clip",
            "scene_move_clip",
            "scene_remove_clip",
            "scene_set_aspect",
            "scene_save_clip",
            "clip_save_frame",
            "flow_upload",
            "agent_mode",
        ),
    ),
    (
        "Spend credits",
        (
            "Each of these needs a `job_id`, writes a ledger row before the click, and refuses an id that "
            "any ledger under `out/` has already seen."
        ),
        (
            "gen_video",
            "job_submit",
            "gen_t2v",
            "gen_i2v",
            "gen_r2v",
            "gen_character",
            "gen_t2i",
            "gen_i2i",
            "clip_extend",
            "clip_edit",
            "agent_send",
        ),
    ),
]


async def tools() -> list[Any]:
    return await mcp_server.server.list_tools()


async def priceless() -> list[str]:
    return sorted(tool.name for tool in await tools() if not PRICE_WORDS.search(tool.description or ""))


def _schema(tool: Any) -> dict[str, Any]:
    """The schema the server really serves, read by whichever name it exposes.

    Measured 2026-09-28: this MCP version names it `input_schema`, and reading `inputSchema` instead published all
    43 tools as taking no arguments, silently, because `getattr` returned None. So a name that is not there is an
    error now, never an empty column.
    """
    for name in ("input_schema", "inputSchema"):
        schema = getattr(tool, name, None)
        if isinstance(schema, dict):
            return schema
    raise AttributeError(f"{getattr(tool, 'name', tool)}: served tool exposes no input_schema or inputSchema")


def _arguments(tool: Any) -> str:
    schema = _schema(tool)
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or ())
    if not properties:
        return "none"
    return ", ".join(f"`{name}`" + ("" if name in required else " (optional)") for name in properties)


async def render() -> str:
    served = {tool.name: tool for tool in await tools()}
    listed = {name for _, _, names in GROUPS for name in names}
    missing = sorted(set(served) - listed)
    lines = [
        "# MCP tools",
        "",
        (
            "Generated from the running server by `scripts/gen_tool_docs.py`; do not edit by hand. Prices "
            "are the ones measured on the account this was built for, and they are part of each tool's own "
            "description, which is what an agent reads before spending."
        ),
        "",
        f"**{len(served)} tools.**",
        "",
    ]
    if missing:
        lines += [
            "> These tools are served but not grouped yet, so they are listed last: "
            + ", ".join(f"`{name}`" for name in missing),
            "",
        ]
    for title, blurb, names in [*GROUPS, ("Ungrouped", "", tuple(missing))]:
        present = [name for name in names if name in served]
        if not present:
            continue
        lines += [f"## {title}", ""]
        if blurb:
            lines += [blurb, ""]
        for name in present:
            tool = served[name]
            lines += [
                f"### `{name}`",
                "",
                f"**Arguments**: {_arguments(tool)}",
                "",
                (tool.description or "").strip(),
                "",
            ]
    return "\n".join(lines).rstrip() + "\n"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    wanted = await render()
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != wanted:
            print("FAIL docs/tools.md is stale; run scripts/gen_tool_docs.py to rebuild it")
            return 1
        print(f"PASS docs/tools.md matches the {len(await tools())} tools served")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(wanted, encoding="utf-8")
    print(f"wrote {TARGET} from {len(await tools())} served tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
