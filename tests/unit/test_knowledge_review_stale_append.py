"""Do not reinterpret an append preview as creating its removed target."""

import json

import pytest

from knowledge.models import KnowledgePackError
from knowledge.service import KnowledgeService
from tests.unit.test_knowledge_unified_core import _finished


pytestmark = pytest.mark.unit


async def _install_source(service):
    created = await service.preview_source("first.txt", b"Original lighthouse reference.")
    value = created["preview"]
    result = await _finished(service, await service.commit_source(value["preview_id"], value["sha256"]))
    assert result["state"] == "active"
    return result["pack_id"], value


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("replace_document", [False, True])
async def test_stale_append_cannot_reinstall_a_removed_target(tmp_path, restart, replace_document):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        pack_id, first = await _install_source(service)
        extra = (await service.preview_source(
            "next.txt", b"Private addition from another window.", pack_id=pack_id,
            doc_id=first["doc_id"] if replace_document else "",
        ))["preview"]
        await service.remove_pack(pack_id)
        if restart:
            # The preview's intent must survive process restart, rather than
            # depend on an in-memory flag from the window that created it.
            await service.stop()
            service = KnowledgeService(tmp_path)
            await service.start()
        retained = {path: path.read_bytes() for path in (tmp_path / "sources").rglob("*") if path.is_file()}
        with pytest.raises(KnowledgePackError, match="source_not_found"):
            await _finished(service, await service.commit_source(extra["preview_id"], extra["sha256"]))
        assert pack_id not in service._registry.packs
        # Rejection must not freeze a new version or discard the private preview.
        assert {path: path.read_bytes() for path in (tmp_path / "sources").rglob("*") if path.is_file()} == retained
    finally:
        await service.stop()


async def test_append_to_a_present_target_keeps_both_sources(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        pack_id, first = await _install_source(service)
        extra = (await service.preview_source("next.txt", b"Garden reference.", pack_id=pack_id))["preview"]
        result = await _finished(service, await service.commit_source(extra["preview_id"], extra["sha256"]))
        assert result["state"] == "active"
        active = [row["doc_id"] for row in (await service.list_sources(pack_id))["sources"] if not row["removed"]]
        assert set(active) == {first["doc_id"], extra["doc_id"]}
    finally:
        await service.stop()


async def test_legacy_preview_without_append_intent_keeps_its_creation_semantics(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        created = await service.preview_source("old.txt", b"Uncommitted legacy personal document.")
        preview = created["preview"]
        path = tmp_path / "sources" / created["pack_id"] / "previews" / (preview["preview_id"] + ".json")
        legacy = json.loads(path.read_text())
        legacy.pop("requires_existing_pack")
        path.write_text(json.dumps(legacy))
        legacy_bytes = path.read_bytes()
        result = await _finished(service, await service.commit_source(preview["preview_id"], preview["sha256"]))
        assert result["state"] == "active" and result["pack_id"] == created["pack_id"]
        # No migration rewrites a legacy record or asks its owner to upload again.
        assert path.read_bytes() == legacy_bytes
    finally:
        await service.stop()


@pytest.mark.parametrize("operation", ["new_preview", "explicit_import", "history_restore"])
async def test_explicit_import_paths_remain_available_after_removal(tmp_path, operation):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        pack_id, first = await _install_source(service)
        record = service._registry.packs[pack_id]
        raw = (tmp_path / "packs" / record.file_name).read_bytes()
        await service.remove_pack(pack_id)
        if operation == "new_preview":
            created = await service.preview_source("new.txt", b"A newly selected personal document.")
            preview = created["preview"]
            assert created["pack_id"] != pack_id
            request = service.commit_source(preview["preview_id"], preview["sha256"])
        elif operation == "explicit_import":
            request = service.import_pack(raw)
        else:
            request = service.restore_source(pack_id, first["doc_id"], 1)
        result = await _finished(service, await request)
        assert result["state"] == "active"
        assert result["pack_id"] in service._registry.packs
        if operation != "new_preview":
            assert result["pack_id"] == pack_id
    finally:
        await service.stop()
