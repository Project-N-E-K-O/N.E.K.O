from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_voice_transcript_request_reports_lifecycle_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import agent_server as srv

    emitted: dict[str, Any] = {}
    dispatch_called = False

    async def _start_plugin_lifecycle() -> bool:
        return False

    async def _emit_main_event(event_type: str, lanlan_name: str | None, **payload: Any) -> None:
        emitted["event_type"] = event_type
        emitted["lanlan_name"] = lanlan_name
        emitted["payload"] = payload

    async def _dispatch_voice_transcript_custom_event(*_: Any, **__: Any) -> dict[str, Any]:
        nonlocal dispatch_called
        dispatch_called = True
        return {"action": "noop", "reason": "unexpected_dispatch"}

    monkeypatch.setitem(srv.Modules.agent_flags, "user_plugin_enabled", True)
    monkeypatch.setattr(srv.Modules, "analyzer_enabled", True)
    monkeypatch.setattr(srv.Modules, "plugin_lifecycle_started", False)
    monkeypatch.setattr(srv, "_ensure_plugin_lifecycle_started", _start_plugin_lifecycle)
    monkeypatch.setattr(srv, "_emit_main_event", _emit_main_event)
    monkeypatch.setattr(
        srv,
        "_dispatch_voice_transcript_custom_event",
        _dispatch_voice_transcript_custom_event,
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


@pytest.mark.asyncio
async def test_voice_transcript_request_skips_plugins_when_agent_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import agent_server as srv

    emitted: dict[str, Any] = {}
    start_called = False
    dispatch_called = False

    async def _start_plugin_lifecycle() -> bool:
        nonlocal start_called
        start_called = True
        return True

    async def _emit_main_event(event_type: str, lanlan_name: str | None, **payload: Any) -> None:
        emitted["event_type"] = event_type
        emitted["lanlan_name"] = lanlan_name
        emitted["payload"] = payload

    async def _dispatch_voice_transcript_custom_event(*_: Any, **__: Any) -> dict[str, Any]:
        nonlocal dispatch_called
        dispatch_called = True
        return {"action": "noop", "reason": "unexpected_dispatch"}

    monkeypatch.setitem(srv.Modules.agent_flags, "user_plugin_enabled", True)
    monkeypatch.setattr(srv.Modules, "analyzer_enabled", False)
    monkeypatch.setattr(srv.Modules, "plugin_lifecycle_started", False)
    monkeypatch.setattr(srv, "_ensure_plugin_lifecycle_started", _start_plugin_lifecycle)
    monkeypatch.setattr(srv, "_emit_main_event", _emit_main_event)
    monkeypatch.setattr(
        srv,
        "_dispatch_voice_transcript_custom_event",
        _dispatch_voice_transcript_custom_event,
    )

    await srv._handle_voice_transcript_request(
        {
            "event_id": "voice-disabled",
            "lanlan_name": "Yui",
            "transcript": "Yui explain this step",
        }
    )

    assert start_called is False
    assert dispatch_called is False
    assert emitted["event_type"] == "voice_bridge_result"
    assert emitted["lanlan_name"] == "Yui"
    assert emitted["payload"]["event_id"] == "voice-disabled"
    assert emitted["payload"]["result"] == {
        "action": "noop",
        "reason": "agent_disabled",
    }


@pytest.mark.asyncio
async def test_voice_transcript_request_dispatches_custom_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import agent_server as srv
    from plugin.server.application.plugins.dispatch_service import PluginDispatchService

    emitted: dict[str, Any] = {}
    captured: dict[str, Any] = {}

    async def _emit_main_event(event_type: str, lanlan_name: str | None, **payload: Any) -> None:
        emitted["event_type"] = event_type
        emitted["lanlan_name"] = lanlan_name
        emitted["payload"] = payload

    async def _trigger_custom_event_subscribers(
        self: PluginDispatchService,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return [
            {
                "plugin_id": "study_companion",
                "event_id": "handle_transcript",
                "success": True,
                "result": {
                    "action": "prime_context",
                    "context": "screen context",
                },
            }
        ]

    monkeypatch.setitem(srv.Modules.agent_flags, "user_plugin_enabled", True)
    monkeypatch.setattr(srv.Modules, "analyzer_enabled", True)
    monkeypatch.setattr(srv.Modules, "plugin_lifecycle_started", True)
    monkeypatch.setattr(srv, "_emit_main_event", _emit_main_event)
    monkeypatch.setattr(
        PluginDispatchService,
        "trigger_custom_event_subscribers",
        _trigger_custom_event_subscribers,
    )

    await srv._handle_voice_transcript_request(
        {
            "event_id": "voice-2",
            "lanlan_name": "Yui",
            "transcript": "Yui explain this step",
            "metadata": {"session_id": "s1"},
        }
    )

    assert captured == {
        "event_type": srv.VOICE_TRANSCRIPT_CUSTOM_EVENT_TYPE,
        "args": {
            "transcript": "Yui explain this step",
            "lanlan_name": "Yui",
            "metadata": {"session_id": "s1"},
        },
        "timeout": srv.VOICE_TRANSCRIPT_CUSTOM_EVENT_TIMEOUT_SECONDS,
    }
    assert emitted["event_type"] == "voice_bridge_result"
    assert emitted["payload"]["event_id"] == "voice-2"
    result = emitted["payload"]["result"]
    assert result["action"] == "prime_context"
    assert result["context"] == "screen context"
    assert result["source_plugin"] == "study_companion"
    assert result["source_event_id"] == "handle_transcript"


def test_voice_bridge_dispatch_results_are_arbitrated() -> None:
    from app import agent_server as srv

    result = srv._voice_bridge_action_from_dispatch_results(
        [
            {
                "plugin_id": "context_plugin",
                "event_id": "prime",
                "success": True,
                "result": {
                    "action": "prime_context",
                    "context": "screen context",
                    "priority": 100,
                },
            },
            {
                "plugin_id": "study_companion",
                "event_id": "handle_transcript",
                "success": True,
                "result": {
                    "action": "cancel_response",
                    "reason": "ocr_overlap",
                    "priority": -10,
                },
            },
            {
                "plugin_id": "broken",
                "event_id": "voice",
                "success": False,
                "error": "timeout",
            },
        ]
    )

    assert result["action"] == "cancel_response"
    assert result["reason"] == "ocr_overlap"
    assert result["source_plugin"] == "study_companion"
    assert result["source_event_id"] == "handle_transcript"
    assert result["failures"] == 1
