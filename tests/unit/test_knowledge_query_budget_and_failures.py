"""Bound query work and preserve keyword fallback, diagnostics and worker leases.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from knowledge import querying as query_module
import knowledge.service as service_module
# pytest resolves this explicitly imported fixture by argument name.
from tests.knowledge_fixtures import FakeEmbedder, _import, _pack, _started, fast_indexer  # noqa: F401

pytestmark = pytest.mark.unit


async def test_embedding_not_ready_means_waiting_and_bm25(tmp_path, fast_indexer):
    service = await _started(tmp_path, FakeEmbedder(state="loading"))
    try:
        await _import(service, _pack())
        await asyncio.sleep(0.2)
        (pack,) = await service.list_packs()
        assert pack["vector_state"] == "waiting"
        assert (await service.query(query="绝绝子"))["retrieval_mode"] == "bm25"
    finally:
        await service.stop()


async def test_slow_query_embedding_falls_back_to_bm25_within_budget(tmp_path, fast_indexer):
    embedder = FakeEmbedder()
    service = await _started(tmp_path, embedder)
    try:
        await _import(service, _pack())
        for _ in range(300):
            if service._vectors is not None:
                break
            await asyncio.sleep(0.02)
        embedder.delay = 2.0
        result = await service.query(query="绝绝子", budget_ms=300)
        assert result["result"] == "matched"
        assert result["retrieval_mode"] == "bm25"
        assert result["elapsed_ms"] < 1_000
    finally:
        await service.stop()


async def test_query_failures_are_recorded_for_diagnostics(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())

        def explode(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(service._store, "lexical_candidates", explode)
        result = await service.query(query="绝绝子")
        assert result["result"] == "error"
        record = service.diagnostics.snapshot()["queries"][0]
        assert record["result"] == "error"
        assert record["error_type"] == "RuntimeError"
        assert "绝绝子" not in json.dumps(record, ensure_ascii=False)
    finally:
        await service.stop()


async def test_busy_and_timeout_results(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        for _ in range(service_module.QUERY_CONCURRENCY):
            await service._query_slots.acquire()
        assert (await service.query(query="绝绝子"))["result"] == "busy"
        for _ in range(service_module.QUERY_CONCURRENCY):
            service._query_slots.release()

        original = service._store.lexical_candidates

        def slow(*args, **kwargs):
            import time

            time.sleep(0.3)
            return original(*args, **kwargs)

        monkeypatch.setattr(service._store, "lexical_candidates", slow)
        assert (await service.query(query="绝绝子", budget_ms=100))["result"] == "timeout"
    finally:
        await service.stop()


async def test_abandoned_query_embeddings_are_bounded(tmp_path, fast_indexer, monkeypatch):
    # Each lookup waits 0.2 s for its vector and keeps a generous 1 s for the
    # keyword result and rendering, so only the bound under test can fail.
    monkeypatch.setattr(query_module, "QUERY_RENDER_RESERVE_SECONDS", 1.0)
    embedder = FakeEmbedder()
    service = await _started(tmp_path, embedder)
    try:
        await _import(service, _pack())
        for _ in range(300):
            if service._vectors is not None:
                break
            await asyncio.sleep(0.02)
        embedder.delay = 5.0  # still running when every lookup below is done
        started = []
        original = embedder.embed

        async def counting(text):
            started.append(text)
            return await original(text)

        embedder.embed = counting
        for _ in range(6):
            result = await service.query(query="绝绝子", budget_ms=1200)
            assert result["result"] == "matched"
        assert len(started) == query_module.MAX_QUERY_EMBEDDINGS
    finally:
        await service.stop()


async def test_no_query_embedding_when_no_eligible_pack_uses_vectors(tmp_path, fast_indexer):
    embedder = FakeEmbedder()
    service = await _started(tmp_path, embedder)
    try:
        await _import(service, _pack())
        for _ in range(300):
            if service._vectors is not None:
                break
            await asyncio.sleep(0.02)
        assert service._vectors is not None
        await service.set_pack_local_embedding("demo-memes", False)
        service._vectors = service._vectors  # an old snapshot is still around
        calls = []
        original = embedder.embed

        async def counting(text):
            calls.append(text)
            return await original(text)

        embedder.embed = counting
        result = await service.query(query="绝绝子")
        assert result["result"] == "matched"
        assert calls == []
    finally:
        await service.stop()


async def test_fusion_runs_off_the_event_loop(tmp_path, monkeypatch):
    import threading

    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        threads = []
        real_fuse = query_module.fuse

        def recording(*args, **kwargs):
            threads.append(threading.current_thread() is threading.main_thread())
            return real_fuse(*args, **kwargs)

        monkeypatch.setattr(query_module, "fuse", recording)
        assert (await service.query(query="绝绝子"))["result"] == "matched"
        assert threads == [False]
    finally:
        await service.stop()


async def test_a_timed_out_query_keeps_its_slot_until_its_thread_work_ends(tmp_path, monkeypatch):
    import time

    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        real_fuse = query_module.fuse

        def slow_fuse(*args, **kwargs):
            time.sleep(0.6)
            return real_fuse(*args, **kwargs)

        monkeypatch.setattr(query_module, "fuse", slow_fuse)
        result = await service.query(query="绝绝子", budget_ms=100)
        assert result["result"] == "timeout"
        assert service._query_slots._value == service_module.QUERY_CONCURRENCY - 1
        for _ in range(100):
            if service._query_slots._value == service_module.QUERY_CONCURRENCY:
                break
            await asyncio.sleep(0.02)
        assert service._query_slots._value == service_module.QUERY_CONCURRENCY
    finally:
        await service.stop()
