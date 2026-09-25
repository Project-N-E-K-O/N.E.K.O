from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.server.application.plugins import query_service as query_module
from plugin.server.application.plugins import router_query_service as router_module
from plugin.server.domain.errors import ServerDomainError
from plugin.sdk.shared.i18n import PluginI18n


pytestmark = pytest.mark.plugin_unit


@pytest.mark.asyncio
async def test_list_plugins_reports_registry_lock_timeout_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        query_module.state,
        "get_plugins_snapshot_cached",
        lambda timeout=2.0: {},
    )

    @contextmanager
    def _registry_lock_timeout(timeout=2.0):
        raise TimeoutError("plugins registry busy")
        yield

    monkeypatch.setattr(
        query_module.state,
        "acquire_plugins_read_lock",
        _registry_lock_timeout,
    )

    with pytest.raises(ServerDomainError) as exc_info:
        await query_module.PluginQueryService().list_plugins()

    assert exc_info.value.code == "PLUGIN_REGISTRY_UNAVAILABLE"
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_snapshot", ["hosts", "handlers"])
async def test_list_plugins_reports_related_snapshot_failure_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    failing_snapshot: str,
) -> None:
    monkeypatch.setattr(
        query_module.state,
        "get_plugins_snapshot_cached",
        lambda timeout=2.0: {"sample": {"id": "sample", "name": "Sample"}},
    )

    def _snapshot_failure(timeout=2.0):
        raise RuntimeError(f"{failing_snapshot} snapshot busy")

    monkeypatch.setattr(
        query_module.state,
        "get_plugin_hosts_snapshot_cached",
        _snapshot_failure if failing_snapshot == "hosts" else lambda timeout=2.0: {},
    )
    monkeypatch.setattr(
        query_module.state,
        "get_event_handlers_snapshot_cached",
        _snapshot_failure if failing_snapshot == "handlers" else lambda timeout=2.0: {},
    )

    with pytest.raises(ServerDomainError) as exc_info:
        await query_module.PluginQueryService().list_plugins()

    assert exc_info.value.code == "PLUGIN_REGISTRY_UNAVAILABLE"
    assert exc_info.value.status_code == 503


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
            "install_source": {
                "source": "unknown",
                "reason": None,
                "installed_at": None,
                "source_detail": None,
            },
        }
    ]


def test_build_plugin_list_omits_internal_entries_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    registry_meta = {
        "id": "demo",
        "name": "Demo",
        "entries_preview": [{"id": "ping", "name": "Ping"}],
    }
    monkeypatch.setattr(
        query_module.state,
        "get_plugins_snapshot_cached",
        lambda timeout=2.0: {"demo": registry_meta},
    )
    monkeypatch.setattr(query_module.state, "get_plugin_hosts_snapshot_cached", lambda timeout=2.0: {})
    monkeypatch.setattr(query_module.state, "get_event_handlers_snapshot_cached", lambda timeout=2.0: {})

    results = query_module._build_plugin_list_sync()

    assert "entries_preview" in registry_meta
    assert "entries_preview" not in results[0]
    assert [entry["id"] for entry in results[0]["entries"]] == ["ping"]


@pytest.mark.asyncio
async def test_summary_projection_keeps_card_contract_and_matches_full_entry_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "id": "summary_demo",
        "name": "Summary demo",
        "description": "card",
        "version": "1.2.3",
        "type": "service",
        "author": {"name": "Neko"},
        "dependencies": [{"id": "shared", "version": ">=1"}],
        "runtime_enabled": True,
        "entries_preview": [
            {
                "id": "declared",
                "name": "Declared",
                "description": "declared description",
                "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
                "metadata": {"private": "large"},
            }
        ],
        "list_actions": [{"id": "open", "kind": "route", "target": "/plugins/summary_demo"}],
    }
    handlers = {
        "summary_demo.runtime": SimpleNamespace(
            meta=SimpleNamespace(
                event_type="plugin_entry",
                id="runtime",
                name="Runtime",
                description="runtime description",
                input_schema={"type": "object"},
                timeout=9,
            )
        )
    }
    monkeypatch.setattr(query_module.state, "get_plugins_snapshot_cached", lambda timeout=2.0: {"summary_demo": metadata})
    monkeypatch.setattr(query_module.state, "get_plugin_hosts_snapshot_cached", lambda timeout=2.0: {})
    monkeypatch.setattr(query_module.state, "get_event_handlers_snapshot_cached", lambda timeout=2.0: handlers)
    monkeypatch.setattr(query_module, "_install_source_index", lambda: ({}, {}))

    full = (await query_module.PluginQueryService().list_plugins("en"))["plugins"][0]
    summary = (await query_module.PluginQueryService().list_plugins("en", summary=True))["plugins"][0]

    assert [entry["id"] for entry in summary["entries"]] == [entry["id"] for entry in full["entries"]]
    assert summary["entry_count"] == len(full["entries"]) == 2
    assert summary["has_input_schema"] is True
    assert summary["dependency_count"] == 1
    assert summary["dependencies"] == metadata["dependencies"]
    assert summary["list_actions"] == full["list_actions"]
    assert summary["entries"][0]["has_input_schema"] is True
    assert summary["entries"][0]["timeout"] == 9
    assert "input_schema" not in summary["entries"][0]
    assert "metadata" not in summary["entries"][0]
    assert "input_schema" not in summary


