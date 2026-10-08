"""The wire signature (plan AQ, T5): the shape of Flow's replies, folded and compared."""

import json
from pathlib import Path

import pytest

from video.flow import version, wire

FIXTURES = Path(__file__).parent / "fixtures"
U1 = "3f5ce5c7-4a65-4211-b846-8a1db0cb1174"
U2 = "e61f5b7f-5b59-4b25-bb83-8640d6807441"


def _record(media, title, created, thumb=None):
    return [media, [title, None, [created, 0], thumb], None, 2]


def test_a_listing_of_three_records_and_one_of_forty_fold_to_the_same_skeleton():
    three = [None, 992, [_record(U1, "a", 1, "https://x/a.jpg"), _record(U2, "b", 2), _record(U1, "c", 3)]]
    forty = [
        None,
        5,
        [_record(U1 if i % 2 else U2, f"t{i}", i, "https://x/a.jpg" if i % 3 else None) for i in range(40)],
    ]

    assert wire.skeleton(three) == wire.skeleton(forty)
    assert wire.skeleton(three)[2] == {
        "*": ["uuid", ["str", "null", ["int", "int"], {"any": ["null", "url"]}], "null", "int"]
    }


def test_a_scalar_folds_to_its_kind():
    assert [wire.skeleton(x) for x in (None, True, 3, 2.5, U1, "https://a/b", "text")] == [
        "null",
        "bool",
        "int",
        "float",
        "uuid",
        "url",
        "str",
    ]


def test_a_field_that_changed_kind_or_went_away_is_drift_and_a_new_one_is_not():
    base = wire.skeleton([None, 1, [_record(U1, "a", 1), _record(U2, "b", 2)]])
    moved = wire.skeleton(
        [None, 1, [[["a", None, [1, 0], None], U1, None, 2], [["b", None, [2, 0], None], U2, None, 2]]]
    )
    gone = wire.skeleton([None, 1, [[U1, ["a", None, [1, 0], None]], [U2, ["b", None, [2, 0], None]]]])
    grown = wire.skeleton([None, 1, [_record(U1, "a", 1) + ["new"], _record(U2, "b", 2) + ["new"]], "tail"])
    filled = wire.skeleton(
        [None, 1, [_record(U1, "a", 1, "https://x/a.jpg"), _record(U2, "b", 2, "https://x/b.jpg")]]
    )

    assert wire.drifted(wire.compare(base, moved)) and wire.drifted(wire.compare(base, gone))
    assert [kind for kind, _, _ in wire.compare(base, grown)] == ["position added", "position added"]
    assert not wire.drifted(wire.compare(base, grown))
    # The baseline saw null where Flow now fills a url: said, not drift.
    assert [kind for kind, _, _ in wire.compare(base, filled)] == ["kind appeared"]
    assert not wire.drifted(wire.compare(base, filled))


def test_a_scalar_turned_null_everywhere_is_drift_and_a_structure_turned_null_is_said():
    # Technical review of plan AQ: the version field turning null everywhere (2026-10-05) is the incident this
    # module exists for, and a rule that read null as compatible with anything would have passed it in silence.
    base = wire.skeleton([U1, "title", 3])
    found = wire.compare(base, wire.skeleton([U1, None, 3]))
    assert [kind for kind, _, _ in found] == ["kind emptied"] and wire.drifted(found), found

    records = wire.skeleton([None, 1, [_record(U1, "a", 1), _record(U2, "b", 2)]])
    emptied = wire.compare(records, wire.skeleton([None, 1, None]))
    assert [kind for kind, _, _ in emptied] == ["structure emptied"] and not wire.drifted(emptied), emptied


def test_what_counts_as_drift_is_the_set_the_check_exits_on():
    for kind in ("kind changed", "position gone", "shape changed", "rpc not heard", "kind emptied"):
        assert wire.drifted([(kind, "x", "")]), kind
    for kind in ("position added", "kind appeared", "structure emptied"):
        assert not wire.drifted([(kind, "x", "")]), kind


def test_an_optional_position_keeps_its_inner_shape_and_is_compared_inside():
    # Technical review of plan AQ (B1): merging a fixture that had null where another had a structure threw the
    # structure away, so the status cell, the character entry and the recipe arm were never compared.
    base = wire.merge(wire.skeleton([U1, [1, "a"]]), wire.skeleton([U1, None]))

    assert base[1] == {"opt": ["int", "str"]}, base
    assert wire.compare(base, wire.skeleton([U1, None])) == []
    assert wire.compare(base, wire.skeleton([U1, [1, "a"]])) == []
    moved = wire.compare(base, wire.skeleton([U1, [1, 1]]))
    assert [(kind, where) for kind, where, _ in moved] == [("kind changed", "[1][1]")], moved


