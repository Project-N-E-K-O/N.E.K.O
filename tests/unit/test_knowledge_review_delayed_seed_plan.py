"""Recover an archived chunk plan after tokenizer assets become available."""

from dataclasses import replace
import json
import struct

import pytest

from knowledge import service as module
from knowledge.archive import export_archive
from knowledge.chunking import derive_chunks
from knowledge.models import parse_pack
from knowledge.service import KnowledgeService
from tests.unit.test_knowledge_archive_v2 import specification
from tests.unit.test_knowledge_unified_core import _finished, _manual_index


pytestmark = pytest.mark.unit


class _LaterEncoder:
    """Expose a deterministic full-input counter and record actual backfill."""

    def __init__(self, ready=False):
        self.ready = ready
        self.identity = specification()
        self.calls = []

    def state(self):
        return "ready" if self.ready else "unavailable"

    def model_id(self):
        return self.identity["model_id"]

    def spec(self):
        return self.identity if self.ready else None

    def count_tokens(self, texts):
        return [len(text) for text in texts] if self.ready else None

    async def embed_documents(self, texts):
        self.calls.extend(texts)
        return [[1.0] + [0.0] * 63 for _ in texts]

    embed_batch = embed_documents


def _archive(tmp_path):
    payload = {
        "schema_version": 1, "pack_id": "imp.delayed", "material_type": "knowledge",
        "source": {"name": "Delayed layout", "homepage": "", "license": "CC0"},
        "entries": [{"title": "Reference", "content": "\n\n".join(
            f"{index:03d} " + "x" * 696 for index in range(100))}],
    }
    pack = parse_pack(payload)
    blob = struct.pack("<64f", 1.0, *([0.0] * 63))
    rows = [{"entry_title": entry.title, "chunk_index": chunk.chunk_index,
             "text_hash": chunk.text_hash, "embedding_text": chunk.embed_text, "vector": blob}
            for entry in pack.entries for chunk in derive_chunks(entry, fit=lambda text: len(text) <= 1024)]
    assert len(rows) == 100
    result = export_archive(tmp_path / "exports", payload, specification=specification(), vectors=rows)
    return tmp_path / "exports" / (result["sha256"] + ".neko-knowledge.zip")


async def _install(tmp_path, encoder):
    service = KnowledgeService(tmp_path / "library", embedder=encoder)
    await service.start()
    await _manual_index(service)
    source = _archive(tmp_path)
    try:
        assert (await _finished(service, await service.import_pack(source)))["state"] == "active"
        return service, source
    except BaseException:
        await service.stop()
        raise


@pytest.mark.parametrize("ready_at_import", [False, True])
async def test_compatible_archive_plan_is_ready_without_reembedding(tmp_path, monkeypatch, ready_at_import):
    encoder = _LaterEncoder(ready_at_import)
    service, source = await _install(tmp_path, encoder)
    try:
        record = service._registry.packs["imp.delayed"]
        assert record.chunks == (100 if ready_at_import else 96)
        raw = (service.root / "packs" / record.file_name).read_bytes()
        zip_bytes = source.read_bytes()
        encoder.ready = True
        # All legacy inputs fit: ordinary overflow replanning cannot repair
        # this difference between the 96-row fallback and 100 archived rows.
        pending = service._store.pending_chunks(model_id=encoder.model_id(), pack_ids=[record.pack_id], limit=200)
        assert all(len(text) <= 1024 for _id, _hash, text in pending)
        monkeypatch.setattr(module, "INDEX_BATCH_PAUSE_SECONDS", 0)
        await service._index_round()
        await service._index_round()
        assert service._store.chunk_stats(encoder.model_id(), specification=encoder.spec())[record.pack_id] == {
            "total": 100, "ready": 100, "failed": 0,
        }
        assert encoder.calls == []
        assert service._registry.packs[record.pack_id].chunks == 100
        assert json.loads((service.root / "registry.json").read_text())["packs"][record.pack_id]["chunks"] == 100
        assert (service.root / "packs" / record.file_name).read_bytes() == raw
        assert source.read_bytes() == zip_bytes
    finally:
        await service.stop()


@pytest.mark.parametrize("limit", ["MAX_CHUNKS_PER_PACK", "MAX_TOTAL_CHUNKS"])
async def test_capacity_refusal_keeps_legacy_rows_and_retries_after_space_returns(tmp_path, monkeypatch, limit):
    encoder = _LaterEncoder()
    service, _source = await _install(tmp_path, encoder)
    try:
        encoder.ready = True
        original = getattr(module, limit)
        monkeypatch.setattr(module, limit, 98)
        await service._seed_retained_packages(["imp.delayed"], encoder.spec())
        assert service._registry.packs["imp.delayed"].chunks == 96
        assert service._store.chunk_stats(None)["imp.delayed"]["total"] == 96
        assert not service._seeded_artifacts and not service._seed_failures
        assert service._store.get_entry("imp.delayed", "Reference").content.endswith("x" * 696)
        monkeypatch.setattr(module, limit, original)
        await service._seed_retained_packages(["imp.delayed"], encoder.spec())
        assert service._store.chunk_stats(encoder.model_id(), specification=encoder.spec())["imp.delayed"]["ready"] == 100
        assert service._registry.packs["imp.delayed"].chunks == 100
        assert encoder.calls == []
    finally:
        await service.stop()


