"""Keep disabled and replaced rows outside candidate windows and rendered results.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import asyncio
import sqlite3

import numpy as np
import pytest

from knowledge import querying as query_module
from knowledge.models import parse_pack
import knowledge.service as service_module
# pytest resolves this explicitly imported fixture by argument name.
from tests.knowledge_fixtures import FakeEmbedder, _entries, _import, _pack, _started, fast_indexer  # noqa: F401

pytestmark = pytest.mark.unit


async def test_semantic_search_skips_packs_with_local_vectors_off(tmp_path, monkeypatch):
    embedder = FakeEmbedder()
    service = await _started(tmp_path, embedder)
    seen: list[list[str]] = []

    def record(snapshot, vector, *, allowed_pack_ids, **_kwargs):
        seen.append(list(allowed_pack_ids))
        return []

    try:
        await _import(service, _pack())
        # Freeze background work so nothing replaces the snapshot below: no
        # new rebuilds (a finishing one may schedule another), then drain.
        monkeypatch.setattr(service._indexer, "_schedule_vector_refresh", lambda: None)
        for task in service._tasks:
            task.cancel()
        while service._vector_task is not None and not service._vector_task.done():
            await asyncio.gather(service._vector_task, return_exceptions=True)
        service._vectors = service_module.VectorSnapshot(
            model_id="fake-16",
            entry_ids=np.zeros(0, dtype=np.int64),
            pack_ids=("demo-memes",),
            chunk_pack_index=np.zeros(0, dtype=np.int32),
            matrix=np.zeros((0, 16), dtype=np.float32),
        )
        monkeypatch.setattr(query_module, "semantic_candidates", record)
        await service.query(query="绝绝子")
        await service.set_pack_local_embedding("demo-memes", False)
        second = await service.query(query="绝绝子")
        assert second["result"] == "matched"
        # With vectors off for every eligible pack the query is not even
        # embedded, so the semantic step runs only for the first query.
        assert seen == [["demo-memes"]]
    finally:
        await service.stop()


async def test_cards_are_labelled_from_the_registry_snapshot_of_the_query(tmp_path):
    from knowledge.retrieval import RankedHit

    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        snapshot = service._registry
        await service.set_pack_material_type("demo-memes", "corpus")
        (entry_id,) = (await asyncio.to_thread(
            service._store.lexical_candidates, "绝绝子", pack_ids=["demo-memes"], limit=5
        ))[0]
        hit = RankedHit(entry_id=entry_id, score=1.0, exact=True, lexical_rank=None, semantic_score=None)
        hits, _context = service._queries._render([hit], "绝绝子", "en", snapshot)
        assert hits[0]["material_type"] == "knowledge"
    finally:
        await service.stop()


async def test_rows_ahead_of_the_registry_snapshot_are_not_served(tmp_path):
    """Between an import's index write and its registry write, queries skip the pack."""
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        newer = _pack(material_type="corpus")
        newer["entries"][0]["summary"] = "NEWER VERSION"
        pack = parse_pack(newer)
        await asyncio.to_thread(service._store.replace_pack, pack, pack_sha256="f" * 64)
        result = await service.query(query="绝绝子", material_type="knowledge")
        assert result["result"] == "miss"
        assert "NEWER VERSION" not in result["context"]
    finally:
        await service.stop()


async def test_entries_disabled_in_the_query_snapshot_stay_hidden(tmp_path):
    """Re-enabling writes the index first; the registry snapshot still says disabled."""
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        await service.set_entry_disabled("demo-memes", "绝绝子", True)
        await asyncio.to_thread(service._store.set_disabled, "demo-memes", "绝绝子", False)
        assert (await service.query(query="绝绝子"))["result"] == "miss"
    finally:
        await service.stop()


def test_disabled_entries_do_not_crowd_out_semantic_candidates():
    from knowledge.retrieval import SEMANTIC_CANDIDATES, semantic_candidates
    from knowledge.store import VectorSnapshot

    count = SEMANTIC_CANDIDATES + 6
    matrix = np.zeros((count, 4), dtype=np.float32)
    matrix[:, 0] = 1.0  # perfect matches, all disabled ...
    matrix[-1] = np.array([0.8, 0.6, 0.0, 0.0], dtype=np.float32)  # ... and one enabled, weaker
    snapshot = VectorSnapshot(
        model_id="m",
        entry_ids=np.arange(count, dtype=np.int64),
        pack_ids=("p",),
        chunk_pack_index=np.zeros(count, dtype=np.int32),
        matrix=matrix,
    )
    matches = semantic_candidates(
        snapshot,
        np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
        allowed_pack_ids=["p"],
        exclude_entry_ids=set(range(count - 1)),
    )
    assert [m.entry_id for m in matches] == [count - 1]


