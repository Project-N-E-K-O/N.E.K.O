# -*- coding: utf-8 -*-
"""Contracts for experimental author-managed mini-game dialogue prompts."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from main_routers.game_router import author_prompt
from main_routers.game_router import runtime
from config.prompts.prompts_minigame_common import (
    get_author_managed_dialogue_host_prompt,
)


def _prompt(messages=None):
    return {
        "mode": "author-managed",
        "messages": messages or [
            {"role": "system", "content": "stable rules"},
            {"role": "user", "content": "round one"},
            {"role": "assistant", "content": "previous reply"},
            {"role": "user", "content": "current event"},
        ],
    }


def test_author_prompt_normalizer_preserves_order_and_content():
    normalized = author_prompt._normalize_author_managed_prompt(_prompt())

    assert [item["role"] for item in normalized["messages"]] == [
        "system", "user", "assistant", "user",
    ]
    assert [item["content"] for item in normalized["messages"]] == [
        "stable rules", "round one", "previous reply", "current event",
    ]


@pytest.mark.parametrize(
    "value",
    [
        {"mode": "host-managed", "messages": [{"role": "user", "content": "x"}]},
        {"mode": "author-managed", "messages": []},
        {"mode": "author-managed", "messages": [{"role": "tool", "content": "x"}]},
        {"mode": "author-managed", "messages": [{"role": "user", "content": ""}]},
        {"mode": "author-managed", "messages": [{"role": "user", "content": "x", "name": "tool"}]},
    ],
)
def test_author_prompt_normalizer_rejects_unsupported_shapes(value):
    with pytest.raises(ValueError):
        author_prompt._normalize_author_managed_prompt(value)


def test_author_prompt_normalizer_enforces_total_bound():
    messages = [
        {"role": "user", "content": "x" * author_prompt._AUTHOR_PROMPT_MAX_CONTENT_CHARS}
        for _ in range(5)
    ]
    with pytest.raises(ValueError, match="total size"):
        author_prompt._normalize_author_managed_prompt(_prompt(messages))


@pytest.mark.parametrize("locale", ["zh-CN", "zh-TW", "en", "ja", "ko", "ru", "pt", "es"])
def test_author_host_prompt_keeps_game_identity_and_exact_watermark(locale):
    prompt = get_author_managed_dialogue_host_prompt(locale).format(
        game_type="test-game",
        name="Test Neko",
        master_name="Player",
        personality="kind",
    )

    assert "test-game" in prompt
    assert "======以上为" in prompt


@pytest.mark.asyncio
async def test_author_prompt_runner_prepends_host_boundary_and_keeps_author_order(monkeypatch):
    captured = {}

    class FakeLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def ainvoke(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(content="hello")

    async def fake_create(*args, **kwargs):
        captured["create_args"] = args
        captured["create_kwargs"] = kwargs
        return FakeLLM()

    monkeypatch.setattr(author_prompt, "_get_character_info", lambda _name: {
        "lanlan_name": "Test Neko",
        "master_name": "Player",
        "lanlan_prompt": "curious and kind",
        "model": "test-model",
        "base_url": "http://127.0.0.1:9999/v1",
        "api_key": "secret",
        "api_type": "local",
        "provider_type": "openai",
        "user_language_full": "en",
    })
    monkeypatch.setattr("utils.llm_client.create_chat_llm_async", fake_create)

    result = await author_prompt._run_author_managed_game_chat(
        "test-game",
        "Test Neko",
        author_prompt._normalize_author_managed_prompt(_prompt()),
        prompt_locale="en",
    )

    messages = captured["messages"]
    assert [message.type for message in messages] == [
        "system", "system", "human", "ai", "human",
    ]
    assert [message.content for message in messages[1:]] == [
        "stable rules", "round one", "previous reply", "current event",
    ]
    assert "Test Neko" in messages[0].content
    assert "======以上为" in messages[0].content
    assert result["reply"] == "hello"
    assert result["source"]["prompt_mode"] == "author-managed"


@pytest.mark.asyncio
async def test_run_game_chat_author_mode_does_not_use_cached_session(monkeypatch):
    state = {
        "game_route_active": True,
        "session_id": "round-1",
    }

    monkeypatch.setattr(runtime, "_find_game_route_state_for_session", lambda *_args: state)

    async def fail_cached_session(*_args, **_kwargs):
        raise AssertionError("author-managed mode must not create a cached Omni session")

    async def fake_author_chat(*_args, **_kwargs):
        return {
            "reply": "Nice shot\n{\"stance\":\"press\",\"pace\":3}",
            "llm_ms": 12,
            "source": {"provider": "test", "prompt_mode": "author-managed"},
            "message_count": 4,
            "roles": ["system", "user", "assistant", "user"],
        }

    monkeypatch.setattr(runtime, "_get_or_create_session", fail_cached_session)
    monkeypatch.setattr(runtime, "_run_author_managed_game_chat", fake_author_chat)
    monkeypatch.setattr(runtime, "_append_game_session_debug_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runtime,
        "_parse_control_instructions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("author-managed controls must not use a legacy game parser")
        ),
    )

    result = await runtime._run_game_chat(
        "test-game",
        "round-1",
        {"kind": "goal", "lanlan_name": "Test Neko"},
        prompt_locale="en",
        author_prompt=_prompt(),
    )

    assert result["line"] == "Nice shot"
    assert result["control"] == {"stance": "press", "pace": 3}
    assert result["llm_source"]["prompt_mode"] == "author-managed"
    assert result["metrics"]["llm_ms"] == 12


@pytest.mark.asyncio
async def test_run_game_chat_author_mode_drops_reply_after_route_exit(monkeypatch):
    active_state = {"game_route_active": True, "session_id": "round-1"}
    inactive_state = {"game_route_active": False, "session_id": "round-1"}
    states = iter([active_state, inactive_state])

    monkeypatch.setattr(runtime, "_find_game_route_state_for_session", lambda *_args: next(states))

    async def fake_author_chat(*_args, **_kwargs):
        return {
            "reply": "late reply",
            "llm_ms": 12,
            "source": {"provider": "test", "prompt_mode": "author-managed"},
            "message_count": 1,
            "roles": ["user"],
        }

    monkeypatch.setattr(runtime, "_run_author_managed_game_chat", fake_author_chat)
    monkeypatch.setattr(runtime, "_append_game_session_debug_log", lambda *_args, **_kwargs: None)

    result = await runtime._run_game_chat(
        "test-game",
        "round-1",
        {"kind": "goal", "lanlan_name": "Test Neko"},
        author_prompt=_prompt([{"role": "user", "content": "current event"}]),
    )

    assert result == {"line": "", "control": {}, "skipped": "route_inactive"}


@pytest.mark.asyncio
async def test_run_game_chat_author_mode_rejects_same_session_replacement(monkeypatch):
    state_a = {
        "game_route_active": True,
        "session_id": "round-1",
        "_sdk_route_instance_id": "route-a",
    }
    state_b = {
        "game_route_active": True,
        "session_id": "round-1",
        "_sdk_route_instance_id": "route-b",
    }
    current = {"state": state_a}
    monkeypatch.setattr(
        runtime,
        "_find_game_route_state_for_session",
        lambda *_args: current["state"],
    )

    async def fake_author_chat(*_args, **_kwargs):
        current["state"] = state_b
        return {
            "reply": "late reply\n{\"stance\":\"press\"}",
            "llm_ms": 12,
            "source": {"provider": "test", "prompt_mode": "author-managed"},
            "message_count": 1,
            "roles": ["user"],
        }

    monkeypatch.setattr(runtime, "_run_author_managed_game_chat", fake_author_chat)
    monkeypatch.setattr(runtime, "_append_game_session_debug_log", lambda *_args, **_kwargs: None)

    result = await runtime._run_game_chat(
        "example-game",
        "round-1",
        {"kind": "turn", "lanlan_name": "Test Neko"},
        author_prompt=_prompt([{"role": "user", "content": "current event"}]),
        expected_route_state=state_a,
        expected_route_instance_id="route-a",
    )

    assert result == {"line": "", "control": {}, "skipped": "route_superseded"}


@pytest.mark.asyncio
async def test_run_game_chat_author_mode_sanitizes_provider_errors(monkeypatch):
    state = {"game_route_active": True, "session_id": "round-1"}
    monkeypatch.setattr(runtime, "_find_game_route_state_for_session", lambda *_args: state)

    async def fail_author_chat(*_args, **_kwargs):
        raise RuntimeError("https://provider.invalid?api_key=secret-value")

    monkeypatch.setattr(runtime, "_run_author_managed_game_chat", fail_author_chat)
    monkeypatch.setattr(runtime, "_append_game_session_debug_log", lambda *_args, **_kwargs: None)

    result = await runtime._run_game_chat(
        "example-game",
        "round-1",
        {"kind": "turn", "lanlan_name": "Test Neko"},
        author_prompt=_prompt([{"role": "user", "content": "current event"}]),
    )

    assert result["error"] == "provider_unavailable"
    assert result["reason"] == "provider_error"
    assert result["error_type"] == "RuntimeError"
    assert "secret-value" not in str(result)


@pytest.mark.asyncio
async def test_game_chat_endpoint_forwards_normalized_author_prompt(monkeypatch):
    captured = {}
    state = {
        "game_route_active": True,
        "session_id": "round-1",
        "_sdk_route_instance_id": "route-a",
    }

    class FakeRequest:
        async def json(self):
            return {
                "session_id": "round-1",
                "sdk_route_instance_id": "route-a",
                "lanlan_name": "Test Neko",
                "event": {"kind": "goal"},
                "prompt": _prompt(),
            }

    async def fake_run(game_type, session_id, event, **kwargs):
        captured.update({
            "game_type": game_type,
            "session_id": session_id,
            "event": event,
            "kwargs": kwargs,
        })
        return {"line": "ok", "control": {}, "metrics": {"total_ms": 1}}

    monkeypatch.setattr(runtime, "_resolve_lanlan_name", lambda value=None: str(value or "Test Neko"))
    monkeypatch.setattr(runtime, "_get_active_game_route_state", lambda *_args: state)
    monkeypatch.setattr(runtime, "_update_game_memory_enabled_from_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_attach_game_memory_flag_to_event", lambda event, *_args, **_kwargs: event)
    monkeypatch.setattr(runtime, "_absorb_request_language", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_update_game_route_language_from_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_resolve_game_prompt_locale", lambda *_args, **_kwargs: "en")
    monkeypatch.setattr(runtime, "_append_game_dialog", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_run_game_chat", fake_run)

    result = await runtime.game_chat("test-game", FakeRequest())

    assert result["line"] == "ok"
    assert captured["kwargs"]["author_prompt"] == _prompt()
    assert captured["kwargs"]["prompt_locale"] == "en"
    assert captured["kwargs"]["expected_route_state"] is state
    assert captured["kwargs"]["expected_route_instance_id"] == "route-a"
    assert captured["event"]["lanlan_name"] == "Test Neko"


@pytest.mark.asyncio
async def test_game_chat_endpoint_accepts_author_prompt_without_game_event(monkeypatch):
    captured = {}
    state = {"game_route_active": True, "session_id": "round-1"}

    class FakeRequest:
        async def json(self):
            return {
                "session_id": "round-1",
                "lanlan_name": "Test Neko",
                "event": None,
                "prompt": _prompt([{"role": "user", "content": "reply to this"}]),
            }

    async def fake_run(game_type, session_id, event, **kwargs):
        captured.update({
            "game_type": game_type,
            "session_id": session_id,
            "event": event,
            "kwargs": kwargs,
        })
        return {"line": "ok", "control": {}, "metrics": {"total_ms": 1}}

    monkeypatch.setattr(runtime, "_resolve_lanlan_name", lambda value=None: str(value or "Test Neko"))
    monkeypatch.setattr(runtime, "_get_active_game_route_state", lambda *_args: state)
    monkeypatch.setattr(runtime, "_update_game_memory_enabled_from_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_attach_game_memory_flag_to_event", lambda event, *_args, **_kwargs: event)
    monkeypatch.setattr(runtime, "_absorb_request_language", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_update_game_route_language_from_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_resolve_game_prompt_locale", lambda *_args, **_kwargs: "en")
    monkeypatch.setattr(runtime, "_append_game_dialog", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_run_game_chat", fake_run)
    monkeypatch.setattr(runtime, "_is_badminton_game_type", lambda _game_type: True)

    def fail_legacy_event_adapter(_event):
        raise AssertionError("author-managed SDK chat must bypass legacy game event adapters")

    monkeypatch.setattr(runtime, "_sanitize_badminton_event", fail_legacy_event_adapter)

    result = await runtime.game_chat("neutral-sdk-game", FakeRequest())

    assert result["line"] == "ok"
    assert captured["game_type"] == "neutral-sdk-game"
    assert captured["event"] is None
    assert captured["kwargs"]["lanlan_name"] == "Test Neko"
    assert captured["kwargs"]["author_prompt"]["mode"] == "author-managed"


@pytest.mark.asyncio
async def test_legacy_game_specific_chat_still_requires_a_registered_event(monkeypatch):
    state = {"game_route_active": True, "session_id": "round-1"}

    class FakeRequest:
        async def json(self):
            return {
                "session_id": "round-1",
                "lanlan_name": "Test Neko",
                "event": None,
            }

    monkeypatch.setattr(runtime, "_resolve_lanlan_name", lambda value=None: str(value or "Test Neko"))
    monkeypatch.setattr(runtime, "_get_active_game_route_state", lambda *_args: state)
    monkeypatch.setattr(runtime, "_update_game_memory_enabled_from_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_check_badminton_chat_rate", lambda *_args: True)
    monkeypatch.setattr(runtime, "_is_badminton_game_type", lambda _game_type: True)
    monkeypatch.setattr(runtime, "_sanitize_badminton_event", lambda _event: (None, "invalid_event"))

    result = await runtime.game_chat("legacy-fixture", FakeRequest())

    assert result["error"] == "invalid_event"


@pytest.mark.asyncio
async def test_game_chat_endpoint_does_not_persist_skipped_result(monkeypatch):
    appended = []
    state = {"game_route_active": True, "session_id": "round-1"}

    class FakeRequest:
        async def json(self):
            return {
                "session_id": "round-1",
                "lanlan_name": "Test Neko",
                "event": {"kind": "goal", "currentState": {"round": 9}},
            }

    async def fake_run(*_args, **_kwargs):
        state["game_route_active"] = False
        return {"line": "", "control": {}, "skipped": "route_inactive"}

    monkeypatch.setattr(runtime, "_resolve_lanlan_name", lambda value=None: str(value or "Test Neko"))
    monkeypatch.setattr(runtime, "_get_active_game_route_state", lambda *_args: state)
    monkeypatch.setattr(runtime, "_update_game_memory_enabled_from_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_attach_game_memory_flag_to_event", lambda event, *_args, **_kwargs: event)
    monkeypatch.setattr(runtime, "_absorb_request_language", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_update_game_route_language_from_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_resolve_game_prompt_locale", lambda *_args, **_kwargs: "en")
    monkeypatch.setattr(runtime, "_append_game_dialog", lambda *args, **_kwargs: appended.append(args))
    monkeypatch.setattr(runtime, "_run_game_chat", fake_run)

    result = await runtime.game_chat("test-game", FakeRequest())

    assert result["skipped"] == "route_inactive"
    assert appended == []
    assert "last_state" not in state


@pytest.mark.asyncio
async def test_game_chat_endpoint_rejects_invalid_author_prompt():
    class FakeRequest:
        async def json(self):
            return {
                "session_id": "round-1",
                "event": {"kind": "goal"},
                "prompt": {
                    "mode": "author-managed",
                    "messages": [{"role": "tool", "content": "not supported"}],
                },
            }

    with pytest.raises(HTTPException) as exc_info:
        await runtime.game_chat("test-game", FakeRequest())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["reason"] == "invalid_author_prompt"
