from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


def test_agent_api_gate_reports_agent_free_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent_server import api_runtime as srv

    class _Config:
        def is_agent_api_ready(self) -> tuple[bool, list[str]]:
            return True, []

        def is_agent_free(self) -> bool:
            return False

        def is_free_version(self) -> bool:
            return True

    monkeypatch.setattr(srv, "get_config_manager", lambda: _Config())

    assert srv._check_agent_api_gate() == {
        "ready": True,
        "reasons": [],
        "is_free_version": False,
    }


@pytest.mark.asyncio
async def test_voice_transcript_request_reports_lifecycle_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_server import api_runtime as srv
    from plugin.server.application.plugins import voice_transcript_bridge

    emitted: list[tuple[str, str | None, dict[str, Any]]] = []
    resolve_called = False

    async def _start_plugin_lifecycle() -> bool:
        return False

    async def _emit_main_event(event_type: str, lanlan_name: str | None, **payload: Any) -> None:
        emitted.append((event_type, lanlan_name, payload))

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
    assert emitted == []


@pytest.mark.asyncio
async def test_voice_transcript_request_skips_plugins_when_agent_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_server import api_runtime as srv
    from plugin.server.application.plugins import voice_transcript_bridge

    emitted: list[tuple[str, str | None, dict[str, Any]]] = []
    start_called = False
    resolve_called = False

    async def _start_plugin_lifecycle() -> bool:
        nonlocal start_called
        start_called = True
        return True

    async def _emit_main_event(event_type: str, lanlan_name: str | None, **payload: Any) -> None:
        emitted.append((event_type, lanlan_name, payload))

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
    assert emitted == []


@pytest.mark.asyncio
async def test_voice_transcript_request_skips_plugins_for_empty_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_server import api_runtime as srv
    from plugin.server.application.plugins import voice_transcript_bridge

    emitted: list[tuple[str, str | None, dict[str, Any]]] = []
    resolve_called = False

    async def _emit_main_event(event_type: str, lanlan_name: str | None, **payload: Any) -> None:
        emitted.append((event_type, lanlan_name, payload))

    async def _resolve_voice_transcript_request(*_: Any, **__: Any) -> dict[str, Any]:
        nonlocal resolve_called
        resolve_called = True
        return {"action": "noop", "reason": "unexpected_dispatch"}

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
            "event_id": "voice-empty",
            "lanlan_name": "Yui",
            "transcript": "   ",
        }
    )

    assert resolve_called is False
    assert emitted == []


@pytest.mark.asyncio
async def test_voice_transcript_request_skips_plugins_when_user_plugin_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_server import api_runtime as srv
    from plugin.server.application.plugins import voice_transcript_bridge

    emitted: list[tuple[str, str | None, dict[str, Any]]] = []
    start_called = False
    resolve_called = False

    async def _start_plugin_lifecycle() -> bool:
        nonlocal start_called
        start_called = True
        return True

    async def _emit_main_event(event_type: str, lanlan_name: str | None, **payload: Any) -> None:
        emitted.append((event_type, lanlan_name, payload))

    async def _resolve_voice_transcript_request(*_: Any, **__: Any) -> dict[str, Any]:
        nonlocal resolve_called
        resolve_called = True
        return {"action": "noop", "reason": "unexpected_dispatch"}

    monkeypatch.setitem(srv.Modules.agent_flags, "user_plugin_enabled", False)
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
            "event_id": "voice-user-plugin-disabled",
            "lanlan_name": "Yui",
            "transcript": "Yui explain this step",
        }
    )

    assert start_called is False
    assert resolve_called is False
    assert emitted == []


@pytest.mark.asyncio
async def test_voice_transcript_request_uses_arbitrated_custom_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_server import api_runtime as srv
    from plugin.server.application.plugins import voice_transcript_bridge

    emitted: list[tuple[str, str | None, dict[str, Any]]] = []
    captured: dict[str, Any] = {}

    async def _emit_main_event(event_type: str, lanlan_name: str | None, **payload: Any) -> None:
        emitted.append((event_type, lanlan_name, payload))

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
    assert emitted == []
