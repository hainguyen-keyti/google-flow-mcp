"""Call ONE MCP tool exactly once, bracketed by a credit balance read on each side.

    uv run python scripts/acceptance/mcp_spend_once.py --tool gen_t2v \
        --set project=<id> --set 'prompt=a wooden sailboat' --set job_id=t6-gen-t2v-1

Six tools spend real money (gen_t2v, gen_i2v, gen_r2v, clip_extend, clip_edit, agent_send), and plan
`scene-timeline-tools` T6 runs each of them once through MCP so the wrapper is proven against Flow, not only against
fakes. This script is the instrument for that, and it is deliberately dumb: it does not choose arguments, does not
retry, and does not loop. One call, one bill.

It prints the balance before and after, every ledger row the job wrote, and the tool's own answer. Exit code is 1
when the tool reported an error, or when a job_id was given and the ledger holds no row for it: a spend with no row
is the failure this repo has already paid for once.

Run it on a draft project. Nothing here asks for confirmation, so the owner's approval happens before it is started.
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

# A second session on the same profile (a running MCP server) waits for the lease instead of failing at once.
os.environ.setdefault("GFLOW_CLI_LEASE_WAIT_SECONDS", "900")

from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from video import gen, mcp_server

OUT = Path("out")


def _value(text: str) -> Any:
    """`--set count=2` is an int, `--set refs=["a.jpg"]` is a list, everything else stays a string."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _arguments(pairs: list[str]) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set wants key=value, got {pair!r}")
        key, _, text = pair.partition("=")
        arguments[key] = _value(text)
    return arguments


def _ledger_rows(job_id: str | None) -> list[dict[str, Any]]:
    if not job_id:
        return []
    rows = []
    for path in sorted(OUT.rglob("ledger.jsonl", case_sensitive=False)):
        if path.is_file():
            rows += [{**row, "ledger": str(path)} for row in gen.Ledger(path).rows(job_id)]
    return rows


async def call(session: ClientSession, name: str, arguments: dict[str, Any]) -> tuple[Any, str | None, float]:
    started = time.monotonic()
    result = await session.call_tool(name, arguments)
    took = time.monotonic() - started
    text = "".join(getattr(chunk, "text", "") for chunk in result.content)
    if result.is_error:
        return None, text[:600], took
    try:
        return json.loads(text), None, took
    except ValueError:
        return text, None, took


async def run(tool: str, arguments: dict[str, Any], report: dict[str, Any]) -> None:
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
                before, error, took = await call(session, "flow_credits", {})
                report["balance_before"] = (before or {}).get("balance")
                print(f"balance before: {report['balance_before']} ({took:.1f}s, error={error})", flush=True)

                print(f"calling {tool} once with {arguments}", flush=True)
                answer, error, took = await call(session, tool, arguments)
                report["answer"] = answer
                report["error"] = error
                report["took_s"] = round(took, 1)
                print(f"{tool}: {'ERROR ' + error if error else json.dumps(answer)[:600]}", flush=True)
                print(f"took {took:.1f}s", flush=True)

                after, credits_error, took = await call(session, "flow_credits", {})
                report["balance_after"] = (after or {}).get("balance")
                print(
                    f"balance after: {report['balance_after']} ({took:.1f}s, error={credits_error})",
                    flush=True,
                )
        finally:
            serve.cancel()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tool", required=True, help="The MCP tool to call exactly once.")
    parser.add_argument(
        "--set", action="append", default=[], metavar="KEY=VALUE", help="One argument for the tool."
    )
    args = parser.parse_args(argv)
    arguments = _arguments(args.set)
    job_id = arguments.get("job_id")
    report: dict[str, Any] = {"tool": args.tool, "arguments": arguments, "ts": time.time()}

    asyncio.run(run(args.tool, arguments, report))

    rows = _ledger_rows(job_id)
    report["ledger_rows"] = rows
    before, after = report.get("balance_before"), report.get("balance_after")
    spent = (before - after) if isinstance(before, int) and isinstance(after, int) else None
    report["spent"] = spent
    for row in rows:
        print(f"  ledger {row['ledger']}: {row.get('status')} {json.dumps(row)[:220]}", flush=True)
    path = OUT / f"spend_once_{args.tool}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    missing_row = bool(job_id) and not rows
    print(f"\ntool={args.tool} spent={spent} ledger_rows={len(rows)} error={report['error']}", flush=True)
    print(f"report={path}", flush=True)
    if missing_row:
        print("NO LEDGER ROW for this job_id: money may have moved with nothing recording it.", flush=True)
    return 1 if (report["error"] or missing_row) else 0


if __name__ == "__main__":
    sys.exit(main())
