from __future__ import annotations

import asyncio
import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from plugin.plugins.galgame_plugin import llm_backend as galgame_llm_backend
from plugin.plugins.galgame_plugin.llm_backend import GalgameLLMBackend
from plugin.sdk.plugin import SdkError


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


def _run_in_new_loop(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.mark.plugin_unit
def test_llm_backend_prompt_message_contracts_are_stable() -> None:
    backend = GalgameLLMBackend(_Logger())
    contexts = {
        "explain_line": {"line_id": "line-1", "text": "line text"},
        "summarize_scene": {"scene_id": "scene-a", "lines": [{"text": "line text"}]},
        "suggest_choice": {
            "visible_choices": [
                {"choice_id": "choice-1", "text": "left"},
                {"choice_id": "choice-2", "text": "right"},
            ]
        },
        "agent_reply": {
            "prompt": "what is happening?",
            "public_context": {"scene_id": "scene-a"},
        },
    }
    expected_schema_tokens = {
        "explain_line": ("explanation", "evidence"),
        "summarize_scene": ("summary", "key_points"),
        "suggest_choice": ("choices", "choice_id"),
        "agent_reply": ("reply", "public_context"),
    }

    for operation, context in contexts.items():
        messages = backend._build_messages(operation, context)

        assert [message["role"] for message in messages] == ["system", "user"]
        assert "JSON" in messages[0]["content"]
        assert "context:" in messages[1]["content"]
        for token in expected_schema_tokens[operation]:
            assert token in messages[1]["content"]

    payload = {
        operation: backend._build_messages(operation, context)
        for operation, context in contexts.items()
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == (
        "ca7a55754459201032758f180bdd85f15d3a0b39831cec328105522f976a9f81"
    )


@pytest.mark.plugin_unit
def test_llm_backend_cache_lock_rebinds_between_event_loops() -> None:
    backend = GalgameLLMBackend(_Logger())
    loops: list[asyncio.AbstractEventLoop | None] = []
    locks: list[asyncio.Lock] = []

    async def _lock_identity() -> None:
        lock = backend._cache_lock()
        async with lock:
            loops.append(backend._llm_cache_loop)
            locks.append(lock)

    _run_in_new_loop(_lock_identity())
    _run_in_new_loop(_lock_identity())

    assert loops[0] is not loops[1]
    assert locks[0] is not locks[1]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_backend_json_correction_prompt_preserves_invalid_reply() -> None:
    backend = GalgameLLMBackend(_Logger())
    calls: list[list[dict[str, str]]] = []

    async def _fake_call_model(*, operation: str, messages):
        calls.append(messages)
        if len(calls) == 1:
            return "not-json"
        return '{"reply": "ok"}'

    backend._call_model = _fake_call_model  # type: ignore[method-assign]

    raw_text = await backend._invoke_json_with_correction(
        operation="agent_reply",
        messages=[
            {"role": "system", "content": "system JSON"},
            {"role": "user", "content": "user context"},
        ],
    )

    assert raw_text == '{"reply": "ok"}'
    assert len(calls) == 2
    assert calls[1][-2] == {"role": "assistant", "content": "not-json"}
    assert calls[1][-1]["role"] == "user"
    assert "JSON" in calls[1][-1]["content"]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_backend_cache_key_uses_api_key_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-test-secret"
    captured: dict[str, object] = {}

    class _Config:
        def get_model_api_config(self, role: str) -> dict[str, str]:
            assert role == "summary"
            return {
                "base_url": "https://llm.example.test",
                "model": "demo-model",
                "api_key": secret,
            }

    class _FakeLLM:
        async def ainvoke(self, messages):
            del messages
            return type("Response", (), {"content": "{}"})()

    async def _fake_get_or_create_llm(**kwargs):
        captured.update(kwargs)
        return _FakeLLM()

    backend = GalgameLLMBackend(_Logger())
    monkeypatch.setattr(galgame_llm_backend, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(backend, "_get_or_create_llm", _fake_get_or_create_llm)

    result = await backend._call_model(
        operation="explain_line",
        messages=[{"role": "user", "content": "{}"}],
    )

    expected_fingerprint = f"k:{hash(secret) & 0xFFFFFFFF:08x}"
    cache_key = captured["cache_key"]
    assert result == "{}"
    assert captured["api_key"] == secret
    assert secret not in repr(cache_key)
    assert expected_fingerprint in cache_key


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_backend_retries_transient_model_call_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    class _Config:
        def get_model_api_config(self, role: str) -> dict[str, str]:
            assert role == "summary"
            return {
                "base_url": "https://llm.example.test",
                "model": "demo-model",
                "api_key": "sk-test",
            }

    class _FakeLLM:
        async def ainvoke(self, messages):
            nonlocal calls
            del messages
            calls += 1
            if calls < 3:
                raise TimeoutError(f"temporary failure {calls}")
            return type("Response", (), {"content": '{"ok": true}'})()

    async def _fake_get_or_create_llm(**kwargs):
        del kwargs
        return _FakeLLM()

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    backend = GalgameLLMBackend(_Logger())
    monkeypatch.setattr(galgame_llm_backend, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(galgame_llm_backend.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(backend, "_get_or_create_llm", _fake_get_or_create_llm)

    result = await backend._call_model(
        operation="explain_line",
        messages=[{"role": "user", "content": "{}"}],
    )

    assert result == '{"ok": true}'
    assert calls == 3
    assert sleeps == [0.25, 0.5]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_backend_attaches_vision_image_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_messages: list[list[dict[str, object]]] = []

    class _Config:
        def get_model_api_config(self, role: str) -> dict[str, str]:
            assert role == "agent"
            return {
                "base_url": "https://llm.example.test",
                "model": "gpt-4o-mini",
                "api_key": "sk-test",
            }

    class _FakeLLM:
        async def ainvoke(self, messages):
            captured_messages.append(messages)
            return type("Response", (), {"content": '{"reply": "ok"}'})()

    async def _fake_get_or_create_llm(**kwargs):
        del kwargs
        return _FakeLLM()

    backend = GalgameLLMBackend(_Logger())
    monkeypatch.setattr(galgame_llm_backend, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(backend, "_get_or_create_llm", _fake_get_or_create_llm)

    result = await backend.invoke(
        operation="agent_reply",
        context={
            "prompt": "what is on screen?",
            "public_context": {},
            "vision_enabled": True,
            "vision_image_base64": "abc123",
        },
    )

    content = captured_messages[0][-1]["content"]
    assert result["reply"] == "ok"
    assert isinstance(content, list)
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,abc123"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_backend_strips_vision_image_for_non_vision_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_messages: list[list[dict[str, object]]] = []

    class _Config:
        def get_model_api_config(self, role: str) -> dict[str, str]:
            assert role == "summary"
            return {
                "base_url": "https://llm.example.test",
                "model": "text-only-model",
                "api_key": "sk-test",
            }

    class _FakeLLM:
        async def ainvoke(self, messages):
            captured_messages.append(messages)
            return type("Response", (), {"content": "{}"})()

    async def _fake_get_or_create_llm(**kwargs):
        del kwargs
        return _FakeLLM()

    backend = GalgameLLMBackend(_Logger())
    monkeypatch.setattr(galgame_llm_backend, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(backend, "_get_or_create_llm", _fake_get_or_create_llm)

    result = await backend._call_model(
        operation="explain_line",
        messages=[
            {"role": "user", "content": [
                {"type": "text", "text": "context"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ]},
        ],
    )

    assert result == "{}"
    assert captured_messages[0][0]["content"] == "context"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_backend_explain_line_parses_fenced_json() -> None:
    backend = GalgameLLMBackend(_Logger())
    captured: list[str] = []

    async def _fake_call_model(*, operation: str, messages):
        captured.append(operation)
        return """```json
        {
          "explanation": "这句台词是在试探对方的态度。",
          "evidence": [
            {
              "type": "current_line",
              "text": "今天一起回家吗？",
              "line_id": "line-1",
              "speaker": "雪乃",
              "scene_id": "scene-a",
              "route_id": ""
            }
          ]
        }
        ```"""

    backend._call_model = _fake_call_model  # type: ignore[method-assign]
    result = await backend.invoke(
        operation="explain_line",
        context={
            "line_id": "line-1",
            "speaker": "雪乃",
            "text": "今天一起回家吗？",
            "evidence": [],
        },
    )

    assert captured == ["explain_line"]
    assert result["explanation"] == "这句台词是在试探对方的态度。"
    assert result["evidence"][0]["type"] == "current_line"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_backend_suggest_choice_filters_invalid_items_and_renumbers() -> None:
    backend = GalgameLLMBackend(_Logger())

    async def _fake_call_model(*, operation: str, messages):
        return """{
          "choices": [
            {"choice_id": "ghost", "text": "不存在", "rank": 1, "reason": "无效"},
            {"choice_id": "choice-2", "text": "右边", "rank": 2, "reason": "更符合当前目标"},
            {"choice_id": "choice-2", "text": "右边", "rank": 3, "reason": "重复"},
            {"choice_id": "choice-1", "text": "左边", "rank": 4, "reason": "备选"}
          ]
        }"""

    backend._call_model = _fake_call_model  # type: ignore[method-assign]
    result = await backend.invoke(
        operation="suggest_choice",
        context={
            "visible_choices": [
                {"choice_id": "choice-1", "text": "左边", "index": 0, "enabled": True},
                {"choice_id": "choice-2", "text": "右边", "index": 1, "enabled": True},
            ]
        },
    )

    assert result["choices"] == [
        {
            "choice_id": "choice-2",
            "text": "右边",
            "rank": 2,
            "reason": "更符合当前目标",
        },
        {
            "choice_id": "choice-1",
            "text": "左边",
            "rank": 4,
            "reason": "备选",
        },
    ]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_backend_agent_reply_retries_once_when_first_reply_is_not_json() -> None:
    backend = GalgameLLMBackend(_Logger())
    calls = {"count": 0}

    async def _fake_call_model(*, operation: str, messages):
        calls["count"] += 1
        if calls["count"] == 1:
            return "这不是 JSON"
        return '{"reply": "当前在放学后的对话场景。"}'

    backend._call_model = _fake_call_model  # type: ignore[method-assign]
    result = await backend.invoke(
        operation="agent_reply",
        context={"prompt": "现在在讲什么？", "scene_id": "scene-a"},
    )

    assert calls["count"] == 2
    assert result["reply"] == "当前在放学后的对话场景。"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_backend_rejects_unknown_operation() -> None:
    backend = GalgameLLMBackend(_Logger())
    with pytest.raises(SdkError, match="unsupported operation"):
        await backend.invoke(operation="unknown", context={})


@pytest.mark.plugin_unit
def test_galgame_plugin_toml_defaults_to_internal_backend() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "galgame_plugin"
        / "plugin.toml"
    )
    with path.open("rb") as handle:
        payload = tomllib.load(handle)

    assert payload["llm"]["target_entry_ref"] == ""
