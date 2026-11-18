"""Retain all authoritative material while reclaiming only known obsolete indexes."""

from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from services.knowledge_library.models import LibraryError
from services.knowledge_library.package_build import build_package
from services.knowledge_library.package_validate import validate_package
from services.knowledge_library.persistence import write_json
from services.knowledge_library.service import LibraryService
from tests.unit.knowledge_library.test_library_domain import add_document, finished
from tests.unit.knowledge_library.test_packages import Encoder


pytestmark = pytest.mark.unit_fast


@pytest.fixture
async def library(tmp_path):
    service = LibraryService(tmp_path, embedder=Encoder())
    await service.start()
    try:
        yield service
    finally:
        await service.close()


def _authorities(root):
    """Capture source and task bytes; no cleanup may remove or alter these."""
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for name in ("libraries", "artifacts", "jobs") for path in (root / name).rglob("*") if path.is_file()}


@asynccontextmanager
async def _reopened(service):
    """Model Host restart with a fresh domain, not a closed JobManager reuse."""
    await service.close()
    restarted = LibraryService(service.store.root, embedder=service.embedder, write_guard=service.store.guard)
    await restarted.start()
    try:
        yield restarted
    finally:
        await restarted.close()


async def test_new_build_snapshot_omits_unused_vectors_but_legacy_frozen_input_still_builds(library):
    identifier = (await library.create_library("Frozen material"))["library_id"]
    await add_document(library, identifier, "An exact retained original.")
    original = library.store.documents(library.store.manifest(identifier))
    snapshot = await library.freeze_library(identifier)
    assert all("vector" not in chunk for chunk in snapshot["chunks"])
    saved = library.store.read(library.store.root / "jobs/snapshots" / f"{snapshot['snapshot_id']}.json")
    assert saved == snapshot
    assert library.store.documents(library.store.manifest(identifier)) == original

    # Old task snapshots remain legal retry inputs. Their cached vectors are
    # ignored by the builder, rather than being migrated or deleted in place.
    legacy = deepcopy(snapshot)
    vectors = {row["chunk_id"]: row["vector"] for document in original for row in document["chunks"]}
    legacy["chunks"] = [{**row, "vector": vectors[row["chunk_id"]]} for row in legacy["chunks"]]
    path = library.store.root / "jobs/snapshots/legacy-input.json"
    write_json(path, legacy)
    before = path.read_bytes()
    artifact = await build_package(library.store.read(path), library.store.root / "artifacts/build.zip", library.embedder)
    assert validate_package(Path(artifact["artifact_path"])).manifest["counts"]["chunks"] == len(snapshot["chunks"])
    assert path.read_bytes() == before


async def test_successful_update_prunes_only_old_index_and_retains_all_material(library):
    identifier = (await library.create_library("Version retention"))["library_id"]
    document = await add_document(library, identifier, "First complete version")
    first = library.store.manifest(identifier)
    old_index = library.store._index_path(first)
    snapshot = await library.freeze_library(identifier)
    await build_package(snapshot, library.store.root / "artifacts/build.zip", library.embedder)
    before = _authorities(library.store.root)
    await add_document(library, identifier, "Second complete version", document_id=document["document_id"])
    current = library.store.manifest(identifier)
    assert library.store._index_path(current).is_file()
    assert not old_index.exists()
    after = _authorities(library.store.root)
    for path, digest in before.items():
        if path.endswith("/manifest.json"):
            continue  # Only the active authority legitimately changes version.
        assert after.get(path) == digest, path


