from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any

import pytest

from plugin.server.routes import market_bridge
from plugin.server.application.plugins.operation_lock import serialized_plugin_operation


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


def _entry(
    plugin_id: str = "demo",
    package_id: str = "",
    *,
    profile_dir: str = "",
    updated_at: str = "",
    version: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        plugin_id=plugin_id,
        directory_name=plugin_id,
        source_detail=SimpleNamespace(version=version, package_sha256="") if version else None,
        package_id=package_id,
        profile_dir=profile_dir,
        updated_at=updated_at,
    )


def test_market_install_request_normalizes_legacy_rename_conflict_policy() -> None:
    request = market_bridge.MarketInstallRequest(
        plugin_id="demo",
        version="1.0.0",
        package_url="https://example.com/demo.neko-plugin",
        package_sha256="a" * 64,
        on_conflict="rename",
    )

    assert request.on_conflict == "fail"


def test_market_override_records_canonical_package_url() -> None:
    canonical_url = "https://github.com/example/demo/releases/download/v1.0.0/demo.neko-plugin"
    request = market_bridge.MarketInstallRequest(
        plugin_id="demo",
        version="1.0.0",
        package_url=f"https://cdn.gh-proxy.org/{canonical_url}",
        canonical_package_url=canonical_url,
        package_sha256="a" * 64,
    )

    override = market_bridge._build_market_override(request, mode="install")

    assert override["market_detail"]["package_url"] == canonical_url


def _configure_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    plugins_root: Path,
    profiles_root: Path,
    entry: SimpleNamespace | None = None,
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
        lambda: SimpleNamespace(
            find_active_market_entry=lambda plugin_id: entry or _entry(plugin_id)
        ),
    )
    monkeypatch.setattr(
        market_bridge,
        "inspect_package",
        lambda path: SimpleNamespace(package_id="demo"),
    )


@pytest.mark.asyncio
async def test_market_upgrade_delegates_file_replacement_to_shared_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(market_bridge, "_download_package", lambda _url, _task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)

    calls: list[dict[str, Any]] = []

    async def shared_replace(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            install_result={"operation": "upgrade"},
            restarted=False,
            rollback_status="not_needed",
            backup_dir=tmp_path / "backup",
        )

    monkeypatch.setattr(market_bridge, "replace_plugin", shared_replace, raising=False)

    task: dict[str, Any] = {}
    await market_bridge._do_upgrade(task, _payload(), {})

    assert len(calls) == 1
    assert calls[0]["layout"].installed_dir == plugin_dir.resolve()
    assert calls[0]["additional_targets"] == (profiles_root / "demo",)
    assert calls[0]["preserve_targets"] == (profiles_root / "demo",)
    assert task["result"] == {
        "operation": "upgrade",
        "restarted": False,
        "rollback_status": "not_needed",
    }


