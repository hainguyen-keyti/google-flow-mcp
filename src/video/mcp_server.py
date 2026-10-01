"""MCP server over stdio: every CLI capability as a tool. One browser session per call; FlowSession's
guard serializes concurrent calls (I4). Generate tools spend credits and are ledgered like the CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from gflow_cli import cli_video
from gflow_cli.api.transports.migrated_composer import R2V_DURATION_S
from gflow_cli.api.video import VideoModel, reference_cap_for, validate_duration_for_model
from gflow_cli.data.redaction import redact_error_detail
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video import gen as gen_mod
from video.flow import agent as agent_mod
from video.flow import characters as characters_mod
from video.flow import clips as clips_mod
from video.flow import download as download_mod
from video.flow import ingredients as ingredients_mod
from video.flow import lane as lane_mod
from video.flow import projects as projects_mod
from video.flow import reader
from video.flow import scenes as scenes_mod
from video.flow import uploads as uploads_mod
from video.flow import video as video_mod
from video.session import FlowSession

# Left empty, gflow lets Flow reuse the composer's last model (cli_video.py:185-196), so the price was unknowable.
VIDEO_DEFAULT_MODEL = "omni-flash"
OMNI_FLASH_SECONDS = 10
# The one cell an end frame has been paid for: 15 credits, 119 s, measured 2026-09-18 (job plan-i-endframe-1).
END_FRAME_MODEL = "omni-flash"
END_FRAME_SECONDS = 10


MEDIA_KINDS = ("image", "video")
BRIEF_PROMPT_CHARS = 120
# Flow's listing trailed its own answer by up to about 60 s (scenes.LISTING_LAG_MS).
IMAGE_LISTING_READS = 7
IMAGE_LISTING_POLL_S = 10


def _media_kind(kind: str | None) -> str | None:
    if kind is None or kind == "":
        return None
    wanted = kind.strip().lower()
    if wanted not in MEDIA_KINDS:
        raise ValueError(f"kind must be one of {', '.join(MEDIA_KINDS)}, got {kind!r}")
    return wanted


def _positive_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    if limit <= 0:
        raise ValueError(f"limit must be a positive number of rows, got {limit}")
    return int(limit)


def _since_epoch(since: str | float | None) -> float | None:
    """A row's created time is epoch seconds while an agent thinks in dates, so take either; a date with no zone
    is this machine's own day, which is what an agent asking for today means.

    A date is tried BEFORE a number: '20260911' is a valid ISO date and would otherwise read as epoch 20,260,911,
    which is August 1970 and filters nothing, silently (review 2026-09-18). Epoch seconds are ten digits, which
    fromisoformat refuses, so nothing else changes hands."""
    if since is None or since == "":
        return None
    if isinstance(since, (int, float)):
        return float(since)
    text = str(since).strip()
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(
            f"since must be epoch seconds or an ISO date like 2026-09-17 or 2026-09-17T08:30, got {since!r}"
        ) from exc


def _filter_rows(
    rows: list[dict[str, Any]], kind: str | None, after: float | None, limit: int | None
) -> list[dict[str, Any]]:
    kept = [
        row
        for row in rows
        if (kind is None or row.get("kind") == kind) and (after is None or (row.get("created") or 0) >= after)
    ]
    if limit is not None:
        # Sorted whenever a limit is asked for, not only when it bites: a limit that happens not to cut used to
        # answer in Flow's order while the description promised newest first (review 2026-09-18).
        kept = sorted(kept, key=lambda row: row.get("created") or 0, reverse=True)[:limit]
    return kept


def _brief_row(row: dict[str, Any]) -> dict[str, Any]:
    """Measured 2026-09-17: url and prompt carry 52,812 of the 122,919 characters a full listing weighs."""
    out = {key: value for key, value in row.items() if key != "url"}
    prompt = out.get("prompt")
    if isinstance(prompt, str) and len(prompt) > BRIEF_PROMPT_CHARS:
        out["prompt"] = prompt[:BRIEF_PROMPT_CHARS] + "…"
    return out


def _check_media_filters(kind: str | None, since: str | float | None, limit: int | None) -> None:
    _media_kind(kind)
    _since_epoch(since)
    _positive_limit(limit)


def media_filters(
    listing: dict[str, Any],
    kind: str | None = None,
    since: str | float | None = None,
    limit: int | None = None,
    brief: bool = False,
) -> dict[str, Any]:
    """The filters a project listing is cut down by, apart from the browser so the same rules serve the smoke's
    offline account. A filtered answer always says what it left out, so a short list never reads as a whole project."""
    after = _since_epoch(since)
    wanted = _media_kind(kind)
    rows = _positive_limit(limit)
    if wanted is None and after is None and rows is None and not brief:
        return listing
    out = dict(listing)
    cut = False
    for key in ("media", "versions"):
        if key not in listing:
            continue
        kept = _filter_rows(listing[key], wanted, after, rows)
        cut = cut or len(kept) < len(listing[key])
        out[key] = [_brief_row(row) for row in kept] if brief else kept
        out[f"{key}_total"] = len(listing[key])
    out["truncated"] = cut
    return out


def _video_settings(
    kind: str,
    model: str | None,
    duration: int | None,
    aspect: str | None = None,
    count: int = 1,
    refs: int = 0,
) -> tuple[str, int | None]:
    """Refuse up front what gflow's CLI refuses only after run_job has written `submitted`, burning the job_id."""
    model = model or VIDEO_DEFAULT_MODEL
    params = {p.name: p.type for p in cli_video.video.commands[kind].params}
    if model not in params["model"].choices:
        raise ValueError(f"model must be one of {list(params['model'].choices)}, got {model!r}")
    if aspect is not None and aspect not in params["aspect"].choices:
        raise ValueError(f"aspect must be one of {list(params['aspect'].choices)}, got {aspect!r}")
    # build_argv sends no --count below 2, so 0 would quietly pay for one clip.
    if not params["count"].min <= count <= params["count"].max:
        raise ValueError(f"count must be {params['count'].min}-{params['count'].max}, got {count}")
    if kind == "r2v":
        cap = reference_cap_for(VideoModel.from_cli(model))
        if refs > cap:
            raise ValueError(f"{model} takes at most {cap} reference images for gen_r2v, got {refs}")
        if duration not in (None, R2V_DURATION_S):
            raise ValueError(f"gen_r2v runs only at {R2V_DURATION_S} s on this host; omit duration")
        # Never sent: gflow pins r2v to that length itself, while an explicit one raises exit 11 on a cohort with no
        # duration row (migrated_composer.py:968-986, 1162-1180).
        return model, None
    if duration is None:
        is_omni = VideoModel.from_cli(model) is VideoModel.OMNI_FLASH
        return model, OMNI_FLASH_SECONDS if is_omni else None
    validate_duration_for_model(VideoModel.from_cli(model), duration)
    return model, duration


def _job_refused(job_id: str | None, seen: str) -> str:
    # The guidance leads because the agent sees at most 500 characters and a ledger path can be long.
    return (
        "that job may already have spent credits: check flow_media and flow_credits, and start it again under a "
        f"new job_id only if it did not run (job_id {job_id} already has {seen})"
    )


