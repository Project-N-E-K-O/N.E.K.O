"""Keep installed export inputs charged to import admission's byte budget."""

import asyncio

import pytest

from knowledge import service as module
from knowledge.models import canonical_pack_bytes, parse_pack
from knowledge.service import KnowledgeService
from tests.unit.test_knowledge_subsystem import _import, _pack, _raw


pytestmark = pytest.mark.unit


@pytest.fixture
async def exporting_library(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        assert (await _import(service, _pack("pack-export")))["state"] == "active"
    finally:
        await service.stop()

    service = KnowledgeService(tmp_path)

    async def paused_runner():
        # Keep the export and any later imports active throughout admission.
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_job_loop", paused_runner)
    await service.start()
    try:
        exported = await service.export_pack("pack-export")
        assert exported["state"] == "queued"
        yield service
    finally:
        await service.stop()


@pytest.mark.parametrize("has_pending_import", [False, True])
@pytest.mark.parametrize("has_room", [False, True])
async def test_export_keeps_installed_bytes_charged_at_import_admission(
    exporting_library, monkeypatch, has_pending_import, has_room
):
    service = exporting_library
    record = service._registry.packs["pack-export"]
    installed_size = (service.root / "packs" / record.file_name).stat().st_size
    pending_size = 0
    if has_pending_import:
        pending = await service.import_pack(_raw(_pack("pack-pending")))
        assert pending["ok"] and pending["state"] == "queued"
        pending_size = service._staging_path(pending["job_id"]).stat().st_size
    candidate = _pack("pack-incoming")
    candidate_size = len(canonical_pack_bytes(parse_pack(candidate)))
    # The boundary includes the installed export source and counts a staged
    # import once, even though that import has both a file and a byte reserve.
    limit = installed_size + pending_size + candidate_size - int(not has_room)
    monkeypatch.setattr(module, "MAX_TOTAL_PACK_BYTES", limit)
    monkeypatch.setattr(module, "MAX_PENDING_IMPORTS", 3 if has_pending_import else 2)
    before = {directory: set((service.root / directory).iterdir()) for directory in ("jobs", ".staging")}
    result = await service.import_pack(_raw(candidate))
    if has_room:
        assert result["ok"] and result["state"] == "queued"
        # Excluding exports from byte reserves must not free their task slot.
        assert service.import_busy()
        refused = await service.import_pack(_raw(_pack("pack-overflow")))
        assert refused == {"ok": False, "reason": "knowledge_busy"}
    else:
        assert result == {"ok": False, "reason": "capacity_bytes"}
        # Refuse before writing either disposable staging or retained retry input.
        assert {directory: set((service.root / directory).iterdir()) for directory in before} == before
    assert set(service._registry.packs) == {"pack-export"}
