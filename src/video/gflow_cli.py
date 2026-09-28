"""Run gflow inside this venv with the Keychain patch applied.

python -m video.gflow_cli video t2v "..." --project <id> --json
"""

import sys

from gflow_cli.cli import main

import video  # noqa: F401
from video import offscreen


def hides_window(argv: list[str]) -> bool:
    """Every gflow command this repo runs, except sign-in: a hidden login window is a login nobody can finish."""
    return bool(argv) and argv[0] != "auth"


if __name__ == "__main__":
    if hides_window(sys.argv[1:]):
        offscreen.install()
    sys.argv[0] = "gflow"
    main()
