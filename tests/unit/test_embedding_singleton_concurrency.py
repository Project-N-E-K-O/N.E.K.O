"""Concurrency guarantees of the ``get_embedding_service()`` singleton.

At memory-server startup the embedding warmup worker and the knowledge
runtime each resolve the service from their own ``asyncio.to_thread`` worker.
Without a lock both could build an instance; only the worker's copy is ever
loaded, so if the other copy ends up as the global singleton every recall
reads a service stuck in INIT for the whole process.

The factory sleeps to widen the race window instead of using a barrier: with
the lock in place only one thread enters the factory, so a barrier inside it
would deadlock.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from memory import embeddings
from memory.embeddings import EmbeddingService, EmbeddingState


class _CountingFactory:
    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.calls = 0
        self._count_lock = threading.Lock()

    def __call__(self) -> EmbeddingService:
        with self._count_lock:
            self.calls += 1
        time.sleep(self.delay)
        return EmbeddingService(model_dir="", enabled=False, ram_gb=0.0, has_vnni=False)


@pytest.fixture
def counting_factory(monkeypatch):
    embeddings.reset_embedding_service_for_tests()
    factory = _CountingFactory()
    monkeypatch.setattr(embeddings, "_build_default_service", factory)
    yield factory
    embeddings.reset_embedding_service_for_tests()


def test_concurrent_get_builds_exactly_one_instance(counting_factory):
    results: list[EmbeddingService] = []
    results_lock = threading.Lock()

    def _get() -> None:
        svc = embeddings.get_embedding_service()
        with results_lock:
            results.append(svc)

    threads = [threading.Thread(target=_get) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == 2
    assert counting_factory.calls == 1
    assert results[0] is results[1]
    assert embeddings.get_embedding_service() is results[0]


def test_startup_worker_and_knowledge_share_the_global_instance(counting_factory):
    """Mirror of the memory-server startup: worker construction and the
    knowledge runtime's resolve run concurrently in ``to_thread``."""
    from app.memory_server import knowledge_routes
    from memory.embedding_worker import EmbeddingWarmupWorker

    def _build_worker() -> EmbeddingWarmupWorker:
        return EmbeddingWarmupWorker(
            get_persona_manager=lambda: None,
            get_reflection_engine=lambda: None,
            get_fact_store=lambda: None,
            get_character_names=lambda: [],
            warmup_delay_seconds=30.0,
        )

    async def _startup():
        return await asyncio.gather(
            asyncio.to_thread(_build_worker),
            asyncio.to_thread(knowledge_routes._resolve_embedding_service),
        )

    worker, knowledge_service = asyncio.run(_startup())

    assert counting_factory.calls == 1
    # The instance the worker will request_load() on must be the one every
    # reader (hybrid_recall / turn_context / knowledge) sees.
    assert worker._service is embeddings.get_embedding_service()
    assert knowledge_service is worker._service


def test_release_clears_singleton_and_next_get_rebuilds(counting_factory):
    first = embeddings.get_embedding_service()
    asyncio.run(embeddings.release_embedding_service())
    assert first.state_name() == EmbeddingState.CLOSED.value
    assert embeddings._SERVICE is None

    second = embeddings.get_embedding_service()
    assert second is not first
    assert counting_factory.calls == 2


def test_release_keeps_instance_installed_by_another_caller(counting_factory, monkeypatch):
    """If a different instance was installed while the old one was closing,
    release must not drop it (compare-and-clear)."""
    first = embeddings.get_embedding_service()
    replacement = EmbeddingService(model_dir="", enabled=False, ram_gb=0.0, has_vnni=False)
    original_close = EmbeddingService.close

    async def _close_and_swap(self):
        await original_close(self)
        embeddings._SERVICE = replacement

    monkeypatch.setattr(EmbeddingService, "close", _close_and_swap)
    asyncio.run(embeddings.release_embedding_service())

    assert first.state_name() == EmbeddingState.CLOSED.value
    assert embeddings._SERVICE is replacement
