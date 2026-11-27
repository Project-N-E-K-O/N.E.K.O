"""Read retained ZIPs only when their verified vectors can still repair rows."""

from dataclasses import replace
import hashlib
import json
import struct

import pytest

from services.knowledge import archive
from services.knowledge.service import KnowledgeService
from tests.knowledge_archive_fixtures import _PackageEmbedder, make_archive, specification
from tests.knowledge_fixtures import _finished, _manual_index
from utils.knowledge_package import canonical_json


pytestmark = pytest.mark.unit_fast


@pytest.fixture
async def installed(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path / "library", embedder=_PackageEmbedder())
    await service.start()
    await _manual_index(service)
    source = make_archive(tmp_path / "source.zip")
    assert (await _finished(service, await service.import_pack(source)))["state"] == "active"
    reads = []
    original = archive.read_archive

    def counted(*args, **kwargs):
        reads.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(archive, "read_archive", counted)
    try:
        yield service, reads
    finally:
        await service.stop()


async def test_complete_current_packs_skip_zip_with_aggregate_queries(installed, tmp_path, monkeypatch):
    service, reads = installed

    def other_pack(manifest, members):
        payload = json.loads(members["pack.json"])
        payload["pack_id"] = manifest["package_id"] = "imp.other"
        members["pack.json"] = canonical_json(payload)
        manifest["files"]["pack.json"] = {"bytes": len(members["pack.json"]),
                                          "sha256": hashlib.sha256(members["pack.json"]).hexdigest()}

    source = make_archive(tmp_path / "other.zip", mutate=other_pack)
    assert (await _finished(service, await service.import_pack(source)))["state"] == "active"
    reads.clear()
    calls = []
    stats, versions = service._store.chunk_stats, service._store.pack_versions

    def counted_stats(*args, **kwargs):
        calls.append("stats")
        return stats(*args, **kwargs)

    def counted_versions():
        calls.append("versions")
        return versions()

    def no_per_pack_blobs(*_args, **_kwargs):
        raise AssertionError("readiness must not fetch vectors for each pack")

    monkeypatch.setattr(service._store, "chunk_stats", counted_stats)
    monkeypatch.setattr(service._store, "pack_versions", counted_versions)
    monkeypatch.setattr(service._store, "vectors_for_pack", no_per_pack_blobs)
    for _ in range(2):
        await service._indexer._seed_retained_packages(["imp.example", "imp.other"], specification())
    assert reads == []
    assert calls == ["stats", "versions"] * 2
    # Skipping is a current readiness observation, not a permanent seed verdict.
    assert not service._seeded_artifacts
    # Within one index round, complete packs are aggregated once, not per batch.
    calls.clear()
    observed = set()
    for _ in range(3):
        await service._indexer._seed_retained_packages(["imp.example", "imp.other"], specification(), observed)
    assert calls == ["stats", "versions"] and len(observed) == 2 and reads == []
    # A replaced revision is not hidden by the previous observation.
    record = service._registry.packs["imp.other"]
    service._registry = service._registry.with_pack(replace(record, pack_sha256="0" * 64))
    try:
        await service._indexer._seed_retained_packages(["imp.example", "imp.other"], specification(), observed)
    finally:
        service._registry = service._registry.with_pack(record)
    assert calls == ["stats", "versions"] * 2


async def test_startup_rebuilt_vectors_do_not_revalidate_the_zip_again(installed):
    service, reads = installed
    root = service.root
    await service.stop()
    service._store.remove_files()  # Only this test's disposable derived index.
    rebuilt = KnowledgeService(root, embedder=_PackageEmbedder())
    await rebuilt.start()
    try:
        await _manual_index(rebuilt)
        assert reads  # Startup genuinely restored and verified the archive.
        reads.clear()
        await rebuilt._indexer._seed_retained_packages(["imp.example"], specification())
        assert reads == []
    finally:
        await rebuilt.stop()


@pytest.mark.parametrize("changed", ["missing", "failed", "stale_spec", "stale_version", "count", "damaged"])
async def test_incomplete_or_stale_rows_still_use_verified_archive_recovery(installed, changed):
    service, reads = installed
    spec = specification()
    if changed == "count":
        record = service._registry.packs["imp.example"]
        service._publish_registry(service._registry.with_pack(replace(record, chunks=record.chunks + 1)))
    elif changed == "stale_spec":
        other = {**spec, "weights_sha256": {key: "b" * 64 for key in spec["weights_sha256"]}}
        with service._store._read() as conn:
            row = conn.execute("SELECT id,text_hash FROM chunks").fetchone()
        service._store.store_vectors(model_id=spec["model_id"], specification=other,
                                     rows=[(row[0], row[1], struct.pack("<64f", 1.0, *([0.0] * 63)))])
    else:
        with service._store._write() as conn:
            if changed == "stale_version":
                conn.execute("UPDATE packs SET pack_sha256=?", ("b" * 64,))
            elif changed == "damaged":
                conn.execute("UPDATE chunks SET vector=?", (b"\0" * 256,))
            else:
                conn.execute("UPDATE chunks SET vector=NULL, attempts=?", (3 if changed == "failed" else 0,))
    if changed == "damaged":
        # Numerical validation stays in the shared snapshot path. A temporary
        # ready count must not cache a success that hides its later NULL rows.
        await service._indexer._seed_retained_packages(["imp.example"], spec)
        assert reads == [] and not service._seeded_artifacts
        snapshot = service._store.load_vectors(spec["model_id"], specification=spec)
        assert snapshot is not None and snapshot.damaged
        await service._indexer._clear_damaged_vectors(snapshot)
    await service._indexer._seed_retained_packages(["imp.example"], spec)
    assert len(reads) == 1
    assert service._store.chunk_stats(spec["model_id"], specification=spec)["imp.example"]["ready"] == 1


async def test_new_resource_identity_is_not_hidden_by_a_complete_previous_spec(installed):
    service, reads = installed
    original = specification()
    await service._indexer._seed_retained_packages(["imp.example"], original)
    assert reads == []
    changed = {**original, "weights_sha256": {key: "b" * 64 for key in original["weights_sha256"]}}
    await service._indexer._seed_retained_packages(["imp.example"], changed)
    assert len(reads) == 1
    # The original artifact cannot certify new weights; ordinary backfill still
    # sees the complete input instead of inheriting an incorrect ready status.
    pending = service._store.pending_chunks(model_id=changed["model_id"], specification=changed,
                                           pack_ids=["imp.example"], limit=10)
    assert len(pending) == 1