def test_an_empty_record_list_where_the_baseline_had_records_is_not_drift():
    # A fresh project lists no media: `flow check` on it must not exit 1 for that.
    base = wire.skeleton([None, 1, [_record(U1, "a", 1), _record(U2, "b", 2)]])
    assert wire.compare(base, wire.skeleton([None, 1, []])) == []


def test_later_live_frames_of_one_rpc_are_merged_and_compared():
    base = {"rpcs": {"Zzl0ze": {"view": "project", "shape": wire.skeleton([None, [U1, "t", 3]])}}}
    frames = {"Zzl0ze": [[None, [U1, "t", 3]], [None, [U1, "t", "late"]]]}

    found = wire.compare_frames(base, frames, views=("project",))

    assert [(kind, where) for kind, where, _ in found] == [("kind changed", "Zzl0ze[1][2]")], found


def _listing():
    return json.loads((FIXTURES / "rpc" / "Zzl0ze_character.json").read_text(encoding="utf-8"))["payload"]


def _status_moved(payload):
    for record in payload[2]:
        if record[5][8]:
            record[5][8] = [None, *record[5][8]]
    return payload


def _character_name_moved(payload):
    for entry in payload[5]:
        entry[3].insert(1, None)
    return payload


def _recipe_arm_replaced(payload):
    for record in payload[2]:
        record[5][6][1] = [["x", 1], 7, "y"]
    return payload


@pytest.mark.parametrize("mutate", [_status_moved, _character_name_moved], ids=["status", "character"])
def test_a_move_inside_a_position_a_fixture_once_showed_null_is_drift_on_the_real_baseline(mutate):
    # Two of the three repros of the technical review of plan AQ: each made the parsers read None and the check say
    # nothing, since the merged baseline held only {"any": [...]} at those positions. The third, the recipe arm
    # (details[6][1]), stays compared by kind only: across the records of one fixture it is a list of model tuples
    # in some and a model tuple beside a row list in others, two shapes no single skeleton pins.
    base = wire.baseline()["rpcs"]["Zzl0ze"]["shape"]
    assert not wire.drifted(wire.compare(base, wire.skeleton(_listing()))), (
        "the fixture itself must not drift"
    )

    found = wire.compare(base, wire.skeleton(mutate(_listing())))

    assert wire.drifted(found), found


def test_the_recipe_arm_is_the_known_limit_of_the_wire_check():
    # Said here so the limit is a measured fact and not a surprise: a replacement of the arm by another tuple of
    # the same kinds passes. The paid path reads the arm back through recipe_check on its own clip.
    base = wire.baseline()["rpcs"]["Zzl0ze"]["shape"]
    assert base[2]["*"][5][6][1] == {"opt": {"any": ["list", "records"]}}, base[2]["*"][5][6][1]
    assert wire.compare(base, wire.skeleton(_recipe_arm_replaced(_listing()))) == []


def test_the_frames_of_a_view_are_held_to_every_rpc_the_baseline_recorded_for_it():
    base = {
        "rpcs": {
            "UpteDb": {"view": "grid", "shape": wire.skeleton([[U1, ["t", None, [1, 0]]]])},
            "nzlxg": {"view": "grid", "shape": wire.skeleton([None, 5])},
            "Zzl0ze": {"view": "project", "shape": wire.skeleton([None, [U1]])},
        }
    }
    heard = {"UpteDb": [[[U2, ["u", None, [2, 0]]]]]}

    found = wire.compare_frames(base, heard, views=("grid",))

    assert found == [("rpc not heard", "nzlxg", "expected on the grid view")], found
    assert wire.compare_frames(base, heard | {"nzlxg": [[None, 7]]}, views=("grid",)) == []


def test_a_capture_folder_takes_a_raw_reply_and_no_folder_takes_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv(wire.CAPTURE_ENV, raising=False)
    wire.capture("jwpduf", "raw")
    assert list(tmp_path.iterdir()) == []

    monkeypatch.setenv(wire.CAPTURE_ENV, str(tmp_path / "replies"))
    wire.capture("jwpduf,as29s", "raw body")

    (written,) = list((tmp_path / "replies").iterdir())
    assert written.name.startswith("jwpduf+as29s_") and written.read_text() == "raw body"


