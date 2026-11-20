"""Reserve all retained rows and preserve SQLite when registry writes fail."""

import hashlib
import json
import struct
import zipfile

import pytest

from knowledge import service as module
from knowledge.models import decode_pack_bytes
from knowledge.registry import KnowledgeRegistryError
from knowledge.service import KnowledgeService
from tests.unit.test_knowledge_archive_v2 import specification
from tests.unit.test_knowledge_review_budget_rejection import ArchiveProvider, _archive, _installed, _rows
from tests.unit.test_knowledge_review_isolation import _stop_automatic_indexer
from tests.unit.test_knowledge_subsystem import _pack, _raw
from tests.unit.test_knowledge_unified_core import _finished
from utils.knowledge_package import canonical_json

pytestmark = pytest.mark.unit


def _ready_vectors(service, pack_id):
    """Bind real stored blobs to a complete synthetic resource specification."""
    spec = specification()
    rows = service._store.pending_chunks(model_id=spec["model_id"], pack_ids=[pack_id], limit=20)
    blob = struct.pack("<64f", 1.0, *([0.0] * 63))
    stored, failed = service._store.store_vectors(
        model_id=spec["model_id"], specification=spec, pack_ids=[pack_id],
        rows=[(chunk_id, digest, blob) for chunk_id, digest, _text in rows],
    )
    assert stored == len(rows) and failed == 0


def _renamed_archive(tmp_path, pack_id):
    """Give another valid one-row product its own registered identity."""
    source = _archive(tmp_path / "growing-original.zip")
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members.pop("manifest.json"))
    pack = json.loads(members["pack.json"])
    pack["pack_id"] = manifest["package_id"] = pack_id
    members["pack.json"] = canonical_json(pack)
    manifest["files"]["pack.json"] = {
        "bytes": len(members["pack.json"]), "sha256": hashlib.sha256(members["pack.json"]).hexdigest(),
    }
    target = tmp_path / "growing.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in {**members, "manifest.json": canonical_json(manifest)}.items():
            archive.writestr(name, raw)
    return target


@pytest.mark.parametrize("growing_first", [True, False])
async def test_all_retained_counts_are_reserved_before_any_startup_plan(tmp_path, monkeypatch, growing_first):
    """The same retained rows must bound growth in either registry order."""
    async with _installed(tmp_path) as (original, _raw_pack):
        growing_id = "imp.a_growing" if growing_first else "imp.z_growing"
        assert (await _finished(original, await original.import_pack(_renamed_archive(tmp_path, growing_id))))["state"] == "active"
        growing = original._registry.packs[growing_id]
        assert growing.chunks == 1
        original.artifact_path(growing.archive_sha256).unlink()
        original._store.delete_pack(growing_id)
        # A failed replacement left two rows, while the registry still says one.
        retained = decode_pack_bytes(_raw(_pack("imp.example", entries=[
            {"title": f"Kept {index}", "content": f"Retained replacement input {index}."} for index in range(2)
        ])))
        original._store.replace_pack(retained, pack_sha256="c" * 64)
        _ready_vectors(original, "imp.other")
        await original.stop()
        monkeypatch.setattr(module, "MAX_TOTAL_CHUNKS", 4)
        reopened = KnowledgeService(original.root, embedder=ArchiveProvider())
        writes = []
        replace = reopened._store.replace_pack

        def observed_replace(*args, **kwargs):
            result = replace(*args, **kwargs)
            writes.append(sum(value["total"] for value in reopened._store.chunk_stats(None).values()))
            return result

        monkeypatch.setattr(reopened._store, "replace_pack", observed_replace)
        await reopened.start()
        try:
            await _stop_automatic_indexer(reopened)
            assert writes and max(writes) <= 4
            assert sum(item.chunks for item in reopened._registry.packs.values()) <= 4
            assert growing_id in reopened._broken_packs
            assert reopened._store.chunk_stats(specification()["model_id"], specification=specification())["imp.other"]["ready"] == 1
        finally:
            await reopened.stop()


@pytest.mark.parametrize("failure", ["permission", "io", "registry"])
async def test_registry_persistence_failure_never_replaces_the_database(tmp_path, monkeypatch, failure):
    """Fail closed with all retained rows/vectors intact, then retry normally."""
    async with _installed(tmp_path) as (original, _raw_pack):
        leftover = decode_pack_bytes(_raw(_pack("imp.example", entries=[
            {"title": f"Kept {index}", "content": f"Retained replacement input {index}."} for index in range(4)
        ])))
        original._store.replace_pack(leftover, pack_sha256="c" * 64)
        _ready_vectors(original, "imp.example")
        _ready_vectors(original, "imp.other")
        before = _rows(original._store)
        registry_bytes = (original.root / "registry.json").read_bytes()
        await original.stop()
        monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", 1)
        save = module.save_registry

        def failed_save(*_args):
            kind = {"permission": PermissionError, "io": OSError, "registry": KnowledgeRegistryError}[failure]
            raise kind("synthetic registry write refusal")

        monkeypatch.setattr(module, "save_registry", failed_save)
        reopened = KnowledgeService(original.root, embedder=ArchiveProvider())
        removed = []
        remove = reopened._store.remove_files

        def observed_remove():
            removed.append(True)
            remove()

        monkeypatch.setattr(reopened._store, "remove_files", observed_remove)
        await reopened.start()
        try:
            assert not removed
            assert reopened._state == "unavailable"
            assert _rows(reopened._store) == before
            assert (reopened.root / "registry.json").read_bytes() == registry_bytes
            # The original registry remains valid. Retrying startup after the
            # filesystem recovers publishes accurate counts without discarding vectors.
            monkeypatch.setattr(module, "save_registry", save)
            await reopened.start()
            await _stop_automatic_indexer(reopened)
            assert reopened._state == "ready"
            assert reopened._registry.packs["imp.example"].chunks == 4
            assert _rows(reopened._store) == before
        finally:
            await reopened.stop()


async def test_missing_source_releases_rows_but_keeps_its_registered_reservation(tmp_path, monkeypatch):
    """A still-registered unavailable pack continues to consume admitted capacity."""
    async with _installed(tmp_path) as (original, _raw_pack):
        growing_id = "imp.z_growing"
        assert (await _finished(original, await original.import_pack(_renamed_archive(tmp_path, growing_id))))["state"] == "active"
        growing = original._registry.packs[growing_id]
        original.artifact_path(growing.archive_sha256).unlink()
        original._store.delete_pack(growing_id)
        missing = original._registry.packs["imp.example"]
        leftover = decode_pack_bytes(_raw(_pack(missing.pack_id, entries=[
            {"title": f"Kept {index}", "content": "Unavailable source."} for index in range(2)
        ])))
        original._store.replace_pack(leftover, pack_sha256="c" * 64)
        (original.root / "packs" / missing.file_name).unlink()
        await original.stop()
        monkeypatch.setattr(module, "MAX_TOTAL_CHUNKS", 3)
        reopened = KnowledgeService(original.root, embedder=ArchiveProvider())
        await reopened.start()
        try:
            await _stop_automatic_indexer(reopened)
            assert {missing.pack_id, growing_id} <= set(reopened._broken_packs)
            assert missing.pack_id not in reopened._store.chunk_stats(None)
            assert reopened._registry.packs[missing.pack_id].chunks == 1
            assert sum(item.chunks for item in reopened._registry.packs.values()) == 3
        finally:
            await reopened.stop()
