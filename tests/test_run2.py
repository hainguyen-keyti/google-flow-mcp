import asyncio

from video import gen
from video.story import pipeline, shots2


def test_run2_sends_every_shot_down_the_route_measured_for_it(monkeypatch, tmp_path):
    calls: list[tuple[str, str]] = []

    async def fake_persona(session, project_id, **kwargs):
        return {"entity_id": "e", "name": "Mai", "created": False}

    async def fake_product(session, project_id):
        return {"media_id": "p", "title": "pink_floral_set.png", "uploaded": False}

    async def fake_character(session, project_id, *, character, job_id, **kwargs):
        calls.append(("character", job_id))
        return {"job_id": job_id, "path": str(tmp_path / f"{job_id}.mp4"), "spent": 10, "media_id": "m"}

    async def fake_frames(session, project_id, *, start_name, job_id, **kwargs):
        calls.append(("frames", job_id))
        return {"job_id": job_id, "path": str(tmp_path / f"{job_id}.mp4"), "spent": 10, "media_id": "m"}

    async def fake_product_shot(session, project_id, *, job_id, **kwargs):
        calls.append(("product", job_id))
        return {"job_id": job_id, "path": str(tmp_path / f"{job_id}.mp4"), "spent": 10, "media_id": "m"}

    async def fake_edit(session, project_id, media_id, prompt, **kwargs):
        calls.append(("edit", kwargs.get("job_id", "")))
        return {"outputs": [{"media_id": media_id, "path": str(tmp_path / "edited.mp4")}], "spent": 20}

    async def fake_upload(session, project_id, path):
        return {"media_id": "u"}

    def fake_last_frame(clip, out):
        out.write_bytes(b"png")
        return out

    async def fake_refetch(session, project_id, media_id, stem):
        return stem.with_suffix(".mp4")

    monkeypatch.setattr(pipeline.persona, "ensure", fake_persona)
    monkeypatch.setattr(pipeline.product, "ensure", fake_product)
    monkeypatch.setattr(pipeline.composer, "generate_with_character", fake_character)
    monkeypatch.setattr(pipeline.composer, "generate_from_frame", fake_frames)
    monkeypatch.setattr(pipeline, "_generate_with_product", fake_product_shot)
    monkeypatch.setattr(pipeline.clips, "edit", fake_edit)
    monkeypatch.setattr(pipeline.uploads_mod, "upload", fake_upload)
    monkeypatch.setattr(pipeline.post, "last_frame", fake_last_frame)
    monkeypatch.setattr(pipeline, "_refetch_720", fake_refetch)

    result = asyncio.run(pipeline.run2(None, "P", out_dir=tmp_path, wait=1.0))

    routes = [route for route, _ in calls]
    assert routes == ["character", "frames", "product", "character", "edit", "frames", "frames"]
    assert [job for _, job in calls][:4] == ["tryon2-01", "tryon2-02", "tryon2-03", "tryon2-04"]
    assert len(result["shots"]) == len(shots2.SHOTS)


def test_run2_skips_shots_already_done(monkeypatch, tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    for index in range(1, 7):
        ledger.append(shots2.job_id(index), "done", path=str(tmp_path / "x.mp4"))

    async def explode(*args, **kwargs):
        raise AssertionError("nothing should be generated when every shot is already done")

    async def fake_persona(session, project_id, **kwargs):
        return {"entity_id": "e", "name": "Mai", "created": False}

    async def fake_product(session, project_id):
        return {"media_id": "p"}

    monkeypatch.setattr(pipeline.persona, "ensure", fake_persona)
    monkeypatch.setattr(pipeline.product, "ensure", fake_product)
    monkeypatch.setattr(pipeline.composer, "generate_with_character", explode)
    monkeypatch.setattr(pipeline.composer, "generate_from_frame", explode)
    monkeypatch.setattr(pipeline, "_generate_with_product", explode)

    result = asyncio.run(pipeline.run2(None, "P", out_dir=tmp_path, wait=1.0))
    assert all(s["status"] == "already done" for s in result["shots"])
