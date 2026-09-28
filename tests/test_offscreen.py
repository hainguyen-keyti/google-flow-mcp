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


class _Cdp:
    def __init__(self, log, fail):
        self.log, self.fail = log, fail

    async def send(self, method, params=None):
        self.log.append((method, params))
        if self.fail:
            raise RuntimeError("the window went away")
        if method == "Browser.getWindowForTarget":
            return {"windowId": 7, "bounds": {"left": 0}}
        if method == "Browser.getWindowBounds":
            return {"bounds": {"left": -1242}}
        return {}

    async def detach(self):
        self.log.append(("detach", None))


class _Context:
    def __init__(self, log, fail=False, pages=("page",)):
        self.log, self.fail, self.pages = log, fail, list(pages)

    async def new_cdp_session(self, page):
        return _Cdp(self.log, self.fail)


@pytest.fixture
def launch(monkeypatch):
    """Playwright's launch, replaced by one returning a recording context, with the install state reset per test."""
    log = []
    made = {"fail": False, "pages": ("page",)}

    async def original(self, user_data_dir, **kwargs):
        log.append(("launch", kwargs))
        return _Context(log, made["fail"], made["pages"])

    monkeypatch.setattr(BrowserType, "launch_persistent_context", original)
    monkeypatch.setattr(offscreen, "_installed", False)
    monkeypatch.delenv(offscreen.ENV, raising=False)
    return log, made


def _launch(**kwargs):
    return asyncio.run(BrowserType.launch_persistent_context(object(), "/tmp/profile", **kwargs))


def _moves(log):
    return [params for method, params in log if method == "Browser.setWindowBounds"]


def test_a_headed_launch_is_moved_off_the_left_edge_after_it_opens(launch):
    """Measured 2026-09-28: a --window-position flag is pulled back on screen at launch, a move sent after it is not."""
    log, _ = launch
    offscreen.install()

    _launch(headless=False, args=["--password-store=basic"])

    assert log[0][0] == "launch" and log[0][1]["args"] == ["--password-store=basic"], (
        "launch flags are gflow's own"
    )
    assert _moves(log) == [{"windowId": 7, "bounds": {"left": offscreen.LEFT, "windowState": "normal"}}], log
    assert offscreen.LEFT < 0


def test_a_headless_launch_is_left_alone(launch):
    log, _ = launch
    offscreen.install()

    _launch(headless=True)

    assert _moves(log) == []


def test_the_owner_can_switch_it_off(launch, monkeypatch):
    log, _ = launch
    monkeypatch.setenv(offscreen.ENV, "0")
    offscreen.install()

    _launch(headless=False)

    assert _moves(log) == []


def test_a_window_that_cannot_be_moved_never_breaks_the_call(launch):
    """Hiding a window is cosmetic; the launch it follows may be the start of a paid run."""
    _, made = launch
    made["fail"] = True
    offscreen.install()

    context = _launch(headless=False)

    assert isinstance(context, _Context), "the caller must still get its browser"


def test_a_long_running_server_installs_the_wrapper_once_however_many_sessions_it_opens(launch):
    """Every FlowSession calls install(); a wrapper added per call would nest one layer per tool call and, a thousand
    calls into a long-running MCP server, overflow the stack on the next launch."""
    log, _ = launch
    for _ in range(3000):
        offscreen.install()

    _launch(headless=False)

    assert len(_moves(log)) == 1, log


@pytest.mark.parametrize(
    ("argv", "hide"),
    [
        (["video", "t2v", "a boat"], True),
        (["image", "t2i", "a boat"], True),
        (["auth", "login", "--profile", "default"], False),
        (["auth", "status"], False),
        (["-v", "auth", "login"], False),
        (["--verbose", "auth", "login"], False),
        (["-v", "video", "t2v", "a boat"], True),
        ([], False),
    ],
)
def test_the_gflow_entry_point_never_hides_a_sign_in(argv, hide):
    """The owner signs in through gflow's own window: hiding it would leave a login nobody can complete."""
    from video import gflow_cli

    assert gflow_cli.hides_window(argv) is hide


def test_every_place_gflow_opens_chrome_goes_through_the_wrapped_launch():
    """The wrapper covers gflow only while gflow opens Chrome with the async launch_persistent_context. Read from the
    installed gflow itself (review of plan V: a regex for `.launch(` alone missed `launch_server`, `connect` and the
    sync API), so a gflow upgrade that opens it another way turns this red instead of silently showing windows."""
    import gflow_cli

    root = pathlib.Path(gflow_cli.__file__).parent
    called, sync = {}, []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        names = set(re.findall(r"chromium\s*\.\s*(\w+)\s*\(", text))
        if names:
            called[str(path.relative_to(root))] = names
        if re.search(r"^\s*(from|import)\s+playwright\.sync_api", text, re.MULTILINE):
            sync.append(str(path.relative_to(root)))
    others = {name: calls for name, calls in called.items() if calls - {"launch_persistent_context"}}
    assert called, "found no browser launch in gflow at all; the scan is not reading it"
    assert others == {}, f"gflow opens a browser another way: {others}"
    assert sync == [], f"gflow uses the sync API, which the wrapper does not cover: {sync}"


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


def test_the_move_says_where_the_window_ended_up(launch, capsys):
    """The gflow child runs out of sight of the repo, so the move reports where macOS actually left the window."""
    offscreen.install()

    _launch(headless=False)

    assert "[video] browser window moved aside (left=-1242)" in capsys.readouterr().err


def test_the_gflow_entry_point_installs_it_for_a_generation_and_never_for_a_sign_in(monkeypatch):
    """Review of plan V, F1: only the helper was tested, so an entry point that installed for `auth` too, or never
    installed at all, stayed green. This drives the entry point itself."""
    from video import gflow_cli

    for argv, expected in (
        (["gflow", "image", "t2i", "a cup"], ["install", "main"]),
        (["gflow", "-v", "auth", "login"], ["main"]),
    ):
        order = []
        monkeypatch.setattr(gflow_cli.offscreen, "install", lambda order=order: order.append("install"))
        monkeypatch.setattr(gflow_cli, "main", lambda order=order: order.append("main"))
        gflow_cli.run(argv)
        assert order == expected, (argv, order)


def test_a_move_chrome_never_answers_cannot_hang_the_launch(launch, monkeypatch):
    """Review of plan V, F3: a CDP call has no timeout of its own, so a Chrome that never answered stalled the launch
    and the paid run behind it. The move gives up and the caller gets its browser."""
    log, _ = launch

    class _Silent:
        async def send(self, method, params=None):
            await asyncio.sleep(3600)

        async def detach(self):
            log.append(("detach", None))

    async def silent_session(self, page):
        return _Silent()

    monkeypatch.setattr(_Context, "new_cdp_session", silent_session)
    monkeypatch.setattr(offscreen, "MOVE_TIMEOUT_S", 0.05)
    offscreen.install()

    context = _launch(headless=False)

    assert isinstance(context, _Context)
    assert ("detach", None) in log, "a move that gave up must still let go of its DevTools session"


def test_a_launch_that_names_no_headless_is_headless_as_playwright_defaults_it():
    """Review of plan V, F4: Playwright launches headless when the flag is omitted, so there is no window to move."""
    assert offscreen.headed({}) is False
    assert offscreen.headed({"headless": True}) is False
    assert offscreen.headed({"headless": False}) is True
