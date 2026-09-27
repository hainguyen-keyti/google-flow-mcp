"""The tool reference has to be generated from the server, not typed next to it.

CLAUDE.md rule 10, paid for twice in this repo: a hand-typed list of tools let two commands go missing for a
week while the test stayed green, and a hand-typed table of which tools take `out_dir` left two of them
unguarded. So the published table is rendered from the running server, and this test fails the moment the file
on disk stops matching what is actually served.
"""

import asyncio
import importlib.util
from pathlib import Path

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


def test_a_tool_that_never_says_what_it_costs_is_reported():
    """Every description in this repo names its price, because the description is the contract an agent reads
    before spending. A new tool that forgets is caught here rather than in someone's bill."""
    silent = asyncio.run(gen.priceless())

    assert silent == [], f"these tools never mention a price: {silent}"
