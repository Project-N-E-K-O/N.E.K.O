"""Exercise recovery and row identity across deferred knowledge backfill."""

import asyncio
import struct

import pytest

from knowledge import service as module
from knowledge.archive import export_archive
from knowledge.chunking import derive_chunks
from knowledge.models import KnowledgePackError, decode_pack_bytes, parse_pack
from knowledge.service import KnowledgeService
from knowledge.store import count_pack_chunks
from tests.unit.test_knowledge_archive_v2 import make_archive, specification
from tests.unit.test_knowledge_review_archive_chunks import _archive
from tests.unit.test_knowledge_review_isolation import _stop_automatic_indexer
from tests.unit.test_knowledge_subsystem import FakeEmbedder, _import, _pack, _raw
from tests.unit.test_knowledge_unified_core import _finished

pytestmark = pytest.mark.unit


class TokenProvider(FakeEmbedder):
    """Make token overflow deterministic without loading a model."""

    def __init__(self):
        super().__init__(model_id=specification()["model_id"])

    def spec(self):
        return specification()

    def count_tokens(self, texts):
        return [len(text) * 8 for text in texts]

    async def embed_batch(self, texts):
        return [[1.0] + [0.0] * 63 for _ in texts]

    embed_documents = embed_batch


async def test_missing_retained_zip_does_not_block_other_pack_backfill(tmp_path, monkeypatch):
    """The retained ZIP is optional once canonical text was installed."""
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        result = await service.import_pack(make_archive(tmp_path / "input.zip"))
        assert (await _finished(service, result))["state"] == "active"
        assert (await _import(service, _pack("imp.other", entries=[{"title": "Other", "content": "Short text."}])))["state"] == "active"
        record = service._registry.packs["imp.example"]
        service.artifact_path(record.archive_sha256).unlink()
        service.embedder = TokenProvider()
        monkeypatch.setattr(module, "INDEX_BATCH_PAUSE_SECONDS", 0)
        assert await service._index_round() == 2
        stats = service._store.chunk_stats(service.embedder.model_id(), specification=specification())
        assert stats["imp.example"]["ready"] == stats["imp.other"]["ready"] == 1
    finally:
        await service.stop()


async def test_replaced_row_is_not_exhausted_by_old_replan_snapshot(tmp_path, monkeypatch):
    """A replacement may reuse the old chunk ID while token planning awaits."""
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        assert (await _import(service, _pack("imp.race", entries=[{"title": "Same", "content": "x" * 900}])))["state"] == "active"
        service.embedder = TokenProvider()
        old = service._store.pending_chunks(model_id=service.embedder.model_id(), pack_ids=["imp.race"], limit=1)[0]
        replans = []

        async def replace_before_refusal(pack_ids):
            replans.append(pack_ids)
            replacement = decode_pack_bytes(_raw(_pack("imp.race", entries=[{"title": "Same", "content": "New short text."}])))
            # Reproduce the commit interleaving at the actual awaited replan.
            service._store.replace_pack(replacement, pack_sha256="b" * 64)
            current = service._store.pending_chunks(model_id=service.embedder.model_id(), pack_ids=["imp.race"], limit=1)[0]
            assert current[0] == old[0] and current[1] != old[1]
            raise KnowledgePackError("too_many_chunks")

        monkeypatch.setattr(service, "_replan_vector_inputs", replace_before_refusal)
        monkeypatch.setattr(module, "INDEX_BATCH_PAUSE_SECONDS", 0)
        await service._index_round()
        assert replans == [["imp.race"]]
        stats = service._store.chunk_stats(service.embedder.model_id(), specification=specification())["imp.race"]
        assert stats["failed"] == 0
        # The stale batch may defer this pack for one round; the fresh input
        # remains pending and succeeds without resetting any failure counter.
        assert len(service._store.pending_chunks(model_id=service.embedder.model_id(), pack_ids=["imp.race"], limit=1)) == 1
        await service._index_round()
        stats = service._store.chunk_stats(service.embedder.model_id(), specification=specification())["imp.race"]
        assert stats["ready"] == 1 and stats["failed"] == 0
    finally:
        await service.stop()


async def test_exhaust_attempts_checks_pack_identity_with_identical_text(tmp_path):
    """Identical input in a different pack must not inherit a stale verdict."""
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        entry = {"title": "Same", "content": "Identical input."}
        assert (await _import(service, _pack("imp.old", entries=[entry])))["state"] == "active"
        row = service._store.pending_chunks(model_id="test", pack_ids=["imp.old"], limit=1)[0]
        service._store.delete_pack("imp.old")
        replacement = decode_pack_bytes(_raw(_pack("imp.new", entries=[entry])))
        service._store.replace_pack(replacement, pack_sha256="b" * 64)
        current = service._store.pending_chunks(model_id="test", pack_ids=["imp.new"], limit=1)[0]
        assert current[:2] == row[:2]
        assert service._store.exhaust_attempts([(row[0], row[1], "imp.old")]) == 0
        assert service._store.chunk_stats("test")["imp.new"]["failed"] == 0
        # A current verdict on the exact same row still marks one failure.
        assert service._store.exhaust_attempts([(current[0], current[1], "imp.new")]) == 1
        assert service._store.chunk_stats("test")["imp.new"]["failed"] == 1
    finally:
        await service.stop()


