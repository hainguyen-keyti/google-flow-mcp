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
def media(project_id: str, profile: str, as_json: bool) -> None:
    """List a project's media with kind, model, size and download URL ($0)."""
    from video.flow import reader

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
        click.echo(f"{row['id']}  {row['name']}")
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


if __name__ == "__main__":
    main()
