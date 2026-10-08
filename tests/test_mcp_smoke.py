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

from video import gen, mcp_server

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
        # Measured 2026-10-01 on 69 listed videos: the 68 generated clips carry a model, the saved voice none.
        "model": "veo_3_1_i2v_lite",
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
        # Shaped like reader.recipe_from's answer for a start-frame clip (plan AL).
        "recipe": {
            "media_id": "m1",
            "workflow_id": "w1",
            "model_key": "veo_3_1_i2v_lite",
            "kind": "frames",
            "frames": [{"slot": "start", "workflow_id": "w0", "media_id": "m0", "title": "key.png"}],
            "reference_images": [],
            "voices": [],
            "characters": [],
            "source": None,
        },
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


# The out folder the offline account keeps its ledgers in, set per test by `_out`: never the repo's own out/.
_STATE = {}


def _settle(folder, job_id, status="done"):
    ledger = gen.Ledger(folder / "ledger.jsonl")
    ledger.append(job_id, "submitted", kind="video", project=PROJECT, credits_before=200)
    ledger.append(job_id, status, media_id="m1", path="out/m1.mp4", spent=10, credits_after=190)
    return ledger


@pytest.fixture(autouse=True)
def _out(tmp_path, monkeypatch):
    """A used account's out folder: one ledger holding one settled job."""
    monkeypatch.setitem(_STATE, "out", tmp_path)
    _settle(tmp_path, "job-settled")
    return tmp_path


class _Account(mcp_server.Backend):
    """The real Backend with its reads answered from canned replies: everything it does NOT override, the refusal
    of an out_dir outside out/ and the answer of a settled job among it, runs the server's own code rather than a
    double of it."""

    def __init__(self, replies):
        super().__init__(out_dir=_STATE["out"])
        self.replies = replies

    async def capabilities(self):
        if "capabilities" in self.replies:
            return self.replies["capabilities"]
        return await super().capabilities()

    async def flow_check(self, project_id=None):
        # The drift check reads the grid and a build label off the page: canned here, like every other read.
        return self.replies.get(
            "flow_check",
            {
                "build": {"live": "Zz9.1.O", "baseline": "Zz9.1.O", "changed": False},
                "ui": [],
                "wire": [],
                "drift": False,
                "folder": "out/check/offline",
            },
        )

    async def job_status(self, job_id):
        self.replies.setdefault("job_asked_for", []).append(job_id)
        if "job_status" in self.replies:
            return self.replies["job_status"]
        return await super().job_status(job_id)

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

    async def clip_recipe(self, project_id, media_id, workflow_id=None):
        self.replies.setdefault("recipe_asked_for", []).append(media_id)
        return self.replies["recipe"]

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

    assert len(rows) == 20
    assert _failing(rows) == {}


def test_the_smoke_asks_where_a_job_the_ledger_settled_stands(monkeypatch, _out):
    # job_status needs a job id the way scene_clips needs a scene id; the gate takes the newest job one ledger alone
    # settled as done, which the server answers from the row with no browser (the offline account has none).
    _settle(_out / "older", "job-older")
    _settle(_out, "job-settled-later")
    _settle(_out / "failed", "job-failed", status="failed")
    _settle(_out / "twice-a", "job-twice")
    _settle(_out / "twice-b", "job-twice")
    replies = _replies()

    rows = _rows(monkeypatch, replies)

    assert replies["job_asked_for"] == ["job-settled-later"]
    assert rows["job_status"][0] == "PASS"


def test_an_out_folder_with_no_settled_job_skips_the_job_row(monkeypatch, _out):
    (_out / "ledger.jsonl").unlink()
    _settle(_out, "job-failed", status="failed")
    replies = _replies()

    rows = _rows(monkeypatch, replies)

    assert rows["job_status"] == ("SKIP", "no ledger under the out folder holds a settled job to ask about")
    assert "job_asked_for" not in replies


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


def test_the_smoke_reads_the_recipe_of_a_clip_the_listing_named(monkeypatch):
    # clip_recipe needs a media id the way scene_clips needs a scene id, so the gate takes a video the listing named.
    replies = _replies()

    rows = _rows(monkeypatch, replies)

    assert replies["recipe_asked_for"] == ["m1"]
    assert rows["clip_recipe"][0] == "PASS"


