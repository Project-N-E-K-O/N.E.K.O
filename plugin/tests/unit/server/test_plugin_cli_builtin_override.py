from __future__ import annotations

from pathlib import Path
import hashlib
import sys
import zipfile

import pytest

from plugin.neko_plugin_cli.public import build_plugin
from plugin.core.host import evict_cached_plugin_modules
from plugin.server.application.plugin_cli.service import PluginCliService
from plugin.server.application.install_source import (
    InstallSourceError,
    InstallSourceManager,
    PluginDirectoryScanner,
    set_global_manager,
)
from plugin.server.application.plugins import source_switch
from plugin.server.application.plugins.source_switch import SourceSwitchError
from plugin.server.domain.errors import ServerDomainError
from plugin.core.state import state


pytestmark = pytest.mark.plugin_unit


@pytest.fixture
def _restore_study_companion_module_cache():
    plugin_id = "study_companion"
    module_names = (f"plugins.{plugin_id}", f"plugin.plugins.{plugin_id}")
    saved_modules = {
        name: module
        for name, module in sys.modules.items()
        if any(name == root or name.startswith(f"{root}.") for root in module_names)
    }
    saved_parent_children = {
        parent_name: getattr(sys.modules.get(parent_name), plugin_id, None)
        for parent_name in ("plugins", "plugin.plugins")
    }
    try:
        yield
    finally:
        evict_cached_plugin_modules(plugin_id)
        sys.modules.update(saved_modules)
        for parent_name, child_module in saved_parent_children.items():
            parent_module = sys.modules.get(parent_name)
            if parent_module is not None and child_module is not None:
                setattr(parent_module, plugin_id, child_module)


def _write_plugin(root: Path, plugin_id: str, version: str) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        (
            "[plugin]\n"
            f'id = "{plugin_id}"\n'
            f'name = "{plugin_id}"\n'
            f'version = "{version}"\n'
            'type = "plugin"\n'
            f'entry = "plugin.plugins.{plugin_id}:Plugin"\n'
        ),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("class Plugin: pass\n", encoding="utf-8")
    return plugin_dir


def _rewrite_package_member(
    package_path: Path,
    member_name: str,
    replacement: bytes | None,
) -> None:
    rewritten = package_path.with_suffix(package_path.suffix + ".tmp")
    with zipfile.ZipFile(package_path) as source, zipfile.ZipFile(rewritten, "w") as target:
        for info in source.infolist():
            if info.filename == member_name:
                if replacement is not None:
                    target.writestr(info, replacement)
                continue
            target.writestr(info, source.read(info.filename))
    rewritten.replace(package_path)


@pytest.mark.asyncio
async def test_market_builtin_replacement_rejects_market_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = PluginCliService()

    async def plan_install(**_kwargs: object) -> dict[str, object]:
        return {
            "action": "override_builtin",
            "package_id": "study_companion",
            "plugin_id": "study_companion",
            "directory_name": "study_companion",
            "target_version": "0.1.6",
        }

    monkeypatch.setattr(service, "plan_install", plan_install)

    with pytest.raises(ValueError, match="Market replacement version"):
        await service._install_market_builtin_replacement(
            package="unused.neko-plugin",
            profiles_root=None,
            _allow_external_profiles_root=False,
            forced_directory_name="study_companion",
            market_detail={
                "expected_plugin_toml_id": "study_companion",
                "package_sha256": "a" * 64,
                "version": "9.9.9",
            },
            actual_sha256="a" * 64,
        )


@pytest.mark.asyncio
async def test_cli_rejects_unverified_direct_builtin_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "study_companion"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    _write_plugin(builtin_root, plugin_id, "0.1.5")
    packages_root.mkdir(parents=True)
    package = packages_root / f"{plugin_id}.neko-plugin"
    build_plugin(_write_plugin(tmp_path / "source", plugin_id, "0.1.6"), package)

    import plugin.settings as settings

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", tmp_path / "state")

    service = PluginCliService()
    plan = await service.plan_install(package=str(package))
    assert plan["action"] == "override_builtin"

    with pytest.raises(ServerDomainError) as exc_info:
        await service.install(
            package=str(package),
            confirm_upgrade=True,
            confirmation_token=str(plan["confirmation_token"]),
        )

    assert exc_info.value.code == "PLUGIN_BUILTIN_OVERRIDE_MARKET_REQUIRED"
    assert not (user_root / plugin_id).exists()


