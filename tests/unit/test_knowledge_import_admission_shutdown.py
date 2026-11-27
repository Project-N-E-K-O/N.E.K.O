"""Keep import admission durable when shutdown overlaps its first task flush."""

import asyncio
import hashlib
import json
import threading

import pytest

from services.knowledge.service import KnowledgeService, STAGING_DIR
from tests.knowledge_archive_fixtures import make_archive
from tests.knowledge_fixtures import _finished, _pack
from tests.unit.test_knowledge_subscriptions import _subscription


pytestmark = pytest.mark.unit_fast


@pytest.mark.parametrize("archive_input", [False, True])
async def test_stop_during_initial_import_flush_does_not_queue_after_worker_exit(tmp_path, archive_input):
    """Wait for the real runner to exit, then release admission persistence."""
    raw = make_archive(tmp_path / "input.zip").read_bytes() if archive_input else json.dumps(_pack()).encode()
    service = KnowledgeService(tmp_path / "library")
    entered, release = asyncio.Event(), asyncio.Event()
    request = stopped = None
    await service.start()
    persist = service._persist_job

    async def paused(job):
        if job.state == "queued":
            entered.set()
            await release.wait()
        return await persist(job)

    service._persist_job = paused
    try:
        request = asyncio.create_task(service.import_pack(raw))
        await asyncio.wait_for(entered.wait(), 2)
        stopped = asyncio.create_task(service.stop())
        # Synchronize on worker completion; no guessed sleep/window is needed.
        runner = next(task for task in service._tasks if task.get_name() == "knowledge-import-jobs")
        await asyncio.wait_for(asyncio.gather(runner, return_exceptions=True), 2)
        assert service._stopping and not stopped.done()
        release.set()
        result = await asyncio.wait_for(request, 2)
        await asyncio.wait_for(stopped, 2)
        assert result["state"] == "failed" and result["reason"] == "knowledge_stopping"
        assert not service._job_queue and not service._registry.packs
        job = service._jobs[result["job_id"]]
        assert not service._staging_path(job.job_id).exists()
        frozen = service.root / "jobs" / (job.job_id + ".pack.json")
        assert frozen.is_file() and hashlib.sha256(frozen.read_bytes()).hexdigest() == job.staged_sha256
        record = json.loads((service.root / "jobs" / (job.job_id + ".json")).read_text())
        assert record["state"] == "failed" and record["reason"] == "knowledge_stopping"
        if archive_input:
            assert service.artifact_path(job.archive_sha256).read_bytes() == raw
    finally:
        release.set()
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
        if stopped is not None:
            await asyncio.gather(stopped, return_exceptions=True)
        await service.stop()


