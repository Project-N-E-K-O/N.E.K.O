"""Keep cancellation gates, real worker lifetime and shutdown draining aligned.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import threading

import pytest

from knowledge.models import parse_pack
from knowledge.registry import load_registry
import knowledge.service as service_module
# pytest resolves this explicitly imported fixture by argument name.
from tests.knowledge_fixtures import (
    FakeEmbedder,
    _import,
    _pack,
    _raw,
    _started,
    _wait_for_last_job,
    fast_indexer,  # noqa: F401
)

pytestmark = pytest.mark.unit


async def test_cancelled_queued_job_leaves_nothing_behind(tmp_path):
    service = await _started(tmp_path)
    try:
        async with service._write_lock:  # hold the writer so the job stays queued
            result = await service.import_pack(_raw(_pack()))
            assert await service.cancel_job(result["job_id"]) is True
        for _ in range(100):
            if not any((tmp_path / ".staging").iterdir()):
                break
            await asyncio.sleep(0.01)
        assert service.list_jobs()[0]["state"] == "cancelled"
        assert load_registry(tmp_path).packs == {}
        assert not any((tmp_path / ".staging").iterdir())
        assert await service.discard_job(result["job_id"]) is True
        assert service.list_jobs() == []
    finally:
        await service.stop()


async def test_cancel_succeeds_when_its_record_write_fails(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        async with service._write_lock:  # hold the writer so the job stays queued
            result = await service.import_pack(_raw(_pack()))

            async def refuse(_job):
                raise OSError("synthetic record write failure")

            persist = service._persist_job
            monkeypatch.setattr(service, "_persist_job", refuse)
            assert await service.cancel_job(result["job_id"]) is True
            monkeypatch.setattr(service, "_persist_job", persist)
        assert (await _wait_for_last_job(service))[0] == "cancelled"
        assert load_registry(tmp_path).packs == {}
    finally:
        await service.stop()


def test_cancel_inside_the_write_transaction_rolls_back(tmp_path):
    from knowledge.store import KnowledgeStore

    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    pack = parse_pack(_pack())
    calls = {"n": 0}

    def cancel_once_writing() -> bool:
        calls["n"] += 1
        return calls["n"] > len(pack.entries) + 1  # past preparation

    with pytest.raises(InterruptedError):
        store.replace_pack(pack, pack_sha256="0" * 64, should_cancel=cancel_once_writing)
    assert store.pack_versions() == {}
    assert store.count_entries() == 0


async def test_cancelled_request_still_finishes_admission(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        real_write = service_module.atomic_write_bytes

        def slow_write(path, data):
            import time

            time.sleep(0.2)
            real_write(path, data)

        monkeypatch.setattr(service_module, "atomic_write_bytes", slow_write)
        async with service._write_lock:
            request = asyncio.create_task(service.import_pack(_raw(_pack())))
            # Parsing runs first, in a thread; cancel only once the request
            # has reached the shielded admission.
            for _ in range(200):
                if service._admitting:
                    break
                await asyncio.sleep(0.01)
            assert service._admitting
            request.cancel()
            (outcome,) = await asyncio.gather(request, return_exceptions=True)
            assert isinstance(outcome, asyncio.CancelledError)
            # A visible job precedes its first durable flush. Await the real
            # admission owner rather than racing Windows filesystem latency.
            owners = tuple(service._detached)
            assert owners
            _done, pending = await asyncio.wait(owners, timeout=5)
            assert not pending
            # The staged file is owned by a job, and the reservation is gone.
            # The runner may already have picked the job up ("building"), but
            # it cannot commit while this test holds the write lock.
            jobs = service.list_jobs()
            assert len(jobs) == 1, jobs
            (job,) = jobs
            assert job["state"] in service_module.ACTIVE_JOB_STATES
            assert service._staging_path(job["job_id"]).exists()
            assert load_registry(tmp_path).packs == {}
            assert service._admitting == {}
    finally:
        await service.stop()


async def test_cancelling_a_queued_job_deletes_its_staged_file_at_once(tmp_path):
    service = await _started(tmp_path)
    try:
        for task in service._tasks:  # keep the job queued: no runner picks it up
            task.cancel()
        result = await service.import_pack(_raw(_pack()))
        assert [job["state"] for job in service.list_jobs()] == ["queued"]
        assert await service.cancel_job(result["job_id"]) is True
        assert not any((tmp_path / ".staging").iterdir())
    finally:
        await service.stop()


async def test_undeletable_staged_file_does_not_stop_the_job_runner(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        real_unlink = Path.unlink
        failures = {"left": 1}

        def unlink(self, *args, **kwargs):
            if self.parent.name == ".staging" and failures["left"]:
                failures["left"] -= 1
                raise PermissionError("held open")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", unlink)
        first = await _import(service, _pack("pack-one"))
        second = await _import(service, _pack("pack-two"))
        assert first["state"] == "active" and second["state"] == "active"
    finally:
        await service.stop()


def test_cancel_after_the_last_entry_still_rolls_back(tmp_path):
    from knowledge.store import KnowledgeStore

    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    pack = parse_pack(_pack())
    calls = {"n": 0}

    def cancel_at_the_end() -> bool:
        calls["n"] += 1
        # preparation (entries + 1) and the per-entry checks pass; only the
        # final check before commit says "cancel".
        return calls["n"] > 2 * len(pack.entries) + 1

    with pytest.raises(InterruptedError):
        store.replace_pack(pack, pack_sha256="0" * 64, should_cancel=cancel_at_the_end)
    assert store.pack_versions() == {}


async def test_cancel_after_the_commit_point_is_refused(tmp_path, monkeypatch):
    import threading

    service = await _started(tmp_path)
    try:
        loop = asyncio.get_running_loop()
        saving = asyncio.Event()
        release = threading.Event()
        real_save = service_module.save_registry

        def slow_save(root, registry):
            loop.call_soon_threadsafe(saving.set)
            release.wait(5)
            real_save(root, registry)

        monkeypatch.setattr(service_module, "save_registry", slow_save)
        job = await service.import_pack(_raw(_pack()))
        await asyncio.wait_for(saving.wait(), 5)
        try:
            assert await service.cancel_job(job["job_id"]) is False
        finally:
            release.set()
        assert await _wait_for_last_job(service) == ["active"]
        assert (await service.query(query="绝绝子"))["result"] == "matched"
    finally:
        await service.stop()


async def test_a_cancelled_mutation_keeps_the_write_lock_until_it_finishes(tmp_path, monkeypatch):
    import threading

    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        loop = asyncio.get_running_loop()
        saving = asyncio.Event()
        release = threading.Event()
        real_save = service_module.save_registry

        def slow_save(root, registry):
            loop.call_soon_threadsafe(saving.set)
            release.wait(5)
            real_save(root, registry)

        monkeypatch.setattr(service_module, "save_registry", slow_save)
        task = asyncio.create_task(service.set_pack_auto_context("demo-memes", True))
        await asyncio.wait_for(saving.wait(), 5)
        task.cancel()
        await asyncio.wait({task})
        assert task.cancelled()
        assert service._write_lock.locked()  # the registry write is still running
        release.set()
        for _ in range(200):
            if not service._write_lock.locked():
                break
            await asyncio.sleep(0.01)
        assert not service._write_lock.locked()
        assert load_registry(tmp_path).packs["demo-memes"].auto_context is True
    finally:
        await service.stop()


async def test_a_cancelled_import_keeps_its_parse_slot_until_parsing_ends(tmp_path, monkeypatch):
    import threading

    service = await _started(tmp_path)
    try:
        parsing = threading.Event()
        release = threading.Event()
        real_prepare = service._prepare_import

        def slow_prepare(raw):
            parsing.set()
            release.wait(5)
            return real_prepare(raw)

        monkeypatch.setattr(service, "_prepare_import", slow_prepare)
        task = asyncio.create_task(service.import_pack(_raw(_pack())))
        await asyncio.to_thread(parsing.wait, 5)
        task.cancel()
        await asyncio.wait({task})
        assert task.cancelled()
        assert service._parsing == 1  # the parse thread still holds the pack
        release.set()
        for _ in range(200):
            if service._parsing == 0:
                break
            await asyncio.sleep(0.01)
        assert service._parsing == 0
    finally:
        await service.stop()


async def test_cancelled_queued_jobs_leave_the_runner_backlog(tmp_path):
    service = await _started(tmp_path)
    try:
        async with service._write_lock:  # the runner is stuck on the first job
            await service.import_pack(_raw(_pack("first")))
            await asyncio.sleep(0.05)
            for i in range(10):
                job = await service.import_pack(_raw(_pack(f"other-{i}")))
                assert job["state"] == "queued"
                assert await service.cancel_job(job["job_id"]) is True
            assert len(service._job_queue) == 0  # only the building job, already taken
    finally:
        await service.stop()


async def test_shutdown_waits_for_a_write_whose_caller_was_cancelled(tmp_path, monkeypatch):
    import threading

    service = await _started(tmp_path)
    await _import(service, _pack())
    loop = asyncio.get_running_loop()
    saving = asyncio.Event()
    release = threading.Event()
    written = threading.Event()
    real_save = service_module.save_registry

    def slow_save(root, registry):
        loop.call_soon_threadsafe(saving.set)
        release.wait(5)
        real_save(root, registry)
        written.set()

    monkeypatch.setattr(service_module, "save_registry", slow_save)
    task = asyncio.create_task(service.set_pack_auto_context("demo-memes", True))
    await asyncio.wait_for(saving.wait(), 5)
    task.cancel()
    await asyncio.wait({task})
    threading.Timer(0.3, release.set).start()
    await service.stop()
    assert written.is_set()  # stop() returned only after the write ended


async def test_stop_waits_for_the_startup_reconcile_thread(tmp_path, monkeypatch):
    import threading

    service = service_module.KnowledgeService(tmp_path)
    opening = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    real_open = service._open_blocking

    def slow_open():
        opening.set()
        release.wait(5)
        real_open()
        finished.set()

    monkeypatch.setattr(service, "_open_blocking", slow_open)
    starting = asyncio.create_task(service.start())
    await asyncio.to_thread(opening.wait, 5)
    starting.cancel()
    await asyncio.wait({starting})
    threading.Timer(0.3, release.set).start()
    await service.stop()
    assert finished.is_set()  # stop() returned only after the rebuild thread ended


async def test_stop_waits_for_an_admission_whose_request_was_cancelled(tmp_path, monkeypatch):
    import threading

    service = await _started(tmp_path)
    writing = threading.Event()
    release = threading.Event()
    written = threading.Event()
    real_write = service_module.atomic_write_bytes

    def slow_write(path, data):
        writing.set()
        release.wait(5)
        real_write(path, data)
        written.set()

    monkeypatch.setattr(service_module, "atomic_write_bytes", slow_write)
    request = asyncio.create_task(service.import_pack(_raw(_pack())))
    await asyncio.to_thread(writing.wait, 5)
    request.cancel()
    await asyncio.wait({request})
    threading.Timer(0.3, release.set).start()
    await service.stop()
    assert written.is_set()  # stop() waited for the staging write
    assert service.list_jobs() == []  # and nothing was queued after the runner stopped
    assert list((tmp_path / ".staging").glob("*.json")) == []


async def test_a_cancelled_unchanged_check_keeps_its_import_slot_until_the_read_ends(tmp_path, monkeypatch):
    import threading

    service = await _started(tmp_path)
    reading = threading.Event()
    release = threading.Event()
    try:
        payload = _pack()
        await _import(service, payload)
        real = service._raw_file_intact

        def slow(record):
            reading.set()
            release.wait(5)
            return real(record)

        monkeypatch.setattr(service, "_raw_file_intact", slow)
        request = asyncio.create_task(service.import_pack(_raw(payload)))
        assert await asyncio.to_thread(reading.wait, 5)
        request.cancel()
        await asyncio.wait({request})
        assert service._parsing == 1  # the read still holds the request's slot
        release.set()
        for _ in range(200):
            if service._parsing == 0:
                break
            await asyncio.sleep(0.01)
        assert service._parsing == 0
    finally:
        release.set()
        await service.stop()


async def test_stop_waits_for_query_work_a_cancelled_lookup_left_running(tmp_path, monkeypatch):
    import threading

    service = await _started(tmp_path)
    scanning = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    try:
        await _import(service, _pack())
        real = service._queries._lexical_search

        def slow(*args):
            scanning.set()
            release.wait(5)
            try:
                return real(*args)
            finally:
                finished.set()

        monkeypatch.setattr(service._queries, "_lexical_search", slow)
        request = asyncio.create_task(service.query(query="绝绝子"))
        assert await asyncio.to_thread(scanning.wait, 5)
        request.cancel()
        await asyncio.wait({request})
        threading.Timer(0.3, release.set).start()
        await service.stop()
        assert finished.is_set()  # stop() waited for the scan to end
    finally:
        release.set()


async def test_stop_waits_for_a_vector_load_whose_task_was_cancelled(tmp_path, monkeypatch, fast_indexer):
    import threading

    service = await _started(tmp_path, FakeEmbedder())
    loading = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    try:
        await _import(service, _pack())
        while service._vector_task is not None and not service._vector_task.done():
            await asyncio.gather(service._vector_task, return_exceptions=True)
        real = service._store.load_vectors

        def slow(model_id):
            loading.set()
            release.wait(5)
            try:
                return real(model_id)
            finally:
                finished.set()

        monkeypatch.setattr(service._store, "load_vectors", slow)
        service._vector_generation += 1
        service._indexer._schedule_vector_refresh()
        assert await asyncio.to_thread(loading.wait, 5)
        threading.Timer(0.3, release.set).start()
        await service.stop()  # cancels the snapshot task mid-load
        assert finished.is_set()  # and waited for its thread
    finally:
        release.set()
