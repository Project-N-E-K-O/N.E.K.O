"""Restoring retained source history cannot bypass disclosure confirmation."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from app.memory_server import knowledge_routes
from knowledge.service import KnowledgeService
from tests.knowledge_fixtures import _finished


pytestmark = pytest.mark.unit_fast
_PRIVATE = "amberlighthousesecret"


@pytest.fixture
async def rejected_history(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        safe = (await service.preview_source("safe.txt", b"An ordinary public reference."))["preview"]
        initial = await service.commit_source(safe["preview_id"], safe["sha256"])
        assert (await _finished(service, initial))["state"] == "active"
        pack_id = initial["pack_id"]
        await service.set_pack_enabled(pack_id, True)
        await service.set_pack_auto_context(pack_id, True)
        private = (await service.preview_source(
            "private.txt", f"{_PRIVATE} should only be disclosed after acknowledgement.".encode(),
            pack_id=pack_id,
        ))["preview"]
        rejected = await service.commit_source(private["preview_id"], private["sha256"])
        outcome = await _finished(service, rejected)
        assert (outcome["state"], outcome["reason"]) == ("failed", "source_confirmation_required")
        assert (await service.query(query=_PRIVATE))["result"] == "miss"
        versions = (await service.source_versions(pack_id, private["doc_id"]))["versions"]
        assert len(versions) == 1 and versions[0]["version"] == 1 and not versions[0]["current"]
        source_root = service.root / "sources" / pack_id
        retained = {path: path.read_bytes() for path in source_root.rglob("*") if path.is_file()}
        assert retained  # The rejected original and immutable history really exist.
        yield service, pack_id, private["doc_id"], retained
    finally:
        await service.stop()


@pytest.mark.parametrize("entrypoint", ["service", "http"])
async def test_never_activated_history_requires_confirmation_before_retrieval(rejected_history, monkeypatch, entrypoint):
    service, pack_id, doc_id, retained = rejected_history
    before = service._registry.packs[pack_id]
    if entrypoint == "http":
        monkeypatch.setattr(knowledge_routes, "_service", service)
        app = FastAPI()
        app.include_router(knowledge_routes.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/internal/knowledge/sources/restore", json={
                "pack_id": pack_id, "doc_id": doc_id, "version": 1,
            })
        assert response.status_code == 200
        queued = response.json()
    else:
        queued = await service.restore_source(pack_id, doc_id, 1)
    outcome = await _finished(service, queued)
    retrieval = await service.query(query=_PRIVATE)
    assert (outcome["state"], outcome["reason"], retrieval["result"]) == (
        "failed", "source_confirmation_required", "miss",
    )
    assert service._registry.packs[pack_id] == before
    frozen = json.loads((service.root / "jobs" / (queued["job_id"] + ".json")).read_text())["frozen_input"]
    assert frozen["source_confirmation_required"] and frozen["enabled_confirmed"] is False
    assert all(path.read_bytes() == raw for path, raw in retained.items())
    # The same rejected history can be restored after a fresh, explicit
    # acknowledgement, through either public entry point without changing policy.
    if entrypoint == "http":
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/internal/knowledge/sources/restore", json={
                "pack_id": pack_id, "doc_id": doc_id, "version": 1, "enabled_confirmed": True,
            })
        assert response.status_code == 200
        confirmed = response.json()
    else:
        confirmed = await service.restore_source(pack_id, doc_id, 1, enabled_confirmed=True)
    assert (await _finished(service, confirmed))["state"] == "active"
    assert (await service.query(query=_PRIVATE))["result"] == "matched"
    record = service._registry.packs[pack_id]
    assert record.enabled == before.enabled and record.auto_context == before.auto_context
    assert all(path.read_bytes() == raw for path, raw in retained.items())


@pytest.mark.parametrize("confirmed", [False, True])
async def test_restore_rechecks_consent_when_another_window_enables_the_queued_pack(rejected_history, confirmed):
    service, pack_id, doc_id, retained = rejected_history
    await service.set_pack_enabled(pack_id, False)
    before = service._registry.packs[pack_id]
    # Pause only the task consumer, outside the mutation lock, so another
    # window can change the real pack policy after restore has been admitted.
    runner = service._tasks[0]
    runner.cancel()
    await asyncio.gather(runner, return_exceptions=True)
    release = asyncio.Event()

    async def delayed_runner():
        await release.wait()
        await service._job_loop()

    service._tasks[0] = asyncio.create_task(delayed_runner(), name="knowledge-import-jobs")
    try:
        options = {"enabled_confirmed": True} if confirmed else {}
        queued = await service.restore_source(pack_id, doc_id, 1, **options)
        assert queued["state"] == "queued"
        await service.set_pack_enabled(pack_id, True)
        release.set()
        outcome = await _finished(service, queued)
        retrieval = await service.query(query=_PRIVATE)
        expected = ("active", "", "matched") if confirmed else ("failed", "source_confirmation_required", "miss")
        assert (outcome["state"], outcome["reason"], retrieval["result"]) == expected
        record = service._registry.packs[pack_id]
        assert record.enabled and record.auto_context == before.auto_context
        assert (record.pack_sha256 == before.pack_sha256) is (not confirmed)
        assert all(path.read_bytes() == raw for path, raw in retained.items())
    finally:
        release.set()


async def test_disabled_pack_can_restore_without_confirmation_and_stays_disabled(rejected_history):
    service, pack_id, doc_id, retained = rejected_history
    await service.set_pack_enabled(pack_id, False)
    before = service._registry.packs[pack_id]
    queued = await service.restore_source(pack_id, doc_id, 1)
    assert (await _finished(service, queued))["state"] == "active"
    record = service._registry.packs[pack_id]
    assert not record.enabled and record.auto_context == before.auto_context
    assert record.pack_sha256 != before.pack_sha256
    assert (await service.query(query=_PRIVATE))["result"] == "miss"
    assert all(path.read_bytes() == raw for path, raw in retained.items())


async def test_restore_route_requires_a_real_bool_and_forwards_the_default(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    seen = []

    async def restore(pack_id, doc_id, version, **options):
        seen.append((pack_id, doc_id, version, options))
        return {"ok": True}

    monkeypatch.setattr(service, "restore_source", restore)
    monkeypatch.setattr(knowledge_routes, "_service", service)
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    payload = {"pack_id": "personal.reference", "doc_id": "doc", "version": 1}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            for value in ("yes", 1, None, []):
                response = await client.post("/internal/knowledge/sources/restore", json={
                    **payload, "enabled_confirmed": value,
                })
                assert response.status_code == 400 and not seen
            for options in ({}, {"enabled_confirmed": False}, {"enabled_confirmed": True}):
                response = await client.post("/internal/knowledge/sources/restore", json={**payload, **options})
                assert response.status_code == 200 and response.json()["ok"]
        assert seen == [("personal.reference", "doc", 1, {"enabled_confirmed": value})
                        for value in (False, False, True)]
    finally:
        await service.stop()
