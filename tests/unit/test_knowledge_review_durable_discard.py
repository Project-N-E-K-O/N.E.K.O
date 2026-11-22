"""A discarded task disappears only after its durable record is removed."""

import asyncio
import json
from pathlib import Path
import threading

import httpx
import pytest
from fastapi import FastAPI

from app.memory_server import knowledge_routes
from knowledge import service as module
from knowledge.models import KnowledgePackError, pack_sha256
from knowledge.service import ImportJob, KnowledgeService
from tests.unit.test_knowledge_subsystem import _pack


pytestmark = pytest.mark.unit_fast


async def _retryable_job(service):
    """Persist a failed import and the job-specific raw input it can retry."""
    raw = json.dumps(_pack()).encode()
    stamp = "2026-01-01T00:00:00Z"
    job = ImportJob("d" * 32, "demo-memes", "failed", stamp, stamp, staged_sha256=pack_sha256(raw))
    service._remember_job(job)
    await service._persist_job(job)
    frozen = service.root / "jobs" / (job.job_id + ".pack.json")
    frozen.write_bytes(raw)
    return job, frozen


async def test_record_unlink_failure_keeps_the_card_after_removing_its_retry_input(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    job, frozen = await _retryable_job(service)
    record = service.root / "jobs" / (job.job_id + ".json")
    original = Path.unlink

    def refuse(path, *args, **kwargs):
        if path == record:
            raise PermissionError("synthetic transient file lock")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    monkeypatch.setattr(knowledge_routes, "_service", service)
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            response = await http.post("/internal/knowledge/packs/jobs/discard", json={"job_id": job.job_id})
        await asyncio.gather(*tuple(service._detached), return_exceptions=True)
        visible_before_restart = job.job_id in service._jobs
        assert record.exists() and not frozen.exists()
        await service.stop()
        service = KnowledgeService(tmp_path)
        await service.start()
        # The old code reports success and hides the card, then restores it
        # from the unchanged JSON. A refusal must remain visible continuously.
        assert (response.status_code, response.json()["ok"], visible_before_restart,
                job.job_id in service._jobs) == (500, False, True, True)
        monkeypatch.setattr(Path, "unlink", original)
        with pytest.raises(KnowledgePackError) as error:
            await service.retry_job(job.job_id)
        assert error.value.reason == "job_not_retryable"
        assert await service.discard_job(job.job_id)
        assert not record.exists() and not frozen.exists()
    finally:
        await service.stop()


async def test_discard_owner_survives_caller_cancellation_and_blocks_late_flush(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    job, frozen = await _retryable_job(service)
    record = service.root / "jobs" / (job.job_id + ".json")
    entered, release = threading.Event(), threading.Event()
    original = Path.unlink

    def paused(path, *args, **kwargs):
        if path == record:
            entered.set()
            assert release.wait(5)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", paused)
    request = late_flush = None
    try:
        request = asyncio.create_task(service.discard_job(job.job_id))
        assert await asyncio.to_thread(entered.wait, 2)
        assert not request.done() and service._jobs[job.job_id] is job
        # Input deletion precedes this paused metadata unlink. The durable
        # card is still visible until the shielded owner finishes both steps.
        assert not job.export_deleted and not frozen.exists() and record.exists()
        # A cancelled worker/request can still have a terminal flush pending.
        late_flush = asyncio.create_task(service._persist_job(job))
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        release.set()
        await asyncio.gather(*tuple(service._detached))
        await late_flush
        assert not record.exists() and not frozen.exists()
        assert job.export_deleted and job.job_id not in service._jobs
        await service._persist_job(job)
        assert not record.exists()
        assert await service.discard_job(job.job_id) is False
    finally:
        release.set()
        await asyncio.gather(*(task for task in (request, late_flush) if task is not None), return_exceptions=True)
        await service.stop()


@pytest.mark.parametrize("read_before_discard", [False, True])
async def test_retry_and_discard_keep_only_independently_owned_inputs(tmp_path, monkeypatch, read_before_discard):
    service = KnowledgeService(tmp_path)
    await service.start()
    job, frozen = await _retryable_job(service)
    entered, release = threading.Event(), threading.Event()
    original = module._read_bounded

    def paused(path):
        if path != frozen:
            return original(path)
        raw = original(path) if read_before_discard else None
        entered.set()
        assert release.wait(5)
        return raw if read_before_discard else original(path)

    monkeypatch.setattr(module, "_read_bounded", paused)
    retry = None
    try:
        retry = asyncio.create_task(service.retry_job(job.job_id))
        assert await asyncio.to_thread(entered.wait, 2)
        assert await service.discard_job(job.job_id)
        assert not frozen.exists()
        release.set()
        if read_before_discard:
            # The retry already owns its bytes independently of the two
            # unlinks, so it may finish and freeze a new task-specific copy.
            result = await retry
            assert result["ok"] and result["job_id"] != job.job_id
            assert (service.root / "jobs" / (result["job_id"] + ".pack.json")).exists()
        else:
            with pytest.raises(KnowledgePackError) as error:
                await retry
            assert error.value.reason == "job_not_retryable"
        assert job.job_id not in service._jobs
        assert not (service.root / "jobs" / (job.job_id + ".json")).exists()
    finally:
        release.set()
        if retry is not None:
            await asyncio.gather(retry, return_exceptions=True)
        await service.stop()
