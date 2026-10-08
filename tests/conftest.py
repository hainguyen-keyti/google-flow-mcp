import pytest
from playwright.async_api import BrowserType

from video import mcp_server, offscreen


@pytest.fixture(autouse=True)
def _playwrights_own_launch(monkeypatch):
    """A test that calls the real offscreen.install() would leave Playwright's launch wrapped for every later test."""
    monkeypatch.setattr(BrowserType, "launch_persistent_context", BrowserType.launch_persistent_context)
    monkeypatch.setattr(offscreen, "_installed", offscreen._installed)


@pytest.fixture(autouse=True)
def _no_agent_mode_browser(monkeypatch):
    """Backend.generate turns Flow's Agent mode off in a real browser before gflow runs; no test may open one, so
    every test gets an Agent that was already off. Tests of that step call the real methods, kept on REAL_AGENT."""

    async def already_off(self, project, resolution=None):
        return False

    async def restored(self, project):
        return True

    monkeypatch.setattr(mcp_server.Backend, "_agent_off", already_off)
    monkeypatch.setattr(mcp_server.Backend, "_agent_restore", restored)
