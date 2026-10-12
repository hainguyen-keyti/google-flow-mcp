"""The repo's gflow wrapper: what it changes in gflow before running it."""

import json
from pathlib import Path

import pytest
from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.transports import batchexecute, migrated_composer

from video import gflow_cli

REQUESTS = Path(__file__).parent / "fixtures" / "requests"
# out/ar/capture_image_bodies.py maps a body's references to stand-ins before any other uuid.
REF = "00000000-0000-4000-8000-000000000001"
UUID1 = "e61f5b7f-5b59-4b25-bb83-8640d6807441"
UUID2 = "e61f5b7f-5b59-4b25-bb83-8640d6807442"
UUID3 = "e61f5b7f-5b59-4b25-bb83-8640d6807443"


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(migrated_composer, "_image_body_problem", migrated_composer._image_body_problem)
    gflow_cli.accept_nano_banana_2_1()
    return migrated_composer._image_body_problem


def _captured(kind):
    # The ogiZ0b bodies Flow received on 2026-10-12 (one t2i, one i2i, nano2, count 1), form-decoded as gflow hands
    # them to its check, captured by out/ar/capture_image_bodies.py with the `at` value, tokens and uuids replaced.
    return (REQUESTS / f"ogiZ0b_{kind}.txt").read_text(encoding="utf-8")


def _with_requests(body, change):
    start = body.index("f.req=") + len("f.req=")
    outer, end = json.JSONDecoder().raw_decode(body, start)
    inner = json.loads(outer[0][0][1])
    change(inner[1])
    outer[0][0][1] = json.dumps(inner, separators=(",", ":"))
    return body[:start] + json.dumps(outer, separators=(",", ":")) + body[end:]


@pytest.mark.parametrize(("kind", "references"), [("t2i", ()), ("i2i", (REF,))])
def test_the_bodies_flow_received_for_a_nano_banana_2_request_are_the_run_asked_for(
    patched, kind, references
):
    # Flow's menu offers "Nano Banana 2.1" in place of "Nano Banana 2" (2026-10-07), gflow's matcher picks it, and the
    # body names it BELUGA as the sixth field of each request, so gflow 0.78.0 refuses every nano2 image as "persisted
    # settings" (WireFormatError, exit 7). The check of 2026-10-08 looked for `[\"BELUGA\"`, a layout never measured.
    assert patched(_captured(kind), references, ImageModel.NARWHAL) is None


def test_another_model_in_the_model_field_is_refused_whatever_the_prompt_says(patched):
    # Review 2026-10-08 (D8): a check that matched the word anywhere passed a prompt that mentions a beluga.
    body = _with_requests(_captured("t2i"), lambda requests: requests[0].__setitem__(5, "GEM_PIX_2"))
    body = body.replace("soft daylight", "soft daylight, a BELUGA toy beside it")

    assert "BELUGA" in body
    assert "NARWHAL" in patched(body, (), ImageModel.NARWHAL)


def test_every_request_of_the_body_has_to_name_the_model(patched):
    def second_request_on_another_model(requests):
        requests.append(list(requests[0]))
        requests[1][5] = "GEM_PIX_2"

    body = _with_requests(_captured("t2i"), second_request_on_another_model)

    assert "NARWHAL" in patched(body, (), ImageModel.NARWHAL)


def test_a_body_in_a_layout_never_measured_goes_to_gflows_own_check(patched):
    # The hand-typed body the check of 2026-10-08 was written against; Flow's bodies do not look like this.
    body = 'f.req=[["ogiZ0b","[\\\\"BELUGA\\\\"]"]]'

    assert "NARWHAL" in patched(body, (), ImageModel.NARWHAL)


@pytest.mark.parametrize(
    ("body", "references", "says"),
    [
        (_captured("i2i"), ("00000000-0000-4000-8000-000000000099",), "missing uploaded reference"),
        ("", (), "could not be read"),
    ],
    ids=["a reference missing", "no body"],
)
def test_every_other_check_of_the_body_still_refuses(patched, body, references, says):
    assert says in patched(body, references, ImageModel.NARWHAL)


def test_another_model_is_checked_as_gflow_checks_it(patched):
    assert "GEM_PIX_2" in patched(_captured("t2i"), (), ImageModel.GEM_PIX_2)


def _record(version):
    return [UUID1, UUID2, UUID3, version, None, [None, None, None, None, None, None, None, None, [6]]]


def test_the_null_version_patch_accepts_cae_and_null_and_nothing_else(monkeypatch):
    # Measured 2026-10-07: Flow's submit replies carry null where "CAE" stood, and gflow 0.78.0 refused every reply
    # ("no generation record"). "CAI" and "CAM" are the versions of a clip's edits (measured 2026-09-28): gflow keeps
    # the first record it finds, so an edit record ahead of the job's own would stand in for the job.
    monkeypatch.setattr(batchexecute, "_is_record", batchexecute._is_record)
    gflow_cli.accept_generation_record_null_version()

    assert batchexecute._is_record(_record("CAE")) is True
    assert batchexecute._is_record(_record(None)) is True
    assert batchexecute._is_record(_record("CAM")) is False
    assert batchexecute._is_record(_record("CAX")) is False
    assert batchexecute._is_record(_record("INVALID")) is False
    assert batchexecute._is_record(_record(None)[:5]) is False


def test_the_patch_is_gone_from_gflow_once_its_test_has_run():
    # A patch applied by a test and never removed reaches every later test of the process (review of plan AQ).
    assert not getattr(batchexecute._is_record, "accepts_null_version", False)


def test_the_wrapper_applies_it_before_gflow_runs(monkeypatch):
    applied = []
    monkeypatch.setattr(gflow_cli, "accept_nano_banana_2_1", lambda: applied.append("nano2"))
    monkeypatch.setattr(
        gflow_cli, "accept_generation_record_null_version", lambda: applied.append("gen_null")
    )
    monkeypatch.setattr(gflow_cli, "main", lambda: applied.append("main"))
    monkeypatch.setattr(gflow_cli.offscreen, "install", lambda: None)

    gflow_cli.run(["python", "image", "i2i", "a prompt"])

    assert applied == ["nano2", "gen_null", "main"]