def test_the_smoke_never_takes_a_saved_voice_for_a_clip(monkeypatch):
    # Measured 2026-10-01: flow_media lists a voice saved on the account as kind "video", with no model. Taken as
    # the first video, it would fail this row with "keeps no recipe" on a healthy account (review of plan AL).
    replies = _replies()
    voice = {
        **replies["media"]["media"][0],
        "id": "voice-1",
        "title": "LilyVoice",
        "model": None,
        "url": None,
    }
    replies["media"]["media"].insert(0, voice)

    rows = _rows(monkeypatch, replies)

    assert replies["recipe_asked_for"] == ["m1"]
    assert rows["clip_recipe"][0] == "PASS"

    only_a_voice = _replies()
    only_a_voice["media"]["media"] = [voice]
    rows = _rows(monkeypatch, only_a_voice)
    assert rows["clip_recipe"][:2] == ("SKIP", "the project holds no generated video to read a recipe from")


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

    assert len(rows) == 20
    assert _failing(rows) == {
        "scene_clips": "the project holds no scene to read",
        "flow_voices": "the project holds no character to read voices from",
        "clip_recipe": "the project holds no generated video to read a recipe from",
    }
    assert rows["scene_clips"][0] == "SKIP" and rows["flow_voices"][0] == "SKIP"
    assert rows["clip_recipe"][0] == "SKIP"
    assert "timeline_asked_for" not in replies and "recipe_asked_for" not in replies


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


def _recipe_of_another_clip(replies):
    replies["recipe"]["media_id"] = "m2"


def _recipe_with_no_model_key(replies):
    replies["recipe"]["model_key"] = None


def _recipe_of_a_kind_nobody_named(replies):
    replies["recipe"]["kind"] = "video"


def _recipe_whose_voices_are_bare_strings(replies):
    # A voice is a row with what Flow recorded, a name and the custom flag; a bare string hides which it was.
    replies["recipe"]["voices"] = ["achird"]


def _recipe_voice_that_does_not_say_if_it_is_custom(replies):
    replies["recipe"]["voices"] = [{"voice": "achird", "name": "Achird"}]


SETTLED = {"job_id": "job-settled", "state": "settled", "status": "done", "outcome": {"code": "DONE"}}


def _job_read_off_flow_instead_of_its_row(replies):
    replies["job_status"] = {**SETTLED, "state": "ready", "status": None}


def _status_of_another_job(replies):
    replies["job_status"] = {**SETTLED, "job_id": "job-other"}


def _settled_job_with_no_outcome(replies):
    replies["job_status"] = {key: value for key, value in SETTLED.items() if key != "outcome"}


def _done_job_said_to_be_unknown(replies):
    replies["job_status"] = {**SETTLED, "outcome": {"code": "UNKNOWN"}}


def _real_map():
    from video.flow import capabilities

    return capabilities.capabilities()


def _a_cell_at_another_price(replies):
    # The table an agent budgets from: one cell a credit off the surveyed file is a wrong budget.
    replies["capabilities"] = _real_map()
    replies["capabilities"]["video"]["models"]["omni-flash"]["credits_x1"][0]["credits"] += 1


def _a_model_the_survey_holds_left_out(replies):
    replies["capabilities"] = _real_map()
    del replies["capabilities"]["video"]["models"]["veo-fast"]


def _a_map_dated_another_day(replies):
    replies["capabilities"] = _real_map()
    replies["capabilities"]["video"]["measured"] = "2020-01-01"


def _a_model_with_no_cap(replies):
    replies["capabilities"] = _real_map()
    replies["capabilities"]["video"]["models"]["veo-lite"]["voice_ingredients"] = None


def _two_cells_with_their_prices_swapped(replies):
    # Found in review: the row compared the sorted prices, so the dearest cell could carry the cheapest price.
    replies["capabilities"] = _real_map()
    cells = replies["capabilities"]["video"]["models"]["omni-flash"]["credits_x1"]
    cells[0]["credits"], cells[-1]["credits"] = cells[-1]["credits"], cells[0]["credits"]
    assert cells[0]["credits"] != cells[-1]["credits"]


