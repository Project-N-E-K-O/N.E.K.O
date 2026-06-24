from types import SimpleNamespace

import pytest

from main_routers import icebreaker_router, system_router
from main_routers.system_router import AUTOSTART_CSRF_TOKEN
from utils import icebreaker_route_state
from utils.game_route_state import _get_active_game_route_state


class _FakeRequest:
    def __init__(self, payload, *, mutation_headers=True, path="/api/icebreaker/context"):
        self._payload = payload
        self.base_url = "http://127.0.0.1:8000/"
        self.url = SimpleNamespace(path=path)
        self.method = "POST"
        self.headers = {}
        if mutation_headers:
            self.headers = {
                "origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": AUTOSTART_CSRF_TOKEN,
            }

    async def json(self):
        return self._payload


class _FakeAppendContextManager:
    def __init__(self, result=None, error=None, speech_error=None):
        self.calls = []
        self.spoken = []
        self.result = result or SimpleNamespace(appended=True, deduped=False, reason=None)
        self.error = error
        self.speech_error = speech_error

    async def append_context(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result

    async def mirror_assistant_speech(self, line, **kwargs):
        self.spoken.append((line, kwargs))
        if self.speech_error is not None:
            raise self.speech_error
        return {"ok": True, "audio_sent": True}


class _FakeConfigManager:
    def __init__(self, characters=None):
        self._characters = characters or {"当前猫娘": "Lan"}

    def load_characters(self):
        return self._characters


def _allow_local_mutation(request, payload=None, **kwargs):
    return None


async def _fake_cache_memory(**kwargs):
    return True, ""


@pytest.mark.asyncio
async def test_icebreaker_route_start_does_not_activate_game_route(monkeypatch):
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {})

    result = await icebreaker_router.icebreaker_route_start(
        _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1"})
    )

    assert result["ok"] is True
    assert result["state"]["icebreaker_active"] is True
    assert icebreaker_route_state._get_active_icebreaker_route_state("Lan") is not None
    assert _get_active_game_route_state("Lan") is None


@pytest.mark.asyncio
async def test_icebreaker_route_start_falls_back_to_current_character(monkeypatch):
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {})
    monkeypatch.setattr(icebreaker_router, "get_config_manager", lambda: _FakeConfigManager({"当前猫娘": "YUI"}))

    result = await icebreaker_router.icebreaker_route_start(
        _FakeRequest({"session_id": "icebreaker-day1"})
    )

    assert result["ok"] is True
    assert result["state"]["lanlan_name"] == "YUI"
    assert icebreaker_route_state._get_active_icebreaker_route_state("YUI") is not None


@pytest.mark.asyncio
async def test_icebreaker_route_start_requires_local_mutation_csrf(monkeypatch):
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {})

    result = await icebreaker_router.icebreaker_route_start(
        _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1"}, mutation_headers=False)
    )

    assert result.status_code == 403
    assert b"csrf_validation_failed" in result.body
    assert icebreaker_route_state._get_active_icebreaker_route_state("Lan") is None


@pytest.mark.asyncio
async def test_icebreaker_context_endpoint_appends_session_history(monkeypatch):
    mgr = _FakeAppendContextManager()
    memory_cache_calls = []

    async def fake_cache_memory(**kwargs):
        memory_cache_calls.append(kwargs)
        return True, ""

    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    monkeypatch.setattr(icebreaker_router, "_cache_icebreaker_context_memory", fake_cache_memory)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_context(
        _FakeRequest({
            "lanlan_name": "Lan",
            "role": "assistant",
            "text": "教程看完啦？",
            "session_id": "icebreaker-day1-test",
        })
    )

    assert result["ok"] is True
    assert result["method"] == "project_session_history"
    assert mgr.calls == [{
        "source": "icebreaker",
        "role": "assistant",
        "text": "教程看完啦？",
        "audience": "model",
        "timing": "when_ready",
        "lifetime": "session_family",
        "request_id": None,
        "ordering_key": "icebreaker-day1-test",
        "metadata": {
            "source": "new_user_icebreaker",
            "session_id": "icebreaker-day1-test",
        },
    }]
    assert memory_cache_calls == [{
        "lanlan_name": "Lan",
        "role": "assistant",
        "text": "教程看完啦？",
    }]