async def test_failed_registry_flush_keeps_real_counts_repairable_and_invalidates_snapshots(tmp_path, monkeypatch):
    encoder = _LaterEncoder()
    service, _source = await _install(tmp_path, encoder)
    try:
        encoder.ready = True
        generation = service._vector_generation
        original = module.save_registry

        def fail_flush(*_args):
            raise OSError("synthetic registry flush failure")

        monkeypatch.setattr(module, "save_registry", fail_flush)
        await service._seed_retained_packages(["imp.delayed"], encoder.spec())
        assert service._pending_chunk_reconcile
        assert service._store.chunk_stats(encoder.model_id(), specification=encoder.spec())["imp.delayed"]["ready"] == 100
        assert service._registry.packs["imp.delayed"].chunks == 96
        assert service._vectors is None and service._vectors_built_for is None
        assert service._vector_generation > generation
        assert not service._seeded_artifacts and not service._seed_failures
        monkeypatch.setattr(module, "save_registry", original)
        await service._index_round()
        assert not service._pending_chunk_reconcile
        assert service._registry.packs["imp.delayed"].chunks == 100
        assert json.loads((service.root / "registry.json").read_text())["packs"]["imp.delayed"]["chunks"] == 100
        assert encoder.calls == []
    finally:
        await service.stop()


async def test_archive_plan_must_match_the_current_canonical_pack_digest(tmp_path):
    encoder = _LaterEncoder()
    service, _source = await _install(tmp_path, encoder)
    try:
        encoder.ready = True
        record = service._registry.packs["imp.delayed"]
        service._publish_registry(service._registry.with_pack(replace(record, pack_sha256="b" * 64)))
        await service._seed_retained_packages([record.pack_id], encoder.spec())
        assert service._store.chunk_stats(encoder.model_id(), specification=encoder.spec())[record.pack_id] == {
            "total": 96, "ready": 0, "failed": 0,
        }
        assert not service._seeded_artifacts and not service._seed_failures
        service._publish_registry(service._registry.with_pack(record))
        await service._seed_retained_packages([record.pack_id], encoder.spec())
        assert service._store.chunk_stats(encoder.model_id(), specification=encoder.spec())[record.pack_id]["ready"] == 100
    finally:
        await service.stop()


async def test_incompatible_archive_preserves_legacy_plan_for_normal_backfill(tmp_path, monkeypatch):
    encoder = _LaterEncoder()
    service, _source = await _install(tmp_path, encoder)
    try:
        encoder.ready = True
        encoder.identity = {**encoder.identity, "weights_sha256": {
            key: "b" * 64 for key in encoder.identity["weights_sha256"]}}
        await service._seed_retained_packages(["imp.delayed"], encoder.spec())
        assert service._store.chunk_stats(encoder.model_id(), specification=encoder.spec())["imp.delayed"] == {
            "total": 96, "ready": 0, "failed": 0,
        }
        monkeypatch.setattr(module, "INDEX_BATCH_PAUSE_SECONDS", 0)
        await service._index_round()
        await service._index_round()
        assert service._store.chunk_stats(encoder.model_id(), specification=encoder.spec())["imp.delayed"]["ready"] == 96
        assert len(encoder.calls) == 96
    finally:
        await service.stop()


async def test_unfittable_header_keeps_existing_backfill_rejection_path(tmp_path, monkeypatch):
    encoder = _LaterEncoder()
    service, _source = await _install(tmp_path, encoder)
    try:
        encoder.ready = True
        monkeypatch.setattr(encoder, "count_tokens", lambda texts: [len(text) * 200 for text in texts])
        # Restoring the archive plan must not turn an existing per-pack header
        # refusal into an exception that aborts the entire index round.
        await service._seed_retained_packages(["imp.delayed"], encoder.spec())
        monkeypatch.setattr(module, "INDEX_BATCH_PAUSE_SECONDS", 0)
        await service._index_round()
        await service._index_round()
        stats = service._store.chunk_stats(encoder.model_id(), specification=encoder.spec())["imp.delayed"]
        assert stats == {"total": 96, "ready": 0, "failed": 96}
        assert service._store.get_entry("imp.delayed", "Reference") is not None
        assert encoder.calls == []
    finally:
        await service.stop()
