"""Protect startup recovery, capacity reservations and local import policy."""

import json
import uuid

import pytest

from knowledge import service as module
from knowledge.registry import load_registry
from knowledge.service import KnowledgeService
from tests.knowledge_fixtures import _finished, _import, _pack


pytestmark = pytest.mark.unit


async def test_a_job_record_with_a_damaged_timestamp_is_skipped_not_fatal(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True)
    damaged, valid = uuid.uuid4().hex, uuid.uuid4().hex
    common = {"pack_id": "imp.example", "state": "failed", "reason": "process_interrupted", "kind": "pack_import"}
    (jobs / (damaged + ".json")).write_text(json.dumps({**common, "job_id": damaged, "created_at": None,
                                                         "updated_at": None}))
    (jobs / (valid + ".json")).write_text(json.dumps({**common, "job_id": valid, "created_at": "2026-11-21T00:00:00Z",
                                                       "updated_at": "2026-11-21T00:00:00Z"}))
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        assert service._state == "ready"
        assert [row["job_id"] for row in service.list_jobs()] == [valid]
    finally:
        await service.stop()


async def test_runtime_count_reconcile_keeps_a_broken_packs_reservation(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        assert (await _import(service, _pack("imp.gone", entries=[
            {"title": "Gone", "content": "Original that will disappear."}])))["state"] == "active"
        reserved = service._registry.packs["imp.gone"].chunks
        file_name = service._registry.packs["imp.gone"].file_name
    finally:
        await service.stop()
    (tmp_path / "packs" / file_name).unlink()
    reopened = KnowledgeService(tmp_path)
    await reopened.start()
    try:
        assert reopened._broken_packs == ("imp.gone",) and reserved > 0
        # Its rows were dropped; a later runtime count repair must not free
        # the capacity the registered (repairable) pack still holds.
        reopened._pending_chunk_reconcile = True
        reopened._reconcile_chunk_counts_blocking()
        assert reopened._registry.packs["imp.gone"].chunks == reserved
        assert load_registry(tmp_path).packs["imp.gone"].chunks == reserved  # Persisted too.
    finally:
        await reopened.stop()


async def test_an_import_refused_while_stopping_leaves_no_frozen_retry_input(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    write = module.atomic_write_bytes

    def write_then_stop(path, data):
        write(path, data)
        if str(path).endswith(".pack.json"):
            service._stopping = True  # Shutdown begins right after both writes.

    monkeypatch.setattr(module, "atomic_write_bytes", write_then_stop)
    try:
        result = await service.import_pack(json.dumps(_pack("imp.late")).encode())
        assert result == {"ok": False, "reason": "knowledge_stopping"}
        assert not list((tmp_path / "jobs").glob("*.pack.json"))
    finally:
        service._stopping = False
        monkeypatch.setattr(module, "atomic_write_bytes", write)
        await service.stop()


async def test_local_file_replacement_turns_automatic_context_off_with_retrieval(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        assert (await _import(service, _pack("imp.local", entries=[
            {"title": "Old", "content": "Previously disclosed content."}])))["state"] == "active"
        await service.set_pack_auto_context("imp.local", True)
        assert service._registry.packs["imp.local"].enabled and service._registry.packs["imp.local"].auto_context
        # The manager's local file import: a replacement is new disclosure scope.
        replaced = await service.import_pack(json.dumps(_pack("imp.local", entries=[
            {"title": "New", "content": "Replacement content."}])).encode(),
            _options={"origin": "imported", "enabled": False})
        assert (await _finished(service, replaced))["state"] == "active"
        record = load_registry(tmp_path).packs["imp.local"]
        assert record.enabled is False and record.auto_context is False
    finally:
        await service.stop()
