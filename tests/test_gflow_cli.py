"""The repo's gflow wrapper: what it changes in gflow before running it."""

import pytest
from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.transports import batchexecute, migrated_composer

from video import gflow_cli

REF = "e61f5b7f-5b59-4b25-bb83-8640d6807443"
UUID1 = "e61f5b7f-5b59-4b25-bb83-8640d6807441"
UUID2 = "e61f5b7f-5b59-4b25-bb83-8640d6807442"
UUID3 = "e61f5b7f-5b59-4b25-bb83-8640d6807443"


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(migrated_composer, "_image_body_problem", migrated_composer._image_body_problem)
    gflow_cli.accept_nano_banana_2_1()
    return migrated_composer._image_body_problem


def test_a_nano_banana_2_request_whose_body_names_2_1_is_the_run_asked_for(patched):
    # Measured 2026-10-07 (out/i2i_probe): Flow's menu offers "Nano Banana 2.1" in place of "Nano Banana 2", gflow's
    # matcher picks it, and the ogiZ0b body names BELUGA, so gflow 0.78.0 refused every nano2 image as "persisted
    # settings" (WireFormatError, exit 7).
    body = f'f.req=[["ogiZ0b","[\\\\"BELUGA\\\\",\\\\"{REF}\\\\"]"]]'

    assert patched(body, (REF,), ImageModel.NARWHAL) is None


@pytest.mark.parametrize(
    ("body", "says"),
    [
        (f'f.req=[["ogiZ0b","[\\\\"GEM_PIX_2\\\\",\\\\"{REF}\\\\"]"]]', "NARWHAL"),
        ('f.req=[["ogiZ0b","[\\\\"BELUGA\\\\"]"]]', "missing uploaded reference"),
        ("", "could not be read"),
    ],
    ids=["another model", "the reference missing", "no body"],
)
def test_every_other_check_of_the_body_still_refuses(patched, body, says):
    assert says in patched(body, (REF,), ImageModel.NARWHAL)


def test_another_model_is_checked_as_gflow_checks_it(patched):
    body = f'f.req=[["ogiZ0b","[\\\\"BELUGA\\\\",\\\\"{REF}\\\\"]"]]'

    assert "GEM_PIX_2" in patched(body, (REF,), ImageModel.GEM_PIX_2)


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
