from __future__ import annotations

from pathlib import Path

from plugin.core.ui_manifest import normalize_plugin_ui_manifest
from plugin.server.application.plugins.ui_query_service import (
    _build_plugin_list_actions_from_meta,
    _build_surfaces_sync,
    _resolve_authorized_action_entry_id,
    _surface_allows_action_call,
)


def test_normalize_plugin_ui_manifest_panel_and_guide() -> None:
    conf = {
        "plugin": {
            "ui": {
                "enabled": True,
                "panel": [
                    {
                        "id": "main",
                        "title": "Main Panel",
                        "mode": "static",
                        "entry": "static/index.html",
                    }
                ],
                "guide": [
                    {
                        "id": "quickstart",
                        "mode": "hosted-tsx",
                        "entry": "docs/quickstart.tsx",
                    }
                ],
            }
        }
    }

    manifest = normalize_plugin_ui_manifest(conf, plugin_id="demo")

    assert manifest is not None
    assert manifest["panel"][0]["kind"] == "panel"
    assert manifest["panel"][0]["permissions"] == ["state:read", "config:read", "action:call"]
    assert manifest["guide"][0]["kind"] == "guide"
    assert manifest["guide"][0]["permissions"] == ["state:read"]


def test_normalize_plugin_ui_manifest_infers_mode_and_id_from_entry() -> None:
    conf = {
        "plugin": {
            "ui": {
                "panel": [{"entry": "static/index.html"}],
                "guide": [{"entry": "docs/quickstart.tsx"}],
            }
        }
    }

    manifest = normalize_plugin_ui_manifest(conf, plugin_id="demo")

    assert manifest is not None
    assert manifest["panel"][0]["id"] == "main"
    assert manifest["panel"][0]["mode"] == "static"
    assert manifest["panel"][0]["context"] == "main"
    assert manifest["guide"][0]["id"] == "quickstart"
    assert manifest["guide"][0]["mode"] == "hosted-tsx"
    assert manifest["guide"][0]["context"] == "quickstart"


def test_normalize_plugin_ui_manifest_warnings_for_invalid_fields() -> None:
    conf = {
        "plugin": {
            "ui": {
                "panel": [
                    {
                        "id": 123,
                        "mode": "tsx",
                        "entry": "ui/panel.tsx",
                        "permissions": ["config:wrtie", "state:read", ""],
                    }
                ],
            }
        }
    }

    manifest = normalize_plugin_ui_manifest(conf, plugin_id="demo")

    assert manifest is not None
    warning_codes = {item["code"] for item in manifest["warnings"]}
    assert "invalid_id" in warning_codes
    assert "unsupported_mode" in warning_codes
    assert "unknown_permission" in warning_codes
    assert "invalid_permission" in warning_codes


def test_invalid_permissions_shape_does_not_fall_back_to_default_permissions() -> None:
    conf = {
        "plugin": {
            "ui": {
                "panel": [
                    {
                        "entry": "ui/panel.tsx",
                        "permissions": "action:call",
                    }
                ],
            }
        }
    }

    manifest = normalize_plugin_ui_manifest(conf, plugin_id="demo")

    assert manifest is not None
    assert manifest["panel"][0]["permissions"] == []
    assert {item["code"] for item in manifest["warnings"]} == {"invalid_permissions"}


def test_surfaces_and_actions_use_manifest_and_static_compat(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    static_dir = plugin_dir / "static"
    docs_dir = plugin_dir / "docs"
    static_dir.mkdir(parents=True)
    docs_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (docs_dir / "quickstart.tsx").write_text("export default function Panel() { return <Page /> }", encoding="utf-8")
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [{"id": "main", "mode": "static", "entry": "static/index.html"}],
                    "guide": [{"id": "quickstart", "mode": "hosted-tsx", "entry": "docs/quickstart.tsx"}],
                }
            }
        },
        plugin_id="demo",
    )
    meta = {
        "id": "demo",
        "config_path": str(config_path),
        "plugin_ui": plugin_ui,
    }

    surfaces, warnings = _build_surfaces_sync("demo", meta)
    actions = _build_plugin_list_actions_from_meta("demo", meta)

    assert warnings == []
    assert [surface["kind"] for surface in surfaces] == ["panel", "guide"]
    assert {action["id"] for action in actions} == {"open_panel", "open_guide"}


def test_unavailable_surfaces_do_not_get_route_actions(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [{"id": "main", "mode": "hosted-tsx", "entry": "ui/missing.tsx"}],
                    "guide": [{"id": "quickstart", "mode": "hosted-tsx", "entry": "docs/missing.tsx"}],
                }
            }
        },
        plugin_id="demo",
    )
    meta = {
        "id": "demo",
        "config_path": str(config_path),
        "plugin_ui": plugin_ui,
    }

    surfaces, _warnings = _build_surfaces_sync("demo", meta)
    actions = _build_plugin_list_actions_from_meta("demo", meta)

    assert all(surface["available"] is False for surface in surfaces)
    assert actions == []


def test_surface_action_permission_and_authorized_entry_resolution() -> None:
    assert _surface_allows_action_call({"permissions": ["state:read", "action:call"]})
    assert not _surface_allows_action_call({"permissions": ["state:read"]})

    actions = [
        {"id": "restart", "entry_id": "restart_server"},
        {"id": "connect_server", "entry_id": "connect_server"},
    ]
    entry_ids = {"restart_server", "connect_server", "hidden_entry"}

    assert _resolve_authorized_action_entry_id("restart", actions=actions, entry_ids=entry_ids) == "restart_server"
    assert _resolve_authorized_action_entry_id("connect_server", actions=actions, entry_ids=entry_ids) == "connect_server"
    assert _resolve_authorized_action_entry_id("hidden_entry", actions=actions, entry_ids=entry_ids) is None
