"""Protect publication ownership, admission capacity and source listing identity."""

import asyncio
import json
import threading

import pytest

from knowledge import archive, service as module
from knowledge.models import KnowledgePackError
from knowledge.service import KnowledgeService
from tests.knowledge_fixtures import _finished, _import, _manual_index, _pack
from tests.knowledge_archive_fixtures import _prepare_export_vectors, make_archive


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("zip_input", [False, True])
async def test_local_file_replacement_requires_explicit_reactivation(tmp_path, zip_input):
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    try:
        await _import(service, _pack("imp.example", entries=[{"title": "T", "content": "Previous content."}]))
        await service.set_pack_enabled("imp.example", True)
        raw = make_archive(tmp_path / "replacement.zip") if zip_input else json.dumps(
            _pack("imp.example", entries=[{"title": "T", "content": "New private content."}])).encode()
        job = await service.import_pack(raw, _options={"origin": "imported", "enabled": False})
        assert (await _finished(service, job))["state"] == "active"
        assert not service._registry.packs["imp.example"].enabled
        assert not service.availability()["tool_available"]
    finally:
        await service.stop()


async def test_next_replan_reconciles_a_previous_partial_write_before_capacity(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    await _manual_index(service)
    try:
        for pack_id in ("first", "second"):
            await _import(service, _pack(pack_id, entries=[{"title": "T", "content": "abcdef " * 40}]))
        original = module.save_registry
        monkeypatch.setattr(service, "_chunk_fit", lambda: lambda text: len(text) <= 24)
        def failed_flush(*args):
            raise OSError("synthetic registry flush failure")
        monkeypatch.setattr(module, "save_registry", failed_flush)
        with pytest.raises(OSError):
            await service._replan_vector_inputs(["first"])
        actual = service._store.chunk_stats(None)
        assert actual["first"]["total"] > service._registry.packs["first"].chunks
        monkeypatch.setattr(module, "MAX_TOTAL_CHUNKS", sum(row["total"] for row in actual.values()))
        monkeypatch.setattr(module, "save_registry", original)
        with pytest.raises(KnowledgePackError, match="capacity_chunks"):
            await service._replan_vector_inputs(["second"])
        assert not service._pending_chunk_reconcile
        assert service._registry.packs["first"].chunks == actual["first"]["total"]
        assert service._store.chunk_stats(None)["second"] == actual["second"]
    finally:
        await service.stop()


async def test_stopping_after_export_gate_preserves_the_published_result(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    entered, release = threading.Event(), threading.Event()
    stopped = None
    original = archive.export_archive
    def pause_after_gate(*args, **kwargs):
        gate = kwargs["commit_gate"]
        def paused():
            allowed = gate()
            if allowed:
                entered.set()
                assert release.wait(5)
            return allowed
        return original(*args, **{**kwargs, "commit_gate": paused})
    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        monkeypatch.setattr(archive, "export_archive", pause_after_gate)
        queued = await service.export_pack("demo-memes")
        assert await asyncio.to_thread(entered.wait, 2)
        stopped = asyncio.create_task(service.stop())
        runner = next(task for task in service._tasks if task.get_name() == "knowledge-import-jobs")
        await asyncio.wait_for(asyncio.gather(runner, return_exceptions=True), 2)
        release.set()
        await asyncio.wait_for(stopped, 3)
        job = service._jobs[queued["job_id"]]
        assert job.state == "active" and job.published and job.result
        assert service.artifact_path(job.result["artifact_id"]).is_file()
        persisted = json.loads((service.root / "jobs" / (job.job_id + ".json")).read_text())
        assert persisted["state"] == "active" and persisted["result"] == job.result
    finally:
        release.set()
        if stopped is not None:
            await asyncio.gather(stopped, return_exceptions=True)
        await service.stop()


@pytest.mark.parametrize("record_written", [False, True])
async def test_failed_admission_only_reclaims_an_unowned_frozen_input(tmp_path, monkeypatch, record_written):
    service = KnowledgeService(tmp_path)
    await service.start()
    original = service._persist_job
    async def fail(job):
        if record_written:
            await original(job)
        raise OSError("synthetic first persistence failure")
    monkeypatch.setattr(service, "_persist_job", fail)
    try:
        with pytest.raises(OSError, match="first persistence"):
            await service.import_pack(json.dumps(_pack()).encode())
        assert not service.list_jobs()
        frozen = list((tmp_path / "jobs").glob("*.pack.json"))
        assert bool(frozen) is record_written
        assert not list((tmp_path / module.STAGING_DIR).glob("*.json"))
    finally:
        await service.stop()


async def test_source_capability_only_applies_to_the_validated_pack_version(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    entered, release = threading.Event(), threading.Event()
    listing = None
    try:
        preview = (await service.preview_source("notes.txt", b"Original local source."))["preview"]
        imported = await _finished(service, await service.commit_source(preview["preview_id"], preview["sha256"]))
        original = service._listed_sources_managed
        def paused(pack_id):
            managed = original(pack_id)
            entered.set()
            assert release.wait(5)
            return managed
        monkeypatch.setattr(service, "_listed_sources_managed", paused)
        listing = asyncio.create_task(service.list_packs())
        assert await asyncio.to_thread(entered.wait, 2)
        await _import(service, _pack(imported["pack_id"], entries=[{"title": "T", "content": "Offline replacement."}]))
        release.set()
        pack, = await asyncio.wait_for(listing, 2)
        assert pack["sources_managed"] is False
    finally:
        release.set()
        if listing is not None:
            await asyncio.gather(listing, return_exceptions=True)
        await service.stop()
