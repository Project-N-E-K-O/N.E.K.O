"""Rebuild plan selection falls back when a tokenizer cannot fit an entry header.

Startup reconcile and rollback restore share `_budgeted_plan`; startup is
exercised end to end."""

import pytest

from services.knowledge import service as module
from services.knowledge.models import decode_pack_bytes
from services.knowledge.service import KnowledgeService
from services.knowledge.store import count_pack_chunks
from tests.knowledge_archive_fixtures import specification
from tests.unit.test_knowledge_rebuild_capacity_refusal import _rows
from tests.knowledge_helpers import _stop_automatic_indexer
from tests.knowledge_fixtures import FakeEmbedder, _import, _pack, _raw


pytestmark = pytest.mark.unit

# Synthetic text only. The header alone exceeds the token budget below.
_LONG = {"title": "Lighthouse " * 20, "summary": "A coastal reference summary. " * 10,
         "content": "Short body."}


class TokenizingEmbedder(FakeEmbedder):
    """Tokenizer assets arrived after the pack was admitted without them."""

    def spec(self):
        return {**specification(), "max_length": 16}

    def count_tokens(self, texts):
        return [len(text) for text in texts]


async def _install(root):
    service = KnowledgeService(root, embedder=FakeEmbedder())  # No tokenizer: fit is None.
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        assert (await _import(service, _pack("imp.long", entries=[_LONG])))["state"] == "active"
        assert (await _import(service, _pack("imp.other", entries=[
            {"title": "Other", "content": "Unchanged reference."}])))["state"] == "active"
        with service._store._write() as connection:
            # Force a real startup rebuild of only the long pack.
            connection.execute("DELETE FROM entries_fts WHERE rowid IN "
                               "(SELECT id FROM entries WHERE pack_id='imp.long')")
        return {table: [tuple(row) for row in rows if "imp.other" in tuple(row)]
                for table, rows in _rows(service._store).items()}
    finally:
        await service.stop()


def test_budgeted_plan_skips_a_token_plan_that_cannot_fit_the_header():
    pack = decode_pack_bytes(_raw(_pack("imp.long", entries=[_LONG])))
    fit = lambda text: len(text) <= 16  # noqa: E731
    with pytest.raises(ValueError, match="embedding_input_too_large"):
        count_pack_chunks(pack, fit=fit)
    options, chunks = module._budgeted_plan(pack, {"fit": fit}, 0)
    assert "fit" not in options and chunks == count_pack_chunks(pack)
    # Any other failure is still surfaced, never mistaken for a fallback.
    def broken(text):
        raise ValueError("tokenizer_failed")

    with pytest.raises(ValueError, match="tokenizer_failed"):
        module._budgeted_plan(pack, {"fit": broken}, 0)


async def test_startup_rebuild_keeps_the_database_when_the_header_cannot_fit(tmp_path, monkeypatch):
    other_rows = await _install(tmp_path)
    reopened = KnowledgeService(tmp_path, embedder=TokenizingEmbedder())
    removed = []
    remove = reopened._store.remove_files

    def observed_remove():
        removed.append(True)
        remove()

    monkeypatch.setattr(reopened._store, "remove_files", observed_remove)
    await reopened.start()
    try:
        await _stop_automatic_indexer(reopened)
        assert not removed  # A token-plan rejection is not database corruption.
        assert reopened._state == "ready" and reopened._broken_packs == ()
        rows = _rows(reopened._store)
        assert {table: [tuple(row) for row in value if "imp.other" in tuple(row)]
                for table, value in rows.items()} == other_rows
        assert len(rows["entries_fts"]) == 2  # The long pack is searchable again.
        assert reopened._registry.packs["imp.long"].chunks == 1
    finally:
        await reopened.stop()


class VectorTokenizingEmbedder(TokenizingEmbedder):
    """Same tokenizer budget, producing vectors of the specification's size."""

    def model_id(self):
        return specification()["model_id"]

    async def embed_batch(self, texts):
        return [[1.0] + [0.0] * 63 for _ in texts]

    embed_documents = embed_batch


async def test_index_rounds_refuse_an_unfittable_replan_once_and_embed_the_rest(tmp_path):
    # Admitted without a tokenizer: one entry whose header alone cannot fit,
    # and one whose legacy input already fits the later token budget.
    installer = KnowledgeService(tmp_path, embedder=FakeEmbedder())
    await installer.start()
    try:
        await _stop_automatic_indexer(installer)
        assert (await _import(installer, _pack("imp.long", entries=[
            _LONG, {"title": "N", "content": "ok body"}])))["state"] == "active"
    finally:
        await installer.stop()
    service = KnowledgeService(tmp_path, embedder=VectorTokenizingEmbedder())
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        replans = []
        replan = service._replan_vector_inputs

        async def observed(pack_ids, **kwargs):
            replans.append(list(pack_ids))
            return await replan(pack_ids, **kwargs)

        service._replan_vector_inputs = observed
        await service._indexer._index_round()
        generation = service._vector_generation
        stats = service._store.chunk_stats(specification()["model_id"], specification=VectorTokenizingEmbedder().spec())
        assert stats["imp.long"]["ready"] == 1  # The fitting entry still gets its vector.
        for _round in range(3):
            await service._indexer._index_round()
        # Re-tokenizing the whole pack cannot change the outcome; it must not
        # be retried, nor invalidate the library's vector snapshot, each round.
        assert replans == [["imp.long"]]
        assert service._vector_generation == generation
        # A changed pack reopens the refusal and gets a fresh token plan.
        assert (await _import(service, _pack("imp.long", entries=[
            {"title": "L", "content": "fixed body"}, {"title": "N", "content": "ok body"}])))["state"] == "active"
        await service._indexer._index_round()
        assert "imp.long" not in service._refused_replans
        stats = service._store.chunk_stats(specification()["model_id"], specification=VectorTokenizingEmbedder().spec())
        assert stats["imp.long"]["ready"] == stats["imp.long"]["total"] == 2
    finally:
        await service.stop()


async def test_new_import_reports_an_unfittable_header_as_invalid_entry(tmp_path):
    service = KnowledgeService(tmp_path, embedder=TokenizingEmbedder())
    await service.start()
    try:
        result = await service.import_pack(_raw(_pack("imp.long", entries=[_LONG])))
        assert result == {"ok": False, "reason": "invalid_entry"}
        assert "imp.long" not in service._registry.packs
    finally:
        await service.stop()


async def test_commit_reports_an_unfittable_header_when_assets_arrive_after_admission(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path, embedder=TokenizingEmbedder())
    await service.start()
    try:
        fit = service._chunk_fit
        calls = []

        def assets_arrive_after_admission():
            calls.append(True)
            return None if len(calls) == 1 else fit()

        monkeypatch.setattr(service, "_chunk_fit", assets_arrive_after_admission)
        job = await _import(service, _pack("imp.long", entries=[_LONG]))
        assert len(calls) >= 2
        assert job["state"] == "failed" and job["reason"] == "invalid_entry"
        assert "imp.long" not in service._registry.packs
    finally:
        await service.stop()
