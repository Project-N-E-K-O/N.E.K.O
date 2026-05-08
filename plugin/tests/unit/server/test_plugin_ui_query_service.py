from __future__ import annotations

import asyncio

import pytest

from plugin.core.state import state
from plugin.core.ui_manifest import normalize_plugin_ui_manifest
from plugin.server.application import config as config_application
from plugin.server.application.plugins.ui_query_service import (
    _build_plugin_list_actions_from_meta,
    _get_static_ui_config_from_meta,
    PluginUiQueryService,
)


def test_static_ui_config_infers_from_config_path_when_missing(tmp_path) -> None:
    root = tmp_path
    plugin_dir = root / "demo_plugin"
    static_dir = plugin_dir / "static"
    static_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    config = _get_static_ui_config_from_meta({
        "id": "demo",
        "config_path": str(plugin_dir / "plugin.toml"),
    })

    assert config is not None
    assert config["enabled"] is True
    assert config["directory"] == str(static_dir)
    assert config["inferred"] is True


def test_build_plugin_list_actions_infers_open_ui_and_normalizes_custom_actions(tmp_path) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    static_dir = plugin_dir / "static"
    static_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    actions = _build_plugin_list_actions_from_meta(
        "demo",
        {
            "id": "demo",
            "config_path": str(plugin_dir / "plugin.toml"),
            "list_actions": [
                {"id": "docs", "kind": "url", "label": "Docs", "target": "https://example.com/{plugin_id}"},
                {"id": "delete", "kind": "builtin", "confirm_mode": "hold", "danger": True},
                {"id": "broken"},
                "invalid",
            ],
        },
    )

    assert actions == [
        {
            "id": "docs",
            "kind": "url",
            "label": "Docs",
            "target": "https://example.com/demo",
        },
        {
            "id": "delete",
            "kind": "builtin",
            "confirm_mode": "hold",
            "danger": True,
        },
        {
            "id": "open_panel",
            "kind": "route",
            "target": "/plugins/demo?tab=panel",
        },
    ]


def test_surface_context_includes_config_snapshot_only_with_permission(monkeypatch, tmp_path) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    plugin_dir.mkdir()
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [{
                        "id": "main",
                        "entry": "ui/panel.tsx",
                        "permissions": ["state:read", "config:read"],
                    }],
                },
            },
        },
        plugin_id="demo",
    )

    class _ConfigService:
        async def get_plugin_config(self, *, plugin_id: str) -> dict[str, object]:
            assert plugin_id == "demo"
            return {
                "plugin_id": "demo",
                "config": {"plugin": {"id": "demo"}, "feature": {"enabled": True}},
                "last_modified": "2026-01-01T00:00:00",
                "profiles_state": {"config_profiles": {"active": None}},
            }

    monkeypatch.setattr(config_application, "ConfigQueryService", _ConfigService)
    with state.acquire_plugins_write_lock():
        previous = state.plugins.get("demo")
        state.plugins["demo"] = {
            "id": "demo",
            "config_path": str(config_path),
            "plugin_ui": plugin_ui,
            "entries": [],
        }
    try:
        context = asyncio.run(PluginUiQueryService().get_surface_context("demo", kind="panel", surface_id="main"))
    finally:
        with state.acquire_plugins_write_lock():
            if previous is None:
                state.plugins.pop("demo", None)
            else:
                state.plugins["demo"] = previous

    assert context["config"]["value"]["feature"] == {"enabled": True}
    assert context["config"]["readonly"] is True
    assert context["actions"] == []
