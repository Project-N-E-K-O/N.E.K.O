from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

from plugin.server.application.plugins import registry_service as module
from plugin.server.application.plugins.inventory_store import (
    mark_plugin_deleted,
    record_user_installation,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure import runtime_overrides


pytestmark = pytest.mark.plugin_unit


@pytest.fixture(autouse=True)
def _isolate_plugin_deletion_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "NEKO_PLUGIN_INSTALLATIONS_PATH",
        str(tmp_path / "plugin-installations.json"),
    )


class _AliveHost:
    def is_alive(self) -> bool:
        return True


def _write_plugin_fixture(tmp_path: Path, plugin_id: str) -> Path:
    root = tmp_path / "plugins"
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    module_name = "entry"
    (plugin_dir / f"{module_name}.py").write_text(
        "\n".join(
            [
                "from plugin.sdk.plugin.decorators import plugin_entry",
                "",
                "class DemoPlugin:",
                "    @plugin_entry(id='ping', name='Ping', description='Ping tool')",
                "    async def ping(self):",
                "        return {'ok': True}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f"id = '{plugin_id}'",
                f"name = '{plugin_id}'",
                "type = 'plugin'",
                f"entry = 'plugin.plugins.{plugin_id}.{module_name}:DemoPlugin'",
                "version = '0.1.0'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


def _write_ordered_plugin_fixture(
    root: Path,
    plugin_id: str,
    *,
    dependencies_block: list[str] | None = None,
) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f"id = '{plugin_id}'",
                f"name = '{plugin_id}'",
                "type = 'plugin'",
                f"entry = '{plugin_id}.module:Plugin'",
                "version = '0.1.0'",
                *(dependencies_block or []),
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return plugin_dir / "plugin.toml"


def _write_package_plugin_fixture(
    root: Path,
    directory_name: str,
    *,
    plugin_id: str | None = None,
    entry_package: str | None = None,
    source: str | None = None,
) -> Path:
    resolved_plugin_id = plugin_id or directory_name
    resolved_entry_package = entry_package or directory_name
    plugin_dir = root / directory_name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text(
        source
        or "\n".join(
            [
                "class DemoPlugin:",
                "    pass",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f"id = '{resolved_plugin_id}'",
                f"name = '{resolved_plugin_id}'",
                "type = 'plugin'",
                f"entry = 'plugins.{resolved_entry_package}:DemoPlugin'",
                "version = '0.1.0'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return plugin_dir / "plugin.toml"


@pytest.mark.asyncio
async def test_refresh_registry_syncs_metadata_and_marks_missing_running_plugin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _write_plugin_fixture(tmp_path, "demo_plugin")

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["stale_plugin"] = {
                "id": "stale_plugin",
                "name": "stale_plugin",
                "config_path": str((tmp_path / "plugins" / "stale_plugin" / "plugin.toml").resolve()),
            }
            module.state.plugins["running_removed"] = {
                "id": "running_removed",
                "name": "running_removed",
                "config_path": str((tmp_path / "plugins" / "running_removed" / "plugin.toml").resolve()),
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
            module.state.plugin_hosts["running_removed"] = _AliveHost()

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

        service = module.PluginRegistryService()
        result = await service.refresh_registry()

        assert result["success"] is True
        assert result["added"] == ["demo_plugin"]
        assert result["removed"] == ["stale_plugin"]
        assert result["removed_running"] == ["running_removed"]

        with module.state.acquire_plugins_read_lock():
            demo_meta = dict(module.state.plugins["demo_plugin"])
            running_removed = dict(module.state.plugins["running_removed"])

        assert demo_meta["runtime_enabled"] is True
        assert demo_meta["runtime_auto_start"] is False
        assert [entry["id"] for entry in demo_meta["entries_preview"]] == ["ping"]
        assert running_removed["runtime_source_missing"] is True
        assert "stale_plugin" not in module.state.plugins
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
            module.state.plugin_hosts.update(hosts_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_refresh_registry_excludes_deleted_plugin_before_entry_import(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    plugin_dir = root / "deleted_demo"
    plugin_dir.mkdir(parents=True)
    import_marker = tmp_path / "imported.txt"
    entry_module = tmp_path / "deleted_demo_entry.py"
    entry_module.write_text(
        "from pathlib import Path\n"
        f"Path({str(import_marker)!r}).write_text('imported', encoding='utf-8')\n"
        "class DemoPlugin:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.toml").write_text(
        "[plugin]\n"
        "id='deleted_demo'\n"
        "name='Deleted Demo'\n"
        "type='plugin'\n"
        "entry='deleted_demo_entry:DemoPlugin'\n"
        "version='0.1.0'\n"
        "[plugin_runtime]\n"
        "enabled=true\n"
        "auto_start=true\n",
        encoding="utf-8",
    )
    state_path = tmp_path / "plugin-installations.json"
    mark_plugin_deleted("deleted_demo", path=state_path)
    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

        result = await module.PluginRegistryService().refresh_registry()

        assert result["success"] is True
        assert result["added"] == []
        assert result["scanned_count"] == 0
        assert import_marker.exists() is False
        assert (plugin_dir / "plugin.toml").is_file()
        with module.state.acquire_plugins_read_lock():
            assert "deleted_demo" not in module.state.plugins
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


def test_discovery_keeps_builtin_root_identity_when_user_roots_are_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    builtin_root = tmp_path / "builtin"
    managed_root = tmp_path / "managed"
    user_root = tmp_path / "user"
    _write_ordered_plugin_fixture(builtin_root, "builtin_demo")
    captured_root_ids: list[str] = []

    def capture_candidates(candidates, *, inventory):
        del inventory
        captured_root_ids.extend(candidate.root_id for candidate in candidates)
        return []

    monkeypatch.setattr(module, "resolve_plugin_candidates", capture_candidates)

    module._discover_registry_snapshot_sync(
        (builtin_root,),
        classification_roots=(builtin_root, managed_root, user_root),
    )

    assert captured_root_ids == ["builtin"]


@pytest.mark.asyncio
async def test_refresh_registry_removes_deleted_mixed_case_plugin_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    root.mkdir()
    plugin_id = "DemoPlugin"
    plugin_dir = tmp_path / "previous-root" / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        "[plugin]\n"
        f"id='{plugin_id}'\n"
        f"name='{plugin_id}'\n"
        "type='plugin'\n"
        "entry='demo_plugin_entry:DemoPlugin'\n"
        "version='0.1.0'\n",
        encoding="utf-8",
    )
    mark_plugin_deleted(plugin_id)
    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins[plugin_id] = {
                "id": plugin_id,
                "config_path": str(plugin_dir / "plugin.toml"),
            }
        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

        result = await module.PluginRegistryService().refresh_registry()

        assert result["removed"] == [plugin_id]
        with module.state.acquire_plugins_read_lock():
            assert plugin_id not in module.state.plugins
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_explicit_user_installation_is_the_only_imported_same_id_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    builtin_dir = builtin_root / "demo"
    user_dir = user_root / "demo"
    builtin_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    builtin_marker = tmp_path / "builtin-imported.txt"
    user_marker = tmp_path / "user-imported.txt"
    (builtin_dir / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(builtin_marker)!r}).write_text('builtin', encoding='utf-8')\n"
        "class DemoPlugin:\n    pass\n",
        encoding="utf-8",
    )
    (user_dir / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(user_marker)!r}).write_text('user', encoding='utf-8')\n"
        "class DemoPlugin:\n    pass\n",
        encoding="utf-8",
    )
    for plugin_dir, entry in (
        (builtin_dir, "plugin.plugins.demo:DemoPlugin"),
        (user_dir, "plugin.plugins.demo:DemoPlugin"),
    ):
        (plugin_dir / "plugin.toml").write_text(
            "[plugin]\n"
            "id='demo'\n"
            "name='Demo'\n"
            "type='plugin'\n"
            f"entry='{entry}'\n"
            "version='1.0.0'\n"
            "[plugin_runtime]\n"
            "enabled=true\n"
            "auto_start=false\n",
            encoding="utf-8",
        )
    record_user_installation(
        "demo",
        directory_name="demo",
        package_id="demo",
        source="market",
        path=tmp_path / "plugin-installations.json",
    )
    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["demo"] = {
                "id": "demo",
                "name": "Demo",
                "config_path": str((builtin_dir / "plugin.toml").resolve()),
                "entry_point": "builtin_demo_entry:DemoPlugin",
            }
        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (builtin_root, user_root))

        result = await module.PluginRegistryService().refresh_registry()

        assert result["success"] is True
        assert result["added"] == []
        assert result["updated"] == ["demo"]
        assert result["removed"] == []
        assert result["resolution_warnings"] == []
        assert user_marker.is_file()
        assert builtin_marker.exists() is False
        with module.state.acquire_plugins_read_lock():
            meta = dict(module.state.plugins["demo"])
        assert Path(str(meta["config_path"])).parent == user_dir.resolve()
        assert "demo_1" not in module.state.plugins
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_running_builtin_blocks_overlay_refresh_without_import_or_suffix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    builtin_dir = builtin_root / "demo"
    user_dir = user_root / "demo"
    builtin_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    for plugin_dir, entry in (
        (builtin_dir, "builtin_running_entry:Plugin"),
        (user_dir, "user_must_not_import_entry:Plugin"),
    ):
        (plugin_dir / "plugin.toml").write_text(
            "[plugin]\n"
            "id='demo'\n"
            "name='Demo'\n"
            "type='plugin'\n"
            f"entry='{entry}'\n"
            "version='1.0.0'\n"
            "[plugin_runtime]\n"
            "enabled=true\n"
            "auto_start=false\n",
            encoding="utf-8",
        )
    record_user_installation(
        "demo",
        directory_name="demo",
        package_id="demo",
        source="market",
        path=tmp_path / "plugin-installations.json",
    )
    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["demo"] = {
                "id": "demo",
                "name": "Demo",
                "config_path": str((builtin_dir / "plugin.toml").resolve()),
                "entry_point": "builtin_running_entry:Plugin",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
            module.state.plugin_hosts["demo"] = _AliveHost()
        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (builtin_root, user_root))

        result = await module.PluginRegistryService().refresh_registry()

        assert result["success"] is False
        assert result["failed"] == [
            {
                "plugin_id": "demo",
                "config_path": "",
                "error": "running plugin prevents activation switch",
            }
        ]
        with module.state.acquire_plugins_read_lock():
            meta = dict(module.state.plugins["demo"])
            assert "demo_1" not in module.state.plugins
        assert Path(str(meta["config_path"])).parent == builtin_dir.resolve()
        assert meta.get("runtime_source_missing") is not True
        assert "user_must_not_import_entry" not in sys.modules
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
            module.state.plugin_hosts.update(hosts_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_refresh_registry_applies_user_auto_start_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _write_plugin_fixture(tmp_path, "remembered_plugin")
    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        runtime_overrides.set_runtime_override(
            "remembered_plugin",
            True,
            auto_start=True,
        )
        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

        await module.PluginRegistryService().refresh_registry()

        with module.state.acquire_plugins_read_lock():
            plugin_meta = dict(module.state.plugins["remembered_plugin"])
        assert plugin_meta["runtime_enabled"] is True
        assert plugin_meta["runtime_auto_start"] is True
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "load_overrides",
    [
        lambda: (_ for _ in ()).throw(
            runtime_overrides.RuntimeOverrideReadError("invalid json")
        ),
        lambda: runtime_overrides._coerce_overrides(
            {
                "manifest_plugin": {
                    "enabled": False,
                    "auto_start": "yes",
                }
            }
        ),
    ],
    ids=("unreadable-file", "invalid-plugin-entry"),
)
async def test_refresh_registry_uses_manifest_defaults_when_overrides_are_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    load_overrides,
) -> None:
    root = _write_plugin_fixture(tmp_path, "manifest_plugin")
    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    monkeypatch.setattr(
        runtime_overrides,
        "_load_from_disk",
        load_overrides,
    )
    runtime_overrides.reset_cache_for_testing()
    monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

    try:
        await module.PluginRegistryService().refresh_registry()

        with module.state.acquire_plugins_read_lock():
            plugin_meta = dict(module.state.plugins["manifest_plugin"])
        assert plugin_meta["runtime_enabled"] is True
        assert plugin_meta["runtime_auto_start"] is False
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_refresh_plugin_returns_updated_status_for_existing_plugin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _write_plugin_fixture(tmp_path, "refresh_me")

    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["refresh_me"] = {
                "id": "refresh_me",
                "name": "Old Name",
                "config_path": str((root / "refresh_me" / "plugin.toml").resolve()),
                "runtime_enabled": True,
                "runtime_auto_start": True,
                "entries_preview": [],
            }

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

        service = module.PluginRegistryService()
        payload = await service.refresh_plugin("refresh_me")

        assert payload["success"] is True
        assert payload["plugin_id"] == "refresh_me"
        assert payload["status"] == "updated"

        with module.state.acquire_plugins_read_lock():
            refreshed = dict(module.state.plugins["refresh_me"])
        assert refreshed["name"] == "refresh_me"
        assert [entry["id"] for entry in refreshed["entries_preview"]] == ["ping"]
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_refresh_plugin_checks_python_requirements_against_vendor_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _write_plugin_fixture(tmp_path, "vendor_refresh")
    plugin_dir = root / "vendor_refresh"
    vendor_dir = plugin_dir / "vendor"
    vendor_dir.mkdir()
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\ndependencies = ["demo-lib>=2"]\n',
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    def _fake_find_missing(requirements, *, search_paths=None):
        seen["requirements"] = list(requirements)
        seen["search_paths"] = list(search_paths or [])
        return []

    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)
    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["vendor_refresh"] = {
                "id": "vendor_refresh",
                "name": "Vendor Refresh",
                "config_path": str((plugin_dir / "plugin.toml").resolve()),
            }

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))
        monkeypatch.setattr(module, "_find_missing_python_requirements", _fake_find_missing)

        payload = await module.PluginRegistryService().refresh_plugin("vendor_refresh")

        assert payload["success"] is True
        assert seen["requirements"] == ["demo-lib>=2"]
        assert seen["search_paths"] == [vendor_dir]
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_refresh_registry_keeps_existing_metadata_when_config_parse_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    plugin_dir = root / "broken_plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin\nid='broken_plugin'\n", encoding="utf-8")

    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["broken_plugin"] = {
                "id": "broken_plugin",
                "name": "Broken Plugin",
                "config_path": str(config_path.resolve()),
                "runtime_enabled": True,
                "runtime_auto_start": False,
            }

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

        service = module.PluginRegistryService()
        result = await service.refresh_registry()

        assert result["success"] is False
        assert result["removed"] == []
        assert result["removed_running"] == []
        assert len(result["failed"]) == 1
        assert result["failed"][0]["config_path"] == str(config_path.resolve())

        with module.state.acquire_plugins_read_lock():
            preserved = dict(module.state.plugins["broken_plugin"])
        assert preserved["name"] == "Broken Plugin"
        assert "runtime_source_missing" not in preserved
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_refresh_registry_marks_syntax_error_plugin_failed_without_aborting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    _write_package_plugin_fixture(root, "healthy_plugin")
    _write_package_plugin_fixture(
        root,
        "broken_plugin",
        source="def broken(:\n    pass\n",
    )

    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

        result = await module.PluginRegistryService().refresh_registry()

        assert result["success"] is True
        with module.state.acquire_plugins_read_lock():
            healthy = dict(module.state.plugins["healthy_plugin"])
            broken = dict(module.state.plugins["broken_plugin"])

        assert healthy.get("runtime_load_state") != "failed"
        assert broken["runtime_load_state"] == "failed"
        assert broken["runtime_load_error_type"] == "SyntaxError"
        assert broken["runtime_load_error_phase"] == "import_module"
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_refresh_registry_marks_entry_directory_mismatch_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    _write_package_plugin_fixture(
        root,
        "repo_file_manager",
        plugin_id="file_manager",
        entry_package="file_manager",
    )

    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

        result = await module.PluginRegistryService().refresh_registry()

        assert result["success"] is True
        with module.state.acquire_plugins_read_lock():
            plugin_meta = dict(module.state.plugins["file_manager"])

        assert plugin_meta["runtime_load_state"] == "failed"
        assert plugin_meta["runtime_load_error_type"] == "PluginEntryDirectoryMismatch"
        assert "repo_file_manager" in plugin_meta["runtime_load_error_message"]
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_refresh_registry_prioritizes_entry_directory_mismatch_before_requirements(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    config_path = _write_package_plugin_fixture(
        root,
        "repo_file_manager",
        plugin_id="file_manager",
        entry_package="file_manager",
    )
    (config_path.parent / "pyproject.toml").write_text(
        '[project]\ndependencies = ["definitely-missing-lib>=1"]\n',
        encoding="utf-8",
    )
    requirements_checked = False

    def _fake_find_missing(requirements, *, search_paths=None):
        nonlocal requirements_checked
        requirements_checked = True
        return ["definitely-missing-lib>=1"]

    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))
        monkeypatch.setattr(module, "_find_missing_python_requirements", _fake_find_missing)

        result = await module.PluginRegistryService().refresh_registry()

        assert result["success"] is True
        assert requirements_checked is False
        with module.state.acquire_plugins_read_lock():
            plugin_meta = dict(module.state.plugins["file_manager"])

        assert plugin_meta["runtime_load_state"] == "failed"
        assert plugin_meta["runtime_load_error_type"] == "PluginEntryDirectoryMismatch"
        assert plugin_meta["runtime_load_error_phase"] == "entry_validation"
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_list_autostart_plugin_ids_uses_dependency_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    provider_config = _write_ordered_plugin_fixture(root, "provider")
    consumer_config = _write_ordered_plugin_fixture(
        root,
        "consumer",
        dependencies_block=[
            "",
            "dependencies = ['provider']",
        ],
    )

    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["consumer"] = {
                "id": "consumer",
                "type": "plugin",
                "config_path": str(consumer_config.resolve()),
                "runtime_enabled": True,
                "runtime_auto_start": True,
            }
            module.state.plugins["provider"] = {
                "id": "provider",
                "type": "plugin",
                "config_path": str(provider_config.resolve()),
                "runtime_enabled": True,
                "runtime_auto_start": True,
            }

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

        service = module.PluginRegistryService()
        ordered = await service.list_autostart_plugin_ids()

        assert ordered == ["provider", "consumer"]
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_refresh_plugin_marks_missing_simple_plugin_dependency_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _write_plugin_fixture(tmp_path, "consumer")
    config_path = root / "consumer" / "plugin.toml"
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'consumer'",
                "name = 'consumer'",
                "type = 'plugin'",
                "entry = 'consumer_entry:DemoPlugin'",
                "version = '0.1.0'",
                "dependencies = ['missing_provider']",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)
    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["consumer"] = {
                "id": "consumer",
                "name": "consumer",
                "config_path": str(config_path.resolve()),
                "runtime_enabled": True,
                "runtime_auto_start": False,
            }

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

        payload = await module.PluginRegistryService().refresh_plugin("consumer")

        assert payload["success"] is True
        with module.state.acquire_plugins_read_lock():
            refreshed = dict(module.state.plugins["consumer"])
        assert refreshed["runtime_load_state"] == "failed"
        assert refreshed["runtime_load_error_type"] == "DependencyCheckFailed"
        assert refreshed["runtime_load_error_phase"] == "dependency_check"
        assert "missing_provider" in refreshed["runtime_load_error_message"]
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_refresh_registry_blocks_duplicate_declared_plugin_ids_without_suffix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    first_dir = root / "demo"
    second_dir = root / "demo_1"
    first_dir.mkdir(parents=True, exist_ok=True)
    second_dir.mkdir(parents=True, exist_ok=True)

    (tmp_path / "demo_entry.py").write_text(
        "\n".join(
            [
                "class DemoPlugin:",
                "    pass",
                "",
            ]
        ),
        encoding="utf-8",
    )
    for plugin_dir in (first_dir, second_dir):
        (plugin_dir / "plugin.toml").write_text(
            "\n".join(
                [
                    "[plugin]",
                    "id = 'demo'",
                    "name = 'demo'",
                    "type = 'plugin'",
                    "entry = 'demo_entry:DemoPlugin'",
                    "version = '0.1.0'",
                    "",
                    "[plugin_runtime]",
                    "enabled = true",
                    "auto_start = false",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

        service = module.PluginRegistryService()
        result = await service.refresh_registry()
        second_result = await service.refresh_registry()

        assert result["success"] is False
        assert result["added"] == []
        assert result["failed"][0]["plugin_id"] == "demo"
        assert result["failed"][0]["error"] == "multiple_unclaimed_installations"
        assert second_result["success"] is False
        assert second_result["added"] == []
        with pytest.raises(ServerDomainError) as exc_info:
            await service.refresh_plugin("demo")
        assert exc_info.value.code == "PLUGIN_RESOLUTION_BLOCKED"
        assert exc_info.value.details["reason"] == "multiple_unclaimed_installations"

        with module.state.acquire_plugins_read_lock():
            assert "demo" not in module.state.plugins
            assert "demo_1" not in module.state.plugins
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_refresh_registry_quarantines_corrupt_inventory_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _write_plugin_fixture(tmp_path, "inventory_recovery_demo")
    inventory_path = tmp_path / "plugin-installations.json"
    inventory_path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

    result = await module.PluginRegistryService().refresh_registry()

    assert "inventory_recovery_demo" not in result["added"]
    assert any(
        failure.get("plugin_id") == "__inventory__"
        and failure.get("error") == "plugin_inventory_quarantined"
        for failure in result["failed"]
    )
    assert any(
        failure.get("plugin_id") == "inventory_recovery_demo"
        and failure.get("error") == "plugin_inventory_unavailable"
        for failure in result["failed"]
    )
    assert not inventory_path.exists()
    quarantined = list(tmp_path.glob("plugin-installations.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{broken"

    second_result = await module.PluginRegistryService().refresh_registry()
    assert "inventory_recovery_demo" not in second_result["added"]
    assert any(
        failure.get("plugin_id") == "inventory_recovery_demo"
        and failure.get("error") == "plugin_inventory_unavailable"
        for failure in second_result["failed"]
    )


@pytest.mark.asyncio
async def test_refresh_registry_preserves_future_inventory_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _write_plugin_fixture(tmp_path, "future_inventory_demo")
    inventory_path = tmp_path / "plugin-installations.json"
    future_payload = json.dumps(
        {
            "schema_version": 3,
            "generation": 17,
            "updated_at": None,
            "installations": [],
            "activation_claims": {},
            "future_field": {"must_survive": True},
        },
        sort_keys=True,
    )
    inventory_path.write_text(future_payload, encoding="utf-8")
    monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

    result = await module.PluginRegistryService().refresh_registry()

    assert "future_inventory_demo" not in result["added"]
    assert any(
        failure.get("plugin_id") == "__inventory__"
        and failure.get("error") == "plugin_inventory_unsupported_schema"
        for failure in result["failed"]
    )
    assert any(
        failure.get("plugin_id") == "future_inventory_demo"
        and failure.get("error") == "plugin_inventory_unavailable"
        for failure in result["failed"]
    )
    assert inventory_path.read_text(encoding="utf-8") == future_payload
    assert not list(tmp_path.glob("plugin-installations.json.corrupt-*"))
