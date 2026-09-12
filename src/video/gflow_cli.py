"""Run gflow inside this venv with the Keychain patch applied.

python -m video.gflow_cli video t2v "..." --project <id> --json
"""

import sys

from gflow_cli.cli import main

import video  # noqa: F401

if __name__ == "__main__":
    sys.argv[0] = "gflow"
    main()
