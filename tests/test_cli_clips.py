import pytest
from click.testing import CliRunner

from video import cli


@pytest.mark.parametrize(
    "args",
    [
        ["flow", "clip", "edit", "P", "M", "red shirt\nstill camera"],
        ["flow", "clip", "extend", "P", "M", "keep going\r"],
        ["flow", "agent", "send", "P", "hi\nthere"],
    ],
)
def test_a_multiline_prompt_is_refused_before_a_browser_opens(monkeypatch, args):
    # Review 2026-09-15: the MCP tools refuse a newline, the CLI did not, and Flow's editor and agent box take one as
    # Enter before the ledger has a `submitted` row.
    def must_not_open(profile, fn):
        raise AssertionError("opened a browser for text Flow would take as Enter")

    monkeypatch.setattr(cli, "_read", must_not_open)

    result = CliRunner().invoke(cli.main, args)

    assert result.exit_code == 2, result.output
    assert "one line" in result.output