@pytest.mark.asyncio
async def test_builtin_override_plan_blocks_existing_incoming_profile_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "study_companion"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    _write_plugin(builtin_root, plugin_id, "0.1.5")
    packages_root.mkdir(parents=True)
    package = packages_root / f"{plugin_id}.neko-plugin"
    build_plugin(_write_plugin(tmp_path / "source", plugin_id, "0.1.6"), package)
    retained_profile = profiles_root / plugin_id
    retained_profile.mkdir(parents=True)
    retained_state = retained_profile / "settings.toml"
    retained_state.write_text("[settings]\nkeep = true\n", encoding="utf-8")

    import plugin.settings as settings

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", tmp_path / "state")

    plan = await PluginCliService().plan_install(package=str(package))

    assert plan["action"] == "blocked"
    assert plan["reason"] == "override_profile_target_exists"
    assert retained_state.read_text(encoding="utf-8") == "[settings]\nkeep = true\n"
    assert not user_root.exists()


@pytest.mark.asyncio
async def test_install_plan_fails_closed_when_exec_and_state_roots_collide(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_root = tmp_path / "plugins"
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package = packages_root / "demo.neko-plugin"
    build_plugin(_write_plugin(tmp_path / "source", "demo", "1.0.0"), package)

    import plugin.settings as settings

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", shared_root)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", shared_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", tmp_path / "profiles")

    with pytest.raises(ServerDomainError) as exc_info:
        await PluginCliService().plan_install(package=str(package))

    assert exc_info.value.code == "PLUGIN_EXEC_STATE_ROOT_COLLISION"
    assert not shared_root.exists()


@pytest.mark.asyncio
async def test_install_plan_fails_before_write_when_exec_and_profile_roots_collide(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_root = tmp_path / "managed"
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package = packages_root / "demo.neko-plugin"
    build_plugin(_write_plugin(tmp_path / "source", "demo", "1.0.0"), package)

    import plugin.settings as settings

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", shared_root)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", shared_root)

    with pytest.raises(ServerDomainError) as exc_info:
        await PluginCliService().plan_install(package=str(package))

    assert exc_info.value.code == "PLUGIN_EXEC_STATE_ROOT_COLLISION"
    assert not shared_root.exists()


def _market_override(
    *,
    plugin_id: str,
    version: str,
    package_sha256: str,
    mode: str = "override_builtin",
    directory_name: str | None = None,
) -> dict[str, object]:
    import plugin.settings as settings

    override: dict[str, object] = {
        "channel": "market",
        "mode": mode,
        "market_detail": {
            "plugin_market_id": plugin_id,
            "version": version,
            "package_url": "https://example.invalid/study_companion.neko-plugin",
            "channel": "stable",
            "package_sha256": package_sha256,
            "payload_hash": None,
            "published_at": "2026-08-24T00:00:00.000000Z",
            "expected_plugin_toml_id": plugin_id,
        },
    }
    builtin_manifest = Path(settings.BUILTIN_PLUGIN_CONFIG_ROOT) / plugin_id / "plugin.toml"
    if mode == "override_builtin" and builtin_manifest.is_file():
        override["override_confirmation"] = {
            "builtin_manifest_sha256": hashlib.sha256(
                builtin_manifest.read_bytes()
            ).hexdigest(),
        }
    if directory_name is not None:
        override["directory_name"] = directory_name
    return override


@pytest.mark.asyncio
async def test_builtin_override_rejects_builtin_changed_after_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "study_companion"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    builtin = _write_plugin(builtin_root, plugin_id, "0.1.5")
    packages_root.mkdir(parents=True)
    package = packages_root / f"{plugin_id}.neko-plugin"
    build_plugin(_write_plugin(tmp_path / "source", plugin_id, "0.1.6"), package)
    package_sha256 = hashlib.sha256(package.read_bytes()).hexdigest()

    import plugin.settings as settings

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", tmp_path / "state")
    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    set_global_manager(manager)
    confirmed_override = _market_override(
        plugin_id=plugin_id,
        version="0.1.6",
        package_sha256=package_sha256,
    )
    (builtin / "plugin.toml").write_text(
        (
            "[plugin]\n"
            f'id = "{plugin_id}"\n'
            'version = "0.1.5-hotfix"\n'
            f'entry = "plugin.plugins.{plugin_id}:Plugin"\n'
        ),
        encoding="utf-8",
    )

    try:
        with pytest.raises(ServerDomainError) as exc_info:
            await PluginCliService().install_builtin_override(
                package=str(package),
                market_override=confirmed_override,
            )

        assert exc_info.value.code == "OVERRIDE_CONFIRMATION_CHANGED"
        assert not user_root.exists()
    finally:
        set_global_manager(None)


@pytest.mark.asyncio
async def test_disabled_builtin_override_with_invalid_entry_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _restore_study_companion_module_cache: None,
) -> None:
    plugin_id = "study_companion"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    builtin = _write_plugin(builtin_root, plugin_id, "0.1.5")
    source = _write_plugin(tmp_path / "source", plugin_id, "0.1.6")
    with (source / "plugin.toml").open("a", encoding="utf-8") as manifest:
        manifest.write("\n[plugin_runtime]\nenabled = false\n")
    (source / "__init__.py").write_text(
        "class DifferentPlugin: pass\n",
        encoding="utf-8",
    )
    packages_root.mkdir(parents=True)
    package = packages_root / f"{plugin_id}.neko-plugin"
    build_plugin(source, package)
    package_sha256 = hashlib.sha256(package.read_bytes()).hexdigest()

    import plugin.settings as settings
    from plugin.server.application.plugins import lifecycle_service

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", tmp_path / "state")
    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    set_global_manager(manager)

    async def refresh_registry() -> dict[str, object]:
        effective = user_root / plugin_id if (user_root / plugin_id).is_dir() else builtin
        with state.acquire_plugins_write_lock():
            state.plugins[plugin_id] = {
                "config_path": str(effective / "plugin.toml"),
                "runtime_enabled": not (user_root / plugin_id).is_dir(),
            }
        return {"ok": True}

    async def is_running(_plugin_id: str) -> bool:
        return False

    async def no_op(_plugin_id: str, strict: bool | None = None) -> None:
        _ = strict

    monkeypatch.setattr(lifecycle_service.plugin_registry_service, "refresh_registry", refresh_registry)
    monkeypatch.setattr(source_switch, "plugin_is_running_for_source_switch", is_running)
    monkeypatch.setattr(source_switch, "stop_plugin_for_source_switch", no_op)
    monkeypatch.setattr(
        source_switch,
        "start_plugin_for_source_switch",
        no_op,
    )

    try:
        with pytest.raises(SourceSwitchError) as exc_info:
            await PluginCliService().install_builtin_override(
                package=str(package),
                market_override=_market_override(
                    plugin_id=plugin_id,
                    version="0.1.6",
                    package_sha256=package_sha256,
                ),
            )

        assert exc_info.value.stage == "validate_promoted_source"
        assert exc_info.value.rollback_code == "override_rollback_completed"
        assert "AttributeError during import_class" in str(exc_info.value.cause)
        assert not (user_root / plugin_id).exists()
        assert (builtin / "plugin.toml").is_file()
        assert manager.find_active_market_entry(plugin_id) is None
        assert f"plugins.{plugin_id}" not in sys.modules
        assert f"plugin.plugins.{plugin_id}" not in sys.modules
    finally:
        set_global_manager(None)
        with state.acquire_plugins_write_lock():
            state.plugins.pop(plugin_id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_market_start", "payload_metadata"),
    [
        (False, "valid"),
        (True, "valid"),
        (False, "missing"),
        (False, "mismatch"),
    ],
)
async def test_market_builtin_override_switches_or_restores_without_touching_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _restore_study_companion_module_cache: None,
    fail_market_start: bool,
    payload_metadata: str,
) -> None:
    plugin_id = "study_companion"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    state_root = tmp_path / "plugins"
    builtin = _write_plugin(builtin_root, plugin_id, "0.1.5")
    packages_root.mkdir(parents=True)
    package = packages_root / f"{plugin_id}.neko-plugin"
    build_plugin(_write_plugin(tmp_path / "source", plugin_id, "0.1.6"), package)
    if payload_metadata == "missing":
        _rewrite_package_member(package, "metadata.toml", None)
    elif payload_metadata == "mismatch":
        _rewrite_package_member(
            package,
            f"payload/plugins/{plugin_id}/__init__.py",
            b"class Plugin: pass\n# tampered after embedded hash\n",
        )
    package_sha256 = hashlib.sha256(package.read_bytes()).hexdigest()
    state_files = {
        state_root / plugin_id / "data" / "study.db": b"sqlite-db",
        state_root / plugin_id / "data" / "study.db-wal": b"sqlite-wal",
        state_root / plugin_id / "data" / "study.db-shm": b"sqlite-shm",
    }
    for path, content in state_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    before_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in state_files}

    import plugin.settings as settings
    from plugin.server.application.plugins import lifecycle_service

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", state_root)

    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    set_global_manager(manager)
    start_calls: list[str] = []

    async def refresh_registry() -> dict[str, object]:
        effective = user_root / plugin_id if (user_root / plugin_id).is_dir() else builtin
        with state.acquire_plugins_write_lock():
            state.plugins[plugin_id] = {
                "config_path": str(effective / "plugin.toml"),
                "status": "stopped",
            }
        return {"ok": True}

    async def is_running(_plugin_id: str) -> bool:
        return True

    async def stop(_plugin_id: str) -> None:
        return None

    async def start(_plugin_id: str) -> None:
        start_calls.append(_plugin_id)
        if fail_market_start and len(start_calls) == 1 and (user_root / plugin_id).exists():
            raise RuntimeError("market start failed")

    monkeypatch.setattr(lifecycle_service.plugin_registry_service, "refresh_registry", refresh_registry)
    monkeypatch.setattr(source_switch, "plugin_is_running_for_source_switch", is_running)
    monkeypatch.setattr(source_switch, "stop_plugin_for_source_switch", stop)
    monkeypatch.setattr(source_switch, "start_plugin_for_source_switch", start)

    try:
        service = PluginCliService()
        if payload_metadata == "mismatch":
            with pytest.raises(ValueError, match="payload hash mismatch"):
                await service.install_builtin_override(
                    package=str(package),
                    market_override=_market_override(
                        plugin_id=plugin_id,
                        version="0.1.6",
                        package_sha256=package_sha256,
                    ),
                )
            assert not (user_root / plugin_id).exists()
            assert start_calls == []
        elif fail_market_start:
            with pytest.raises(SourceSwitchError) as exc_info:
                await service.install_builtin_override(
                    package=str(package),
                    market_override=_market_override(
                        plugin_id=plugin_id,
                        version="0.1.6",
                        package_sha256=package_sha256,
                    ),
                )
            assert exc_info.value.code == "override_start_failed"
            assert exc_info.value.rollback_code == "override_rollback_completed"
            assert not (user_root / plugin_id).exists()
            assert (builtin / "plugin.toml").is_file()
            assert start_calls == [plugin_id, plugin_id]
        else:
            result = await service.install_builtin_override(
                package=str(package),
                market_override=_market_override(
                    plugin_id=plugin_id,
                    version="0.1.6",
                    package_sha256=package_sha256,
                ),
            )
            assert result["operation"] == "override_builtin"
            assert result["previous_version"] == "0.1.5"
            assert result["payload_hash_verified"] is (
                True if payload_metadata == "valid" else None
            )
            assert result["restarted"] is True
            assert (user_root / plugin_id / "plugin.toml").is_file()
            assert (builtin / "plugin.toml").is_file()
            entry = manager.find_active_market_entry(plugin_id)
            assert entry is not None
            assert entry.directory_name == plugin_id
            assert start_calls == [plugin_id]
        after_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in state_files}
        assert after_hashes == before_hashes
        assert not list(user_root.glob(".neko_override_*"))
    finally:
        set_global_manager(None)
        with state.acquire_plugins_write_lock():
            state.plugins.pop(plugin_id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_case", "expected_plugin_id"),
    [
        ("missing", None),
        ("null", None),
        ("empty", ""),
        ("whitespace", "   "),
        ("mismatch", "other_plugin"),
    ],
)
async def test_market_builtin_override_rejects_missing_or_invalid_expected_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity_case: str,
    expected_plugin_id: str | None,
) -> None:
    plugin_id = "study_companion"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    _write_plugin(builtin_root, plugin_id, "0.1.5")
    packages_root.mkdir(parents=True)
    package = packages_root / f"{plugin_id}.neko-plugin"
    build_plugin(_write_plugin(tmp_path / "source", plugin_id, "0.1.6"), package)
    package_sha256 = hashlib.sha256(package.read_bytes()).hexdigest()

    import plugin.settings as settings

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", tmp_path / "state")

    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    set_global_manager(manager)
    service = PluginCliService()
    staging_calls: list[str] = []

    def stage_override(**_kwargs: object) -> None:
        staging_calls.append("stage")

    monkeypatch.setattr(service, "_stage_builtin_override_sync", stage_override)
    override = _market_override(
        plugin_id=plugin_id,
        version="0.1.6",
        package_sha256=package_sha256,
    )
    market_detail = override["market_detail"]
    assert isinstance(market_detail, dict)
    if identity_case == "missing":
        market_detail.pop("expected_plugin_toml_id")
    else:
        market_detail["expected_plugin_toml_id"] = expected_plugin_id

    try:
        with pytest.raises(ValueError, match="Market plugin identity"):
            await service.install_builtin_override(
                package=str(package),
                market_override=override,
            )

        assert staging_calls == []
        assert not user_root.exists()
        assert not profiles_root.exists()
    finally:
        set_global_manager(None)


