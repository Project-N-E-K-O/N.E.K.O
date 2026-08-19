from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any

import pytest

from plugin.server.application.plugins import lifecycle_service as module
from plugin.server.application.plugins.package_ownership import sha256_file
from plugin.server.application.plugins.mutation_guard import plugin_mutation_guard
from plugin.server.application.plugins.registry_service import PluginRegistryService
from plugin.server.domain.errors import ServerDomainError


def test_delete_journal_syncs_parent_after_owner_and_state_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = tmp_path / "plugins" / "demo"
    rollback_snapshot = tmp_path / "plugins" / ".delete-backups" / "demo.snapshot"
    synced_paths: list[Path] = []
    monkeypatch.setattr(
        module,
        "fsync_parent_directory",
        lambda path: synced_paths.append(path),
        raising=False,
    )

    journal = module._DeleteJournal.create(
        plugin_id="demo",
        plugin_dir=plugin_dir,
        rollback_snapshot=rollback_snapshot,
    )

    assert synced_paths == [journal.owner_path, journal.path]


async def _checkpoint() -> None:
    event = asyncio.Event()
    asyncio.get_running_loop().call_soon(event.set)
    await event.wait()


def _snapshot_tree(root: Path) -> dict[str, tuple[str, bytes]]:
    snapshot: dict[str, tuple[str, bytes]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[relative] = ("directory", b"")
        else:
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def _write_plugin_tree(plugin_dir: Path, plugin_id: str) -> None:
    files = {
        "plugin.toml": (
            f"[plugin]\nid='{plugin_id}'\nentry='tests.fake:Plugin'\n".encode()
        ),
        "code/main.py": b"PLUGIN_VERSION = 'old'\n",
        "assets/model.bin": b"package-owned-model",
        "config/settings.json": b'{"theme":"dark"}',
        "data/user.db": b"persistent-user-database",
        "cache/index.bin": b"runtime-cache",
    }
    for relative_path, contents in files.items():
        path = plugin_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_delete_snapshot_rejects_junction_without_copying_external_data(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "user.db"
    secret.write_bytes(b"external-user-data")
    junction = plugin_dir / "data"
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")

    with pytest.raises(OSError, match="linked path"):
        module._capture_delete_rollback_snapshot_sync(plugin_dir)

    assert secret.read_bytes() == b"external-user-data"
    assert not (plugin_dir.parent / ".delete-backups").exists()


def _write_delete_journal(
    journal_root: Path,
    *,
    operation_id: str,
    plugin_id: str,
    phase: str,
    plugin_dir: Path,
    backup_dir: Path,
    rollback_snapshot: Path,
    schema_version: int = 1,
) -> Path:
    journal_root.mkdir(parents=True, exist_ok=True)
    journal_path = journal_root / f"{operation_id}.json"
    journal_path.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "operation_id": operation_id,
                "plugin_id": plugin_id,
                "phase": phase,
                "plugin_dir": str(plugin_dir),
                "backup_dir": str(backup_dir),
                "rollback_snapshot": str(rollback_snapshot),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    journal_path.with_suffix(".owner").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation_id": operation_id,
                "plugin_id": plugin_id,
                "kind": "deletion",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return journal_path


class _SourceManager:
    def __init__(self, plugin_dir: Path) -> None:
        self._state = {str(plugin_dir): "imported"}
        self.restore_calls = 0

    @property
    def state(self) -> dict[str, str]:
        return dict(self._state)

    def snapshot(self) -> dict[str, str]:
        return dict(self._state)

    def mark_removed(self, *, directory_path: Path, reason: str) -> None:
        assert reason == "user_overlay_removed"
        self._state.pop(str(directory_path), None)

    def restore_snapshot_for_rollback(self, snapshot: dict[str, str]) -> None:
        self.restore_calls += 1
        self._state = dict(snapshot)


class _TrackingMutationGuard:
    def __init__(
        self,
        factory: Callable[[], Any],
        exit_started: threading.Event,
    ) -> None:
        self._delegate = factory()
        self._exit_started = exit_started

    async def __aenter__(self) -> None:
        await self._delegate.__aenter__()

    async def __aexit__(self, *args: object) -> bool:
        self._exit_started.set()
        return await self._delegate.__aexit__(*args)


def _prepare_delete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    plugin_id: str,
    running: bool,
) -> tuple[Path, _SourceManager, dict[str, str], dict[str, bool], list[str]]:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    plugin_dir = user_root / plugin_id
    builtin_root.mkdir()
    _write_plugin_tree(plugin_dir, plugin_id)

    source_manager = _SourceManager(plugin_dir)
    inventory = {plugin_id: plugin_id}
    runtime = {"running": running}
    lifecycle_calls: list[str] = []

    monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (builtin_root, user_root))
    monkeypatch.setattr(
        module,
        "_get_plugin_meta_sync",
        lambda requested_id: {
            "id": requested_id,
            "config_path": str(plugin_dir / "plugin.toml"),
        },
    )
    monkeypatch.setattr(
        module,
        "_resolve_plugin_dir_sync",
        lambda _plugin_id, _plugin_meta: plugin_dir,
    )
    monkeypatch.setattr(module, "_path_within_plugin_roots_sync", lambda _path: True)
    monkeypatch.setattr(module, "_plugin_root_kind_sync", lambda _path: "user")
    monkeypatch.setattr(module, "_builtin_plugin_exists_sync", lambda _plugin_id: False)
    monkeypatch.setattr(module, "get_install_source_manager", lambda: source_manager)
    monkeypatch.setattr(module, "capture_inventory_snapshot", lambda: dict(inventory))

    def _remove_inventory(requested_id: str) -> None:
        inventory.pop(requested_id, None)

    def _restore_inventory(snapshot: dict[str, str]) -> None:
        inventory.clear()
        inventory.update(snapshot)

    monkeypatch.setattr(module, "remove_user_installation", _remove_inventory)
    monkeypatch.setattr(module, "restore_inventory_snapshot", _restore_inventory)
    monkeypatch.setattr(module, "_plugin_is_running_sync", lambda _plugin_id: runtime["running"])

    async def _stop(_plugin_id: str) -> dict[str, object]:
        lifecycle_calls.append("stop")
        runtime["running"] = False
        return {"success": True}

    async def _start(_plugin_id: str, **_kwargs: object) -> dict[str, object]:
        lifecycle_calls.append("start")
        runtime["running"] = True
        return {"success": True}

    async def _refresh() -> dict[str, object]:
        return {"success": True}

    monkeypatch.setattr(module.PluginLifecycleService, "stop_plugin", staticmethod(_stop))
    monkeypatch.setattr(module.PluginLifecycleService, "start_plugin", staticmethod(_start))
    monkeypatch.setattr(module.plugin_registry_service, "refresh_registry", _refresh)
    monkeypatch.setattr(module, "_pop_plugin_host_sync", lambda _plugin_id: None)
    monkeypatch.setattr(module, "_remove_event_handlers_sync", lambda _plugin_id: None)
    monkeypatch.setattr(module, "_remove_plugin_metadata_sync", lambda _plugin_id: None)
    monkeypatch.setattr(module, "clear_runtime_override", lambda _plugin_id: None)
    monkeypatch.setattr(module, "emit_lifecycle_event", lambda _event: None)

    return plugin_dir, source_manager, inventory, runtime, lifecycle_calls


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_delete_partial_filesystem_failure_restores_exact_original_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_id = "partial_delete_demo"
    plugin_dir, source_manager, inventory, runtime, lifecycle_calls = _prepare_delete(
        monkeypatch,
        tmp_path,
        plugin_id=plugin_id,
        running=True,
    )
    before = _snapshot_tree(plugin_dir)
    source_before = source_manager.state

    def _fail_after_removing_one_child(target: Path) -> bool:
        shutil.rmtree(target / "code")
        raise OSError("injected failure after one child was removed")

    monkeypatch.setattr(
        module,
        "_delete_plugin_directory_sync",
        _fail_after_removing_one_child,
    )

    with pytest.raises(ServerDomainError) as exc_info:
        await module.PluginLifecycleService().delete_plugin(plugin_id)

    assert exc_info.value.code == "PLUGIN_DELETE_FAILED"
    assert plugin_dir.is_dir()
    assert _snapshot_tree(plugin_dir) == before
    assert inventory == {plugin_id: plugin_id}
    assert source_manager.state == source_before
    assert source_manager.restore_calls == 1
    assert runtime["running"] is True
    assert lifecycle_calls == ["stop", "start"]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_cancel_during_failed_delete_recovery_finishes_before_guard_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_id = "failed_delete_cancel_recovery"
    plugin_dir, source_manager, inventory, runtime, lifecycle_calls = _prepare_delete(
        monkeypatch,
        tmp_path,
        plugin_id=plugin_id,
        running=True,
    )
    before = _snapshot_tree(plugin_dir)
    source_before = source_manager.state
    recovery_entered = asyncio.Event()
    recovery_finished = asyncio.Event()
    release_recovery = threading.Event()
    contender_acquired = asyncio.Event()
    release_contender = asyncio.Event()
    loop = asyncio.get_running_loop()
    original_restore = source_manager.restore_snapshot_for_rollback

    def fail_delete(target: Path) -> bool:
        shutil.rmtree(target / "code")
        raise OSError("injected delete failure")

    def blocking_restore(snapshot: dict[str, str]) -> None:
        loop.call_soon_threadsafe(recovery_entered.set)
        release_recovery.wait()
        original_restore(snapshot)
        loop.call_soon_threadsafe(recovery_finished.set)

    source_manager.restore_snapshot_for_rollback = blocking_restore  # type: ignore[method-assign]
    monkeypatch.setattr(module, "_delete_plugin_directory_sync", fail_delete)

    async def contend() -> None:
        async with plugin_mutation_guard():
            contender_acquired.set()
            await release_contender.wait()

    operation = asyncio.create_task(
        module.PluginLifecycleService().delete_plugin(plugin_id)
    )
    contender: asyncio.Task[None] | None = None
    try:
        await recovery_entered.wait()
        operation.cancel()
        await _checkpoint()
        contender = asyncio.create_task(contend())
        await _checkpoint()

        assert not operation.done()
        assert not contender_acquired.is_set()

        operation.cancel()
        await _checkpoint()
        assert not operation.done()
        assert not contender_acquired.is_set()

        release_recovery.set()
        await recovery_finished.wait()
        with pytest.raises(asyncio.CancelledError):
            await operation

        assert _snapshot_tree(plugin_dir) == before
        assert inventory == {plugin_id: plugin_id}
        assert source_manager.state == source_before
        assert runtime["running"] is True
        assert lifecycle_calls == ["stop", "start"]
        await contender_acquired.wait()
        release_contender.set()
        await contender
    finally:
        release_recovery.set()
        release_contender.set()
        tasks = [operation]
        if contender is not None:
            tasks.append(contender)
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_delete_cancel_during_filesystem_move_holds_guard_and_restores_everything(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_id = "cancel_delete_demo"
    plugin_dir, source_manager, inventory, runtime, lifecycle_calls = _prepare_delete(
        monkeypatch,
        tmp_path,
        plugin_id=plugin_id,
        running=True,
    )
    before = _snapshot_tree(plugin_dir)
    source_before = source_manager.state
    backup_dir = plugin_dir.parent / ".delete-backups" / plugin_id
    worker_entered = asyncio.Event()
    release_worker = threading.Event()
    guard_exit_started = threading.Event()
    loop = asyncio.get_running_loop()

    def _move_then_block(target: Path) -> bool:
        backup_dir.parent.mkdir(parents=True, exist_ok=True)
        target.rename(backup_dir)
        loop.call_soon_threadsafe(worker_entered.set)
        release_worker.wait()
        return True

    original_guard_factory = plugin_mutation_guard
    monkeypatch.setattr(
        module,
        "plugin_mutation_guard",
        lambda: _TrackingMutationGuard(original_guard_factory, guard_exit_started),
    )
    monkeypatch.setattr(module, "_delete_plugin_directory_sync", _move_then_block)

    delete_task = asyncio.create_task(
        module.PluginLifecycleService().delete_plugin(plugin_id),
        name="delete-cancel-in-flight",
    )
    await worker_entered.wait()
    delete_task.cancel()

    guard_released_at_cancel_checkpoint: list[bool] = []
    checkpoint_reached = asyncio.Event()

    def _cancellation_checkpoint() -> None:
        guard_released_at_cancel_checkpoint.append(guard_exit_started.is_set())
        delete_task.cancel()
        release_worker.set()
        checkpoint_reached.set()

    loop.call_soon(_cancellation_checkpoint)
    await checkpoint_reached.wait()

    with pytest.raises(asyncio.CancelledError):
        await delete_task

    assert guard_released_at_cancel_checkpoint == [False]
    assert plugin_dir.is_dir()
    assert not backup_dir.exists()
    assert _snapshot_tree(plugin_dir) == before
    assert inventory == {plugin_id: plugin_id}
    assert source_manager.state == source_before
    assert source_manager.restore_calls == 1
    assert runtime["running"] is True
    assert lifecycle_calls == ["stop", "start"]

    guard_reacquired = False
    async with original_guard_factory():
        guard_reacquired = True
    assert guard_reacquired is True


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_delete_success_preserves_mutable_state_and_cleans_transaction_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_id = "successful_delete_demo"
    plugin_dir, source_manager, inventory, runtime, lifecycle_calls = _prepare_delete(
        monkeypatch,
        tmp_path,
        plugin_id=plugin_id,
        running=False,
    )
    expected_state = {
        name: _snapshot_tree(plugin_dir / name)
        for name in ("config", "data", "cache")
    }

    result = await module.PluginLifecycleService().delete_plugin(plugin_id)

    assert result["success"] is True
    assert result["deleted_from_disk"] is True
    assert result["user_data_preserved"] is True
    for name, snapshot in expected_state.items():
        state_dir = plugin_dir / name
        assert state_dir.is_dir()
        assert _snapshot_tree(state_dir) == snapshot
    assert not (plugin_dir / "plugin.toml").exists()
    assert not (plugin_dir / "code").exists()
    assert not (plugin_dir / "assets").exists()
    assert inventory == {}
    assert source_manager.state == {}
    assert runtime["running"] is False
    assert lifecycle_calls == []
    backup_root = plugin_dir.parent / ".delete-backups"
    assert not backup_root.exists() or not any(backup_root.iterdir())


@pytest.mark.plugin_unit
def test_plugin_root_kind_distinguishes_managed_code_from_legacy_state_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    builtin_root = tmp_path / "distribution" / "plugin" / "plugins"
    managed_root = tmp_path / "plugin-installations"
    legacy_root = tmp_path / "plugins"
    monkeypatch.setattr(
        module,
        "PLUGIN_CONFIG_ROOTS",
        (builtin_root, managed_root, legacy_root),
    )

    assert module._plugin_root_kind_sync(builtin_root / "demo") == "builtin"
    assert module._plugin_root_kind_sync(managed_root / "demo") == "managed"
    assert module._plugin_root_kind_sync(legacy_root / "demo") == "legacy"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_delete_managed_payload_removes_code_and_preserves_external_user_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_id = "managed_delete_demo"
    plugin_dir, source_manager, inventory, runtime, lifecycle_calls = _prepare_delete(
        monkeypatch,
        tmp_path,
        plugin_id=plugin_id,
        running=True,
    )
    external_state = tmp_path / "runtime" / "plugins" / plugin_id
    external_files = {
        external_state / "config" / "plugin.toml": b"user-config\x00",
        external_state / "data" / "user.db": b"user-data\x00",
        external_state / "cache" / "index.bin": b"user-cache\x00",
    }
    for path, payload in external_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    # This package is SDK-compliant: its managed payload owns every file that
    # happens to use a legacy state directory name.  User state lives only in
    # the external runtime root above.
    package_state_files = {
        path.relative_to(plugin_dir).as_posix(): sha256_file(path)
        for state_name in ("config", "data", "cache")
        for path in (plugin_dir / state_name).rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(module, "_plugin_root_kind_sync", lambda _path: "managed")
    monkeypatch.setattr(module, "_builtin_plugin_exists_sync", lambda _plugin_id: True)
    monkeypatch.setattr(
        module,
        "get_user_installation_package_state_files",
        lambda requested_id, *, directory_name: (
            package_state_files
            if requested_id == plugin_id and directory_name == plugin_dir.name
            else None
        ),
        raising=False,
    )

    result = await module.PluginLifecycleService().delete_plugin(plugin_id)

    assert result["success"] is True
    assert result["installation_kind"] == "managed"
    assert result["user_data_preserved"] is True
    assert result["fallback_to_builtin"] is True
    assert result["fallback_runtime_started"] is True
    assert not plugin_dir.exists()
    assert inventory == {}
    assert source_manager.state == {}
    assert runtime["running"] is True
    assert lifecycle_calls == ["stop", "start"]
    assert {path: path.read_bytes() for path in external_files} == external_files


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_delete_managed_payload_keeps_only_unowned_legacy_state_residue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_id = "managed_legacy_state_demo"
    plugin_dir, _source_manager, _inventory, _runtime, _lifecycle_calls = _prepare_delete(
        monkeypatch,
        tmp_path,
        plugin_id=plugin_id,
        running=False,
    )
    package_owned = plugin_dir / "data" / "defaults.json"
    package_owned.write_bytes(b"package defaults")
    user_created = plugin_dir / "data" / "user-created.db"
    user_created.write_bytes(b"legacy runtime state")
    package_state_files = {
        path.relative_to(plugin_dir).as_posix(): sha256_file(path)
        for state_name in ("config", "data", "cache")
        for path in (plugin_dir / state_name).rglob("*")
        if path.is_file() and path != user_created
    }
    monkeypatch.setattr(module, "_plugin_root_kind_sync", lambda _path: "managed")
    monkeypatch.setattr(
        module,
        "get_user_installation_package_state_files",
        lambda _plugin_id, *, directory_name: package_state_files,
        raising=False,
    )

    result = await module.PluginLifecycleService().delete_plugin(plugin_id)

    assert result["success"] is True
    assert result["compatibility_state_preserved"] is True
    assert user_created.read_bytes() == b"legacy runtime state"
    assert not package_owned.exists()
    assert not (plugin_dir / "plugin.toml").exists()
    assert not (plugin_dir / "code").exists()
    assert not (plugin_dir / "assets").exists()


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_delete_cancel_waits_for_failed_committed_cleanup_and_keeps_journal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_id = "delete_committed_cleanup_failure"
    plugin_dir, _source_manager, inventory, _runtime, _lifecycle_calls = _prepare_delete(
        monkeypatch,
        tmp_path,
        plugin_id=plugin_id,
        running=False,
    )
    backup_root = plugin_dir.parent / ".delete-backups"
    cleanup_entered = asyncio.Event()
    cleanup_finished = asyncio.Event()
    release_cleanup = threading.Event()
    contender_acquired = asyncio.Event()
    release_contender = asyncio.Event()
    loop = asyncio.get_running_loop()
    original_cleanup = module._finalize_delete_transaction_sync

    def fail_cleanup(_plugin_dir: Path, _rollback_snapshot: Path | None) -> None:
        loop.call_soon_threadsafe(cleanup_entered.set)
        release_cleanup.wait()
        loop.call_soon_threadsafe(cleanup_finished.set)
        raise OSError("simulated committed backup cleanup failure")

    monkeypatch.setattr(module, "_finalize_delete_transaction_sync", fail_cleanup)

    async def contend() -> None:
        async with plugin_mutation_guard():
            contender_acquired.set()
            await release_contender.wait()

    operation = asyncio.create_task(
        module.PluginLifecycleService().delete_plugin(plugin_id),
        name="delete-committed-cleanup-failure",
    )
    contender: asyncio.Task[None] | None = None
    try:
        await cleanup_entered.wait()
        journal_path = next((backup_root / ".transactions").glob("*.json"))
        assert json.loads(journal_path.read_text(encoding="utf-8"))["phase"] == "committed"

        operation.cancel()
        await _checkpoint()
        contender = asyncio.create_task(contend())
        await _checkpoint()
        operation.cancel()
        await _checkpoint()

        assert not operation.done()
        assert not contender_acquired.is_set()

        release_cleanup.set()
        await cleanup_finished.wait()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await operation

        assert getattr(exc_info.value, "cleanup_code", None) == (
            "PLUGIN_DELETE_COMMITTED_CLEANUP_INCOMPLETE"
        )
        assert getattr(exc_info.value, "cleanup_errors", ()) == (
            "backup_cleanup:OSError",
        )
        assert journal_path.exists()
        assert journal_path.with_suffix(".owner").exists()
        assert inventory == {}

        await contender_acquired.wait()
        release_contender.set()
        assert contender is not None
        await contender

        monkeypatch.setattr(
            module,
            "_finalize_delete_transaction_sync",
            original_cleanup,
        )
        recovered = module.recover_incomplete_plugin_deletions(
            journal_root=backup_root / ".transactions",
            user_root=plugin_dir.parent,
        )
        assert recovered.recovered_operation_ids == (journal_path.stem,)
        assert not journal_path.exists()
        assert not journal_path.with_suffix(".owner").exists()
        assert cleanup_finished.is_set()
    finally:
        release_cleanup.set()
        release_contender.set()
        tasks = [operation]
        if contender is not None:
            tasks.append(contender)
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_delete_cancel_preserves_committed_journal_when_finish_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_id = "delete_committed_journal_failure"
    plugin_dir, _source_manager, inventory, _runtime, _lifecycle_calls = _prepare_delete(
        monkeypatch,
        tmp_path,
        plugin_id=plugin_id,
        running=False,
    )
    backup_root = plugin_dir.parent / ".delete-backups"
    cleanup_entered = asyncio.Event()
    finish_entered = asyncio.Event()
    finish_finished = asyncio.Event()
    release_cleanup = threading.Event()
    release_finish = threading.Event()
    contender_acquired = asyncio.Event()
    release_contender = asyncio.Event()
    loop = asyncio.get_running_loop()
    original_cleanup = module._finalize_delete_transaction_sync

    def blocked_cleanup(target: Path, snapshot: Path | None) -> None:
        loop.call_soon_threadsafe(cleanup_entered.set)
        release_cleanup.wait()
        original_cleanup(target, snapshot)

    def fail_finish(_journal: object) -> None:
        loop.call_soon_threadsafe(finish_entered.set)
        release_finish.wait()
        loop.call_soon_threadsafe(finish_finished.set)
        raise OSError("simulated committed journal finish failure")

    monkeypatch.setattr(module, "_finalize_delete_transaction_sync", blocked_cleanup)
    monkeypatch.setattr(module._DeleteJournal, "finish", fail_finish)

    async def contend() -> None:
        async with plugin_mutation_guard():
            contender_acquired.set()
            await release_contender.wait()

    operation = asyncio.create_task(
        module.PluginLifecycleService().delete_plugin(plugin_id),
        name="delete-committed-journal-failure",
    )
    contender: asyncio.Task[None] | None = None
    try:
        await cleanup_entered.wait()
        journal_path = next((backup_root / ".transactions").glob("*.json"))
        operation.cancel()
        await _checkpoint()
        contender = asyncio.create_task(contend())
        await _checkpoint()
        assert not contender_acquired.is_set()

        release_cleanup.set()
        await finish_entered.wait()
        operation.cancel()
        await _checkpoint()
        assert not operation.done()
        assert not contender_acquired.is_set()

        release_finish.set()
        await finish_finished.wait()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await operation

        assert getattr(exc_info.value, "cleanup_code", None) == (
            "PLUGIN_DELETE_COMMITTED_CLEANUP_INCOMPLETE"
        )
        assert getattr(exc_info.value, "cleanup_errors", ()) == (
            "journal_finish:OSError",
        )
        assert journal_path.exists()
        assert journal_path.with_suffix(".owner").exists()
        assert json.loads(journal_path.read_text(encoding="utf-8"))["phase"] == "committed"
        assert inventory == {}

        await contender_acquired.wait()
        release_contender.set()
        assert contender is not None
        await contender

        recovered = module.recover_incomplete_plugin_deletions(
            journal_root=backup_root / ".transactions",
            user_root=plugin_dir.parent,
        )
        assert recovered.recovered_operation_ids == (journal_path.stem,)
        assert not journal_path.exists()
        assert not journal_path.with_suffix(".owner").exists()
        assert finish_finished.is_set()
    finally:
        release_cleanup.set()
        release_finish.set()
        release_contender.set()
        tasks = [operation]
        if contender is not None:
            tasks.append(contender)
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_delete_finish_failure_after_commit_keeps_deleted_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_id = "delete_committed_finish_failure"
    plugin_dir, source_manager, inventory, runtime, lifecycle_calls = _prepare_delete(
        monkeypatch,
        tmp_path,
        plugin_id=plugin_id,
        running=False,
    )
    backup_root = plugin_dir.parent / ".delete-backups"

    def fail_finish(_journal: object) -> None:
        raise OSError("simulated committed journal finish failure")

    monkeypatch.setattr(module._DeleteJournal, "finish", fail_finish)

    with pytest.raises(ServerDomainError) as exc_info:
        await module.PluginLifecycleService().delete_plugin(plugin_id)

    assert exc_info.value.code == "PLUGIN_DELETE_COMMITTED_CLEANUP_INCOMPLETE"
    assert exc_info.value.details["committed"] is True
    assert exc_info.value.details["cleanup_errors"] == ["journal_finish:OSError"]
    assert inventory == {}
    assert source_manager.state == {}
    assert source_manager.restore_calls == 0
    assert runtime["running"] is False
    assert lifecycle_calls == []
    assert plugin_dir.is_dir()
    assert not (plugin_dir / "plugin.toml").exists()
    assert not (plugin_dir / "code").exists()
    assert (plugin_dir / "config" / "settings.json").exists()
    assert (plugin_dir / "data" / "user.db").exists()
    assert (plugin_dir / "cache" / "index.bin").exists()
    journal_path = next((backup_root / ".transactions").glob("*.json"))
    assert json.loads(journal_path.read_text(encoding="utf-8"))["phase"] == (
        "committed"
    )
    assert journal_path.with_suffix(".owner").exists()


@pytest.mark.plugin_unit
def test_delete_recovery_restores_precommit_state_after_backup_move_began(
    tmp_path: Path,
) -> None:
    user_root = tmp_path / "user"
    plugin_id = "delete_recovery_demo"
    plugin_dir = user_root / plugin_id
    backup_root = user_root / ".delete-backups"
    backup_dir = backup_root / plugin_id
    rollback_snapshot = backup_root / f"{plugin_id}.rollback.snapshot"
    _write_plugin_tree(rollback_snapshot, plugin_id)
    expected = _snapshot_tree(rollback_snapshot)
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "data").mkdir()
    (plugin_dir / "data" / "new.db").write_bytes(b"state-only crash view")
    backup_dir.mkdir()
    (backup_dir / "code.py").write_bytes(b"staged delete payload")
    journal_root = backup_root / ".transactions"
    _write_delete_journal(
        journal_root,
        operation_id="clear-delete-precommit",
        plugin_id=plugin_id,
        phase="precommit",
        plugin_dir=plugin_dir,
        backup_dir=backup_dir,
        rollback_snapshot=rollback_snapshot,
    )

    recover = getattr(module, "recover_incomplete_plugin_deletions", None)
    assert callable(recover), "delete journal recovery API is missing"
    first = recover(journal_root=journal_root, user_root=user_root)

    assert first.recovered_operation_ids == ("clear-delete-precommit",)
    assert first.manual_recovery_operation_ids == ()
    assert _snapshot_tree(plugin_dir) == expected
    assert not backup_dir.exists()
    assert not rollback_snapshot.exists()

    second = recover(journal_root=journal_root, user_root=user_root)
    assert second.recovered_operation_ids == ()
    assert _snapshot_tree(plugin_dir) == expected


@pytest.mark.plugin_unit
def test_delete_recovery_preserves_offline_edits_when_backup_move_never_started(
    tmp_path: Path,
) -> None:
    user_root = tmp_path / "user"
    plugin_id = "delete_not_moved_demo"
    plugin_dir = user_root / plugin_id
    backup_root = user_root / ".delete-backups"
    backup_dir = backup_root / plugin_id
    rollback_snapshot = backup_root / f"{plugin_id}.rollback.snapshot"
    _write_plugin_tree(plugin_dir, plugin_id)
    shutil.copytree(plugin_dir, rollback_snapshot)
    journal_root = backup_root / ".transactions"
    journal_path = _write_delete_journal(
        journal_root,
        operation_id="delete-precommit-before-move",
        plugin_id=plugin_id,
        phase="precommit",
        plugin_dir=plugin_dir,
        backup_dir=backup_dir,
        rollback_snapshot=rollback_snapshot,
    )

    edited_source = plugin_dir / "code" / "main.py"
    edited_source.write_bytes(b"developer offline edit")
    offline_file = plugin_dir / "code" / "offline-only.py"
    offline_file.write_bytes(b"created while the host was stopped")
    expected = _snapshot_tree(plugin_dir)

    recover = getattr(module, "recover_incomplete_plugin_deletions", None)
    assert callable(recover), "delete journal recovery API is missing"
    result = recover(journal_root=journal_root, user_root=user_root)

    assert result.recovered_operation_ids == ("delete-precommit-before-move",)
    assert result.manual_recovery_operation_ids == ()
    assert not backup_dir.exists()
    assert _snapshot_tree(plugin_dir) == expected
    assert edited_source.read_bytes() == b"developer offline edit"
    assert offline_file.read_bytes() == b"created while the host was stopped"
    assert not rollback_snapshot.exists()
    assert not journal_path.exists()
    assert not journal_path.with_suffix(".owner").exists()


@pytest.mark.plugin_unit
def test_delete_recovery_cleans_snapshot_pending_without_overwriting_live_edits(
    tmp_path: Path,
) -> None:
    user_root = tmp_path / "user"
    plugin_id = "delete_snapshot_pending_demo"
    plugin_dir = user_root / plugin_id
    backup_root = user_root / ".delete-backups"
    backup_dir = backup_root / plugin_id
    rollback_snapshot = backup_root / f"{plugin_id}.rollback.snapshot"
    _write_plugin_tree(plugin_dir, plugin_id)
    shutil.copytree(plugin_dir, rollback_snapshot)
    journal_root = backup_root / ".transactions"
    journal_path = _write_delete_journal(
        journal_root,
        operation_id="delete-snapshot-pending",
        plugin_id=plugin_id,
        phase="snapshot_pending",
        plugin_dir=plugin_dir,
        backup_dir=backup_dir,
        rollback_snapshot=rollback_snapshot,
    )

    edited_source = plugin_dir / "code" / "main.py"
    edited_source.write_bytes(b"developer edit after snapshot intent")
    expected = _snapshot_tree(plugin_dir)

    result = module.recover_incomplete_plugin_deletions(
        journal_root=journal_root,
        user_root=user_root,
    )

    assert result.recovered_operation_ids == ("delete-snapshot-pending",)
    assert result.manual_recovery_operation_ids == ()
    assert _snapshot_tree(plugin_dir) == expected
    assert edited_source.read_bytes() == b"developer edit after snapshot intent"
    assert not rollback_snapshot.exists()
    assert not journal_path.exists()
    assert not journal_path.with_suffix(".owner").exists()


@pytest.mark.plugin_unit
def test_delete_recovery_blocks_ambiguous_precommit_without_live_or_backup(
    tmp_path: Path,
) -> None:
    user_root = tmp_path / "user"
    plugin_id = "delete_ambiguous_demo"
    plugin_dir = user_root / plugin_id
    backup_root = user_root / ".delete-backups"
    backup_dir = backup_root / plugin_id
    rollback_snapshot = backup_root / f"{plugin_id}.rollback.snapshot"
    _write_plugin_tree(rollback_snapshot, plugin_id)
    journal_root = backup_root / ".transactions"
    journal_path = _write_delete_journal(
        journal_root,
        operation_id="delete-precommit-ambiguous",
        plugin_id=plugin_id,
        phase="precommit",
        plugin_dir=plugin_dir,
        backup_dir=backup_dir,
        rollback_snapshot=rollback_snapshot,
    )
    snapshot_before = _snapshot_tree(rollback_snapshot)
    journal_before = journal_path.read_bytes()

    recover = getattr(module, "recover_incomplete_plugin_deletions", None)
    assert callable(recover), "delete journal recovery API is missing"
    result = recover(journal_root=journal_root, user_root=user_root)

    assert result.recovered_operation_ids == ()
    assert result.manual_recovery_operation_ids == ("delete-precommit-ambiguous",)
    assert result.manual_recovery_plugin_ids == (plugin_id,)
    assert not plugin_dir.exists()
    assert not backup_dir.exists()
    assert _snapshot_tree(rollback_snapshot) == snapshot_before
    assert journal_path.read_bytes() == journal_before
    assert journal_path.with_suffix(".owner").exists()


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("phase", "schema_version"),
    (("commit_started", 1), ("precommit", 99)),
)
def test_delete_recovery_keeps_ambiguous_or_future_state_for_manual_action(
    tmp_path: Path,
    phase: str,
    schema_version: int,
) -> None:
    user_root = tmp_path / "user"
    plugin_id = "delete_manual_demo"
    plugin_dir = user_root / plugin_id
    backup_root = user_root / ".delete-backups"
    backup_dir = backup_root / plugin_id
    rollback_snapshot = backup_root / f"{plugin_id}.rollback.snapshot"
    _write_plugin_tree(plugin_dir, plugin_id)
    _write_plugin_tree(rollback_snapshot, plugin_id)
    backup_dir.mkdir()
    (backup_dir / "payload.py").write_bytes(b"backup")
    journal_root = backup_root / ".transactions"
    journal_path = _write_delete_journal(
        journal_root,
        operation_id=f"manual-{phase}-{schema_version}",
        plugin_id=plugin_id,
        phase=phase,
        plugin_dir=plugin_dir,
        backup_dir=backup_dir,
        rollback_snapshot=rollback_snapshot,
        schema_version=schema_version,
    )
    before = {
        "plugin": _snapshot_tree(plugin_dir),
        "backup": _snapshot_tree(backup_dir),
        "snapshot": _snapshot_tree(rollback_snapshot),
        "journal": journal_path.read_bytes(),
    }

    recover = getattr(module, "recover_incomplete_plugin_deletions", None)
    assert callable(recover), "delete journal recovery API is missing"
    result = recover(journal_root=journal_root, user_root=user_root)

    assert result.recovered_operation_ids == ()
    assert result.manual_recovery_plugin_ids == (plugin_id,)
    assert _snapshot_tree(plugin_dir) == before["plugin"]
    assert _snapshot_tree(backup_dir) == before["backup"]
    assert _snapshot_tree(rollback_snapshot) == before["snapshot"]
    assert journal_path.read_bytes() == before["journal"]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_registry_blocks_delete_commit_started_before_scanning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin import settings

    plugin_id = "delete_registry_gate"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    for root in (builtin_root, user_root, packages_root, profiles_root):
        root.mkdir()
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "MANAGED_PLUGIN_INSTALLATIONS_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    plugin_dir = user_root / plugin_id
    backup_root = user_root / ".delete-backups"
    backup_dir = backup_root / plugin_id
    rollback_snapshot = backup_root / f"{plugin_id}.rollback.snapshot"
    _write_plugin_tree(plugin_dir, plugin_id)
    _write_plugin_tree(rollback_snapshot, plugin_id)
    backup_dir.mkdir()
    _write_delete_journal(
        backup_root / ".transactions",
        operation_id="delete-registry-commit",
        plugin_id=plugin_id,
        phase="commit_started",
        plugin_dir=plugin_dir,
        backup_dir=backup_dir,
        rollback_snapshot=rollback_snapshot,
    )
    observed: list[frozenset[str]] = []
    service = PluginRegistryService()

    def scan(
        only_plugin_id: str | None = None,
        *,
        blocked_recovery_plugin_ids: frozenset[str],
        block_user_plugin_root: bool = False,
    ) -> dict[str, object]:
        assert only_plugin_id is None
        assert block_user_plugin_root is False
        observed.append(blocked_recovery_plugin_ids)
        return {"success": False}

    monkeypatch.setattr(service, "_refresh_registry_sync", scan)

    result = await service.refresh_registry()

    assert result == {"success": False}
    assert observed == [frozenset({plugin_id})]
