"""Numeric v2 storage hardening: portable delete manifests, share-violation retries, quarantine privacy."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_archive, numeric_v2_maintenance, numeric_v2_store
from services.theater.numeric_v2_registry import NumericV2PackageRegistry
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime
from services.theater.numeric_v2_store import update_numeric_v2_character_bindings
from tests.unit.test_theater_numeric_v2_runtime import _binding, _branch_story, _opening


def test_completed_maintenance_does_not_resolve_unrelated_character_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(numeric_v2_maintenance, '_MAINTAINED_ROOTS', {str(tmp_path.resolve())})

    def fail_ids():
        raise PermissionError('unrelated old character cannot be written')

    assert numeric_v2_maintenance.maintain_numeric_v2_storage_once(
        tmp_path, NumericV2PackageRegistry(tmp_path / 'packages'), character_ids_by_name=fail_ids) is None


async def _prepared_interrupted_delete(theater_root):
    story = _branch_story()
    story_id = story["meta"]["story_id"]
    registry = NumericV2PackageRegistry(theater_root / "numeric_v2" / "packages")
    registry.import_package(story)
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), theater_root)
    stored = await runtime.start_session(
        session_id="runtime_delete_migrated",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    transaction_dir, manifest_path, manifest = numeric_v2_maintenance._prepare_delete_transaction(
        theater_root, registry, story_id,
    )
    # Simulate the crash after the destructive phase started.
    await numeric_v2_store.delete_numeric_v2_sessions(theater_root, story_id=story_id)
    registry.delete_package(story_id)
    return story_id, stored.session.session_id, manifest_path, manifest


def _migrate(old_root, new_root):
    shutil.copytree(old_root, new_root)
    shutil.rmtree(old_root)


@pytest.mark.asyncio
async def test_delete_manifest_is_portable_across_storage_root_migration(tmp_path):
    old_root = tmp_path / "old" / "theater"
    new_root = tmp_path / "new" / "theater"
    story_id, session_id, manifest_path, manifest = await _prepared_interrupted_delete(old_root)
    assert not any(
        str(value).startswith(str(tmp_path))
        for value in manifest.values()
        if isinstance(value, str)
    )

    _migrate(old_root, new_root)
    numeric_v2_maintenance.recover_numeric_v2_delete_transactions(new_root)

    assert (new_root / "numeric_v2" / "packages" / f"{story_id}.json").is_file()
    assert (new_root / "numeric_v2" / "sessions" / f"{session_id}.json").is_file()
    assert not old_root.exists()
    assert not list((new_root / "numeric_v2" / "delete_transactions").iterdir())


@pytest.mark.asyncio
async def test_legacy_absolute_manifest_is_mapped_onto_current_root(tmp_path):
    old_root = tmp_path / "old" / "theater"
    new_root = tmp_path / "new" / "theater"
    story_id, session_id, manifest_path, manifest = await _prepared_interrupted_delete(old_root)
    legacy = dict(manifest)
    for key in numeric_v2_maintenance._MANIFEST_PATH_KEYS:
        legacy[key] = str(old_root / manifest[key])
    manifest_path.write_text(json.dumps(legacy), encoding="utf-8")

    _migrate(old_root, new_root)
    numeric_v2_maintenance.recover_numeric_v2_delete_transactions(new_root)

    assert (new_root / "numeric_v2" / "packages" / f"{story_id}.json").is_file()
    assert (new_root / "numeric_v2" / "sessions" / f"{session_id}.json").is_file()
    assert not old_root.exists()


@pytest.mark.asyncio
async def test_unmappable_legacy_manifest_is_kept_and_never_restored_elsewhere(tmp_path):
    old_root = tmp_path / "old" / "theater"
    new_root = tmp_path / "new" / "theater"
    story_id, session_id, manifest_path, manifest = await _prepared_interrupted_delete(old_root)
    legacy = dict(manifest)
    legacy["session_root"] = str(tmp_path / "elsewhere" / "sessions")
    manifest_path.write_text(json.dumps(legacy), encoding="utf-8")

    _migrate(old_root, new_root)
    numeric_v2_maintenance.recover_numeric_v2_delete_transactions(new_root)

    assert not (tmp_path / "elsewhere").exists()
    assert not (new_root / "numeric_v2" / "packages" / f"{story_id}.json").exists()
    assert len(list((new_root / "numeric_v2" / "delete_transactions").iterdir())) == 1


def test_manifest_path_rejects_parent_traversal(tmp_path):
    with pytest.raises(numeric_v2_maintenance._UnresolvableManifestPathError):
        numeric_v2_maintenance._manifest_path({"session_root": "../escape"}, "session_root", tmp_path)
    with pytest.raises(numeric_v2_maintenance._UnresolvableManifestPathError):
        numeric_v2_maintenance._manifest_path(
            {"session_root": str(tmp_path / ".." / "escape")}, "session_root", tmp_path,
        )


def _flaky(monkeypatch, target_owner, name, *, failures, predicate):
    original = getattr(target_owner, name)
    calls = {"failed": 0}

    def flaky(*args, **kwargs):
        if predicate(*args) and calls["failed"] < failures:
            calls["failed"] += 1
            raise PermissionError(32, "The process cannot access the file")
        return original(*args, **kwargs)

    monkeypatch.setattr(target_owner, name, flaky)
    return calls


def _simulate_windows(monkeypatch):
    monkeypatch.setattr(numeric_v2_archive, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(numeric_v2_archive, "time", SimpleNamespace(sleep=lambda _: None))


def test_story_session_index_retries_windows_share_violation(tmp_path, monkeypatch):
    _simulate_windows(monkeypatch)
    index = tmp_path / "numeric_v2" / "story_sessions.json"
    replace_calls = _flaky(
        monkeypatch, os, "replace", failures=2,
        predicate=lambda source, target, *rest: Path(target) == index,
    )
    numeric_v2_store._write_story_session_slots(index, {"story": {"char": "session"}})
    read_calls = _flaky(
        monkeypatch, Path, "read_text", failures=2,
        predicate=lambda path, *rest: path == index,
    )

    assert numeric_v2_store._read_story_session_slots(index) == {"story": {"char": "session"}}
    assert replace_calls["failed"] == 2
    assert read_calls["failed"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("exclusive", [True, False])
async def test_session_file_io_retries_windows_share_violation(tmp_path, monkeypatch, exclusive):
    story = _branch_story()
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_share_violation",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    store = runtime.store
    target = store._path(
        "runtime_share_violation_copy" if exclusive else "runtime_share_violation"
    )
    _simulate_windows(monkeypatch)
    replace_calls = _flaky(
        monkeypatch, os, "replace", failures=2,
        predicate=lambda source, destination, *rest: Path(destination) == target,
    )
    store._write(target, stored, exclusive=exclusive)
    read_calls = _flaky(
        monkeypatch, Path, "read_text", failures=2,
        predicate=lambda candidate, *rest: candidate == target,
    )

    assert store._read(target).session.session_id == stored.session.session_id
    assert replace_calls["failed"] == 2
    assert read_calls["failed"] == 2


_OTHER_CHARACTER = "character_22222222222222222222222222222222"


def _quarantine_copy(theater_root, source, session_id, *, reason="invalid", mutate=None, raw=None):
    """Place one session-quarantine file named exactly like the startup audit does."""
    quarantine_root = theater_root / "numeric_v2" / "quarantine"
    staging = theater_root / "staging"
    staging.mkdir(exist_ok=True)
    # The audit quarantines ``sessions/<session_id>.json`` and keeps that name as the suffix.
    staged = staging / f"{session_id}.json"
    if raw is not None:
        staged.write_text(raw, encoding="utf-8")
    else:
        payload = json.loads(source.read_text(encoding="utf-8"))
        if mutate is not None:
            mutate(payload)
        staged.write_text(json.dumps(payload), encoding="utf-8")
    before = set(quarantine_root.glob("*")) if quarantine_root.is_dir() else set()
    numeric_v2_maintenance._quarantine_session(staged, quarantine_root, reason)
    (created,) = set(quarantine_root.glob("*")) - before
    return created


async def _quarantine_fixture(theater_root):
    """One live Lan session plus quarantined copies: own, other character, other story, unknown."""
    story = _branch_story()
    story_id = story["meta"]["story_id"]
    registry = NumericV2PackageRegistry(theater_root / "numeric_v2" / "packages")
    registry.import_package(story)
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), theater_root)
    stored = await runtime.start_session(
        session_id="runtime_quarantine_live",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    source = runtime.store._path(stored.session.session_id)

    def rebind(session_id, **binding):
        def mutate(payload):
            payload["session"]["session_id"] = session_id
            payload["session"]["catgirl_binding"].update(binding)
        return mutate

    def restory(payload):
        payload["session"]["session_id"] = "runtime_other_story"
        payload["session"]["story_package_id"] = "other_story"

    files = {
        # Duplicate of the live session: attributable by its name/session id.
        "duplicate": _quarantine_copy(
            theater_root, source, stored.session.session_id, reason="duplicate",
        ),
        "own": _quarantine_copy(
            theater_root, source, "runtime_own_old", mutate=rebind("runtime_own_old"),
        ),
        "other_character": _quarantine_copy(
            theater_root, source, "runtime_other_char",
            mutate=rebind("runtime_other_char", character_id=_OTHER_CHARACTER, catgirl_name="Other"),
        ),
        "other_story": _quarantine_copy(
            theater_root, source, "runtime_other_story", mutate=restory,
        ),
        "unknown": _quarantine_copy(theater_root, source, "runtime_corrupt", raw="{broken"),
        # Unparseable, but its name still records an in-scope session id.
        "corrupt_live": _quarantine_copy(
            theater_root, source, stored.session.session_id, raw="{broken",
        ),
        # Another character that once used the same display name.
        "same_name_other": _quarantine_copy(
            theater_root, source, "runtime_same_name",
            mutate=rebind("runtime_same_name", character_id=_OTHER_CHARACTER),
        ),
    }
    return story_id, registry, runtime, stored, files


@pytest.mark.asyncio
async def test_quarantined_session_attribution_follows_archive_policy(tmp_path):
    story_id, _registry, _runtime, stored, files = await _quarantine_fixture(tmp_path)
    store = numeric_v2_archive.NumericV2ArchiveStore(tmp_path)

    character_scope = dict(
        character_id=_binding()["character_id"],
        legacy_catgirl_name="Lan",
        session_ids=[stored.session.session_id],
    )
    assert set(store.quarantined_session_paths(**character_scope, include_unattributable=True)) == {
        files["duplicate"], files["own"], files["other_story"], files["unknown"],
        files["corrupt_live"],
    }
    assert set(store.quarantined_session_paths(**character_scope)) == {
        files["duplicate"], files["own"], files["other_story"], files["corrupt_live"],
    }
    # Package delete: story scope, attributable only.
    assert set(store.quarantined_session_paths(
        story_id=story_id, session_ids=[stored.session.session_id],
    )) == {
        files["duplicate"], files["own"], files["other_character"], files["corrupt_live"],
        files["same_name_other"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_commit", [False, True])
async def test_package_delete_erases_attributable_quarantined_sessions_recoverably(
    tmp_path, monkeypatch, fail_commit,
):
    story_id, registry, _runtime, _stored, files = await _quarantine_fixture(tmp_path)
    contents = {name: path.read_bytes() for name, path in files.items()}
    if fail_commit:
        monkeypatch.setattr(
            registry, "delete_package", lambda _story_id: (_ for _ in ()).throw(OSError("busy")),
        )
        with pytest.raises(OSError):
            await numeric_v2_maintenance.delete_numeric_v2_story_transactionally(
                tmp_path, registry, story_id,
            )
        assert {name: path.read_bytes() for name, path in files.items()} == contents
        return

    await numeric_v2_maintenance.delete_numeric_v2_story_transactionally(tmp_path, registry, story_id)

    assert not files["duplicate"].exists()
    assert not files["own"].exists()
    assert not files["other_character"].exists()
    assert not files["corrupt_live"].exists()
    # Another story's copy and the unattributable one survive a package delete.
    assert files["other_story"].read_bytes() == contents["other_story"]
    assert files["unknown"].read_bytes() == contents["unknown"]


@pytest.mark.asyncio
async def test_interrupted_package_delete_restores_quarantined_sessions(tmp_path):
    story_id, registry, _runtime, _stored, files = await _quarantine_fixture(tmp_path)
    contents = {name: path.read_bytes() for name, path in files.items()}
    _dir, _manifest_path, manifest = numeric_v2_maintenance._prepare_delete_transaction(
        tmp_path, registry, story_id,
    )
    for name in manifest["quarantined_session_files"]:
        (tmp_path / "numeric_v2" / "quarantine" / name).unlink()

    numeric_v2_maintenance.recover_numeric_v2_delete_transactions(tmp_path)

    assert {name: path.read_bytes() for name, path in files.items()} == contents


@pytest.mark.asyncio
async def test_forget_erases_story_character_quarantined_sessions(tmp_path):
    story_id, _registry, _runtime, stored, files = await _quarantine_fixture(tmp_path)
    store = numeric_v2_archive.NumericV2ArchiveStore(tmp_path)

    pending = store.prepare_forget(
        story_id=story_id,
        character_id=_binding()["character_id"],
        legacy_catgirl_name="Lan",
        session=stored.session,
    )
    store.delete_forget_files(pending)

    assert not files["duplicate"].exists()
    assert not files["own"].exists()
    assert not files["unknown"].exists()
    assert not files["corrupt_live"].exists()
    assert files["other_character"].is_file()
    assert files["same_name_other"].is_file()
    assert files["other_story"].is_file()


def test_forget_rejects_path_like_quarantined_session_names(tmp_path):
    store = numeric_v2_archive.NumericV2ArchiveStore(tmp_path)
    with pytest.raises(numeric_v2_archive.NumericV2ArchiveError):
        store.delete_forget_files({
            "archive_files": [],
            "receipt_files": [],
            "quarantined_session_files": ["../sessions/live.json"],
        })


@pytest.mark.asyncio
async def test_character_delete_snapshots_and_erases_quarantined_sessions(tmp_path):
    from main_routers.characters_router import crud

    _story_id, _registry, _runtime, stored, files = await _quarantine_fixture(tmp_path)
    purge = await crud.collect_numeric_v2_character_purge(
        tmp_path, character_id=_binding()["character_id"], legacy_catgirl_name="Lan",
    )
    erased = {
        files["duplicate"], files["own"], files["other_story"], files["unknown"],
        files["corrupt_live"],
    }
    assert set(purge.quarantined_session_paths) == erased
    assert erased <= set(purge.snapshot_targets())

    await crud.purge_numeric_v2_character_data(purge)

    assert not any(path.exists() for path in erased)
    assert files["other_character"].is_file()
    assert files["same_name_other"].is_file()


@pytest.mark.asyncio
async def test_character_delete_scan_runs_off_event_loop(tmp_path, monkeypatch):
    from main_routers.characters_router import crud

    await _quarantine_fixture(tmp_path)
    loop_thread = threading.get_ident()
    observed: dict[str, list[int]] = {}

    def record(owner, name):
        original = getattr(owner, name)

        def wrapper(*args, **kwargs):
            observed.setdefault(name, []).append(threading.get_ident())
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, wrapper)

    record(crud, "list_numeric_v2_sessions")
    record(crud, "list_numeric_v2_public_archives")
    record(numeric_v2_archive.NumericV2ArchiveStore, "receipt_paths_for_scope")
    purge = await crud.collect_numeric_v2_character_purge(
        tmp_path, character_id=_binding()["character_id"], legacy_catgirl_name="Lan",
    )

    assert purge.session_paths
    assert set(observed) == {
        "list_numeric_v2_sessions", "list_numeric_v2_public_archives", "receipt_paths_for_scope",
    }
    assert all(ident != loop_thread for idents in observed.values() for ident in idents)


_OFF_LOOP_IO = (
    "list_numeric_v2_sessions",
    "_read_story_session_slots",
    "_atomic_write_json_payload",
    "_write_story_session_slots",
)


@pytest.mark.asyncio
async def test_character_binding_update_runs_file_io_off_event_loop(tmp_path, monkeypatch):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_rename_off_loop",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    loop_thread = threading.get_ident()
    observed: dict[str, list[int]] = {}

    def record(name):
        original = getattr(numeric_v2_store, name)

        def wrapper(*args, **kwargs):
            observed.setdefault(name, []).append(threading.get_ident())
            return original(*args, **kwargs)

        monkeypatch.setattr(numeric_v2_store, name, wrapper)

    for name in _OFF_LOOP_IO:
        record(name)
    renamed = {**_binding(), "catgirl_name": "Lan Renamed"}

    assert await update_numeric_v2_character_bindings(
        tmp_path,
        character_id=_binding()["character_id"],
        legacy_catgirl_name="Lan",
        catgirl_binding=renamed,
    ) == 1

    assert set(observed) == set(_OFF_LOOP_IO)
    assert all(ident != loop_thread for idents in observed.values() for ident in idents)
    restored = await runtime.restore_story_session(renamed)
    assert restored is not None
    assert restored.session.session_id == stored.session.session_id
    assert restored.session.catgirl_binding["catgirl_name"] == "Lan Renamed"


@pytest.mark.asyncio
async def test_cancelled_character_binding_update_keeps_session_lock_until_write_ends(
    tmp_path, monkeypatch,
):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_rename_cancel",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    writing = threading.Event()
    release = threading.Event()
    original_write = numeric_v2_store._atomic_write_json_payload

    def blocking_write(path, payload):
        writing.set()
        assert release.wait(timeout=5)
        return original_write(path, payload)

    monkeypatch.setattr(numeric_v2_store, "_atomic_write_json_payload", blocking_write)
    renamed = {**_binding(), "catgirl_name": "Lan Renamed"}
    update = asyncio.create_task(update_numeric_v2_character_bindings(
        tmp_path,
        character_id=_binding()["character_id"],
        legacy_catgirl_name="Lan",
        catgirl_binding=renamed,
    ))
    assert await asyncio.to_thread(writing.wait, 5)
    update.cancel()
    load = asyncio.create_task(runtime.store.load(stored.session.session_id))
    await asyncio.sleep(0.05)
    # The session path lock stays with the cancelled rename until its worker ends.
    assert not update.done()
    assert not load.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await update
    loaded = await load
    assert loaded is not None
    assert loaded.session.catgirl_binding["catgirl_name"] == "Lan Renamed"


@pytest.fixture
def locked_temporary_files(monkeypatch):
    """Simulate Windows: a temp file another process holds open cannot be unlinked."""

    original_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name.endswith(".tmp"):
            raise PermissionError(13, "file is being used by another process", str(self))
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)


@pytest.mark.asyncio
async def test_locked_temp_file_does_not_mask_session_exists(tmp_path, locked_temporary_files):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_locked_temp",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    with pytest.raises(numeric_v2_store.NumericV2SessionExistsError):
        runtime.store._write(runtime.store._path("runtime_locked_temp"), stored, exclusive=True)


def test_locked_temp_file_does_not_mask_package_exists(tmp_path, monkeypatch, locked_temporary_files):
    from services.theater.numeric_v2_registry import NumericV2PackageExistsError

    registry = NumericV2PackageRegistry(tmp_path / "packages")
    registry.import_package(_branch_story())
    original_lexists = os.path.lexists
    checks = []

    def lexists(path):
        # Another importer publishes between the fast check and the locked one.
        checks.append(path)
        return original_lexists(path) if len(checks) > 1 else False

    monkeypatch.setattr(os.path, "lexists", lexists)
    with pytest.raises(NumericV2PackageExistsError):
        registry.import_package(_branch_story())


@pytest.mark.parametrize("writer", ["archive", "index", "payload", "manifest"])
def test_locked_temp_file_does_not_mask_replace_failure(tmp_path, monkeypatch, locked_temporary_files, writer):
    def failing_replace(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", failing_replace)
    target = tmp_path / "target.json"
    if writer == "archive":
        with pytest.raises(numeric_v2_archive.NumericV2ArchiveError, match="write_failed"):
            numeric_v2_archive.NumericV2ArchiveStore._write(target, {"a": 1})
    elif writer == "index":
        with pytest.raises(OSError, match="No space left"):
            numeric_v2_store._write_story_session_slots(target, {})
    elif writer == "payload":
        with pytest.raises(OSError, match="No space left"):
            numeric_v2_store._atomic_write_json_payload(target, {"a": 1})
    else:
        with pytest.raises(OSError, match="No space left"):
            numeric_v2_maintenance._atomic_write_manifest(target, {"a": 1})



@pytest.mark.asyncio
async def test_character_delete_erases_her_queued_memory_retractions(tmp_path):
    """Queued retractions target a memory that the character delete removes anyway."""
    from main_routers.characters_router import crud

    store = numeric_v2_archive.NumericV2ArchiveStore(tmp_path)

    def queue(character_id, request_id):
        store.queue_retractions({
            "story_id": "story", "session_id": f"session_{request_id}", "status": "pending",
            "receipt_id": "theater_end_" + "0" * 40, "character_id": character_id,
            "catgirl_name": "Lan", "archive_request_id": request_id, "archive_attempt": 1,
            "archive_through_revision": 2,
        })

    queue(_binding()["character_id"], "own_request")
    queue("character_" + "f" * 32, "other_request")
    purge = await crud.collect_numeric_v2_character_purge(
        tmp_path, character_id=_binding()["character_id"], legacy_catgirl_name="Lan",
    )
    intent_path = store._retract_intent_path("own_request")
    assert intent_path in purge.purge_targets()
    # Workshop unsubscribe persists the same list as a purge intent first.
    numeric_v2_maintenance.write_character_purge_intent(
        tmp_path, character_id=_binding()["character_id"], legacy_catgirl_name="Lan",
        targets=purge.purge_targets(),
    )

    await crud.purge_numeric_v2_character_data(purge)

    assert not intent_path.exists()
    assert store._retract_intent_path("other_request").is_file()
