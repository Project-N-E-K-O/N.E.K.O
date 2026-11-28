"""Respect model readiness, policy switches and bounded background inference duty.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from services.knowledge import indexing as index_module
from services.knowledge.registry import load_registry
import services.knowledge.service as service_module
# pytest resolves this explicitly imported fixture by argument name.
from tests.knowledge_fixtures import FakeEmbedder, _entries, _import, _pack, _started, fast_indexer  # noqa: F401

pytestmark = pytest.mark.unit


async def test_backfill_embeds_chunks_and_enables_hybrid_lookup(tmp_path, fast_indexer):
    embedder = FakeEmbedder()
    service = await _started(tmp_path, embedder)
    try:
        await _import(service, _pack())
        for _ in range(300):
            packs = await service.list_packs()
            if packs[0]["vector_state"] == "complete" and service._vectors is not None:
                break
            await asyncio.sleep(0.02)
        assert packs[0]["vector_state"] == "complete"
        assert max(embedder.batches) <= index_module.INDEX_BATCH_SIZE
        result = await service.query(query="绝绝子")
        assert result["result"] == "matched"
        assert result["retrieval_mode"] == "hybrid"
    finally:
        await service.stop()


async def test_local_embedding_off_skips_backfill(tmp_path, fast_indexer):
    embedder = FakeEmbedder(state="loading")
    service = await _started(tmp_path, embedder)
    try:
        await _import(service, _pack())
        await service.set_pack_local_embedding("demo-memes", False)
        embedder._state = "ready"
        service._index_wakeup.set()
        await asyncio.sleep(0.3)
        (pack,) = await service.list_packs()
        assert embedder.batches == []
        assert pack["chunks_ready"] == 0
        assert pack["vector_state"] == "off"
    finally:
        await service.stop()


async def test_failed_chunks_are_retried_after_a_model_change(tmp_path, fast_indexer):
    class Flaky(FakeEmbedder):
        fail = True

        async def embed_batch(self, texts):
            self.batches.append(len(texts))
            if self.fail:
                return [None] * len(texts)
            return [self.vector(text) for text in texts]

    embedder = Flaky()
    service = await _started(tmp_path, embedder)
    try:
        await _import(service, _pack())
        for _ in range(300):
            (pack,) = await service.list_packs()
            if pack["chunks_failed"] == pack["chunks_total"]:
                break
            await asyncio.sleep(0.02)
        assert pack["vector_state"] == "partial"
        embedder.fail = False
        embedder._model_id = "fake-16-v2"
        service._index_wakeup.set()
        for _ in range(300):
            (pack,) = await service.list_packs()
            if pack["vector_state"] == "complete":
                break
            await asyncio.sleep(0.02)
        assert pack["vector_state"] == "complete"
    finally:
        await service.stop()


async def test_enabling_vectors_succeeds_even_if_the_retry_reset_fails(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        await service.set_pack_local_embedding("demo-memes", False)

        def fail(*_args, **_kwargs):
            raise sqlite3.OperationalError("locked")

        monkeypatch.setattr(service._store, "reset_attempts", fail)
        result = await service.set_pack_local_embedding("demo-memes", True)
        assert result["local_embedding"] is True
        assert load_registry(tmp_path).packs["demo-memes"].local_embedding is True
    finally:
        await service.stop()


async def test_turning_knowledge_off_mid_batch_stops_indexing(tmp_path, fast_indexer):
    class SwitchingEmbedder(FakeEmbedder):
        service = None

        async def embed_batch(self, texts):
            self.batches.append(len(texts))
            if len(self.batches) == 1:
                await self.service.set_enabled(False)
            return [self.vector(text) for text in texts]

    embedder = SwitchingEmbedder()
    service = service_module.KnowledgeService(tmp_path, embedder=embedder)
    embedder.service = service
    await service.start()
    try:
        await _import(service, _pack(entries=_entries("k", 30)))
        for _ in range(100):
            if embedder.batches:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)
        assert embedder.batches == [index_module.INDEX_BATCH_SIZE]
        stats = await asyncio.to_thread(service._store.chunk_stats, "fake-16")
        assert stats["demo-memes"]["ready"] == 0
    finally:
        await service.stop()


def test_the_pause_after_a_batch_scales_with_its_inference_time():
    duty = index_module.INDEX_TARGET_DUTY
    assert index_module._batch_pause(2.0) == pytest.approx(2.0 * (1 - duty) / duty)
    assert index_module._batch_pause(0.0) == index_module.INDEX_BATCH_PAUSE_SECONDS


async def test_background_indexing_stays_under_the_target_duty(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(index_module, "INDEX_BATCH_PAUSE_SECONDS", 0.0)
    monkeypatch.setattr(index_module, "INDEX_IDLE_SECONDS", 0.05)
    batches: list[tuple[float, float]] = []
    sizes: list[int] = []

    class SlowEmbedder(FakeEmbedder):
        async def embed_batch(self, texts):
            sizes.append(len(texts))
            start = time.monotonic()
            await asyncio.sleep(0.05)
            batches.append((start, time.monotonic()))
            return [self.vector(text) for text in texts]

    service = await _started(tmp_path, SlowEmbedder())
    try:
        await _import(service, _pack(entries=_entries("d", 12)))
        for _ in range(300):
            if len(batches) >= 3:
                break
            await asyncio.sleep(0.02)
        assert len(batches) >= 3
        assert max(sizes) <= index_module.INDEX_BATCH_SIZE == 4
        duty = index_module.INDEX_TARGET_DUTY
        for (start, end), (next_start, _next_end) in zip(batches, batches[1:]):
            busy = end - start
            # The idle gap before the next batch is proportional to the work.
            assert next_start - end >= busy * (1 - duty) / duty * 0.9
            assert busy / (next_start - start) <= duty + 0.02
    finally:
        await service.stop()


def test_the_pause_after_a_batch_is_capped():
    # e.g. a laptop slept for an hour in the middle of a batch
    assert index_module._batch_pause(3600.0) == index_module.INDEX_MAX_BATCH_PAUSE_SECONDS


async def test_a_batch_that_stores_nothing_still_pauses(tmp_path, monkeypatch):
    pauses: list[float] = []

    def record(seconds):
        pauses.append(seconds)
        return 0.0

    monkeypatch.setattr(index_module, "_batch_pause", record)

    class FailingEmbedder(FakeEmbedder):
        async def embed_batch(self, texts):
            return [None for _ in texts]

    service = await _started(tmp_path, FailingEmbedder())
    try:
        await _import(service, _pack())
        for _ in range(200):
            if service.diagnostics.snapshot()["index_batches"] and pauses:
                break
            await asyncio.sleep(0.01)
        batches = service.diagnostics.snapshot()["index_batches"]
        assert batches and all(batch["stored"] == 0 for batch in batches)
        assert pauses  # the failed batch paused like any other
    finally:
        await service.stop()


async def test_long_indexing_rounds_end_when_a_vector_refresh_is_due(tmp_path, monkeypatch, fast_indexer):
    monkeypatch.setattr(index_module, "VECTOR_REFRESH_SECONDS", 0.05)
    monkeypatch.setattr(index_module, "_batch_pause", lambda _seconds: 0.08)
    events: list[str] = []

    class Recording(FakeEmbedder):
        async def embed_batch(self, texts):
            events.append("batch")
            return await super().embed_batch(texts)

    service = await _started(tmp_path, Recording())
    real_schedule = service._indexer._schedule_vector_refresh

    def schedule():
        events.append("refresh")
        real_schedule()

    monkeypatch.setattr(service._indexer, "_schedule_vector_refresh", schedule)
    try:
        await _import(service, _pack(entries=_entries("r", 16)))
        for _ in range(300):
            if events.count("batch") >= 3:
                break
            await asyncio.sleep(0.01)
        batches = [i for i, event in enumerate(events) if event == "batch"]
        assert len(batches) >= 3
        # A reload is scheduled between batches, not only after the round.
        assert "refresh" in events[batches[0] : batches[-1]]
    finally:
        await service.stop()
