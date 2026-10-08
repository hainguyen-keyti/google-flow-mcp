"""Write src/video/flow/flow_wire.json, the shape signature of every reply this server parses, from the test
fixtures, which are replies Flow sent (tests/fixtures/rpc/*.json from the free reads, with the view each came from;
tests/fixtures/replies/*.txt from a paid run, redacted by redact_replies.py). `video flow check` compares the live
free reads with it, and tests/test_wire.py fails when this file and the fixtures disagree.

    uv run python scripts/acceptance/wire_baseline.py            # write the file
    uv run python scripts/acceptance/wire_baseline.py --check    # fail if the file on disk is stale

To refresh after Flow changed: capture new replies (VIDEO_CAPTURE_REPLIES=<folder> on a paid run; the free reads
do not capture yet, their fixtures under tests/fixtures/rpc/ are refreshed by hand), turn them into fixtures with
redact_replies.py, fix the parsers and their tests, then run this again.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from gflow_cli.api.transports.batchexecute import parse_frames

from video.flow import wire

RPC_FIXTURES = ROOT / "tests" / "fixtures" / "rpc"
REPLY_FIXTURES = ROOT / "tests" / "fixtures" / "replies"


def payloads() -> dict[str, tuple[str, list]]:
    """Every fixture's payloads by rpcid, with the view it was read on (a reply of a paid run is "paid")."""
    found: dict[str, tuple[str, list]] = {}
    for path in sorted(RPC_FIXTURES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        rpcid = path.stem.split("_")[0]
        view, items = found.get(rpcid, (data["view"], []))
        found[rpcid] = (view, [*items, data["payload"]])
    for path in sorted(REPLY_FIXTURES.glob("*.txt")):
        for rpcid, payload in parse_frames(path.read_text(encoding="utf-8")):
            view, items = found.get(rpcid, ("paid", []))
            found[rpcid] = (view, [*items, payload])
    return found


def build() -> dict:
    rpcs = {}
    for rpcid, (view, items) in sorted(payloads().items()):
        shape = wire.skeleton(items[0])
        for payload in items[1:]:
            shape = wire.merge(shape, wire.skeleton(payload))
        rpcs[rpcid] = {"view": view, "shape": shape}
    return {"measured": datetime.now().astimezone().strftime("%Y-%m-%d"), "rpcs": rpcs}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="fail when the file on disk is stale")
    args = parser.parse_args(argv)
    built = build()
    if args.check:
        on_disk = wire.baseline()
        if on_disk.get("rpcs") != built["rpcs"]:
            print("flow_wire.json is stale: run scripts/acceptance/wire_baseline.py")
            return 1
        print(f"flow_wire.json is current ({len(built['rpcs'])} rpcs)")
        return 0
    wire.BASELINE.write_text(json.dumps(built, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {wire.BASELINE} from {len(built['rpcs'])} rpcs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
