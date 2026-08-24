from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.server.routes import market_bridge


pytestmark = pytest.mark.plugin_unit


def _write_plugin(
    root: Path,
    plugin_id: str,
    version: str,
    *,
    directory_name: str | None = None,
) -> Path:
    plugin_dir = root / (directory_name or plugin_id)
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        (
            "[plugin]\n"
            f'id = "{plugin_id}"\n'
            f'version = "{version}"\n'
            f'entry = "plugin.plugins.{plugin_id}:Plugin"\n'
        ),
        encoding="utf-8",
    )
    return plugin_dir


@pytest.mark.asyncio
async def test_installed_projects_user_source_over_builtin_without_renaming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    builtin = _write_plugin(builtin_root, "study_companion", "0.1.5")
    user = _write_plugin(user_root, "study_companion", "0.1.6")
    policy = SimpleNamespace(
        builtin_plugins_root=builtin_root.resolve(),
        user_plugins_root=user_root.resolve(),
    )

    monkeypatch.setattr(market_bridge, "_verify_token", lambda _token: None)
    monkeypatch.setattr(
        market_bridge.PluginCliPathPolicy,
        "from_settings",
        classmethod(lambda cls: policy),
    )
    monkeypatch.setattr(market_bridge, "get_install_source_manager", lambda: None)

    response = await market_bridge.market_installed(token="test")

    assert response.count == 1
    [installed] = response.installed
    assert installed.plugin_id == "study_companion"
    assert installed.path == str(user)
    assert installed.effective_source == "manual"
    assert installed.effective_version == "0.1.6"
    assert installed.builtin_version == "0.1.5"
    assert installed.market_installed is False
    assert "study_companion_1" not in installed.path
    assert builtin.is_dir()


@pytest.mark.asyncio
async def test_installed_projects_builtin_when_no_user_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    builtin = _write_plugin(builtin_root, "study_companion", "0.1.5")
    policy = SimpleNamespace(
        builtin_plugins_root=builtin_root.resolve(),
        user_plugins_root=user_root.resolve(),
    )

    monkeypatch.setattr(market_bridge, "_verify_token", lambda _token: None)
    monkeypatch.setattr(
        market_bridge.PluginCliPathPolicy,
        "from_settings",
        classmethod(lambda cls: policy),
    )
    monkeypatch.setattr(market_bridge, "get_install_source_manager", lambda: None)

    response = await market_bridge.market_installed(token="test")

    [installed] = response.installed
    assert installed.path == str(builtin)
    assert installed.effective_source == "builtin"
    assert installed.effective_version == "0.1.5"
    assert installed.builtin_version == "0.1.5"
    assert installed.market_installed is False


@pytest.mark.asyncio
async def test_installed_ignores_hidden_override_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    builtin = _write_plugin(builtin_root, "study_companion", "0.1.5")
    _write_plugin(
        user_root,
        "study_companion",
        "0.1.6",
        directory_name=".neko_override_staging_test",
    )
    policy = SimpleNamespace(
        builtin_plugins_root=builtin_root.resolve(),
        user_plugins_root=user_root.resolve(),
    )

    monkeypatch.setattr(market_bridge, "_verify_token", lambda _token: None)
    monkeypatch.setattr(
        market_bridge.PluginCliPathPolicy,
        "from_settings",
        classmethod(lambda cls: policy),
    )
    monkeypatch.setattr(market_bridge, "get_install_source_manager", lambda: None)

    response = await market_bridge.market_installed(token="test")

    assert response.count == 1
    [installed] = response.installed
    assert installed.path == str(builtin)
    assert installed.effective_source == "builtin"
