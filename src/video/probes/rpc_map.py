"""Map batchexecute rpcids to Flow views. $0: navigation and sidebar clicks only, nothing created.

uv run python -m video.probes.rpc_map --profile default --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from video.probes._common import build_client, out_path, resolve_profile_dir, step

SIDEBAR_ITEMS = ("Images", "Videos", "Characters", "Scenes", "Uploads", "Tools")


def decode(body: str) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for line in body.split("\n"):
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            arr = json.loads(line)
        except ValueError:
            continue
        for e in arr:
            if isinstance(e, list) and e and e[0] == "wrb.fr":
                payload = e[2]
                try:
                    payload = json.loads(payload) if isinstance(payload, str) else payload
                except ValueError:
                    pass
                out.append((e[1], payload))
    return out


def shape(x: Any, depth: int = 0) -> str:
    if isinstance(x, list):
        inner = ", ".join(shape(i, depth + 1) for i in x[:4]) if depth < 2 else "..."
        return f"list[{len(x)}]({inner})"
    if isinstance(x, dict):
        return "dict{" + ",".join(list(x)[:5]) + "}"
    if isinstance(x, str):
        return f"str({len(x)})" if len(x) > 40 else repr(x)
    return repr(x)[:30]


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    records: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    async def on_response(resp) -> None:
        if "batchexecute" not in resp.request.url:
            return
        try:
            body = await resp.text()
        except Exception:  # noqa: BLE001
            body = ""
        pending.append({"status": resp.status, "post": (resp.request.post_data or "")[:400], "body": body})

    def flush(view: str) -> None:
        for r in pending:
            for rpcid, payload in decode(r["body"]):
                records.append(
                    {
                        "view": view,
                        "rpcid": rpcid,
                        "status": r["status"],
                        "post": r["post"],
                        "shape": shape(payload),
                        "raw": json.dumps(payload)[:1500],
                    }
                )
        pending.clear()

    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = await client._context.new_page()
        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        step("view", "grid")
        await page.goto("https://flow.google.com/", wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(10_000)
        flush("grid")
        step("view", "project")
        await page.goto(
            f"https://flow.google.com/project/{args.project}", wait_until="domcontentloaded", timeout=60_000
        )
        await page.wait_for_timeout(12_000)
        flush("project")
        for item in SIDEBAR_ITEMS:
            step("click", item)
            try:
                await page.locator("mat-list-item, a.mat-mdc-list-item", has_text=item).first.click(
                    timeout=8_000
                )
                await page.wait_for_timeout(7_000)
            except Exception as exc:  # noqa: BLE001
                records.append(
                    {"view": item, "rpcid": "(click failed)", "status": 0, "shape": str(exc)[:120]}
                )
            flush(item)

    out = out_path("rpc_map")
    out.write_text(json.dumps(records, indent=1, ensure_ascii=False), encoding="utf-8")
    seen: dict[tuple[str, str], int] = {}
    shapes: dict[tuple[str, str], str] = {}
    for r in records:
        key = (r["view"], r["rpcid"])
        seen[key] = seen.get(key, 0) + 1
        shapes.setdefault(key, r["shape"])
    for (view, rpcid), n in seen.items():
        print(f"{view:10s} {rpcid:14s} x{n}  {shapes[(view, rpcid)][:110]}")
    print(f"({out})")


if __name__ == "__main__":
    asyncio.run(main())
