from __future__ import annotations

import pytest

from plugin.server.application.plugins import query_service as query_module
from plugin.server.application.plugins import router_query_service as router_module


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
            "entries": [],
        }
    ]


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