async def test_capacity_refusal_resumes_when_another_pack_is_removed(tmp_path, monkeypatch):
    """Changed capacity reopens token planning without restarting or toggling."""
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        oversized = _pack("imp.waiting", entries=[{"title": "Large", "content": "x" * 900}])
        assert (await _import(service, oversized))["state"] == "active"
        assert (await _import(service, _pack("imp.occupant", entries=[{"title": "Small", "content": "Short."}])))["state"] == "active"
        service.embedder = TokenProvider()
        required = count_pack_chunks(decode_pack_bytes(_raw(oversized)), fit=service._chunk_fit())
        monkeypatch.setattr(module, "MAX_TOTAL_CHUNKS", required)
        monkeypatch.setattr(module, "INDEX_BATCH_PAUSE_SECONDS", 0)
        replan = service._replan_vector_inputs
        calls = []

        async def observed(pack_ids):
            calls.append(pack_ids)
            await replan(pack_ids)

        monkeypatch.setattr(service, "_replan_vector_inputs", observed)
        await service._index_round()
        stats = service._store.chunk_stats(service.embedder.model_id(), specification=specification())["imp.waiting"]
        assert stats["failed"] == stats["total"] > 0
        assert await service._index_round() == 0
        assert calls == [["imp.waiting"]]
        await service.remove_pack("imp.occupant")
        await service._index_round()
        stats = service._store.chunk_stats(service.embedder.model_id(), specification=specification())["imp.waiting"]
        assert stats["ready"] == stats["total"] == required
        assert stats["failed"] == 0
        assert calls == [["imp.waiting"], ["imp.waiting"]]
    finally:
        await service.stop()


