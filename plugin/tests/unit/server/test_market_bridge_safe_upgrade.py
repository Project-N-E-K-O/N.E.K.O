from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any

import pytest

from plugin.server.routes import market_bridge


pytestmark = pytest.mark.plugin_unit


def _payload(plugin_id: str = "demo") -> SimpleNamespace:
    return SimpleNamespace(
        plugin_id=plugin_id,
        version="2.0.0",
        expected_plugin_toml_id=plugin_id,
        package_url="https://example.invalid/demo.neko-plugin",
        package_sha256="a" * 64,
        payload_hash="",
        channel="stable",
        published_at="",
    )


def _entry(plugin_id: str = "demo", package_id: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        plugin_id=plugin_id,
        directory_name=plugin_id,
        source_detail=None,
        package_id=package_id,
    )


def _configure_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    plugins_root: Path,
    profiles_root: Path,
) -> None:
    policy = SimpleNamespace(
        user_plugins_root=plugins_root,
        package_profiles_root=profiles_root,
        package_artifacts_root=plugins_root.parent / "packages",
    )
    monkeypatch.setattr(
        market_bridge.PluginCliPathPolicy,
        "from_settings",
        classmethod(lambda cls: policy),
    )
    monkeypatch.setattr(
        market_bridge,
        "get_install_source_manager",
        lambda: SimpleNamespace(find_active_market_entry=lambda plugin_id: _entry(plugin_id)),
    )
    monkeypatch.setattr(
        market_bridge,
        "inspect_package",
        lambda path: SimpleNamespace(package_id="demo"),
    )


@pytest.mark.asyncio
async def test_market_upgrade_rolls_back_plugin_profile_with_plugin_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    profile_dir = profiles_root / "demo"
    plugin_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    (profile_dir / "default.toml").write_text("version = 1\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(
        monkeypatch,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda url, task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda path: None)

    async def install_then_fail(**kwargs: Any) -> dict[str, object]:
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)
        if profile_dir.exists():
            shutil.rmtree(profile_dir)
        plugin_dir.mkdir(parents=True)
        profile_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text("version = '2.0.0'\n", encoding="utf-8")
        (profile_dir / "default.toml").write_text("version = 2\n", encoding="utf-8")
        raise RuntimeError("install failed after promotion")

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=install_then_fail),
    )

    with pytest.raises(market_bridge._TaskError, match="install failed after promotion"):
        await market_bridge._do_upgrade({}, _payload(), {})

    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == "version = '1.0.0'\n"
    assert (profile_dir / "default.toml").read_text(encoding="utf-8") == "version = 1\n"


@pytest.mark.asyncio
async def test_market_upgrade_preserves_existing_profile_files_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    profile_dir = profiles_root / "demo"
    plugin_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    (profile_dir / "default.toml").write_text("user_value = true\n", encoding="utf-8")
    (profile_dir / "custom.toml").write_text("custom = true\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(
        monkeypatch,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda url, task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda path: None)

    async def install_new(**kwargs: Any) -> dict[str, object]:
        plugin_dir.mkdir(parents=True)
        profile_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text("version = '2.0.0'\n", encoding="utf-8")
        (profile_dir / "default.toml").write_text("package_value = true\n", encoding="utf-8")
        (profile_dir / "new.toml").write_text("new = true\n", encoding="utf-8")
        return {"operation": "upgrade"}

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=install_new),
    )

    await market_bridge._do_upgrade({}, _payload(), {})

    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == "version = '2.0.0'\n"
    assert (profile_dir / "default.toml").read_text(encoding="utf-8") == "user_value = true\n"
    assert (profile_dir / "custom.toml").read_text(encoding="utf-8") == "custom = true\n"
    assert (profile_dir / "new.toml").read_text(encoding="utf-8") == "new = true\n"


@pytest.mark.asyncio
async def test_market_upgrade_uses_package_id_for_profile_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "demo"
    package_id = "demo-package"
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / plugin_id
    profile_dir = profiles_root / package_id
    plugin_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    (profile_dir / "default.toml").write_text("user_value = true\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(
        monkeypatch,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda url, task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda path: None)
    monkeypatch.setattr(
        market_bridge,
        "inspect_package",
        lambda path: SimpleNamespace(package_id=package_id),
        raising=False,
    )

    async def install_new(**kwargs: Any) -> dict[str, object]:
        if profile_dir.exists():
            raise FileExistsError(profile_dir)
        plugin_dir.mkdir(parents=True)
        profile_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text("version = '2.0.0'\n", encoding="utf-8")
        (profile_dir / "new.toml").write_text("new = true\n", encoding="utf-8")
        return {"operation": "upgrade"}

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=install_new),
    )

    await market_bridge._do_upgrade({}, _payload(plugin_id), {})

    assert (profile_dir / "default.toml").read_text(encoding="utf-8") == "user_value = true\n"
    assert (profile_dir / "new.toml").read_text(encoding="utf-8") == "new = true\n"


@pytest.mark.asyncio
async def test_market_upgrade_blocks_package_id_change_and_preserves_old_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    old_profile = profiles_root / "old-package"
    plugin_dir.mkdir(parents=True)
    old_profile.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    (old_profile / "default.toml").write_text("user_value = true\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(
        market_bridge,
        "get_install_source_manager",
        lambda: SimpleNamespace(
            find_active_market_entry=lambda _plugin_id: _entry("demo", "old-package")
        ),
    )
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda _url, _task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
    monkeypatch.setattr(
        market_bridge,
        "inspect_package",
        lambda _path: SimpleNamespace(package_id="new-package"),
    )

    install_called = False

    async def unexpected_install(**_kwargs: Any) -> dict[str, object]:
        nonlocal install_called
        install_called = True
        return {}

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=unexpected_install),
    )

    with pytest.raises(market_bridge._TaskError, match="package id changes are not supported"):
        await market_bridge._do_upgrade({}, _payload(), {})

    assert install_called is False
    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == "version = '1.0.0'\n"
    assert (old_profile / "default.toml").read_text(encoding="utf-8") == "user_value = true\n"


@pytest.mark.asyncio
async def test_market_rollback_marks_restart_failure_as_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task: dict[str, Any] = {"rollback": {"prepared": True}}

    async def fail_restart(plugin_id: str, *, strict: bool) -> bool:
        assert plugin_id == "demo"
        assert strict is False
        return False

    monkeypatch.setattr(market_bridge, "start_plugin_after_upgrade", fail_restart)

    restored = await market_bridge._run_rollback(task, [], True, "demo")

    assert restored is False
    assert task["rollback"]["restored"] is False


@pytest.mark.asyncio
async def test_market_backup_failure_reports_incomplete_when_old_plugin_cannot_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    (plugins_root / "demo").mkdir(parents=True)
    _configure_paths(
        monkeypatch,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda plugin_id: _async_true())
    monkeypatch.setattr(market_bridge, "stop_plugin_for_upgrade", lambda plugin_id: _async_none())
    monkeypatch.setattr(
        market_bridge,
        "start_plugin_after_upgrade",
        lambda plugin_id, strict: _async_false(),
    )
    monkeypatch.setattr(market_bridge.os, "rename", lambda source, target: _raise_permission_error())

    with pytest.raises(market_bridge._TaskError) as exc_info:
        await market_bridge._do_upgrade({}, _payload(), {})

    assert exc_info.value.code == "upgrade_rollback_incomplete"


async def _async_none() -> None:
    return None


async def _async_true() -> bool:
    return True


async def _async_false() -> bool:
    return False


async def _async_value(value: Any) -> Any:
    return value


def _raise_permission_error() -> None:
    raise PermissionError("backup denied")
