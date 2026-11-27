"""Keep unavailable resource specifications retryable without refresh loops."""

import asyncio

import pytest

from knowledge.service import KnowledgeService
from tests.knowledge_fixtures import _import, _manual_index, _pack
from tests.knowledge_archive_fixtures import _prepare_export_vectors


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("scheduled", [False, True])
async def test_snapshot_refresh_retries_after_a_transient_missing_spec(tmp_path, monkeypatch, scheduled):
    service = KnowledgeService(tmp_path)
    await service.start()
    await _manual_index(service)
    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        specification = service.embedder.spec()
        missing = True
        # READY may retain its profile identity while resource verification
        # temporarily fails; recovery does not change the generation or ID.
        monkeypatch.setattr(service.embedder, "model_id", lambda: specification["model_id"])
        monkeypatch.setattr(service.embedder, "spec", lambda: None if missing else specification)
        loads = []
        original = service._indexer._load_vector_snapshot
        def record(model_id):
            loads.append(model_id)
            return original(model_id)
        monkeypatch.setattr(service._indexer, "_load_vector_snapshot", record)
        key = (service._vector_generation, specification["model_id"])
        if scheduled:
            service._indexer._schedule_vector_refresh()
            first = service._vector_task
            assert first is not None
            assert await first is False
            await asyncio.sleep(0)  # Deliver its real done callback.
            assert service._vector_task is first and len(loads) == 1
        else:
            await service._indexer._refresh_vectors_now()
        assert service._vectors is None
        assert service._vectors_built_for is None

        missing = False
        service._indexer._schedule_vector_refresh()
        retry = service._vector_task
        assert retry is not None and await retry is True
        await asyncio.sleep(0)
        assert len(loads) == 2
        assert service._vectors_built_for == key
        assert service._vectors is not None and service._vectors.matrix.shape == (2, 64)
    finally:
        await service.stop()
