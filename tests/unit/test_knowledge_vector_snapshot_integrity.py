"""Publish current vector matrices without serving damaged or stale row identities.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading

import numpy as np
import pytest

from knowledge.models import parse_pack
import knowledge.service as service_module
# pytest resolves this explicitly imported fixture by argument name.
from tests.knowledge_fixtures import FakeEmbedder, _entries, _import, _pack, _started, fast_indexer  # noqa: F401

pytestmark = pytest.mark.unit


async def test_updated_pack_never_maps_stale_vectors_to_new_rows(tmp_path):
    """Entry ids are never reused, so an old snapshot cannot alias new entries."""
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        before = set((await asyncio.to_thread(service._store.fetch_entries, range(1, 50))).keys())
        await service.remove_pack("demo-memes")
        await _import(service, _pack())
        after = set((await asyncio.to_thread(service._store.fetch_entries, range(1, 50))).keys())
        assert before and after and not (before & after)
    finally:
        await service.stop()


async def test_vector_snapshot_catches_up_with_changes_made_while_rebuilding(tmp_path):
    import threading

    service = await _started(tmp_path, FakeEmbedder())
    entered, release = threading.Event(), threading.Event()
    try:
        # An unavailable/empty snapshot is deliberately retryable. Exercise
        # generation catch-up against actual stored vectors instead.
        service._tasks[1].cancel()
        await asyncio.gather(service._tasks[1], return_exceptions=True)
        await _import(service, _pack())
        await service._indexer._index_round()
        loads = []
        real_load = service._store.load_vectors

        def slow_load(model_id):
            loads.append(model_id)
            if len(loads) == 1:
                entered.set()
                assert release.wait(5)
            return real_load(model_id)

        service._store.load_vectors = slow_load
        service._vector_generation += 1
        service._indexer._schedule_vector_refresh()
        assert await asyncio.to_thread(entered.wait, 2)
        service._vector_generation += 1  # changes while the first rebuild runs
        service._indexer._schedule_vector_refresh()  # skipped: one is in flight
        release.set()
        for _ in range(100):
            if service._vectors_built_for == (service._vector_generation, "fake-16"):
                break
            await asyncio.sleep(0.02)
        assert service._vectors_built_for == (service._vector_generation, "fake-16")
        assert len(loads) >= 2
    finally:
        release.set()
        await service.stop()


def test_first_chunk_keeps_its_body_despite_a_long_summary():
    from knowledge.chunking import derive_chunks

    entry = parse_pack(_pack(entries=[{
        "title": "Long summary",
        "summary": "summary text " * 400,
        "content": "the distinctive opening passage",
    }])).entries[0]
    (first,) = derive_chunks(entry)
    assert "the distinctive opening passage" in first.embed_text


async def test_an_import_reports_active_only_with_current_vector_ids(tmp_path, fast_indexer, monkeypatch):
    service = await _started(tmp_path, FakeEmbedder())
    try:
        await _import(service, _pack())
        for _ in range(300):
            if service._vectors is not None and len(service._vectors.entry_ids):
                break
            await asyncio.sleep(0.02)
        assert service._vectors is not None and len(service._vectors.entry_ids)
        # Only the reload done before "active" may update the snapshot.
        monkeypatch.setattr(service._indexer, "_schedule_vector_refresh", lambda: None)
        updated = _pack()
        updated["entries"].append({"title": "Newcomer", "content": "brand new entry"})
        await _import(service, updated)  # every row is reinserted with a new id
        rows = await asyncio.to_thread(service._store.list_entries, limit=50, offset=0)
        current = {row.entry_id for row in rows}
        snapshot_ids = set(service._vectors.entry_ids.tolist())
        assert snapshot_ids and snapshot_ids <= current
    finally:
        await service.stop()


def test_an_older_vector_snapshot_never_replaces_a_newer_one(tmp_path):
    service = service_module.KnowledgeService(tmp_path)
    newer, older = object(), object()
    service._publish_vector_snapshot(newer, (5, "m"))
    service._publish_vector_snapshot(older, (3, "m"))  # a slow rebuild finishing late
    assert service._vectors is newer
    service._publish_vector_snapshot(older, (1, "other-model"))  # a model switch always wins
    assert service._vectors is older


async def test_damaged_vectors_do_not_disqualify_the_healthy_ones(tmp_path, fast_indexer, monkeypatch):
    service = await _started(tmp_path, FakeEmbedder())
    try:
        await _import(service, _pack(entries=_entries("v", 6)))
        for _ in range(300):
            stats = await asyncio.to_thread(service._store.chunk_stats, "fake-16")
            if stats["demo-memes"]["ready"] == stats["demo-memes"]["total"]:
                break
            await asyncio.sleep(0.02)
        total = stats["demo-memes"]["total"]
        # Freeze background work: a refresh would otherwise notice the damage
        # first and the indexer would recompute it before we look.
        monkeypatch.setattr(service._indexer, "_schedule_vector_refresh", lambda: None)
        if service._vector_task is not None:
            await asyncio.wait({service._vector_task})
        await service.set_enabled(False)
        conn = sqlite3.connect(tmp_path / "knowledge.db")
        try:
            ids = [row[0] for row in conn.execute("SELECT id FROM chunks ORDER BY id")]
            conn.execute("UPDATE chunks SET vector=? WHERE id=?", (bytes([0, 1, 2]), ids[0]))  # truncated
            nan = np.full(16, np.nan, dtype="<f4").tobytes()
            conn.execute("UPDATE chunks SET vector=? WHERE id=?", (nan, ids[1]))  # not finite
            conn.commit()
        finally:
            conn.close()
        # Loading only reports the damage; it writes nothing outside the lock.
        snapshot = await asyncio.to_thread(service._store.load_vectors, "fake-16")
        assert snapshot.matrix.shape == (total - 2, 16)
        assert sorted(chunk_id for chunk_id, _blob in snapshot.damaged) == sorted(ids[:2])
        conn = sqlite3.connect(tmp_path / "knowledge.db")
        try:
            still = conn.execute("SELECT COUNT(*) FROM chunks WHERE vector IS NULL").fetchone()[0]
        finally:
            conn.close()
        assert still == 0
        # The service clears it, under its write lock.
        await service._indexer._refresh_vectors_now()
        assert service._vectors.matrix.shape == (total - 2, 16)
        conn = sqlite3.connect(tmp_path / "knowledge.db")
        try:
            cleared = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE vector IS NULL AND id IN (?, ?)", (ids[0], ids[1])
            ).fetchone()[0]
        finally:
            conn.close()
        assert cleared == 2  # handed back to the indexer
    finally:
        await service.stop()


def test_a_damaged_vector_rewritten_meanwhile_is_not_cleared(tmp_path):
    from knowledge.store import KnowledgeStore

    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    store.replace_pack(parse_pack(_pack()), pack_sha256="0" * 64)
    conn = sqlite3.connect(tmp_path / "knowledge.db")
    try:
        (chunk_id,) = conn.execute("SELECT id FROM chunks ORDER BY id LIMIT 1").fetchone()
        conn.execute("UPDATE chunks SET model_id='m', vector=? WHERE id=?", (bytes([1, 2, 3]), chunk_id))
        conn.commit()
        damaged = [(chunk_id, bytes([1, 2, 3]))]
        good = np.ones(16, dtype="<f4").tobytes()
        conn.execute("UPDATE chunks SET vector=? WHERE id=?", (good, chunk_id))  # rewritten since the read
        conn.commit()
    finally:
        conn.close()
    assert store.clear_vectors(damaged) == 0


def test_empty_vector_blobs_do_not_outvote_real_ones(tmp_path):
    from knowledge.store import KnowledgeStore

    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    store.replace_pack(parse_pack(_pack(entries=_entries("e", 3))), pack_sha256="0" * 64)
    conn = sqlite3.connect(tmp_path / "knowledge.db")
    try:
        ids = [row[0] for row in conn.execute("SELECT id FROM chunks ORDER BY id")]
        good = (np.ones(16, dtype="<f4") / 4.0).tobytes()  # unit length
        conn.execute("UPDATE chunks SET model_id='m', vector=? WHERE id=?", (b"", ids[0]))
        conn.execute("UPDATE chunks SET model_id='m', vector=? WHERE id=?", (b"", ids[1]))
        conn.execute("UPDATE chunks SET model_id='m', vector=? WHERE id=?", (good, ids[2]))
        conn.commit()
    finally:
        conn.close()
    snapshot = store.load_vectors("m")
    assert snapshot.matrix.shape == (1, 16)  # the real vector is kept
    assert sorted(chunk_id for chunk_id, _blob in snapshot.damaged) == ids[:2]


def test_stored_vectors_that_are_not_unit_length_are_damaged(tmp_path):
    from knowledge.store import KnowledgeStore

    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    store.replace_pack(parse_pack(_pack(entries=_entries("u", 3))), pack_sha256="0" * 64)
    unit = np.zeros(16, dtype="<f4")
    unit[0] = 1.0
    huge = np.full(16, 50.0, dtype="<f4")  # right length, finite, far from unit
    conn = sqlite3.connect(tmp_path / "knowledge.db")
    try:
        ids = [row[0] for row in conn.execute("SELECT id FROM chunks ORDER BY id")]
        for chunk_id, vector in zip(ids, (unit, unit, huge)):
            conn.execute("UPDATE chunks SET model_id='m', vector=? WHERE id=?", (vector.tobytes(), chunk_id))
        conn.commit()
    finally:
        conn.close()
    snapshot = store.load_vectors("m")
    assert snapshot.matrix.shape == (2, 16)
    assert [chunk_id for chunk_id, _blob in snapshot.damaged] == [ids[2]]
