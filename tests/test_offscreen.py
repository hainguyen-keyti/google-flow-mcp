"""The tool's Chrome opens off to the side of the screen (plan V, 2026-09-28).

Headless is not an option: reCAPTCHA Enterprise answers headless Chromium with a 403 (gflow
_docs/AUTHENTICATION.md:490). A real, headed window placed off screen is what Google sees as a browser while the
owner does not see it. macOS keeps a ~40 px strip on screen whatever is asked (measured: left=-4000 came back as -1242
for a 1282-wide window).
"""

import asyncio
import pathlib
import re

import pytest
from playwright.async_api import BrowserType

from video import offscreen


@pytest.fixture
def launch(monkeypatch):
    """Playwright's launch, replaced by a recorder, with the module's install state reset around each test."""
    seen = []

    async def original(self, user_data_dir, **kwargs):
        seen.append(kwargs)
        return "context"

    monkeypatch.setattr(BrowserType, "launch_persistent_context", original)
    monkeypatch.setattr(offscreen, "_installed", False)
    monkeypatch.delenv(offscreen.ENV, raising=False)
    return seen


def _launch(**kwargs):
    return asyncio.run(BrowserType.launch_persistent_context(object(), "/tmp/profile", **kwargs))


def test_a_headed_launch_gets_exactly_one_window_position(launch):
    offscreen.install()

    _launch(headless=False, args=["--password-store=basic", "--window-position=10,10"])

    args = launch[0]["args"]
    assert args.count(offscreen.FLAG) == 1 and "--password-store=basic" in args, args
    assert [a for a in args if a.startswith("--window-position")] == [offscreen.FLAG], args


def test_a_headless_launch_is_left_alone(launch):
    offscreen.install()

    _launch(headless=True, args=["--password-store=basic"])

    assert launch[0]["args"] == ["--password-store=basic"]


def test_the_owner_can_switch_it_off(launch, monkeypatch):
    monkeypatch.setenv(offscreen.ENV, "0")
    offscreen.install()

    _launch(headless=False, args=[])

    assert launch[0]["args"] == []


def test_a_long_running_server_installs_the_wrapper_once_however_many_sessions_it_opens(launch):
    """Every FlowSession calls install(); a wrapper added per call would nest one layer per tool call and, a thousand
    calls into a long-running MCP server, overflow the stack on the next launch."""
    for _ in range(3000):
        offscreen.install()

    _launch(headless=False, args=[])

    assert launch[0]["args"] == [offscreen.FLAG]


@pytest.mark.parametrize(
    ("argv", "hide"),
    [
        (["video", "t2v", "a boat"], True),
        (["image", "t2i", "a boat"], True),
        (["auth", "login", "--profile", "default"], False),
        (["auth", "status"], False),
        ([], False),
    ],
)
def test_the_gflow_entry_point_never_hides_a_sign_in(argv, hide):
    """The owner signs in through gflow's own window: hiding it would leave a login nobody can complete."""
    from video import gflow_cli

    assert gflow_cli.hides_window(argv) is hide


def test_every_place_gflow_opens_chrome_goes_through_the_wrapped_launch():
    """The wrapper covers gflow only while gflow opens Chrome with launch_persistent_context. Read from the installed
    gflow itself, so a gflow upgrade that opens it another way turns this red instead of silently showing windows."""
    import gflow_cli

    root = pathlib.Path(gflow_cli.__file__).parent
    launches = {
        str(path.relative_to(root)): re.findall(
            r"\.(launch_persistent_context|launch|connect_over_cdp)\(", text
        )
        for path in root.rglob("*.py")
        if (text := path.read_text(encoding="utf-8"))
        and re.search(r"\.(launch_persistent_context|launch|connect_over_cdp)\(", text)
    }
    others = {name: calls for name, calls in launches.items() if set(calls) - {"launch_persistent_context"}}
    assert launches, "found no browser launch in gflow at all; the scan is not reading it"
    assert others == {}, f"gflow opens a browser another way: {others}"


def test_the_repos_own_browser_session_installs_it_before_it_launches(monkeypatch):
    """Every tool that drives the repo's own session (reads, scenes, the editor, gen_character, agent) opens Chrome
    through FlowSession, so that is where the wrapper goes in, before the client is built."""
    from video import session as session_mod

    order = []
    monkeypatch.setattr(offscreen, "install", lambda: order.append("install"))

    class _Client:
        async def __aenter__(self):
            order.append("launch")
            raise RuntimeError("stop here")

        async def __aexit__(self, *exc):
            return False

    def factory(profile_dir, *, headless):
        order.append("factory")
        return _Client()

    session = session_mod.FlowSession(profile_dir=pathlib.Path("/tmp/profile"), client_factory=factory)
    with pytest.raises(RuntimeError, match="stop here"):
        asyncio.run(session.__aenter__())

    assert order[:2] == ["install", "factory"], order
