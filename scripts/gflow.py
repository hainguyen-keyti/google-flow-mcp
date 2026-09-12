"""gflow launcher that skips the macOS Keychain cookie pre-read.

browser_cookie3 asks Keychain for the Chrome Safe Storage key on every run; this
profile runs with --password-store=basic, so that key never decrypts anything and
the Playwright fallback is what always ran. Fail the read up front, same path.

    uv tool run --from gflow-cli python scripts/gflow.py video t2v "..." --project <id>
"""

import sys

import browser_cookie3


def _skip_keychain(*args, **kwargs):
    sys.stderr.write("[scripts/gflow.py] keychain pre-read skipped\n")
    raise browser_cookie3.BrowserCookieError("keychain pre-read skipped by scripts/gflow.py")


browser_cookie3.chrome = _skip_keychain
browser_cookie3.load = _skip_keychain

from gflow_cli.cli import main  # noqa: E402

sys.argv[0] = "gflow"
main()
