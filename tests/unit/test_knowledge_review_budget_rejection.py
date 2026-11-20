"""Keep admitted data intact when every rebuild plan exceeds its budget."""

import hashlib
import json
from contextlib import asynccontextmanager

import pytest

from knowledge import service as module
from knowledge.models import decode_pack_bytes
from knowledge.service import KnowledgeService
from knowledge.store import count_pack_chunks
from knowledge.archive import read_archive
from tests.unit.test_knowledge_archive_v2 import make_archive, specification
from tests.unit.test_knowledge_review_isolation import _stop_automatic_indexer
from tests.unit.test_knowledge_subsystem import FakeEmbedder, _import, _pack, _raw
from tests.unit.test_knowledge_unified_core import _finished
from utils.knowledge_package import canonical_json

pytestmark = pytest.mark.unit


class ArchiveProvider(FakeEmbedder):
    """Accept the archive's one full input without loading a native model."""

    def __init__(self):
        super().__init__(model_id=specification()["model_id"])

    def spec(self):
        return specification()

    def count_tokens(self, texts):
        return [(len(text) + 1) // 2 for text in texts]

    async def embed_batch(self, texts):
        return [[1.0] + [0.0] * 63 for _ in texts]

    embed_documents = embed_batch


def _archive(path, *, fragments=1):
    """A valid one-row product whose ordinary local layout needs two rows."""
    def mutate(manifest, members):
        pack = json.loads(members["pack.json"])
        pack["entries"][0]["content"] = "A lighthouse reference. " * 70
        # Keep the source canonical so its original vector is genuinely reusable.
        pack["entries"][0]["content"] = pack["entries"][0]["content"].strip()
        content = pack["entries"][0]["content"]
        rows = []
        for index in range(fragments):
            text = "Example\n" + content[len(content) * index // fragments:len(content) * (index + 1) // fragments]
            rows.append({"entry_title": "Example", "chunk_index": index, "embedding_text": text,
                         "text_hash": hashlib.sha256(text.encode()).hexdigest()})
        members["pack.json"] = canonical_json(pack)
        members["chunks.jsonl"] = b"".join(canonical_json(row) + b"\n" for row in rows)
        members["vectors.f32"] *= fragments
        manifest["counts"]["chunks"] = fragments
        manifest["files"] = {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                             for name, raw in members.items()}
    return make_archive(path, mutate=mutate)


def _rows(store):
    """Observe actual indexed versions and inputs, including retained vectors."""
    with store._read() as connection:
        return {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("packs", "entries", "chunks", "surfaces", "entries_fts")}


def _limit(monkeypatch, reason):
    # Both existing packs have one admitted row. Only the selected budget binds.
    monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", 1 if reason == "pack" else 20)
    monkeypatch.setattr(module, "MAX_TOTAL_CHUNKS", 2 if reason == "total" else 20)


@asynccontextmanager
async def _installed(tmp_path):
    service = KnowledgeService(tmp_path / "library", embedder=ArchiveProvider())
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        source = _archive(tmp_path / "source.zip")
        assert (await _finished(service, await service.import_pack(source)))["state"] == "active"
        assert (await _import(service, _pack("imp.other", entries=[{"title": "Other", "content": "Unchanged reference."}])))["state"] == "active"
        record = service._registry.packs["imp.example"]
        raw = (service.root / "packs" / record.file_name).read_bytes()
        assert record.chunks == 1 and count_pack_chunks(decode_pack_bytes(raw)) == 2
        # Only this temporary fixture's optional retained ZIP is unavailable.
        service.artifact_path(record.archive_sha256).unlink()
        yield service, raw
    finally:
        await service.stop()


@pytest.mark.parametrize("reason", ["pack", "total"])
def test_budgeted_plan_reports_refusal_instead_of_returning_an_overflow(tmp_path, monkeypatch, reason):
    checked = read_archive(_archive(tmp_path / "three-rows.zip", fragments=3), expected_spec=specification())
    pack = decode_pack_bytes(canonical_json(checked["pack"]))
    _limit(monkeypatch, reason)
    others = 1 if reason == "total" else 0
    # The archive candidates have three rows; even the final two-row legacy
    # candidate cannot fit the one remaining slot and must not be returned.
    assert count_pack_chunks(pack, seed_vectors=checked["seed_vectors"]) == 3
    assert count_pack_chunks(pack) == 2
    options = {"fit": lambda text: len(text) <= 1000, "seed_vectors": checked["seed_vectors"]}
    assert module._budgeted_plan(pack, options, others) is None


@pytest.mark.parametrize("reason", ["pack", "total"])
async def test_startup_refusal_keeps_index_registry_and_other_packs(tmp_path, monkeypatch, reason):
    async with _installed(tmp_path) as (original, raw):
        record = original._registry.packs["imp.example"]
        # Force a real rebuild need while retaining the other tables and pack.
        with original._store._write() as connection:
            connection.execute("DELETE FROM entries_fts WHERE rowid IN (SELECT id FROM entries WHERE pack_id=?)", (record.pack_id,))
        before = _rows(original._store)
        registry_bytes = (original.root / "registry.json").read_bytes()
        await original.stop()
        _limit(monkeypatch, reason)
        reopened = KnowledgeService(original.root, embedder=ArchiveProvider())
        removed = []
        remove = reopened._store.remove_files

        def observed_remove():
            removed.append(True)
            remove()

        monkeypatch.setattr(reopened._store, "remove_files", observed_remove)
        await reopened.start()
        try:
            await _stop_automatic_indexer(reopened)
            assert not removed  # Capacity is not database corruption.
            assert _rows(reopened._store) == before
            assert (reopened.root / "registry.json").read_bytes() == registry_bytes
            assert (reopened.root / "packs" / record.file_name).read_bytes() == raw
            assert reopened._broken_packs == (record.pack_id,)
            # Reuse the existing repair entry after the user makes room.
            monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", 2)
            monkeypatch.setattr(module, "MAX_TOTAL_CHUNKS", 3)
            repaired = await reopened.import_pack(raw)
            assert (await _finished(reopened, repaired))["state"] == "active"
            assert record.pack_id not in reopened._broken_packs
            assert reopened._registry.packs[record.pack_id].chunks == 2
        finally:
            await reopened.stop()


@pytest.mark.parametrize("reason", ["pack", "total"])
async def test_rollback_refusal_preserves_rows_and_keeps_the_pack_repairable(tmp_path, monkeypatch, reason):
    async with _installed(tmp_path) as (service, raw):
        previous = service._registry.packs["imp.example"]
        # Model SQLite having committed the failed replacement before registry
        # persistence failed. Its version must not be mistaken for the old one.
        replacement = decode_pack_bytes(_raw(_pack(previous.pack_id, entries=[{"title": "New", "content": "A failed replacement."}])))
        service._store.replace_pack(replacement, pack_sha256="b" * 64)
        before = _rows(service._store)
        registry_bytes = (service.root / "registry.json").read_bytes()
        _limit(monkeypatch, reason)
        await service._thread(service._restore_index_blocking, previous.pack_id, previous)
        assert _rows(service._store) == before
        assert (service.root / "registry.json").read_bytes() == registry_bytes
        assert previous.pack_id in service._broken_packs
        assert service._pending_chunk_reconcile
        await service._thread(service._reconcile_chunk_counts_blocking)
        monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", 2)
        monkeypatch.setattr(module, "MAX_TOTAL_CHUNKS", 3)
        assert (await _finished(service, await service.import_pack(raw)))["state"] == "active"
        assert previous.pack_id not in service._broken_packs


async def test_refused_rollback_keeps_a_registered_index_usable_without_a_model(tmp_path, monkeypatch):
    """An intact registered version is retained; ordinary coordination needs no model."""
    async with _installed(tmp_path) as (service, _raw_pack):
        previous = service._registry.packs["imp.example"]
        before = _rows(service._store)
        _limit(monkeypatch, "pack")
        await service._thread(service._restore_index_blocking, previous.pack_id, previous)
        assert _rows(service._store) == before
        assert previous.pack_id not in service._broken_packs
        assert service._pending_chunk_reconcile
        service.embedder = None
        assert await service._index_round() == 0
        assert not service._pending_chunk_reconcile
        assert service._registry.packs[previous.pack_id] == previous


async def test_startup_refusal_counts_kept_rows_before_any_admission(tmp_path, monkeypatch):
    """Rows left by an unfinished replacement may outnumber the registry; capacity must see them."""
    async with _installed(tmp_path) as (original, _raw_pack):
        record = original._registry.packs["imp.example"]
        entries = [{"title": f"Left {index}", "content": f"Unfinished replacement row {index}."} for index in range(4)]
        leftover = decode_pack_bytes(_raw(_pack(record.pack_id, entries=entries)))
        original._store.replace_pack(leftover, pack_sha256="c" * 64)
        actual = original._store.chunk_stats(None)[record.pack_id]["total"]
        assert actual > record.chunks
        await original.stop()
        _limit(monkeypatch, "pack")
        reopened = KnowledgeService(original.root, embedder=ArchiveProvider())
        await reopened.start()
        try:
            await _stop_automatic_indexer(reopened)
            assert reopened._broken_packs == (record.pack_id,)
            # Counted at startup, before any admission can rely on it.
            assert reopened._registry.packs[record.pack_id].chunks == actual
            others = sum(item.chunks for key, item in reopened._registry.packs.items() if key != record.pack_id)
            monkeypatch.setattr(module, "MAX_TOTAL_CHUNKS", others + actual)
            assert (await reopened.import_pack(_raw(_pack("imp.third"))))["reason"] == "capacity_chunks"
        finally:
            await reopened.stop()
