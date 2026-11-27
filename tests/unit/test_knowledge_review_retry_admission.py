"""Exercise cancelled import/export admission and retry arrival ordering."""

import asyncio
import hashlib
import json
import uuid

import pytest

from knowledge import service as module
from knowledge.service import ImportJob, KnowledgeService, MAX_PENDING_IMPORTS
from tests.unit.test_knowledge_archive_v2 import make_archive
from tests.unit.test_knowledge_subsystem import _import, _pack
from tests.unit.test_knowledge_unified_core import _finished
from utils.knowledge_package import hash_file


pytestmark = pytest.mark.unit


async def test_exports_cancelled_during_initial_persistence_do_not_fill_the_queue(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _import(service, _pack())
    finally:
        await service.stop()

    service = KnowledgeService(tmp_path)

    async def paused_runner():
        # Leave the real queue observable while the runner is busy elsewhere.
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_job_loop", paused_runner)
    await service.start()
    persist = service._persist_job
    release = asyncio.Event()
    request = None
    try:
        for _ in range(MAX_PENDING_IMPORTS + 2):
            entered, release = asyncio.Event(), asyncio.Event()

            async def hold_first_record(job):
                if job.kind == "pack_export" and job.state == "queued":
                    entered.set()
                    await release.wait()
                await persist(job)

            monkeypatch.setattr(service, "_persist_job", hold_first_record)
            request = asyncio.create_task(service.export_pack("demo-memes"))
            await asyncio.wait_for(entered.wait(), 2)
            job = next(job for job in service.list_jobs()
                       if job["kind"] == "pack_export" and job["state"] == "queued")
            assert await service.cancel_job(job["job_id"])
            release.set()
            assert (await request)["state"] == "cancelled"
        assert list(service._job_queue) == []
        assert not service._job_queued.is_set()
    finally:
        release.set()
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
        await service.stop()


@pytest.mark.parametrize("archive_format", ["json", "zip"])
async def test_imports_cancelled_during_initial_persistence_do_not_fill_the_queue(tmp_path, monkeypatch, archive_format):
    service = KnowledgeService(tmp_path / "library")

    async def paused_runner():
        # Keep cancelled IDs observable instead of letting the runner drain them.
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_job_loop", paused_runner)
    await service.start()
    raw = make_archive(tmp_path / "input.zip") if archive_format == "zip" else json.dumps(_pack()).encode()
    archive_bytes = raw.read_bytes() if archive_format == "zip" else None
    persist = service._persist_job
    release = asyncio.Event()
    request = None
    try:
        for _ in range(MAX_PENDING_IMPORTS + 2):
            entered, release = asyncio.Event(), asyncio.Event()

            async def hold_first_record(job):
                # The public job already exists, but its owner has not flushed
                # the initial record or appended its ID to the runnable queue.
                if job.kind == "pack_import" and job.state == "queued":
                    entered.set()
                    await release.wait()
                await persist(job)

            monkeypatch.setattr(service, "_persist_job", hold_first_record)
            request = asyncio.create_task(service.import_pack(raw))
            await asyncio.wait_for(entered.wait(), 2)
            job = next(job for job in service.list_jobs()
                       if job["kind"] == "pack_import" and job["state"] == "queued")
            assert await service.cancel_job(job["job_id"])
            release.set()
            result = await request
            assert result["ok"] and result["state"] == "cancelled"
            record = json.loads((service.root / "jobs" / (job["job_id"] + ".json")).read_text())
            assert record["state"] == "cancelled"
            # Explicit cancellation reclaims staging, retaining the exact JSON
            # and any original ZIP that a later deliberate retry will read.
            frozen = service.root / "jobs" / (job["job_id"] + ".pack.json")
            assert hashlib.sha256(frozen.read_bytes()).hexdigest() == record["staged_sha256"]
            assert not service._staging_path(job["job_id"]).exists()
            if archive_format == "zip":
                assert service.artifact_path(record["archive_sha256"]).read_bytes() == archive_bytes
        assert list(service._job_queue) == []
        assert not service._job_queued.is_set()
    finally:
        release.set()
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
        await service.stop()


@pytest.mark.parametrize("archive_format", ["json", "zip"])
@pytest.mark.parametrize("remove_during_read", [True, False])
async def test_retry_keeps_its_order_across_frozen_input_io(tmp_path, monkeypatch, archive_format, remove_during_read):
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    entered, release = asyncio.Event(), asyncio.Event()
    retry = None
    try:
        if archive_format == "zip":
            archive = make_archive(tmp_path / "input.zip")
            installed = await _finished(service, await service.import_pack(archive))
            pack_id = installed["pack_id"]
        else:
            pack_id = (await _import(service, _pack()))["pack_id"]
        record = service._registry.packs[pack_id]
        stamp = "2026-11-22T00:00:00Z"
        job = ImportJob(uuid.uuid4().hex, pack_id, "failed", stamp, stamp,
                        staged_sha256=record.pack_sha256, archive_sha256=record.archive_sha256)
        service._remember_job(job)
        await service._persist_job(job)
        if archive_format == "zip":
            frozen = service.artifact_path(job.archive_sha256)
            assert hashlib.sha256(frozen.read_bytes()).hexdigest() == job.archive_sha256
            read = hash_file
        else:
            frozen = service.root / "jobs" / (job.job_id + ".pack.json")
            frozen.write_bytes((service.root / "packs" / record.file_name).read_bytes())
            read = module._read_bounded
        thread = service._thread
        waiting = True

        async def hold_read(fn, *args, **kwargs):
            nonlocal waiting
            if waiting and fn is read and args[0] == frozen:
                waiting = False
                entered.set()
                await release.wait()
            return await thread(fn, *args, **kwargs)

        monkeypatch.setattr(service, "_thread", hold_read)
        if remove_during_read:
            retry = asyncio.create_task(service.retry_job(job.job_id))
            await asyncio.wait_for(entered.wait(), 2)
            await service.remove_pack(pack_id)
            release.set()
        else:
            # A new explicit retry after deletion may still install its frozen
            # version; it must not reuse the failed task's old arrival number.
            await service.remove_pack(pack_id)
            release.set()
            retry = asyncio.create_task(service.retry_job(job.job_id))
        result = await _finished(service, await retry)
        expected = "cancelled" if remove_during_read else "active"
        assert result["state"] == expected
        assert (pack_id in service._registry.packs) is (not remove_during_read)
        assert frozen.exists()
        assert json.loads((service.root / "jobs" / (job.job_id + ".json")).read_text())["state"] == "failed"
    finally:
        release.set()
        if retry is not None:
            await asyncio.gather(retry, return_exceptions=True)
        await service.stop()
