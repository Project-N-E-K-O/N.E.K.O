"""Exercise source mutation ordering and shared import/export admission."""

import asyncio
import json

import pytest

from tests.knowledge_helpers import ObservedLock

from knowledge.models import KnowledgePackError
from knowledge.service import KnowledgeService
from tests.knowledge_fixtures import _finished, _import, _pack


@pytest.mark.parametrize("operation", ["commit", "restore", "remove"])
async def test_later_pack_removal_cancels_a_source_operation_waiting_for_the_write_lock(tmp_path, operation):
    service = KnowledgeService(tmp_path)
    await service.start()
    source_request = removal = None
    lock = None
    holding_lock = False
    try:
        first = (await service.preview_source("a.txt", b"Original lighthouse reference."))["preview"]
        initial = await _finished(service, await service.commit_source(first["preview_id"], first["sha256"]))
        pack_id = initial["pack_id"]
        second = (await service.preview_source("b.txt", b"Garden reference.", pack_id=pack_id))["preview"]
        await _finished(service, await service.commit_source(second["preview_id"], second["sha256"]))
        await service.set_pack_enabled(pack_id, True)
        preview = (await service.preview_source("c.txt", b"Additional mountain reference.", pack_id=pack_id))["preview"]

        # The source mutation enters the actual lock queue before deletion;
        # no scheduler sleep or private asyncio waiter state controls the race.
        lock = ObservedLock()
        service._write_lock = lock
        await lock.acquire()
        holding_lock = True
        if operation == "commit":
            request = service.commit_source(preview["preview_id"], preview["sha256"], enabled=True,
                                            enabled_confirmed=True)
        elif operation == "restore":
            request = service.restore_source(pack_id, first["doc_id"], 1)
        else:
            request = service.remove_source(pack_id, second["doc_id"])
        source_request = asyncio.create_task(request)
        await asyncio.wait_for(lock.waiting.get(), 2)
        removal = asyncio.create_task(service.remove_pack(pack_id))
        await asyncio.wait_for(lock.waiting.get(), 2)
        lock.release()
        holding_lock = False

        source_result, removed = await asyncio.gather(source_request, removal)
        assert removed["pack_id"] == pack_id
        assert (await _finished(service, source_result))["state"] == "cancelled"
        assert pack_id not in {pack["pack_id"] for pack in await service.list_packs()}
    finally:
        if holding_lock:
            lock.release()
        pending = [task for task in (source_request, removal) if task is not None and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await service.stop()


@pytest.fixture
async def admission_service(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        for pack_id in ("quota.incoming", "quota.first", "quota.second", "quota.third"):
            await _import(service, _pack(pack_id))
    finally:
        await service.stop()

    service = KnowledgeService(tmp_path)

    async def paused_runner():
        # These tests exercise real admission/persistence, without encoding
        # vectors or consuming the queued jobs before their counts are checked.
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_job_loop", paused_runner)
    await service.start()
    try:
        yield service
    finally:
        await service.stop()


def _replacement(pack_id):
    payload = _pack(pack_id)
    payload["source"]["name"] = "Replacement"
    return json.dumps(payload).encode()


@pytest.mark.parametrize("target,reason", [("quota.third", "knowledge_busy"), ("quota.incoming", "job_in_progress")])
async def test_export_counts_import_reservations_before_their_job_is_registered(admission_service, monkeypatch, target, reason):
    service = admission_service
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._admit_import

    async def hold_admission(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "_admit_import", hold_admission)
    await service.export_pack("quota.first")
    await service.export_pack("quota.second")
    incoming = asyncio.create_task(service.import_pack(_replacement("quota.incoming")))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(KnowledgePackError) as error:
            await service.export_pack(target)
        assert error.value.reason == reason
    finally:
        release.set()
        await incoming


@pytest.mark.parametrize("next_operation", ["import", "export"])
async def test_registered_import_and_its_lingering_reservation_share_one_slot(admission_service, monkeypatch, next_operation):
    service = admission_service
    entered, release = asyncio.Event(), asyncio.Event()
    next_entered = asyncio.Event()
    persist, admit = service._persist_job, service._admit_import

    async def hold_first_record(job):
        if job.pack_id == "quota.incoming" and job.state == "queued":
            entered.set()
            await release.wait()
        await persist(job)

    async def observe_admission(pack, *args, **kwargs):
        if pack.pack_id == "quota.third":
            next_entered.set()
        return await admit(pack, *args, **kwargs)

    monkeypatch.setattr(service, "_persist_job", hold_first_record)
    monkeypatch.setattr(service, "_admit_import", observe_admission)
    incoming = asyncio.create_task(service.import_pack(_replacement("quota.incoming")))
    next_request = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await service.export_pack("quota.first")
        # The import's job is visible while its reservation still exists.
        assert "quota.incoming" in service._admitting
        assert not service.import_busy()
        if next_operation == "export":
            assert (await service.export_pack("quota.third"))["state"] == "queued"
        else:
            next_request = asyncio.create_task(service.import_pack(_replacement("quota.third")))
            await asyncio.wait_for(next_entered.wait(), 2)
        assert service.import_busy()
    finally:
        release.set()
        await incoming
        if next_request is not None:
            assert (await next_request)["ok"]
