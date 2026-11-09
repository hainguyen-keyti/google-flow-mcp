import os

os.environ.setdefault("GFLOW_CLI_LOG_LEVEL", "WARNING")
# gflow 0.95.0 counts and reports usage weekly unless this is off; the owner turned it off (2026-11-09).
os.environ["GFLOW_CLI_TELEMETRY"] = "0"

from gflow_cli.observability import configure_logging

from video import keychain

keychain.apply()
configure_logging()