@pytest.mark.asyncio
async def test_icebreaker_context_caches_user_choice_to_recent_memory(monkeypatch):
    mgr = _FakeAppendContextManager()
    memory_cache_calls = []

    async def fake_cache_memory(**kwargs):
        memory_cache_calls.append(kwargs)
        return True, ""

    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    monkeypatch.setattr(icebreaker_router, "_cache_icebreaker_context_memory", fake_cache_memory)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_context(
        _FakeRequest({
            "lanlan_name": "Lan",
            "role": "user",
            "text": "可以，多陪一会儿",
            "session_id": "icebreaker-day1-test",
            "request_id": "choice-1",
        })
    )

    assert result["ok"] is True
    assert result["memory_cached"] is True
    assert memory_cache_calls == [{
        "lanlan_name": "Lan",
        "role": "user",
        "text": "可以，多陪一会儿",
    }]


@pytest.mark.asyncio
async def test_icebreaker_context_cache_failure_does_not_block_context(monkeypatch, caplog):
    mgr = _FakeAppendContextManager()
    memory_cache_calls = []

    async def fake_cache_memory(**kwargs):
        memory_cache_calls.append(kwargs)
        return False, "timeout"

    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    monkeypatch.setattr(icebreaker_router, "_cache_icebreaker_context_memory", fake_cache_memory)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_context(
        _FakeRequest({
            "lanlan_name": "Lan",
            "role": "assistant",
            "text": "教程看完啦？",
            "session_id": "icebreaker-day1-test",
            "request_id": "line-1",
        })
    )

    assert result["ok"] is True
    assert result["memory_cached"] is False
    assert mgr.calls[0]["text"] == "教程看完啦？"
    assert memory_cache_calls == [{
        "lanlan_name": "Lan",
        "role": "assistant",
        "text": "教程看完啦？",
    }]
    assert "icebreaker memory cache failed" in caplog.text


@pytest.mark.asyncio
async def test_icebreaker_context_deduped_response_keeps_memory_cached_field(monkeypatch):
    mgr = _FakeAppendContextManager(result=SimpleNamespace(appended=False, deduped=True, reason="duplicate"))
    memory_cache_calls = []

    async def fake_cache_memory(**kwargs):
        memory_cache_calls.append(kwargs)
        return True, ""

    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    monkeypatch.setattr(icebreaker_router, "_cache_icebreaker_context_memory", fake_cache_memory)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_context(
        _FakeRequest({
            "lanlan_name": "Lan",
            "role": "assistant",
            "text": "教程看完啦？",
            "session_id": "icebreaker-day1-test",
            "request_id": "line-1",
        })
    )

    assert result["ok"] is True
    assert result["deduped"] is True
    assert result["memory_cached"] is False
    assert memory_cache_calls == []


@pytest.mark.asyncio
async def test_icebreaker_context_falls_back_to_active_session_id(monkeypatch):
    mgr = _FakeAppendContextManager()
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    monkeypatch.setattr(icebreaker_router, "_cache_icebreaker_context_memory", _fake_cache_memory)
    icebreaker_route_state.activate_icebreaker_route("Lan", "active-session")

    result = await icebreaker_router.icebreaker_context(
        _FakeRequest({
            "lanlan_name": "Lan",
            "role": "assistant",
            "text": "missing session still belongs to the active icebreaker",
        })
    )

    assert result["ok"] is True
    assert result["session_id"] == "active-session"
    assert mgr.calls[0]["ordering_key"] == "active-session"
    assert mgr.calls[0]["metadata"]["session_id"] == "active-session"


@pytest.mark.asyncio
async def test_icebreaker_context_memory_cache_uses_existing_cache_endpoint(monkeypatch):
    calls = []

    async def fake_post_memory_server(endpoint, lanlan_name, payload, *, timeout_s):
        calls.append({
            "endpoint": endpoint,
            "lanlan_name": lanlan_name,
            "payload": payload,
            "timeout_s": timeout_s,
        })
        return True, "", {"status": "cached", "count": 1}

    import main_logic.cross_server as cross_server

    monkeypatch.setattr(cross_server, "_post_memory_server", fake_post_memory_server)

    ok, err = await icebreaker_router._cache_icebreaker_context_memory(
        lanlan_name="Lan",
        role="user",
        text="可以，多陪一会儿",
    )

    assert ok is True
    assert err == ""
    assert calls == [{
        "endpoint": "cache",
        "lanlan_name": "Lan",
        "payload": [{
            "role": "user",
            "content": [{"type": "text", "text": "可以，多陪一会儿"}],
        }],
        "timeout_s": icebreaker_router.ICEBREAKER_MEMORY_CACHE_TIMEOUT_SECONDS,
    }]


