"""Rebuild plan selection falls back when a tokenizer cannot fit an entry header.

Startup reconcile and rollback restore share `_budgeted_plan`; startup is
exercised end to end.
"""

import pytest

from knowledge import service as module
from knowledge.models import decode_pack_bytes
from knowledge.service import KnowledgeService
from knowledge.store import count_pack_chunks
from tests.unit.test_knowledge_archive_v2 import specification
from tests.unit.test_knowledge_review_budget_rejection import _rows
from tests.unit.test_knowledge_review_isolation import _stop_automatic_indexer
from tests.unit.test_knowledge_subsystem import FakeEmbedder, _import, _pack, _raw


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
