"""Rebuild only damaged derived indexes while preserving authoritative pack data.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3

import pytest

from services.knowledge.registry import REGISTRY_FILE, load_registry
import services.knowledge.service as service_module
from tests.knowledge_fixtures import _import, _pack, _started

pytestmark = pytest.mark.unit


async def test_missing_database_is_rebuilt_from_registry_and_packs(tmp_path):
    service = await _started(tmp_path)
    await _import(service, _pack())
    await service.set_entry_disabled("demo-memes", "Rubber duck debugging", True)
    await service.stop()
    for path in tmp_path.glob("knowledge.db*"):
        path.unlink()

    rebuilt = await _started(tmp_path)
    try:
        assert (await rebuilt.query(query="绝绝子"))["result"] == "matched"
        assert (await rebuilt.query(query="rubber duck debugging"))["result"] == "miss"
    finally:
        await rebuilt.stop()


async def test_database_of_unknown_schema_is_rebuilt(tmp_path):
    service = await _started(tmp_path)
    await _import(service, _pack())
    await service.stop()
    conn = sqlite3.connect(tmp_path / "knowledge.db")
    try:
        with conn:
            conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    finally:
        conn.close()

    rebuilt = await _started(tmp_path)
    try:
        assert rebuilt.availability()["ready"] is True
        assert (await rebuilt.query(query="绝绝子"))["result"] == "matched"
    finally:
        await rebuilt.stop()


async def test_lost_raw_pack_is_reported_not_served(tmp_path):
    service = await _started(tmp_path)
    await _import(service, _pack())
    await service.stop()
    for path in (tmp_path / "packs").iterdir():
        path.unlink()
    for path in tmp_path.glob("knowledge.db*"):
        path.unlink()

    rebuilt = await _started(tmp_path)
    try:
        status = await rebuilt.status()
        assert status["broken_packs"] == ["demo-memes"]
        assert rebuilt.availability()["tool_available"] is False
    finally:
        await rebuilt.stop()


async def test_invalid_registry_leaves_subsystem_unavailable_without_overwriting(tmp_path):
    (tmp_path / REGISTRY_FILE).write_bytes(b"{broken")
    service = await _started(tmp_path)
    try:
        assert (await service.status())["state"] == "unavailable"
        assert (await service.query(query="x"))["result"] == "unavailable"
        assert (tmp_path / REGISTRY_FILE).read_bytes() == b"{broken"
    finally:
        await service.stop()


async def test_disabled_flags_follow_the_registry_after_a_torn_write(tmp_path):
    service = await _started(tmp_path)
    await _import(service, _pack())
    await service.stop()
    # Simulate dying after the index write but before the registry write.
    conn = sqlite3.connect(tmp_path / "knowledge.db")
    try:
        with conn:
            conn.execute("UPDATE entries SET disabled=1 WHERE title='绝绝子'")
    finally:
        conn.close()

    restarted = await _started(tmp_path)
    try:
        assert (await restarted.query(query="绝绝子"))["result"] == "matched"
    finally:
        await restarted.stop()


async def test_altered_raw_pack_is_not_served_even_with_a_current_index(tmp_path):
    service = await _started(tmp_path)
    await _import(service, _pack())
    await service.stop()
    (raw_file,) = (tmp_path / "packs").iterdir()
    raw_file.write_bytes(raw_file.read_bytes() + b" ")

    restarted = await _started(tmp_path)
    try:
        assert (await restarted.status())["broken_packs"] == ["demo-memes"]
        assert (await restarted.query(query="绝绝子"))["result"] == "miss"
    finally:
        await restarted.stop()


async def test_leftover_file_that_cannot_be_deleted_does_not_disable_knowledge(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    await _import(service, _pack())
    await service.stop()
    leftover = tmp_path / "packs" / "orphan.0000000000000000.json"
    leftover.write_bytes(b"{}")
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == leftover:
            raise PermissionError("held open")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    restarted = await _started(tmp_path)
    try:
        assert (await restarted.status())["state"] == "ready"
        assert (await restarted.query(query="绝绝子"))["result"] == "matched"
    finally:
        await restarted.stop()


async def test_database_missing_a_table_is_rebuilt(tmp_path):
    service = await _started(tmp_path)
    await _import(service, _pack())
    await service.stop()
    conn = sqlite3.connect(tmp_path / "knowledge.db")
    try:
        with conn:
            conn.execute("DROP TABLE surfaces")
    finally:
        conn.close()

    rebuilt = await _started(tmp_path)
    try:
        assert rebuilt.availability()["ready"] is True
        assert (await rebuilt.query(query="绝绝子"))["result"] == "matched"
    finally:
        await rebuilt.stop()


async def test_reimporting_an_unchanged_pack_restores_a_lost_raw_file(tmp_path):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        (raw_file,) = (tmp_path / "packs").iterdir()
        raw_file.unlink()
        result = await _import(service, _pack())
        assert result.get("unchanged") is not True
        assert result["state"] == "active"
        assert raw_file.exists()
    finally:
        await service.stop()


async def test_an_index_missing_rows_is_rebuilt_at_startup(tmp_path):
    service = await _started(tmp_path)
    await _import(service, _pack())
    await service.stop()
    conn = sqlite3.connect(tmp_path / "knowledge.db")
    try:
        conn.execute("DELETE FROM chunks")  # the pack version row stays
        conn.commit()
    finally:
        conn.close()
    restarted = await _started(tmp_path)
    try:
        stats = await asyncio.to_thread(restarted._store.chunk_stats, None)
        assert stats["demo-memes"]["total"] == load_registry(tmp_path).packs["demo-memes"].chunks
    finally:
        await restarted.stop()


async def test_a_healthy_index_is_not_rebuilt_at_startup(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    await _import(service, _pack())
    await service.stop()
    rebuilt = []
    real_replace = service_module.KnowledgeStore.replace_pack

    def recording(self, *args, **kwargs):
        rebuilt.append(args[0].pack_id)
        return real_replace(self, *args, **kwargs)

    monkeypatch.setattr(service_module.KnowledgeStore, "replace_pack", recording)
    restarted = await _started(tmp_path)
    try:
        assert rebuilt == []
    finally:
        await restarted.stop()


async def test_an_index_missing_search_rows_is_rebuilt_at_startup(tmp_path):
    service = await _started(tmp_path)
    await _import(service, _pack())
    await service.stop()
    conn = sqlite3.connect(tmp_path / "knowledge.db")
    try:
        conn.execute("DELETE FROM surfaces")  # entries, chunks and the version stay
        conn.commit()
    finally:
        conn.close()
    restarted = await _started(tmp_path)
    try:
        assert (await restarted.query(query="绝绝子"))["result"] == "matched"
        counts = await asyncio.to_thread(restarted._store.search_row_counts)
        fts, surfaces, expected = counts["demo-memes"]
        assert surfaces == expected > 0
    finally:
        await restarted.stop()


async def test_reconcile_persists_updated_counts_after_chunking_change(tmp_path, monkeypatch):
    """F0137: startup reconcile updates PackRecord counts on disk so subsequent startups do not rebuild."""
    from dataclasses import replace as dc_replace

    from services.knowledge.registry import save_registry

    long_content = " ".join(f"segment-{i}" for i in range(400))
    pack = _pack(
        "chunk-pack",
        entries=[
            {
                "title": "Long Entry",
                "terms": {"alias": ["long"]},
                "tags": ["domain:test"],
                "summary": "Long summary",
                "content": long_content,
            }
        ],
    )
    service = await _started(tmp_path)
    try:
        await _import(service, pack)
    finally:
        await service.stop()

    # Simulate stale counts in registry.json (as when chunking parameters change).
    stale_reg = load_registry(tmp_path)
    orig_record = stale_reg.packs["chunk-pack"]
    stale_reg = stale_reg.with_pack(
        dc_replace(orig_record, entries=orig_record.entries + 1, chunks=orig_record.chunks + 1)
    )
    save_registry(tmp_path, stale_reg)

    rebuilds: list[str] = []
    real_replace = service_module.KnowledgeStore.replace_pack

    def spy_replace(self_store, p, **kwargs):
        rebuilds.append(p.pack_id)
        return real_replace(self_store, p, **kwargs)

    monkeypatch.setattr(service_module.KnowledgeStore, "replace_pack", spy_replace)

    # First restart: reconciles once and persists healed counts.
    service2 = await _started(tmp_path)
    try:
        assert rebuilds == ["chunk-pack"]
        assert service2._registry.packs["chunk-pack"].entries == orig_record.entries
        assert service2._registry.packs["chunk-pack"].chunks == orig_record.chunks
    finally:
        await service2.stop()

    persisted = load_registry(tmp_path)
    assert persisted.packs["chunk-pack"].entries == orig_record.entries
    assert persisted.packs["chunk-pack"].chunks == orig_record.chunks
    assert persisted.packs["chunk-pack"].updated_at == orig_record.updated_at

    # Second restart: zero rebuilds.
    rebuilds.clear()
    service3 = await _started(tmp_path)
    try:
        assert rebuilds == []
    finally:
        await service3.stop()


async def test_reconcile_registry_save_failure_warns_without_wiping_db(tmp_path, monkeypatch):
    """F0137: OSError while persisting reconciled registry must not trigger DB wipe retry."""
    from dataclasses import replace as dc_replace

    from services.knowledge.registry import save_registry

    service = await _started(tmp_path)
    try:
        await _import(service, _pack("warn-pack"))
    finally:
        await service.stop()

    stale_reg = load_registry(tmp_path)
    orig = stale_reg.packs["warn-pack"]
    save_registry(tmp_path, stale_reg.with_pack(dc_replace(orig, chunks=orig.chunks + 2)))

    def fail_save(_root, _reg):
        raise OSError("disk full")

    monkeypatch.setattr(service_module, "save_registry", fail_save)
    remove_calls: list[str] = []
    monkeypatch.setattr(
        service_module.KnowledgeStore,
        "remove_files",
        lambda self_store: remove_calls.append("removed"),
    )

    service2 = await _started(tmp_path)
    try:
        assert remove_calls == []
        status = await service2.status()
        assert status["state"] == "ready"
        assert service2._registry.packs["warn-pack"].chunks == orig.chunks
    finally:
        await service2.stop()
