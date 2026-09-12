import click


@click.group()
@click.version_option(package_name="video")
def main() -> None:
    """Drive Google Flow (flow.google.com) from the terminal and from agents."""


if __name__ == "__main__":
    main()
