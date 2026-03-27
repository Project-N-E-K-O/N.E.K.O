from __future__ import annotations

import pytest

from plugin.server.application.plugins import router_query_service as module


@pytest.mark.plugin_unit
def test_query_plugins_reports_load_failed_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module.state,
        "get_plugins_snapshot_cached",
        lambda timeout=1.0: {
            "broken_plugin": {
                "name": "Broken Plugin",
                "description": "broken",
                "version": "0.1.0",
                "sdk_version": "test",
                "runtime_load_state": "failed",
            }
        },
    )
    monkeypatch.setattr(module.state, "get_event_handlers_snapshot_cached", lambda timeout=1.0: {})
    monkeypatch.setattr(module.status_manager, "get_plugin_status", lambda: {})

    results = module._query_plugins_sync({"status_in": ["load_failed"]})

    assert results == [
        {
            "plugin_id": "broken_plugin",
            "name": "Broken Plugin",
            "description": "broken",
            "version": "0.1.0",
            "sdk_version": "test",
            "status": "load_failed",
        }
    ]
