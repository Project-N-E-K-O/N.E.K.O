"""Reject unreadable activations and recover real process-killed commits."""

from copy import deepcopy
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import time

import pytest

from services.knowledge_library.models import LibraryError, LibraryLimits
from services.knowledge_library.store import LibraryStore
from services.knowledge_library import persistence
from services.knowledge_library import package_install, store as storage
from services.knowledge_library.package_validate import DEFAULT_LIMITS
from services.knowledge_library.service import LibraryService
from tests.process_isolation import child_process_env


pytestmark = pytest.mark.unit_fast


def _version(text="Original lighthouse", *, identity="version-one", rows=1):
    """Keep the storage fixture valid without loading an encoder or parser."""
    return {
        "document_id": "document-one", "version_id": identity,
        "filename": "notes.txt", "content": text,
        "chunks": [{"chunk_id": f"chunk-{index}", "text": text,
                    "location": {"paragraph": index + 1},
                    "vector": [0.125] * 64} for index in range(rows)],
    }


def _activate(store, manifest, version):
    updated = deepcopy(manifest)
    updated["revision"] += 1
    updated["documents"] = {version["document_id"]: version["version_id"]}
    return store._activate(updated, [version])


def test_activation_checks_formal_reader_before_switching_manifest(tmp_path, monkeypatch):
    """An unreadable candidate must never replace an existing usable revision."""
    store = LibraryStore(tmp_path, LibraryLimits())
    store.initialize()
    original = _activate(store, store.create("Saved notes"), _version())
    read = store.read

    def bounded_read(path):
        # A small injected boundary exercises the same post-expansion failure
        # as a legal large binary package, without allocating hundreds of MiB.
        if Path(path).stat().st_size > 8192:
            raise LibraryError("metadata_too_large")
        return read(path)

    monkeypatch.setattr(store, "read", bounded_read)
    candidate = _version("Replacement lighthouse", identity="version-two", rows=100)
    with pytest.raises(LibraryError, match="metadata_too_large"):
        _activate(store, original, candidate)
    assert store.manifest(original["library_id"]) == original
    assert store.document(original["library_id"], "document-one")["content"] == "Original lighthouse"
    assert store.search(original["library_id"], "Original", None, None, 5)["hits"]


def test_existing_candidate_is_checked_before_activation(tmp_path, monkeypatch):
    """A retained but damaged candidate cannot bypass write-time validation."""
    store = LibraryStore(tmp_path, LibraryLimits())
    store.initialize()
    original = _activate(store, store.create("Saved notes"), _version())
    candidate = _version("New material", identity="version-two")
    path = tmp_path / "libraries" / original["library_id"] / "versions/version-two.json"
    path.write_text('{"partial":', encoding="utf-8")
    with pytest.raises(LibraryError, match="metadata_invalid"):
        _activate(store, original, candidate)
    assert store.manifest(original["library_id"]) == original


def test_vector_json_expansion_is_bounded_without_replacing_old_file(tmp_path, monkeypatch):
    """Binary vector size cannot bypass the persisted record's byte budget."""
    target = tmp_path / "version.json"
    persistence.write_json(target, {"accepted": True})
    original = target.read_bytes()
    monkeypatch.setattr(persistence, "MAX_METADATA_BYTES", 1024)
    with pytest.raises(LibraryError, match="metadata_too_large"):
        persistence.write_json(target, {"vectors": [[0.03608439117670059] * 768]})
    assert target.read_bytes() == original
    assert not list(tmp_path.glob(".record-*.tmp"))


@pytest.mark.parametrize("invalid", [float("nan"), "bad\ud800"])
def test_invalid_record_does_not_replace_saved_metadata(tmp_path, invalid):
    """Reject invalid UTF-8 and JSON numbers before any visible replacement."""
    target = tmp_path / "metadata.json"
    persistence.write_json(target, {"accepted": True})
    with pytest.raises(LibraryError, match="metadata_invalid"):
        persistence.write_json(target, {"value": invalid})
    assert json.loads(target.read_bytes()) == {"accepted": True}
    assert not list(tmp_path.glob(".record-*.tmp"))


async def test_post_replace_sync_error_reports_committed_with_warning(tmp_path, monkeypatch):
    """Do not report rollback when the active pointer has already changed."""
    service = LibraryService(tmp_path)
    await service.start()
    try:
        store = service.store
        original = store.create("Saved notes")
        sync = persistence.sync_directory

        def fail_after_manifest_replace(path):
            target = store._path(original["library_id"])
            if path == target.parent and json.loads(target.read_bytes())["revision"] == 1:
                raise OSError("private filesystem detail must not become a task reason")
            sync(path)

        monkeypatch.setattr(persistence, "sync_directory", fail_after_manifest_replace)

        async def run(context):
            result = await context.run_sync(_activate, store, original, _version())
            return {"library_id": result["library_id"], "warnings": result.get("warnings", [])}

        submitted = await service.submit_job("install", original["library_id"], run, frozen_input={})
        await service.jobs.queue.join()
        terminal = await service.get_job(submitted["job_id"])
        assert terminal["state"] == "completed"
        assert terminal["result"]["warnings"] == ["storage_sync_unconfirmed"]
        selected = store.manifest(original["library_id"])
        assert selected["revision"] == 1 and "warnings" not in selected
        assert store.documents(selected)[0]["content"] == "Original lighthouse"
    finally:
        await service.close()