async def test_lookup_passes_disabled_entries_to_semantic_search(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        await service.set_entry_disabled("demo-memes", "绝绝子", True)
        seen = {}

        def record(snapshot, vector, *, allowed_pack_ids, exclude_entry_ids=(), **_kwargs):
            seen["excluded"] = set(exclude_entry_ids)
            return []

        monkeypatch.setattr(query_module, "semantic_candidates", record)
        (row,) = [r for r in await asyncio.to_thread(service._store.list_entries, limit=10, offset=0) if r.disabled]
        vector = np.zeros(4, dtype=np.float32)
        await asyncio.to_thread(service._queries._semantic_search, None, vector, ["demo-memes"], service._registry)
        assert seen["excluded"] == {row.entry_id}
        # Re-enabled in the index while the query's registry snapshot still
        # disables it: excluded all the same.
        snapshot = service._registry
        await asyncio.to_thread(service._store.set_disabled, "demo-memes", "绝绝子", False)
        await asyncio.to_thread(service._queries._semantic_search, None, vector, ["demo-memes"], snapshot)
        assert seen["excluded"] == {row.entry_id}
    finally:
        await service.stop()


async def test_sampling_skips_entries_the_snapshot_disables(tmp_path):
    entries = [
        {"title": "Hidden", "tags": ["snack"], "content": "hidden snack"},
        {"title": "Shown", "tags": ["snack"], "content": "shown snack"},
    ]
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(entries=entries))
        await service.set_entry_disabled("demo-memes", "Hidden", True)
        # Re-enabling writes the index first; the registry still says disabled.
        await asyncio.to_thread(service._store.set_disabled, "demo-memes", "Hidden", False)
        for _ in range(20):
            result = await service.query(query="snack", mode="sample", limit=1)
            assert result["result"] == "matched"
            assert result["hits"][0]["title"] == "Shown"
    finally:
        await service.stop()


async def test_snapshot_disabled_entries_do_not_crowd_the_lexical_window(tmp_path, monkeypatch):
    monkeypatch.setattr(query_module, "LEXICAL_CANDIDATES", 2)
    entries = [{"title": f"Hidden {i}", "content": "zanzibar zanzibar zanzibar"} for i in range(3)]
    entries.append({"title": "Shown", "content": "zanzibar"})
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(entries=entries))
        for i in range(3):
            await service.set_entry_disabled("demo-memes", f"Hidden {i}", True)
            # Re-enabled in the index; the registry snapshot still disables it.
            await asyncio.to_thread(service._store.set_disabled, "demo-memes", f"Hidden {i}", False)
        result = await service.query(query="zanzibar")
        assert result["result"] == "matched"
        assert [hit["title"] for hit in result["hits"]] == ["Shown"]
    finally:
        await service.stop()


async def test_normally_disabled_entries_do_not_widen_the_lexical_window(tmp_path, monkeypatch):
    entries = [{"title": f"Note {i}", "content": "zanzibar"} for i in range(8)]
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(entries=entries))
        for i in range(5):
            await service.set_entry_disabled("demo-memes", f"Note {i}", True)
        limits = []
        real = service._store.lexical_candidates

        def recording(query, *, pack_ids, limit, exclude_ids=()):
            limits.append((limit, set(exclude_ids)))
            return real(query, pack_ids=pack_ids, limit=limit, exclude_ids=exclude_ids)

        monkeypatch.setattr(service._store, "lexical_candidates", recording)
        assert (await service.query(query="zanzibar"))["result"] == "matched"
        # Disabled in the index too: SQL already filters them, nothing extra.
        assert limits == [(query_module.LEXICAL_CANDIDATES, set())]
    finally:
        await service.stop()


async def test_a_pack_mid_replacement_does_not_take_candidate_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(query_module, "LEXICAL_CANDIDATES", 2)
    service = await _started(tmp_path)
    try:
        await _import(service, _pack("stale-pack", entries=_entries("s", 5)))
        await _import(service, _pack("fact-pack", entries=[{"title": "Fact", "content": "a kotatsu fact"}]))
        before = await service.query(query="kotatsu")
        assert {hit["pack_id"] for hit in before["hits"]} == {"stale-pack"}  # it fills the window
        # Its rows are now a version the registry does not describe yet, as
        # between an import's index commit and its registry update.
        conn = sqlite3.connect(tmp_path / "knowledge.db")
        try:
            conn.execute("UPDATE packs SET pack_sha256=? WHERE pack_id='stale-pack'", ("f" * 64,))
            conn.commit()
        finally:
            conn.close()
        result = await service.query(query="kotatsu")
        assert result["result"] == "matched"
        assert [hit["pack_id"] for hit in result["hits"]] == ["fact-pack"]
    finally:
        await service.stop()


