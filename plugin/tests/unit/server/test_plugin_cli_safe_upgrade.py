from __future__ import annotations

from pathlib import Path
import shutil
import zipfile

import pytest

from plugin.neko_plugin_cli.public import build_plugin
from plugin.server.application.plugin_cli.service import PluginCliService
from plugin.server.application.plugin_cli.install_plan import PluginInstallPlan
from plugin.server.application.plugins import upgrade_support
from plugin.server.application.plugins.upgrade_support import SafeUpgradeError, perform_safe_upgrade
from plugin.server.domain.errors import ServerDomainError

pytestmark = pytest.mark.plugin_unit


def _upgrade_plan() -> PluginInstallPlan:
    return PluginInstallPlan(
        action="upgrade",
        package_type="plugin",
        package_id="demo",
        plugin_id="demo",
        directory_name="demo",
        current_version="1.0.0",
        target_version="2.0.0",
        confirmation_token="a" * 64,
        reason="",
        legacy_plugin_ids=(),
    )


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

    with pytest.raises(SafeUpgradeError, match=failure_stage):
        await perform_safe_upgrade(
            plan=_upgrade_plan(),
            target_dir=target,
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

    result = await perform_safe_upgrade(
        plan=_upgrade_plan(),
        target_dir=target,
        install_new=install_new,
        validate_new=lambda: _async_none(),
        is_running=lambda _plugin_id: _async_true(),
        stop=lambda plugin_id: _record(calls, f"stop:{plugin_id}"),
        start=lambda plugin_id: _record(calls, f"start:{plugin_id}"),
        cleanup_backup=cleanup_backup,
    )

    assert result.operation == "upgrade"
    assert result.restarted is True
    assert result.rollback_status == "not_needed"
    assert result.backup_dir.name.startswith("demo.bak.")
    assert 'version = "2.0.0"' in (target / "plugin.toml").read_text(encoding="utf-8")
    assert calls[0:2] == ["stop:demo", "start:demo"]
    assert calls[2].startswith("cleanup:demo.bak.")
    assert not result.backup_dir.exists()


async def _async_none() -> None:
    return None


async def _async_true() -> bool:
    return True


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

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)

    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))
    (target / "plugin.toml").write_text(
        '[plugin]\nid = "demo"\nname = "demo"\nversion = "1.0.1"\n',
        encoding="utf-8",
    )
    stop_calls: list[str] = []

    async def unexpected_stop(plugin_id: str) -> None:
        stop_calls.append(plugin_id)

    monkeypatch.setattr(upgrade_support, "stop_plugin_for_upgrade", unexpected_stop)
    with pytest.raises(ServerDomainError) as exc_info:
        await service.install(
            package=str(package_path),
            confirm_upgrade=True,
            confirmation_token=str(plan["confirmation_token"]),
        )

    assert exc_info.value.code == "PLUGIN_UPGRADE_PLAN_CHANGED"
    assert stop_calls == []


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

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", plugins_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)

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
