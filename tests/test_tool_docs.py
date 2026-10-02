"""The tool reference has to be generated from the server, not typed next to it.

CLAUDE.md rule 10, paid for twice in this repo: a hand-typed list of tools let two commands go missing for a
week while the test stayed green, and a hand-typed table of which tools take `out_dir` left two of them
unguarded. So the published table is rendered from the running server, and this test fails the moment the file
on disk stops matching what is actually served.
"""

import asyncio
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_GEN = ROOT / "scripts" / "gen_tool_docs.py"
_spec = importlib.util.spec_from_file_location("gen_tool_docs", _GEN)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def test_the_published_table_matches_the_tools_the_server_serves():
    on_disk = (ROOT / "docs" / "tools.md").read_text(encoding="utf-8")

    assert on_disk == asyncio.run(gen.render()), (
        "docs/tools.md is stale: run `uv run python scripts/gen_tool_docs.py` to rebuild it from the server"
    )


def test_every_served_tool_appears_with_its_own_description():
    rendered = asyncio.run(gen.render())
    tools = asyncio.run(gen.tools())

    assert len(tools) >= 40, f"only {len(tools)} tools served"
    for tool in tools:
        assert f"### `{tool.name}`" in rendered, f"{tool.name} is missing from the table"
        first = (tool.description or "").strip().split(". ")[0]
        assert first[:60] in rendered, f"{tool.name} is listed without what it does"


def test_no_served_tool_is_left_without_a_group():
    """The groups are typed by hand, so a new tool lands under "Ungrouped" until someone files it: gen_video and
    clip_recipe sat there from plans AB and AL until 2026-10-02."""
    served = {tool.name for tool in asyncio.run(gen.tools())}
    listed = [name for _, _, names in gen.GROUPS for name in names]

    assert sorted(served - set(listed)) == []
    assert sorted(set(listed) - served) == [], "a group names a tool the server no longer serves"
    assert len(listed) == len(set(listed)), "a tool is filed under two groups"
    assert "Ungrouped" not in asyncio.run(gen.render())


def test_a_tool_that_never_says_what_it_costs_is_reported():
    """Every description in this repo names its price, because the description is the contract an agent reads
    before spending. A new tool that forgets is caught here rather than in someone's bill."""
    silent = asyncio.run(gen.priceless())

    assert silent == [], f"these tools never mention a price: {silent}"


def _sections(rendered: str) -> dict[str, str]:
    """The rendered text of each tool's own section, split the way render() joins it."""
    sections = {}
    for chunk in rendered.split("### `")[1:]:
        name, _, body = chunk.partition("`")
        sections[name] = body
    return sections


def test_the_table_lists_the_arguments_the_server_actually_takes():
    """Measured 2026-09-28: the generator read `tool.inputSchema`, a name the served Tool does not have (it has
    `input_schema`), so `getattr` returned None, `or {}` made it empty and all 43 tools were published as taking
    no arguments. The expected table is GENERATED from the server here, never typed (rule 13), because a typed
    one drifts the moment a tool gains a parameter."""
    sections = _sections(asyncio.run(gen.render()))
    served = {tool.name: tool for tool in asyncio.run(gen.tools())}

    for name, tool in served.items():
        schema = tool.input_schema or {}
        properties = list((schema.get("properties") or {}).keys())
        required = set(schema.get("required") or ())
        stated = [ln for ln in sections[name].splitlines() if ln.startswith("**Arguments**: ")]
        assert len(stated) == 1, f"{name} states its arguments {len(stated)} times"
        line = stated[0]
        if not properties:
            assert line == "**Arguments**: none", f"{name} takes nothing but says {line}"
            continue
        for argument in properties:
            wanted = f"`{argument}`" + ("" if argument in required else " (optional)")
            assert wanted in line, f"{name} serves {argument} but the table says {line}"


def test_the_tools_that_really_take_nothing_are_the_only_ones_saying_none():
    """The three reads that take no argument are the whole of the honest "none"; 43 of them was the bug."""
    rendered = asyncio.run(gen.render())
    served = asyncio.run(gen.tools())

    nothing = sorted(t.name for t in served if not (t.input_schema or {}).get("properties"))
    assert nothing == ["flow_credits", "flow_lane", "flow_projects"], nothing
    assert rendered.count("**Arguments**: none") == len(nothing), rendered.count("**Arguments**: none")


def test_a_served_tool_with_no_schema_at_all_stops_the_generator():
    """The failure mode this bug had: a renamed attribute answered "none" instead of saying it could not read the
    schema. A generator that cannot find a schema must stop, not publish an empty column."""

    class _Schemaless:
        name = "mystery_tool"
        description = "free"

    with pytest.raises(AttributeError, match="mystery_tool"):
        gen._arguments(_Schemaless())


# Each place a README states how many tools the server serves. A sentence reworded out of its pattern fails here
# too, so the count is never left without a check (2026-10-01: README.md said 44 and README.vi.md 43, with 45 served).
README_COUNTS = [
    ("README.md", r"(\d+) MCP tools plus a CLI"),
    ("README.md", r"serves \*\*(\d+) tools\*\*"),
    ("README.vi.md", r"(?m)^(\d+) tool, chia theo"),
]


@pytest.mark.parametrize(("name", "pattern"), README_COUNTS)
def test_a_readme_states_the_number_of_tools_the_server_really_serves(name, pattern):
    served = len(asyncio.run(gen.tools()))
    stated = re.findall(pattern, (ROOT / name).read_text(encoding="utf-8"))

    assert stated, (
        f"{name} no longer states its tool count as /{pattern}/; update this pattern with the sentence"
    )
    assert {int(count) for count in stated} == {served}, (
        f"{name} says {stated} tools, the server serves {served}"
    )
