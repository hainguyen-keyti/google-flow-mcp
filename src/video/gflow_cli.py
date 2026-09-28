"""Run gflow inside this venv with the Keychain patch applied.

python -m video.gflow_cli video t2v "..." --project <id> --json
"""

import sys

from gflow_cli.cli import main

import video  # noqa: F401
from video import offscreen


def hides_window(argv: list[str]) -> bool:
    """Every gflow command this repo runs, except sign-in: a hidden login window is a login nobody can finish. The
    command is the first argument that is not a flag, since gflow's root takes -v and -V before it (review, F2)."""
    command = next((arg for arg in argv if not arg.startswith("-")), None)
    return command is not None and command != "auth"


def run(argv: list[str]) -> None:
    if hides_window(argv[1:]):
        offscreen.install()
    argv[0] = "gflow"
    main()


if __name__ == "__main__":
    run(sys.argv)
