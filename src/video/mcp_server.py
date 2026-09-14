"""MCP server over stdio: every CLI capability as a tool. One browser session per call; FlowSession's
guard serializes concurrent calls (I4). Generate tools spend credits and are ledgered like the CLI."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

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


class Backend:
    def __init__(self, profile: str = "default", out_dir: Path = Path("out")) -> None:
        self.profile = profile
        self.out_dir = out_dir

    async def _with(self, fn: Callable[[FlowSession], Awaitable[Any]]) -> Any:
        async with FlowSession(self.profile) as session:
            return await fn(session)

    async def lane(self) -> dict[str, Any]:
        return await self._with(lane_mod.run)

    async def projects(self) -> list[dict[str, Any]]:
        return await self._with(reader.projects)

    async def credits(self) -> dict[str, Any]:
        return await self._with(reader.credits)

    async def media(self, project_id: str, all_versions: bool = False) -> Any:
        # The grid collapses a media to one row, so an Omni edit that stacks a new version onto the same
        # media id is invisible there. `reader.records` is the only view that shows every version.
        if all_versions:
            return await self._with(lambda s: reader.records(s, project_id))
        return await self._with(lambda s: reader.project(s, project_id))

    async def characters(self, project_id: str) -> list[dict[str, Any]]:
        return await self._with(lambda s: characters_mod.list_characters(s, project_id))

    async def tools(self, project_id: str) -> list[dict[str, Any]]:
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

    async def agent_mode(self, project_id: str, enabled: bool) -> dict[str, Any]:
        return await self._with(lambda s: agent_mod.set_mode(s, project_id, enabled))

    async def agent_send(self, project_id: str, message: str, wait: float = 60.0) -> dict[str, Any]:
        return await self._with(lambda s: agent_mod.send(s, project_id, message, wait=wait))

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

    async def clip_reconcile(self, project_id: str, out_dir: str | None = None) -> list[dict[str, Any]]:
        target = Path(out_dir) if out_dir else self.out_dir
        return await self._with(lambda s: clips_mod.reconcile_editor(s, project_id, out_dir=target))

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
        target = Path(out_dir) if out_dir else self.out_dir
        return await self._with(
            lambda s: clips_mod.extend(
                s, project_id, media_id, prompt, out_dir=target, job_id=job_id, wait=wait
            )
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
        target = Path(out_dir) if out_dir else self.out_dir
        return await self._with(
            lambda s: clips_mod.edit(
                s, project_id, media_id, prompt, out_dir=target, job_id=job_id, wait=wait
            )
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
        return await gen_mod.run_job(
            job, target, read_credits=lambda: gen_mod.read_credits_live(self.profile)
        )


backend = Backend()
server = MCPServer(
    "video",
    instructions=(
        "Google Flow (flow.google.com) control for this account. These tools spend Flow credits and are "
        "recorded in the ledger (out/ledger.jsonl by default): gen_t2v, gen_i2v, gen_r2v, clip_extend, "
        "clip_edit, and agent_send (may spend). clip_download at 4k is a Flow upscale whose cost is "
        "unmeasured: ask the owner first. gen_t2i and gen_i2i are credit-free but draw on a daily image "
        "quota. Check a tool's description for its cost before calling it. If a tool reports that Google "
        "flagged unusual activity (WAF), stop: do not retry and do not re-authenticate; tell the owner. "
        "Pass an existing project id from flow_projects, or make one with project_create."
    ),
)


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _require(value: str, name: str) -> None:
    if not value:
        raise ValueError(f"{name} is required")


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
        "A project's media (id, kind, model, size, url), meta and models. Free. Set all_versions=true "
        "for every generation record instead: each Omni edit or upscale stacks another version onto the "
        "SAME media id, and only this view shows them, so it is how you find the clip an edit produced."
    ),
)
async def flow_media(project_id: str, all_versions: bool = False) -> str:
    _require(project_id, "project_id")
    return _json(await backend.media(project_id, all_versions))


@server.tool(name="flow_characters", description="A project's characters (entity_id, name, portrait). Free.")
async def flow_characters(project_id: str) -> str:
    _require(project_id, "project_id")
    return _json(await backend.characters(project_id))


@server.tool(name="flow_tools", description="The community Tools gallery (id, name, author, tags). Free.")
async def flow_tools(project_id: str) -> str:
    _require(project_id, "project_id")
    return _json(await backend.tools(project_id))


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


@server.tool(name="project_rename", description="Rename a project. Free.")
async def project_rename(project_id: str, title: str) -> str:
    _require(project_id, "project_id")
    _require(title, "title")
    return _json({"title": await backend.project_rename(project_id, title)})


@server.tool(
    name="project_delete", description="Delete a project permanently (clips, ingredients, prompts). Free."
)
async def project_delete(project_id: str) -> str:
    _require(project_id, "project_id")
    return _json(await backend.project_delete(project_id))


@server.tool(
    name="character_create",
    description="Create a character from a face prompt (portrait via Nano Banana 2, credit-free), set name and personality.",
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


@server.tool(name="scene_delete", description="Move a scene to the project's trash. Free.")
async def scene_delete(project_id: str, scene_id: str) -> str:
    _require(project_id, "project_id")
    _require(scene_id, "scene_id")
    return _json(await backend.scene_delete(project_id, scene_id))


@server.tool(
    name="agent_mode",
    description="Turn Flow's agent mode on or off for a project; leaving it on hides the composer settings. Free.",
)
async def agent_mode(project_id: str, enabled: bool) -> str:
    _require(project_id, "project_id")
    return _json(await backend.agent_mode(project_id, enabled))


@server.tool(
    name="agent_send", description="Send a message to Flow's agent in a project (may spend credits)."
)
async def agent_send(project_id: str, message: str, wait: float = 60.0) -> str:
    _require(project_id, "project_id")
    _require(message, "message")
    return _json(await backend.agent_send(project_id, message, wait))


@server.tool(
    name="clip_download",
    description=(
        "Download a clip rendition from the editor: gif (270p), 720p, 1080p or 4k (upscaled by Flow). "
        "Defaults to the NEWEST finished version of the media; pass workflow_id (from flow_media with "
        "all_versions=true) to fetch one specific version, such as the clip a particular edit produced. "
        "The 4k upscale's cost is unmeasured and it may spend credits (gflow reports 4K upscale as "
        "tier-gated): ask the owner before choosing 4k."
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
        "and the balance. Free: it reads and writes the ledger, it never generates. Run this after a "
        "clip_extend or clip_edit died mid-flight, otherwise the spend has no outcome against it."
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


@server.tool(name="clip_extend", description="Extend a clip with Veo 3.1 Lite (spends credits, ledgered).")
async def clip_extend(
    project_id: str, media_id: str, prompt: str, job_id: str | None = None, out_dir: str | None = None
) -> str:
    _require(project_id, "project_id")
    _require(media_id, "media_id")
    _require(prompt, "prompt")
    return _json(await backend.clip_extend(project_id, media_id, prompt, job_id, out_dir))


@server.tool(
    name="clip_edit",
    description="Video-to-video edit of a clip with Omni 1.1 Flash (spends credits, ledgered).",
)
async def clip_edit(
    project_id: str, media_id: str, prompt: str, job_id: str | None = None, out_dir: str | None = None
) -> str:
    _require(project_id, "project_id")
    _require(media_id, "media_id")
    _require(prompt, "prompt")
    return _json(await backend.clip_edit(project_id, media_id, prompt, job_id, out_dir))


async def _gen(kind: str, **kwargs: Any) -> str:
    _require(kwargs.get("project", ""), "project")
    _require(kwargs.get("prompt", ""), "prompt")
    return _json(await backend.generate(kind=kind, **kwargs))


@server.tool(name="gen_t2v", description="Text to video via gflow (spends credits; veo-lite 720p 8s = 10).")
async def gen_t2v(
    prompt: str,
    project: str,
    model: str | None = None,
    aspect: str | None = None,
    count: int = 1,
    duration: int | None = None,
    job_id: str | None = None,
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
    name="gen_i2v", description="Image (first frame, optional last frame) to video (spends credits)."
)
async def gen_i2v(
    initial_frame: str,
    prompt: str,
    project: str,
    end_frame: str | None = None,
    model: str | None = None,
    aspect: str | None = None,
    duration: int | None = None,
    job_id: str | None = None,
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


@server.tool(name="gen_r2v", description="Reference images (ingredients) to video (spends credits).")
async def gen_r2v(
    refs: list[str],
    prompt: str,
    project: str,
    model: str | None = None,
    aspect: str | None = None,
    duration: int | None = None,
    job_id: str | None = None,
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


@server.tool(name="gen_t2i", description="Text to image via gflow (credit-free, daily quota).")
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


@server.tool(name="gen_i2i", description="Reference images to image via gflow (credit-free, daily quota).")
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
