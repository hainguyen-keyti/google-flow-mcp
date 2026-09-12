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


if __name__ == "__main__":
    main()
