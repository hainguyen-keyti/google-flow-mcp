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


def accept_nano_banana_2_1() -> None:
    """Measured 2026-10-07 (out/i2i_probe): Flow's image menu offers "Nano Banana 2.1" where it offered "Nano Banana
    2", gflow's matcher (contains "Nano Banana 2", not "Lite") picks it, and the submit body names it BELUGA, so gflow
    0.78.0 (0.83.0 too) refused every nano2 image as made with persisted settings. A Nano Banana 2 request whose body
    names BELUGA is that run; every other check gflow makes of the body still holds."""
    from gflow_cli.api.image import Model
    from gflow_cli.api.transports import migrated_composer

    checked = migrated_composer._image_body_problem
    if getattr(checked, "accepts_nano_banana_2_1", False):
        return

    def check(body, reference_ids, model=None):
        if model is Model.NARWHAL and body and "BELUGA" in body:
            return checked(body, reference_ids, None)
        return checked(body, reference_ids, model)

    check.accepts_nano_banana_2_1 = True
    migrated_composer._image_body_problem = check


def run(argv: list[str]) -> None:
    if hides_window(argv[1:]):
        offscreen.install()
    accept_nano_banana_2_1()
    argv[0] = "gflow"
    main()


if __name__ == "__main__":
    run(sys.argv)
