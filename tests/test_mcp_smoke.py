"""The MCP smoke has to judge what a tool SAID, not only what type it said it in.

Measured 2026-09-14: flow_uploads answered `count: null`, a doubled tile figure and an empty rpcid list on a
project holding 4 uploads, and the smoke stayed green through all of it, because its check only asked
whether the reply was a dict. Every other read tool was checked the same way. These tests drive the real
`run()` against a fake account whose replies are shaped exactly like the live ones in out/smoke_payloads.
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from video import mcp_server

_SMOKE = Path(__file__).resolve().parents[1] / "scripts" / "acceptance" / "mcp_smoke.py"
_spec = importlib.util.spec_from_file_location("mcp_smoke", _SMOKE)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

PROJECT = "604b2de7-997a-4911-9b50-eaddec250d45"
OTHER = "c5d1301b-07fe-4485-86d9-49a47449a494"
EMPTY = "e110839d-25f3-4e1e-aa0a-832c4fc3856f"
# A real project id on the account that is NOT a UUID; a check demanding UUIDs would fail the live gate.
BACKFILL = "8822142b-ca75-46b7-aac8-03d2831_backfill"


def _replies(first=PROJECT, empty=False):
    grid_row = {
        "id": "m1",
        "project_id": first,
        "kind": "video",
        "title": "t",
        "created": 1,
        "model": None,
        "prompt": None,
        "size_bytes": 1,
        "status": None,
        "url": "https://flow-content.google/REDACTED",
        "workflow_id": "w1",
    }
    version = {key: value for key, value in grid_row.items() if key != "title"} | {
        "type": "CAE",
        "listed": True,
    }
    scene = {"scene_id": "s1", "title": "s", "trashed": False, "created": 1, "updated": 1}
    return {
        "lane": {"verdict": "MIGRATED", "projects": 2, "roots": {"labs": {}, "migrated": {}}},
        "projects": [
            {"id": first, "title": "a", "created": 1, "cover_media_id": "c", "thumbnail_url": "u"},
            {
                "id": BACKFILL,
                "title": "Ingredient Archive",
                "created": 1,
                "cover_media_id": "c",
                "thumbnail_url": "u",
            },
        ],
        "credits": {"balance": 195, "raw": [195, 1, 2, 2, None, 195]},
        "media": {
            "meta": {"id": first, "title": "a"},
            "media": [] if empty else [grid_row],
            "models": ["veo_3_1_lite", "veo_3_1_fast", "veo_3_1_quality", "abra"],
        },
        "versions": [] if empty else [version],
        "characters": [] if empty else [{"entity_id": "e1", "name": "Mai", "portrait_media_id": "p1"}],
        # The community Tools gallery is the same for every project, empty ones included (62 on both).
        "tools": [{"id": "community-1", "name": "Tool", "author": "a", "path": "p", "tags": ["x"]}],
        "uploads": {"count": 0 if empty else 4},
        "scenes": [] if empty else [scene],
    }


class _Account:
    def __init__(self, replies):
        self.replies = replies

    async def lane(self):
        return self.replies["lane"]

    async def projects(self):
        return self.replies["projects"]

    async def credits(self):
        return self.replies["credits"]

    async def media(self, project_id, all_versions=False):
        return self.replies["versions" if all_versions else "media"]

    async def characters(self, project_id):
        return self.replies["characters"]

    async def tools(self, project_id):
        return self.replies["tools"]

    async def uploads(self, project_id):
        return self.replies["uploads"]

    async def scene_list(self, project_id, include_trashed=False):
        return self.replies["scenes"]


def _rows(monkeypatch, replies):
    monkeypatch.setattr(mcp_server, "backend", _Account(replies))
    findings = []
    asyncio.run(smoke.run(findings))
    return {finding["name"]: (finding["status"], finding["detail"]) for finding in findings}


def _failing(rows):
    return {name: detail for name, (status, detail) in rows.items() if status != "PASS"}


def test_a_healthy_account_passes_every_row(monkeypatch):
    rows = _rows(monkeypatch, _replies())

    assert len(rows) == 12
    assert _failing(rows) == {}


def test_an_empty_project_passes_every_row(monkeypatch):
    # Measured 2026-09-14 on e110839d: media [], versions [], characters [], scenes [] and uploads 0 are
    # all correct answers for a project with nothing in it, so none of them may fail a row.
    rows = _rows(monkeypatch, _replies(first=EMPTY, empty=True))

    assert len(rows) == 12
    assert _failing(rows) == {}


def _signed_out(replies):
    replies["lane"]["verdict"] = "SIGNED_OUT"


def _repeated_project_id(replies):
    replies["projects"].append(dict(replies["projects"][0]))


def _null_balance(replies):
    replies["credits"]["balance"] = None


def _meta_of_another_project(replies):
    replies["media"]["meta"]["id"] = OTHER


def _grid_rows_instead_of_versions(replies):
    # The shape a swallowed all_versions produces: grid rows carry no `type`, version records always do.
    replies["versions"] = [dict(replies["media"]["media"][0])]


def _character_without_entity(replies):
    replies["characters"][0]["entity_id"] = ""


def _no_tools(replies):
    replies["tools"] = []


def _trashed_scene_in_default_listing(replies):
    # Measured 2026-09-14 on c5d1301b: the default listing held 10 scenes, none trashed; include_trashed
    # added exactly the 6 trashed ones. A trashed scene in a default reply means the flag was ignored.
    replies["scenes"].append({"scene_id": "s2", "title": "gone", "trashed": True, "created": 1, "updated": 1})


CORRUPTIONS = [
    ("flow_lane", _signed_out),
    ("flow_projects", _repeated_project_id),
    ("flow_credits", _null_balance),
    ("flow_media", _meta_of_another_project),
    ("flow_media all", _grid_rows_instead_of_versions),
    ("flow_characters", _character_without_entity),
    ("flow_tools", _no_tools),
    ("scene_list", _trashed_scene_in_default_listing),
]


@pytest.mark.parametrize(
    ("row", "corrupt"), CORRUPTIONS, ids=[corrupt.__name__.strip("_") for _, corrupt in CORRUPTIONS]
)
def test_a_reply_of_the_right_type_but_the_wrong_content_fails_its_own_row(monkeypatch, row, corrupt):
    replies = _replies()
    corrupt(replies)

    rows = _rows(monkeypatch, replies)

    assert set(_failing(rows)) == {row}