@pytest.mark.asyncio
async def test_market_builtin_override_rejects_market_version_mismatch_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "study_companion"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    _write_plugin(builtin_root, plugin_id, "0.1.5")
    packages_root.mkdir(parents=True)
    package = packages_root / f"{plugin_id}.neko-plugin"
    build_plugin(_write_plugin(tmp_path / "source", plugin_id, "0.1.6"), package)
    package_sha256 = hashlib.sha256(package.read_bytes()).hexdigest()

    import plugin.settings as settings

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", tmp_path / "state")

    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    set_global_manager(manager)
    service = PluginCliService()
    staging_calls: list[str] = []

    def stage_override(**_kwargs: object) -> None:
        staging_calls.append("stage")

    monkeypatch.setattr(service, "_stage_builtin_override_sync", stage_override)

    try:
        with pytest.raises(ValueError, match="Market plugin version"):
            await service.install_builtin_override(
                package=str(package),
                market_override=_market_override(
                    plugin_id=plugin_id,
                    version="9.9.9",
                    package_sha256=package_sha256,
                ),
            )

        assert staging_calls == []
        assert not user_root.exists()
        assert manager.find_active_market_entry(plugin_id) is None
    finally:
        set_global_manager(None)


@pytest.mark.asyncio
async def test_market_builtin_override_rejects_read_only_lock_before_staging_or_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "study_companion"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    builtin = _write_plugin(builtin_root, plugin_id, "0.1.5")
    package = packages_root / f"{plugin_id}.neko-plugin"
    package.parent.mkdir(parents=True)
    build_plugin(_write_plugin(tmp_path / "source", plugin_id, "0.1.6"), package)
    package_sha256 = hashlib.sha256(package.read_bytes()).hexdigest()
    builtin_before = (builtin / "plugin.toml").read_bytes()

    import plugin.settings as settings

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", tmp_path / "state")

    unreadable_lock = tmp_path / "plugins.lock.json"
    unreadable_lock.mkdir()
    manager = InstallSourceManager(
        lock_path=unreadable_lock,
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    manager.load()
    assert manager.is_degraded is True
    set_global_manager(manager)
    runtime_calls: list[str] = []
    staging_calls: list[str] = []

    async def is_running(_plugin_id: str) -> bool:
        runtime_calls.append("is_running")
        return True

    async def stop(_plugin_id: str) -> None:
        runtime_calls.append("stop")

    monkeypatch.setattr(source_switch, "plugin_is_running_for_source_switch", is_running)
    monkeypatch.setattr(source_switch, "stop_plugin_for_source_switch", stop)
    service = PluginCliService()

    def stage_override(**_kwargs: object) -> None:
        staging_calls.append("stage")

    monkeypatch.setattr(service, "_stage_builtin_override_sync", stage_override)

    try:
        with pytest.raises(ServerDomainError) as exc_info:
            await service.install_builtin_override(
                package=str(package),
                market_override=_market_override(
                    plugin_id=plugin_id,
                    version="0.1.6",
                    package_sha256=package_sha256,
                ),
            )

        assert exc_info.value.code == "INSTALL_SOURCE_READ_ONLY"
        assert exc_info.value.status_code == 503
        assert runtime_calls == []
        assert staging_calls == []
        assert not user_root.exists()
        assert not profiles_root.exists()
        assert (builtin / "plugin.toml").read_bytes() == builtin_before
    finally:
        set_global_manager(None)


@pytest.mark.asyncio
async def test_upload_and_install_routes_override_mode_to_source_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin.settings as settings

    packages_root = tmp_path / "packages"
    user_root = tmp_path / "installations" / "plugins"
    profiles_root = tmp_path / "profiles"
    source_package = tmp_path / "download" / "study_companion.neko-plugin"
    source_package.parent.mkdir()
    source_package.write_bytes(b"verified-package")
    package_sha256 = hashlib.sha256(source_package.read_bytes()).hexdigest()
    target_dir = user_root / "study_companion"

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", tmp_path / "state")

    service = PluginCliService()
    calls: list[dict[str, object]] = []

    async def install_override(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "package_path": str(packages_root / source_package.name),
            "package_type": "plugin",
            "package_id": "study_companion",
            "plugins_root": str(user_root),
            "profiles_root": str(profiles_root),
            "installed_plugins": [
                {
                    "source_folder": "study_companion",
                    "target_plugin_id": "study_companion",
                    "target_dir": str(target_dir),
                    "renamed": False,
                }
            ],
            "profile_dir": None,
            "metadata_found": True,
            "payload_hash": "b" * 64,
            "payload_hash_verified": True,
            "conflict_strategy": "fail",
            "installed_plugin_count": 1,
            "operation": "override_builtin",
            "restarted": True,
            "rollback_status": "not_needed",
            "previous_version": "0.1.5",
            "install_source_warning": None,
        }

    monkeypatch.setattr(service, "install_builtin_override", install_override)
    result = await service.upload_and_install(
        filename=source_package.name,
        package_path=str(source_package),
        install_source_override=_market_override(
            plugin_id="study_companion",
            version="0.1.6",
            package_sha256=package_sha256,
        ),
    )

    assert len(calls) == 1
    assert calls[0]["market_override"]["mode"] == "override_builtin"
    assert result["unpack"]["operation"] == "override_builtin"
    assert result["unpack"]["restarted"] is True
    assert result["install"]["channel"] == "market"
    assert result["install"]["version"] == "0.1.6"
    assert result["install"]["previous_version"] == "0.1.5"


@pytest.mark.asyncio
async def test_committed_override_is_not_deleted_when_response_composition_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin.settings as settings

    packages_root = tmp_path / "packages"
    user_root = tmp_path / "installations" / "plugins"
    profiles_root = tmp_path / "profiles"
    source_package = tmp_path / "download" / "study_companion.neko-plugin"
    source_package.parent.mkdir()
    source_package.write_bytes(b"verified-package")
    package_sha256 = hashlib.sha256(source_package.read_bytes()).hexdigest()
    target_dir = user_root / "study_companion"
    profile_dir = profiles_root / "study_companion"

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", tmp_path / "state")

    service = PluginCliService()

    async def install_override(**_kwargs: object) -> dict[str, object]:
        target_dir.mkdir(parents=True)
        (target_dir / "plugin.toml").write_text(
            "[plugin]\nid='study_companion'\n",
            encoding="utf-8",
        )
        profile_dir.mkdir(parents=True)
        (profile_dir / "default.toml").write_text("enabled=true\n", encoding="utf-8")
        return {
            "package_path": str(packages_root / source_package.name),
            "package_type": "plugin",
            "package_id": "study_companion",
            "plugins_root": str(user_root),
            "profiles_root": str(profiles_root),
            "installed_plugins": [
                {
                    "source_folder": "study_companion",
                    "target_plugin_id": "study_companion",
                    "target_dir": str(target_dir),
                    "renamed": False,
                }
            ],
            "profile_dir": str(profile_dir),
            "metadata_found": True,
            "payload_hash": "b" * 64,
            "payload_hash_verified": True,
            "conflict_strategy": "fail",
            "installed_plugin_count": 1,
            "operation": "override_builtin",
            "restarted": True,
            "rollback_status": "not_needed",
            "previous_version": "0.1.5",
            "install_source_warning": None,
        }

    monkeypatch.setattr(service, "install_builtin_override", install_override)
    monkeypatch.setattr(
        service,
        "_compose_install_result",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("compose failed")),
    )

    with pytest.raises(RuntimeError, match="compose failed"):
        await service.upload_and_install(
            filename=source_package.name,
            package_path=str(source_package),
            install_source_override=_market_override(
                plugin_id="study_companion",
                version="0.1.6",
                package_sha256=package_sha256,
            ),
        )

    assert (target_dir / "plugin.toml").is_file()
    assert (profile_dir / "default.toml").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["install", "upgrade", "reinstall"])
