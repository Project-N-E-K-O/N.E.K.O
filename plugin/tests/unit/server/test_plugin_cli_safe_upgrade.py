from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil
from types import SimpleNamespace
import zipfile

import pytest

from plugin.core.plugin_layout import resolve_plugin_layout
from plugin.neko_plugin_cli.public import build_plugin
from plugin.server.application.plugin_cli.service import (
    PluginCliService,
    _replacement_error_details,
)
from plugin.server.application.install_source.models import LockEntry
from plugin.server.application.plugins import upgrade_support
from plugin.server.application.plugins.upgrade_support import ReplacePluginError, replace_plugin
from plugin.server.domain.errors import ServerDomainError

pytestmark = pytest.mark.plugin_unit


class _ManualTakeoverManager:
    def __init__(self, entry: LockEntry) -> None:
        self.entry = entry
        self.is_degraded = False
        self.restore_calls = 0

    def entry_for_directory(
        self,
        _directory_path: Path,
        *,
        include_removed: bool = False,
    ) -> LockEntry | None:
        assert include_removed is False
        return self.entry

    def package_id_for_directory(self, _directory_path: Path) -> str:
        return self.entry.package_id

    def record_import(self, *, directory_path: Path, **_kwargs: object) -> None:
        self.entry = replace(
            self.entry,
            directory_name=directory_path.name,
            channel="imported",
            updated_at="2026-08-29T00:00:01.000000Z",
        )

    def restore_entry_for_rollback(self, entry: LockEntry) -> None:
        self.restore_calls += 1
        self.entry = entry


def _manual_entry() -> LockEntry:
    return LockEntry(
        root_id="user",
        directory_name="demo",
        plugin_id="demo",
        channel="manual",
        reason="user_requested",
        installed_at="2026-08-29T00:00:00.000000Z",
        updated_at="2026-08-29T00:00:00.000000Z",
        last_seen_at="2026-08-29T00:00:00.000000Z",
    )


def _managed_entry(*, package_id: str = "demo") -> LockEntry:
    return replace(
        _manual_entry(),
        channel="imported",
        package_id=package_id,
    )


def _managed_manager(*, package_id: str = "demo") -> SimpleNamespace:
    entry = _managed_entry(package_id=package_id)
    return SimpleNamespace(
        entry_for_directory=lambda _path: entry,
        package_id_for_directory=lambda _path: entry.package_id,
    )


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            ValueError("payload hash mismatch between archive payload and metadata.toml"),
            "PLUGIN_PACKAGE_HASH_MISMATCH",
        ),
        (
            ValueError("plugin folder 'demo' does not match plugin.toml id 'other'"),
            "PLUGIN_PACKAGE_IDENTITY_MISMATCH",
        ),
        (
            ValueError(
                "package archive contains paths that are equivalent on common filesystems"
            ),
            "PLUGIN_PACKAGE_INVALID_ARCHIVE",
        ),
    ],
)
def test_package_validation_errors_use_stable_codes(
    error: Exception,
    expected_code: str,
) -> None:
    domain_error = PluginCliService()._domain_error_from_exception(error, action="inspect")

    assert domain_error.code == expected_code
    assert domain_error.status_code == 400


def test_hash_mismatch_reason_survives_upgrade_rollback() -> None:
    error = ReplacePluginError(
        stage="install",
        rollback_status="completed",
        cause=ServerDomainError(
            code="PLUGIN_PACKAGE_HASH_MISMATCH",
            message="package bytes do not match metadata",
            status_code=400,
        ),
    )

    assert _replacement_error_details(error) == {
        "stage": "install",
        "rollback_status": "completed",
        "cause_code": "PLUGIN_PACKAGE_HASH_MISMATCH",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["install", "validate", "restart"])