@pytest.mark.asyncio
async def test_market_upgrade_holds_operation_lock_for_entire_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("id = 'demo'\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(market_bridge, "_download_package", lambda _url, _task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)

    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[dict[str, Any]] = []

    async def blocked_replace(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        if len(calls) == 1:
            entered.set()
            await release.wait()
        return SimpleNamespace(
            install_result={"operation": "upgrade"},
            restarted=False,
            rollback_status="not_needed",
            backup_dir=tmp_path / "backup",
        )

    monkeypatch.setattr(market_bridge, "replace_plugin", blocked_replace)
    first = asyncio.create_task(market_bridge._do_upgrade({}, _payload(), {}))
    await entered.wait()
    second = asyncio.create_task(market_bridge._do_upgrade({}, _payload(), {}))
    await asyncio.sleep(0)
    assert len(calls) == 1

    release.set()
    await asyncio.gather(first, second)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_market_upgrade_does_not_hold_operation_lock_while_downloading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("id = 'demo'\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    download_started = asyncio.Event()
    release_download = asyncio.Event()

    async def slow_download(_url: str, _task: dict[str, Any]) -> Path:
        download_started.set()
        await release_download.wait()
        return package_path

    monkeypatch.setattr(market_bridge, "_download_package", slow_download)
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
    monkeypatch.setattr(
        market_bridge,
        "replace_plugin",
        lambda **_kwargs: _async_value(
            SimpleNamespace(
                install_result={"operation": "upgrade"},
                restarted=False,
                rollback_status="not_needed",
                backup_dir=tmp_path / "backup",
            )
        ),
    )

    observed: list[str] = []

    @serialized_plugin_operation
    async def unrelated_operation() -> None:
        observed.append("ran")

    upgrade_task = asyncio.create_task(market_bridge._do_upgrade({}, _payload(), {}))
    await download_started.wait()
    await unrelated_operation()
    assert observed == ["ran"]

    release_download.set()
    await upgrade_task


@pytest.mark.asyncio
async def test_market_upgrade_preserves_profile_at_recorded_custom_location(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    custom_profile_dir = tmp_path / "custom_profiles" / "demo"
    plugin_dir.mkdir(parents=True)
    custom_profile_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("id = 'demo'\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")
    entry = _entry("demo", "demo", profile_dir=str(custom_profile_dir))

    _configure_paths(
        monkeypatch,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
        entry=entry,
    )
    monkeypatch.setattr(market_bridge, "_download_package", lambda _url, _task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
    calls: list[dict[str, Any]] = []
    upload_calls: list[dict[str, Any]] = []

    async def fake_replace(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        await kwargs["install_new"]()
        return SimpleNamespace(
            install_result={"operation": "upgrade"},
            restarted=False,
            rollback_status="not_needed",
            backup_dir=tmp_path / "backup",
        )

    monkeypatch.setattr(market_bridge, "replace_plugin", fake_replace)
    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(
            upload_and_install=lambda **kwargs: (
                upload_calls.append(kwargs) or _async_value({"operation": "upgrade"})
            )
        ),
    )
    await market_bridge._do_upgrade({}, _payload(), {})

    assert calls[0]["additional_targets"] == (custom_profile_dir.resolve(),)
    assert calls[0]["preserve_targets"] == (custom_profile_dir.resolve(),)
    assert upload_calls[0]["profiles_root"] == str(custom_profile_dir.parent)
    assert upload_calls[0]["_allow_external_profiles_root"] is True


@pytest.mark.asyncio
async def test_market_upgrade_rejects_symlinked_recorded_profile_ancestor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    symlinked_ancestor = tmp_path / "recorded_profiles"
    profile_dir = symlinked_ancestor / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("id = 'demo'\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")
    entry = _entry("demo", "demo", profile_dir=str(profile_dir))

    _configure_paths(
        monkeypatch,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
        entry=entry,
    )
    monkeypatch.setattr(market_bridge, "_download_package", lambda _url, _task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
    original_is_symlink = Path.is_symlink

    def _is_symlink(path: Path) -> bool:
        return path == symlinked_ancestor or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", _is_symlink)

    with pytest.raises(market_bridge._TaskError) as exc_info:
        await market_bridge._do_upgrade({}, _payload(), {})

    assert exc_info.value.code == "unsafe_profile_path"


@pytest.mark.asyncio
async def test_market_upgrade_rejects_stale_lock_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_entry = _entry("demo", "demo", updated_at="2026-01-01T00:00:00Z", version="1.0.0")
    updated_entry = _entry("demo", "demo", updated_at="2026-01-02T00:00:00Z", version="2.0.0")
    manager = SimpleNamespace(find_active_market_entry=lambda _plugin_id: updated_entry)

    with pytest.raises(market_bridge._TaskError) as exc_info:
        await market_bridge._replace_market_plugin_transaction(
            manager=manager,
            expected_plugin_id="demo",
            original_entry=first_entry,
            original_entry_fingerprint=market_bridge._market_entry_fingerprint(first_entry),
            installed_package_id="demo",
            replace_kwargs={"layout": object()},
        )

    assert exc_info.value.code == "plugin_upgrade_plan_changed"


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
async def test_market_upgrade_exposes_rollback_while_files_are_being_restored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.application.plugins import upgrade_support

    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda _url, _task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(
            upload_and_install=lambda **_kwargs: _async_raise(RuntimeError("install failed")),
        ),
    )

    rollback_started = asyncio.Event()
    allow_rollback = asyncio.Event()
    remove_directory = upgrade_support.remove_directory

    async def pause_during_rollback(path: Path) -> None:
        rollback_started.set()
        await allow_rollback.wait()
        await remove_directory(path)

    monkeypatch.setattr(upgrade_support, "remove_directory", pause_during_rollback)

    task: dict[str, Any] = {}
    operation = asyncio.create_task(market_bridge._do_upgrade(task, _payload(), {}))
    await asyncio.wait_for(rollback_started.wait(), timeout=1)

    assert task["stage"] == "rollback"
    assert task["rollback"]["running"] is True
    assert task["rollback"]["restored"] is False

    allow_rollback.set()
    with pytest.raises(market_bridge._TaskError) as exc_info:
        await operation

    assert exc_info.value.code == "upgrade_rollback_completed"
    assert task["rollback"]["running"] is False
    assert task["rollback"]["restored"] is True


@pytest.mark.asyncio
async def test_market_upgrade_preserves_install_source_error_after_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda _url, _task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(
            upload_and_install=lambda **_kwargs: _async_raise(
                market_bridge.InstallSourceError("lock_write_failed", "lock is read-only")
            ),
        ),
    )

    task: dict[str, Any] = {}
    with pytest.raises(market_bridge._TaskError) as exc_info:
        await market_bridge._do_upgrade(task, _payload(), {})

    assert exc_info.value.code == "lock_write_failed"
    assert task["rollback"]["running"] is False
    assert task["rollback"]["restored"] is True
    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == "version = '1.0.0'\n"


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
    monkeypatch.setattr(
        market_bridge,
        "get_install_source_manager",
        lambda: SimpleNamespace(
            find_active_market_entry=lambda _plugin_id: _entry(plugin_id, package_id)
        ),
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
async def test_market_upgrade_rejects_legacy_rename_despite_stale_incoming_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    old_profile = profiles_root / "old-package"
    stale_profile = profiles_root / "new-package"
    plugin_dir.mkdir(parents=True)
    old_profile.mkdir(parents=True)
    stale_profile.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    (old_profile / "default.toml").write_text("user_value = true\n", encoding="utf-8")
    (stale_profile / "default.toml").write_text("stale = true\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(
        market_bridge,
        "get_install_source_manager",
        lambda: SimpleNamespace(find_active_market_entry=lambda _plugin_id: _entry("demo", "")),
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

    with pytest.raises(market_bridge._TaskError) as exc_info:
        await market_bridge._do_upgrade({}, _payload(), {})

    assert exc_info.value.code == "package_id_change"
    assert "package id changes are not supported" in str(exc_info.value)
    assert install_called is False
    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == "version = '1.0.0'\n"
    assert (old_profile / "default.toml").read_text(encoding="utf-8") == "user_value = true\n"
    assert (stale_profile / "default.toml").read_text(encoding="utf-8") == "stale = true\n"


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
async def test_market_restart_failure_restores_previous_install_source_entry(
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
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    old_entry = _entry("demo", "demo")

    class FakeManager:
        def __init__(self) -> None:
            self.current = old_entry
            self.restore_calls: list[SimpleNamespace] = []

        def find_active_market_entry(self, _plugin_id: str) -> SimpleNamespace:
            return self.current

        def restore_entry_for_rollback(self, entry: SimpleNamespace) -> None:
            self.restore_calls.append(entry)
            self.current = entry

    manager = FakeManager()
    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(market_bridge, "get_install_source_manager", lambda: manager)
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_true())
    monkeypatch.setattr(
        market_bridge,
        "stop_plugin_for_upgrade",
        lambda _plugin_id: _async_none(),
    )
    monkeypatch.setattr(
        market_bridge,
        "_download_package",
        lambda _url, _task: _async_value(package_path),
    )
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)

    async def install_new(**_kwargs: Any) -> dict[str, object]:
        plugin_dir.mkdir(parents=True)
        profile_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text("version = '2.0.0'\n", encoding="utf-8")
        (profile_dir / "generated.toml").write_text("replacement = true\n", encoding="utf-8")
        manager.current = _entry("demo", "demo")
        manager.current.source_detail = SimpleNamespace(version="2.0.0")
        return {"operation": "upgrade"}

    start_calls = 0

    async def fail_new_start(_plugin_id: str, *, strict: bool) -> bool:
        nonlocal start_calls
        start_calls += 1
        if start_calls == 1:
            raise RuntimeError("replacement start failed")
        return True

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=install_new),
    )
    monkeypatch.setattr(market_bridge, "start_plugin_after_upgrade", fail_new_start)

    with pytest.raises(market_bridge._TaskError) as exc_info:
        await market_bridge._do_upgrade({}, _payload(), {})

    assert exc_info.value.code == "upgrade_rollback_completed"
    assert manager.restore_calls == [old_entry]
    assert manager.current is old_entry
    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == "version = '1.0.0'\n"
    assert (profile_dir / "default.toml").read_text(encoding="utf-8") == "user_value = true\n"
    assert not (profile_dir / "generated.toml").exists()


@pytest.mark.asyncio
async def test_market_backup_failure_reports_incomplete_when_old_plugin_cannot_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
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
        lambda plugin_id, strict: _async_raise(RuntimeError("old plugin restart failed")),
    )
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")
    monkeypatch.setattr(
        market_bridge,
        "_download_package",
        lambda _url, _task: _async_value(package_path),
    )
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
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


async def _async_raise(error: Exception) -> None:
    raise error


def _raise_permission_error() -> None:
    raise PermissionError("backup denied")