def _a_model_with_no_image_cap(replies):
    replies["capabilities"] = _real_map()
    replies["capabilities"]["video"]["models"]["veo-lite"]["image_ingredients"] = None


def _a_cap_below_zero(replies):
    replies["capabilities"] = _real_map()
    replies["capabilities"]["video"]["models"]["veo-lite"]["voice_ingredients"] = -1


def _a_cap_that_is_a_flag(replies):
    replies["capabilities"] = _real_map()
    replies["capabilities"]["video"]["models"]["veo-lite"]["voice_ingredients"] = True


def _counts_the_survey_does_not_hold(replies):
    replies["capabilities"] = _real_map()
    replies["capabilities"]["video"]["counts"] = [1]


def _aspects_the_survey_does_not_hold(replies):
    replies["capabilities"] = _real_map()
    replies["capabilities"]["video"]["aspects"] = ["1:1"]


def _a_length_the_survey_does_not_hold(replies):
    replies["capabilities"] = _real_map()
    replies["capabilities"]["video"]["models"]["omni-flash"]["seconds"] = [99]


def _a_mode_the_survey_does_not_hold(replies):
    replies["capabilities"] = _real_map()
    replies["capabilities"]["video"]["models"]["veo-quality"]["modes"] = ["Frames"]


def _modes_the_survey_does_not_hold(replies):
    replies["capabilities"] = _real_map()
    replies["capabilities"]["video"]["modes"] = ["Frames"]


def _a_resolution_the_survey_does_not_hold(replies):
    replies["capabilities"] = _real_map()
    replies["capabilities"]["video"]["models"]["omni-flash"]["resolutions"] = ["720p"]


def _an_image_composer_of_another_survey(replies):
    replies["capabilities"] = _real_map()
    replies["capabilities"]["image"]["composer"]["price_line_x1"] = 50


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
    ("clip_recipe", _recipe_of_another_clip),
    ("clip_recipe", _recipe_with_no_model_key),
    ("clip_recipe", _recipe_of_a_kind_nobody_named),
    ("clip_recipe", _recipe_whose_voices_are_bare_strings),
    ("clip_recipe", _recipe_voice_that_does_not_say_if_it_is_custom),
    ("job_status", _job_read_off_flow_instead_of_its_row),
    ("job_status", _status_of_another_job),
    ("job_status", _settled_job_with_no_outcome),
    ("job_status", _done_job_said_to_be_unknown),
    ("flow_capabilities", _a_cell_at_another_price),
    ("flow_capabilities", _a_model_the_survey_holds_left_out),
    ("flow_capabilities", _a_map_dated_another_day),
    ("flow_capabilities", _a_model_with_no_cap),
    ("flow_capabilities", _two_cells_with_their_prices_swapped),
    ("flow_capabilities", _a_model_with_no_image_cap),
    ("flow_capabilities", _a_cap_below_zero),
    ("flow_capabilities", _a_cap_that_is_a_flag),
    ("flow_capabilities", _counts_the_survey_does_not_hold),
    ("flow_capabilities", _aspects_the_survey_does_not_hold),
    ("flow_capabilities", _a_length_the_survey_does_not_hold),
    ("flow_capabilities", _a_mode_the_survey_does_not_hold),
    ("flow_capabilities", _modes_the_survey_does_not_hold),
    ("flow_capabilities", _a_resolution_the_survey_does_not_hold),
    ("flow_capabilities", _an_image_composer_of_another_survey),
]


@pytest.mark.parametrize(
    ("row", "corrupt"), CORRUPTIONS, ids=[corrupt.__name__.strip("_") for _, corrupt in CORRUPTIONS]
)
def test_a_reply_of_the_right_type_but_the_wrong_content_fails_its_own_row(monkeypatch, row, corrupt):
    replies = _replies()
    corrupt(replies)

    rows = _rows(monkeypatch, replies)

    assert set(_failing(rows)) == ({row} if isinstance(row, str) else set(row))
