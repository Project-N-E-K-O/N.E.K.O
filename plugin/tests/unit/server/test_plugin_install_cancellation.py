from __future__ import annotations

import asyncio
import hashlib
import io
import threading
from pathlib import Path
from typing import Literal

import pytest

import plugin.settings as plugin_settings
from plugin.server.application.install_source import (
    InstallSourceManager,
    PluginDirectoryScanner,
    set_global_manager,
)
from plugin.server.application.plugin_cli.service import PluginCliService
from plugin.server.application.plugins.inventory_store import get_inventory_resolution
from plugin.server.application.plugins.mutation_guard import plugin_mutation_guard


pytestmark = pytest.mark.plugin_unit


def _patch_roots(
    monkeypatch: pytest.MonkeyPatch,
    *,
    builtin_root: Path,
    user_root: Path,
    packages_root: Path,
    profiles_root: Path,
) -> None:
    monkeypatch.setattr(
        plugin_settings,
        "BUILTIN_PLUGIN_CONFIG_ROOT",
        builtin_root,
    )
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(
        plugin_settings, "MANAGED_PLUGIN_INSTALLATIONS_ROOT", user_root
    )
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(
        plugin_settings,
        "USER_PACKAGE_PROFILES_ROOT",
        profiles_root,
    )


def _file_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_cancelled_install_rollback_exposes_stable_incomplete_diagnostics() -> None:
    cancellation = asyncio.CancelledError()

    PluginCliService._annotate_cancelled_rollback(
        cancellation,
        ["payload_cleanup_incomplete", "inventory_restore:OSError"],
    )

    assert cancellation.rollback_code == "PLUGIN_INSTALL_ROLLBACK_INCOMPLETE"
    assert cancellation.recovery_errors == (
        "payload_cleanup_incomplete",
        "inventory_restore:OSError",
    )


async def _event_loop_checkpoint() -> None:
    checkpoint = asyncio.Event()
    asyncio.get_running_loop().call_soon(checkpoint.set)
    await checkpoint.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["install", "upload", "market"])
