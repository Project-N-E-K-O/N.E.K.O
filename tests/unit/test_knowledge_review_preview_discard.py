"""Cancelled or replaced previews delete only their own two files."""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.memory_server import knowledge_routes
from knowledge.models import KnowledgePackError
from knowledge.service import KnowledgeService
from tests.unit.test_knowledge_unified_core import _finished
from utils.http import knowledge_proxy


pytestmark = pytest.mark.unit


def _preview_files(root, preview_id):
    return sorted(path.name for path in (root / "sources").rglob(f"previews/{preview_id}.*"))


@pytest.fixture
async def service(tmp_path):
    value = KnowledgeService(tmp_path)
    await value.start()
    try:
        yield value
    finally:
        await value.stop()


async def test_discard_deletes_only_that_uncommitted_preview(service, tmp_path):
    created = await service.preview_source("a.txt", b"Private first draft.")
    first = created["preview"]
    second = (await service.preview_source("b.txt", b"Private second draft."))["preview"]
    assert _preview_files(tmp_path, first["preview_id"]) == [first["preview_id"] + ".json", first["preview_id"] + ".original"]
    assert await service.discard_preview(first["preview_id"]) == {"deleted": True}
    assert _preview_files(tmp_path, first["preview_id"]) == []
    assert len(_preview_files(tmp_path, second["preview_id"])) == 2
    with pytest.raises(KnowledgePackError, match="preview_not_found"):
        await service.commit_source(first["preview_id"], first["sha256"])
    # Repeating a cancel is harmless; arbitrary paths are never accepted.
    assert await service.discard_preview(first["preview_id"]) == {"deleted": False}
    with pytest.raises(KnowledgePackError, match="invalid_request"):
        await service.discard_preview("../" + second["preview_id"])


async def test_committed_preview_and_frozen_inputs_are_kept(service, tmp_path):
    value = (await service.preview_source("a.txt", b"Lighthouse reference."))["preview"]
    result = await service.commit_source(value["preview_id"], value["sha256"])
    assert (await _finished(service, result))["state"] == "active"
    originals = sorted((tmp_path / "sources").rglob("original"))
    assert await service.discard_preview(value["preview_id"]) == {"deleted": False}
    assert len(_preview_files(tmp_path, value["preview_id"])) == 2
    assert sorted((tmp_path / "sources").rglob("original")) == originals and originals
    assert (tmp_path / "jobs" / (result["job_id"] + ".pack.json")).exists()


async def test_discard_waits_for_an_in_flight_commit_and_keeps_its_preview(service, tmp_path, monkeypatch):
    value = (await service.preview_source("a.txt", b"Concurrent lighthouse reference."))["preview"]
    frozen, release = asyncio.Event(), asyncio.Event()
    project = service._project_sources

    async def slow_project(*args, **kwargs):
        # The preview is already frozen into a version; hold the write lock here.
        frozen.set()
        await release.wait()
        return await project(*args, **kwargs)

    monkeypatch.setattr(service, "_project_sources", slow_project)
    commit = asyncio.create_task(service.commit_source(value["preview_id"], value["sha256"]))
    await frozen.wait()
    discard = asyncio.create_task(service.discard_preview(value["preview_id"]))
    await asyncio.sleep(0.05)
    assert not discard.done()
    assert len(_preview_files(tmp_path, value["preview_id"])) == 2
    release.set()
    result = await commit
    assert await discard == {"deleted": False}
    assert len(_preview_files(tmp_path, value["preview_id"])) == 2
    assert (await _finished(service, result))["state"] == "active"


async def test_commit_after_a_completed_discard_cannot_freeze_deleted_input(service, tmp_path):
    value = (await service.preview_source("a.txt", b"Cancelled before commit."))["preview"]
    results = await asyncio.gather(service.discard_preview(value["preview_id"]),
                                    service.commit_source(value["preview_id"], value["sha256"]),
                                    return_exceptions=True)
    assert results[0] == {"deleted": True}
    assert isinstance(results[1], KnowledgePackError) and results[1].reason == "preview_not_found"
    assert not list((tmp_path / "sources").rglob("versions/*.json"))
    assert service.list_jobs() == []


async def test_discard_route_and_both_proxy_hops_accept_only_a_preview_identity(service, monkeypatch):
    assert knowledge_proxy.WRITE_PATHS["sources/preview/discard"] == knowledge_proxy.JSON_BODY_MAX_BYTES
    monkeypatch.setattr(knowledge_routes, "_service", service)
    value = (await service.preview_source("a.txt", b"Route cancel."))["preview"]
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        bad = await client.post("/internal/knowledge/sources/preview/discard", json={"preview_id": 1})
        good = await client.post("/internal/knowledge/sources/preview/discard", json={"preview_id": value["preview_id"]})
    assert bad.status_code == 400 and bad.json()["reason"] == "invalid_request"
    assert good.status_code == 200 and good.json()["ok"] is True and good.json()["deleted"] is True


async def test_commit_after_an_interrupted_discard_reports_a_missing_preview(service, tmp_path):
    value = (await service.preview_source("a.txt", b"Half-deleted preview."))["preview"]
    # An interrupted discard leaves the JSON but has already removed the original.
    next((tmp_path / "sources").rglob(f"previews/{value['preview_id']}.original")).unlink()
    with pytest.raises(KnowledgePackError, match="preview_not_found"):
        await service.commit_source(value["preview_id"], value["sha256"])
    assert await service.discard_preview(value["preview_id"]) == {"deleted": True}
    assert _preview_files(tmp_path, value["preview_id"]) == []
