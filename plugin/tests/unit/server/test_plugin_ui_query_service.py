from __future__ import annotations

from plugin.server.application.plugins.ui_query_service import (
    _build_plugin_list_actions_from_meta,
    _get_static_ui_config_from_meta,
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
            "id": "open_ui",
            "kind": "ui",
            "target": "/plugin/demo/ui/",
            "open_in": "new_tab",
        },
    ]
