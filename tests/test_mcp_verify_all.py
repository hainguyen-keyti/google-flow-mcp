"""The live verification script's steps, read without running them."""

import asyncio
import importlib.util
import os
from pathlib import Path

import pytest

from video import mcp_server

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "acceptance" / "mcp_verify_all.py"
# A served tool that takes a prompt makes a media from it, and Flow titles that media from the prompt.
MAKES_MEDIA = {
    tool.name
    for tool in asyncio.run(mcp_server.server.list_tools())
    if "prompt" in tool.input_schema["properties"]
}


@pytest.fixture(scope="module")
def verify_all():
    # The script sets gflow's lease wait and log level on import; they must not reach the tests after this one.
    saved = dict(os.environ)
    spec = importlib.util.spec_from_file_location("mcp_verify_all", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return module


class _Ids(dict):
    def __missing__(self, key):
        return key


def test_no_two_steps_that_make_media_share_a_prompt_or_its_start(verify_all):
    # Flow titles a media from its prompt and scene_add_clip refuses a title two media share: S01 hit that on
    # 2026-10-08 and 2026-10-12, its clip's prompt being the t2i image's prompt with a few words added.
    prompts = {}
    for step in verify_all.STEPS:
        args = step["args"](_Ids())
        if step["tool"] in MAKES_MEDIA and not args.get("dry_run") and args.get("prompt"):
            prompts[step["name"]] = args["prompt"]

    shared = [
        (one, other)
        for one, first in prompts.items()
        for other, second in prompts.items()
        if one < other and (first.startswith(second) or second.startswith(first))
    ]

    assert len(prompts) >= 10
    assert shared == []
