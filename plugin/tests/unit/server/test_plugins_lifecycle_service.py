from __future__ import annotations

from pathlib import Path

import pytest

from plugin.server.application.plugins import lifecycle_service as module


@pytest.mark.plugin_unit
def test_get_plugin_config_path_returns_existing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    config_file = root / "demo" / "plugin.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOT", root)

    resolved = module._get_plugin_config_path("demo")
    assert resolved == config_file.resolve()


@pytest.mark.plugin_unit
@pytest.mark.parametrize("plugin_id", ["../evil", "a/b", "", "  ", "demo..", "demo/"])
def test_get_plugin_config_path_rejects_invalid_plugin_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    plugin_id: str,
) -> None:
    root = tmp_path / "plugins"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOT", root)

    assert module._get_plugin_config_path(plugin_id) is None


@pytest.mark.plugin_unit
def test_get_plugin_config_path_returns_none_for_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOT", root)

    assert module._get_plugin_config_path("demo") is None
