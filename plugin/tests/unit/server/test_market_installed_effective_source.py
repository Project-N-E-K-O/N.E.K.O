from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

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
async def test_installed_keeps_builtin_effective_for_noncanonical_user_conflict(
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
        directory_name="old_study_companion",
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
    assert installed.effective_version == "0.1.5"
    assert installed.builtin_version == "0.1.5"


@pytest.mark.asyncio
async def test_installed_keeps_noncanonical_builtin_over_canonical_user_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    builtin = _write_plugin(
        builtin_root,
        "study_companion",
        "0.1.5",
        directory_name="legacy_builtin",
    )
    _write_plugin(user_root, "study_companion", "0.1.6")
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


@pytest.mark.asyncio
async def test_installed_propagates_enumeration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market_bridge, "_verify_token", lambda _token: None)
    monkeypatch.setattr(
        market_bridge,
        "_plugin_config_roots",
        lambda: (_ for _ in ()).throw(OSError("enumeration failed")),
    )

    with pytest.raises(HTTPException) as caught:
        await market_bridge.market_installed(token="test")

    assert caught.value.status_code == 500
    assert caught.value.detail == "market_installed_enumeration_failed"


@pytest.mark.asyncio
async def test_installed_prefers_canonical_user_among_user_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    _write_plugin(builtin_root, "study_companion", "0.1.5")
    canonical_user = _write_plugin(user_root, "study_companion", "0.1.6")
    _write_plugin(
        user_root,
        "study_companion",
        "9.9.9",
        directory_name="old_study_companion",
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

    [installed] = response.installed
    assert installed.path == str(canonical_user)
    assert installed.effective_source == "manual"
    assert installed.effective_version == "0.1.6"


@pytest.mark.asyncio
async def test_installed_preserves_registry_order_for_user_only_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    legacy_user = _write_plugin(
        user_root,
        "study_companion",
        "0.1.5",
        directory_name="aaa_old",
    )
    _write_plugin(user_root, "study_companion", "0.1.6")
    policy = SimpleNamespace(
        builtin_plugins_root=builtin_root.resolve(),
        user_plugins_root=user_root.resolve(),
    )
    market_entry = market_bridge.LockEntry(
        root_id="user",
        directory_name="study_companion",
        plugin_id="study_companion",
        channel="market",
        reason="user_requested",
        installed_at="2026-08-26T00:00:00.000000Z",
        updated_at="2026-08-26T00:00:00.000000Z",
        last_seen_at="2026-08-26T00:00:00.000000Z",
        source_detail=market_bridge.SourceDetailMarket(
            plugin_market_id="study_companion",
            version="0.1.6",
            package_url="https://example.invalid/study-companion.neko-plugin",
            package_sha256="a" * 64,
            payload_hash=None,
            channel="stable",
            published_at="2026-08-26T00:00:00.000000Z",
        ),
    )
    manager = SimpleNamespace(
        builtin_root=builtin_root.resolve(),
        user_root=user_root.resolve(),
        load=lambda: None,
        snapshot=lambda: SimpleNamespace(entries=(market_entry,)),
    )

    monkeypatch.setattr(market_bridge, "_verify_token", lambda _token: None)
    monkeypatch.setattr(
        market_bridge.PluginCliPathPolicy,
        "from_settings",
        classmethod(lambda cls: policy),
    )
    monkeypatch.setattr(market_bridge, "get_install_source_manager", lambda: manager)

    response = await market_bridge.market_installed(token="test")

    [installed] = response.installed
    assert installed.path == str(legacy_user)
    assert installed.effective_source == "manual"
    assert installed.effective_version == "0.1.5"
    assert installed.market_installed is False
    assert installed.latest_install_source is None


@pytest.mark.asyncio
async def test_installed_reloads_lock_before_projecting_market_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "installations" / "plugins"
    _write_plugin(builtin_root, "study_companion", "0.1.5")
    user = _write_plugin(user_root, "study_companion", "0.1.6")
    policy = SimpleNamespace(
        builtin_plugins_root=builtin_root.resolve(),
        user_plugins_root=user_root.resolve(),
    )
    market_entry = market_bridge.LockEntry(
        root_id="user",
        directory_name="study_companion",
        plugin_id="study_companion",
        channel="market",
        reason="user_requested",
        installed_at="2026-08-26T00:00:00.000000Z",
        updated_at="2026-08-26T00:00:00.000000Z",
        last_seen_at="2026-08-26T00:00:00.000000Z",
        source_detail=market_bridge.SourceDetailMarket(
            plugin_market_id="study_companion",
            version="0.1.6",
            package_url="https://example.invalid/study-companion.neko-plugin",
            package_sha256="a" * 64,
            payload_hash=None,
            channel="stable",
            published_at="2026-08-26T00:00:00.000000Z",
        ),
    )

    class _Manager:
        def __init__(self) -> None:
            self.builtin_root = builtin_root.resolve()
            self.user_root = user_root.resolve()
            self.entries: tuple[market_bridge.LockEntry, ...] = ()
            self.load_calls = 0

        def load(self) -> None:
            self.load_calls += 1
            self.entries = (market_entry,)

        def snapshot(self) -> SimpleNamespace:
            return SimpleNamespace(entries=self.entries)

    manager = _Manager()
    monkeypatch.setattr(market_bridge, "_verify_token", lambda _token: None)
    monkeypatch.setattr(
        market_bridge.PluginCliPathPolicy,
        "from_settings",
        classmethod(lambda cls: policy),
    )
    monkeypatch.setattr(market_bridge, "get_install_source_manager", lambda: manager)

    response = await market_bridge.market_installed(token="test")

    assert manager.load_calls == 1
    [installed] = response.installed
    assert installed.path == str(user)
    assert installed.effective_source == "market"
    assert installed.market_installed is True
    assert installed.latest_install_source is not None
    assert installed.latest_install_source["version"] == "0.1.6"


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
