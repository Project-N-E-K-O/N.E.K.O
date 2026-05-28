from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_voice_transcript_request_reports_lifecycle_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import agent_server as srv
    from plugin.server.application.plugins import voice_transcript_bridge

    emitted: dict[str, Any] = {}
    resolve_called = False

    async def _start_plugin_lifecycle() -> bool:
        return False

    async def _emit_main_event(event_type: str, lanlan_name: str | None, **payload: Any) -> None:
        emitted["event_type"] = event_type
        emitted["lanlan_name"] = lanlan_name
        emitted["payload"] = payload

    async def _resolve_voice_transcript_request(*_: Any, **__: Any) -> dict[str, Any]:
        nonlocal resolve_called
        resolve_called = True
        return {"action": "noop", "reason": "unexpected_dispatch"}

    monkeypatch.setitem(srv.Modules.agent_flags, "user_plugin_enabled", True)
    monkeypatch.setattr(srv.Modules, "analyzer_enabled", True)
    monkeypatch.setattr(srv.Modules, "plugin_lifecycle_started", False)
    monkeypatch.setattr(srv, "_ensure_plugin_lifecycle_started", _start_plugin_lifecycle)
    monkeypatch.setattr(srv, "_emit_main_event", _emit_main_event)
    monkeypatch.setattr(
        voice_transcript_bridge,
        "resolve_voice_transcript_request",
        _resolve_voice_transcript_request,
    )

    await srv._handle_voice_transcript_request(
        {
            "event_id": "voice-1",
            "lanlan_name": "Yui",
            "transcript": "Yui explain this step",
        }
    )

    assert resolve_called is False
    assert emitted["event_type"] == "voice_bridge_result"
    assert emitted["lanlan_name"] == "Yui"
    assert emitted["payload"]["event_id"] == "voice-1"
    assert emitted["payload"]["result"] == {
        "action": "noop",
        "reason": "plugin_lifecycle_start_failed",
    }


@pytest.mark.asyncio
async def test_voice_transcript_request_skips_plugins_when_agent_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import agent_server as srv
    from plugin.server.application.plugins import voice_transcript_bridge

    emitted: dict[str, Any] = {}
    start_called = False
    resolve_called = False

    async def _start_plugin_lifecycle() -> bool:
        nonlocal start_called
        start_called = True
        return True

    async def _emit_main_event(event_type: str, lanlan_name: str | None, **payload: Any) -> None:
        emitted["event_type"] = event_type
        emitted["lanlan_name"] = lanlan_name
        emitted["payload"] = payload

    async def _resolve_voice_transcript_request(*_: Any, **__: Any) -> dict[str, Any]:
        nonlocal resolve_called
        resolve_called = True
        return {"action": "noop", "reason": "unexpected_dispatch"}

    monkeypatch.setitem(srv.Modules.agent_flags, "user_plugin_enabled", True)
    monkeypatch.setattr(srv.Modules, "analyzer_enabled", False)
    monkeypatch.setattr(srv.Modules, "plugin_lifecycle_started", False)
    monkeypatch.setattr(srv, "_ensure_plugin_lifecycle_started", _start_plugin_lifecycle)
    monkeypatch.setattr(srv, "_emit_main_event", _emit_main_event)
    monkeypatch.setattr(
        voice_transcript_bridge,
        "resolve_voice_transcript_request",
        _resolve_voice_transcript_request,
    )

    await srv._handle_voice_transcript_request(
        {
            "event_id": "voice-disabled",
            "lanlan_name": "Yui",
            "transcript": "Yui explain this step",
        }
    )

    assert start_called is False
    assert resolve_called is False
    assert emitted["event_type"] == "voice_bridge_result"
    assert emitted["lanlan_name"] == "Yui"
    assert emitted["payload"]["event_id"] == "voice-disabled"
    assert emitted["payload"]["result"] == {
        "action": "noop",
        "reason": "agent_disabled",
    }


@pytest.mark.asyncio
async def test_voice_transcript_request_uses_arbitrated_custom_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import agent_server as srv
    from plugin.server.application.plugins import voice_transcript_bridge

    emitted: dict[str, Any] = {}
    captured: dict[str, Any] = {}

    async def _emit_main_event(event_type: str, lanlan_name: str | None, **payload: Any) -> None:
        emitted["event_type"] = event_type
        emitted["lanlan_name"] = lanlan_name
        emitted["payload"] = payload

    async def _resolve_voice_transcript_request(
        event: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured["event"] = event
        captured.update(kwargs)
        return {
            "action": "prime_context",
            "context": "screen context",
            "source_plugin": "study_companion",
        }

    monkeypatch.setitem(srv.Modules.agent_flags, "user_plugin_enabled", True)
    monkeypatch.setattr(srv.Modules, "analyzer_enabled", True)
    monkeypatch.setattr(srv.Modules, "plugin_lifecycle_started", True)
    monkeypatch.setattr(srv, "_emit_main_event", _emit_main_event)
    monkeypatch.setattr(
        voice_transcript_bridge,
        "resolve_voice_transcript_request",
        _resolve_voice_transcript_request,
    )

    await srv._handle_voice_transcript_request(
        {
            "event_id": "voice-2",
            "lanlan_name": "Yui",
            "transcript": "Yui explain this step",
            "metadata": {"session_id": "s1"},
        }
    )

    assert captured["event"]["event_id"] == "voice-2"
    assert captured["event"]["transcript"] == "Yui explain this step"
    assert captured["event"]["metadata"] == {"session_id": "s1"}
    assert captured["timeout"] == voice_transcript_bridge.VOICE_TRANSCRIPT_DISPATCH_TIMEOUT_SECONDS
    assert emitted["event_type"] == "voice_bridge_result"
    assert emitted["payload"]["event_id"] == "voice-2"
    assert emitted["payload"]["result"] == {
        "action": "prime_context",
        "context": "screen context",
        "source_plugin": "study_companion",
    }
