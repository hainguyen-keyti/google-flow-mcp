"""A character, and a character with an image, go into a Flow prompt through MCP, on a draft project. $0.

    uv run python scripts/acceptance/character_gen.py --project <draft project id>
    uv run python scripts/acceptance/character_gen.py character --project <id> --image <png> --name <name>
    uv run python scripts/acceptance/character_gen.py compare --project <id> --job <job_id> --entity <entity_id>

The default run never clicks Start generation (every gen_character call is a dry_run), but it changes the draft
project: it uploads two small generated images and creates one character from an existing character's portrait. Exit
code is 1 when any row is FAIL or missing.

C1 gen_character dry_run with one character: exactly one chip, the entity with that id, the omni-flash 8 s price 12,
   and a composer left with no mention and no text.
C2 dry_run with the character and an uploaded image: the entity chip, then a media chip naming the image's WORKFLOW id
   (a media chip carries the workflow id, not the media id, measured in T1), price 12.
C3 dry_run with veo-lite: price 10.
C4 dry_run with veo-fast: price 20, Flow's own price table, never measured by a spend.
C5 an entity id that is not a character of the project is refused, and the refusal names the id.
C6 an image whose title contains the character's name does not take the character's chip: Flow lists characters and
   media under one unranked search, and a file named like a character once won the pick (gflow #723).
C7 character_create with an image (the character's own portrait, downloaded for free) lists a new character with that
   name and a portrait.
C8 none of the rows above wrote a ledger row, and the balance did not drop.

`character` creates a character from a local image and checks the listing; `compare` puts a character's portrait next
to a frame of a generated clip, for a person to look at. Both are $0.

What this gate does NOT cover: whether Flow honours the chips when it generates. A dry_run stops before the click, so the
submit body, the price actually charged and the clip itself are measured only by the spends of T7.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from video import gen, mcp_server

ROWS = 8
OUT = Path("out")
PROMPT = "stands in a sunny bakery and smiles at the camera"
PORTRAIT_PROMPT = "A cheerful baker with curly grey hair and round glasses, studio portrait, plain background"
PRICES = {"omni-flash": 12, "veo-lite": 10, "veo-fast": 20}


async def call(session: ClientSession, name: str, arguments: dict[str, Any]) -> tuple[Any, str | None, float]:
    started = time.monotonic()
    result = await session.call_tool(name, arguments)
    took = time.monotonic() - started
    text = "".join(getattr(chunk, "text", "") for chunk in result.content)
    if result.is_error:
        return None, text[:400], took
    try:
        return json.loads(text), None, took
    except ValueError:
        return text, None, took


@asynccontextmanager
async def client() -> AsyncIterator[ClientSession]:
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
                yield session
        finally:
            serve.cancel()


def ledger_rows() -> int:
    return sum(
        len(gen.Ledger(path).rows())
        for path in OUT.rglob("ledger.jsonl", case_sensitive=False)
        if path.is_file()
    )


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def png(path: Path, color: str) -> Path:
    ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=512x512", "-frames:v", "1", str(path))
    return path


def side_by_side(portrait: Path, clip: Path, target: Path) -> Path:
    frame = target.with_name(f"{target.stem}_frame.png")
    ffmpeg("-ss", "4", "-i", str(clip), "-frames:v", "1", str(frame))
    ffmpeg(
        "-i",
        str(portrait),
        "-i",
        str(frame),
        "-filter_complex",
        "[0:v]scale=-2:720[a];[1:v]scale=-2:720[b];[a][b]hstack",
        "-frames:v",
        "1",
        str(target),
    )
    return target


def chips(answer: Any) -> list[tuple[Any, Any]]:
    return [(chip.get("kind"), chip.get("id")) for chip in (answer or {}).get("chips") or []]


async def balance(session: ClientSession) -> int | None:
    answer, _, _ = await call(session, "flow_credits", {})
    return (answer or {}).get("balance")


async def run(project: str, rows: list[dict[str, Any]]) -> None:
    stamp = int(time.time())
    # A room of its own, so a portrait an earlier run downloaded never meets the no-overwrite rule (CLAUDE.md rule 5).
    room = OUT / f"character_gen_{stamp}"
    room.mkdir(parents=True, exist_ok=True)

    def row(name: str, ok: bool, detail: str) -> None:
        status = "PASS" if ok else "FAIL"
        rows.append({"name": name, "status": status, "detail": detail})
        print(f"{status}  {name:52} {detail}", flush=True)

    async with client() as session:
        listed, error, _ = await call(session, "flow_characters", {"project_id": project})
        if error:
            row("C1 dry_run with one character", False, f"flow_characters failed: {error}")
            return
        names = [(each.get("name") or "").casefold() for each in listed]
        unique = [each for each in listed if each.get("name") and names.count(each["name"].casefold()) == 1]
        if not unique:
            arguments = {"project_id": project, "prompt": PORTRAIT_PROMPT, "name": f"Ca{stamp}"}
            _, error, _ = await call(session, "character_create", arguments)
            listed, list_error, _ = await call(session, "flow_characters", {"project_id": project})
            unique = [each for each in listed or [] if each.get("name") == f"Ca{stamp}"]
            if error or list_error or not unique:
                row("C1 dry_run with one character", False, f"no character to use: {error or list_error}")
                return
        character = unique[0]
        entity, name = character["entity_id"], character["name"]
        print(f"character {name!r} {entity}", flush=True)

        balance_before = await balance(session)
        ledger_before = ledger_rows()

        image = png(room / f"pco{stamp}.png", "0x2f6fb5")
        uploaded, error, _ = await call(session, "flow_upload", {"project_id": project, "path": str(image)})
        if error:
            row("C1 dry_run with one character", False, f"flow_upload failed: {error}")
            return
        base = {"project": project, "prompt": PROMPT, "characters": [entity], "dry_run": True}

        answer, error, took = await call(session, "gen_character", base)
        left = (answer or {}).get("composer_left") or {}
        row(
            "C1 dry_run with one character",
            error is None
            and (answer or {}).get("dry_run") is True
            and chips(answer) == [("entity", entity)]
            and (answer or {}).get("quoted_credits") == PRICES["omni-flash"]
            and left.get("mentions") == 0
            and not (left.get("text") or "").strip(),
            f"{took:.1f}s chips={chips(answer)} quoted={(answer or {}).get('quoted_credits')} left={left} "
            f"error={error}",
        )

        arguments = {**base, "media_ids": [uploaded["media_id"]]}
        answer, error, took = await call(session, "gen_character", arguments)
        row(
            "C2 dry_run with the character and an image",
            error is None
            and chips(answer) == [("entity", entity), ("media", uploaded["workflow_id"])]
            and (answer or {}).get("quoted_credits") == PRICES["omni-flash"],
            f"{took:.1f}s chips={chips(answer)} quoted={(answer or {}).get('quoted_credits')} error={error}",
        )

        for label, model in (
            ("C3 dry_run with veo-lite", "veo-lite"),
            ("C4 dry_run with veo-fast", "veo-fast"),
        ):
            answer, error, took = await call(session, "gen_character", {**base, "model": model})
            row(
                label,
                error is None
                and chips(answer) == [("entity", entity)]
                and (answer or {}).get("quoted_credits") == PRICES[model],
                f"{took:.1f}s quoted={(answer or {}).get('quoted_credits')} expected={PRICES[model]} error={error}",
            )

        stranger = str(uuid.uuid4())
        _, error, took = await call(session, "gen_character", {**base, "characters": [stranger]})
        row(
            "C5 an unknown entity id is refused",
            error is not None and stranger in error,
            f"{took:.1f}s error={error}",
        )

        namesake = png(room / f"{name}_ref_{stamp}.png", "0xb52f2f")
        _, upload_error, _ = await call(
            session, "flow_upload", {"project_id": project, "path": str(namesake)}
        )
        answer, error, took = await call(session, "gen_character", base)
        row(
            "C6 an image named after the character keeps out",
            upload_error is None and error is None and chips(answer) == [("entity", entity)],
            f"{took:.1f}s file={namesake.name} chips={chips(answer)} upload_error={upload_error} error={error}",
        )

        portrait_id = character.get("portrait_media_id")
        created_name = f"pE{stamp}"
        detail = "the character has no portrait media id to download"
        ok = False
        if portrait_id:
            arguments = {"project_id": project, "media_id": portrait_id, "out_dir": str(room)}
            portrait, download_error, _ = await call(session, "flow_download", arguments)
            created, error, took = await call(
                session,
                "character_create",
                {"project_id": project, "image": (portrait or {}).get("path", ""), "name": created_name},
            )
            again, list_error, _ = await call(session, "flow_characters", {"project_id": project})
            found = [
                each for each in again or [] if each.get("entity_id") == (created or {}).get("entity_id")
            ]
            ok = (
                error is None
                and len(found) == 1
                and found[0].get("name") == created_name
                and bool(found[0].get("portrait_media_id"))
            )
            detail = (
                f"{took:.1f}s entity={(created or {}).get('entity_id')} listed={found} "
                f"download_error={download_error} error={error} list_error={list_error}"
            )
        row("C7 character_create from an image", ok, detail)

        balance_after = await balance(session)
        ledger_after = ledger_rows()
        row(
            "C8 no ledger row and no balance drop",
            ledger_after == ledger_before
            and isinstance(balance_before, int)
            and isinstance(balance_after, int)
            and balance_after >= balance_before,
            f"ledger_rows {ledger_before}->{ledger_after} balance {balance_before}->{balance_after}",
        )


async def make_character(project: str, image: str, name: str) -> int:
    async with client() as session:
        before = await balance(session)
        arguments = {"project_id": project, "image": image, "name": name}
        created, error, took = await call(session, "character_create", arguments)
        listed, list_error, _ = await call(session, "flow_characters", {"project_id": project})
        found = [each for each in listed or [] if each.get("entity_id") == (created or {}).get("entity_id")]
        after = await balance(session)
    ok = (
        error is None
        and len(found) == 1
        and found[0].get("name") == name
        and bool(found[0].get("portrait_media_id"))
    )
    print(f"{'PASS' if ok else 'FAIL'}  character {name!r} from {image}: {took:.1f}s", flush=True)
    print(
        f"  created={created} listed={found} balance {before}->{after} error={error} list_error={list_error}"
    )
    return 0 if ok else 1


async def portrait_and_clip(project: str, job: str, entity: str, room: Path) -> tuple[Path, Path] | None:
    done = [
        row
        for path in sorted(OUT.rglob("ledger.jsonl", case_sensitive=False))
        if path.is_file()
        for row in gen.Ledger(path).rows(job)
        if row.get("status") == "done" and row.get("path")
    ]
    if not done:
        print(f"FAIL  no done row with a path for job {job}")
        return None
    async with client() as session:
        listed, error, _ = await call(session, "flow_characters", {"project_id": project})
        match = [each for each in listed or [] if each.get("entity_id") == entity]
        if error or not match or not match[0].get("portrait_media_id"):
            print(f"FAIL  no portrait for entity {entity}: error={error}")
            return None
        arguments = {"project_id": project, "media_id": match[0]["portrait_media_id"], "out_dir": str(room)}
        portrait, error, _ = await call(session, "flow_download", arguments)
    if error or not portrait:
        print(f"FAIL  portrait download: {error}")
        return None
    return Path(portrait["path"]), Path(done[-1]["path"])


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["character"]:
        parser = argparse.ArgumentParser(
            description="Create a character from a local image, check the listing."
        )
        parser.add_argument("--project", required=True)
        parser.add_argument("--image", required=True)
        parser.add_argument("--name", required=True)
        args = parser.parse_args(argv[1:])
        return asyncio.run(make_character(args.project, args.image, args.name))
    if argv[:1] == ["compare"]:
        parser = argparse.ArgumentParser(description="Put a character's portrait next to a frame of a clip.")
        parser.add_argument("--project", required=True)
        parser.add_argument("--job", required=True)
        parser.add_argument("--entity", required=True)
        args = parser.parse_args(argv[1:])
        room = OUT / f"character_gen_compare_{int(time.time())}"
        room.mkdir(parents=True, exist_ok=True)
        found = asyncio.run(portrait_and_clip(args.project, args.job, args.entity, room))
        if found is None:
            return 1
        target = side_by_side(found[0], found[1], room / f"compare_{args.job}.png")
        print(f"PASS  compare={target} clip={found[1]} portrait={found[0]}")
        return 0
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--project",
        required=True,
        help="A draft project: the run uploads two images and creates a character.",
    )
    args = parser.parse_args(argv)
    rows: list[dict[str, Any]] = []
    asyncio.run(run(args.project, rows))
    failed = sum(1 for each in rows if each["status"] == "FAIL") + ROWS - len(rows)
    print(f"\nrows={ROWS} pass={ROWS - failed} fail={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