def _fixture_payloads():
    out = {}
    for path in sorted((FIXTURES / "rpc").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out.setdefault(path.stem.split("_")[0], []).append(data["payload"])
    return out


def test_the_baseline_in_the_repo_is_what_the_script_builds_from_the_fixtures():
    # Rule 13: the baseline is generated, and this reads the generator, so neither can be typed by hand.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "acceptance"))
    import wire_baseline

    built = wire_baseline.build()
    on_disk = wire.baseline()
    assert set(built["rpcs"]) == set(on_disk["rpcs"]), sorted(built["rpcs"])
    assert built["rpcs"] == on_disk["rpcs"], (
        "flow_wire.json is stale: run scripts/acceptance/wire_baseline.py"
    )
    assert {entry["view"] for entry in on_disk["rpcs"].values()} <= {"grid", "project", "paid"}


@pytest.mark.parametrize("rpcid", sorted(_fixture_payloads()))
def test_every_free_read_fixture_is_compatible_with_the_baseline(rpcid):
    base = wire.baseline()["rpcs"][rpcid]
    for payload in _fixture_payloads()[rpcid]:
        assert not wire.drifted(wire.compare(base["shape"], wire.skeleton(payload))), rpcid


def test_the_redaction_keeps_a_capture_consistent_and_blanks_a_negative_request_id():
    # Technical review of plan AQ (S3): every captured length line reads len(next line) + 2, and a request id can
    # be negative (jwpduf.txt carried one through the first redaction).
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "acceptance"))
    import redact_replies

    body = ')]}\'\n\n9\n[["wrb.fr","jwpduf","[]",null,null,null,"generic"],["af.httprm",736,"-6364764508568145520",90]]\n9\n[["e",4,null,null,1]]\n'
    redacted = redact_replies.redact(body, {})

    lines = redacted.split("\n")
    assert lines[2] == str(len(lines[3]) + 2), lines[:4]
    assert '"af.httprm",736,"0"' in redacted and "-6364764508568145520" not in redacted
    assert redacted.rstrip("\n").endswith(f'[["e",4,null,null,{len(redacted)}]]'), redacted[-60:]
    for name in ("eb1hJf", "jwpduf", "jwpduf_pending"):
        text = (FIXTURES / "replies" / f"{name}.txt").read_text(encoding="utf-8")
        assert "-6364764508568145520" not in text and '"af.httprm",' in text, name


def test_the_build_label_is_the_bundle_key_after_the_language():
    measured = (
        "https://www.gstatic.com/_/mss/boq-labs-ai-sandbox/_/js/"
        "k=boq-labs-ai-sandbox.AiSandboxAngularFrontend.en.Lt87BHY7SpE.2018.O/am=AAAE/d=1/rs=AHGl0Dg/m=base"
    )
    assert version.label_in(["https://www.gstatic.com/x.js", measured]) == "Lt87BHY7SpE.2018.O"
    assert version.label_in(["https://accounts.google.com/a.js", ""]) is None
    # Technical review of plan AQ (S2): an account in another language loads the same bundle under pt-BR or zh-CN.
    for lang in ("pt-BR", "zh-CN", "en_US", "vi"):
        assert version.label_in([measured.replace(".en.", f".{lang}.")]) == "Lt87BHY7SpE.2018.O", lang


def test_a_page_with_a_bundle_sets_the_current_build_and_one_without_leaves_it(monkeypatch):
    import asyncio

    monkeypatch.setattr(version, "current", None)

    class _Page:
        def __init__(self, sources):
            self.sources = sources

        async def evaluate(self, js):
            assert js == version.SCRIPTS_JS
            return self.sources

    class _Broken:
        async def evaluate(self, js):
            raise RuntimeError("navigating")

    assert (
        asyncio.run(
            version.read(_Page(["https://g/k=boq-labs-ai-sandbox.AiSandboxAngularFrontend.vi.Ab12.3.O/"]))
        )
        == "Ab12.3.O"
    )
    assert version.current == "Ab12.3.O"
    assert asyncio.run(version.read(_Page(["https://accounts.google.com/a.js"]))) is None
    assert asyncio.run(version.read(_Broken())) is None
    assert version.current == "Ab12.3.O"
