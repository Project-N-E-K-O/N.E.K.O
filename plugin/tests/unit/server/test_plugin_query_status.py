from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.server.application.plugins import query_service as query_module
from plugin.server.application.plugins import router_query_service as router_module
from plugin.sdk.shared.i18n import PluginI18n


pytestmark = pytest.mark.plugin_unit


def test_build_plugin_list_reports_source_missing_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        query_module.state,
        "get_plugins_snapshot_cached",
        lambda timeout=2.0: {
            "missing_plugin": {
                "id": "missing_plugin",
                "name": "Missing Plugin",
                "runtime_source_missing": True,
            }
        },
    )
    monkeypatch.setattr(query_module.state, "get_plugin_hosts_snapshot_cached", lambda timeout=2.0: {})
    monkeypatch.setattr(query_module.state, "get_event_handlers_snapshot_cached", lambda timeout=2.0: {})

    results = query_module._build_plugin_list_sync()

    assert results == [
        {
            "id": "missing_plugin",
            "name": "Missing Plugin",
            "runtime_source_missing": True,
            "status": "source_missing",
            "i18n": {"messages": {}},
            "entries": [],
            "list_actions": [],
        }
    ]


def test_plugin_card_i18n_payload_keeps_only_plugin_display_keys() -> None:
    payload = query_module._plugin_card_i18n_payload(
        {"i18n": {"default_locale": "zh-CN", "locales_dir": "i18n"}},
        PluginI18n(
            {
                "ja": {
                    "plugin.name": "ギャルゲームプレイアシスタント",
                    "plugin.description": "猫娘がサポートします。",
                    "plugin.internal": "一覧には不要",
                    "entries.demo.name": "内部エントリ",
                },
                "en": {
                    "plugin.name": "Galgame Play Assistant",
                },
            },
            default_locale="zh-CN",
        ),
    )

    assert payload == {
        "default_locale": "zh-CN",
        "locales_dir": "i18n",
        "messages": {
            "ja": {
                "plugin.name": "ギャルゲームプレイアシスタント",
                "plugin.description": "猫娘がサポートします。",
            },
            "en": {
                "plugin.name": "Galgame Play Assistant",
            },
        },
    }


def test_build_plugin_list_includes_plugin_card_i18n(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "galgame_plugin"
    i18n_dir = plugin_dir / "i18n"
    i18n_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='galgame_plugin'\n", encoding="utf-8")
    (i18n_dir / "ja.json").write_text(
        json.dumps(
            {
                "plugin.name": "ギャルゲームプレイアシスタント",
                "plugin.description": "猫娘がサポートします。",
                "actions.open_ui.label": "UI を開く",
                "actions.open_ui.confirm": "開きますか?",
                "entries.handler_demo.name": "ハンドラーを実行",
                "entries.handler_demo.description": "ハンドラー由来エントリを実行する。",
                "entries.demo.name": "デモを実行",
                "entries.demo.description": "デモエントリを実行する。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        query_module.state,
        "get_plugins_snapshot_cached",
        lambda timeout=2.0: {
            "galgame_plugin": {
                "id": "galgame_plugin",
                "name": "Galgame游玩助手",
                "description": "让猫娘陪伴你一起玩galgame",
                "config_path": str(config_path),
                "i18n": {"default_locale": "zh-CN", "locales_dir": "i18n"},
                "entries_preview": [
                    {
                        "id": "demo",
                        "name": {"$i18n": "entries.demo.name", "default": "Run demo"},
                        "description": {"$i18n": "entries.demo.description", "default": "Run the demo entry."},
                    }
                ],
                "list_actions": [
                    {
                        "id": "open_ui",
                        "kind": "route",
                        "target": "/plugins/{plugin_id}?tab=panel",
                        "label": {"$i18n": "actions.open_ui.label", "default": "Open UI"},
                        "confirm_message": {"$i18n": "actions.open_ui.confirm", "default": "Open?"},
                    }
                ],
            }
        },
    )
    monkeypatch.setattr(query_module.state, "get_plugin_hosts_snapshot_cached", lambda timeout=2.0: {})
    monkeypatch.setattr(
        query_module.state,
        "get_event_handlers_snapshot_cached",
        lambda timeout=2.0: {
            "galgame_plugin.handler_demo": SimpleNamespace(
                meta=SimpleNamespace(
                    event_type="plugin_entry",
                    id="handler_demo",
                    name={"$i18n": "entries.handler_demo.name", "default": "Run handler"},
                    description={
                        "$i18n": "entries.handler_demo.description",
                        "default": "Run the handler entry.",
                    },
                    return_message="",
                    timeout=None,
                    input_schema={},
                    metadata={},
                    llm_result_schema={},
                    llm_result_fields=[],
                )
            )
        },
    )

    results = query_module._build_plugin_list_sync("ja")

    assert results[0]["name"] == "ギャルゲームプレイアシスタント"
    assert results[0]["description"] == "猫娘がサポートします。"
    assert results[0]["i18n"] == {
        "default_locale": "zh-CN",
        "locales_dir": "i18n",
        "messages": {
            "ja": {
                "plugin.name": "ギャルゲームプレイアシスタント",
                "plugin.description": "猫娘がサポートします。",
            }
        },
    }
    entries = results[0]["entries"]
    handler_demo = next(entry for entry in entries if entry["id"] == "handler_demo")
    demo_entry = next(entry for entry in entries if entry["id"] == "demo")
    assert handler_demo["name"] == "ハンドラーを実行"
    assert handler_demo["description"] == "ハンドラー由来エントリを実行する。"
    assert demo_entry["name"] == "デモを実行"
    assert demo_entry["description"] == "デモエントリを実行する。"
    assert results[0]["list_actions"][0]["label"] == "UI を開く"
    assert results[0]["list_actions"][0]["confirm_message"] == "開きますか?"


def test_router_query_reports_source_missing_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        router_module.state,
        "get_plugins_snapshot_cached",
        lambda timeout=1.0: {
            "missing_plugin": {
                "name": "Missing Plugin",
                "description": "missing",
                "version": "0.1.0",
                "sdk_version": "test",
                "runtime_source_missing": True,
            }
        },
    )
    monkeypatch.setattr(router_module.state, "get_event_handlers_snapshot_cached", lambda timeout=1.0: {})
    monkeypatch.setattr(router_module.status_manager, "get_plugin_status", lambda: {})

    results = router_module._query_plugins_sync({"status_in": ["source_missing"]})

    assert results == [
        {
            "plugin_id": "missing_plugin",
            "name": "Missing Plugin",
            "description": "missing",
            "version": "0.1.0",
            "sdk_version": "test",
            "status": "source_missing",
        }
    ]
