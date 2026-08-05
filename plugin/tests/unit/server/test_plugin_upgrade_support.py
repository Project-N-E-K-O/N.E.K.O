from __future__ import annotations

from pathlib import Path

import pytest

from plugin.core.plugin_layout import resolve_plugin_layout
from plugin.server.application.plugins.upgrade_support import (
    ReplacePluginError,
    plugin_is_running,
    replace_plugin,
    remove_directory,
    run_rollback,
)

pytestmark = pytest.mark.plugin_unit


async def _async_none() -> None:
    return None


async def _async_false() -> bool:
    return False


@pytest.mark.asyncio
async def test_replace_plugin_replaces_only_payload_and_preserves_external_user_state(
    tmp_path: Path,
) -> None:
    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text("version = 1\n", encoding="utf-8")
    (target / "vendor").mkdir()
    (target / "vendor" / "dependency.txt").write_text("old", encoding="utf-8")

    storage_root = tmp_path / "state"
    state_root = storage_root / "plugins" / "demo"
    expected_state = {
        state_root / "config" / "plugin.toml": "user_config = true\n",
        state_root / "data" / "database.txt": "user data\n",
        state_root / "cache" / "cached.txt": "cache data\n",
    }
    for path, content in expected_state.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text("version = 2\n", encoding="utf-8")
        (target / "vendor").mkdir()
        (target / "vendor" / "dependency.txt").write_text("new", encoding="utf-8")
        return {"installed": True}

    result = await replace_plugin(
        layout=resolve_plugin_layout("demo", target, storage_root=storage_root),
        install_new=install_new,
        validate_new=_async_none,
        is_running=lambda _plugin_id: _async_false(),
        stop=lambda _plugin_id: _async_none(),
        start=lambda _plugin_id: _async_none(),
        cleanup_backup=remove_directory,
    )

    assert (target / "plugin.toml").read_text(encoding="utf-8") == "version = 2\n"
    assert (target / "vendor" / "dependency.txt").read_text(encoding="utf-8") == "new"
    for path, content in expected_state.items():
        assert path.read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_replace_plugin_initializes_runtime_config_from_old_payload_before_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "state"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    old_manifest = (
        "[plugin]\n"
        'id = "demo"\n'
        'version = "1.0.0"\n'
        'entry = "plugins.demo:Demo"\n'
        "\n[demo]\n"
        'message = "user value"\n'
    )
    (target / "plugin.toml").write_text(old_manifest, encoding="utf-8")

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text(
            "[plugin]\n"
            'id = "demo"\n'
            'version = "2.0.0"\n'
            'entry = "plugins.demo:Demo"\n',
            encoding="utf-8",
        )
        return {"installed": True}

    await replace_plugin(
        layout=resolve_plugin_layout("demo", target, storage_root=storage_root),
        install_new=install_new,
        validate_new=_async_none,
        is_running=lambda _plugin_id: _async_false(),
        stop=lambda _plugin_id: _async_none(),
        start=lambda _plugin_id: _async_none(),
        cleanup_backup=remove_directory,
    )

    runtime_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    assert runtime_config.read_text(encoding="utf-8") == old_manifest


@pytest.mark.asyncio
async def test_run_rollback_removes_new_directory_restores_backup_and_restarts(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    backup = tmp_path / "demo.bak"
    target.mkdir()
    (target / "new.txt").write_text("new", encoding="utf-8")
    backup.mkdir()
    (backup / "old.txt").write_text("old", encoding="utf-8")
    restarted: list[str] = []

    async def start(plugin_id: str) -> None:
        restarted.append(plugin_id)

    restored = await run_rollback(
        plugin_id="demo",
        target_dir=target,
        backup_dir=backup,
        restart=True,
        start=start,
    )

    assert restored is True
    assert (target / "old.txt").read_text(encoding="utf-8") == "old"
    assert restarted == ["demo"]


@pytest.mark.asyncio
async def test_backup_failure_restarts_running_plugin_without_installing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    (target / "plugin.toml").write_text(
        '[plugin]\nid = "demo"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    events: list[str] = []

    async def is_running(plugin_id: str) -> bool:
        return True

    async def stop(plugin_id: str) -> None:
        events.append(f"stop:{plugin_id}")

    async def start(plugin_id: str) -> None:
        events.append(f"start:{plugin_id}")

    async def install_new() -> dict[str, object]:
        events.append("install")
        return {}

    async def validate_new() -> None:
        events.append("validate")

    async def cleanup_backup(path: Path) -> None:
        events.append(f"cleanup:{path.name}")

    def fail_rename(self: Path, destination: Path) -> Path:
        raise PermissionError(destination)

    monkeypatch.setattr(Path, "rename", fail_rename)

    with pytest.raises(ReplacePluginError) as exc_info:
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target),
            install_new=install_new,
            validate_new=validate_new,
            is_running=is_running,
            stop=stop,
            start=start,
            cleanup_backup=cleanup_backup,
        )

    assert exc_info.value.stage == "backup"
    assert exc_info.value.rollback_status == "completed"
    assert events == ["stop:demo", "start:demo"]


@pytest.mark.asyncio
async def test_plugin_is_running_propagates_registry_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.application.plugins import lifecycle_service

    def fail_probe(plugin_id: str) -> bool:
        raise RuntimeError(f"registry unavailable for {plugin_id}")

    monkeypatch.setattr(lifecycle_service, "_plugin_is_running_sync", fail_probe)

    with pytest.raises(RuntimeError, match="registry unavailable"):
        await plugin_is_running("demo")


@pytest.mark.asyncio
async def test_remove_directory_propagates_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.application.plugins import upgrade_support

    target = tmp_path / "demo"
    target.mkdir()
    ignore_values: list[bool] = []

    def fail_unless_errors_are_suppressed(path: Path, ignore_errors: bool = False) -> None:
        assert path == target
        ignore_values.append(ignore_errors)
        if not ignore_errors:
            raise PermissionError("cleanup denied")

    monkeypatch.setattr(upgrade_support.shutil, "rmtree", fail_unless_errors_are_suppressed)

    with pytest.raises(PermissionError, match="cleanup denied"):
        await remove_directory(target)

    assert ignore_values == [False]
