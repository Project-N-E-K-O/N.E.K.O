from __future__ import annotations

from pathlib import Path

from plugin.core.ui_manifest import normalize_plugin_ui_manifest
from plugin.server.application.plugins.ui_query_service import (
    _build_plugin_list_actions_from_meta,
)


def _action(actions: list[dict[str, object]], action_id: str) -> dict[str, object]:
    return next(action for action in actions if action["id"] == action_id)


def test_legacy_static_ui_keeps_open_ui_action_after_runtime_actions_are_lost(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "demo"
    static_dir = plugin_dir / "static"
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    startup_meta = {
        "id": "demo",
        "config_path": str(config_path),
        "list_actions": [
            {
                "id": "open_ui",
                "kind": "ui",
                "target": "/plugin/demo/ui/",
                "open_in": "new_tab",
            }
        ],
    }
    refreshed_meta = {
        "id": "demo",
        "config_path": str(config_path),
    }

    startup_actions = _build_plugin_list_actions_from_meta("demo", startup_meta)
    refreshed_actions = _build_plugin_list_actions_from_meta("demo", refreshed_meta)

    assert _action(refreshed_actions, "open_ui") == _action(
        startup_actions, "open_ui"
    )
    assert _action(refreshed_actions, "open_ui") == {
        "id": "open_ui",
        "kind": "ui",
        "target": "/plugin/demo/ui/",
        "open_in": "new_tab",
    }
    assert _action(refreshed_actions, "open_panel")["target"] == (
        "/plugins/demo?tab=panel"
    )


def test_manifest_static_ui_uses_available_surface_ui_path(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    static_dir = plugin_dir / "static"
    static_dir.mkdir(parents=True)
    (static_dir / "dashboard.html").write_text("<html></html>", encoding="utf-8")
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [
                        {
                            "id": "dashboard",
                            "mode": "static",
                            "entry": "static/dashboard.html",
                        }
                    ]
                }
            }
        },
        plugin_id="demo",
    )

    actions = _build_plugin_list_actions_from_meta(
        "demo",
        {
            "id": "demo",
            "config_path": str(config_path),
            "plugin_ui": plugin_ui,
        },
    )

    assert _action(actions, "open_ui") == {
        "id": "open_ui",
        "kind": "ui",
        "target": "/plugin/demo/ui/dashboard.html",
        "open_in": "new_tab",
    }
    assert {action["id"] for action in actions} == {"open_ui", "open_panel"}


def test_explicit_open_ui_wins_without_duplicate_and_route_actions_remain(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "demo"
    static_dir = plugin_dir / "static"
    docs_dir = plugin_dir / "docs"
    static_dir.mkdir(parents=True)
    docs_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (docs_dir / "guide.md").write_text("# Guide", encoding="utf-8")
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "guide": [
                        {
                            "id": "guide",
                            "mode": "markdown",
                            "entry": "docs/guide.md",
                        }
                    ]
                }
            }
        },
        plugin_id="demo",
    )

    actions = _build_plugin_list_actions_from_meta(
        "demo",
        {
            "id": "demo",
            "config_path": str(config_path),
            "plugin_ui": plugin_ui,
            "list_actions": [
                {
                    "id": "open_ui",
                    "kind": "url",
                    "target": "https://example.test/custom-ui",
                    "open_in": "same_tab",
                }
            ],
        },
    )

    assert [action["id"] for action in actions].count("open_ui") == 1
    assert _action(actions, "open_ui") == {
        "id": "open_ui",
        "kind": "url",
        "target": "https://example.test/custom-ui",
        "open_in": "same_tab",
    }
    assert {action["id"] for action in actions} == {
        "open_ui",
        "open_panel",
        "open_guide",
    }


def test_non_static_ui_does_not_infer_open_ui(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    ui_dir = plugin_dir / "ui"
    ui_dir.mkdir(parents=True)
    (ui_dir / "panel.tsx").write_text(
        "export default function Panel() { return null }", encoding="utf-8"
    )
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [
                        {
                            "id": "main",
                            "mode": "hosted-tsx",
                            "entry": "ui/panel.tsx",
                        }
                    ]
                }
            }
        },
        plugin_id="demo",
    )

    actions = _build_plugin_list_actions_from_meta(
        "demo",
        {
            "id": "demo",
            "config_path": str(config_path),
            "plugin_ui": plugin_ui,
        },
    )

    assert [action["id"] for action in actions] == ["open_panel"]
