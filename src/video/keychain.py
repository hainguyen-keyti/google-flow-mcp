"""Disable browser_cookie3's Keychain read: this profile uses --password-store=basic, so
the Keychain key never decrypts anything and gflow's Playwright fallback is what runs."""

import sys

import browser_cookie3

_MARK = "_video_keychain_patched"


def _skip(*args, **kwargs):
    sys.stderr.write("[video] keychain pre-read skipped\n")
    raise browser_cookie3.BrowserCookieError("keychain pre-read skipped by video.keychain")


def apply() -> None:
    if getattr(browser_cookie3, _MARK, False):
        return
    browser_cookie3.chrome = _skip
    browser_cookie3.load = _skip
    setattr(browser_cookie3, _MARK, True)
