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
                "expose_legacy_static_panel": False,
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
    assert manifest["expose_legacy_static_panel"] is False
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


def test_document_parse_is_a_supported_explicit_permission() -> None:
    manifest = normalize_plugin_ui_manifest({
        "plugin": {
            "ui": {
                "panel": [{
                    "id": "study",
                    "mode": "hosted-tsx",
                    "entry": "surfaces/study.tsx",
                    "permissions": ["state:read", "document:parse"],
                }],
            },
        },
    })

    assert manifest is not None
    assert manifest["panel"][0]["permissions"] == ["state:read", "document:parse"]
    assert "warnings" not in manifest


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
    assert surfaces[0]["legacy_static_compat"] is False
    assert {action["id"] for action in actions} == {
        "open_ui",
        "open_panel",
        "open_guide",
    }


def test_static_compat_surface_is_marked_and_precedes_declared_panels(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    static_dir = plugin_dir / "static"
    ui_dir = plugin_dir / "ui"
    static_dir.mkdir(parents=True)
    ui_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (ui_dir / "dashboard.tsx").write_text("export default function Panel() { return <Page /> }", encoding="utf-8")
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\\nid='demo'\\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "panel": [{"id": "dashboard", "mode": "hosted-tsx", "entry": "ui/dashboard.tsx"}],
                }
            }
        },
        plugin_id="demo",
    )

    surfaces, warnings = _build_surfaces_sync("demo", {
        "id": "demo",
        "config_path": str(config_path),
        "plugin_ui": plugin_ui,
    })

    assert warnings == []
    assert [(surface["id"], surface["legacy_static_compat"]) for surface in surfaces] == [
        ("main", True),
        ("dashboard", False),
    ]


def test_legacy_static_panel_can_be_hidden_without_losing_open_ui(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    static_dir = plugin_dir / "static"
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {"plugin": {"ui": {"expose_legacy_static_panel": False}}},
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
    assert surfaces == []
    assert actions == [
        {
            "id": "open_ui",
            "kind": "ui",
            "target": "/plugin/demo/ui/",
            "open_in": "new_tab",
        },
    ]


def test_open_ui_uses_legacy_static_root_before_auxiliary_static_surface(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    static_dir = plugin_dir / "static"
    docs_dir = plugin_dir / "docs"
    static_dir.mkdir(parents=True)
    docs_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (docs_dir / "guide.html").write_text("<html>guide</html>", encoding="utf-8")
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {
            "plugin": {
                "ui": {
                    "expose_legacy_static_panel": False,
                    "docs": [
                        {
                            "id": "guide",
                            "mode": "static",
                            "entry": "docs/guide.html",
                        },
                    ],
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

    actions = _build_plugin_list_actions_from_meta("demo", meta)

    open_ui = next(action for action in actions if action["id"] == "open_ui")
    open_guide = next(action for action in actions if action["id"] == "open_guide")
    assert open_ui["target"] == "/plugin/demo/ui/"
    assert open_guide["target"] == "/plugins/demo?tab=guide"


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


def test_auto_only_panel_does_not_get_route_action(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {"plugin": {"ui": {"panel": [{"id": "main", "mode": "auto"}]}}},
        plugin_id="demo",
    )
    meta = {
        "id": "demo",
        "config_path": str(config_path),
        "plugin_ui": plugin_ui,
    }

    surfaces, _warnings = _build_surfaces_sync("demo", meta)
    actions = _build_plugin_list_actions_from_meta("demo", meta)

    assert surfaces[0]["mode"] == "auto"
    assert actions == []


def test_static_compat_replaces_auto_main_panel(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    static_dir = plugin_dir / "static"
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    plugin_ui = normalize_plugin_ui_manifest(
        {"plugin": {"ui": {"panel": [{"id": "main", "mode": "auto"}]}}},
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
    assert [(surface["mode"], surface["legacy_static_compat"]) for surface in surfaces] == [
        ("static", True),
        ("auto", False),
    ]
    assert actions == [
        {
            "id": "open_ui",
            "kind": "ui",
            "target": "/plugin/demo/ui/",
            "open_in": "new_tab",
        },
        {
            "id": "open_panel",
            "kind": "route",
            "target": "/plugins/demo?tab=panel",
        },
    ]


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
