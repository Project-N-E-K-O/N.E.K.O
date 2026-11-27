"""Replan refusals belong to the registry checked after acquiring the lock."""

# Index scheduling hooks follow VectorIndexer; shared runtime state stays on the service.
from services.knowledge import indexing as index_module
import asyncio
from dataclasses import replace

import pytest

from services.knowledge import service as module
from services.knowledge.models import KnowledgePackError, decode_pack_bytes
from services.knowledge.service import KnowledgeService
from services.knowledge.store import count_pack_chunks
from tests.unit.test_knowledge_vector_backfill_recovery import TokenProvider
from tests.knowledge_helpers import _stop_automatic_indexer
from tests.knowledge_fixtures import _import, _pack, _raw


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("restore_capacity", [True, False])
async def test_refusal_key_uses_the_capacity_checked_after_lock_wait(tmp_path, monkeypatch, restore_capacity):
    service = KnowledgeService(tmp_path)
    await service.start()
    round_task = waiter = None
    lock_held = False
    try:
        await _stop_automatic_indexer(service)
        pack = _pack("imp.waiting", entries=[{"title": "Large", "content": "x" * 900}])
        assert (await _import(service, pack))["state"] == "active"
        assert (await _import(service, _pack("imp.other", entries=[{"title": "Other", "content": "Short."}])))["state"] == "active"
        provider = TokenProvider()
        service.embedder = provider
        # No model-transition reset may consume the held lock before replan.
        service._index_model_id = provider.model_id()
        service._indexer._vector_pack_ids = lambda: ["imp.waiting"]
        planned = count_pack_chunks(decode_pack_bytes(_raw(pack)), fit=service._chunk_fit())
        original_other = service._registry.packs["imp.other"]
        monkeypatch.setattr(module, "MAX_TOTAL_CHUNKS", planned + original_other.chunks)
        # End the bounded round after its first refused row, before the next
        # round can observe a different registry and accidentally hide the race.
        monkeypatch.setattr(index_module, "INDEX_ROUND_CHUNKS", 1)
        monkeypatch.setattr(index_module, "INDEX_BATCH_PAUSE_SECONDS", 0)
        waiting = asyncio.Event()
        locked = service._locked

        async def observe_lock(func, *, wait=False):
            if service._write_lock.locked():
                waiting.set()
            return await locked(func, wait=wait)

        monkeypatch.setattr(service, "_locked", observe_lock)
        await service._write_lock.acquire()
        lock_held = True
        round_task = asyncio.create_task(service._indexer._index_round())
        waiter = asyncio.create_task(waiting.wait())
        await asyncio.wait({round_task, waiter}, timeout=3, return_when=asyncio.FIRST_COMPLETED)
        if round_task.done():
            round_task.result()
        assert waiting.is_set() and not round_task.done(), "replan did not wait for the real mutation lock"
        # Another mutation owns the lock and consumes capacity after the
        # indexer read its old key, but before replan checks the registry.
        service._publish_registry(service._registry.with_pack(
            replace(original_other, chunks=original_other.chunks + 1)))
        service._write_lock.release()
        lock_held = False
        await asyncio.wait_for(round_task, 3)
        assert service._refused_replans["imp.waiting"][1] == "capacity_chunks"

        resets = []
        reset = service._store.reset_attempts

        def observe_reset(pack_id=""):
            resets.append(pack_id)
            return reset(pack_id)

        monkeypatch.setattr(service._store, "reset_attempts", observe_reset)
        if restore_capacity:
            async def release_capacity():
                service._publish_registry(service._registry.with_pack(original_other))

            await service._locked(release_capacity, wait=True)
        await service._indexer._index_round()
        assert resets == (["imp.waiting"] if restore_capacity else [])
        assert ("imp.waiting" not in service._refused_replans) is restore_capacity
        if restore_capacity:
            assert service._registry.packs["imp.waiting"].chunks == planned
    finally:
        if lock_held:
            service._write_lock.release()
        pending = [task for task in (round_task, waiter) if task is not None]
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await service.stop()


@pytest.mark.parametrize("reason", ["too_many_chunks", "capacity_chunks", "embedding_input_too_large"])
async def test_direct_replan_keeps_its_original_error_contract(tmp_path, monkeypatch, reason):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        pack = _pack("imp.waiting", entries=[{"title": "Large", "content": "x" * 900}])
        assert (await _import(service, pack))["state"] == "active"
        assert (await _import(service, _pack("imp.other", entries=[{"title": "Other", "content": "Short."}])))["state"] == "active"
        provider = TokenProvider()
        service.embedder = provider
        if reason == "embedding_input_too_large":
            spec = {**provider.spec(), "max_length": 1}
            monkeypatch.setattr(provider, "spec", lambda: spec)
        elif reason == "too_many_chunks":
            monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", 1)
        else:
            planned = count_pack_chunks(decode_pack_bytes(_raw(pack)), fit=service._chunk_fit())
            monkeypatch.setattr(module, "MAX_TOTAL_CHUNKS", planned)
        spec = provider.spec()
        with pytest.raises(ValueError) as caught:
            await service._replan_vector_inputs(["imp.waiting"], specification=spec)
        assert str(caught.value) == reason
        assert isinstance(caught.value, KnowledgePackError) is (reason != "embedding_input_too_large")
        key = caught.value.replan_keys["imp.waiting"]
        assert key == (service._registry.packs["imp.waiting"].pack_sha256,
                       service._store._spec_hash(spec), service._registry.packs["imp.other"].chunks)
    finally:
        await service.stop()
