from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import threading

import pytest

from plugin.core.plugin_layout import resolve_plugin_layout
from plugin.neko_plugin_cli.public import pack_plugin
from plugin.server.application.plugin_cli import service as service_module
from plugin.server.application.plugin_cli.service import PluginCliService
from plugin.server.application.plugins import mutation_guard, upgrade_support
from plugin.server.application.plugins.mutation_guard import plugin_mutation_guard
from plugin.server.application.plugins.registry_service import PluginRegistryService
from plugin.server.domain.errors import ServerDomainError


pytestmark = pytest.mark.plugin_unit


def test_replacement_journal_syncs_parent_after_owner_and_state_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "plugins" / "demo"
    backup = tmp_path / "plugins" / ".upgrade-backups" / "demo.bak"
    synced_paths: list[Path] = []
    monkeypatch.setattr(
        upgrade_support,
        "fsync_parent_directory",
        lambda path: synced_paths.append(path),
        raising=False,
    )

    journal = upgrade_support._ReplacementJournal.create(
        plugin_id="demo",
        journal_root=tmp_path / "plugins" / ".upgrade-backups" / ".transactions",
        targets=(target,),
        backups={target: backup},
        preexisting_targets=frozenset({target}),
    )

    assert synced_paths == [journal.owner_path, journal.path]


async def _async_none() -> None:
    return None


async def _async_false() -> bool:
    return False


async def _event_loop_checkpoint() -> None:
    checkpoint = asyncio.Event()
    asyncio.get_running_loop().call_soon(checkpoint.set)
    await checkpoint.wait()


def _make_plugin(
    root: Path,
    *,
    plugin_id: str,
    version: str,
    implementation: bytes,
) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            (
                "[plugin]",
                f'id = "{plugin_id}"',
                f'name = "{plugin_id}"',
                f'version = "{version}"',
                'type = "plugin"',
                f'entry = "{plugin_id}:Plugin"',
                "",
            )
        ),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_bytes(implementation)
    return plugin_dir


@pytest.mark.asyncio
async def test_cancelled_package_snapshot_finishes_and_is_removed_before_guard_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.neko-plugin"
    source.write_bytes(b"source-package")
    snapshot = tmp_path / "snapshot.neko-plugin"
    worker_entered = asyncio.Event()
    worker_finished = asyncio.Event()
    release_worker = threading.Event()
    contender_acquired = asyncio.Event()
    release_contender = asyncio.Event()
    loop = asyncio.get_running_loop()
    service = PluginCliService()

    def make_snapshot(_package_path: Path) -> Path:
        loop.call_soon_threadsafe(worker_entered.set)
        release_worker.wait()
        snapshot.write_bytes(b"snapshot-written-after-cancel")
        loop.call_soon_threadsafe(worker_finished.set)
        return snapshot

    monkeypatch.setattr(service, "_snapshot_package_for_operation", make_snapshot)

    async def run_snapshot() -> Path:
        async with plugin_mutation_guard():
            return await service._snapshot_package_mutation(source)

    async def contend_for_guard() -> None:
        async with plugin_mutation_guard():
            contender_acquired.set()
            await release_contender.wait()

    operation_task = asyncio.create_task(run_snapshot())
    contender_task: asyncio.Task[None] | None = None
    try:
        await worker_entered.wait()
        operation_task.cancel()
        await _event_loop_checkpoint()
        contender_task = asyncio.create_task(contend_for_guard())
        await _event_loop_checkpoint()

        assert not operation_task.done()
        assert not contender_acquired.is_set()

        operation_task.cancel()
        await _event_loop_checkpoint()
        assert not operation_task.done()
        assert not contender_acquired.is_set()

        release_worker.set()
        await worker_finished.wait()
        with pytest.raises(asyncio.CancelledError):
            await operation_task

        assert not snapshot.exists()
        await contender_acquired.wait()
        release_contender.set()
        await contender_task
        async with plugin_mutation_guard():
            pass
    finally:
        release_worker.set()
        release_contender.set()
        tasks = [operation_task]
        if contender_task is not None:
            tasks.append(contender_task)
        await asyncio.gather(*tasks, return_exceptions=True)


