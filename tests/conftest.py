import pytest
from playwright._impl._network import Response
from playwright.async_api import BrowserType

from video import bodies, extensions, mcp_server, offscreen


@pytest.fixture(autouse=True)
def _playwrights_own_launch(monkeypatch):
    """A test that calls the real offscreen.install() or bodies.install() would leave Playwright's launch wrapped, and
    its Response.body patched, for every later test."""
    monkeypatch.setattr(BrowserType, "launch_persistent_context", BrowserType.launch_persistent_context)
    monkeypatch.setattr(offscreen, "_installed", offscreen._installed)
    monkeypatch.setattr(extensions, "_installed", extensions._installed)
    monkeypatch.setattr(Response, "body", Response.body)
    monkeypatch.setattr(bodies, "_installed", bodies._installed)
    monkeypatch.setattr(bodies, "_bodies", bodies._bodies.__class__())


@pytest.fixture(autouse=True)
def _no_focus_scripting(monkeypatch):
    """Plan AX: a launch asks macOS which app is in front and gives it the focus back; no test may script the owner's
    desktop, so every test sees no app in front. Tests of that step replace these two themselves."""

    async def nobody():
        return None

    async def nothing(app):
        return None

    monkeypatch.setattr(offscreen, "front_app", nobody, raising=False)
    monkeypatch.setattr(offscreen, "activate", nothing, raising=False)


@pytest.fixture(autouse=True)
def _no_agent_mode_browser(monkeypatch):
    """Backend.generate turns Flow's Agent mode off in a real browser before gflow runs; no test may open one, so
    every test gets an Agent that was already off. Tests of that step call the real methods, kept on REAL_AGENT."""

    async def already_off(self, project, resolution=None, **settings):
        return False

    async def restored(self, project):
        return True

    monkeypatch.setattr(mcp_server.Backend, "_agent_off", already_off)
    monkeypatch.setattr(mcp_server.Backend, "_agent_restore", restored)
