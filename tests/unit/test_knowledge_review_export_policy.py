"""Export existing proven vectors independently of the current encoder policy."""

import sqlite3
from contextlib import closing

import pytest

from knowledge.service import KnowledgeService
from tests.unit.test_knowledge_archive_v2 import make_archive, specification
from tests.unit.test_knowledge_subsystem import _import, _pack
from tests.unit.test_knowledge_unified_core import (
    _PackageEmbedder,
    _finished,
    _manual_index,
    _prepare_export_vectors,
)
from utils.knowledge_package import validate_package


pytestmark = pytest.mark.unit


class _UnloadedEmbedder(_PackageEmbedder):
    """An absent local encoder must not hide a persisted package specification."""

    def state(self):
        return "unavailable"

    def spec(self):
        return None


class _DifferentEmbedder(_PackageEmbedder):
    """The loaded encoder's profile is unrelated to already stored package rows."""

    def spec(self):
        return specification(dimension=128)


@pytest.mark.parametrize("provider", [_PackageEmbedder, _UnloadedEmbedder, _DifferentEmbedder])
async def test_stored_export_readiness_does_not_require_local_inference(tmp_path, provider):
    service = KnowledgeService(tmp_path / "library", embedder=_PackageEmbedder())
    await service.start()
    await _manual_index(service)
    try:
        imported = await service.import_pack(make_archive(tmp_path / "original.zip"))
        assert (await _finished(service, imported))["state"] == "active"
        await service.set_pack_local_embedding("imp.example", False)
        service.embedder = provider()
        pack = (await service.list_packs())[0]
        assert pack["vector_state"] == "off"
        assert pack.get("export_ready") is True
        if provider is not _PackageEmbedder:
            assert pack["chunks_ready"] == 0  # Current-encoder retrieval counts cannot decide exportability.
        exported = await _finished(service, await service.export_pack("imp.example"))
        assert exported["state"] == "active"
        checked = validate_package(service.artifact_path(exported["result"]["artifact_id"]))
        assert checked.manifest["embedding_spec"] == specification()
    finally:
        await service.stop()


@pytest.mark.parametrize("unavailable", ["other_revision", "broken"])
async def test_complete_vectors_are_not_export_ready_for_an_unavailable_revision(tmp_path, unavailable):
    service = KnowledgeService(tmp_path / "library", embedder=_PackageEmbedder())
    await service.start()
    await _manual_index(service)
    try:
        imported = await service.import_pack(make_archive(tmp_path / "original.zip"))
        assert (await _finished(service, imported))["state"] == "active"
        if unavailable == "other_revision":
            # A failed replacement can leave complete rows owned by another
            # index revision while the registry still names the active pack.
            with closing(sqlite3.connect(service._store.database_path)) as connection:
                connection.execute("UPDATE packs SET pack_sha256=? WHERE pack_id=?", ("b" * 64, "imp.example"))
                connection.commit()
        else:
            service._broken_packs = ("imp.example",)
        assert service._store.chunk_stats(None, include_export_readiness=True)["imp.example"]["export_ready"] == 1
        assert (await service.list_packs())[0]["export_ready"] is False
    finally:
        await service.stop()


@pytest.mark.parametrize("damage", ["missing_vector", "unbound", "missing_spec", "mixed_specs", "short_vector"])
async def test_export_requires_complete_provenance_bound_vector_rows(tmp_path, damage):
    service = KnowledgeService(tmp_path)
    await service.start()
    await _manual_index(service)
    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        await service.set_pack_local_embedding("demo-memes", False)
        with closing(sqlite3.connect(service._store.database_path)) as connection:
            row = connection.execute("SELECT id,text_hash,vector FROM chunks ORDER BY id LIMIT 1").fetchone()
            if damage == "mixed_specs":
                # Keep both identities valid and complete; mixing them is not one export specification.
                other = specification()
                other["weights_sha256"]["model"] = "b" * 64
                service._store.store_vectors(model_id=other["model_id"], rows=[row], specification=other)
            else:
                statement, args = {
                    "missing_vector": ("UPDATE chunks SET vector=NULL WHERE id=?", (row[0],)),
                    "unbound": ("UPDATE chunks SET spec_sha256=NULL", ()),
                    "missing_spec": ("DELETE FROM vector_specs", ()),
                    "short_vector": ("UPDATE chunks SET vector=? WHERE id=?", (b"short", row[0])),
                }[damage]
                connection.execute(statement, args)
                connection.commit()
        service.embedder = _UnloadedEmbedder()
        pack = (await service.list_packs())[0]
        if damage != "short_vector":
            assert pack.get("export_ready") is False
        # UI aggregates do not deserialize BLOBs; the actual archive validator
        # must still reject a damaged length even when all rows are present.
        exported = await _finished(service, await service.export_pack("demo-memes"))
        assert exported["state"] == "failed"
        assert exported["reason"] in {"embedding_unavailable", "vectors_not_ready"}
        assert not exported.get("result")
    finally:
        await service.stop()