async def test_safe_upgrade_restores_old_directory_after_each_failure(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    (target / "plugin.toml").write_text(
        '[plugin]\nid = "demo"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    profile = tmp_path / "profiles" / "demo"
    profile.mkdir(parents=True)
    (profile / "default.toml").write_text("version = 1\n", encoding="utf-8")
    storage_root = tmp_path / "state"
    expected_state = {
        storage_root / "plugins" / "demo" / "config" / "plugin.toml": "user_config = true\n",
        storage_root / "plugins" / "demo" / "data" / "value.txt": "user data\n",
        storage_root / "plugins" / "demo" / "cache" / "value.txt": "cache data\n",
    }
    for path, content in expected_state.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    calls: list[str] = []
    start_attempts = 0

    async def is_running(plugin_id: str) -> bool:
        assert plugin_id == "demo"
        return True

    async def stop(plugin_id: str) -> None:
        calls.append(f"stop:{plugin_id}")

    async def install_new() -> dict[str, object]:
        if failure_stage == "install":
            raise RuntimeError("install failed")
        target.mkdir()
        (target / "plugin.toml").write_text(
            '[plugin]\nid = "demo"\nversion = "2.0.0"\n',
            encoding="utf-8",
        )
        profile.mkdir()
        (profile / "default.toml").write_text("version = 2\n", encoding="utf-8")
        return {"ok": True}

    async def validate_new() -> None:
        if failure_stage == "validate":
            raise RuntimeError("validate failed")

    async def start(plugin_id: str) -> None:
        nonlocal start_attempts
        start_attempts += 1
        calls.append(f"start:{plugin_id}")
        if failure_stage == "restart" and start_attempts == 1:
            raise RuntimeError("restart failed")

    async def cleanup_backup(path: Path) -> None:
        calls.append(f"cleanup:{path.name}")

    with pytest.raises(ReplacePluginError, match=failure_stage):
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target, storage_root=storage_root),
            install_new=install_new,
            validate_new=validate_new,
            is_running=is_running,
            stop=stop,
            start=start,
            cleanup_backup=cleanup_backup,
            additional_targets=(profile,),
        )

    assert 'version = "1.0.0"' in (target / "plugin.toml").read_text(encoding="utf-8")
    assert (profile / "default.toml").read_text(encoding="utf-8") == "version = 1\n"
    assert "stop:demo" in calls
    assert "start:demo" in calls
    for path, content in expected_state.items():
        assert path.read_text(encoding="utf-8") == content
    assert not list((tmp_path / ".upgrade-backups").glob("demo.bak.*"))
    assert not list((profile.parent / ".upgrade-backups").glob("demo.bak.*"))