class Backend:
    def __init__(self, profile: str = "default", out_dir: Path = Path("out")) -> None:
        self.profile = profile
        self.out_dir = out_dir
        self._running: set[str] = set()

    async def _with(self, fn: Callable[[FlowSession], Awaitable[Any]]) -> Any:
        async with FlowSession(self.profile) as session:
            result = await fn(session)
            # The owner lets the driver press Flow's cookie notice (2026-10-01); a call that did so says it did.
            if session.notices and isinstance(result, dict):
                result["dismissed_notices"] = list(session.notices)
            return result

    def _editor_out_dir(self, out_dir: str | None) -> Path:
        """Editor jobs ledger where out_dir points, and job ids are looked up only under the out folder."""
        target = Path(out_dir) if out_dir else self.out_dir
        if not target.resolve().is_relative_to(self.out_dir.resolve()):
            raise ValueError(
                f"out_dir must be inside {self.out_dir.resolve()} (outputs stay in out/), got {target}"
            )
        # Any part named ledger.jsonl, in any case since the disk may not tell the names apart, makes the driver create a
        # folder every spend then fails to read. The path as given counts too: `out/ledger.jsonl/..` resolves that name
        # away, while the driver still creates it. A part that is already a file would fail only once a browser is open.
        current = self.out_dir.resolve()
        walked = target.resolve().relative_to(current).parts
        for part in (*Path(out_dir).parts, *walked) if out_dir else walked:
            if part.casefold() == "ledger.jsonl":
                raise ValueError(f"out_dir must be a folder, not a ledger or another file, got {target}")
        for part in walked:
            current = current / part
            if current.exists() and not current.is_dir():
                raise ValueError(f"out_dir must be a folder, not a ledger or another file, got {target}")
        return target

    async def _spend_once(self, job_id: str | None, out_dir: Path, run: Callable[[], Awaitable[Any]]) -> Any:
        """One job per job_id through MCP (DECISIONS 2026-09-15), decided before a browser opens: refused while a call
        with that id still runs in this server, and when any ledger under the out folder, or the one out_dir names,
        holds a row for it, `opening` included."""
        if job_id:
            # The ledger would store such an id as another one, so no check below could ever find it (DECISIONS 2026-09-16).
            gen_mod.check_job_id(job_id)
            if job_id in self._running:
                # Not _job_refused: flow_media and flow_credits cannot show a job still in flight, so "start it under a
                # new job_id if it did not run" would pay twice.
                raise gen_mod.AlreadySubmitted(
                    "that job is still running in another call: wait for it to finish, then call again with the SAME "
                    "job_id; never start it under a new job_id, or it pays twice; if it never finishes, stop and tell "
                    f"the owner (job_id {job_id})"
                )
            found = sorted(
                path for path in self.out_dir.rglob("ledger.jsonl", case_sensitive=False) if path.is_file()
            )
            ledgers = dict.fromkeys([*found, out_dir / "ledger.jsonl"])
            for ledger in ledgers:
                statuses = sorted({str(row.get("status")) for row in gen_mod.Ledger(ledger).rows(job_id)})
                if statuses:
                    seen = f"ledger rows ({', '.join(statuses)}) in {ledger}"
                    raise gen_mod.AlreadySubmitted(_job_refused(job_id, seen))
            # Marked with no await since the check, so a second call with this id cannot slip in between.
            self._running.add(job_id)
        try:
            return await run()
        except gen_mod.AlreadySubmitted as exc:
            # The drivers' own message ends "use a new job id", which invites paying twice.
            raise gen_mod.AlreadySubmitted(_job_refused(job_id, "a submitted row")) from exc
        finally:
            self._running.discard(job_id)

    async def lane(self) -> dict[str, Any]:
        return await self._with(lane_mod.run)

    async def projects(self) -> list[dict[str, Any]]:
        return await self._with(reader.projects)

    async def credits(self) -> dict[str, Any]:
        return await self._with(reader.credits)

    async def media(
        self,
        project_id: str,
        all_versions: bool = False,
        kind: str | None = None,
        since: str | float | None = None,
        limit: int | None = None,
        brief: bool = False,
    ) -> dict[str, Any]:
        # The grid collapses a media to one row, so an Omni edit that stacks a new version onto the same
        # media id is invisible there; `versions` is the only view that shows every version.
        # A filter that cannot be honoured is refused before a browser opens, never quietly ignored.
        _check_media_filters(kind, since, limit)
        listing = await self._with(lambda s: reader.project(s, project_id, versions=all_versions))
        return media_filters(listing, kind, since, limit, brief)

    async def characters(self, project_id: str) -> list[dict[str, Any]]:
        return await self._with(lambda s: characters_mod.list_characters(s, project_id))

    async def voices(self, project_id: str, entity_id: str) -> list[dict[str, str]]:
        return await self._with(lambda s: characters_mod.list_voices(s, project_id, entity_id))

    async def character_set_voice(self, project_id: str, entity_id: str, voice: str) -> dict[str, Any]:
        return await self._with(lambda s: characters_mod.set_voice(s, project_id, entity_id, voice))

    async def character_make_voice(
        self,
        project_id: str,
        entity_id: str,
        preset: str,
        performance: str,
        name: str,
        sample: str | None = None,
        attach: bool = True,
    ) -> dict[str, Any]:
        extra = {} if sample is None else {"sample": sample}
        return await self._with(
            lambda s: characters_mod.make_voice(
                s, project_id, entity_id, preset, performance, name=name, attach=attach, **extra
            )
        )

    async def character_clear_voice(self, project_id: str, entity_id: str) -> dict[str, Any]:
        return await self._with(lambda s: characters_mod.clear_voice(s, project_id, entity_id))

    async def clip_save_frame(self, project_id: str, media_id: str) -> dict[str, Any]:
        return await self._with(lambda s: clips_mod.save_frame(s, project_id, media_id))

    async def scene_save_clip(self, project_id: str, scene_id: str, clip_id: str) -> dict[str, Any]:
        return await self._with(lambda s: scenes_mod.save_clip_to_project(s, project_id, scene_id, clip_id))

    async def tools(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return await self._with(lambda s: reader.tools(s, project_id))

    async def download(self, project_id: str, media_id: str, out_dir: str | None = None) -> str:
        # `download._write_new` refuses to clobber an existing file, so the hazard here is not overwriting: it is
        # that the writer happily MAKES directories, dropping media anywhere on the disk (CLAUDE.md rule 5).
        target = self._editor_out_dir(out_dir)
        return str(await self._with(lambda s: download_mod.download(s, project_id, media_id, target)))

    async def upload(self, project_id: str, path: str) -> dict[str, Any]:
        return await self._with(lambda s: uploads_mod.upload(s, project_id, Path(path)))

    async def project_create(self, title: str | None = None) -> dict[str, Any]:
        return await self._with(lambda s: projects_mod.create(s, title))

    async def project_rename(self, project_id: str, title: str) -> str:
        return await self._with(lambda s: projects_mod.rename(s, project_id, title))

    async def project_delete(self, project_id: str) -> dict[str, Any]:
        return await self._with(lambda s: projects_mod.delete(s, project_id))

    async def character_create(
        self,
        project_id: str,
        prompt: str | None = None,
        name: str | None = None,
        personality: str | None = None,
        wait: float = 90.0,
        image: str | None = None,
    ) -> dict[str, Any]:
        return await self._with(
            lambda s: characters_mod.create(
                s,
                project_id,
                prompt,
                image=Path(image) if image else None,
                name=name,
                personality=personality,
                wait=wait,
            )
        )

    async def character_delete(self, project_id: str, entity_id: str) -> dict[str, Any]:
        return await self._with(lambda s: characters_mod.delete(s, project_id, entity_id))

    async def scene_list(self, project_id: str, include_trashed: bool = False) -> list[dict[str, Any]]:
        return await self._with(
            lambda s: scenes_mod.list_scenes(s, project_id, include_trashed=include_trashed)
        )

    async def scene_create(self, project_id: str, title: str | None = None) -> dict[str, Any]:
        return await self._with(lambda s: scenes_mod.create(s, project_id, title))

    async def scene_delete(self, project_id: str, scene_id: str) -> dict[str, Any]:
        return await self._with(lambda s: scenes_mod.delete(s, project_id, scene_id))

    async def scene_restore(self, project_id: str, scene_id: str) -> dict[str, Any]:
        return await self._with(lambda s: scenes_mod.restore(s, project_id, scene_id))

    async def scene_add_clip(self, project_id: str, scene_id: str, media_id: str) -> dict[str, Any]:
        return await self._with(lambda s: scenes_mod.add_clip(s, project_id, scene_id, media_id))

    async def scene_download(
        self, project_id: str, scene_id: str, out_dir: str | None = None
    ) -> dict[str, Any]:
        target = self._editor_out_dir(out_dir)
        # Measured on 2026-09-16 and 2026-09-17: the browser can die just as the film lands, a click can start no export
        # at all, and a built film can fail to reach the browser. None changes anything in Flow and none spends, so one
        # retry in a fresh session is safe. Any other failure is passed straight through, and the credit tools never do
        # this: there a second attempt is a second bill.
        flaky = (
            "has been closed",
            'waiting for event "download"',
            "started no export",
            "no file reached this browser",
            "failed in the browser",
        )
        for attempt in range(2):
            try:
                film = await self._with(
                    lambda s: scenes_mod.download(s, project_id, scene_id, out_dir=target)
                )
                # Reported, so a retry is not invisible to the agent reading the result (review 2026-09-16).
                return {**film, "attempts": attempt + 1}
            except Exception as exc:
                if attempt or not any(mark in str(exc) for mark in flaky):
                    raise
        raise AssertionError("unreachable")

    async def scene_rename(self, project_id: str, scene_id: str, title: str) -> dict[str, Any]:
        return await self._with(lambda s: scenes_mod.rename(s, project_id, scene_id, title))

    async def scene_clips(self, project_id: str, scene_id: str) -> dict[str, Any]:
        return await self._with(lambda s: scenes_mod.timeline(s, project_id, scene_id))

    async def scene_set_aspect(self, project_id: str, scene_id: str, aspect: str) -> dict[str, Any]:
        return await self._with(lambda s: scenes_mod.set_aspect(s, project_id, scene_id, aspect))

    async def scene_remove_clip(self, project_id: str, scene_id: str, clip_id: str) -> dict[str, Any]:
        return await self._with(lambda s: scenes_mod.remove_clip(s, project_id, scene_id, clip_id))

    async def scene_move_clip(
        self, project_id: str, scene_id: str, clip_id: str, position: int
    ) -> dict[str, Any]:
        return await self._with(lambda s: scenes_mod.move_clip(s, project_id, scene_id, clip_id, position))

    async def agent_mode(self, project_id: str, enabled: bool) -> dict[str, Any]:
        return await self._with(lambda s: agent_mod.set_mode(s, project_id, enabled))

    async def agent_send(
        self, project_id: str, message: str, wait: float = 60.0, job_id: str | None = None
    ) -> dict[str, Any]:
        return await self._spend_once(
            job_id,
            self.out_dir,
            lambda: self._with(
                lambda s: agent_mod.send(
                    s, project_id, message, wait=wait, out_dir=self.out_dir, job_id=job_id
                )
            ),
        )

    async def clip_download(
        self,
        project_id: str,
        media_id: str,
        quality: str = "1080p",
        out_dir: str | None = None,
        workflow_id: str | None = None,
    ) -> str:
        target = self._editor_out_dir(out_dir)
        return str(
            await self._with(
                lambda s: clips_mod.download_rendition(
                    s, project_id, media_id, quality, target, workflow_id=workflow_id
                )
            )
        )

    async def clip_reconcile(self, project_id: str, out_dir: str | None = None) -> dict[str, Any]:
        # A ledger outside the out folder is worse than a stray file: `_spend_once` sweeps only what lives under
        # out/, so a job_id written elsewhere would never stop the second spend (review 2026-09-18).
        target = self._editor_out_dir(out_dir)
        ledger = target / "ledger.jsonl"
        # jobs [] reads as "clean" only next to the ledger it came from: a missing file also yields [].
        found = {"ledger": str(ledger.resolve()), "ledger_exists": ledger.exists()}
        found["ledger_rows"] = len(gen_mod.Ledger(ledger).rows())
        if clips_mod.reconcile_needs_flow(project_id, out_dir=target):
            jobs = await self._with(lambda s: clips_mod.reconcile_editor(s, project_id, out_dir=target))
        else:
            # Nothing here can be judged, so there is no listing to read and no reason to wait for a browser.
            jobs = await clips_mod.reconcile_editor(None, project_id, out_dir=target)
        return {**found, "jobs": jobs}

    async def uploads(self, project_id: str) -> dict[str, Any]:
        return await self._with(lambda s: uploads_mod.list_uploads(s, project_id))

    async def clip_recipe(
        self, project_id: str, media_id: str, workflow_id: str | None = None
    ) -> dict[str, Any]:
        return await self._with(lambda s: reader.recipe(s, project_id, media_id, workflow_id=workflow_id))

    async def clip_extend(
        self,
        project_id: str,
        media_id: str,
        prompt: str,
        job_id: str | None = None,
        out_dir: str | None = None,
        wait: float = 240.0,
    ) -> dict[str, Any]:
        target = self._editor_out_dir(out_dir)
        return await self._spend_once(
            job_id,
            target,
            lambda: self._with(
                lambda s: clips_mod.extend(
                    s, project_id, media_id, prompt, out_dir=target, job_id=job_id, wait=wait
                )
            ),
        )

    async def clip_edit(
        self,
        project_id: str,
        media_id: str,
        prompt: str,
        job_id: str | None = None,
        out_dir: str | None = None,
        wait: float = 240.0,
    ) -> dict[str, Any]:
        target = self._editor_out_dir(out_dir)
        return await self._spend_once(
            job_id,
            target,
            lambda: self._with(
                lambda s: clips_mod.edit(
                    s, project_id, media_id, prompt, out_dir=target, job_id=job_id, wait=wait
                )
            ),
        )

    async def gen_character(
        self,
        project: str,
        prompt: str,
        characters: list[str],
        media_ids: list[str] | None = None,
        model: str = VIDEO_DEFAULT_MODEL,
        aspect: str = "9:16",
        dry_run: bool = False,
        job_id: str | None = None,
        out_dir: str | None = None,
        duration: int = ingredients_mod.SECONDS,
    ) -> dict[str, Any]:
        # A dry run spends nothing but still opens a browser for a minute or two, so a folder that could not hold
        # the clip or its ledger is refused here rather than after the wait.
        target = self._editor_out_dir(out_dir)

        def run() -> Awaitable[Any]:
            return self._with(
                lambda s: ingredients_mod.generate(
                    s,
                    project,
                    prompt=prompt,
                    characters=characters,
                    media_ids=media_ids or [],
                    model=model,
                    aspect=aspect,
                    job_id=job_id,
                    out_dir=target,
                    dry_run=dry_run,
                    duration=duration,
                )
            )

        # A dry run clicks nothing and writes no row, so there is no job to guard.
        return await run() if dry_run else await self._spend_once(job_id, target, run)

    async def gen_video(self, project: str, prompt: str, **options: Any) -> dict[str, Any]:
        out_dir = options.pop("out_dir", None)
        job_id = options.get("job_id")
        dry_run = options.get("dry_run", False)
        target = self._editor_out_dir(out_dir)

        def run() -> Awaitable[Any]:
            return self._with(
                lambda s: video_mod.generate(s, project, prompt=prompt, out_dir=target, **options)
            )

        # A dry run clicks nothing and writes no row, so there is no job to guard.
        return await run() if dry_run else await self._spend_once(job_id, target, run)

    async def generate(
        self,
        *,
        kind: str,
        prompt: str,
        project: str,
        model: str | None = None,
        aspect: str | None = None,
        count: int = 1,
        duration: int | None = None,
        initial_frame: str | None = None,
        end_frame: str | None = None,
        refs: list[str] | None = None,
        job_id: str | None = None,
        out_dir: str | None = None,
    ) -> dict[str, Any]:
        if kind in gen_mod.VIDEO_KINDS:
            model, duration = _video_settings(kind, model, duration, aspect, count, len(refs or []))
        job = gen_mod.Job(
            job_id=job_id or str(uuid.uuid4()),
            kind=kind,
            prompt=prompt,
            project=project,
            model=model,
            aspect=aspect,
            count=count,
            duration=duration,
            initial_frame=Path(initial_frame) if initial_frame else None,
            end_frame=Path(end_frame) if end_frame else None,
            refs=[Path(r) for r in refs or []],
        )
        target = Path(out_dir) if out_dir else self.out_dir

        async def run() -> dict[str, Any]:
            was_on = await self._agent_off(project)
            try:
                result = await gen_mod.run_job(job, target, profile=self.profile)
            except BaseException:
                if was_on:
                    await self._agent_restore(project)
                raise
            if was_on:
                result["agent_mode_restored"] = await self._agent_restore(project)
            if kind in gen_mod.IMAGE_KINDS:
                await self._image_media_ids(project, result.get("outputs") or [])
            return result

        return await self._spend_once(job_id, target, run)

    async def _image_media_ids(self, project: str, outputs: list[dict[str, Any]]) -> None:
        """gflow names an image by its workflow id (measured 2026-09-30), which gen_video refuses as a frame: the
        media id is read off the listing, which can trail a fresh image, and the workflow id is kept beside it."""
        if not outputs:
            return
        for output in outputs:
            output["workflow_id"], output["media_id"] = output.get("media_id"), None
        why = "the project listing never showed it"
        try:
            for attempt in range(IMAGE_LISTING_READS):
                if attempt:
                    await asyncio.sleep(IMAGE_LISTING_POLL_S)
                media = (await self._with(lambda s: reader.project(s, project)))["media"]
                by_workflow = {m.get("workflow_id"): m["id"] for m in media if m.get("workflow_id")}
                for output in outputs:
                    output["media_id"] = by_workflow.get(output["workflow_id"])
                if all(output["media_id"] for output in outputs):
                    return
        # The image is made and its ledger row says done, which refuses this job id again: never lose the path here.
        except Exception as exc:  # noqa: BLE001
            why = f"the project listing could not be read ({type(exc).__name__}: {str(exc)[:120]})"
        for output in outputs:
            if not output["media_id"]:
                output["media_id_note"] = (
                    f"{why}, so this image of workflow {output['workflow_id']} has no media id yet: find it with "
                    "flow_media (its workflow_id), or flow_upload the file at path"
                )

    async def _agent_off(self, project: str) -> bool:
        """gflow dies with exit 25 while Flow's Agent chip is on (measured 2026-09-28), so it goes off first; returns
        whether it was on. Anything short of off stops the call here, before gflow or the ledger is touched."""
        try:
            state = await self._with(lambda s: agent_mod.set_mode(s, project, False))
        except PlaywrightTimeoutError as exc:
            # Only the chip's own wait: a project page that never loads (a wrong id) keeps its own error (review, e1).
            if "agent-mode-chip" not in str(exc):
                raise
            raise LookupError(
                "Flow's Agent chip never showed in this project, so Agent mode could not be checked; it is the "
                "'Agent' button in the prompt bar at the bottom of the project page, right of the '+' button, and "
                "gflow cannot generate while it is on. Nothing was run and nothing was spent."
            ) from exc
        if state.get("enabled"):
            raise RuntimeError(
                "Flow's Agent mode stayed on after the chip was clicked, and gflow cannot generate while it is on. "
                "Nothing was run and nothing was spent."
            )
        return bool(state.get("was"))

    async def _agent_restore(self, project: str) -> bool:
        """Put Agent mode back on after the run; never raises, since it follows a call that may have spent."""
        try:
            state = await self._with(lambda s: agent_mod.set_mode(s, project, True))
            return bool(state.get("enabled"))
        except Exception:  # noqa: BLE001
            return False


class TellingServer(MCPServer):
    """mcp shows the agent only "Error executing tool <name>" for any exception that is not a ToolError
    (mcp/server/mcpserver/exceptions.py:61-73). Measured 2026-09-15: a WAF stop, a refused job_id and a missing
    project all reached the agent as that same bare line, so the reason is passed on here, scrubbed of secrets."""

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        # An undeclared argument used to vanish and the tool ran on its defaults (measured 2026-09-28: gen_t2i took an
        # out_dir it does not declare). On a paying tool that is a bill for the default, so it stops here, first.
        declared = {tool.name: tool.input_schema or {} for tool in await self.list_tools()}
        if name in declared:
            takes = sorted((declared[name].get("properties") or {}).keys())
            unknown = sorted(set(arguments or {}) - set(takes))
            if unknown:
                raise ToolError(
                    f"{name} got unknown argument(s) {unknown}; it takes {takes}. "
                    "Nothing was run and nothing was spent."
                )
        try:
            return await super().call_tool(name, arguments, context)
        except UnexpectedToolError as exc:
            cause = exc.__cause__ or exc
            # A Playwright error appends a call log listing request headers, cookies included: drop it whole.
            text = str(cause).split("\nCall log:")[0]
            detail = redact_error_detail(gen_mod._scrub(f"{type(cause).__name__}: {text}"))
            # Only the scrubbed line is logged: the raw traceback would put the same cookies in the server's stderr.
            logging.getLogger(__name__).error("tool %s failed: %s", name, detail)
            raise ToolError(f"Error executing tool {name}: {detail}") from cause


backend = Backend()
server = TellingServer(
    "video",
    instructions=(
        "Google Flow (flow.google.com) control for this account. These tools spend Flow credits and are "
        "recorded in the ledger (out/ledger.jsonl by default): gen_video, gen_t2v, gen_i2v, gen_r2v, "
        "gen_character, clip_extend, clip_edit, and agent_send (may spend). gen_video covers every video option "
        "Flow's composer offers (every model, 360p/720p, 4-10 s, x1-x4, first and last frame, ingredients) and "
        "needs max_credits for a real run. clip_download at 4k is a Flow upscale its price "
        "table offers only from the Ultra plan, at 50 credits; on this Pro account Flow greys it out, so it is "
        "refused before any click. gen_t2i and gen_i2i are credit-free but draw on a daily image quota. Check a tool's description "
        "for its cost before calling it: each carries what was MEASURED here and, where Flow's published table "
        "disagrees, that figure too (Omni Flash Edit is listed at 40 and measured 20). Every call drives a real Chrome "
        "session and blocks until Flow answers: a read takes about 15-50 s and a change about 50 s, a generation "
        "2-5 min, clip_extend and clip_edit up to about 7 min, so a slow call is not a failed one. The "
        "credit-spending tools require a job_id. Never call one again under a new job_id because it was slow, "
        "errored or timed out: check flow_media and flow_credits first, and if you do call again keep the same "
        "job_id, which the ledger refuses instead of charging twice. When model is omitted gen_t2v and gen_i2v "
        "use omni-flash for 10 s, and gen_r2v uses omni-flash at 8 s, the only length this host offers it. "
        "When aspect is omitted, all three run 9:16: that is gflow's own default, not the shape of the image you "
        "passed, and Flow crops a start frame of another shape to fit. Name the aspect you want. "
        "If a tool reports that Google flagged unusual activity (WAF), stop: do not retry and "
        "do not re-authenticate; tell the owner. Pass an existing project id from flow_projects, or make one "
        "with project_create."
    ),
)

_IMAGE_IDS = (
    " Each output's media_id is the project media id that gen_video takes as start_frame, end_frame or media_ids; "
    "workflow_id is the id gflow itself reports. The listing can trail a new image, so this call reads it for up to "
    "about a minute; when it never shows the image, media_id is null and media_id_note says how to find it."
)
_AGENT_NOTE = (
    " Flow's Agent mode is turned off in the project before gflow runs, since gflow cannot generate while it is on, "
    "and turned back on afterwards if it was on; measured 2026-09-29, this adds about 19 s, or about 35 s when it "
    "was on."
)
_BALANCE_MOVED = (
    "When the balance moved by anything other than the measured price, the answer carries balance_moved "
    "{kind, measured, moved, note}, or, when the call ends in an error, the job's ledger row does: read it before "
    "assuming the price in this description still holds."
)
_JOB_ID_RULE = (
    " job_id is required: use a new one for each new job, and keep the SAME one when calling again after an "
    "error or a timeout. Any job_id already in a ledger under the out folder is refused before a browser opens, so a "
    "retry never pays twice; that refusal means the job may already have spent credits, so check flow_media and "
    "flow_credits before starting it under a new one. A job_id still running in another call is refused too: wait "
    "for that call to finish and call again with the SAME job_id, never a new one."
)


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _require(value: str, name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} is required")


def _one_line(value: str, name: str) -> None:
    # The clip editor and the agent box are typed into with keyboard.type, which presses Enter for a newline.
    if "\n" in value or "\r" in value:
        raise ValueError(
            f"{name} must be one line: Flow's box takes a newline as Enter, which may submit early"
        )


@server.tool(name="flow_lane", description="Which lane the profile is on (LABS, MIGRATED, SIGNED_OUT). Free.")
async def flow_lane() -> str:
    return _json(await backend.lane())


@server.tool(name="flow_projects", description="List the account's Flow projects (id, title, created). Free.")
async def flow_projects() -> str:
    return _json(await backend.projects())


@server.tool(name="flow_credits", description="Current Flow credit balance. Free.")
async def flow_credits() -> str:
    return _json(await backend.credits())


@server.tool(
    name="flow_media",
    description=(
        "A project's media (id, kind, model, size, url), meta and models, always as one object, in Flow's own "
        "listing order, which is NOT sorted by age: read `created` (epoch seconds) to tell what is new. "
        "all_versions=true adds a versions list holding every generation record: each Omni edit or upscale "
        "stacks another version onto the SAME media id, and only that list shows them, so it is how you find "
        "the clip an edit produced. A whole project is big (measured 2026-09-17: 28,378 characters, and 122,919 "
        "with all_versions), so four filters cut it: kind is 'video' or 'image'; since keeps rows made at or "
        "after epoch seconds or an ISO date (2026-09-17, or 2026-09-17T08:30+07:00; a date with no zone is this "
        "machine's day); limit keeps that many rows of EACH list, the newest by created, and puts them newest "
        "first; brief=true drops each row's url and cuts its prompt to 120 characters plus an ellipsis, which is "
        "where most of the weight sits. Ask for any of them and the answer also carries media_total (and "
        "versions_total with all_versions), how many rows the project holds BEFORE filtering, plus truncated, "
        "true when any row was left out; brief cuts fields, not rows, so it leaves truncated false. A filter it "
        "cannot honour is refused, never silently ignored. Free."
    ),
)
async def flow_media(
    project_id: str,
    all_versions: bool = False,
    kind: str | None = None,
    since: str | float | None = None,
    limit: int | None = None,
    brief: bool = False,
) -> str:
    _require(project_id, "project_id")
    return _json(await backend.media(project_id, all_versions, kind, since, limit, brief))


@server.tool(
    name="flow_characters",
    description=(
        "A project's characters: entity_id, name, portrait_media_id (the id flow_download accepts; null when "
        "the portrait is not in the listing) and portrait_workflow_id. Free."
    ),
)
async def flow_characters(project_id: str) -> str:
    _require(project_id, "project_id")
    return _json(await backend.characters(project_id))


@server.tool(
    name="flow_tools",
    description=(
        "The community Tools gallery (id, name, author, tags), the same in every project. project_id is "
        "optional: Flow only loads the gallery inside a project, so without one the first project on the grid "
        "is opened, which costs one extra page load. Free."
    ),
)
async def flow_tools(project_id: str | None = None) -> str:
    return _json(await backend.tools(project_id or None))


@server.tool(
    name="flow_download",
    description=(
        "Download one media item to out_dir as <media_id>.<ext>. out_dir must be inside the out folder, and a "
        "folder it names is created for you. Free."
    ),
)
async def flow_download(project_id: str, media_id: str, out_dir: str | None = None) -> str:
    _require(project_id, "project_id")
    _require(media_id, "media_id")
    return _json({"path": await backend.download(project_id, media_id, out_dir)})


@server.tool(
    name="flow_voices",
    description=(
        "The voices a character can speak with, read off the character's own voice selector: 30 presets on this "
        "account (measured 2026-09-18), each a name and a one-line description like 'Female, youthful, mid-high "
        "pitch', plus every voice character_make_voice has saved here, which are listed first and say so under "
        "`custom`. Needs a character to read them from, because that page is the only place Flow shows them. "
        "Free."
    ),
)
async def flow_voices(project_id: str, entity_id: str) -> str:
    _require(project_id, "project_id")
    _require(entity_id, "entity_id")
    return _json(await backend.voices(project_id, entity_id))


@server.tool(
    name="character_set_voice",
    description=(
        "Give a character one of Flow's preset voices (names from flow_voices), so a generation starring that "
        "character can speak. The voice belongs to the CHARACTER, not to a generation: set it once and every "
        "later run of that character uses it. Measured 2026-09-18: free, about 20 s, and the page then shows the "
        "voice with a play button. A name the selector does not offer is refused with the list it does. Free."
    ),
)
async def character_set_voice(project_id: str, entity_id: str, voice: str) -> str:
    _require(project_id, "project_id")
    _require(entity_id, "entity_id")
    _require(voice, "voice")
    return _json(await backend.character_set_voice(project_id, entity_id, voice))


@server.tool(
    name="character_make_voice",
    description=(
        "Make a VOICE OF YOUR OWN and give it to a character: a preset from flow_voices plus a written "
        "performance ('giọng nữ Sài Gòn, nhỏ nhẹ, nhí nhảnh, khoảng 20 tuổi'), saved under a name so later "
        "characters can reuse it. Use it rather than character_set_voice when the words matter: that tool sends "
        "the character update with the preset NAME only (measured rpc body, 2026-09-18), so a performance "
        "written next to a preset is not part of what gets attached. Measured 2026-09-18: free, about "
        "45 s, because Flow synthesises a preview first and the save does nothing until that answer lands. "
        "sample is the line Flow speaks in the preview, 120 characters at most. attach false saves the voice "
        "without changing the character's current one. The saved sample also shows up in flow_media as a row "
        "whose kind is video, titled with the voice name, carrying no url and no prompt: that row is the voice, "
        "not a clip. Free."
    ),
)
async def character_make_voice(
    project_id: str,
    entity_id: str,
    preset: str,
    performance: str,
    name: str,
    sample: str | None = None,
    attach: bool = True,
) -> str:
    _require(project_id, "project_id")
    _require(entity_id, "entity_id")
    _require(preset, "preset")
    _require(performance, "performance")
    _require(name, "name")
    return _json(
        await backend.character_make_voice(
            project_id, entity_id, preset, performance, name, sample=sample, attach=attach
        )
    )


@server.tool(
    name="character_clear_voice",
    description="Take the voice off a character, leaving it silent again. Free.",
)
async def character_clear_voice(project_id: str, entity_id: str) -> str:
    _require(project_id, "project_id")
    _require(entity_id, "entity_id")
    return _json(await backend.character_clear_voice(project_id, entity_id))


@server.tool(
    name="clip_save_frame",
    description=(
        "Save the frame the clip editor opens on as an IMAGE of the project, and answer its media_id. That image "
        "is how a later shot continues this one: download it with flow_download and give that file to gen_i2v as "
        "initial_frame, which takes a path on this machine. Passing the media_id straight to gen_i2v has never "
        "been run, so do not assume it works. Measured 2026-09-18: free, "
        "about 55 s when it works: the grid shows the image titled 'Saved frame from <clip>' about 40 s after the "
        "click, which this tool waits 90 s for. It does NOT always work: of five live clicks on 2026-09-18 three "
        "left an image (media 1765128f, 68413992 and 508cb3cd on project 118aece2) and two left none, both of "
        "those after Flow had raised its 'Saving frame' notice, so a refusal here can also mean a save Flow took "
        "and lost. It never invents a media id. It saves the frame the editor shows, which is the clip's start. The "
        "editor draws into a canvas about five seconds after the page is ready and Flow uploads whatever that "
        "canvas holds, so the tool waits until it has painted and REFUSES a blank editor rather than store a black "
        "picture (a run that clicked too early saved 1080x1920 of pure black). Free."
    ),
)
async def clip_save_frame(project_id: str, media_id: str) -> str:
    _require(project_id, "project_id")
    _require(media_id, "media_id")
    return _json(await backend.clip_save_frame(project_id, media_id))


@server.tool(
    name="scene_save_clip",
    description=(
        "Copy one clip of a scene's timeline (clip_id from scene_clips) onto the project grid as its own media, "
        "so other scenes and tools can use it: a clip that lives only inside a scene is invisible to flow_media. "
        "The timeline is not changed. The answer's rpcids are only what was overheard after the click, not proof: "
        "a live run copied the clip without Sc7aEb showing up in that window, so the listing row is the evidence. "
        "Measured 2026-09-18: free, and the new media appears about 40 s later, "
        "which this tool waits for. Free."
    ),
)
async def scene_save_clip(project_id: str, scene_id: str, clip_id: str) -> str:
    _require(project_id, "project_id")
    _require(scene_id, "scene_id")
    _require(clip_id, "clip_id")
    return _json(await backend.scene_save_clip(project_id, scene_id, clip_id))


@server.tool(name="flow_upload", description="Upload a local image or video into a project. Free.")
async def flow_upload(project_id: str, path: str) -> str:
    _require(project_id, "project_id")
    _require(path, "path")
    return _json(await backend.upload(project_id, path))


@server.tool(name="project_create", description="Create a project on the grid, optionally renaming it. Free.")
async def project_create(title: str | None = None) -> str:
    return _json(await backend.project_create(title))


@server.tool(
    name="project_rename",
    description=(
        "Rename a project, then confirm the new title on the project grid (the listing flow_projects reads). "
        "Replies {id, title} with the title as the grid lists it, and fails if the grid shows another. Free."
    ),
)
async def project_rename(project_id: str, title: str) -> str:
    _require(project_id, "project_id")
    _require(title, "title")
    return _json({"id": project_id, "title": await backend.project_rename(project_id, title)})


@server.tool(
    name="project_delete", description="Delete a project permanently (clips, ingredients, prompts). Free."
)
async def project_delete(project_id: str) -> str:
    _require(project_id, "project_id")
    return _json(await backend.project_delete(project_id))


@server.tool(
    name="character_create",
    description=(
        "Create a character, then set name and personality. Give exactly one of prompt (a face described in words; "
        "the portrait comes from Nano Banana 2, credit-free) and image (a local png, jpg, jpeg or webp of a face; "
        "the upload becomes the portrait). Flow has refused photos without a message, for example of people wearing "
        "lace (measured 2026-09-13), while a close-up portrait was accepted. The reply's portrait.workflow_id is NOT "
        "a media id: call flow_characters for the portrait's media id, which flow_download accepts. Free."
    ),
)
async def character_create(
    project_id: str,
    prompt: str | None = None,
    image: str | None = None,
    name: str | None = None,
    personality: str | None = None,
    wait: float = 90.0,
) -> str:
    _require(project_id, "project_id")
    if bool(prompt and prompt.strip()) == bool(image and image.strip()):
        raise ValueError("give exactly one of prompt and image")
    if image and not Path(image).is_file():
        raise ValueError(f"no image file at {image}")
    return _json(await backend.character_create(project_id, prompt, name, personality, wait, image))


@server.tool(name="character_delete", description="Delete a character entity permanently. Free.")
async def character_delete(project_id: str, entity_id: str) -> str:
    _require(project_id, "project_id")
    _require(entity_id, "entity_id")
    return _json(await backend.character_delete(project_id, entity_id))


@server.tool(
    name="scene_list", description="List a project's scenes (Scenebuilder); trashed ones on request. Free."
)
async def scene_list(project_id: str, include_trashed: bool = False) -> str:
    _require(project_id, "project_id")
    return _json(await backend.scene_list(project_id, include_trashed))


@server.tool(name="scene_create", description="Create a scene (Scenebuilder), optionally titled. Free.")
async def scene_create(project_id: str, title: str | None = None) -> str:
    _require(project_id, "project_id")
    return _json(await backend.scene_create(project_id, title))


@server.tool(
    name="scene_delete",
    description=(
        "Move a scene to the project's trash; scene_restore brings it back. Grid tiles carry no scene id, so the tile "
        "is found by the scene's exact title, once the grid shows one tile per active scene (waited for, up to 15 s). "
        "A title that is blank or only invisible characters, a title two scenes share, and a grid that never shows "
        "that many tiles are all refused rather than guessed. Free."
    ),
)
async def scene_delete(project_id: str, scene_id: str) -> str:
    _require(project_id, "project_id")
    _require(scene_id, "scene_id")
    return _json(await backend.scene_delete(project_id, scene_id))


@server.tool(
    name="scene_restore",
    description=(
        "Bring a trashed scene back from the project's trash (undoes scene_delete), confirmed by the listing. "
        "The trash shows no ids, so the tile is found by the scene's exact title, once the trash shows one tile per "
        "trashed scene (waited for, up to 15 s). A title that is blank or only invisible characters, a title more "
        "than one trashed scene shares, and a trash that never shows that many tiles are all refused. Free."
    ),
)
async def scene_restore(project_id: str, scene_id: str) -> str:
    _require(project_id, "project_id")
    _require(scene_id, "scene_id")
    return _json(await backend.scene_restore(project_id, scene_id))


@server.tool(
    name="scene_add_clip",
    description=(
        "Put one of the project's clips at the end of a scene's timeline, which is how a scene becomes a film made "
        "of several clips: add them in the order the film should play them. The picker carries no media id, only "
        "the media's title, so the media id is resolved to its title through the listing and refused rather than "
        "guessed when that title is blank, when another media in the project shares it, or when the picker shows it "
        "more than once. Flow stores a clip only when it answers the add, 11 to 14 s after the click (measured "
        "2026-09-17), so the call waits for that answer and then reads the listing back: the result carries the "
        "clip's position (counted from 0), its clip_id, the scene's seconds, every clip in order, and answered, "
        "which is false when Flow's own answer never arrived and the listing alone showed the clip on the timeline. "
        "If the call fails after the click, the clip may still land: read scene_clips first and never add it again "
        "before you have, or the film gets it twice. Free."
    ),
)
async def scene_add_clip(project_id: str, scene_id: str, media_id: str) -> str:
    _require(project_id, "project_id")
    _require(scene_id, "scene_id")
    _require(media_id, "media_id")
    return _json(await backend.scene_add_clip(project_id, scene_id, media_id))


@server.tool(
    name="scene_download",
    description=(
        "Download a scene as ONE film, the whole timeline rather than a single clip: two 8 s clips came back as "
        "one 16.0 s mp4 (measured 2026-09-16). The film is built inside the page before it is handed over, and a "
        "40 s film took 33 to 42 s to build (measured 2026-09-17), so the call waits while Flow shows the export "
        "running, as long as the film's length warrants, and fails fast when the click starts no export. A scene "
        "with nothing on its timeline is refused, and out_dir must sit inside out/. The file is written under a "
        "temporary name and renamed only once whole, so a partial film never sits under the name returned. The "
        "result carries the seconds and clips the listing gives the scene, to check the film against, and "
        "attempts: 2 means the first try failed on the way and the film was fetched again in a fresh session. A "
        "scene offers no quality choice, so use clip_download for a single media and its 270p, 720p, 1080p or 4k "
        "renditions. Free."
    ),
)
async def scene_download(project_id: str, scene_id: str, out_dir: str | None = None) -> str:
    _require(project_id, "project_id")
    _require(scene_id, "scene_id")
    return _json(await backend.scene_download(project_id, scene_id, out_dir))


@server.tool(
    name="scene_rename",
    description=(
        "Rename a scene, confirmed by re-reading the listing. A title that is blank or only invisible characters "
        "is refused, since the scene tools find a scene by its exact title. Free."
    ),
)
async def scene_rename(project_id: str, scene_id: str, title: str) -> str:
    _require(project_id, "project_id")
    _require(scene_id, "scene_id")
    _require(title, "title")
    return _json(await backend.scene_rename(project_id, scene_id, title))


@server.tool(
    name="scene_clips",
    description=(
        "Read one scene's timeline as Flow stores it: its clips in the order the film plays them, each with its "
        "position (counted from 0), clip_id, title and seconds, plus the scene's aspect ratio (9:16 or 16:9, "
        "null when the listing names neither) and its total seconds. A clip_id names one clip on this timeline, "
        "not a project media: adding the same media twice gives two clip_ids, and scene_move_clip and "
        "scene_remove_clip take a clip_id. The page's own Total duration label "
        "moves before Flow has stored a change (measured 2026-09-17), so read this again after changing the "
        "timeline instead of trusting the label or an earlier answer. Free."
    ),
)
async def scene_clips(project_id: str, scene_id: str) -> str:
    _require(project_id, "project_id")
    _require(scene_id, "scene_id")
    return _json(await backend.scene_clips(project_id, scene_id))


@server.tool(
    name="scene_set_aspect",
    description=(
        "Set the aspect ratio a scene's film is exported in: 9:16 (portrait) or 16:9 (landscape); a new scene starts "
        "at 16:9 (measured 2026-09-17). The editor offers one toggle, so a scene already at the ratio asked for is "
        "left alone, and a scene whose page shows another ratio than Flow's listing is refused rather than toggled "
        "blind. Confirmed by reading the listing back; scene_clips shows the ratio as aspect. Free."
    ),
)
async def scene_set_aspect(project_id: str, scene_id: str, aspect: str) -> str:
    _require(project_id, "project_id")
    _require(scene_id, "scene_id")
    if aspect not in scenes_mod.ASPECTS.values():
        raise ValueError(f"aspect must be 9:16 or 16:9, got {aspect!r}")
    return _json(await backend.scene_set_aspect(project_id, scene_id, aspect))


@server.tool(
    name="scene_remove_clip",
    description=(
        "Take one clip off a scene's timeline, named by its clip_id from scene_clips; the clips after it move up "
        "one position. Flow asks nothing before deleting (measured 2026-09-17), so the call acts only on the clip "
        "the listing places at that position once the editor shows as many clips as the listing holds, and checks "
        "the change the page sends before reading the listing back. The media itself stays in the project and "
        "scene_add_clip can put it back at the end. The result carries the remaining clips in order. Free."
    ),
)
async def scene_remove_clip(project_id: str, scene_id: str, clip_id: str) -> str:
    _require(project_id, "project_id")
    _require(scene_id, "scene_id")
    _require(clip_id, "clip_id")
    return _json(await backend.scene_remove_clip(project_id, scene_id, clip_id))


@server.tool(
    name="scene_move_clip",
    description=(
        "Move one clip, named by its clip_id from scene_clips, to a position counted from 0 on a scene's timeline; "
        "the clips in between shift by one. Flow reorders a timeline only by drag and drop, so the editor is zoomed "
        "out until both places are on screen and the clip is dragged there, then the order the page sends and the "
        "listing read back must both match the one asked for. A clip already at that position is left alone. The "
        "result carries every clip in its new order. Free."
    ),
)
async def scene_move_clip(project_id: str, scene_id: str, clip_id: str, position: int) -> str:
    _require(project_id, "project_id")
    _require(scene_id, "scene_id")
    _require(clip_id, "clip_id")
    if position < 0:
        raise ValueError(f"position counts from 0, got {position}")
    return _json(await backend.scene_move_clip(project_id, scene_id, clip_id, position))


@server.tool(
    name="agent_mode",
    description="Turn Flow's agent mode on or off for a project; leaving it on hides the composer settings. Free.",
)
async def agent_mode(project_id: str, enabled: bool) -> str:
    _require(project_id, "project_id")
    return _json(await backend.agent_mode(project_id, enabled))


@server.tool(
    name="agent_send",
    description=(
        "Send a message to Flow's agent in a project. It may spend credits: 0 credits in every measured send where "
        "the agent generated nothing, but a message that makes it generate media costs that generation's "
        "price. The send itself took 69-80 s in those runs, plus a balance read before and after. "
        + _BALANCE_MOVED
        + _JOB_ID_RULE
    ),
)
async def agent_send(project_id: str, message: str, job_id: str, wait: float = 60.0) -> str:
    _require(project_id, "project_id")
    _require(message, "message")
    _one_line(message, "message")
    _require(job_id, "job_id")
    return _json(await backend.agent_send(project_id, message, wait, job_id))


@server.tool(
    name="clip_download",
    description=(
        "Download a clip rendition from the editor: gif (270p), 720p, 1080p or 4k (upscaled by Flow). "
        "Defaults to the NEWEST finished version of the media; pass workflow_id (from flow_media with "
        "all_versions=true) to fetch one specific version, such as the clip a particular edit produced. "
        "1080p measured 0 credits. 4k is an upscale Flow's price table offers only from the Ultra plan, at 50 "
        "credits; on this Pro account the Download menu shows it greyed out (measured 2026-09-29 on every clip "
        "tried), so a 4k request is refused before any click and costs nothing."
    ),
)
async def clip_download(
    project_id: str,
    media_id: str,
    quality: str = "1080p",
    out_dir: str | None = None,
    workflow_id: str | None = None,
) -> str:
    _require(project_id, "project_id")
    _require(media_id, "media_id")
    if quality.lower() not in clips_mod.RENDITIONS:
        raise ValueError(f"quality must be one of {sorted(clips_mod.RENDITIONS)}")
    return _json({"path": await backend.clip_download(project_id, media_id, quality, out_dir, workflow_id)})


@server.tool(
    name="clip_reconcile",
    description=(
        "Close out editor jobs that spent credits without recording an outcome, by checking the listing "
        "and the balance. It reads Flow and writes the ledger, it never generates. Run this after a "
        "clip_extend or clip_edit died mid-flight, otherwise the spend has no outcome against it. Replies with "
        "the ledger it read (absolute path), ledger_exists, ledger_rows and one verdict per open job in jobs: "
        "jobs [] means nothing is left open in THAT ledger, so check that it exists; an error means the check "
        "itself failed. Verdicts: done (clip_edit only: exactly one new version on the source clip carries "
        "the job's own prompt and is held by no other job's generated outputs in that ledger, counting one still "
        "rendering; that version is finished; and no rival is left in that ledger, a job that named that clip and "
        "prompt and is open, or closed holding no version there, unless its last row is clip_reconcile's failed; "
        "it is written into outputs), failed (balance unchanged AND no record in the project that the job had not "
        "already seen when it opened; clip_extend can end here too), unknown (left open for a person: every "
        "clip_extend that is not failed, since nothing ties its new clip to the job; also while the version is "
        "still rendering, when two versions or a rival could own it, when the listing holds none of the source "
        "clip's records the job saw, or when the job's row predates recorded workflows and prompt or lists no "
        "workflows), skipped (never "
        "written: a gen_* or agent_send job, which names no clip, so check it with flow_media and flow_credits; or "
        "another project's editor job, whose project is given, so run clip_reconcile on that project). It opens "
        "the browser only when the ledger holds a job of this project it can judge, otherwise it answers at once. "
        "The spent it writes is the balance change since the job opened, which can include other spends and the "
        "balance moving on its own, so never add those rows up as a total. Free."
    ),
)
async def clip_reconcile(project_id: str, out_dir: str | None = None) -> str:
    _require(project_id, "project_id")
    return _json(await backend.clip_reconcile(project_id, out_dir))


@server.tool(
    name="flow_uploads",
    description="How many items the project's Uploads view holds, as {count}. Free.",
)
async def flow_uploads(project_id: str) -> str:
    _require(project_id, "project_id")
    return _json(await backend.uploads(project_id))


@server.tool(
    name="clip_recipe",
    description=(
        "What one clip was made from, read back off the project listing: the model key Flow ran, the kind of run "
        "(frames, ingredients, derived for an edit or an upscale, extend), the start and end frame, the reference "
        "images, the voices and the characters, each with its id and its name, and for a derived or extended clip "
        "the clip it came from. Use it after a paid generation to check that the clip carried what was asked: Flow "
        "drops a character typed into a Frames prompt without saying so (measured 2026-10-01), and this is where "
        "that shows. A voice is listed as Flow recorded it: a preset by its lowercase name, a voice made on this "
        "account by its id, with `custom` true. Defaults to the newest version of the media, never an upscale; "
        "pass workflow_id (from flow_media with all_versions=true) to read one specific version. An image keeps no "
        "recipe and is refused. Free."
    ),
)
async def clip_recipe(project_id: str, media_id: str, workflow_id: str | None = None) -> str:
    _require(project_id, "project_id")
    _require(media_id, "media_id")
    return _json(await backend.clip_recipe(project_id, media_id, workflow_id))


@server.tool(
    name="clip_extend",
    description=(
        "Extend a clip with Veo 3.1 Lite. It spends credits and is ledgered: 10 credits per extend (measured). "
        "Flow greys Extend out on some clips (measured: on Omni clips, and on a Veo clip after Omni edits and a 1080p "
        "upscale); the call is then refused before the click, at no cost. The extension is a new clip inside a new "
        "scene (the source is copied in first): its own file answered HTTP 400 and clip_download could not open it "
        "(measured 2026-09-29), so fetch it with scene_download on the scene_id this tool returns; that film's video "
        "stream ran 15.0 s for an 8 s source, the extension overlapping the source's last second. "
        "Takes about 2-3 min, up to about 7 min when Flow is slow. out_dir, when given, must be inside the out "
        "folder. " + _BALANCE_MOVED + _JOB_ID_RULE
    ),
)
async def clip_extend(
    project_id: str, media_id: str, prompt: str, job_id: str, out_dir: str | None = None
) -> str:
    _require(project_id, "project_id")
    _require(media_id, "media_id")
    _require(prompt, "prompt")
    _one_line(prompt, "prompt")
    _require(job_id, "job_id")
    return _json(await backend.clip_extend(project_id, media_id, prompt, job_id, out_dir))


@server.tool(
    name="clip_edit",
    description=(
        "Video-to-video edit of a clip with Omni 1.1 Flash. It spends credits and is ledgered: every edit measured "
        "on this account cost 20 credits, while Flow's own price table lists Omni Flash Edit at 40, so budget for "
        "40 and expect 20. This tool does not read the live price line before it clicks, so what stands between a "
        "changed price and a surprise bill is the balance read before and after, answered as credits_before and "
        "credits_after (the ledger row holds their difference as `spent`). Takes about "
        "2-3 min, up to about 7 min when Flow is slow. out_dir, when given, must be inside the out folder. "
        + _BALANCE_MOVED
        + _JOB_ID_RULE
    ),
)
async def clip_edit(
    project_id: str, media_id: str, prompt: str, job_id: str, out_dir: str | None = None
) -> str:
    _require(project_id, "project_id")
    _require(media_id, "media_id")
    _require(prompt, "prompt")
    _one_line(prompt, "prompt")
    _require(job_id, "job_id")
    return _json(await backend.clip_edit(project_id, media_id, prompt, job_id, out_dir))


async def _gen(kind: str, **kwargs: Any) -> str:
    _require(kwargs.get("project", ""), "project")
    _require(kwargs.get("prompt", ""), "prompt")
    if kind in gen_mod.VIDEO_KINDS:
        _require(kwargs.get("job_id") or "", "job_id")
    return _json(await backend.generate(kind=kind, **kwargs))


@server.tool(
    name="gen_t2v",
    description=(
        "Text to video via gflow. It spends credits and is ledgered, measured on the PRO plan: omni-flash 10 s "
        "x1 = 15 credits (the default when model is omitted), veo-lite 8 s x1 = 10 credits, and count "
        "multiplies it (veo-lite x2 = 20 credits). Allow 2-5 min: the gflow job took 74-85 s, plus a balance "
        "read before and after." + _JOB_ID_RULE + _AGENT_NOTE
    ),
)
async def gen_t2v(
    prompt: str,
    project: str,
    job_id: str,
    model: str | None = VIDEO_DEFAULT_MODEL,
    aspect: str | None = None,
    count: int = 1,
    duration: int | None = None,
) -> str:
    return await _gen(
        "t2v",
        prompt=prompt,
        project=project,
        model=model,
        aspect=aspect,
        count=count,
        duration=duration,
        job_id=job_id,
    )


@server.tool(
    name="gen_i2v",
    description=(
        "Image (first frame, optionally a last frame too) to video via gflow. It spends credits and is ledgered, "
        "both forms measured once each on 2026-09-18 at omni-flash 10 s x1, the default when model is omitted: a "
        "start frame alone = 15 credits in 105 s, and start + end_frame = 15 credits in 119 s. Those two runs used "
        "different images and prompts, so read each time on its own, not the gap between them. Pass end_frame to interpolate "
        "between two local images, at omni-flash 10 s only, which is the cell that was priced: any other model or "
        "length is refused, because Flow picks its interpolation model by cohort and that run has never been "
        "paid for here. The measured clip did begin and end on the frames given: over its 240 frames the one "
        "closest to the end image IS the last (1.9 of 255, converging 10.0, 9.1, 8.0, 6.2, 3.9, 1.9 over the "
        "final six), the one closest to the start image is frame 1 (2.1), and either image against the other "
        "end of the clip reads about 50. Both runs that finished did so on gflow "
        "0.78.0; every attempt before it died in Flow's frame picker and spent nothing. PASS aspect, and match it "
        "to your images: leaving it out means 9:16, gflow's own default, and Flow CROPS a frame of another shape "
        "to fit, which pushed the subject of a 16:9 photo half out of the left edge. Allow 2-5 min."
        + _JOB_ID_RULE
        + _AGENT_NOTE
    ),
)
async def gen_i2v(
    initial_frame: str,
    prompt: str,
    project: str,
    job_id: str,
    end_frame: str | None = None,
    model: str | None = VIDEO_DEFAULT_MODEL,
    aspect: str | None = None,
    duration: int | None = None,
) -> str:
    _require(initial_frame, "initial_frame")
    # One run priced one cell. gflow gates --end-frame on nothing and picks its interpolation model by cohort, so
    # any other model or length is a submit nobody here has paid for once (review 2026-09-18).
    priced = (model or VIDEO_DEFAULT_MODEL) == END_FRAME_MODEL and duration in (None, END_FRAME_SECONDS)
    if end_frame and not priced:
        raise ValueError(
            f"end_frame is measured only for {END_FRAME_MODEL} at {END_FRAME_SECONDS} s "
            f"(15 credits, 2026-09-18); asked for {model or VIDEO_DEFAULT_MODEL} at {duration or 'the default'} s. "
            "Flow picks a different interpolation model per cohort, so that run's price is unknown: leave model "
            "and duration out, or ask the owner to price the one you want."
        )
    return await _gen(
        "i2v",
        prompt=prompt,
        project=project,
        model=model,
        aspect=aspect,
        duration=duration,
        initial_frame=initial_frame,
        end_frame=end_frame,
        job_id=job_id,
    )


@server.tool(
    name="gen_r2v",
    description=(
        "Reference images (ingredients) to video via gflow. It spends credits and is ledgered: omni-flash x1 = 12 "
        "credits (the default when model is omitted, measured 2026-09-15) and veo-lite x1 = 10 credits "
        "(measured). It always runs 8 s through gflow, so leave duration out; for 10 s, put the images in the project "
        "with flow_upload and pass their media ids to gen_character. "
        "omni-flash takes up to 7 reference images, veo-lite, veo-fast and veo-lite-lp up to 3, veo-quality none; "
        "more is refused before anything is spent. "
        "Allow 2-5 min: that omni-flash run took 292 s end to end." + _JOB_ID_RULE + _AGENT_NOTE
    ),
)
async def gen_r2v(
    refs: list[str],
    prompt: str,
    project: str,
    job_id: str,
    model: str | None = VIDEO_DEFAULT_MODEL,
    aspect: str | None = None,
    duration: int | None = None,
) -> str:
    if not refs:
        raise ValueError("refs is required")
    return await _gen(
        "r2v",
        prompt=prompt,
        project=project,
        model=model,
        aspect=aspect,
        duration=duration,
        refs=refs,
        job_id=job_id,
    )


def _longer_lengths() -> str:
    # Written from the price table the driver charges against, so the two cannot drift apart.
    return " ".join(
        f"duration={seconds} is offered on {model} at {price} credits (the composer's own quote)."
        for (model, seconds), price in sorted(ingredients_mod.LONGER_PRICES.items())
    )


@server.tool(
    name="gen_character",
    description=(
        "Video starring the project's characters (entity ids from flow_characters), with or without images already "
        "in the project (media ids from flow_media, images only), or from those images alone, which is the way to a "
        "10 s reference video since gen_r2v runs 8 s only. A character goes into the prompt as a Flow @ mention and "
        "an image through the composer's '+' dialog, and every chip is checked against its id before anything is "
        "spent; a chip Flow refuses (an image over the model's cap, a character taking one of its image slots) stops "
        "the run before the click, in Flow's own words. It spends credits and is ledgered, at x1: "
        "8 s by default, omni-flash (the default) 12 credits and veo-lite 10 credits, both measured; veo-fast 20 "
        "credits by Flow's own price table, unmeasured. "
        + _longer_lengths()
        + " Veo 3.1 Lite showed no length choice here (measured); veo-fast stays at 8 s, unmeasured. The live price line is read first and a different price is refused "
        "before the click. dry_run=true returns the quote and the chips, clicks nothing, writes no ledger row, leaves "
        "the composer empty and needs no job_id (each chip's id is the character's entity id or the image's workflow "
        "id). out_dir puts the clip and its ledger in a folder of your own, which keeps one film's takes together; "
        "it must be inside out/, and a job_id is refused when ANY ledger under out/ already holds it, that folder's "
        "included. For a real run,"
        + _JOB_ID_RULE
        + " Flow can refuse a run under its content filters and charges nothing for it: the error then opens with that "
        "and carries Flow's own status, reason and words, for example status 4 with "
        "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED for a character made from a real person's photo (measured "
        "2026-09-17). The filter judges the generated video, so the same character can pass one run and be refused the "
        "next, and a dry run cannot tell in advance; do not retry the same inputs hoping they pass. Allow 3-7 min for a "
        "real run, about 1-2 min for a dry run."
    ),
)
async def gen_character(
    project: str,
    prompt: str,
    characters: list[str] | None = None,
    job_id: str | None = None,
    media_ids: list[str] | None = None,
    model: str = VIDEO_DEFAULT_MODEL,
    aspect: str = "9:16",
    dry_run: bool = False,
    out_dir: str | None = None,
    duration: int = ingredients_mod.SECONDS,
) -> str:
    _require(project, "project")
    _require(prompt, "prompt")
    characters = characters or []
    wanted = [*characters, *(media_ids or [])]
    if not wanted:
        raise ValueError(
            "at least one character or image is required: entity ids from flow_characters, media ids from flow_media"
        )
    if any(not value or not value.strip() for value in wanted):
        raise ValueError("character and media ids must not be blank")
    if len(set(wanted)) != len(wanted):
        raise ValueError(f"each character and image may be named once, got {wanted}")
    if model not in ingredients_mod.PRICES:
        raise ValueError(f"model must be one of {sorted(ingredients_mod.PRICES)}, got {model!r}")
    if aspect not in ingredients_mod.ASPECTS:
        raise ValueError(f"aspect must be one of {sorted(ingredients_mod.ASPECTS)}, got {aspect!r}")
    if duration not in ingredients_mod.LENGTHS[model]:
        raise ValueError(
            f"duration must be one of {list(ingredients_mod.LENGTHS[model])} for {model}, got {duration}"
        )
    if not dry_run:
        _require(job_id or "", "job_id")
    return _json(
        await backend.gen_character(
            project, prompt, characters, media_ids, model, aspect, dry_run, job_id, out_dir, duration
        )
    )


def _video_options() -> str:
    """The models, settings and surveyed prices, written from flow_options.json so the description never drifts."""
    video = video_mod.VIDEO
    parts = []
    for name, entry in video["models"].items():
        prices = entry["price_x1"]
        if entry["durations"]:
            cells = ", ".join(
                f"{r} " + "/".join(f"{d}s {prices[f'{r} {d}s']}" for d in entry["durations"])
                for r in entry["resolutions"]
            )
            parts.append(f"{name} ({entry['label']}): {cells}")
        else:
            parts.append(f"{name} ({entry['label']}): {prices['']} credits, 8 s, 720p")
    return (
        f"Models and x1 prices from Flow's price line on {video_mod.OPTIONS['measured']}: "
        + "; ".join(parts)
        + f". count {video['counts'][0]}-{video['counts'][-1]} multiplies the price; aspect one of {video['aspects']}."
    )


@server.tool(
    name="gen_video",
    description=(
        "One video from Flow's composer with any option it offers; it spends credits and is ledgered. Text alone "
        "runs Frames; start_frame (and end_frame) are project image media ids for the first and last frame; "
        "characters (entity ids) and media_ids (project images) run Ingredients. Frames and ingredients do not mix. "
        + _video_options()
        + " resolution and duration apply to omni-flash only (defaults 720p and 8 s). The money guard is Flow's own "
        "price line, read right before the single click: a real run needs max_credits and is refused when the live "
        "price is over it; dry_run=true reads that price and the settings for free, clicks nothing and needs no job_id "
        "or max_credits. Every setting is read back before the click, and the submit request is checked afterwards "
        "for the mode and length asked: a mismatch is reported as an error even though it was paid. x2-x4 return "
        "every clip in outputs. Flow's Agent mode is turned off for the run and put back after. out_dir must be "
        "inside out/. Allow 3-8 min for a real run, 1-2 min for a dry run." + _BALANCE_MOVED + _JOB_ID_RULE
    ),
)
async def gen_video(
    project: str,
    prompt: str,
    job_id: str | None = None,
    max_credits: int | None = None,
    model: str = VIDEO_DEFAULT_MODEL,
    aspect: str = "9:16",
    resolution: str | None = None,
    duration: int | None = None,
    count: int = 1,
    start_frame: str | None = None,
    end_frame: str | None = None,
    characters: list[str] | None = None,
    media_ids: list[str] | None = None,
    dry_run: bool = False,
    out_dir: str | None = None,
) -> str:
    _require(project, "project")
    _require(prompt, "prompt")
    _one_line(prompt, "prompt")
    wanted = [*(characters or []), *(media_ids or []), *(x for x in (start_frame, end_frame) if x)]
    if any(not value or not value.strip() for value in wanted):
        raise ValueError("frame, character and media ids must not be blank")
    filled_resolution, filled_duration = video_mod.defaults(model, resolution, duration)
    video_mod.check_settings(
        model=model, resolution=filled_resolution, duration=filled_duration, count=count, aspect=aspect
    )
    video_mod.mode_for(
        start_frame=start_frame, end_frame=end_frame, characters=characters or [], media_ids=media_ids or []
    )
    if not dry_run:
        _require(job_id or "", "job_id")
        if max_credits is None:
            raise ValueError("max_credits is required for a real run: the most credits this call may spend")
        if max_credits < 1:
            raise ValueError(f"max_credits must be at least 1, got {max_credits}")
    return _json(
        await backend.gen_video(
            project,
            prompt,
            job_id=job_id,
            max_credits=max_credits,
            model=model,
            aspect=aspect,
            resolution=resolution,
            duration=duration,
            count=count,
            start_frame=start_frame,
            end_frame=end_frame,
            characters=characters or [],
            media_ids=media_ids or [],
            dry_run=dry_run,
            out_dir=out_dir,
        )
    )


@server.tool(
    name="gen_t2i",
    description=(
        "Text to image via gflow: 0 credits with the default nano2 model, but it draws on a daily image quota."
        + _IMAGE_IDS
        + _AGENT_NOTE
    ),
)
async def gen_t2i(
    prompt: str,
    project: str,
    model: str | None = None,
    aspect: str | None = None,
    count: int = 1,
    job_id: str | None = None,
) -> str:
    return await _gen(
        "t2i", prompt=prompt, project=project, model=model, aspect=aspect, count=count, job_id=job_id
    )


@server.tool(
    name="gen_i2i",
    description=(
        "Reference images to image via gflow: 0 credits with the default nano2 model, but it draws on a daily "
        "image quota." + _IMAGE_IDS + _AGENT_NOTE
    ),
)
async def gen_i2i(
    refs: list[str],
    prompt: str,
    project: str,
    model: str | None = None,
    aspect: str | None = None,
    count: int = 1,
    job_id: str | None = None,
) -> str:
    if not refs:
        raise ValueError("refs is required")
    return await _gen(
        "i2i",
        prompt=prompt,
        project=project,
        model=model,
        aspect=aspect,
        count=count,
        refs=refs,
        job_id=job_id,
    )


def run_stdio() -> None:
    asyncio.run(server.run_stdio_async())
