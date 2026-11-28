"""Expose bounded management rows and counts under current pack policy.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from services.knowledge.models import parse_pack
import services.knowledge.service as service_module
# pytest resolves this explicitly imported fixture by argument name.
from tests.knowledge_fixtures import (
    FakeEmbedder,
    _entries,
    _import,
    _pack,
    _raw,
    _started,
    fast_indexer,  # noqa: F401
)

pytestmark = pytest.mark.unit


async def test_catalog_search_filters_pack_before_pagination(tmp_path):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack("aaa-pack", entries=_entries("a", 6)))
        await _import(service, _pack("bbb-pack", entries=[{"title": "B one", "content": "kotatsu"}]))
        page = await service.list_entries(query="kotatsu", pack_id="bbb-pack", limit=1)
        assert [item["title"] for item in page["items"]] == ["B one"]
    finally:
        await service.stop()


async def test_vector_state_reports_off_even_when_vectors_exist(tmp_path, fast_indexer):
    service = await _started(tmp_path, FakeEmbedder())
    try:
        await _import(service, _pack())
        for _ in range(300):
            (pack,) = await service.list_packs()
            if pack["vector_state"] == "complete":
                break
            await asyncio.sleep(0.02)
        await service.set_pack_local_embedding("demo-memes", False)
        (pack,) = await service.list_packs()
        assert pack["chunks_ready"] == pack["chunks_total"]
        assert pack["vector_state"] == "off"
    finally:
        await service.stop()


async def test_catalog_exact_matches_are_bounded_by_the_page(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        entries = [
            {"title": f"t{i}", "terms": {"alias": ["shared"]}, "content": f"body {i}"} for i in range(30)
        ]
        await _import(service, _pack(entries=entries))
        fetched: list[int] = []
        original = service._store._fetch

        def spy(conn, ids):
            fetched.append(len(ids))
            return original(conn, ids)

        monkeypatch.setattr(service._store, "_fetch", spy)
        page = await service.list_entries(query="shared", limit=5)
        assert len(page["items"]) == 5
        assert max(fetched) <= 6 + 6
    finally:
        await service.stop()


async def test_pack_count_and_status_sources_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "MAX_PACKS", 2)
    monkeypatch.setattr(service_module, "STATUS_SOURCES", 1)
    service = await _started(tmp_path)
    try:
        await _import(service, _pack("pack-one"))
        await _import(service, _pack("pack-two", entries=_entries("x", 3)))
        third = await service.import_pack(_raw(_pack("pack-three")))
        assert third == {"ok": False, "reason": "capacity_packs"}
        status = await service.status()
        assert [source["pack_id"] for source in status["sources"]] == ["pack-two"]
    finally:
        await service.stop()


async def test_catalog_hides_leftovers_of_a_removed_pack(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        monkeypatch.setattr(service._store, "delete_pack", lambda pack_id: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
        await service.remove_pack("demo-memes")
        listing = await service.list_entries()
        assert listing["total"] == 0 and listing["items"] == []
        assert (await service.list_entries(query="绝绝子"))["items"] == []
        with pytest.raises(service_module.KnowledgeUnavailable) as excinfo:
            await service.get_entry("demo-memes", "绝绝子")
        assert excinfo.value.reason == "not_found"
    finally:
        await service.stop()


async def test_catalog_rows_replaced_during_the_read_are_left_out(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        real_list = service._store.list_entries
        newer = parse_pack(_pack(material_type="corpus"))

        def list_then_replace(**kwargs):
            rows = real_list(**kwargs)
            service._store.replace_pack(newer, pack_sha256="e" * 64)
            return rows

        monkeypatch.setattr(service._store, "list_entries", list_then_replace)
        page = await service.list_entries()
        assert page["items"] == []
    finally:
        await service.stop()


async def test_vectors_report_paused_while_knowledge_is_off(tmp_path):
    service = await _started(tmp_path, FakeEmbedder())
    try:
        await service.set_enabled(False)  # nothing gets embedded
        await _import(service, _pack())
        (pack,) = await service.list_packs()
        assert pack["vector_state"] == "paused"
    finally:
        await service.stop()


async def test_the_overview_counts_only_packs_with_vectors_on(tmp_path, fast_indexer):
    service = await _started(tmp_path, FakeEmbedder())
    try:
        await _import(service, _pack("pack-a", entries=_entries("a", 3)))
        await service.set_pack_local_embedding("pack-a", False)  # before any vector exists
        await _import(service, _pack("pack-b", entries=_entries("b", 3)))
        for _ in range(300):
            packs = {p["pack_id"]: p for p in await service.list_packs()}
            if packs["pack-b"]["chunks_ready"] == packs["pack-b"]["chunks_total"]:
                break
            await asyncio.sleep(0.02)
        overview = await service.status()
        assert overview["chunks_total"] == packs["pack-b"]["chunks_total"]
        assert overview["indexed_percent"] == 100.0
    finally:
        await service.stop()