@pytest.mark.asyncio
async def test_safe_upgrade_replaces_plugin_and_cleans_backup_on_success(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    (target / "plugin.toml").write_text(
        '[plugin]\nid = "demo"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    calls: list[str] = []

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text(
            '[plugin]\nid = "demo"\nversion = "2.0.0"\n',
            encoding="utf-8",
        )
        return {"ok": True}

    async def cleanup_backup(path: Path) -> None:
        calls.append(f"cleanup:{path.name}")
        shutil.rmtree(path)

    result = await replace_plugin(
        layout=resolve_plugin_layout("demo", target),
        install_new=install_new,
        validate_new=lambda: _async_none(),
        is_running=lambda _plugin_id: _async_true(),
        stop=lambda plugin_id: _record(calls, f"stop:{plugin_id}"),
        start=lambda plugin_id: _record(calls, f"start:{plugin_id}"),
        cleanup_backup=cleanup_backup,
    )

    assert result.restarted is True
    assert result.rollback_status == "not_needed"
    assert result.backup_dir.name.startswith("demo.bak.")
    assert 'version = "2.0.0"' in (target / "plugin.toml").read_text(encoding="utf-8")
    assert calls[0:2] == ["stop:demo", "start:demo"]
    assert calls[2].startswith("cleanup:demo.bak.")
    assert not result.backup_dir.exists()


@pytest.mark.asyncio
async def test_rollback_keeps_targets_that_were_not_backed_up(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    backup = tmp_path / ".upgrade-backups" / "demo.bak"
    backup.mkdir(parents=True)
    (backup / "plugin.toml").write_text("version = 1\n", encoding="utf-8")
    profile = tmp_path / "profiles" / "demo"
    profile.mkdir(parents=True)
    (profile / "default.toml").write_text("user_value = true\n", encoding="utf-8")

    restored = await upgrade_support._rollback_targets(
        targets=(target, profile),
        backups={target: backup},
        preexisting_targets=frozenset({target, profile}),
        remove_created_targets=False,
    )

    assert restored is True
    assert (target / "plugin.toml").read_text(encoding="utf-8") == "version = 1\n"
    assert (profile / "default.toml").read_text(encoding="utf-8") == "user_value = true\n"


@pytest.mark.asyncio
async def test_safe_upgrade_removes_profile_created_by_failed_install(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    (target / "plugin.toml").write_text("version = 1\n", encoding="utf-8")
    profile = tmp_path / "profiles" / "demo"

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text("version = 2\n", encoding="utf-8")
        profile.mkdir(parents=True)
        (profile / "default.toml").write_text("new_value = true\n", encoding="utf-8")
        return {"ok": True}

    async def validate_new() -> None:
        raise RuntimeError("validation failed")

    with pytest.raises(ReplacePluginError, match="validate"):
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target),
            install_new=install_new,
            validate_new=validate_new,
            is_running=lambda _plugin_id: _async_true(),
            stop=lambda _plugin_id: _async_none(),
            start=lambda _plugin_id: _async_none(),
            cleanup_backup=lambda _path: _async_none(),
            additional_targets=(profile,),
        )

    assert (target / "plugin.toml").read_text(encoding="utf-8") == "version = 1\n"
    assert not profile.exists()


async def _async_none() -> None:
    return None


async def _async_true() -> bool:
    return True


async def _async_false() -> bool:
    return False


async def _record(calls: list[str], value: str) -> None:
    calls.append(value)


def _write_plugin(root: Path, plugin_id: str, version: str) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        (
            "[plugin]\n"
            f'id = "{plugin_id}"\n'
            f'name = "{plugin_id}"\n'
            f'version = "{version}"\n'
            'type = "plugin"\n\n'
            f"[{plugin_id}]\n"
            "enabled = true\n"
        ),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    return plugin_dir


def _rewrite_package_manifest_id(package_path: Path, package_id: str) -> None:
    entries: list[tuple[zipfile.ZipInfo, bytes]] = []
    with zipfile.ZipFile(package_path) as src:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "manifest.toml":
                manifest = data.decode("utf-8")
                data = manifest.replace('id = "demo"', f'id = "{package_id}"', 1).encode("utf-8")
            entries.append((info, data))

    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for info, data in entries:
            dst.writestr(info, data)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_version", "expected_operation"),
    [
        ("2.0.0", "upgrade"),
        ("1.0.0", "reinstall"),
        ("0.5.0", "downgrade"),
    ],
)
async def test_service_replaces_with_new_same_or_old_version_without_touching_user_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_version: str,
    expected_operation: str,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", target_version)
    (source / "data").mkdir()
    (source / "data" / "resource.json").write_text("new-package-resource\n", encoding="utf-8")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / f"demo-{target_version}.neko-plugin"
    build_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    installed_plugin = _write_plugin(plugins_root, "demo", "1.0.0")
    (installed_plugin / "data").mkdir()
    (installed_plugin / "data" / "resource.json").write_text(
        "old-package-resource\n",
        encoding="utf-8",
    )
    (installed_plugin / "data" / "removed.json").write_text(
        "removed-package-resource\n",
        encoding="utf-8",
    )
    profiles_root = tmp_path / "profiles"
    storage_root = tmp_path / "state"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))

    expected_state = {
        storage_root / "plugins" / "demo" / "config" / "plugin.toml": "user_config = true\n",
        storage_root / "plugins" / "demo" / "data" / "value.txt": "user data\n",
        storage_root / "plugins" / "demo" / "cache" / "value.txt": "cache data\n",
    }
    for path, content in expected_state.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(
        service_module,
        "get_install_source_manager",
        lambda: _managed_manager(),
    )
    monkeypatch.setattr(upgrade_support, "plugin_is_running", lambda _plugin_id: _async_false())

    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))
    assert plan["action"] == expected_operation

    result = await service.install(
        package=str(package_path),
        confirm_upgrade=True,
        confirmation_token=str(plan["confirmation_token"]),
    )

    assert result["operation"] == expected_operation
    installed_manifest = (plugins_root / "demo" / "plugin.toml").read_text(encoding="utf-8")
    assert f'version = "{target_version}"' in installed_manifest
    assert (plugins_root / "demo" / "data" / "resource.json").read_text(
        encoding="utf-8"
    ) == "new-package-resource\n"
    assert not (plugins_root / "demo" / "data" / "removed.json").exists()
    for path, content in expected_state.items():
        assert path.read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_service_rejects_changed_target_before_stopping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / "demo-2.0.0.neko-plugin"
    build_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    target = _write_plugin(plugins_root, "demo", "1.0.0")
    profiles_root = tmp_path / "profiles"

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(
        service_module,
        "get_install_source_manager",
        lambda: _managed_manager(),
    )

    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))
    (target / "plugin.toml").write_text(
        '[plugin]\nid = "demo"\nname = "demo"\nversion = "1.0.1"\n',
        encoding="utf-8",
    )
    stop_calls: list[str] = []

    async def unexpected_stop(plugin_id: str) -> None:
        stop_calls.append(plugin_id)

    monkeypatch.setattr(upgrade_support, "stop_plugin_for_replace", unexpected_stop)
    with pytest.raises(ServerDomainError) as exc_info:
        await service.install(
            package=str(package_path),
            confirm_upgrade=True,
            confirmation_token=str(plan["confirmation_token"]),
        )

    assert exc_info.value.code == "PLUGIN_UPGRADE_PLAN_CHANGED"
    assert stop_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [("directory_name", "../outside"), ("package_id", "../outside")],
)
async def test_service_rejects_unsafe_upgrade_plan_paths_before_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    unsafe_value: str,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / "demo-2.0.0.neko-plugin"
    build_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "demo", "1.0.0")
    profiles_root = tmp_path / "profiles"

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(
        service_module,
        "get_install_source_manager",
        lambda: _managed_manager(),
    )

    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))
    plan[field] = unsafe_value

    async def unsafe_plan_install(**_kwargs: object) -> dict[str, object]:
        return plan

    backup_attempted = False

    async def unexpected_upgrade(**_kwargs: object) -> object:
        nonlocal backup_attempted
        backup_attempted = True
        return object()

    monkeypatch.setattr(service, "plan_install", unsafe_plan_install)
    monkeypatch.setattr(upgrade_support, "replace_plugin", unexpected_upgrade)

    with pytest.raises(ValueError, match=field):
        await service.install(
            package=str(package_path),
            confirm_upgrade=True,
            confirmation_token=str(plan["confirmation_token"]),
        )

    assert backup_attempted is False