def test_retained_version_retry_rechecks_durability_before_activation(tmp_path, monkeypatch):
    """A readable candidate from a failed flush still needs a successful flush."""
    store = LibraryStore(tmp_path, LibraryLimits())
    store.initialize()
    original = _activate(store, store.create("Saved notes"), _version())
    candidate = _version("Replacement lighthouse", identity="version-two")
    sync = persistence.sync_directory
    attempts = []

    def flush_versions(path):
        if path.name == "versions":
            attempts.append(path)
            if len(attempts) <= 2:
                raise OSError("injected version directory flush failure")
        sync(path)

    monkeypatch.setattr(persistence, "sync_directory", flush_versions)
    monkeypatch.setattr(storage, "sync_directory", flush_versions)
    with pytest.raises(persistence.CommittedWriteError):
        _activate(store, original, candidate)
    assert store.manifest(original["library_id"]) == original
    with pytest.raises(OSError):
        _activate(store, original, candidate)
    assert store.manifest(original["library_id"]) == original
    activated = _activate(store, original, candidate)
    assert store.manifest(original["library_id"]) == activated
    assert len(attempts) == 3


def test_retained_artifact_retry_rechecks_directory_flush(tmp_path, monkeypatch):
    """A previous visible copy cannot bypass the installation durability fence."""
    (tmp_path / "artifacts").mkdir()
    source = tmp_path / "source"
    source.write_bytes(b"Synthetic immutable artifact bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    attempts = []

    def flush_directory(path):
        attempts.append(path)
        if len(attempts) == 1:
            raise OSError("injected artifact directory flush failure")

    monkeypatch.setattr(package_install, "sync_directory", flush_directory)
    with pytest.raises(OSError):
        package_install._retain(source, tmp_path, digest, DEFAULT_LIMITS)
    saved = package_install._retain(source, tmp_path, digest, DEFAULT_LIMITS)
    assert saved.read_bytes() == source.read_bytes() and len(attempts) == 2


async def test_visible_build_reports_unconfirmed_durability_without_false_failure(tmp_path, monkeypatch):
    """A completed immutable artifact remains exportable after a late flush error."""
    from services.knowledge_library import package_build
    from tests.unit.knowledge_library.test_packages import Encoder, _snapshot

    def fail_directory_sync(_path):
        raise OSError("injected artifact directory flush failure")

    monkeypatch.setattr(package_build, "sync_directory", fail_directory_sync)
    result = await package_build.build_package(_snapshot(), tmp_path / "artifacts/build.zip", Encoder())
    assert result["warnings"] == ["storage_sync_unconfirmed"]
    assert package_install.get_artifact(tmp_path, result["artifact_id"]).is_file()


_CRASH_CHILD = r'''
import json
from pathlib import Path
import sys
import threading

from tests.process_isolation import isolate_test_process
isolate_test_process()
from services.knowledge_library import store as storage
from services.knowledge_library.models import LibraryLimits

root, point = Path(sys.argv[1]), sys.argv[2]
payload = json.loads((root / "candidate.json").read_bytes())
store = storage.LibraryStore(root, LibraryLimits())
store.initialize()

def checkpoint():
    (root / "checkpoint").write_text("ready", encoding="utf-8")
    threading.Event().wait()

write_record = storage.atomic_write_json
def save_record(path, value):
    write_record(path, value)
    if point == "version_written" and path.name == "version-two.json":
        checkpoint()
storage.atomic_write_json = save_record

write_manifest = store._write_manifest
def commit(value):
    if point == "before_manifest":
        checkpoint()
    write_manifest(value)
    if point == "after_manifest":
        checkpoint()
store._write_manifest = commit
store._activate(payload["manifest"], [payload["version"]])
'''


@pytest.mark.parametrize("point", ["version_written", "before_manifest", "after_manifest"])
def test_killed_process_recovers_only_committed_revision(tmp_path, point):
    """Kill the actual writer and rebuild only the manifest-selected index."""
    store = LibraryStore(tmp_path, LibraryLimits())
    store.initialize()
    original = _activate(store, store.create("Saved notes"), _version())
    next_manifest = deepcopy(original)
    next_manifest["revision"] += 1
    next_manifest["documents"]["document-one"] = "version-two"
    candidate = _version("Replacement lighthouse", identity="version-two")
    (tmp_path / "candidate.json").write_text(
        json.dumps({"manifest": next_manifest, "version": candidate}), encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, "-c", _CRASH_CHILD, str(tmp_path), point],
        env=child_process_env(tmp_path / "isolated-runtime"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "checkpoint").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (tmp_path / "checkpoint").exists(), "writer did not reach the requested crash point"
        # POSIX uses SIGKILL; Windows uses TerminateProcess. Neither allows
        # finally blocks or graceful service shutdown to complete the commit.
        process.kill()
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)

    recovered = LibraryStore(tmp_path, LibraryLimits())
    recovered.initialize()
    manifest = recovered.manifest(original["library_id"])
    expected = "Replacement" if point == "after_manifest" else "Original"
    assert manifest == (next_manifest if point == "after_manifest" else original)
    assert recovered.document(manifest["library_id"], "document-one")["content"].startswith(expected)
    # Simulate a missing derived database after restart. Saved vectors and
    # text remain sufficient; no document parser or encoder is constructed.
    recovered._index_path(manifest).unlink()
    assert recovered.search(manifest["library_id"], expected, None, None, 5)["hits"]
    assert recovered.documents(manifest)[0]["chunks"][0]["vector"] == [0.125] * 64
