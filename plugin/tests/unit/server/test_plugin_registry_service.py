from __future__ import annotations

import copy
from pathlib import Path

import pytest

from plugin.server.application.plugins import registry_service as module


pytestmark = pytest.mark.plugin_unit


class _AliveHost:
    def is_alive(self) -> bool:
        return True


def _write_plugin_fixture(tmp_path: Path, plugin_id: str) -> Path:
    root = tmp_path / "plugins"
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    module_name = f"{plugin_id}_entry"
    (tmp_path / f"{module_name}.py").write_text(
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
                f"entry = '{module_name}:DemoPlugin'",
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
            "[[plugin.dependency]]",
            "id = 'provider'",
            "untested = '>=0.1.0'",
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
async def test_refresh_registry_registers_duplicate_declared_plugin_ids_with_runtime_suffix(
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
        refreshed_duplicate = await service.refresh_plugin("demo_1")

        assert result["success"] is True
        assert result["failed"] == []
        assert result["added"] == ["demo", "demo_1"]
        assert second_result["success"] is True
        assert second_result["failed"] == []
        assert second_result["added"] == []
        assert second_result["unchanged"] == ["demo", "demo_1"]
        assert refreshed_duplicate["success"] is True
        assert refreshed_duplicate["plugin_id"] == "demo_1"
        assert refreshed_duplicate["status"] == "unchanged"

        with module.state.acquire_plugins_read_lock():
            first_meta = dict(module.state.plugins["demo"])
            second_meta = dict(module.state.plugins["demo_1"])

        assert Path(first_meta["config_path"]).parent.name == "demo"
        assert Path(second_meta["config_path"]).parent.name == "demo_1"
        assert first_meta["id"] == "demo"
        assert second_meta["id"] == "demo_1"
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup
