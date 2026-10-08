"""Run gflow inside this venv with the Keychain patch applied.

python -m video.gflow_cli video t2v "..." --project <id> --json
"""

import re
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
        # The quoted model field opens the request's inner array (`[\"BELUGA\",\"<reference>\"...`); a prompt that
        # mentions a beluga is quoted elsewhere and must not pass for it (review 2026-10-08, D8).
        if model is Model.NARWHAL and body and re.search(r'\[\\*"BELUGA\\*"', body):
            return checked(body, reference_ids, None)
        return checked(body, reference_ids, model)

    check.accepts_nano_banana_2_1 = True
    migrated_composer._image_body_problem = check


def accept_generation_record_null_version() -> None:
    """Measured 2026-10-07: Flow's submit replies (maseQ, YhhmEf) carry null in record[3] where "CAE" stood, and
    gflow's batchexecute._is_record, which wants exactly "CAE", refused every reply as holding no generation record
    (two paid runs on 2026-10-06, one of them charged). A record is one with "CAE" or null there and nothing else:
    "CAI" and "CAM" are a clip's edit versions (measured 2026-09-28), and gflow keeps the first record it finds, so an
    edit record ahead of the job's own would stand in for the job."""
    from gflow_cli.api.transports import batchexecute

    checked = batchexecute._is_record
    if getattr(checked, "accepts_null_version", False):
        return

    def check(node):
        if not isinstance(node, list) or len(node) < 6 or node[3] not in ("CAE", None):
            return False
        return all(isinstance(node[i], str) and batchexecute._UUID_RE.match(node[i]) for i in (0, 1, 2))

    check.accepts_null_version = True
    batchexecute._is_record = check


def run(argv: list[str]) -> None:
    if hides_window(argv[1:]):
        offscreen.install()
    accept_nano_banana_2_1()
    accept_generation_record_null_version()
    argv[0] = "gflow"
    main()


if __name__ == "__main__":
    run(sys.argv)
