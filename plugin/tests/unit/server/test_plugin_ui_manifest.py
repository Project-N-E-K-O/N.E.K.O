from __future__ import annotations

from pathlib import Path

from plugin.core.ui_manifest import normalize_plugin_ui_manifest
from plugin.server.application.plugins.ui_query_service import _build_plugin_list_actions_from_meta, _build_surfaces_sync


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