@pytest.mark.asyncio
async def test_service_backs_up_profile_by_package_id_during_upgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / "demo-2.0.0.neko-plugin"
    build_plugin(source, package_path)
    _rewrite_package_manifest_id(package_path, "demo-package")

    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "demo", "1.0.0")
    profiles_root = tmp_path / "profiles"
    profile_dir = profiles_root / "demo-package"
    profile_dir.mkdir(parents=True)
    (profile_dir / "default.toml").write_text("old_profile = true\n", encoding="utf-8")
    (profile_dir / "custom.toml").write_text("custom = true\n", encoding="utf-8")

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(
        service_module,
        "get_install_source_manager",
        lambda: _managed_manager(package_id="demo-package"),
    )

    async def not_running(_plugin_id: str) -> bool:
        return False

    monkeypatch.setattr(upgrade_support, "plugin_is_running", not_running)

    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))
    assert plan["package_id"] == "demo-package"
    result = await service.install(
        package=str(package_path),
        confirm_upgrade=True,
        confirmation_token=str(plan["confirmation_token"]),
    )

    assert result["operation"] == "upgrade"
    assert 'version = "2.0.0"' in (plugins_root / "demo" / "plugin.toml").read_text(encoding="utf-8")
    assert (profile_dir / "default.toml").read_text(encoding="utf-8") == "old_profile = true\n"
    assert (profile_dir / "custom.toml").read_text(encoding="utf-8") == "custom = true\n"


