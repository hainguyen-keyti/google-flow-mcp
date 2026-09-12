import os

os.environ.setdefault("GFLOW_CLI_LOG_LEVEL", "WARNING")

from gflow_cli.observability import configure_logging

from video import keychain

keychain.apply()
configure_logging()
