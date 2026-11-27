"""History retention and replan staleness regressions (synthetic packs only)."""

import pytest

from knowledge.service import KnowledgeService
from tests.knowledge_fixtures import _import, _manual_index, _pack


pytestmark = pytest.mark.unit

_A = [{"title": "T", "content": "Revision A reference."}]
_B = [{"title": "T", "content": "Revision B reference."}]


async def _a_b_a(service):
    for entries in (_A, _B, _A):
        assert (await _import(service, _pack("imp.rev", entries=entries)))["state"] == "active"
    record = service._registry.packs["imp.rev"]
    history = service.root / "history" / "imp.rev" / record.file_name
    assert history.is_file()  # A was archived when B replaced it.
    return record, history


@pytest.mark.parametrize("then", ["remove", "replace"])
async def test_damaged_active_file_never_overwrites_a_verified_history_copy(tmp_path, then):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        record, history = await _a_b_a(service)
        good = history.read_bytes()
        active = service.root / "packs" / record.file_name
        active.write_bytes(b"damaged synthetic bytes")
        if then == "remove":
            await service.remove_pack("imp.rev")
            assert not active.exists()  # The damaged pack can still be removed.
        else:
            assert (await _import(service, _pack("imp.rev", entries=[
                {"title": "T", "content": "Revision C reference."}])))["state"] == "active"
        assert history.read_bytes() == good
    finally:
        await service.stop()


async def test_verified_active_bytes_still_refresh_history(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        record, history = await _a_b_a(service)
        history.write_bytes(b"damaged history copy")
        await service.remove_pack("imp.rev")
        assert history.read_bytes() != b"damaged history copy"
    finally:
        await service.stop()


async def test_replan_skips_a_pack_whose_trigger_rows_were_replaced(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    await _manual_index(service)
    try:
        await _import(service, _pack("imp.rev", entries=[{"title": "T", "content": "abcdef " * 40}]))
        monkeypatch.setattr(service, "_chunk_fit", lambda: lambda text: len(text) <= 24)
        rows = service._store.pending_chunks(model_id="any", pack_ids=["imp.rev"], limit=10)
        stale = [(rows[0][0], "0" * 64)]  # The row was replaced by another revision.
        generation = service._vector_generation
        before = service._store.chunk_stats(None)["imp.rev"]["total"]
        await service._replan_vector_inputs(["imp.rev"], triggers={"imp.rev": stale})
        assert service._store.chunk_stats(None)["imp.rev"]["total"] == before
        assert service._vector_generation == generation  # Snapshot kept.
        live = [(rows[0][0], rows[0][1])]
        await service._replan_vector_inputs(["imp.rev"], triggers={"imp.rev": live})
        assert service._store.chunk_stats(None)["imp.rev"]["total"] > before
        assert service._vector_generation == generation + 1
    finally:
        await service.stop()
