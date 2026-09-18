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
        # Measured 2026-09-18: the selector holds 30 presets, each a name and a one-line description, plus any
        # voice saved on this account, which is listed first and says so.
        "voices": [{"name": "SaigonGirl20", "description": "giọng nữ Sài Gòn", "custom": True}]
        + [
            {"name": f"V{i:02d}", "description": "Female, youthful, mid-high pitch", "custom": False}
            for i in range(30)
        ],
        # The community Tools gallery is the same for every project, empty ones included (62 on both).
        "tools": [{"id": "community-1", "name": "Tool", "author": "a", "path": "p", "tags": ["x"]}],
        "uploads": {"count": 0 if empty else 4},
        "scenes": [] if empty else [scene],
        "timeline": {
            "scene_id": "s1",
            "title": "s",
            "trashed": False,
            "aspect": "9:16",
            "seconds": 16.0,
            "clips": [
                {"position": 0, "clip_id": "c1", "title": "t", "seconds": 8.0},
                {"position": 1, "clip_id": "c2", "title": "t", "seconds": 8.0},
            ],
        },
    }


class _Account(mcp_server.Backend):
    """The real Backend with its reads answered from canned replies: everything it does NOT override, the refusal
    of an out_dir outside out/ among it, runs the server's own code rather than a double of it."""

    def __init__(self, replies):
        super().__init__()
        self.replies = replies

    async def _with(self, fn):
        # The unit suite never talks to Flow (CLAUDE.md rule 6). Inheriting the real Backend means an unguarded
        # path would otherwise launch Chrome from a test rather than fail it (review 2026-09-18).
        raise AssertionError("the offline account must never open a browser")

    async def lane(self):
        return self.replies["lane"]

    async def projects(self):
        return self.replies["projects"]

    async def credits(self):
        return self.replies["credits"]

    async def media(self, project_id, all_versions=False, kind=None, since=None, limit=None, brief=False):
        # Only the browser is faked: the filters run through the server's own rules, so a smoke row that checks a
        # filtered answer is checking the real ones.
        if not all_versions:
            listing = self.replies["media"]
        else:
            listing = self.replies.get(
                "media_all", {**self.replies["media"], "versions": self.replies["versions"]}
            )
        return mcp_server.media_filters(listing, kind, since, limit, brief)

    async def characters(self, project_id):
        return self.replies["characters"]

    async def voices(self, project_id, entity_id):
        self.replies.setdefault("voices_asked_for", []).append(entity_id)
        return self.replies["voices"]

    async def tools(self, project_id=None):
        self.replies.setdefault("tools_asked_for", []).append(project_id)
        return self.replies["tools"]

    async def uploads(self, project_id):
        return self.replies["uploads"]

    async def scene_list(self, project_id, include_trashed=False):
        return self.replies["scenes"]

    async def scene_clips(self, project_id, scene_id):
        self.replies.setdefault("timeline_asked_for", []).append(scene_id)
        return self.replies["timeline"]


def _rows(monkeypatch, replies):
    monkeypatch.setattr(mcp_server, "backend", _Account(replies))
    findings = []
    asyncio.run(smoke.run(findings))
    return {finding["name"]: (finding["status"], finding["detail"]) for finding in findings}


def _failing(rows):
    return {name: detail for name, (status, detail) in rows.items() if status != "PASS"}


def test_a_healthy_account_passes_every_row(monkeypatch):
    rows = _rows(monkeypatch, _replies())

    assert len(rows) == 16
    assert _failing(rows) == {}


def test_the_smoke_reads_the_timeline_of_a_scene_the_listing_named(monkeypatch):
    # scene_clips needs a scene id no fixed argument can supply, so the gate takes it from scene_list's own answer.
    replies = _replies()

    _rows(monkeypatch, replies)

    assert replies["timeline_asked_for"] == ["s1"]


def test_the_smoke_reads_voices_off_a_character_the_listing_named(monkeypatch):
    # flow_voices needs an entity id for the same reason scene_clips needs a scene id: Flow only shows the
    # voices on a character's own page.
    replies = _replies()

    _rows(monkeypatch, replies)

    assert replies["voices_asked_for"] == ["e1"]


def test_the_smoke_asks_for_the_tools_gallery_without_a_project(monkeypatch):
    # Since 2026-09-15 flow_tools opens the first project itself when given none; the live gate has to walk
    # that path, or it keeps proving only the branch that already worked.
    replies = _replies()

    _rows(monkeypatch, replies)

    assert replies["tools_asked_for"] == [None]


