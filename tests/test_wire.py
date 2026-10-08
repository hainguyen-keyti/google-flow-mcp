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


def test_a_null_now_where_the_baseline_had_a_value_is_not_drift():
    base = wire.skeleton([U1, "title", 3])
    assert wire.compare(base, wire.skeleton([U1, None, 3])) == []


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


def test_the_build_label_is_the_bundle_key_after_the_language():
    measured = (
        "https://www.gstatic.com/_/mss/boq-labs-ai-sandbox/_/js/"
        "k=boq-labs-ai-sandbox.AiSandboxAngularFrontend.en.Lt87BHY7SpE.2018.O/am=AAAE/d=1/rs=AHGl0Dg/m=base"
    )
    assert version.label_in(["https://www.gstatic.com/x.js", measured]) == "Lt87BHY7SpE.2018.O"
    assert version.label_in(["https://accounts.google.com/a.js", ""]) is None


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
