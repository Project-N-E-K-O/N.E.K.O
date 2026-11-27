"""Exercise public knowledge lifecycle, defaults, privacy and failure isolation.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from knowledge.registry import load_registry
from knowledge.service import KnowledgeService
import knowledge.service as service_module
from tests.knowledge_fixtures import _import, _pack, _raw, _started

pytestmark = pytest.mark.unit


async def test_import_query_and_remove(tmp_path):
    service = await _started(tmp_path)
    try:
        assert service.availability()["tool_available"] is False
        job = await _import(service, _pack())
        assert job["state"] == "active"
        assert service.availability()["tool_available"] is True

        hit = await service.query(query="绝绝子", language="zh")
        assert hit["result"] == "matched"
        assert hit["hits"][0]["title"] == "绝绝子"
        assert hit["retrieval_mode"] == "bm25"
        assert hit["context"].startswith("======以下为本地公共知识参考======")

        miss = await service.query(query="明天的天气预报怎么样", language="zh")
        assert miss["result"] == "miss"
        assert miss["context"] == ""
        # Shares one bigram (网络, network) with a card, far below the coverage bar.
        weak = await service.query(query="网络安全漏洞扫描工具推荐", language="zh")
        assert weak["result"] == "miss"

        removed = await service.remove_pack("demo-memes")
        assert removed["removed_entries"] == 2
        assert service.availability()["tool_available"] is False
        assert not any((tmp_path / "packs").iterdir())
    finally:
        await service.stop()


async def test_new_pack_defaults_follow_product_decision(tmp_path):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack(material_type="corpus"))
        (pack,) = await service.list_packs()
        assert pack["auto_context"] is False
        assert pack["local_embedding"] is True
        assert load_registry(tmp_path).enabled is True
    finally:
        await service.stop()


async def test_reimport_keeps_user_choices_and_unchanged_pack_is_noop(tmp_path):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        await service.set_pack_auto_context("demo-memes", True)
        await service.set_entry_disabled("demo-memes", "绝绝子", True)
        again = await service.import_pack(_raw(_pack()))
        assert again["unchanged"] is True

        updated = _pack()
        updated["entries"][1]["content"] += " Updated."
        await _import(service, updated)
        record = load_registry(tmp_path).packs["demo-memes"]
        assert record.auto_context is True
        assert record.disabled_titles == ("绝绝子",)
        assert (await service.query(query="绝绝子"))["result"] == "miss"
    finally:
        await service.stop()


async def test_global_switch_disables_queries_and_tool(tmp_path):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        await service.set_enabled(False)
        assert service.availability()["tool_available"] is False
        assert (await service.query(query="绝绝子"))["result"] == "disabled"
        await service.set_enabled(True)
        assert (await service.query(query="绝绝子"))["result"] == "matched"
    finally:
        await service.stop()


async def test_material_type_filter_and_sample(tmp_path):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        assert (await service.query(query="绝绝子", material_type="corpus"))["result"] == "miss"
        await service.set_pack_material_type("demo-memes", "corpus")
        assert (await service.query(query="绝绝子", material_type="corpus"))["result"] == "matched"
        sample = await service.query(query="domain:meme", mode="sample")
        assert sample["result"] == "matched"
        assert [hit["title"] for hit in sample["hits"]] == ["绝绝子"]
    finally:
        await service.stop()


async def test_capacity_and_validation_failures_are_reported(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        rejected = await service.import_pack(b"{}")
        assert rejected == {"ok": False, "reason": "unexpected_pack_field"}
        monkeypatch.setattr(service_module, "MAX_TOTAL_ENTRIES", 1)
        over = await service.import_pack(_raw(_pack()))
        assert over == {"ok": False, "reason": "capacity_entries"}
    finally:
        await service.stop()


def test_the_knowledge_service_has_no_unused_listener_hook():
    assert not hasattr(service_module.KnowledgeService, "add_availability_listener")


async def test_pack_enabled_filters_tool_query_and_passive_context_without_losing_vectors(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _import(service, _pack())
        assert service.availability()["tool_available"]
        await service.set_pack_auto_context("demo-memes", True)
        await service.set_pack_enabled("demo-memes", False)
        assert not service.availability()["tool_available"]
        assert (await service.query(query="rubber duck debugging"))["result"] == "miss"
        assert (await service.turn_context(text="rubber duck debugging"))["result"] == "off"
        assert service._indexer._vector_pack_ids() == ["demo-memes"]
        assert (await service.list_packs())[0]["enabled"] is False
        await service.set_pack_enabled("demo-memes", True)
        assert service.availability()["tool_available"]
        raw = json.loads((tmp_path / "registry.json").read_text())
        del raw["packs"]["demo-memes"]["enabled"]
        (tmp_path / "registry.json").write_text(json.dumps(raw))
        assert load_registry(tmp_path).packs["demo-memes"].enabled is True
    finally:
        await service.stop()


async def test_private_exception_message_and_cause_never_reach_logs_or_api(tmp_path, monkeypatch):
    from app.memory_server import knowledge_routes
    from knowledge import service as module

    service = KnowledgeService(tmp_path)
    await service.start()
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    # Product loggers deliberately do not propagate to pytest's root handler.
    # Capture their real formatted records, including any accidental traceback.
    monkeypatch.setattr(module.logger, "handlers", [handler])
    monkeypatch.setattr(knowledge_routes.logger, "handlers", [handler])
    secret = "private-filename-secret-query-secret-body"
    try:
        await _import(service, _pack())
        await service.set_pack_auto_context("demo-memes", True)

        def explode(*args, **kwargs):
            try:
                raise ValueError(secret)
            except ValueError as cause:
                raise RuntimeError(secret) from cause

        monkeypatch.setattr(service, "_current_packs", explode)
        assert (await service.query(query=secret))["result"] == "error"
        assert (await service.turn_context(text=secret))["result"] == "error"
        monkeypatch.setattr(knowledge_routes, "_service", service)

        async def rejected(svc):
            explode()

        response = await knowledge_routes._call(rejected)
        assert response.status_code == 500
        assert secret not in response.body.decode()
        assert secret not in output.getvalue()
        assert "RuntimeError" in output.getvalue()
        assert secret not in json.dumps(service.diagnostics.snapshot())
    finally:
        await service.stop()