async def test_cancelled_install_waits_for_file_worker_and_rolls_back_before_guard_release(
    operation: Literal["install", "upload", "market"],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_id = f"cancel_{operation}_demo"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    inventory_path = tmp_path / "plugin-installations.json"
    lock_path = tmp_path / "plugins.lock.json"
    for root in (builtin_root, user_root, packages_root, profiles_root):
        root.mkdir(parents=True)
    _patch_roots(
        monkeypatch,
        builtin_root=builtin_root,
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setenv("NEKO_PLUGIN_INSTALLATIONS_PATH", str(inventory_path))

    source_package = packages_root / f"{plugin_id}.neko-plugin"
    if operation == "install":
        source_package.write_bytes(b"direct-install-package")
    packages_before = _file_snapshot(packages_root)

    source_manager = InstallSourceManager(
        lock_path=lock_path,
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    source_manager.load()
    set_global_manager(source_manager)

    service = PluginCliService()
    worker_entered = asyncio.Event()
    worker_finished = asyncio.Event()
    release_worker = threading.Event()
    contender_attempted = asyncio.Event()
    contender_acquired = asyncio.Event()
    release_contender = asyncio.Event()
    target_dir = user_root / plugin_id
    loop = asyncio.get_running_loop()

    async def plan_install(**_kwargs: object) -> dict[str, object]:
        return {"action": "install"}

    def install_sync(**kwargs: object) -> dict[str, object]:
        loop.call_soon_threadsafe(worker_entered.set)
        release_worker.wait()
        target_dir.mkdir(parents=True)
        (target_dir / "plugin.toml").write_text(
            "\n".join(
                (
                    "[plugin]",
                    f'id = "{plugin_id}"',
                    'version = "1.0.0"',
                    f'entry = "{plugin_id}:Plugin"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        (target_dir / "plugin.py").write_bytes(b"written-after-cancel")
        loop.call_soon_threadsafe(worker_finished.set)
        return {
            "package_path": str(kwargs["package"]),
            "package_type": "plugin",
            "package_id": plugin_id,
            "plugins_root": str(user_root),
            "profiles_root": str(profiles_root),
            "installed_plugins": [
                {
                    "source_folder": plugin_id,
                    "target_plugin_id": plugin_id,
                    "target_dir": str(target_dir),
                    "renamed": False,
                }
            ],
            "installed_plugin_count": 1,
            "profile_dir": None,
            "operation": "install",
        }

    monkeypatch.setattr(service, "plan_install", plan_install)
    monkeypatch.setattr(service, "_install_sync", install_sync)

    async def run_operation() -> dict[str, object]:
        if operation == "install":
            return await service.install(
                package=str(source_package),
                install_source="imported",
            )
        if operation == "upload":
            return await service.upload_and_install(
                filename=f"{plugin_id}.neko-plugin",
                content=b"uploaded-package",
            )
        content = b"market-package"
        return await service.upload_and_install(
            filename=f"{plugin_id}.neko-plugin",
            content=content,
            install_source_override={
                "channel": "market",
                "mode": "install",
                "market_detail": {
                    "plugin_market_id": "cancel-test",
                    "version": "1.0.0",
                    "package_url": "https://invalid.example/cancel-test.neko-plugin",
                    "package_sha256": hashlib.sha256(content).hexdigest(),
                },
            },
        )

    async def contend_for_guard() -> None:
        contender_attempted.set()
        async with plugin_mutation_guard():
            contender_acquired.set()
            await release_contender.wait()

    operation_task = asyncio.create_task(run_operation())
    contender_task: asyncio.Task[None] | None = None
    try:
        await worker_entered.wait()
        operation_task.cancel()
        await _event_loop_checkpoint()

        contender_task = asyncio.create_task(contend_for_guard())
        await contender_attempted.wait()
        await _event_loop_checkpoint()

        assert not operation_task.done(), (
            "cancellation escaped before the file worker finished"
        )
        assert not contender_acquired.is_set(), (
            "mutation guard released during an in-flight write"
        )

        operation_task.cancel()
        await _event_loop_checkpoint()
        assert not operation_task.done(), (
            "a repeated cancel interrupted the only cleanup path"
        )
        assert not contender_acquired.is_set()

        release_worker.set()
        await worker_finished.wait()
        with pytest.raises(asyncio.CancelledError):
            await operation_task

        assert not target_dir.exists()
        assert _file_snapshot(packages_root) == packages_before
        assert source_manager.snapshot().entries == ()
        assert get_inventory_resolution(
            path=inventory_path
        ).active_user_directories == {}

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
        set_global_manager(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["upload", "stream", "market"])
async def test_cancelled_upload_waits_for_package_copy_and_removes_saved_file(
    operation: Literal["upload", "stream", "market"],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    for root in (builtin_root, user_root, packages_root, profiles_root):
        root.mkdir(parents=True)
    _patch_roots(
        monkeypatch,
        builtin_root=builtin_root,
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    source_package = tmp_path / "incoming.neko-plugin"
    source_package.write_bytes(b"incoming-package")
    saved_package = packages_root / "incoming.neko-plugin"

    service = PluginCliService()
    worker_entered = asyncio.Event()
    worker_finished = asyncio.Event()
    release_worker = threading.Event()
    contender_acquired = asyncio.Event()
    release_contender = asyncio.Event()
    loop = asyncio.get_running_loop()

    def save_package(**_kwargs: object) -> dict[str, object]:
        loop.call_soon_threadsafe(worker_entered.set)
        release_worker.wait()
        saved_package.write_bytes(b"copied-after-cancel")
        loop.call_soon_threadsafe(worker_finished.set)
        return {
            "name": saved_package.name,
            "path": str(saved_package),
            "size_bytes": saved_package.stat().st_size,
            "modified_at": "2026-08-16T00:00:00+00:00",
        }

    monkeypatch.setattr(service, "_save_package_file_sync", save_package)
    monkeypatch.setattr(service, "_save_uploaded_file_sync", save_package)

    async def run_operation() -> dict[str, object]:
        kwargs: dict[str, object] = {"filename": source_package.name}
        if operation == "stream":
            kwargs["source_file"] = io.BytesIO(b"streamed-package")
        else:
            kwargs["package_path"] = str(source_package)
        if operation == "market":
            kwargs["install_source_override"] = {
                "channel": "market",
                "mode": "install",
                "market_detail": {},
            }
        return await service.upload_and_install(**kwargs)

    async def contend_for_guard() -> None:
        async with plugin_mutation_guard():
            contender_acquired.set()
            await release_contender.wait()

    operation_task = asyncio.create_task(run_operation())
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

        assert not saved_package.exists()
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
