from __future__ import annotations

import tomllib
from pathlib import Path

from plugin.plugins.neko_roast import NekoRoastPlugin
from plugin.plugins.neko_roast.core.runtime_dashboard_actions import dashboard_actions
from plugin.sdk.plugin.ui import UI_ACTION_META_ATTR
from plugin.sdk.shared.constants import EVENT_META_ATTR


def test_neko_roast_manifest_smoke():
    root = Path(__file__).resolve().parents[1]
    with (root / "plugin.toml").open("rb") as handle:
        manifest = tomllib.load(handle)

    assert manifest["plugin"]["id"] == "neko_roast"
    assert manifest["plugin"]["entry"] == "plugin.plugins.neko_roast:NekoRoastPlugin"
    assert manifest["neko_roast"]["roast_strength"] == "normal"
    panel_entry = manifest["plugin"]["ui"]["panel"][0]["entry"]
    assert (root / panel_entry).is_file()


def test_dashboard_actions_are_exposed_plugin_entries() -> None:
    projected = {item["entry_id"] for item in dashboard_actions()}
    entry_ids = set()
    ui_action_ids = set()
    for member in vars(NekoRoastPlugin).values():
        entry_meta = getattr(member, EVENT_META_ATTR, None)
        if entry_meta is not None and entry_meta.event_type == "plugin_entry":
            entry_ids.add(entry_meta.id)
        action_meta = getattr(member, UI_ACTION_META_ATTR, None)
        if isinstance(action_meta, dict):
            ui_action_ids.add(action_meta.get("id"))

    assert projected <= entry_ids
    assert projected <= ui_action_ids


def test_patched_panel_saves_include_current_room_reference() -> None:
    root = Path(__file__).resolve().parents[1]
    expected = (
        "...patch,\n"
        "          live_room_ref: liveRoomRef,\n"
        "          live_room_id: livePlatform === \"bilibili\" ? liveRoomRef : 0,"
    )

    for panel_name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / panel_name).read_text(encoding="utf-8")
        assert expected in source


def test_patched_panel_saves_ignore_unhydrated_room_sentinel() -> None:
    root = Path(__file__).resolve().parents[1]
    expected_room_priority = (
        'normalizedRoomRef(configForm.values.live_room_ref) ||\n'
        '          normalizedRoomRef(config.live_room_ref) ||\n'
        '          normalizedRoomRef(config.live_room_id) ||\n'
        '          normalizedRoomRef(configForm.values.live_room_id)'
    )

    for panel_name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / panel_name).read_text(encoding="utf-8")
        assert 'return roomRef === "0" ? "" : roomRef' in source
        assert expected_room_priority in source


def test_platform_switch_defers_room_reset_to_backend_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    platform_only_switch = 'saveConfig({ live_platform: next, live_enabled: false })'
    platform_only_payload = (
        "const patchedPayload = hasPatchedPlatform && "
        "!hasPatchedRoomRef && !hasPatchedRoomId\n"
        "      ? patch"
    )

    for panel_name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / panel_name).read_text(encoding="utf-8")
        assert platform_only_switch in source
        assert platform_only_payload in source


def test_compat_panel_mirrors_live_connection_and_theme_controls() -> None:
    root = Path(__file__).resolve().parents[1]

    for panel_name in ("panel.tsx", "panel_compat.tsx"):
        source = (root / "ui" / panel_name).read_text(encoding="utf-8")
        assert "result.logged_in || result.has_cookie" in source
        assert "connection.listening ||" in source
        assert 'connectionState === "receiving"' in source
        for field in ("streamGoal", "streamColumns", "streamAvoidTopics"):
            assert f't("panel.fields.{field}")' in source


def test_compat_panel_mirrors_accessible_qr_and_result_tones() -> None:
    root = Path(__file__).resolve().parents[1]
    components = (root / "ui" / "panel_components.tsx").read_text(encoding="utf-8")
    sections = (root / "ui" / "panel_data_sections.tsx").read_text(encoding="utf-8")
    helpers = (root / "ui" / "panel_helpers.ts").read_text(encoding="utf-8")
    compat = (root / "ui" / "panel_compat.tsx").read_text(encoding="utf-8")

    assert '<button\n                  type="button"\n                  onClick={onLogin}' in components
    assert 'aria-label={t("panel.auth.refreshHint")}' in components
    assert 'recentResultTone(String(row.status || ""))' in sections
    for source in (helpers, compat):
        assert 'if (status === "failed") return "danger"' in source
        assert 'if (status === "skipped") return "warning"' in source
    assert '<button\n                  type="button"\n                  onClick={onLogin}' in compat
    assert 'recentResultTone(String(row.status || ""))' in compat