@pytest.mark.asyncio
async def test_service_uses_custom_profile_root_with_recorded_package_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / "demo-2.0.0.neko-plugin"
    build_plugin(source, package_path)
    _rewrite_package_manifest_id(package_path, "demo-package")

    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "demo", "1.0.0")
    profiles_root = tmp_path / "current_profiles"
    custom_profiles_root = tmp_path / "recorded_profiles" / "custom"
    profile_dir = custom_profiles_root / "demo-package"
    profile_dir.mkdir(parents=True)
    (profile_dir / "custom.toml").write_text("custom = true\n", encoding="utf-8")

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(
        service_module,
        "get_install_source_manager",
        lambda: _managed_manager(package_id="demo-package"),
    )

    async def not_running(_plugin_id: str) -> bool:
        return False

    monkeypatch.setattr(upgrade_support, "plugin_is_running", not_running)

    service = PluginCliService()
    plan = await service.plan_install(
        package=str(package_path),
        profiles_root=str(custom_profiles_root),
        _allow_external_profiles_root=True,
    )

    assert plan["action"] == "upgrade"
    assert plan["installed_package_id"] == "demo-package"

    result = await service.install(
        package=str(package_path),
        profiles_root=str(custom_profiles_root),
        confirm_upgrade=True,
        confirmation_token=str(plan["confirmation_token"]),
        _allow_external_profiles_root=True,
    )

    assert result["operation"] == "upgrade"
    assert (profile_dir / "custom.toml").read_text(encoding="utf-8") == "custom = true\n"


@pytest.mark.asyncio
async def test_service_rejects_legacy_package_rename_despite_stale_incoming_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / "demo-2.0.0.neko-plugin"
    build_plugin(source, package_path)
    _rewrite_package_manifest_id(package_path, "new-package")

    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "demo", "1.0.0")
    profiles_root = tmp_path / "profiles"
    stale_profile = profiles_root / "new-package"
    stale_profile.mkdir(parents=True)
    (stale_profile / "default.toml").write_text("stale = true\n", encoding="utf-8")

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(
        service_module,
        "get_install_source_manager",
        lambda: _managed_manager(package_id=""),
    )

    plan = await PluginCliService().plan_install(package=str(package_path))

    assert plan["action"] == "blocked"
    assert plan["reason"] == "package_id_change"
    assert plan["installed_package_id"] == "demo"
    assert (stale_profile / "default.toml").read_text(encoding="utf-8") == "stale = true\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("source_state", ["manager_missing", "entry_missing"])
async def test_local_replacement_fails_closed_when_manifest_ownership_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_state: str,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / "demo.neko-plugin"
    build_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "demo", "1.0.0")

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", tmp_path / "profiles")
    manager = (
        None
        if source_state == "manager_missing"
        else SimpleNamespace(entry_for_directory=lambda _path: None)
    )
    monkeypatch.setattr(service_module, "get_install_source_manager", lambda: manager)

    plan = await PluginCliService().plan_install(package=str(package_path))

    assert plan["action"] == "blocked"
    assert plan["reason"] == "install_source_ownership_unknown"
    assert plan["confirmation_token"] == ""
    assert plan["current_source"] == "unknown"


@pytest.mark.asyncio
async def test_local_manual_takeover_fails_closed_when_install_source_is_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / "demo.neko-plugin"
    build_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "demo", "1.0.0")
    manager = _ManualTakeoverManager(_manual_entry())
    manager.is_degraded = True

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(service_module, "get_install_source_manager", lambda: manager)

    plan = await PluginCliService().plan_install(package=str(package_path))

    assert plan["action"] == "blocked"
    assert plan["reason"] == "install_source_read_only"
    assert plan["confirmation_token"] == ""
    assert plan["current_source"] == "manual"
    assert plan["target_source"] == "imported"


