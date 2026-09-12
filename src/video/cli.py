import asyncio
import json
import os

import click


@click.group()
@click.version_option(package_name="video")
def main() -> None:
    """Drive Google Flow (flow.google.com) from the terminal and from agents."""
    os.environ.setdefault("GFLOW_CLI_LOG_LEVEL", "WARNING")


@main.group()
def flow() -> None:
    """Read Flow state on the migrated host."""


@flow.command()
@click.option("--profile", default="default", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit the full probe result as JSON.")
def lane(profile: str, as_json: bool) -> None:
    """Report which lane the profile is on: LABS, MIGRATED, or SIGNED_OUT ($0)."""
    from video.flow import lane as lane_mod
    from video.session import FlowSession

    async def run() -> dict:
        async with FlowSession(profile) as session:
            return await lane_mod.run(session)

    result = asyncio.run(run())
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(f"{result['verdict']} projects={result['projects']}")


def _read(profile: str, fn):
    from video.session import FlowSession

    async def run():
        async with FlowSession(profile) as session:
            return await fn(session)

    return asyncio.run(run())


@flow.command()
@click.option("--profile", default="default", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def projects(profile: str, as_json: bool) -> None:
    """List the account's projects ($0)."""
    from video.flow import reader

    rows = _read(profile, reader.projects)
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    for row in rows:
        click.echo(f"{row['id']}  {row['title']}")
    click.echo(f"projects={len(rows)}")


@flow.command()
@click.option("--profile", default="default", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def credits(profile: str, as_json: bool) -> None:
    """Show the credit balance ($0)."""
    from video.flow import reader

    info = _read(profile, reader.credits)
    click.echo(json.dumps(info) if as_json else str(info["balance"]))


@flow.command()
@click.argument("project_id")
@click.option("--profile", default="default", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option(
    "--all",
    "every_record",
    is_flag=True,
    help="Every generation record, including clips inside scenes and portrait candidates the grid hides.",
)
def media(project_id: str, profile: str, as_json: bool, every_record: bool) -> None:
    """List a project's media with kind, model, size and download URL ($0)."""
    from video.flow import reader

    if every_record:
        rows = _read(profile, lambda s: reader.records(s, project_id))
        if as_json:
            click.echo(json.dumps(rows, indent=2))
            return
        for r in rows:
            flag = "listed" if r["listed"] else "unlisted"
            click.echo(
                f"{r['id']}  {r['kind'] or '?':5s} {flag:8s} {r['model'] or '-':40s} {r['size_bytes'] or '-'}"
            )
        click.echo(f"records={len(rows)}")
        return
    info = _read(profile, lambda s: reader.project(s, project_id))
    if as_json:
        click.echo(json.dumps(info, indent=2))
        return
    click.echo(f"project {info['meta']['id']}  {info['meta']['title']}  models={','.join(info['models'])}")
    for m in info["media"]:
        click.echo(
            f"{m['id']}  {m['kind'] or '?':5s} {m['model'] or '-':40s} {m['size_bytes'] or '-'}  {m['title'] or ''}"
        )
    click.echo(f"media={len(info['media'])}")


@flow.command()
@click.argument("project_id")
@click.option("--profile", default="default", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def characters(project_id: str, profile: str, as_json: bool) -> None:
    """List a project's characters ($0)."""
    from video.flow import reader

    rows = _read(profile, lambda s: reader.characters(s, project_id))
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    for row in rows:
        click.echo(f"{row['entity_id']}  {row['name']}")
    click.echo(f"characters={len(rows)}")


@flow.command()
@click.argument("project_id")
@click.option("--profile", default="default", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def tools(project_id: str, profile: str, as_json: bool) -> None:
    """List the community Tools gallery as served on a project page ($0)."""
    from video.flow import reader

    rows = _read(profile, lambda s: reader.tools(s, project_id))
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    for row in rows:
        click.echo(f"{row['id']}  {row['name']}  by {row['author'] or '-'}  {','.join(row['tags'])}")
    click.echo(f"tools={len(rows)}")


@flow.command()
@click.argument("project_id")
@click.argument("media_id")
@click.option("--out", "out_dir", default="out", show_default=True, type=click.Path(file_okay=False))
@click.option("--profile", default="default", show_default=True)
def download(project_id: str, media_id: str, out_dir: str, profile: str) -> None:
    """Download one media item into OUT as <media_id>.<ext>; never overwrites ($0)."""
    from pathlib import Path

    from video.flow import download as download_mod

    path = _read(profile, lambda s: download_mod.download(s, project_id, media_id, Path(out_dir)))
    click.echo(str(path))


@flow.group()
def project() -> None:
    """Create, rename, delete projects on the grid ($0)."""


@project.command("create")
@click.option("--title", default=None, help="Rename right after creation.")
@click.option("--profile", default="default", show_default=True)
def project_create(title: str | None, profile: str) -> None:
    from video.flow import projects

    click.echo(json.dumps(_read(profile, lambda s: projects.create(s, title))))


@project.command("rename")
@click.argument("project_id")
@click.argument("title")
@click.option("--profile", default="default", show_default=True)
def project_rename(project_id: str, title: str, profile: str) -> None:
    from video.flow import projects

    click.echo(_read(profile, lambda s: projects.rename(s, project_id, title)))


@project.command("delete")
@click.argument("project_id")
@click.option("--yes", is_flag=True, help="Required: deleting is permanent (clips, ingredients, prompts).")
@click.option("--profile", default="default", show_default=True)
def project_delete(project_id: str, yes: bool, profile: str) -> None:
    from video.flow import projects

    if not yes:
        raise click.UsageError(
            "refusing to delete without --yes; Flow deletes clips, ingredients and prompts permanently"
        )
    click.echo(json.dumps(_read(profile, lambda s: projects.delete(s, project_id))))


@flow.command()
@click.argument("project_id")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--profile", default="default", show_default=True)
def upload(project_id: str, file: str, profile: str) -> None:
    """Upload a local image or video into a project ($0)."""
    from pathlib import Path

    from video.flow import uploads

    click.echo(json.dumps(_read(profile, lambda s: uploads.upload(s, project_id, Path(file)))))


@flow.command()
@click.argument("project_id")
@click.option("--profile", default="default", show_default=True)
def uploads(project_id: str, profile: str) -> None:
    """Show the Uploads view of a project: rpcids, item count, tiles ($0)."""
    from video.flow import uploads as uploads_mod

    click.echo(json.dumps(_read(profile, lambda s: uploads_mod.list_uploads(s, project_id))))


@flow.group()
def character() -> None:
    """Create, rename, describe and delete characters (portrait generation is credit-free)."""


@character.command("create")
@click.argument("project_id")
@click.argument("prompt")
@click.option("--name", default=None)
@click.option("--personality", default=None)
@click.option("--wait", default=90.0, show_default=True, type=float, help="Seconds to wait for the portrait.")
@click.option("--profile", default="default", show_default=True)
def character_create(
    project_id: str, prompt: str, name: str | None, personality: str | None, wait: float, profile: str
) -> None:
    from video.flow import characters as characters_mod

    result = _read(
        profile,
        lambda s: characters_mod.create(s, project_id, prompt, name=name, personality=personality, wait=wait),
    )
    click.echo(json.dumps(result))


@character.command("list")
@click.argument("project_id")
@click.option("--profile", default="default", show_default=True)
def character_list(project_id: str, profile: str) -> None:
    from video.flow import characters as characters_mod

    click.echo(json.dumps(_read(profile, lambda s: characters_mod.list_characters(s, project_id))))


@character.command("delete")
@click.argument("project_id")
@click.argument("entity_id")
@click.option("--yes", is_flag=True, help="Required: deleting a character is permanent.")
@click.option("--profile", default="default", show_default=True)
def character_delete(project_id: str, entity_id: str, yes: bool, profile: str) -> None:
    from video.flow import characters as characters_mod

    if not yes:
        raise click.UsageError("refusing to delete without --yes")
    click.echo(json.dumps(_read(profile, lambda s: characters_mod.delete(s, project_id, entity_id))))


@flow.group()
def scene() -> None:
    """Create and trash scenes (Scenebuilder) ($0)."""


@scene.command("list")
@click.argument("project_id")
@click.option("--all", "include_trashed", is_flag=True, help="Include scenes already moved to the trash.")
@click.option("--profile", default="default", show_default=True)
def scene_list(project_id: str, include_trashed: bool, profile: str) -> None:
    from video.flow import scenes

    click.echo(
        json.dumps(
            _read(profile, lambda s: scenes.list_scenes(s, project_id, include_trashed=include_trashed))
        )
    )


@scene.command("create")
@click.argument("project_id")
@click.option("--title", default=None)
@click.option("--profile", default="default", show_default=True)
def scene_create(project_id: str, title: str | None, profile: str) -> None:
    from video.flow import scenes

    click.echo(json.dumps(_read(profile, lambda s: scenes.create(s, project_id, title))))


@scene.command("delete")
@click.argument("project_id")
@click.argument("scene_id")
@click.option("--yes", is_flag=True)
@click.option("--profile", default="default", show_default=True)
def scene_delete(project_id: str, scene_id: str, yes: bool, profile: str) -> None:
    from video.flow import scenes

    if not yes:
        raise click.UsageError("refusing to trash a scene without --yes")
    click.echo(json.dumps(_read(profile, lambda s: scenes.delete(s, project_id, scene_id))))


@flow.group()
def clip() -> None:
    """Per-clip actions in the clip editor: renditions (upscale), extend, Omni edit."""


@clip.command("download")
@click.argument("project_id")
@click.argument("media_id")
@click.option(
    "--quality", default="1080p", show_default=True, type=click.Choice(["gif", "720p", "1080p", "4k"])
)
@click.option("--out", "out_dir", default="out", show_default=True, type=click.Path(file_okay=False))
@click.option("--profile", default="default", show_default=True)
def clip_download(project_id: str, media_id: str, quality: str, out_dir: str, profile: str) -> None:
    """Download a rendition from 'Download media' (1080p and 4K are upscaled by Flow)."""
    from pathlib import Path

    from video.flow import clips

    path = _read(profile, lambda s: clips.download_rendition(s, project_id, media_id, quality, Path(out_dir)))
    click.echo(str(path))


@clip.command("extend")
@click.argument("project_id")
@click.argument("media_id")
@click.argument("prompt")
@click.option("--out", "out_dir", default="out", show_default=True, type=click.Path(file_okay=False))
@click.option("--job", "job_id", default=None)
@click.option("--wait", default=240.0, show_default=True, type=float)
@click.option("--profile", default="default", show_default=True)
def clip_extend(
    project_id: str, media_id: str, prompt: str, out_dir: str, job_id: str | None, wait: float, profile: str
) -> None:
    """Extend a clip (Veo 3.1 Lite); spends credits, ledgered."""
    from pathlib import Path

    from video.flow import clips

    result = _read(
        profile,
        lambda s: clips.extend(
            s, project_id, media_id, prompt, out_dir=Path(out_dir), job_id=job_id, wait=wait
        ),
    )
    click.echo(json.dumps(result))


@clip.command("edit")
@click.argument("project_id")
@click.argument("media_id")
@click.argument("prompt")
@click.option("--out", "out_dir", default="out", show_default=True, type=click.Path(file_okay=False))
@click.option("--job", "job_id", default=None)
@click.option("--wait", default=240.0, show_default=True, type=float)
@click.option("--profile", default="default", show_default=True)
def clip_edit(
    project_id: str, media_id: str, prompt: str, out_dir: str, job_id: str | None, wait: float, profile: str
) -> None:
    """Video-to-video edit with Omni 1.1 Flash; spends credits, ledgered."""
    from pathlib import Path

    from video.flow import clips

    result = _read(
        profile,
        lambda s: clips.edit(
            s, project_id, media_id, prompt, out_dir=Path(out_dir), job_id=job_id, wait=wait
        ),
    )
    click.echo(json.dumps(result))


@flow.group()
def agent() -> None:
    """Flow Agent mode: toggle, or send a message (the agent may generate, spending credits)."""


@agent.command("mode")
@click.argument("project_id")
@click.argument("state", type=click.Choice(["on", "off"]))
@click.option("--profile", default="default", show_default=True)
def agent_mode(project_id: str, state: str, profile: str) -> None:
    from video.flow import agent as agent_mod

    click.echo(json.dumps(_read(profile, lambda s: agent_mod.set_mode(s, project_id, state == "on"))))


@agent.command("send")
@click.argument("project_id")
@click.argument("message")
@click.option("--wait", default=60.0, show_default=True, type=float)
@click.option("--profile", default="default", show_default=True)
def agent_send(project_id: str, message: str, wait: float, profile: str) -> None:
    from video.flow import agent as agent_mod

    click.echo(json.dumps(_read(profile, lambda s: agent_mod.send(s, project_id, message, wait))))


@main.group()
def story() -> None:
    """The try-on selling video: shot list built on the owner's character bible."""


@story.command("plan")
@click.option("--json", "as_json", is_flag=True, help="Emit the whole plan as JSON.")
def story_plan(as_json: bool) -> None:
    """Print the shot list with its locked prompts ($0, never opens Flow)."""
    from video.story import shots

    rows = shots.plan()
    if as_json:
        click.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    for row in rows:
        click.echo(f"{row['job_id']}  {row['key']:9s} {row['beat']}")
        click.echo(f"    outfit: {row['outfit']}")
    click.echo(f"shots={len(rows)} aspect={shots.ASPECT} model={shots.MODEL} duration={shots.DURATION}s")


@story.command("run")
@click.argument("project_id")
@click.option("--out", "out_dir", default="out/story", show_default=True, type=click.Path(file_okay=False))
@click.option("--only", default=None, help="Comma-separated shot keys, e.g. 'hook,tryon'.")
@click.option("--wait", default=300.0, show_default=True, type=float)
@click.option("--price", default=10, show_default=True, help="Credits each shot may cost; a mismatch aborts.")
@click.option("--profile", default="default", show_default=True)
def story_run(project_id: str, out_dir: str, only: str | None, wait: float, price: int, profile: str) -> None:
    """Generate the try-on shots with the character attached (SPENDS CREDITS, ledgered)."""
    from pathlib import Path

    from video.story import pipeline

    keys = [k.strip() for k in only.split(",")] if only else None
    result = _read(
        profile,
        lambda s: pipeline.run(s, project_id, out_dir=Path(out_dir), only=keys, wait=wait, price=price),
    )
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


@story.command("reconcile")
@click.argument("project_id")
@click.option("--out", "out_dir", default="out/story", show_default=True, type=click.Path(file_okay=False))
@click.option("--profile", default="default", show_default=True)
def story_reconcile(project_id: str, out_dir: str, profile: str) -> None:
    """Close out shots stuck on 'submitted' by checking the listing and the balance ($0)."""
    from pathlib import Path

    from video.story import pipeline

    rows = _read(profile, lambda s: pipeline.reconcile(s, project_id, out_dir=Path(out_dir)))
    click.echo(json.dumps(rows, indent=2, ensure_ascii=False))


@main.group()
def mcp() -> None:
    """Model Context Protocol server exposing every command above."""


@mcp.command("run")
def mcp_run() -> None:
    """Serve over stdio (for Claude Code .mcp.json, Claude Desktop, Cursor)."""
    from video import mcp_server

    mcp_server.run_stdio()


@main.group()
def gen() -> None:
    """Generate on Flow through gflow; every job is ledgered in OUT/ledger.jsonl (spends credits)."""


def _gen_options(fn):
    for option in reversed(
        [
            click.option("--project", required=True, help="Existing Flow project id."),
            click.option(
                "--model",
                default=None,
                help="gflow model alias (veo-lite, veo-fast, veo-quality, omni-flash, nano2, nano-pro).",
            ),
            click.option("--aspect", default=None, help="9:16 or 16:9 (images also 1:1, 4:3)."),
            click.option("--count", default=1, show_default=True, type=int),
            click.option("--duration", default=None, type=int, help="4, 6, 8 (10 on omni-flash)."),
            click.option(
                "--out", "out_dir", default="out", show_default=True, type=click.Path(file_okay=False)
            ),
            click.option(
                "--job",
                "job_id",
                default=None,
                help="Idempotency key; a job id with a submitted row is refused.",
            ),
            click.option("--profile", default="default", show_default=True),
        ]
    ):
        fn = option(fn)
    return fn


def _run_gen(kind: str, prompt: str, opts: dict, **extra) -> None:
    import uuid
    from pathlib import Path

    from video import gen as gen_mod

    job = gen_mod.Job(
        job_id=opts["job_id"] or str(uuid.uuid4()),
        kind=kind,
        prompt=prompt,
        project=opts["project"],
        model=opts["model"],
        aspect=opts["aspect"],
        count=opts["count"],
        duration=opts["duration"],
        **extra,
    )

    async def run():
        return await gen_mod.run_job(
            job, Path(opts["out_dir"]), read_credits=lambda: gen_mod.read_credits_live(opts["profile"])
        )

    click.echo(json.dumps(asyncio.run(run()), indent=2))


@gen.command()
@click.argument("prompt")
@_gen_options
def t2v(prompt: str, **opts) -> None:
    """Text to video."""
    _run_gen("t2v", prompt, opts)


@gen.command()
@click.argument("initial_frame", type=click.Path(exists=True, dir_okay=False))
@click.argument("prompt")
@click.option("--end-frame", default=None, type=click.Path(exists=True, dir_okay=False))
@_gen_options
def i2v(initial_frame: str, prompt: str, end_frame: str | None, **opts) -> None:
    """Image (first frame, optional last frame) to video."""
    from pathlib import Path

    _run_gen(
        "i2v",
        prompt,
        opts,
        initial_frame=Path(initial_frame),
        end_frame=Path(end_frame) if end_frame else None,
    )


@gen.command()
@click.argument("prompt")
@click.option("--ref", "refs", multiple=True, required=True, type=click.Path(exists=True, dir_okay=False))
@_gen_options
def r2v(prompt: str, refs: tuple[str, ...], **opts) -> None:
    """Reference images (ingredients) to video."""
    from pathlib import Path

    _run_gen("r2v", prompt, opts, refs=[Path(r) for r in refs])


@gen.command()
@click.argument("prompt")
@_gen_options
def t2i(prompt: str, **opts) -> None:
    """Text to image."""
    _run_gen("t2i", prompt, opts)


@gen.command()
@click.argument("prompt")
@click.option("--ref", "refs", multiple=True, required=True, type=click.Path(exists=True, dir_okay=False))
@_gen_options
def i2i(prompt: str, refs: tuple[str, ...], **opts) -> None:
    """Reference images to image."""
    from pathlib import Path

    _run_gen("i2i", prompt, opts, refs=[Path(r) for r in refs])


if __name__ == "__main__":
    main()