async def test_failed_commit_does_not_prune_and_cleanup_failure_cannot_fail_a_commit(library, monkeypatch):
    identifier = (await library.create_library("Commit boundary"))["library_id"]
    document = await add_document(library, identifier, "Committed version")
    old_manifest = library.store.manifest(identifier)
    old_index = library.store._index_path(old_manifest)
    older = old_index.with_name("0.sqlite")
    older.write_bytes(old_index.read_bytes())
    real_write = library.store._write_manifest

    def refuse(_manifest):
        raise OSError("injected commit failure")

    monkeypatch.setattr(library.store, "_write_manifest", refuse)
    preview_job = await finished(library, await library.preview_document(identifier, "notes.txt", b"Rejected version", document["document_id"]))
    preview = await library.get_preview(preview_job["result"]["preview_id"])
    failed = await finished(library, await library.confirm_preview(preview["preview_id"], preview["sha256"]))
    assert failed["state"] == "failed"
    assert library.store.manifest(identifier) == old_manifest
    assert older.is_file() and old_index.is_file()

    monkeypatch.setattr(library.store, "_write_manifest", real_write)
    unlink = Path.unlink

    def deny_old(path, *args, **kwargs):
        if path == older:
            raise PermissionError("injected derived-index cleanup failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_old)
    await add_document(library, identifier, "Successful committed replacement", document_id=document["document_id"])
    assert older.is_file()
    assert library.store._index_path(library.store.manifest(identifier)).is_file()
    monkeypatch.setattr(Path, "unlink", unlink)
    async with _reopened(library):
        assert not older.exists()


async def test_startup_skips_unknown_corrupt_future_and_symlink_indexes(library):
    identifier = (await library.create_library("Conservative startup"))["library_id"]
    document = await add_document(library, identifier, "One")
    await add_document(library, identifier, "Two", document_id=document["document_id"])
    await add_document(library, identifier, "Three", document_id=document["document_id"])
    manifest = library.store.manifest(identifier)
    current = library.store._index_path(manifest)
    old = [current.with_name("0.sqlite"), current.with_name("1.sqlite")]
    for path in old:
        path.write_bytes(b"obsolete derived index")
    preserved = []
    for name in ("01.sqlite", "unknown.sqlite", "4.sqlite", "1.building", "1.sqlite.bak"):
        path = current.with_name(name)
        path.write_bytes(b"unrecognized or uncommitted file")
        preserved.append(path)
    nested = current.parent / "unknown-directory/0.sqlite"
    nested.parent.mkdir()
    nested.write_bytes(b"unknown nested file")
    preserved.append(nested)
    unknown = library.store.root / "indexes/unknown-library/0.sqlite"
    unknown.parent.mkdir()
    unknown.write_bytes(b"no authoritative manifest")
    preserved.append(unknown)
    for identity, raw in (("damaged-library", b"{broken"), ("future-schema", json.dumps({
        "schema_version": 99, "library_id": "future-schema", "revision": 1, "documents": {},
    }).encode())):
        path = library.store.root / "libraries" / identity / "manifest.json"
        path.parent.mkdir()
        path.write_bytes(raw)
        index = library.store.root / "indexes" / identity / "0.sqlite"
        index.parent.mkdir()
        index.write_bytes(b"untrusted manifest keeps this file")
        preserved.append(index)
    target = library.store.root / "outside-index.sqlite"
    target.write_bytes(b"symlink target stays intact")
    link = current.with_name("2.sqlite")
    try:
        link.symlink_to(target)
    except OSError:
        link = None  # Other assertions remain useful where symlinks are unavailable.
    async with _reopened(library):
        assert all(not path.exists() for path in old)
        assert current.is_file() and all(path.is_file() for path in preserved)
        assert target.read_bytes() == b"symlink target stays intact"
        if link is not None:
            assert link.is_symlink()


async def test_deleted_library_keeps_tombstone_current_index_and_all_source_versions(library):
    identifier = (await library.create_library("Deleted but retained"))["library_id"]
    await add_document(library, identifier, "Source remains retained")
    current = library.store._index_path(library.store.manifest(identifier))
    older = current.with_name("0.sqlite")
    older.write_bytes(b"old derived file")
    before = _authorities(library.store.root)
    await library.delete_library(identifier)
    assert not older.exists() and current.is_file()
    tombstone = library.store._path(identifier).read_bytes()
    for path, digest in before.items():
        if not path.endswith("/manifest.json"):
            assert _authorities(library.store.root)[path] == digest
    older.write_bytes(b"old file can reappear after interrupted deletion")
    async with _reopened(library):
        assert not older.exists() and current.is_file()
        assert library.store._path(identifier).read_bytes() == tombstone


async def test_startup_cleanup_guard_failure_is_best_effort_and_retried(library):
    identifier = (await library.create_library("Cleanup admission"))["library_id"]
    await add_document(library, identifier, "Source authority")
    current = library.store._index_path(library.store.manifest(identifier))
    old = current.with_name("0.sqlite")
    old.write_bytes(b"obsolete derived data")
    await library.close()
    count = 0

    @contextmanager
    def guard():
        nonlocal count
        count += 1
        if count == 2:
            raise LibraryError("storage_busy")  # initialize succeeds; optional GC is refused.
        yield

    library.store.guard = guard
    async with _reopened(library) as restarted:
        assert old.is_file() and current.is_file()
        async with _reopened(restarted):
            assert not old.exists() and current.is_file()


@pytest.mark.parametrize("location", ["indexes", "index-library", "libraries", "library", "manifest", "current-index"])
async def test_cleanup_does_not_follow_symlinks_at_any_owned_boundary(library, location):
    identifier = (await library.create_library("Symlink boundaries"))["library_id"]
    await add_document(library, identifier, "Source authority")
    pointer = library.store._path(identifier)
    current = library.store._index_path(library.store.manifest(identifier))
    older = current.with_name("0.sqlite")
    older.write_bytes(b"kept behind a symlink")
    path = {"indexes": library.store.root / "indexes", "index-library": current.parent,
            "libraries": library.store.root / "libraries", "library": pointer.parent,
            "manifest": pointer, "current-index": current}[location]
    await library.close()
    is_directory = path.is_dir()
    target = path.with_name(path.name + ".retained-target")
    path.rename(target)
    try:
        path.symlink_to(target, target_is_directory=is_directory)
    except OSError:
        pytest.skip("this platform does not permit test symlinks")
    async with _reopened(library):
        assert path.is_symlink()
        assert older.read_bytes() == b"kept behind a symlink"
        assert current.is_file()


async def test_cancelled_precommit_candidate_is_not_pruned_as_an_old_revision(library):
    identifier = (await library.create_library("Cancellation boundary"))["library_id"]
    await add_document(library, identifier, "Committed source")
    manifest = library.store.manifest(identifier)
    current = library.store._index_path(manifest)
    older = current.with_name("0.sqlite")
    older.write_bytes(b"not touched before commit")
    candidate = deepcopy(manifest)
    candidate["revision"] += 1

    def cancel():
        raise LibraryError("job_cancelled")

    with library.store.lock, library.store.guard(), pytest.raises(LibraryError, match="job_cancelled"):
        library.store._activate_library_revision(candidate, library.store.documents(manifest), cancel)
    prepared_index = library.store._index_path(candidate)
    assert prepared_index.is_file() and older.is_file() and current.is_file()
    assert library.store.manifest(identifier) == manifest
    async with _reopened(library):
        assert not older.exists()
        assert prepared_index.is_file() and current.is_file()


async def test_missing_current_index_defers_cleanup_until_authoritative_rebuild(library):
    identifier = (await library.create_library("Missing active index"))["library_id"]
    await add_document(library, identifier, "Committed source")
    current = library.store._index_path(library.store.manifest(identifier))
    older = current.with_name("0.sqlite")
    older.write_bytes(b"defer cleanup while the active state is unknown")
    await library.close()
    current.unlink()
    async with _reopened(library) as restarted:
        assert older.is_file()
        assert (await restarted.search(identifier, "Committed"))["hits"]
        assert current.is_file()
        async with _reopened(restarted):
            assert not older.exists() and current.is_file()


@pytest.mark.parametrize("location", ["indexes", "index-library", "libraries", "library", "manifest", "current-index", "old-index"])
@pytest.mark.parametrize("reparse_tag", [0, 0xA0000003])
async def test_cleanup_treats_windows_reparse_points_as_links(library, monkeypatch, location, reparse_tag):
    """Preserve native stat fields while simulating a Windows directory junction."""
    identifier = (await library.create_library("Reparse boundaries"))["library_id"]
    await add_document(library, identifier, "Source remains authoritative")
    pointer = library.store._path(identifier)
    current = library.store._index_path(library.store.manifest(identifier))
    older = current.with_name("0.sqlite")
    older.write_bytes(b"do not traverse a reparse boundary")
    path = {"indexes": library.store.root / "indexes", "index-library": current.parent,
            "libraries": library.store.root / "libraries", "library": pointer.parent,
            "manifest": pointer, "current-index": current, "old-index": older}[location]
    real_lstat = Path.lstat

    class ReparseStat:
        def __init__(self, original):
            self.original = original
            self.st_reparse_tag = reparse_tag

        def __getattr__(self, name):
            return getattr(self.original, name)

    def lstat(candidate, *args, **kwargs):
        actual = real_lstat(candidate, *args, **kwargs)
        return ReparseStat(actual) if candidate == path else actual

    monkeypatch.setattr(Path, "lstat", lstat)
    async with _reopened(library):
        assert older.exists() is bool(reparse_tag)
        assert current.is_file()