@pytest.mark.asyncio
async def test_local_package_manual_takeover_requires_bound_plan_and_records_imported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / "demo.neko-plugin"
    build_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    manual_dir = _write_plugin(plugins_root, "demo", "1.0.0")
    (manual_dir / "manual.py").write_text("ORIGINAL = True\n", encoding="utf-8")
    profiles_root = tmp_path / "profiles"
    manager = _ManualTakeoverManager(_manual_entry())

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(service_module, "get_install_source_manager", lambda: manager)
    monkeypatch.setattr(upgrade_support, "plugin_is_running", lambda _plugin_id: _async_false())

    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))

    assert plan["reason"] == "manual_takeover"
    assert plan["current_source"] == "manual"
    assert plan["target_source"] == "imported"
    assert len(str(plan["confirmation_token"])) == 64

    result = await service.install(
        package=str(package_path),
        confirm_upgrade=True,
        confirmation_token=str(plan["confirmation_token"]),
    )

    assert result["operation"] == "upgrade"
    assert manager.entry.channel == "imported"
    assert not (manual_dir / "manual.py").exists()


@pytest.mark.asyncio
async def test_local_manual_takeover_reloads_ownership_inside_operation_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / "demo.neko-plugin"
    build_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    manual_dir = _write_plugin(plugins_root, "demo", "1.0.0")
    sentinel = manual_dir / "manual.py"
    sentinel.write_text("ORIGINAL = True\n", encoding="utf-8")
    manager = _ManualTakeoverManager(_manual_entry())
    load_calls = 0

    def reload_changed_owner() -> None:
        nonlocal load_calls
        load_calls += 1
        manager.entry = _managed_entry()

    manager.load = reload_changed_owner  # type: ignore[attr-defined]

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(service_module, "get_install_source_manager", lambda: manager)

    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))
    with pytest.raises(ServerDomainError) as exc_info:
        await service.install(
            package=str(package_path),
            confirm_upgrade=True,
            confirmation_token=str(plan["confirmation_token"]),
        )

    assert exc_info.value.code == "PLUGIN_UPGRADE_PLAN_CHANGED"
    assert load_calls == 1
    assert sentinel.read_text(encoding="utf-8") == "ORIGINAL = True\n"


@pytest.mark.asyncio
async def test_local_manual_takeover_rejects_unowned_existing_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / "demo.neko-plugin"
    build_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    manual_dir = _write_plugin(plugins_root, "demo", "1.0.0")
    profile_dir = tmp_path / "profiles" / "demo"
    profile_dir.mkdir(parents=True)
    sentinel = profile_dir / "custom.toml"
    sentinel.write_text("belongs_to_user = true\n", encoding="utf-8")
    manager = _ManualTakeoverManager(_manual_entry())

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(
        plugin_settings,
        "USER_PACKAGE_PROFILES_ROOT",
        profile_dir.parent,
    )
    monkeypatch.setattr(service_module, "get_install_source_manager", lambda: manager)

    plan = await PluginCliService().plan_install(package=str(package_path))

    assert plan["action"] == "blocked"
    assert plan["reason"] == "manual_takeover_profile_target_exists"
    assert sentinel.read_text(encoding="utf-8") == "belongs_to_user = true\n"
    assert manual_dir.is_dir()


@pytest.mark.asyncio
async def test_local_manual_takeover_revalidates_backup_after_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / "demo.neko-plugin"
    build_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    manual_dir = _write_plugin(plugins_root, "demo", "1.0.0")
    sentinel = manual_dir / "manual.py"
    sentinel.write_text("confirmed = true\n", encoding="utf-8")
    manager = _ManualTakeoverManager(_manual_entry())

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(
        plugin_settings,
        "USER_PACKAGE_PROFILES_ROOT",
        tmp_path / "profiles",
    )
    monkeypatch.setattr(service_module, "get_install_source_manager", lambda: manager)
    monkeypatch.setattr(upgrade_support, "plugin_is_running", lambda _plugin_id: _async_true())

    async def mutate_while_stopping(_plugin_id: str) -> None:
        sentinel.write_text("edited_during_stop = true\n", encoding="utf-8")

    monkeypatch.setattr(upgrade_support, "stop_plugin_for_replace", mutate_while_stopping)
    monkeypatch.setattr(
        upgrade_support,
        "start_plugin_after_replace",
        lambda _plugin_id, *, strict: _async_true(),
    )

    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))
    with pytest.raises(ServerDomainError) as exc_info:
        await service.install(
            package=str(package_path),
            confirm_upgrade=True,
            confirmation_token=str(plan["confirmation_token"]),
        )

    assert exc_info.value.code == "PLUGIN_UPGRADE_ROLLED_BACK"
    assert sentinel.read_text(encoding="utf-8") == "edited_during_stop = true\n"
    assert manager.entry.channel == "manual"
    assert manager.restore_calls == 0