async def test_archive_capacity_and_index_rebuild_preserve_the_installed_plan(tmp_path, monkeypatch):
    """Admission, commit and rebuilding a lost derived DB use actual archive rows."""
    pack, path, _checked, spec = _archive(tmp_path)

    class ArchiveProvider(TokenProvider):
        def __init__(self):
            super().__init__()
            self.calls = []

        def count_tokens(self, texts):
            return [(len(text) + 1) // 2 for text in texts]

        async def embed_batch(self, texts):
            self.calls.append(texts)
            return await super().embed_batch(texts)

        embed_documents = embed_batch

    provider = ArchiveProvider()
    root = tmp_path / "library"
    service = KnowledgeService(root, embedder=provider)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        # A fresh local split has 101 rows; the verified product needs only 97.
        monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", 97)
        result = await service.import_pack(path)
        assert result["ok"] is True
        assert (await _finished(service, result))["state"] == "active"
        assert service._registry.packs[pack.pack_id].chunks == 97
        assert service._store.chunk_stats(spec["model_id"], specification=spec)[pack.pack_id]["ready"] == 97
    finally:
        await service.stop()
    # Only the temporary test index is removed; the canonical text and product
    # are retained, just as when a damaged derived index needs rebuilding.
    service._store.remove_files()
    monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", 10_000)
    rebuilt = KnowledgeService(root, embedder=provider)
    await rebuilt.start()
    try:
        assert rebuilt._registry.packs[pack.pack_id].chunks == 97
        assert rebuilt._store.chunk_stats(spec["model_id"], specification=spec)[pack.pack_id]["ready"] == 97
        assert provider.calls == []
    finally:
        await rebuilt.stop()


async def test_rollback_restore_keeps_the_installed_archive_plan_and_its_vectors(tmp_path, monkeypatch):
    """A failed update's rollback rebuilds the old archive install with its own rows, not a local split."""
    pack, path, _checked, spec = _archive(tmp_path)

    class ArchiveProvider(TokenProvider):
        def count_tokens(self, texts):
            return [(len(text) + 1) // 2 for text in texts]

    service = KnowledgeService(tmp_path / "library", embedder=ArchiveProvider())
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        result = await service.import_pack(path)
        assert (await _finished(service, result))["state"] == "active"
        previous = service._registry.packs[pack.pack_id]
        assert previous.chunks == 97
        # The same restore a failed replacement runs after writing SQLite.
        service._store.delete_pack(pack.pack_id)
        await asyncio.to_thread(service._restore_index_blocking, pack.pack_id, previous)
        assert not service._pending_chunk_reconcile
        stats = service._store.chunk_stats(spec["model_id"], specification=spec)[pack.pack_id]
        assert stats == {"total": 97, "ready": 97, "failed": 0}
    finally:
        await service.stop()


async def test_index_rebuild_never_grows_an_archive_pack_past_the_total_budget(tmp_path, monkeypatch):
    """A product installed while assets were missing keeps its admitted size when rebuilt later."""
    spec = specification()
    payload = {
        "schema_version": 1, "pack_id": "archive-fine", "material_type": "knowledge",
        "source": {"name": "Fine", "homepage": "", "license": "CC0"},
        "entries": [{"title": "Reference", "content": "\n\n".join(f"{index:03d} " + "y" * 280 for index in range(40))}],
    }
    pack = parse_pack(payload)
    blob = struct.pack("<64f", 1.0, *([0.0] * 63))
    # The sender's tokenizer split finely: far more rows than the local legacy plan.
    rows = [{"entry_title": entry.title, "chunk_index": chunk.chunk_index, "text_hash": chunk.text_hash,
             "embedding_text": chunk.embed_text, "vector": blob}
            for entry in pack.entries for chunk in derive_chunks(entry, fit=lambda text: len(text) <= 300)]
    artifact = export_archive(tmp_path / "exports", payload, specification=spec, vectors=rows)
    path = tmp_path / "exports" / (artifact["sha256"] + ".neko-knowledge.zip")
    legacy = count_pack_chunks(pack)
    assert len(rows) > legacy

    class LateAssets(TokenProvider):
        ready = False

        def spec(self):
            return specification() if self.ready else None

        def count_tokens(self, texts):
            return [1 for _ in texts] if self.ready else None

    provider = LateAssets()
    root = tmp_path / "library"
    service = KnowledgeService(root, embedder=provider)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        assert (await _finished(service, await service.import_pack(path)))["state"] == "active"
        assert service._registry.packs[pack.pack_id].chunks == legacy
        assert (await _import(service, _pack("imp.other")))["state"] == "active"
        others = service._registry.packs["imp.other"].chunks
    finally:
        await service.stop()
    service._store.remove_files()
    # Assets arrive, and the remaining total budget only fits the admitted size.
    provider.ready = True
    monkeypatch.setattr(module, "MAX_TOTAL_CHUNKS", others + legacy)
    rebuilt = KnowledgeService(root, embedder=provider)
    await rebuilt.start()
    try:
        assert rebuilt._registry.packs[pack.pack_id].chunks == legacy
        assert rebuilt._store.chunk_stats(None)[pack.pack_id]["total"] == legacy
        assert sum(record.chunks for record in rebuilt._registry.packs.values()) <= others + legacy
    finally:
        await rebuilt.stop()


async def test_rebuild_and_rollback_refuse_when_no_plan_fits_and_keep_the_pack_recoverable(tmp_path, monkeypatch):
    """Archive layout at the per-pack limit, retained ZIP lost: only an over-limit local split remains."""
    pack, path, _checked, spec = _archive(tmp_path)

    class ArchiveProvider(TokenProvider):
        def count_tokens(self, texts):
            return [(len(text) + 1) // 2 for text in texts]

    provider = ArchiveProvider()
    root = tmp_path / "library"
    monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", 97)
    service = KnowledgeService(root, embedder=provider)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        assert (await _finished(service, await service.import_pack(path)))["state"] == "active"
        record = service._registry.packs[pack.pack_id]
        assert record.chunks == 97
        artifact = service.artifact_path(record.archive_sha256)
        kept = artifact.read_bytes()
        artifact.unlink()
        # Without the product only local layouts remain (97 legacy, 101 token
        # rows); a tighter budget leaves no plan that fits at all.
        monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", 96)
        # Rollback of a failed update: never writes an over-budget split, drops inconsistent rows.
        await asyncio.to_thread(service._restore_index_blocking, pack.pack_id, record)
        assert pack.pack_id not in service._store.chunk_stats(None)
    finally:
        await service.stop()
    # Startup rebuild of a lost index: refused without deleting the database or the record.
    service._store.remove_files()
    rebuilt = KnowledgeService(root, embedder=provider)
    await rebuilt.start()
    try:
        assert pack.pack_id in rebuilt._broken_packs
        assert rebuilt._registry.packs[pack.pack_id].chunks == 97
        assert pack.pack_id not in rebuilt._store.chunk_stats(None)
        assert (root / "packs" / record.file_name).is_file()
    finally:
        await rebuilt.stop()
    # Once the retained product is back and the budget allows it, the pack recovers.
    monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", 97)
    artifact.write_bytes(kept)
    recovered = KnowledgeService(root, embedder=provider)
    await recovered.start()
    try:
        assert pack.pack_id not in recovered._broken_packs
        assert recovered._store.chunk_stats(spec["model_id"], specification=spec)[pack.pack_id] == {
            "total": 97, "ready": 97, "failed": 0}
    finally:
        await recovered.stop()