async def test_cancelled_bytes_zip_keeps_input_until_retain_owner_finishes(tmp_path, monkeypatch):
    """A disconnected waiter cannot unlink an already-owned archive input."""
    raw = make_archive(tmp_path / "input.zip").read_bytes()
    service = KnowledgeService(tmp_path / "library")
    entered, release = asyncio.Event(), asyncio.Event()
    await service.start()
    thread = service._thread
    request = None

    async def pause_before_open(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") == "retain":
            entered.set()
            await release.wait()
        return await thread(fn, *args, **kwargs)

    service._thread = pause_before_open
    try:
        request = asyncio.create_task(service.import_pack(raw, subscription=_subscription(raw, pack_id="imp.example")))
        await asyncio.wait_for(entered.wait(), 2)
        spool, = (service.root / STAGING_DIR).glob("*.upload.zip")
        request.cancel()
        # Keep the cancellation regression itself bounded so a broken waiter
        # reaches finally and releases the retained native owner.
        done, _pending = await asyncio.wait({request}, timeout=2)
        assert request in done
        with pytest.raises(asyncio.CancelledError):
            await request
        assert spool.is_file() and service._parsing == 1 and service._incoming
        from services.knowledge import service as module
        monkeypatch.setattr(module, "MAX_PENDING_IMPORTS", 1)
        assert await service.import_pack(json.dumps(_pack()).encode()) == {"ok": False, "reason": "knowledge_busy"}
        owners = tuple(service._detached)
        assert owners
        release.set()
        await asyncio.wait_for(asyncio.gather(*owners), 2)
        job, = service.list_jobs()
        assert (await _finished(service, job))["state"] == "active"
        assert not spool.exists()
        assert service._parsing == 0 and not service._incoming
        digest = service._registry.packs["imp.example"].archive_sha256
        assert service.artifact_path(digest).read_bytes() == raw
    finally:
        release.set()
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
        await service.stop()


async def test_cancel_at_archive_owner_creation_keeps_the_handed_off_input(tmp_path, monkeypatch):
    """Cancel synchronously at create_task, before the owner takes one step."""
    raw = make_archive(tmp_path / "input.zip").read_bytes()
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    create_task = asyncio.create_task
    handed_off = []
    request = None

    def cancel_waiter(operation, *, name=None, context=None):
        task = create_task(operation, name=name, context=context)
        if name == "knowledge-archive-import":
            handed_off.append(task)
            asyncio.current_task().cancel()
        return task

    monkeypatch.setattr(asyncio, "create_task", cancel_waiter)
    try:
        request = create_task(service.import_pack(raw))
        with pytest.raises(asyncio.CancelledError):
            await request
        assert handed_off
        await asyncio.wait_for(asyncio.gather(*handed_off), 2)
        job, = service.list_jobs()
        assert (await _finished(service, job))["state"] == "active"
        assert not list((service.root / STAGING_DIR).glob("*.upload.zip"))
    finally:
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
        await service.stop()


async def test_cancelled_initial_zip_write_is_cleaned_only_after_the_real_writer(tmp_path, monkeypatch):
    from services.knowledge import service as module

    raw = make_archive(tmp_path / "input.zip").read_bytes()
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    entered, release = threading.Event(), threading.Event()
    original = module.atomic_write_bytes
    paths = []
    request = None

    def paused(path, content):
        if path.name.endswith(".upload.zip"):
            paths.append(path)
            entered.set()
            assert release.wait(5)
        return original(path, content)

    monkeypatch.setattr(module, "atomic_write_bytes", paused)
    try:
        request = asyncio.create_task(service.import_pack(raw))
        assert await asyncio.to_thread(entered.wait, 2)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(request, 2)
        assert service._parsing == 1
        release.set()
        await asyncio.wait_for(asyncio.gather(*tuple(service._detached)), 2)
        assert paths and all(not path.exists() for path in paths)
        assert service._parsing == 0 and not service.list_jobs()
    finally:
        release.set()
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
        await service.stop()


async def test_cancelled_http_zip_keeps_its_external_path_until_the_owner_finishes(tmp_path, monkeypatch):
    """The existing HTTP file owner drains the full service Path input chain."""
    import httpx
    from fastapi import FastAPI
    from app.memory_server import knowledge_routes

    raw = make_archive(tmp_path / "input.zip").read_bytes()
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    entered, release = asyncio.Event(), asyncio.Event()
    thread = service._thread
    request = None

    async def paused(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") == "retain":
            entered.set()
            await release.wait()
        return await thread(fn, *args, **kwargs)

    service._thread = paused
    monkeypatch.setattr(knowledge_routes, "_service", service)
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            request = asyncio.create_task(client.post("/internal/knowledge/packs/import", content=raw,
                                                       headers={"Content-Type": "application/zip"}))
            await asyncio.wait_for(entered.wait(), 2)
            spool, = (service.root / STAGING_DIR).glob("*.upload")
            request.cancel()
            await asyncio.sleep(0)  # Let the HTTP owner receive its cancellation.
            assert not request.done() and spool.is_file()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(request, 2)
            job, = service.list_jobs()
            assert (await _finished(service, job))["state"] == "active"
            assert not spool.exists()
    finally:
        release.set()
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
        await service.stop()


async def test_cancelled_zip_verification_drains_before_its_input_is_removed(tmp_path, monkeypatch):
    """Keep a queued native parser's input while returning cancellation promptly."""
    from services.knowledge import archive

    raw = make_archive(tmp_path / "input.zip").read_bytes()
    service = KnowledgeService(tmp_path / "library")
    entered, release = threading.Event(), threading.Event()
    parse = archive.read_archive
    await service.start()
    request = None

    def pause_before_open(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return parse(*args, **kwargs)

    monkeypatch.setattr(archive, "read_archive", pause_before_open)
    try:
        request = asyncio.create_task(service.import_pack(raw))
        assert await asyncio.to_thread(entered.wait, 2)
        spool, = (service.root / STAGING_DIR).glob("*.upload.zip")
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(request, 2)
        assert spool.is_file() and service._parsing == 1
        release.set()
        await asyncio.wait_for(asyncio.gather(*tuple(service._detached)), 2)
        assert not spool.exists() and service._parsing == 0
        assert not service.list_jobs()
    finally:
        release.set()
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
        await service.stop()