@pytest.mark.asyncio
async def test_icebreaker_context_rejects_stale_session(monkeypatch):
    mgr = _FakeAppendContextManager(error=AssertionError("stale context must not append"))
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    icebreaker_route_state.activate_icebreaker_route("Lan", "active-session")

    result = await icebreaker_router.icebreaker_context(
        _FakeRequest({
            "lanlan_name": "Lan",
            "role": "assistant",
            "text": "late line",
            "session_id": "old-session",
        })
    )

    assert result["ok"] is True
    assert result["skipped"] == "stale_session"
    assert result["reason"] == "session_id_mismatch"
    assert result["method"] == "project_session_history"
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_icebreaker_speak_uses_independent_project_tts(monkeypatch):
    mgr = _FakeAppendContextManager()
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_speak(
        _FakeRequest({
            "lanlan_name": "Lan",
            "line": "现在开始跟我聊天吧",
            "session_id": "icebreaker-day1-test",
            "request_id": "icebreaker-tts-1",
            "mirror_text": False,
            "emit_turn_end": True,
            "interrupt_audio": True,
        })
    )

    assert result["ok"] is True
    assert result["method"] == "project_tts"
    assert mgr.spoken == [("现在开始跟我聊天吧", {
        "metadata": {
            "source": "new_user_icebreaker",
            "kind": "new_user_icebreaker",
            "session_id": "icebreaker-day1-test",
            "mirror": {
                "kind": "new_user_icebreaker",
                "session_id": "icebreaker-day1-test",
                "event": {},
            },
        },
        "request_id": "icebreaker-tts-1",
        "mirror_text": False,
        "emit_turn_end_after": True,
        "interrupt_audio": True,
    })]


@pytest.mark.asyncio
async def test_icebreaker_speak_coerces_numeric_false_options(monkeypatch):
    mgr = _FakeAppendContextManager()
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_speak(
        _FakeRequest({
            "lanlan_name": "Lan",
            "line": "现在开始跟我聊天吧",
            "session_id": "icebreaker-day1-test",
            "mirror_text": 0,
            "emit_turn_end": 0,
        })
    )

    assert result["ok"] is True
    assert mgr.spoken[0][1]["mirror_text"] is False
    assert mgr.spoken[0][1]["emit_turn_end_after"] is False


@pytest.mark.asyncio
async def test_icebreaker_speak_falls_back_to_active_session_id(monkeypatch):
    mgr = _FakeAppendContextManager()
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    icebreaker_route_state.activate_icebreaker_route("Lan", "active-session")

    result = await icebreaker_router.icebreaker_speak(
        _FakeRequest({
            "lanlan_name": "Lan",
            "line": "现在开始跟我聊天吧",
        })
    )

    assert result["ok"] is True
    assert mgr.spoken[0][1]["metadata"]["session_id"] == "active-session"


@pytest.mark.asyncio
async def test_icebreaker_speak_returns_structured_failure_when_project_tts_fails(monkeypatch):
    mgr = _FakeAppendContextManager(speech_error=RuntimeError("tts down"))
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(system_router, "_validate_local_mutation_request", _allow_local_mutation)
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_speak(
        _FakeRequest({
            "lanlan_name": "Lan",
            "line": "现在开始跟我聊天吧",
            "session_id": "icebreaker-day1-test",
        })
    )

    assert result["ok"] is False
    assert result["reason"] == "project_tts_failed"
    assert result["audio_sent"] is False
    assert result["method"] == "project_tts"


@pytest.mark.asyncio
async def test_icebreaker_route_end_clears_only_icebreaker_state(monkeypatch):
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {})
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1-test")

    result = await icebreaker_router.icebreaker_route_end(
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "icebreaker-day1-test",
            "reason": "icebreaker_handoff",
        })
    )

    assert result["ok"] is True
    assert result["state"]["icebreaker_active"] is False
    assert icebreaker_route_state._get_active_icebreaker_route_state("Lan") is None
    assert _get_active_game_route_state("Lan") is None


@pytest.mark.asyncio
async def test_icebreaker_route_end_rejects_stale_session(monkeypatch):
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {})
    icebreaker_route_state.activate_icebreaker_route("Lan", "active-session")

    result = await icebreaker_router.icebreaker_route_end(
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "old-session",
            "reason": "icebreaker_handoff",
        })
    )

    assert result["ok"] is False
    assert result["reason"] == "session_id_mismatch"
    assert result["method"] == "route_end"
    assert icebreaker_route_state._get_active_icebreaker_route_state("Lan") is not None
