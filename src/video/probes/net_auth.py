"""Which auth scheme the migrated page uses per host. $0: one navigation, passive network listener.
Records the Authorization scheme word only, never the credential.

    uv run python -m video.probes.net_auth --profile default --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter

from video.probes._common import build_client, out_path, resolve_profile_dir, step

HOSTS = ("labs.google", "aisandbox-pa.googleapis.com", "flow.google.com")
SKIP_EXT = (".js", ".css", ".png", ".jpg", ".woff2", ".svg", ".ico", ".webp", ".mp4")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--project", required=True)
    ap.add_argument("--settle", type=float, default=15.0)
    args = ap.parse_args()

    responses = []
    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = await client._context.new_page()
        page.on("response", lambda r: responses.append(r))
        step("nav", f"project {args.project}")
        await page.goto(
            f"https://flow.google.com/project/{args.project}", wait_until="domcontentloaded", timeout=60_000
        )
        await page.wait_for_timeout(int(args.settle * 1000))
        rows = []
        for resp in responses:
            req = resp.request
            url = req.url
            if not any(h in url for h in HOSTS) or url.split("?")[0].endswith(SKIP_EXT):
                continue
            try:
                hdrs = await req.all_headers()
            except Exception:  # noqa: BLE001
                hdrs = req.headers
            auth = (hdrs.get("authorization") or "").split(" ", 1)[0]
            rpcids = url.split("rpcids=")[1].split("&")[0] if "rpcids=" in url else ""
            rows.append(
                {
                    "status": resp.status,
                    "method": req.method,
                    "auth": auth or "-",
                    "cookie": "cookie" in hdrs,
                    "rpcids": rpcids,
                    "url": url.split("?")[0][:110],
                }
            )

    out = out_path("net_auth")
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    counts = Counter((r["url"].split("/")[2], r["auth"], r["status"]) for r in rows)
    for (host, auth, status), n in sorted(counts.items()):
        print(f"{n:3d}  {host:32s} auth={auth:12s} status={status}")
    print("rpcids: " + " ".join(sorted({r["rpcids"] for r in rows if r["rpcids"]})))
    print(f"({out})")


if __name__ == "__main__":
    asyncio.run(main())
