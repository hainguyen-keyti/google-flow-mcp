"""Turn raw Flow replies captured with VIDEO_CAPTURE_REPLIES into test fixtures that carry nothing of the account.

The composer writes one file per reply it heard into the folder VIDEO_CAPTURE_REPLIES names, as
<rpcids>_<time_ns>.txt (video.flow.composer._capture). This takes the capture each SPEC names, replaces every uuid
with a stand-in that is the same for the same uuid across one run (so a record's workflow, project and media stay
distinct and matchable, and a status reply still names the submit's workflow), blanks the batchexecute request id,
runs the composer's own redaction over long tokens and signed urls, recomputes the chunk-length lines, and writes
tests/fixtures/replies/<name>.txt. A reader test parses those files the way the composer parses a live reply, so a
shape Flow changes is caught by a fixture refreshed from a capture, never hidden by a hand-typed one (a typed "CAE"
in every fixture hid a week of unread replies, 2026-10-05 to 2026-10-08).

    uv run python scripts/acceptance/redact_replies.py --from out/aq/replies eb1hJf jwpduf
    uv run python scripts/acceptance/redact_replies.py --from out/aq/replies jwpduf_pending=jwpduf_1791453543098283000.txt

A SPEC is an rpcid (its newest capture becomes <rpcid>.txt) or name=<capture file> (that file becomes <name>.txt).
Prompts are kept: they are this repo's own test prompts, and the reader tests match a clip by them.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from video.flow import composer

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
REQUEST_ID_RE = re.compile(r'("af\.httprm",\d+,")-?\d+(")')
STAND_IN = "00000000-0000-4000-8000-{:012d}"


def stand_ins(text: str, seen: dict[str, str]) -> str:
    return UUID_RE.sub(lambda m: seen.setdefault(m.group().lower(), STAND_IN.format(len(seen) + 1)), text)


def relength(text: str) -> str:
    """The chunk-length lines of a batchexecute body read the chunk after them plus 2 (measured on all 75 captures
    of 2026-10-08), and the closing frame carries the body's whole length; both are rewritten for the redacted text
    so the fixture is consistent."""
    for _ in range(3):
        lines = text.split("\n")
        for i, line in enumerate(lines[:-1]):
            if line.isdigit():
                lines[i] = str(len(lines[i + 1]) + 2)
        text = "\n".join(lines)
        total = len(text)
        text = re.sub(r'(\[\["e",\d+,null,null,)\d+(\]\])', rf"\g<1>{total}\2", text)
    return text


def redact(text: str, seen: dict[str, str]) -> str:
    text = stand_ins(text, seen)
    text = REQUEST_ID_RE.sub(r"\g<1>0\2", text)
    text = composer._redacted(text)
    return relength(text)


def pick(folder: Path, spec: str) -> tuple[str, Path]:
    if "=" in spec:
        name, file = spec.split("=", 1)
        return name, folder / file
    files = sorted(folder.glob(f"{spec}_*.txt"))
    if not files:
        raise SystemExit(f"no capture of {spec} under {folder}")
    return spec, files[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("specs", nargs="+", metavar="SPEC", help="an rpcid, or name=<capture file>")
    parser.add_argument("--from", dest="folder", default="out/aq/replies", help="the capture folder")
    parser.add_argument("--to", default="tests/fixtures/replies", help="where the fixtures go")
    args = parser.parse_args(argv)
    seen: dict[str, str] = {}
    out = Path(args.to)
    out.mkdir(parents=True, exist_ok=True)
    for spec in args.specs:
        name, source = pick(Path(args.folder), spec)
        redacted = redact(source.read_text(encoding="utf-8"), seen)
        if UUID_RE.search(redacted.replace("00000000-0000-4000-8000-", "")):
            raise SystemExit(f"a uuid survived the redaction of {source}")
        (out / f"{name}.txt").write_text(redacted, encoding="utf-8")
        print(f"{name}.txt <- {source.name} ({len(redacted)} chars, {len(seen)} uuids stood in for)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