def _patch_plugin_roots(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path: Path,
    user_root: Path,
    packages_root: Path,
) -> None:
    from plugin import settings

    builtin_root = tmp_path / "builtin"
    profiles_root = tmp_path / "profiles"
    builtin_root.mkdir()
    packages_root.mkdir(exist_ok=True)
    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "MANAGED_PLUGIN_INSTALLATIONS_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setenv(
        "NEKO_PLUGIN_INSTALLATIONS_PATH",
        str(tmp_path / "plugin-installations.json"),
    )
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "runtime"))


@pytest.mark.asyncio
async def test_committed_replacement_cleanup_cancellation_keeps_new_version_and_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation during final cleanup must never reopen the rollback window."""

    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_bytes(b"old-version\n")
    cleanup_deleted = asyncio.Event()
    allow_cleanup_finish = threading.Event()
    committed = asyncio.Event()
    early_guard_releases: list[bool] = []
    loop = asyncio.get_running_loop()

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_bytes(b"new-version\n")
        return {"installed": True}

    async def commit(result: dict[str, object]) -> dict[str, object]:
        committed.set()
        return result

    def blocking_cleanup(backup: Path) -> None:
        shutil.rmtree(backup)
        loop.call_soon_threadsafe(cleanup_deleted.set)
        assert allow_cleanup_finish.wait(timeout=10)

    async def cleanup_backup(backup: Path) -> None:
        await asyncio.to_thread(blocking_cleanup, backup)

    original_cancellation_safe = upgrade_support.await_cancellation_safe

    async def observed_cancellation_safe(operation):  # type: ignore[no-untyped-def]
        # The corrected implementation waits for the already-running cleanup
        # task. The old implementation reaches this helper only with a newly
        # created rollback coroutine after the backup has already disappeared.
        if isinstance(operation, asyncio.Task):
            allow_cleanup_finish.set()
        return await original_cancellation_safe(operation)

    original_release = mutation_guard._MUTATION_LOCK.release

    def observed_release() -> None:
        released_before_cleanup_finished = not allow_cleanup_finish.is_set()
        early_guard_releases.append(released_before_cleanup_finished)
        # Let the old implementation terminate instead of leaking its worker
        # when this assertion exposes the early release.
        allow_cleanup_finish.set()
        original_release()

    monkeypatch.setattr(
        upgrade_support,
        "await_cancellation_safe",
        observed_cancellation_safe,
    )
    monkeypatch.setattr(mutation_guard._MUTATION_LOCK, "release", observed_release)

    async def replace_under_guard() -> None:
        async with plugin_mutation_guard():
            await upgrade_support.replace_plugin(
                layout=resolve_plugin_layout("demo", target),
                install_new=install_new,
                validate_new=_async_none,
                is_running=lambda _plugin_id: _async_false(),
                stop=lambda _plugin_id: _async_none(),
                start=lambda _plugin_id: _async_none(),
                cleanup_backup=cleanup_backup,
                commit=commit,
            )

    operation = asyncio.create_task(replace_under_guard())
    await cleanup_deleted.wait()
    assert committed.is_set()

    operation.cancel()
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation

    assert early_guard_releases == [False]
    assert (target / "plugin.toml").read_bytes() == b"new-version\n"
    assert not any((target.parent / ".upgrade-backups").glob("*"))

    async with plugin_mutation_guard():
        pass


@pytest.mark.asyncio
async def test_committed_replacement_finish_failure_keeps_new_version_and_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A final journal failure must not reopen an already-closed rollback window."""

    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_bytes(b"old-version\n")

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_bytes(b"new-version\n")
        return {"installed": True}

    def fail_finish(_journal: object) -> None:
        raise OSError("simulated committed journal finish failure")

    monkeypatch.setattr(upgrade_support._ReplacementJournal, "finish", fail_finish)

    with pytest.raises(upgrade_support.ReplacePluginError) as exc_info:
        await upgrade_support.replace_plugin(
            layout=resolve_plugin_layout("demo", target),
            install_new=install_new,
            validate_new=_async_none,
            is_running=lambda _plugin_id: _async_false(),
            stop=lambda _plugin_id: _async_none(),
            start=lambda _plugin_id: _async_none(),
            cleanup_backup=upgrade_support.remove_directory,
        )

    assert exc_info.value.committed is True
    assert exc_info.value.stage == "cleanup"
    assert exc_info.value.rollback_status == "not_needed"
    assert (target / "plugin.toml").read_bytes() == b"new-version\n"
    backup_root = target.parent / ".upgrade-backups"
    assert not any(
        item for item in backup_root.iterdir() if item.name != ".transactions"
    )
    journal_path = next((backup_root / ".transactions").glob("*.json"))
    assert json.loads(journal_path.read_text(encoding="utf-8"))["phase"] == (
        "committed"
    )
    assert journal_path.with_suffix(".owner").exists()


