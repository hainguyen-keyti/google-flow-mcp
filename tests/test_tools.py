import json
from pathlib import Path

import pytest

from video.flow import parsers

FIXTURE = Path(__file__).parent / "fixtures" / "rpc" / "tRARke.json"


def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["payload"]


def test_tools_lists_the_community_gallery():
    tools = parsers.tools(payload())
    assert len(tools) == 62
    first = tools[0]
    assert first["id"] == "community-9101c9fd-d061-4771-985e-7a4d0d0e05de"
    assert first["name"] == "Screen Script"
    assert first["description"].startswith("Professional screenplay editor")
    assert first["author"] == "Zach M"
    assert first["tags"] == ["Video"]
    assert first["path"] == "applets/community-9101c9fd-d061-4771-985e-7a4d0d0e05de"
    assert all(t["id"] and t["name"] for t in tools)


def test_tools_rejects_a_non_listing():
    with pytest.raises(TypeError):
        parsers.tools([840, 1, 2, 2, None, 840])
