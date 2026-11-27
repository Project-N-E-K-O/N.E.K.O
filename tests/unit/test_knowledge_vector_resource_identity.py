"""Reuse certified vectors and defer encoding until the current resource identity is proven.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

from contextlib import closing
import sqlite3

import pytest

from knowledge.service import KnowledgeService
from tests.knowledge_archive_fixtures import _PackageEmbedder, make_archive, specification
from tests.knowledge_fixtures import _finished, _import, _manual_index, _pack

pytestmark = pytest.mark.unit


async def test_background_busy_defers_without_attempts_or_failure(tmp_path):
    class BusyEmbedder(_PackageEmbedder):
        def __init__(self):
            self.calls = []

        async def embed_documents(self, texts):
            self.calls.append(list(texts))
            return await super().embed_documents(texts)

    service = KnowledgeService(tmp_path)
    await service.start()
    await _manual_index(service)
    try:
        await _import(service, _pack())
        service.embedder = BusyEmbedder()
        assert await service._indexer._index_round() == 0
        assert len(service.embedder.calls) == 1 and service.embedder.calls[0]
        with closing(sqlite3.connect(service._store.database_path)) as conn:
            assert conn.execute("SELECT MAX(attempts) FROM chunks").fetchone()[0] == 0
        assert service._embedding_state() == "ready"
        assert service._store.pending_chunks(model_id=service.embedder.model_id(), pack_ids=["demo-memes"], limit=100)
    finally:
        await service.stop()


async def test_portable_vectors_survive_export_and_deleted_index_without_encoding(tmp_path):
    root = tmp_path / "installed"
    service = KnowledgeService(root, embedder=_PackageEmbedder())
    await service.start()
    await _manual_index(service)
    try:
        imported = await service.import_pack(make_archive(tmp_path / "source.zip"))
        assert (await _finished(service, imported))["state"] == "active"
        assert len(service._store.vectors_for_pack("imp.example", service.embedder.model_id())) == 1
        exported = await service.export_pack("imp.example", format_version=2)
        finished = await _finished(service, exported)
        assert finished["state"] == "active"
        assert service.artifact_path(finished["result"]["artifact_id"]).suffix == ".zip"
        assert await service._indexer._index_round() == 0
    finally:
        await service.stop()
    # Only the disposable SQLite files disappear; exact ZIP/original pack
    # bytes remain available to the normal startup reconciliation path.
    service._store.remove_files()
    reopened = KnowledgeService(root, embedder=_PackageEmbedder())
    await reopened.start()
    try:
        assert reopened.availability()["ready"]
        assert len(reopened._store.vectors_for_pack("imp.example", reopened.embedder.model_id())) == 1
    finally:
        await reopened.stop()


async def test_new_tokenizer_replans_complete_inputs_before_any_encoding(tmp_path, monkeypatch):
    from knowledge import service as module

    class SmallInputs(_PackageEmbedder):
        def __init__(self):
            self.inputs = []

        def spec(self):
            return {**specification(), "max_length": 100, "model_id": "small-test"}

        def count_tokens(self, texts):
            return [len(text) for text in texts]

        async def embed_documents(self, texts):
            assert all(len(text) <= 100 for text in texts)
            self.inputs.extend(texts)
            return [[1.0] + [0.0] * 63 for _text in texts]

    service = KnowledgeService(tmp_path)
    await service.start()
    await _manual_index(service)
    try:
        value = (await service.preview_source("n.txt", ("Lighthouse coast. " * 80 + "TAILMARK").encode()))["preview"]
        assert (await _finished(service, await service.commit_source(value["preview_id"], value["sha256"])))["state"] == "active"
        embedder = SmallInputs()
        service.embedder = embedder
        # Only the scheduling pause moves; token planning still uses service state.
        from knowledge import indexing
        monkeypatch.setattr(indexing, "_batch_pause", lambda _: 0)
        assert await service._indexer._index_round() > 0
        assert any("TAILMARK" in text for text in embedder.inputs)
        assert service._store.pending_chunks(model_id=embedder.model_id(), pack_ids=[next(iter(service._registry.packs))], limit=100) == []
        from knowledge.retrieval import RankedHit

        pack_id = next(iter(service._registry.packs))
        await service.set_pack_enabled(pack_id, True)
        with sqlite3.connect(service._store.database_path) as conn:
            entry_id, last = conn.execute("SELECT entry_id,MAX(chunk_index) FROM chunks").fetchone()
        ranked = RankedHit(entry_id=entry_id, score=1.0, exact=False, lexical_rank=None,
                           semantic_score=1.0, semantic_chunk=last)
        _hits, context = service._queries._render([ranked], "unrelated semantic wording", None, service._registry)
        assert "TAILMARK" in context
    finally:
        await service.stop()


async def test_package_imported_before_model_assets_seeds_later_without_encoding(tmp_path):
    service = KnowledgeService(tmp_path / "installed")
    await service.start()
    await _manual_index(service)
    try:
        imported = await service.import_pack(make_archive(tmp_path / "source.zip"))
        assert (await _finished(service, imported))["state"] == "active"
        assert service._store.vectors_for_pack("imp.example", specification()["model_id"]) == []
        service.embedder = _PackageEmbedder()
        assert await service._indexer._index_round() == 0
        assert len(service._store.vectors_for_pack("imp.example", service.embedder.model_id())) == 1
    finally:
        await service.stop()


async def test_same_profile_with_different_weights_never_queries_as_current(tmp_path):
    class ChangedWeights(_PackageEmbedder):
        def __init__(self):
            self.changed = False
            self.calls = 0

        def spec(self):
            value = specification()
            if self.changed:
                value["weights_sha256"]["model"] = "b" * 64
            return value

        async def embed_query(self, text):
            self.calls += 1
            return [1.0] + [0.0] * 63

    embedder = ChangedWeights()
    service = KnowledgeService(tmp_path / "installed", embedder=embedder)
    await service.start()
    await _manual_index(service)
    try:
        imported = await service.import_pack(make_archive(tmp_path / "source.zip"))
        assert (await _finished(service, imported))["state"] == "active"
        assert service._vectors is not None
        embedder.changed = True
        result = await service.query(query="coast")
        assert result["result"] == "matched" and result["retrieval_mode"] == "bm25"
        assert embedder.calls == 0
        assert service._store.load_vectors(embedder.model_id(), specification=embedder.spec()) is None
        pending = service._store.pending_chunks(model_id=embedder.model_id(), specification=embedder.spec(), pack_ids=["imp.example"], limit=10)
        assert len(pending) == 1
    finally:
        await service.stop()
