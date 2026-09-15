"""MCP server over stdio: every CLI capability as a tool. One browser session per call; FlowSession's
guard serializes concurrent calls (I4). Generate tools spend credits and are ledgered like the CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from gflow_cli import cli_video
from gflow_cli.api.transports.migrated_composer import R2V_DURATION_S
from gflow_cli.api.video import VideoModel, reference_cap_for, validate_duration_for_model
from gflow_cli.data.redaction import redact_error_detail
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from video import gen as gen_mod
from video.flow import agent as agent_mod
from video.flow import characters as characters_mod
from video.flow import clips as clips_mod
from video.flow import download as download_mod
from video.flow import lane as lane_mod
from video.flow import projects as projects_mod
from video.flow import reader
from video.flow import scenes as scenes_mod
from video.flow import uploads as uploads_mod
from video.session import FlowSession

# Left empty, gflow lets Flow reuse the composer's last model (cli_video.py:185-196), so the price was unknowable.
VIDEO_DEFAULT_MODEL = "omni-flash"
OMNI_FLASH_SECONDS = 10


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
            return await fn(session)

    def _editor_out_dir(self, out_dir: str | None) -> Path:
        """Editor jobs ledger where out_dir points, and job ids are looked up only under the out folder."""
        target = Path(out_dir) if out_dir else self.out_dir
        if not target.resolve().is_relative_to(self.out_dir.resolve()):
            raise ValueError(
                f"out_dir must be inside {self.out_dir.resolve()} (outputs stay in out/), got {target}"
            )
        # A ledger path would make the driver create a folder named ledger.jsonl, which every spend then reads.
        if target.name == "ledger.jsonl" or (target.exists() and not target.is_dir()):
            raise ValueError(f"out_dir must be a folder, not a ledger or another file, got {target}")
        return target

    async def _spend_once(self, job_id: str | None, out_dir: Path, run: Callable[[], Awaitable[Any]]) -> Any:
        """One job per job_id through MCP (DECISIONS 2026-09-15), decided before a browser opens: refused while a call
        with that id still runs in this server, and when any ledger under the out folder, or the one out_dir names,
        holds a row for it, `opening` included."""
        if job_id:
            if job_id in self._running:
                # Not _job_refused: flow_media and flow_credits cannot show a job still in flight, so "start it under a
                # new job_id if it did not run" would pay twice.
                raise gen_mod.AlreadySubmitted(
                    "that job is still running in another call: wait for it to finish, then call again with the SAME "
                    "job_id; never start it under a new job_id, or it pays twice; if it never finishes, stop and tell "
                    f"the owner (job_id {job_id})"
                )
            found = sorted(path for path in self.out_dir.rglob("ledger.jsonl") if path.is_file())
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

    async def media(self, project_id: str, all_versions: bool = False) -> dict[str, Any]:
        # The grid collapses a media to one row, so an Omni edit that stacks a new version onto the same
        # media id is invisible there; `versions` is the only view that shows every version.
        return await self._with(lambda s: reader.project(s, project_id, versions=all_versions))

    async def characters(self, project_id: str) -> list[dict[str, Any]]:
        return await self._with(lambda s: characters_mod.list_characters(s, project_id))

    async def tools(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return await self._with(lambda s: reader.tools(s, project_id))

    async def download(self, project_id: str, media_id: str, out_dir: str | None = None) -> str:
        target = Path(out_dir) if out_dir else self.out_dir
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
        prompt: str,
        name: str | None = None,
        personality: str | None = None,
        wait: float = 90.0,
    ) -> dict[str, Any]:
        return await self._with(
            lambda s: characters_mod.create(
                s, project_id, prompt, name=name, personality=personality, wait=wait
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
        target = Path(out_dir) if out_dir else self.out_dir
        return str(
            await self._with(
                lambda s: clips_mod.download_rendition(
                    s, project_id, media_id, quality, target, workflow_id=workflow_id
                )
            )
        )

    async def clip_reconcile(self, project_id: str, out_dir: str | None = None) -> dict[str, Any]:
        target = Path(out_dir) if out_dir else self.out_dir
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
        return await self._spend_once(
            job_id,
            target,
            lambda: gen_mod.run_job(
                job, target, read_credits=lambda: gen_mod.read_credits_live(self.profile)
            ),
        )


class TellingServer(MCPServer):
    """mcp shows the agent only "Error executing tool <name>" for any exception that is not a ToolError
    (mcp/server/mcpserver/exceptions.py:61-73). Measured 2026-09-15: a WAF stop, a refused job_id and a missing
    project all reached the agent as that same bare line, so the reason is passed on here, scrubbed of secrets."""

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
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
        "recorded in the ledger (out/ledger.jsonl by default): gen_t2v, gen_i2v, gen_r2v, clip_extend, "
        "clip_edit, and agent_send (may spend). clip_download at 4k is a Flow upscale whose cost is "
        "unmeasured: ask the owner first. gen_t2i and gen_i2i are credit-free but draw on a daily image "
        "quota. Check a tool's description for its cost before calling it. Every call drives a real Chrome "
        "session and blocks until Flow answers: a read takes about 15-50 s and a change about 50 s, a generation "
        "2-5 min, clip_extend and clip_edit up to about 7 min, so a slow call is not a failed one. The "
        "credit-spending tools require a job_id. Never call one again under a new job_id because it was slow, "
        "errored or timed out: check flow_media and flow_credits first, and if you do call again keep the same "
        "job_id, which the ledger refuses instead of charging twice. When model is omitted gen_t2v and gen_i2v "
        "use omni-flash for 10 s, and gen_r2v uses omni-flash at 8 s, the only length this host offers it. "
        "If a tool reports that Google flagged unusual activity (WAF), stop: do not retry and "
        "do not re-authenticate; tell the owner. Pass an existing project id from flow_projects, or make one "
        "with project_create."
    ),
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
        "A project's media (id, kind, model, size, url), meta and models, always as one object. "
        "all_versions=true adds a versions list holding every generation record: each Omni edit or upscale "
        "stacks another version onto the SAME media id, and only that list shows them, so it is how you find "
        "the clip an edit produced. Free."
    ),
)
async def flow_media(project_id: str, all_versions: bool = False) -> str:
    _require(project_id, "project_id")
    return _json(await backend.media(project_id, all_versions))


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
    name="flow_download", description="Download one media item to out_dir as <media_id>.<ext>. Free."
)
async def flow_download(project_id: str, media_id: str, out_dir: str | None = None) -> str:
    _require(project_id, "project_id")
    _require(media_id, "media_id")
    return _json({"path": await backend.download(project_id, media_id, out_dir)})


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
        "Create a character from a face prompt (portrait via Nano Banana 2, credit-free), set name and "
        "personality. The reply's portrait.workflow_id is NOT a media id: call flow_characters for the "
        "portrait's media id, which flow_download accepts. Free."
    ),
)
async def character_create(
    project_id: str, prompt: str, name: str | None = None, personality: str | None = None, wait: float = 90.0
) -> str:
    _require(project_id, "project_id")
    _require(prompt, "prompt")
    return _json(await backend.character_create(project_id, prompt, name, personality, wait))


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
    description="Move a scene to the project's trash; scene_restore brings it back. Free.",
)
async def scene_delete(project_id: str, scene_id: str) -> str:
    _require(project_id, "project_id")
    _require(scene_id, "scene_id")
    return _json(await backend.scene_delete(project_id, scene_id))


@server.tool(
    name="scene_restore",
    description=(
        "Bring a trashed scene back from the project's trash (undoes scene_delete), confirmed by the listing. "
        "The trash shows no ids, so it refuses when more than one trashed scene fits the title. Free."
    ),
)
async def scene_restore(project_id: str, scene_id: str) -> str:
    _require(project_id, "project_id")
    _require(scene_id, "scene_id")
    return _json(await backend.scene_restore(project_id, scene_id))


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
        "Send a message to Flow's agent in a project. It may spend credits: 0 credits in 3 measured sends where "
        "the agent generated nothing, but a message that makes it generate media costs that generation's "
        "price. The send itself took 69-80 s in those runs, plus a balance read before and after."
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
        "1080p measured 0 credits. The 4k upscale's cost is unmeasured and it may spend credits (gflow reports "
        "4K upscale as tier-gated): ask the owner before choosing 4k."
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
    name="clip_extend",
    description=(
        "Extend a clip with Veo 3.1 Lite. It spends credits and is ledgered: 10 credits per extend (measured). "
        "Takes about 2-3 min, up to about 7 min when Flow is slow. out_dir, when given, must be inside the out "
        "folder." + _JOB_ID_RULE
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
        "Video-to-video edit of a clip with Omni 1.1 Flash. It spends credits and is ledgered: 20 credits per "
        "edit (measured). Takes about 2-3 min, up to about 7 min when Flow is slow. out_dir, when given, must be "
        "inside the out folder." + _JOB_ID_RULE
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
        "read before and after." + _JOB_ID_RULE
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
        "Image (first frame, optional last frame) to video via gflow. It spends credits and is ledgered; the "
        "price is unmeasured, because every attempt so far failed at Flow's frame picker and spent nothing, so "
        "prefer gen_r2v with the frame as a reference. Uses omni-flash for 10 s when model is omitted. Allow 2-5 "
        "min." + _JOB_ID_RULE
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
        "(measured). It always runs 8 s, the only length this host offers references, so leave duration out. "
        "omni-flash takes up to 7 reference images, veo-lite, veo-fast and veo-lite-lp up to 3, veo-quality none; "
        "more is refused before anything is spent. "
        "Allow 2-5 min: that omni-flash run took 292 s end to end." + _JOB_ID_RULE
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


@server.tool(
    name="gen_t2i",
    description="Text to image via gflow: 0 credits with the default nano2 model, but it draws on a daily image quota.",
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
        "image quota."
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
