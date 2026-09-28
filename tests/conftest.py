import pytest
from playwright.async_api import BrowserType

from video import offscreen


@pytest.fixture(autouse=True)
def _playwrights_own_launch(monkeypatch):
    """A test that calls the real offscreen.install() would leave Playwright's launch wrapped for every later test."""
    monkeypatch.setattr(BrowserType, "launch_persistent_context", BrowserType.launch_persistent_context)
    monkeypatch.setattr(offscreen, "_installed", offscreen._installed)