async def test_vectors_of_a_replaced_version_do_not_take_semantic_slots(tmp_path, monkeypatch, fast_indexer):
    import dataclasses

    service = await _started(tmp_path, FakeEmbedder())
    try:
        await _import(service, _pack())
        for _ in range(300):
            if service._vectors is not None and "demo-memes" in service._vectors.pack_ids:
                break
            await asyncio.sleep(0.01)
        assert service._vectors is not None and "demo-memes" in service._vectors.pack_ids
        assert service._vectors.pack_versions["demo-memes"] == service._registry.packs["demo-memes"].pack_sha256
        # Freeze background work so the snapshot stays as set below.
        monkeypatch.setattr(service._indexer, "_schedule_vector_refresh", lambda: None)
        for task in service._tasks:
            task.cancel()
        while service._vector_task is not None and not service._vector_task.done():
            await asyncio.gather(service._vector_task, return_exceptions=True)
        # As between a replacement's registry update and the snapshot reload:
        # the vectors were read for the previous version.
        service._vectors = dataclasses.replace(service._vectors, pack_versions={"demo-memes": "0" * 64})
        searched: list[list[str]] = []
        real = query_module.semantic_candidates

        def record(snapshot, vector, *, allowed_pack_ids, **kwargs):
            searched.append(list(allowed_pack_ids))
            return real(snapshot, vector, allowed_pack_ids=allowed_pack_ids, **kwargs)

        monkeypatch.setattr(query_module, "semantic_candidates", record)
        result = await service.query(query="绝绝子")
        assert result["result"] == "matched"  # keyword search still finds it
        assert all("demo-memes" not in packs for packs in searched)
    finally:
        await service.stop()


async def test_a_replacement_during_candidate_selection_is_chosen_around(tmp_path, monkeypatch):
    monkeypatch.setattr(query_module, "LEXICAL_CANDIDATES", 2)
    service = await _started(tmp_path)
    try:
        await _import(service, _pack("stale-pack", entries=_entries("s", 5)))
        await _import(service, _pack("fact-pack", entries=[{"title": "Fact", "content": "a kotatsu fact"}]))
        real = service._queries._lexical_search
        calls: list[list[str]] = []

        def search(query, allowed, registry):
            if not calls:
                # The replacement commits after the version check and before
                # this search reads the rows.
                conn = sqlite3.connect(tmp_path / "knowledge.db")
                try:
                    conn.execute("UPDATE packs SET pack_sha256=? WHERE pack_id='stale-pack'", ("f" * 64,))
                    conn.commit()
                finally:
                    conn.close()
            calls.append(list(allowed))
            return real(query, allowed, registry)

        monkeypatch.setattr(service._queries, "_lexical_search", search)
        result = await service.query(query="kotatsu")
        assert result["result"] == "matched"
        assert [hit["pack_id"] for hit in result["hits"]] == ["fact-pack"]
        assert calls == [["stale-pack", "fact-pack"], ["fact-pack"]]
    finally:
        await service.stop()


async def test_sampling_skips_a_pack_replaced_after_the_version_check(tmp_path, monkeypatch):
    def tagged(prefix, count):
        return [{"title": f"{prefix} {i}", "content": "x", "tags": ["domain:meme"]} for i in range(count)]

    service = await _started(tmp_path)
    try:
        await _import(service, _pack("stale-pack", entries=tagged("s", 5)))
        await _import(service, _pack("fact-pack", entries=tagged("f", 1)))
        # Deterministic picks: the lowest ids, which are the stale pack's.
        monkeypatch.setattr(query_module.random, "sample", lambda population, k: sorted(population)[:k])
        real = service._store.entries_with_tag

        def rows(tag, pack_ids):
            found = real(tag, pack_ids)
            # The replacement commits after the version check and after these
            # rows were read (they are the old ones here, but the version moves).
            conn = sqlite3.connect(tmp_path / "knowledge.db")
            try:
                conn.execute("UPDATE packs SET pack_sha256=? WHERE pack_id='stale-pack'", ("f" * 64,))
                conn.commit()
            finally:
                conn.close()
            return found

        monkeypatch.setattr(service._store, "entries_with_tag", rows)
        result = await service.query(query="domain:meme", mode="sample", limit=1)
        assert result["result"] == "matched"
        assert [hit["pack_id"] for hit in result["hits"]] == ["fact-pack"]
    finally:
        await service.stop()
