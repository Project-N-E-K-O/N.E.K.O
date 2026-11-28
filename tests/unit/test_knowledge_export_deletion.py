"""Delete only explicit completed exports without losing referenced recovery archives.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import threading

import pytest

from services.knowledge.models import KnowledgePackError
from services.knowledge.service import KnowledgeService
from tests.knowledge_archive_fixtures import _prepare_export_vectors
from tests.knowledge_fixtures import _finished, _import, _pack

pytestmark = pytest.mark.unit


async def test_delete_export_removes_its_record_and_zip_without_losing_the_library(tmp_path):
    """The explicit delete cleans one output, never its source pack or history."""
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        value = (await service.preview_source("notes.txt", b"Lighthouse reference.", name="References"))["preview"]
        await _finished(service, await service.commit_source(value["preview_id"], value["sha256"]))
        pack_id = (await service.list_packs())[0]["pack_id"]
        _prepare_export_vectors(service, pack_id)
        exported = await _finished(service, await service.export_pack(pack_id))
        path = service.artifact_path(exported["result"]["artifact_id"])
        sources = {item: item.read_bytes() for item in (tmp_path / "sources").rglob("*") if item.is_file()}
        registry = (tmp_path / "registry.json").read_bytes()
        response = await service.delete_export(exported["job_id"])
        assert response == {"ok": True, "job_id": exported["job_id"], "artifact_retained": False}
        assert not path.exists() and not (tmp_path / "jobs" / (exported["job_id"] + ".json")).exists()
        assert not any(job["job_id"] == exported["job_id"] for job in service.list_jobs())
        assert (tmp_path / "registry.json").read_bytes() == registry
        assert all(item.read_bytes() == raw for item, raw in sources.items())
    finally:
        await service.stop()
    reopened = KnowledgeService(tmp_path)
    await reopened.start()
    try:
        assert not any(job["job_id"] == exported["job_id"] for job in reopened.list_jobs())
        assert any(pack["pack_id"] == pack_id for pack in await reopened.list_packs())
    finally:
        await reopened.stop()


@pytest.mark.parametrize("owner", ["registry", "other_export", "evicted_import"])
async def test_delete_export_keeps_zip_referenced_by_library_or_retained_tasks(tmp_path, owner):
    """Even a disk-only recovery task protects its immutable input archive."""
    from dataclasses import replace
    from services.knowledge.service import ImportJob
    from services.knowledge.registry import save_registry, utc_now

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        exported = await _finished(service, await service.export_pack("demo-memes"))
        digest = exported["result"]["artifact_id"]
        path = service.artifact_path(digest)
        before = path.read_bytes()
        if owner == "registry":
            service._registry = service._registry.with_pack(replace(service._registry.packs["demo-memes"], archive_sha256=digest))
            save_registry(tmp_path, service._registry)
        else:
            stamp = utc_now()
            other = ImportJob("d" * 32, "demo-memes", "failed", stamp, stamp)
            if owner == "other_export":
                other.kind, other.state, other.result = "pack_export", "active", dict(exported["result"])
            else:
                other.archive_sha256 = digest
            service._remember_job(other)
            await service._persist_job(other)
            if owner == "evicted_import":
                service._jobs.pop(other.job_id)
        assert (await service.delete_export(exported["job_id"]))["artifact_retained"] is True
        assert path.read_bytes() == before
        assert not (tmp_path / "jobs" / (exported["job_id"] + ".json")).exists()
    finally:
        await service.stop()


@pytest.mark.parametrize("failed_part", ["artifact", "record"])
async def test_failed_export_delete_stays_visible_and_can_be_retried(tmp_path, monkeypatch, failed_part):
    """No I/O failure may be reported as successful durable deletion."""
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        exported = await _finished(service, await service.export_pack("demo-memes"))
        artifact = service.artifact_path(exported["result"]["artifact_id"])
        record = tmp_path / "jobs" / (exported["job_id"] + ".json")
        failed = artifact if failed_part == "artifact" else record
        unlink = Path.unlink

        def refuse(path, *args, **kwargs):
            if path == failed:
                raise OSError("synthetic deletion failure")
            return unlink(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", refuse)
            with pytest.raises(OSError, match="synthetic deletion failure"):
                await service.delete_export(exported["job_id"])
        assert any(job["job_id"] == exported["job_id"] for job in service.list_jobs()) and record.exists()
        assert artifact.exists() is (failed_part == "artifact")
        assert (await service.delete_export(exported["job_id"]))["ok"]
        assert not artifact.exists() and not record.exists()
    finally:
        await service.stop()


async def test_export_delete_cannot_be_revived_by_a_late_worker_flush(tmp_path, monkeypatch):
    """Delete after active is visible but before the worker persists its end."""
    service = KnowledgeService(tmp_path)
    await service.start()
    reached, resume, flushed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = service._persist_job

    async def pause(job):
        if job.kind == "pack_export" and job.state == "active":
            reached.set()
            await resume.wait()
        await original(job)
        if job.kind == "pack_export" and job.state == "active":
            flushed.set()

    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        monkeypatch.setattr(service, "_persist_job", pause)
        exported = await service.export_pack("demo-memes")
        await asyncio.wait_for(reached.wait(), 3)
        await service.delete_export(exported["job_id"])
        resume.set()
        await asyncio.wait_for(flushed.wait(), 3)
        assert not (tmp_path / "jobs" / (exported["job_id"] + ".json")).exists()
    finally:
        resume.set()
        await service.stop()
    assert not (tmp_path / "jobs" / (exported["job_id"] + ".json")).exists()


async def test_export_delete_waits_until_archive_import_owns_its_recovery_reference(tmp_path, monkeypatch):
    """Deletion cannot unlink between retaining a ZIP and admitting its import."""
    from services.knowledge import service as module

    service = KnowledgeService(tmp_path)
    await service.start()
    reached, resume = threading.Event(), threading.Event()
    original = module.canonical_pack_bytes
    importing = deleting = None

    def pause(pack):
        reached.set()
        if not resume.wait(5):
            raise TimeoutError("archive admission coordination timed out")
        return original(pack)

    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        exported = await _finished(service, await service.export_pack("demo-memes"))
        artifact = service.artifact_path(exported["result"]["artifact_id"])
        # Force the re-import through a job instead of the unchanged-pack fast path.
        await service.remove_pack("demo-memes")
        monkeypatch.setattr(module, "canonical_pack_bytes", pause)
        importing = asyncio.create_task(service.import_pack(artifact))
        assert await asyncio.to_thread(reached.wait, 3)
        deleting = asyncio.create_task(service.delete_export(exported["job_id"]))
        await asyncio.sleep(0)
        assert not deleting.done() and artifact.exists()
        resume.set()
        imported = await importing
        assert (await deleting)["artifact_retained"] is True
        assert (await _finished(service, imported))["state"] == "active"
        assert artifact.exists()
        assert service._registry.packs["demo-memes"].archive_sha256 == exported["result"]["artifact_id"]
    finally:
        resume.set()
        for task in (importing, deleting):
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
        await service.stop()


@pytest.mark.parametrize("case", ["invalid_id", "missing", "import", "queued", "failed", "no_result", "invalid_artifact"])
async def test_delete_export_refuses_anything_except_a_completed_output(tmp_path, case):
    """Untrusted identifiers and ordinary import tasks cannot delete files."""
    from services.knowledge.models import KnowledgePackError
    from services.knowledge.registry import utc_now
    from services.knowledge.service import ImportJob

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        job_id, digest = "a" * 32, "b" * 64
        stamp = utc_now()
        job = ImportJob(job_id, "demo-memes", "active", stamp, stamp,
                        kind="pack_export", result={"artifact_id": digest})
        if case == "import":
            job.kind = "pack_import"
        elif case in {"queued", "failed"}:
            job.state = case
        elif case == "no_result":
            job.result = None
        elif case == "invalid_artifact":
            job.result["artifact_id"] = "../../private"
        if case != "missing":
            service._remember_job(job)
            await service._persist_job(job)
        artifact = tmp_path / "artifacts" / (digest + ".neko-knowledge.zip")
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_bytes(b"untouched")
        with pytest.raises(KnowledgePackError, match="invalid_request"):
            await service.delete_export("../../private" if case == "invalid_id" else job_id)
        assert artifact.read_bytes() == b"untouched"
        assert case == "missing" or job_id in service._jobs
    finally:
        await service.stop()