async def test_market_mutations_reject_degraded_lock_before_package_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    import plugin.settings as settings

    packages_root = tmp_path / "packages"
    user_root = tmp_path / "installations" / "plugins"
    lock_path = tmp_path / "plugins.lock.json"
    lock_path.mkdir()
    manager = InstallSourceManager(
        lock_path=lock_path,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        scanner=PluginDirectoryScanner(tmp_path / "builtin", user_root),
    )
    manager.load()
    assert manager.is_degraded
    with pytest.raises(InstallSourceError) as manager_error:
        manager.record_market_install(
            root_id="user",
            directory_name="study_companion",
            plugin_id="study_companion",
            package_id="study_companion",
            market_detail={
                "plugin_market_id": "study_companion",
                "version": "0.1.6",
                "package_url": "https://example.invalid/study_companion.neko-plugin",
                "package_sha256": "a" * 64,
            },
        )
    assert manager_error.value.code == "INSTALL_SOURCE_READ_ONLY"
    set_global_manager(manager)

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", tmp_path / "state")

    try:
        with pytest.raises(ServerDomainError) as exc_info:
            await PluginCliService().upload_and_install(
                filename="study_companion.neko-plugin",
                content=b"package",
                install_source_override=_market_override(
                    plugin_id="study_companion",
                    version="0.1.6",
                    package_sha256=hashlib.sha256(b"package").hexdigest(),
                    mode=mode,
                    directory_name="study_companion" if mode != "install" else None,
                ),
            )

        assert exc_info.value.code == "INSTALL_SOURCE_READ_ONLY"
        assert exc_info.value.status_code == 503
        assert not packages_root.exists()
    finally:
        set_global_manager(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["upgrade", "reinstall"])
