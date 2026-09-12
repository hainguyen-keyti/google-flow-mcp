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

    async def fake_extend(session, project_id, media_id, prompt, **kwargs):
        calls.append(("extend", kwargs.get("job_id", "")))
        return {"outputs": [{"media_id": f"{media_id}+", "path": str(tmp_path / "ext.mp4")}], "spent": 10}

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
    monkeypatch.setattr(pipeline.clips, "extend", fake_extend)
    monkeypatch.setattr(pipeline.uploads_mod, "upload", fake_upload)
    monkeypatch.setattr(pipeline.post, "last_frame", fake_last_frame)
    monkeypatch.setattr(pipeline, "_refetch_720", fake_refetch)

    result = asyncio.run(pipeline.run2(None, "P", out_dir=tmp_path, wait=1.0))

    routes = [route for route, _ in calls]
    assert routes == [
        "character",
        "frames",
        "frames",
        "product",
        "character",
        "edit",
        "extend",
        "extend",
        "extend",
    ]
    jobs = [job for _, job in calls]
    # Hands break on the shots that touch fabric, so each of those is shot twice and judged by eye.
    assert "tryon2-02b" in jobs and "tryon2-05b" in jobs
    assert jobs[:4] == ["tryon2-01", "tryon2-02", "tryon2-02b", "tryon2-03"]
    assert len(result["shots"]) == len(shots2.SHOTS) + shots2.EXTRA_TAKES


def test_run2_continues_the_chain_from_the_first_take_not_the_spare(monkeypatch, tmp_path):
    starts: list[str] = []
    sources: list[tuple[str, str]] = []

    async def fake_persona(session, project_id, **kwargs):
        return {"entity_id": "e", "name": "Mai", "created": False}

    async def fake_product(session, project_id):
        return {"media_id": "p"}

    async def fake_character(session, project_id, *, character, job_id, **kwargs):
        return {"job_id": job_id, "path": str(tmp_path / f"{job_id}.mp4"), "spent": 10, "media_id": "m"}

    async def fake_frames(session, project_id, *, start_name, job_id, **kwargs):
        starts.append(f"{job_id}<-{start_name}")
        return {"job_id": job_id, "path": str(tmp_path / f"{job_id}.mp4"), "spent": 10, "media_id": "m"}

    async def fake_product_shot(session, project_id, *, job_id, **kwargs):
        return {"job_id": job_id, "path": str(tmp_path / f"{job_id}.mp4"), "spent": 10, "media_id": "m"}

    async def fake_edit(session, project_id, media_id, prompt, **kwargs):
        return {"outputs": [{"media_id": media_id, "path": str(tmp_path / "edited.mp4")}], "spent": 20}

    async def fake_upload(session, project_id, path):
        return {"media_id": "u"}

    def fake_last_frame(clip, out):
        out.write_bytes(b"png")
        return out

    async def fake_refetch(session, project_id, media_id, stem):
        return stem.with_suffix(".mp4")

    async def fake_extend(session, project_id, media_id, prompt, **kwargs):
        job = kwargs.get("job_id", "")
        sources.append((job, media_id))
        return {"outputs": [{"media_id": f"{media_id}+", "path": str(tmp_path / "ext.mp4")}], "spent": 10}

    monkeypatch.setattr(pipeline.persona, "ensure", fake_persona)
    monkeypatch.setattr(pipeline.product, "ensure", fake_product)
    monkeypatch.setattr(pipeline.composer, "generate_with_character", fake_character)
    monkeypatch.setattr(pipeline.composer, "generate_from_frame", fake_frames)
    monkeypatch.setattr(pipeline, "_generate_with_product", fake_product_shot)
    monkeypatch.setattr(pipeline.clips, "edit", fake_edit)
    monkeypatch.setattr(pipeline.clips, "extend", fake_extend)
    monkeypatch.setattr(pipeline.uploads_mod, "upload", fake_upload)
    monkeypatch.setattr(pipeline.post, "last_frame", fake_last_frame)
    monkeypatch.setattr(pipeline, "_refetch_720", fake_refetch)

    asyncio.run(pipeline.run2(None, "P", out_dir=tmp_path, wait=1.0))

    # Both takes of a shot start from the same frame, and only the frames route pins a still at all.
    assert starts == ["tryon2-02<-tryon2-02_start.png", "tryon2-02b<-tryon2-02b_start.png"]
    # The shots after the reveal continue the edited clip, which shares the base clip's media id.
    assert sources == [("tryon2-05", "m"), ("tryon2-05b", "m"), ("tryon2-06", "m+")]


def test_run2_skips_shots_already_done(monkeypatch, tmp_path):
    ledger = gen.Ledger(tmp_path / "ledger.jsonl")
    for index, shot in enumerate(shots2.SHOTS, start=1):
        for job in pipeline.take_ids(shots2.job_id(index), shot.hands_risk):
            ledger.append(job, "done", path=str(tmp_path / "x.mp4"))

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
