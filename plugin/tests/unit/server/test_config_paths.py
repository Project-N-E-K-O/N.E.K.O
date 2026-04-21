from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from plugin.server.infrastructure import config_paths as module


@pytest.mark.plugin_unit
def test_get_plugin_config_path_prefers_registered_metadata_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    root.mkdir(parents=True, exist_ok=True)

    actual_config = root / "demo_plugin" / "plugin.toml"
    actual_config.parent.mkdir(parents=True, exist_ok=True)
    actual_config.write_text("[plugin]\nid='demo-plugin'\n", encoding="utf-8")

    plugins_backup = dict(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    try:
        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["demo-plugin"] = {"config_path": str(actual_config)}
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()

        assert module.get_plugin_config_path("demo-plugin") == actual_config.resolve()
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
            module.state.plugin_hosts.update(hosts_backup)


@pytest.mark.plugin_unit
def test_get_plugin_config_path_falls_back_to_directory_lookup_when_unregistered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    config_file = root / "demo" / "plugin.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

    assert module.get_plugin_config_path("demo") == config_file.resolve()


@pytest.mark.plugin_unit
def test_get_plugin_config_path_raises_not_found_for_missing_plugin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

    with pytest.raises(HTTPException) as exc_info:
        module.get_plugin_config_path("missing")

    assert exc_info.value.status_code == 404