async def test_market_replaces_existing_builtin_override_after_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """The replace transaction may temporarily hide the user override."""

    plugin_id = "study_companion"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    _write_plugin(builtin_root, plugin_id, "0.1.5")
    incoming_package = packages_root / f"{plugin_id}.neko-plugin"
    incoming_package.parent.mkdir()
    build_plugin(
        _write_plugin(tmp_path / "source", plugin_id, "0.1.7"),
        incoming_package,
    )
    package_sha256 = hashlib.sha256(incoming_package.read_bytes()).hexdigest()

    import plugin.settings as settings

    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", tmp_path / "state")

    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    manager.record_market_install(
        root_id="user",
        directory_name=plugin_id,
        plugin_id=plugin_id,
        package_id=plugin_id,
        market_detail={
            "plugin_market_id": plugin_id,
            "version": "0.1.6",
            "package_url": "https://example.invalid/study_companion-0.1.6.neko-plugin",
            "channel": "stable",
            "package_sha256": "a" * 64,
            "payload_hash": None,
            "published_at": "2026-08-23T00:00:00.000000Z",
        },
    )
    set_global_manager(manager)

    try:
        assert not (user_root / plugin_id).exists()
        plan = await PluginCliService().plan_install(package=str(incoming_package))
        assert plan["action"] == "override_builtin"

        result = await PluginCliService().upload_and_install(
            filename=incoming_package.name,
            package_path=str(incoming_package),
            on_conflict="fail",
            install_source_override=_market_override(
                plugin_id=plugin_id,
                version="0.1.7",
                package_sha256=package_sha256,
                mode=mode,
                directory_name=plugin_id,
            ),
        )

        assert result["install"]["version"] == "0.1.7"
        assert result["install"]["previous_version"] == "0.1.6"
        assert 'version = "0.1.7"' in (
            user_root / plugin_id / "plugin.toml"
        ).read_text(encoding="utf-8")
        entry = manager.find_active_market_entry(plugin_id)
        assert entry is not None
        assert entry.root_id == "user"
        assert entry.directory_name == plugin_id
    finally:
        set_global_manager(None)
        with state.acquire_plugins_write_lock():
            state.plugins.pop(plugin_id, None)

    with state.acquire_plugins_read_lock():
        assert plugin_id not in state.plugins