@pytest.mark.asyncio
async def test_cancelled_local_upgrade_waits_for_install_worker_before_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "cancelled_upgrade_worker"
    user_root = tmp_path / "user-plugins"
    packages_root = tmp_path / "packages"
    old_source = _make_plugin(
        tmp_path / "old-source",
        plugin_id=plugin_id,
        version="1.0.0",
        implementation=b"old implementation\n",
    )
    new_source = _make_plugin(
        tmp_path / "new-source",
        plugin_id=plugin_id,
        version="2.0.0",
        implementation=b"new implementation\n",
    )
    target = user_root / plugin_id
    shutil.copytree(old_source, target)
    _patch_plugin_roots(
        monkeypatch,
        tmp_path=tmp_path,
        user_root=user_root,
        packages_root=packages_root,
    )
    package = packages_root / f"{plugin_id}.neko-plugin"
    pack_plugin(new_source, package)
    service = PluginCliService()
    confirmed_plan = await service.plan_install(package=str(package))

    worker_entered = asyncio.Event()
    worker_finished = asyncio.Event()
    release_worker = threading.Event()
    contender_acquired = asyncio.Event()
    release_contender = asyncio.Event()
    loop = asyncio.get_running_loop()

    def install_sync(**kwargs: object) -> dict[str, object]:
        loop.call_soon_threadsafe(worker_entered.set)
        release_worker.wait()
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(new_source / "plugin.toml", target / "plugin.toml")
        (target / "__init__.py").write_bytes(b"late new implementation\n")
        loop.call_soon_threadsafe(worker_finished.set)
        return {
            "package_path": str(kwargs["package"]),
            "package_type": "plugin",
            "package_id": plugin_id,
            "installed_plugins": [
                {
                    "source_folder": plugin_id,
                    "target_plugin_id": plugin_id,
                    "target_dir": str(target),
                    "renamed": False,
                }
            ],
            "installed_plugin_count": 1,
            "profile_dir": None,
            "operation": "install",
        }

    monkeypatch.setattr(service, "_install_sync", install_sync)

    async def run_upgrade() -> dict[str, object]:
        return await service.install(
            package=str(package),
            confirm_upgrade=True,
            confirmation_token=str(confirmed_plan["confirmation_token"]),
            activate_installation=False,
        )

    async def contend() -> None:
        async with plugin_mutation_guard():
            contender_acquired.set()
            await release_contender.wait()

    operation = asyncio.create_task(run_upgrade())
    contender: asyncio.Task[None] | None = None
    try:
        await worker_entered.wait()
        operation.cancel()
        await _event_loop_checkpoint()
        contender = asyncio.create_task(contend())
        await _event_loop_checkpoint()

        assert not operation.done()
        assert not contender_acquired.is_set()

        operation.cancel()
        await _event_loop_checkpoint()
        assert not operation.done()
        assert not contender_acquired.is_set()

        release_worker.set()
        await worker_finished.wait()
        with pytest.raises(asyncio.CancelledError):
            await operation

        assert (target / "__init__.py").read_bytes() == b"old implementation\n"
        assert not any((user_root / ".upgrade-backups").glob("*"))
        await contender_acquired.wait()
        release_contender.set()
        await contender
    finally:
        release_worker.set()
        release_contender.set()
        tasks = [operation]
        if contender is not None:
            tasks.append(contender)
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_runtime_config_write_finishes_before_guard_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "plugins" / "runtime_config_cancel"
    _make_plugin(
        target.parent,
        plugin_id=target.name,
        version="1.0.0",
        implementation=b"old implementation\n",
    )
    worker_entered = asyncio.Event()
    worker_finished = asyncio.Event()
    release_worker = threading.Event()
    contender_acquired = asyncio.Event()
    release_contender = asyncio.Event()
    loop = asyncio.get_running_loop()
    install_calls = 0

    def runtime_config_write(_layout: object) -> None:
        loop.call_soon_threadsafe(worker_entered.set)
        release_worker.wait()
        (target / "runtime-config-finished").write_bytes(b"finished")
        loop.call_soon_threadsafe(worker_finished.set)

    async def install_new() -> dict[str, object]:
        nonlocal install_calls
        install_calls += 1
        return {}

    monkeypatch.setattr(
        upgrade_support,
        "ensure_plugin_layout_runtime_config",
        runtime_config_write,
    )

    async def replace_under_guard() -> None:
        async with plugin_mutation_guard():
            await upgrade_support.replace_plugin(
                layout=resolve_plugin_layout(target.name, target),
                install_new=install_new,
                validate_new=_async_none,
                is_running=lambda _plugin_id: _async_false(),
                stop=lambda _plugin_id: _async_none(),
                start=lambda _plugin_id: _async_none(),
                cleanup_backup=upgrade_support.remove_directory,
            )

    async def contend() -> None:
        async with plugin_mutation_guard():
            contender_acquired.set()
            await release_contender.wait()

    operation = asyncio.create_task(replace_under_guard())
    contender: asyncio.Task[None] | None = None
    try:
        await worker_entered.wait()
        operation.cancel()
        await _event_loop_checkpoint()
        contender = asyncio.create_task(contend())
        await _event_loop_checkpoint()
        assert not operation.done()
        assert not contender_acquired.is_set()

        operation.cancel()
        await _event_loop_checkpoint()
        assert not operation.done()
        assert not contender_acquired.is_set()

        release_worker.set()
        await worker_finished.wait()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert install_calls == 0
        assert (target / "runtime-config-finished").read_bytes() == b"finished"
        await contender_acquired.wait()
        release_contender.set()
        await contender
    finally:
        release_worker.set()
        release_contender.set()
        tasks = [operation]
        if contender is not None:
            tasks.append(contender)
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("restore_kind", ("source", "inventory"))
async def test_cancel_during_failed_upgrade_metadata_rollback_finishes_before_unlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_kind: str,
) -> None:
    plugin_id = f"rollback_cancel_{restore_kind}"
    user_root = tmp_path / "user-plugins"
    packages_root = tmp_path / "packages"
    old_source = _make_plugin(
        tmp_path / "old-source",
        plugin_id=plugin_id,
        version="1.0.0",
        implementation=b"old implementation\n",
    )
    new_source = _make_plugin(
        tmp_path / "new-source",
        plugin_id=plugin_id,
        version="2.0.0",
        implementation=b"new implementation\n",
    )
    target = user_root / plugin_id
    shutil.copytree(old_source, target)
    _patch_plugin_roots(
        monkeypatch,
        tmp_path=tmp_path,
        user_root=user_root,
        packages_root=packages_root,
    )
    package = packages_root / f"{plugin_id}.neko-plugin"
    pack_plugin(new_source, package)

    rollback_entered = asyncio.Event()
    rollback_finished = asyncio.Event()
    release_rollback = threading.Event()
    contender_acquired = asyncio.Event()
    release_contender = asyncio.Event()
    loop = asyncio.get_running_loop()

    class SourceManager:
        def snapshot(self) -> dict[str, str]:
            return {"source": "before"}

        def package_id_for_directory(self, _target: Path) -> str:
            return ""

        def restore_snapshot_for_rollback(self, _snapshot: object) -> None:
            if restore_kind == "source":
                loop.call_soon_threadsafe(rollback_entered.set)
                release_rollback.wait()
                loop.call_soon_threadsafe(rollback_finished.set)

    def restore_inventory(_snapshot: object) -> None:
        if restore_kind == "inventory":
            loop.call_soon_threadsafe(rollback_entered.set)
            release_rollback.wait()
            loop.call_soon_threadsafe(rollback_finished.set)

    monkeypatch.setattr(
        service_module,
        "get_install_source_manager",
        lambda: SourceManager(),
    )
    monkeypatch.setattr(service_module, "capture_inventory_snapshot", lambda: {})
    monkeypatch.setattr(service_module, "restore_inventory_snapshot", restore_inventory)

    service = PluginCliService()
    confirmed_plan = await service.plan_install(package=str(package))

    def fail_install(**_kwargs: object) -> dict[str, object]:
        raise OSError("injected install failure")

    monkeypatch.setattr(service, "_install_sync", fail_install)

    async def run_upgrade() -> dict[str, object]:
        return await service.install(
            package=str(package),
            install_source="imported",
            confirm_upgrade=True,
            confirmation_token=str(confirmed_plan["confirmation_token"]),
            activate_installation=True,
        )

    async def contend() -> None:
        async with plugin_mutation_guard():
            contender_acquired.set()
            await release_contender.wait()

    operation = asyncio.create_task(run_upgrade())
    contender: asyncio.Task[None] | None = None
    try:
        await rollback_entered.wait()
        operation.cancel()
        await _event_loop_checkpoint()
        contender = asyncio.create_task(contend())
        await _event_loop_checkpoint()

        assert not operation.done()
        assert not contender_acquired.is_set()

        operation.cancel()
        await _event_loop_checkpoint()
        assert not operation.done()
        assert not contender_acquired.is_set()

        release_rollback.set()
        await rollback_finished.wait()
        with pytest.raises(asyncio.CancelledError):
            await operation

        assert (target / "__init__.py").read_bytes() == b"old implementation\n"
        assert not any((user_root / ".upgrade-backups").glob("*"))
        await contender_acquired.wait()
        release_contender.set()
        await contender
    finally:
        release_rollback.set()
        release_contender.set()
        tasks = [operation]
        if contender is not None:
            tasks.append(contender)
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_input", ("target", "package"))
async def test_upgrade_revalidates_confirmed_bytes_after_backup_before_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_input: str,
) -> None:
    """The bytes used for replacement must still match the confirmed plan."""

    plugin_id = f"confirmed_{changed_input}_demo"
    user_root = tmp_path / "user-plugins"
    packages_root = tmp_path / "packages"
    old_source = _make_plugin(
        tmp_path / "old-source",
        plugin_id=plugin_id,
        version="1.0.0",
        implementation=b"old implementation\n",
    )
    new_source = _make_plugin(
        tmp_path / "new-source",
        plugin_id=plugin_id,
        version="2.0.0",
        implementation=b"confirmed implementation\n",
    )
    changed_package_source = _make_plugin(
        tmp_path / "changed-package-source",
        plugin_id=plugin_id,
        version="2.0.0",
        implementation=b"changed package implementation\n",
    )
    target = user_root / plugin_id
    shutil.copytree(old_source, target)
    _patch_plugin_roots(
        monkeypatch,
        tmp_path=tmp_path,
        user_root=user_root,
        packages_root=packages_root,
    )
    package = packages_root / f"{plugin_id}.neko-plugin"
    changed_package = packages_root / f"{plugin_id}-changed.neko-plugin"
    pack_plugin(new_source, package)
    pack_plugin(changed_package_source, changed_package)

    service = PluginCliService()
    confirmed_plan = await service.plan_install(package=str(package))
    assert confirmed_plan["action"] == "upgrade"

    replacement_reached = asyncio.Event()
    release_replacement = asyncio.Event()
    release_package_snapshot = threading.Event()
    running_probe_calls = 0

    async def block_after_second_plan(_plugin_id: str) -> bool:
        nonlocal running_probe_calls
        running_probe_calls += 1
        if changed_input == "target" and running_probe_calls == 1:
            replacement_reached.set()
            await release_replacement.wait()
        return False

    monkeypatch.setattr(
        upgrade_support,
        "plugin_is_running",
        block_after_second_plan,
    )
    if changed_input == "package":
        original_snapshot_package = service._snapshot_package_for_operation
        loop = asyncio.get_running_loop()

        def block_package_snapshot(source: Path) -> Path:
            loop.call_soon_threadsafe(replacement_reached.set)
            assert release_package_snapshot.wait(timeout=10)
            return original_snapshot_package(source)

        monkeypatch.setattr(
            service,
            "_snapshot_package_for_operation",
            block_package_snapshot,
        )
    original_install_sync = service._install_sync
    install_calls = 0

    def observed_install_sync(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal install_calls
        install_calls += 1
        return original_install_sync(**kwargs)

    monkeypatch.setattr(service, "_install_sync", observed_install_sync)
    operation = asyncio.create_task(
        service.install(
            package=str(package),
            confirm_upgrade=True,
            confirmation_token=str(confirmed_plan["confirmation_token"]),
            activate_installation=False,
        )
    )
    await replacement_reached.wait()

    if changed_input == "target":
        expected_target_bytes = b"developer edit after confirmation\n"
        (target / "__init__.py").write_bytes(expected_target_bytes)
        release_replacement.set()
    else:
        expected_target_bytes = b"old implementation\n"
        shutil.copyfile(changed_package, package)
        release_package_snapshot.set()

    with pytest.raises(ServerDomainError) as exc_info:
        await operation

    assert exc_info.value.code == "PLUGIN_UPGRADE_PLAN_CHANGED"
    assert install_calls == 0
    assert (target / "__init__.py").read_bytes() == expected_target_bytes
    assert not any((user_root / ".upgrade-backups").glob("*"))


def _write_replacement_journal(
    journal_root: Path,
    *,
    schema_version: int,
    operation_id: str,
    phase: str,
    target: Path,
    backup: Path,
) -> Path:
    journal_root.mkdir(parents=True, exist_ok=True)
    journal_path = journal_root / f"{operation_id}.json"
    journal_path.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "operation_id": operation_id,
                "plugin_id": "journal_demo",
                "phase": phase,
                "targets": [
                    {
                        "target": str(target),
                        "backup": str(backup),
                        "preexisting": True,
                        "moved": True,
                    }
                ],
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
                "plugin_id": "journal_demo",
                "kind": "replacement",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return journal_path


def test_recovery_restores_clear_precommit_journal_idempotently(
    tmp_path: Path,
) -> None:
    target = tmp_path / "plugins" / "journal_demo"
    backup = tmp_path / "plugins" / ".upgrade-backups" / "journal_demo.bak.20260816"
    backup.mkdir(parents=True)
    (backup / "plugin.toml").write_bytes(b"old version\n")
    journal_root = tmp_path / "journals"
    _write_replacement_journal(
        journal_root,
        schema_version=1,
        operation_id="clear-precommit",
        phase="precommit",
        target=target,
        backup=backup,
    )

    recovery = getattr(
        upgrade_support,
        "recover_incomplete_plugin_replacements",
        None,
    )
    assert callable(recovery), "replacement journal recovery API is missing"
    first = recovery(journal_root=journal_root)

    assert first.recovered_operation_ids == ("clear-precommit",)
    assert first.manual_recovery_operation_ids == ()
    assert (target / "plugin.toml").read_bytes() == b"old version\n"
    assert not backup.exists()

    second = recovery(journal_root=journal_root)
    assert second.recovered_operation_ids == ()
    assert second.manual_recovery_operation_ids == ()
    assert (target / "plugin.toml").read_bytes() == b"old version\n"


@pytest.mark.parametrize(
    ("schema_version", "phase", "target_exists"),
    (
        (1, "precommit", True),
        (1, "commit_started", False),
        (99, "precommit", False),
    ),
)
def test_recovery_never_overwrites_ambiguous_or_future_journal(
    tmp_path: Path,
    schema_version: int,
    phase: str,
    target_exists: bool,
) -> None:
    operation_id = f"manual-{schema_version}-{phase}-{target_exists}"
    target = tmp_path / "plugins" / "journal_demo"
    backup = tmp_path / "plugins" / ".upgrade-backups" / "journal_demo.bak.20260816"
    backup.mkdir(parents=True)
    (backup / "plugin.toml").write_bytes(b"old version\n")
    if target_exists:
        target.mkdir(parents=True)
        (target / "plugin.toml").write_bytes(b"new or external version\n")
    journal_root = tmp_path / "journals"
    journal_path = _write_replacement_journal(
        journal_root,
        schema_version=schema_version,
        operation_id=operation_id,
        phase=phase,
        target=target,
        backup=backup,
    )
    journal_before = journal_path.read_bytes()

    recovery = getattr(
        upgrade_support,
        "recover_incomplete_plugin_replacements",
        None,
    )
    assert callable(recovery), "replacement journal recovery API is missing"
    result = recovery(journal_root=journal_root)

    assert result.recovered_operation_ids == ()
    assert result.manual_recovery_operation_ids == (operation_id,)
    assert (backup / "plugin.toml").read_bytes() == b"old version\n"
    if target_exists:
        assert (target / "plugin.toml").read_bytes() == b"new or external version\n"
    else:
        assert not target.exists()
    assert journal_path.read_bytes() == journal_before


@pytest.mark.parametrize(
    "scenario",
    (
        "truncated",
        "missing_owner",
        "operation_id_mismatch",
        "missing_plugin_id",
        "outside_root",
    ),
)
def test_untrusted_replacement_journal_blocks_user_root_without_mutation(
    tmp_path: Path,
    scenario: str,
) -> None:
    target = tmp_path / "plugins" / "journal_demo"
    backup = tmp_path / "plugins" / ".upgrade-backups" / "journal_demo.bak.test"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_bytes(b"current target\n")
    backup.mkdir(parents=True)
    (backup / "plugin.toml").write_bytes(b"old backup\n")
    journal_root = tmp_path / "journals"
    journal_path = _write_replacement_journal(
        journal_root,
        schema_version=1,
        operation_id="trusted-operation",
        phase="precommit",
        target=target,
        backup=backup,
    )
    if scenario == "truncated":
        journal_path.write_bytes(b'{"schema_version":1')
    elif scenario == "missing_owner":
        journal_path.with_suffix(".owner").unlink()
    else:
        payload = json.loads(journal_path.read_text(encoding="utf-8"))
        if scenario == "operation_id_mismatch":
            payload["operation_id"] = "different-operation"
        elif scenario == "missing_plugin_id":
            payload.pop("plugin_id")
        else:
            outside_target = tmp_path / "outside" / "journal_demo"
            outside_target.mkdir(parents=True)
            (outside_target / "plugin.toml").write_bytes(b"outside target\n")
            payload["targets"][0]["target"] = str(outside_target)
        journal_path.write_text(json.dumps(payload), encoding="utf-8")
    journal_before = journal_path.read_bytes()
    target_before = (target / "plugin.toml").read_bytes()
    backup_before = (backup / "plugin.toml").read_bytes()

    result = upgrade_support.recover_incomplete_plugin_replacements(
        journal_root=journal_root,
        allowed_roots=(tmp_path / "plugins",),
    )

    assert result.recovered_operation_ids == ()
    assert result.manual_recovery_operation_ids
    assert result.block_user_plugin_root is (scenario == "missing_owner")
    if scenario != "missing_owner":
        assert result.manual_recovery_plugin_ids == ("journal_demo",)
    assert journal_path.read_bytes() == journal_before
    assert (target / "plugin.toml").read_bytes() == target_before
    assert (backup / "plugin.toml").read_bytes() == backup_before


def test_new_target_claim_is_never_used_to_delete_an_existing_directory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "plugins" / "journal_demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_bytes(b"external or completed target\n")
    backup = tmp_path / "plugins" / ".upgrade-backups" / "journal_demo.bak.test"
    journal_root = tmp_path / "journals"
    journal_path = _write_replacement_journal(
        journal_root,
        schema_version=1,
        operation_id="new-target-must-not-delete",
        phase="precommit",
        target=target,
        backup=backup,
    )
    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    payload["targets"][0]["preexisting"] = False
    payload["targets"][0]["moved"] = False
    journal_path.write_text(json.dumps(payload), encoding="utf-8")

    result = upgrade_support.recover_incomplete_plugin_replacements(
        journal_root=journal_root,
        allowed_roots=(tmp_path / "plugins",),
    )

    assert result.recovered_operation_ids == ()
    assert result.manual_recovery_plugin_ids == ("journal_demo",)
    assert (target / "plugin.toml").read_bytes() == b"external or completed target\n"


@pytest.mark.asyncio
async def test_registry_blocks_all_user_candidates_for_unidentified_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_root = tmp_path / "plugins"
    packages_root = tmp_path / "packages"
    user_root.mkdir()
    _patch_plugin_roots(
        monkeypatch,
        tmp_path=tmp_path,
        user_root=user_root,
        packages_root=packages_root,
    )
    observed: list[bool] = []
    service = PluginRegistryService()

    async def recover():
        return upgrade_support.ReplacementRecoveryResult(
            (),
            ("unknown",),
            (),
            block_user_plugin_root=True,
        )

    def scan(
        only_plugin_id: str | None = None,
        *,
        blocked_recovery_plugin_ids: frozenset[str],
        block_user_plugin_root: bool = False,
    ) -> dict[str, object]:
        assert only_plugin_id is None
        assert blocked_recovery_plugin_ids == frozenset()
        observed.append(block_user_plugin_root)
        return {"success": False}

    monkeypatch.setattr(service, "_recover_incomplete_replacements", recover)
    monkeypatch.setattr(service, "_refresh_registry_sync", scan)

    result = await service.refresh_registry()

    assert result == {"success": False}
    assert observed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "expected_blocked"),
    (("precommit", frozenset()), ("commit_started", frozenset({"journal_demo"}))),
)
@pytest.mark.parametrize("entrypoint", ("registry", "plugin"))
async def test_registry_recovers_or_blocks_replacement_before_scanning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    expected_blocked: frozenset[str],
    entrypoint: str,
) -> None:
    user_root = tmp_path / "plugins"
    packages_root = tmp_path / "packages"
    user_root.mkdir()
    _patch_plugin_roots(
        monkeypatch,
        tmp_path=tmp_path,
        user_root=user_root,
        packages_root=packages_root,
    )
    target = user_root / "journal_demo"
    backup = user_root / ".upgrade-backups" / "journal_demo.bak.20260816"
    backup.mkdir(parents=True)
    (backup / "plugin.toml").write_bytes(b"old version\n")
    journal_root = user_root / ".upgrade-backups" / ".transactions"
    _write_replacement_journal(
        journal_root,
        schema_version=1,
        operation_id=f"registry-{phase}",
        phase=phase,
        target=target,
        backup=backup,
    )

    observed: list[frozenset[str]] = []
    service = PluginRegistryService()

    def scan(
        only_plugin_id: str | None = None,
        *,
        blocked_recovery_plugin_ids: frozenset[str],
        block_user_plugin_root: bool = False,
    ) -> dict[str, object]:
        observed.append(blocked_recovery_plugin_ids)
        assert block_user_plugin_root is False
        assert only_plugin_id == ("journal_demo" if entrypoint == "plugin" else None)
        if phase == "precommit":
            assert (target / "plugin.toml").read_bytes() == b"old version\n"
        return {"success": True}

    monkeypatch.setattr(service, "_refresh_registry_sync", scan)
    if entrypoint == "plugin":
        monkeypatch.setattr(
            service,
            "_refresh_plugin_sync",
            lambda plugin_id, *, blocked_recovery_plugin_ids, block_user_plugin_root=False: scan(
                plugin_id,
                blocked_recovery_plugin_ids=blocked_recovery_plugin_ids,
                block_user_plugin_root=block_user_plugin_root,
            ),
        )
        result = await service.refresh_plugin("journal_demo")
    else:
        result = await service.refresh_registry()

    assert result == {"success": True}
    assert observed == [expected_blocked]