@pytest.mark.asyncio
async def test_local_package_takes_over_exact_manual_slot_alongside_builtin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / "demo.neko-plugin"
    build_plugin(source, package_path)
    builtin_root = tmp_path / "builtin"
    builtin_dir = _write_plugin(builtin_root, "demo", "0.5.0")
    plugins_root = tmp_path / "plugins"
    manual_dir = _write_plugin(plugins_root, "demo", "1.0.0")
    (manual_dir / "manual.py").write_text("ORIGINAL = True\n", encoding="utf-8")
    manager = _ManualTakeoverManager(_manual_entry())

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(service_module, "get_install_source_manager", lambda: manager)
    monkeypatch.setattr(upgrade_support, "plugin_is_running", lambda _plugin_id: _async_false())

    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))
    result = await service.install(
        package=str(package_path),
        confirm_upgrade=True,
        confirmation_token=str(plan["confirmation_token"]),
    )

    assert plan["action"] == "upgrade"
    assert plan["reason"] == "manual_takeover"
    assert result["operation"] == "upgrade"
    assert manager.entry.channel == "imported"
    assert 'version = "2.0.0"' in (manual_dir / "plugin.toml").read_text(encoding="utf-8")
    assert 'version = "0.5.0"' in (builtin_dir / "plugin.toml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_failed_local_manual_takeover_restores_directory_and_lock_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / "demo.neko-plugin"
    build_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    manual_dir = _write_plugin(plugins_root, "demo", "1.0.0")
    sentinel = manual_dir / "manual.py"
    sentinel.write_text("ORIGINAL = True\n", encoding="utf-8")
    profiles_root = tmp_path / "profiles"
    original_entry = _manual_entry()
    manager = _ManualTakeoverManager(original_entry)

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(service_module, "get_install_source_manager", lambda: manager)

    async def running(_plugin_id: str) -> bool:
        return True

    async def stop(_plugin_id: str) -> None:
        return None

    async def fail_start(_plugin_id: str, *, strict: bool) -> bool:
        assert strict is True
        raise RuntimeError("restart failed")

    monkeypatch.setattr(upgrade_support, "plugin_is_running", running)
    monkeypatch.setattr(upgrade_support, "stop_plugin_for_replace", stop)
    monkeypatch.setattr(upgrade_support, "start_plugin_after_replace", fail_start)

    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))
    with pytest.raises(ServerDomainError) as exc_info:
        await service.install(
            package=str(package_path),
            confirm_upgrade=True,
            confirmation_token=str(plan["confirmation_token"]),
        )

    assert exc_info.value.code == "PLUGIN_UPGRADE_ROLLED_BACK"
    assert sentinel.read_text(encoding="utf-8") == "ORIGINAL = True\n"
    assert manager.entry == original_entry
    assert manager.restore_calls == 1
    assert not (profiles_root / "demo").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("target_version", ["2.0.0", "1.0.0", "0.5.0"])
async def test_service_blocks_package_id_change_before_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_version: str,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", target_version)
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / f"demo-{target_version}.neko-plugin"
    build_plugin(source, package_path)
    _rewrite_package_manifest_id(package_path, "new-package")

    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "demo", "1.0.0")
    profiles_root = tmp_path / "profiles"
    old_profile = profiles_root / "old-package"
    old_profile.mkdir(parents=True)
    (old_profile / "default.toml").write_text("user_value = true\n", encoding="utf-8")

    import plugin.settings as plugin_settings
    import plugin.server.application.plugin_cli.service as service_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(
        service_module,
        "get_install_source_manager",
        lambda: _managed_manager(package_id="old-package"),
    )

    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))

    assert plan["action"] == "blocked"
    assert plan["reason"] == "package_id_change"
    assert plan["package_id"] == "new-package"
    assert plan["installed_package_id"] == "old-package"
    assert (old_profile / "default.toml").read_text(encoding="utf-8") == "user_value = true\n"
