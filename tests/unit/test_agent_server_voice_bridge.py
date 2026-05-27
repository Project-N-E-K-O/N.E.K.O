from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_voice_transcript_request_reports_lifecycle_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import agent_server as srv
    from plugin.server.application.plugins import dispatch_service as dispatch_module

    emitted: dict[str, Any] = {}
    dispatch_called = False

    async def _start_plugin_lifecycle() -> bool:
        return False

    async def _emit_main_event(event_type: str, lanlan_name: str | None, **payload: Any) -> None:
        emitted["event_type"] = event_type
        emitted["lanlan_name"] = lanlan_name
        emitted["payload"] = payload

    class _DispatchService:
        async def trigger_custom_event(self, **_: Any) -> dict[str, Any]:
            nonlocal dispatch_called
            dispatch_called = True
            return {"action": "noop", "reason": "unexpected_dispatch"}

    monkeypatch.setitem(srv.Modules.agent_flags, "user_plugin_enabled", True)
    monkeypatch.setattr(srv.Modules, "plugin_lifecycle_started", False)
    monkeypatch.setattr(srv, "_ensure_plugin_lifecycle_started", _start_plugin_lifecycle)
    monkeypatch.setattr(srv, "_emit_main_event", _emit_main_event)
    monkeypatch.setattr(
        dispatch_module,
        "PluginDispatchService",
        lambda: _DispatchService(),
    )

    await srv._handle_voice_transcript_request(
        {
            "event_id": "voice-1",
            "lanlan_name": "Yui",
            "transcript": "Yui explain this step",
        }
    )

    assert dispatch_called is False
    assert emitted["event_type"] == "voice_bridge_result"
    assert emitted["lanlan_name"] == "Yui"
    assert emitted["payload"]["event_id"] == "voice-1"
    assert emitted["payload"]["result"] == {
        "action": "noop",
        "reason": "plugin_lifecycle_start_failed",
    }