def test_an_empty_project_passes_every_row(monkeypatch):
    # Measured 2026-09-14 on e110839d: media [], versions [], characters [], scenes [] and uploads 0 are
    # all correct answers for a project with nothing in it, so none of them may fail a row. A project with no scene
    # leaves scene_clips nothing to read: that row says SKIP, never a PASS it did not earn (CLAUDE.md rule 10).
    replies = _replies(first=EMPTY, empty=True)

    rows = _rows(monkeypatch, replies)

    assert len(rows) == 16
    assert _failing(rows) == {
        "scene_clips": "the project holds no scene to read",
        "flow_voices": "the project holds no character to read voices from",
    }
    assert rows["scene_clips"][0] == "SKIP" and rows["flow_voices"][0] == "SKIP"
    assert "timeline_asked_for" not in replies


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


def _versions_as_a_bare_list(replies):
    # The reply shape before 2026-09-15: all_versions swapped the object for a list, breaking payload["media"].
    replies["media_all"] = replies["versions"]


def _character_without_entity(replies):
    replies["characters"][0]["entity_id"] = ""


def _voice_named_by_its_icon(replies):
    # What a driver reading the row's own textContent answers: the icon, the name and the description run
    # together with no whitespace. Every name is non-empty, so a truthiness check passes it.
    replies["voices"] = [
        {
            "name": f"voice_selectionV{i:02d}Female, youthful, mid-high pitch",
            "description": "Female, youthful, mid-high pitch",
            "custom": False,
        }
        for i in range(30)
    ]


def _every_voice_claims_to_be_custom(replies):
    # What a custom flag read off the wrong element answers: true for all 30 presets as well.
    replies["voices"] = [{**voice, "custom": True} for voice in replies["voices"]]


def _voice_without_the_custom_flag(replies):
    replies["voices"] = [{k: v for k, v in voice.items() if k != "custom"} for voice in replies["voices"]]


def _no_tools(replies):
    replies["tools"] = []


def _trashed_scene_in_default_listing(replies):
    # Measured 2026-09-14 on c5d1301b: the default listing held 10 scenes, none trashed; include_trashed
    # added exactly the 6 trashed ones. A trashed scene in a default reply means the flag was ignored.
    replies["scenes"].append({"scene_id": "s2", "title": "gone", "trashed": True, "created": 1, "updated": 1})


def _timeline_of_another_scene(replies):
    replies["timeline"]["scene_id"] = "s2"


def _clips_out_of_order(replies):
    # The order is the whole point of the read: a film plays its clips by position.
    replies["timeline"]["clips"].reverse()


def _unknown_aspect(replies):
    replies["timeline"]["aspect"] = "4:3"


CORRUPTIONS = [
    ("flow_lane", _signed_out),
    ("flow_projects", _repeated_project_id),
    ("flow_credits", _null_balance),
    ("flow_media", _meta_of_another_project),
    ("flow_media all", _grid_rows_instead_of_versions),
    # A bare list breaks both reads of the versions, the plain one and the filtered one.
    (("flow_media all", "flow_media filtered"), _versions_as_a_bare_list),
    # No entity id also leaves flow_voices nothing to read, so both rows go non-PASS.
    (("flow_characters", "flow_voices"), _character_without_entity),
    ("flow_voices", _voice_named_by_its_icon),
    ("flow_voices", _voice_without_the_custom_flag),
    ("flow_voices", _every_voice_claims_to_be_custom),
    ("flow_tools", _no_tools),
    ("scene_list", _trashed_scene_in_default_listing),
    ("scene_clips", _timeline_of_another_scene),
    ("scene_clips", _clips_out_of_order),
    ("scene_clips", _unknown_aspect),
]


@pytest.mark.parametrize(
    ("row", "corrupt"), CORRUPTIONS, ids=[corrupt.__name__.strip("_") for _, corrupt in CORRUPTIONS]
)
def test_a_reply_of_the_right_type_but_the_wrong_content_fails_its_own_row(monkeypatch, row, corrupt):
    replies = _replies()
    corrupt(replies)

    rows = _rows(monkeypatch, replies)

    assert set(_failing(rows)) == ({row} if isinstance(row, str) else set(row))