@pytest.mark.asyncio
async def test_get_plugin_builds_only_requested_full_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str | None] = []
    original = query_module._build_plugin_list_sync

    def _build(locale=None, plugin_id_filter=None):
        calls.append(plugin_id_filter)
        return original(locale, plugin_id_filter)

    monkeypatch.setattr(query_module, "_build_plugin_list_sync", _build)
    monkeypatch.setattr(query_module.state, "get_plugins_snapshot_cached", lambda timeout=2.0: {
        "one": {"id": "one", "name": "One"},
        "two": {"id": "two", "name": "Two"},
    })
    monkeypatch.setattr(query_module.state, "get_plugin_hosts_snapshot_cached", lambda timeout=2.0: {})
    monkeypatch.setattr(query_module.state, "get_event_handlers_snapshot_cached", lambda timeout=2.0: {})
    monkeypatch.setattr(query_module, "_install_source_index", lambda: ({}, {}))

    result = await query_module.PluginQueryService().get_plugin("two", "en")
    assert result["plugin"]["id"] == "two"
    assert calls == ["two"]


@pytest.mark.asyncio
async def test_get_plugin_returns_not_found_for_unknown_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        query_module.state,
        "get_plugins_snapshot_cached",
        lambda timeout=2.0: {"other": {"id": "other", "name": "Other"}},
    )
    monkeypatch.setattr(query_module.state, "get_plugin_hosts_snapshot_cached", lambda timeout=2.0: {})
    monkeypatch.setattr(query_module.state, "get_event_handlers_snapshot_cached", lambda timeout=2.0: {})
    monkeypatch.setattr(query_module, "_install_source_index", lambda: ({}, {}))
    with pytest.raises(ServerDomainError) as exc_info:
        await query_module.PluginQueryService().get_plugin("missing", "en")
    assert exc_info.value.code == "PLUGIN_NOT_FOUND"
    assert exc_info.value.status_code == 404


def test_plugin_entry_handler_index_preserves_supported_key_order_and_filters_types() -> None:
    """The list query's one-pass index must preserve serializer semantics."""
    handlers = {
        "alpha.first": SimpleNamespace(
            meta=SimpleNamespace(event_type="plugin_entry", id="first")
        ),
        "alpha:plugin_entry:second": SimpleNamespace(
            meta=SimpleNamespace(event_type="plugin_entry", id="second")
        ),
        "alpha.lifecycle:reload": SimpleNamespace(
            meta=SimpleNamespace(event_type="lifecycle", id="reload")
        ),
        "beta:plugin_entry:only": SimpleNamespace(
            meta=SimpleNamespace(event_type="plugin_entry", id="only")
        ),
    }

    indexed = query_module._index_plugin_entry_handlers(handlers)

    assert list(indexed["alpha"]) == ["alpha.first", "alpha:plugin_entry:second"]
    assert list(indexed["beta"]) == ["beta:plugin_entry:only"]
    assert "alpha.lifecycle:reload" not in indexed["alpha"]

    full_entries, _ = query_module._build_entries_from_handlers(
        plugin_id="alpha", handlers_snapshot=handlers
    )
    indexed_entries, _ = query_module._build_entries_from_handlers(
        plugin_id="alpha", handlers_snapshot=indexed["alpha"]
    )
    assert indexed_entries == full_entries


def test_plugin_entry_handler_index_falls_back_for_dotted_legacy_plugin_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dotted plugin id keeps the old full-snapshot prefix behavior."""
    handlers = {
        "foo.bar.first": SimpleNamespace(
            meta=SimpleNamespace(event_type="plugin_entry", id="first")
        ),
        "foo.bar:plugin_entry:second": SimpleNamespace(
            meta=SimpleNamespace(event_type="plugin_entry", id="second")
        ),
    }

    monkeypatch.setattr(
        query_module.state,
        "get_plugins_snapshot_cached",
        lambda timeout=2.0: {"foo.bar": {"id": "foo.bar", "name": "Dotted"}},
    )
    monkeypatch.setattr(
        query_module.state,
        "get_plugin_hosts_snapshot_cached",
        lambda timeout=2.0: {},
    )
    monkeypatch.setattr(
        query_module.state,
        "get_event_handlers_snapshot_cached",
        lambda timeout=2.0: handlers,
    )
    monkeypatch.setattr(query_module, "_install_source_index", lambda: ({}, {}))

    results = query_module._build_plugin_list_sync()

    assert [entry["id"] for entry in results[0]["entries"]] == [
        "first",
        "second",
    ]


def test_resolve_plugin_display_fields_preserves_empty_description_without_translation() -> None:
    plugin_info: dict[str, object] = {
        "id": "empty_description_plugin",
        "name": "Empty Description Plugin",
        "description": "",
    }

    query_module._resolve_plugin_display_fields(
        plugin_info,
        PluginI18n({"ja": {"plugin.name": "空の説明プラグイン"}}),
        locale="ja",
    )

    assert plugin_info["name"] == "空の説明プラグイン"
    assert plugin_info["description"] == ""


def test_resolve_plugin_display_fields_uses_id_when_name_is_empty_without_translation() -> None:
    plugin_info: dict[str, object] = {
        "id": "empty_name_plugin",
        "name": "",
        "description": "Description",
    }

    query_module._resolve_plugin_display_fields(
        plugin_info,
        PluginI18n(),
        locale="ja",
    )

    assert plugin_info["name"] == "empty_name_plugin"
    assert plugin_info["description"] == "Description"


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
