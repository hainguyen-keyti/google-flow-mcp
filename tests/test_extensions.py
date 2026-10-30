"""Opt-in: keep the profile's browser extensions on in the Chrome this repo launches (a VPN extension in profile_acc2).

Playwright adds --disable-extensions to every launch unless it is listed in ignore_default_args.
"""

import asyncio

import pytest
from playwright.async_api import BrowserType

from video import extensions

FLAG = "--disable-extensions"


@pytest.fixture
def launch(monkeypatch):
    seen = []

    async def original(self, user_data_dir, **kwargs):
        seen.append(kwargs)
        return object()

    monkeypatch.setattr(BrowserType, "launch_persistent_context", original)
    monkeypatch.setattr(extensions, "_installed", False)
    monkeypatch.delenv(extensions.ENV, raising=False)
    return seen


def _launch(**kwargs):
    return asyncio.run(BrowserType.launch_persistent_context(object(), "/tmp/profile", **kwargs))


def test_opted_in_launch_also_ignores_disable_extensions_and_keeps_the_stealth_flags(launch, monkeypatch):
    monkeypatch.setenv(extensions.ENV, "1")
    extensions.install()

    _launch(ignore_default_args=["--enable-automation", "--no-sandbox"], headless=False)

    assert launch[0]["ignore_default_args"] == ["--enable-automation", "--no-sandbox", FLAG]
    assert launch[0]["headless"] is False


def test_opted_in_launch_without_ignore_default_args_gets_just_the_flag(launch, monkeypatch):
    monkeypatch.setenv(extensions.ENV, "1")
    extensions.install()

    _launch()

    assert launch[0]["ignore_default_args"] == [FLAG]


def test_ignore_default_args_true_is_left_alone(launch, monkeypatch):
    monkeypatch.setenv(extensions.ENV, "1")
    extensions.install()

    _launch(ignore_default_args=True)

    assert launch[0]["ignore_default_args"] is True


@pytest.mark.parametrize("value", [None, "0", ""])
def test_not_opted_in_nothing_is_wrapped(launch, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv(extensions.ENV, value)
    before = BrowserType.launch_persistent_context
    extensions.install()

    _launch(ignore_default_args=["--enable-automation"])

    assert BrowserType.launch_persistent_context is before
    assert launch[0]["ignore_default_args"] == ["--enable-automation"]
