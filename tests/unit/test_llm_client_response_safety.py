# -*- coding: utf-8 -*-
"""Regression tests for ChatOpenAI defensive response reads.

Background: free-agent-model 上游会返回 HTTP 200 + choices 非空，但
choices[0].message 是 None 的合法响应。原来 ainvoke/invoke 直接
.message.content 会触发 'NoneType' object has no attribute 'content'，
连通性预检随之失败。这里固定该场景下不再崩溃、content 退化为 ""。
"""
from __future__ import annotations

import asyncio
import gc
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

import utils.llm_client as llm_client_module


def _make_client_with_response(resp) -> llm_client_module.ChatOpenAI:
    """Construct a ChatOpenAI and stub both sync/async create() to return resp."""
    client = llm_client_module.ChatOpenAI(
        model="free-agent-model",
        base_url="https://example.com/v1",
        api_key="free-access",
    )
    client._aclient = MagicMock()
    client._aclient.chat = MagicMock()
    client._aclient.chat.completions = MagicMock()
    client._aclient.chat.completions.create = AsyncMock(return_value=resp)
    client._client = MagicMock()
    client._client.chat = MagicMock()
    client._client.chat.completions = MagicMock()
    client._client.chat.completions.create = MagicMock(return_value=resp)
    return client


def _resp_with_none_message():
    """choices=[choice], choice.message is None — what free-agent-model returns."""
    resp = MagicMock()
    choice = MagicMock()
    choice.message = None
    resp.choices = [choice]
    resp.usage = None
    return resp


def _resp_with_empty_choices():
    resp = MagicMock()
    resp.choices = []
    resp.usage = None
    return resp


def _resp_with_none_content():
    resp = MagicMock()
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = None
    resp.choices = [choice]
    resp.usage = None
    return resp


class TestAinvokeDefensiveRead:
    @pytest.mark.asyncio
    async def test_none_message_returns_empty_string(self):
        client = _make_client_with_response(_resp_with_none_message())
        out = await client.ainvoke([{"role": "user", "content": "hi"}])
        assert out.content == ""

    @pytest.mark.asyncio
    async def test_empty_choices_returns_empty_string(self):
        client = _make_client_with_response(_resp_with_empty_choices())
        out = await client.ainvoke([{"role": "user", "content": "hi"}])
        assert out.content == ""

    @pytest.mark.asyncio
    async def test_none_content_returns_empty_string(self):
        client = _make_client_with_response(_resp_with_none_content())
        out = await client.ainvoke([{"role": "user", "content": "hi"}])
        assert out.content == ""


class TestInvokeDefensiveRead:
    def test_none_message_returns_empty_string(self):
        client = _make_client_with_response(_resp_with_none_message())
        out = client.invoke([{"role": "user", "content": "hi"}])
        assert out.content == ""

    def test_empty_choices_returns_empty_string(self):
        client = _make_client_with_response(_resp_with_empty_choices())
        out = client.invoke([{"role": "user", "content": "hi"}])
        assert out.content == ""

    def test_none_content_returns_empty_string(self):
        client = _make_client_with_response(_resp_with_none_content())
        out = client.invoke([{"role": "user", "content": "hi"}])
        assert out.content == ""


@pytest.mark.asyncio
async def test_create_chat_llm_async_offloads_factory(monkeypatch):
    event_loop_thread_id = threading.get_ident()
    calls = []
    sentinel = object()

    def fake_create_chat_llm(*args, **kwargs):
        calls.append((threading.get_ident(), args, kwargs))
        return sentinel

    monkeypatch.setattr(llm_client_module, "create_chat_llm", fake_create_chat_llm)

    result = await llm_client_module.create_chat_llm_async(
        "model-a",
        "https://example.com/v1",
        "sk-test",
        timeout=3,
        max_retries=0,
    )

    assert result is sentinel
    assert calls == [
        (
            calls[0][0],
            ("model-a", "https://example.com/v1", "sk-test"),
            {"timeout": 3, "max_retries": 0},
        )
    ]
    assert calls[0][0] != event_loop_thread_id


@pytest.mark.asyncio
async def test_create_chat_llm_async_closes_late_result_after_cancellation(
    monkeypatch,
):
    started = threading.Event()
    release = threading.Event()
    closed = asyncio.Event()

    class _LateLLM:
        async def aclose(self) -> None:
            closed.set()

    def fake_create_chat_llm(*_args, **_kwargs):
        started.set()
        release.wait(timeout=5)
        return _LateLLM()

    monkeypatch.setattr(llm_client_module, "create_chat_llm", fake_create_chat_llm)

    task = asyncio.create_task(
        llm_client_module.create_chat_llm_async(
            "model-a",
            "https://example.com/v1",
            "sk-test",
            timeout=3,
            max_completion_tokens=10,
        )
    )
    await asyncio.wait_for(asyncio.to_thread(started.wait, 5), timeout=1)

    task.cancel()
    try:
        result = await task
    except asyncio.CancelledError:
        pass
    else:
        pytest.fail(f"expected cancellation, got {result!r}")

    release.set()
    await asyncio.wait_for(closed.wait(), timeout=2)


def test_create_chat_llm_routes_kimi_code_to_anthropic_client(monkeypatch):
    class _FakeAnthropic:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def close(self):
            pass

    class _FakeAsyncAnthropic(_FakeAnthropic):
        async def close(self):
            pass

    monkeypatch.setattr(llm_client_module, "Anthropic", _FakeAnthropic)
    monkeypatch.setattr(llm_client_module, "AsyncAnthropic", _FakeAsyncAnthropic)

    client = llm_client_module.create_chat_llm(
        "kimi-for-coding",
        "https://api.kimi.com/coding",
        "sk-test",
        max_retries=0,
    )
    try:
        assert isinstance(client, llm_client_module.ChatAnthropic)
        assert client._client.kwargs["base_url"] == "https://api.kimi.com/coding"
        assert client._client.kwargs["default_headers"]["User-Agent"] == "claude-code/0.1.0"
    finally:
        client.close()


def test_create_chat_llm_keeps_kimi_openai_compatible_url_on_openai_client():
    client = llm_client_module.create_chat_llm(
        "kimi-for-coding",
        "https://api.kimi.com/coding/v1",
        "sk-test",
        max_completion_tokens=10,
        timeout=1,
    )
    try:
        assert isinstance(client, llm_client_module.ChatOpenAI)
        assert not llm_client_module._is_anthropic_endpoint("https://api.kimi.com/coding/v1")
    finally:
        client.close()


def test_anthropic_image_media_type_requires_webp_fourcc():
    webp = b"RIFF\x00\x00\x00\x00WEBPVP8 "
    wav = b"RIFF\x00\x00\x00\x00WAVEfmt "

    assert llm_client_module._detect_image_media_type(webp) == "image/webp"
    assert llm_client_module._detect_image_media_type(wav) == "image/jpeg"


def test_chat_anthropic_defaults_and_forwards_payload_overrides(monkeypatch):
    captured = {}

    class _TextBlock:
        type = "text"
        text = "ok"

    class _Resp:
        content = [_TextBlock()]
        usage = None

    class _Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _Resp()

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.messages = _Messages()

        def close(self):
            pass

    class _FakeAsyncAnthropic(_FakeAnthropic):
        async def close(self):
            pass

    monkeypatch.setattr(llm_client_module, "Anthropic", _FakeAnthropic)
    monkeypatch.setattr(llm_client_module, "AsyncAnthropic", _FakeAsyncAnthropic)

    client = llm_client_module.ChatAnthropic(
        model="claude-test",
        base_url="https://api.anthropic.com/v1",
        api_key="sk-test",
    )
    try:
        assert client._client.kwargs["base_url"] == "https://api.anthropic.com"
        default_payload = client._build_payload([{"role": "user", "content": "hi"}])
        assert default_payload["max_tokens"] == 2048

        response = client.invoke(
            [{"role": "user", "content": "hi"}],
            max_completion_tokens=0,
            metadata={"source": "unit", "user_id": "user-1"},
            extra_body={"thinking": {"type": "disabled"}, "metadata": {"user_id": "body-user"}},
            tools=[{
                "type": "function",
                "function": {
                    "name": "noop",
                    "description": "Do nothing",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
            tool_choice="auto",
            stream=True,
        )

        assert response.content == "ok"
        assert captured["max_tokens"] == 1
        assert captured["metadata"] == {"user_id": "user-1"}
        assert captured["thinking"] == {"type": "disabled"}
        assert "stream" not in captured
        assert captured["tools"] == [{
            "name": "noop",
            "input_schema": {"type": "object", "properties": {}},
            "description": "Do nothing",
        }]
        assert captured["tool_choice"] == {"type": "auto"}
        assert "extra_body" not in captured
    finally:
        client.close()


def test_chat_anthropic_explicit_empty_extra_body_skips_default(monkeypatch):
    captured = {}
    recorded = []

    class _Usage:
        def model_dump(self):
            return {
                "input_tokens": 11,
                "output_tokens": 7,
                "cache_read_input_tokens": 3,
            }

    class _TextBlock:
        type = "text"
        text = "ok"

    class _Resp:
        content = [_TextBlock()]
        usage = _Usage()

    class _Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _Resp()

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.messages = _Messages()

        def close(self):
            pass

    class _FakeAsyncAnthropic(_FakeAnthropic):
        async def close(self):
            pass

    monkeypatch.setattr(llm_client_module, "Anthropic", _FakeAnthropic)
    monkeypatch.setattr(llm_client_module, "AsyncAnthropic", _FakeAsyncAnthropic)
    monkeypatch.setattr(
        llm_client_module,
        "_record_anthropic_token_usage",
        lambda model, usage: recorded.append((model, dict(usage))),
    )

    client = llm_client_module.ChatAnthropic(
        model="claude-test",
        base_url="https://api.anthropic.com",
        api_key="sk-test",
        extra_body={"thinking": {"type": "disabled"}},
    )
    try:
        default_payload = client._build_payload([{"role": "user", "content": "hi"}])
        assert default_payload["thinking"] == {"type": "disabled"}

        response = client.invoke([{"role": "user", "content": "hi"}], extra_body=None)

        assert response.content == "ok"
        assert "thinking" not in captured
        assert recorded == [
            (
                "claude-test",
                {"input_tokens": 11, "output_tokens": 7, "cache_read_input_tokens": 3},
            )
        ]
    finally:
        client.close()


def test_chat_anthropic_max_completion_tokens_property_sync(monkeypatch):
    class _FakeAnthropic:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def close(self):
            pass

    class _FakeAsyncAnthropic(_FakeAnthropic):
        async def close(self):
            pass

    monkeypatch.setattr(llm_client_module, "Anthropic", _FakeAnthropic)
    monkeypatch.setattr(llm_client_module, "AsyncAnthropic", _FakeAsyncAnthropic)
    monkeypatch.setattr(llm_client_module, "_record_anthropic_token_usage", lambda *_args: None)

    client = llm_client_module.ChatAnthropic(
        model="claude-test",
        base_url="https://api.anthropic.com",
        api_key="sk-test",
        max_completion_tokens=123,
    )
    try:
        assert client.max_tokens == 123
        assert client.max_completion_tokens == 123

        client.max_completion_tokens = 3000
        assert client.max_tokens == 3000
        assert client._build_payload([{"role": "user", "content": "hi"}])["max_tokens"] == 3000

        client.max_tokens = None
        assert client.max_completion_tokens == 2048

        client.max_completion_tokens = 0
        assert client.max_tokens == 1
    finally:
        client.close()


def test_anthropic_message_normalization_handles_system_only_and_empty():
    system, messages = llm_client_module._normalize_messages_to_anthropic(
        [{"role": "system", "content": "system prompt"}]
    )
    assert system == "system prompt"
    assert messages == [{"role": "user", "content": [{"type": "text", "text": "..."}]}]

    empty_system, empty_messages = llm_client_module._normalize_messages_to_anthropic([])
    assert empty_system == ""
    assert empty_messages == [{"role": "user", "content": [{"type": "text", "text": "..."}]}]


def test_anthropic_message_normalization_preserves_tool_turns_without_none_text():
    _system, messages = llm_client_module._normalize_messages_to_anthropic([
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{\"q\":\"neko\"}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
    ])

    assert messages[0]["role"] == "user"
    assert messages[1]["content"] == [
        {"type": "tool_use", "id": "call_1", "name": "lookup", "input": {"q": "neko"}}
    ]
    assert messages[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "result"}
    ]
    assert "None" not in repr(messages)


def test_anthropic_message_normalization_dedupes_existing_tool_use_blocks():
    _system, messages = llm_client_module._normalize_messages_to_anthropic([
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "lookup",
                    "input": {"q": "neko"},
                }
            ],
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{\"q\":\"neko\"}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
    ])

    assistant_blocks = messages[1]["content"]
    assert [block.get("id") for block in assistant_blocks if block.get("type") == "tool_use"] == ["call_1"]
    assert messages[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "result"}
    ]


def test_anthropic_message_normalization_keeps_pending_tool_use_across_assistant_text():
    _system, messages = llm_client_module._normalize_messages_to_anthropic([
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{\"q\":\"neko\"}"},
                }
            ],
        },
        {"role": "assistant", "content": "Let me check..."},
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
    ])

    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == [
        {"type": "tool_use", "id": "call_1", "name": "lookup", "input": {"q": "neko"}},
        {"type": "text", "text": "Let me check..."},
    ]
    assert messages[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "result"}
    ]


def test_anthropic_message_normalization_downgrades_orphan_tool_result():
    _system, messages = llm_client_module._normalize_messages_to_anthropic([
        {"role": "user", "content": "start"},
        {"role": "tool", "tool_call_id": "call_orphan", "content": "orphan result"},
    ])

    assert "tool_result" not in repr(messages)
    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "start"},
                {"type": "text", "text": "[tool result] orphan result"},
            ],
        }
    ]


@pytest.mark.asyncio
async def test_chat_anthropic_stream_helper_does_not_forward_stream_kwarg(monkeypatch):
    captured = {}

    class _Usage:
        def __init__(self, **data):
            self._data = data

        def model_dump(self):
            return dict(self._data)

    class _Message:
        usage = _Usage(input_tokens=2)

    class _MessageStart:
        type = "message_start"
        message = _Message()

    class _TextDelta:
        type = "text_delta"
        text = "ok"

    class _Event:
        type = "content_block_delta"
        delta = _TextDelta()

    class _StopDelta:
        stop_reason = "end_turn"

    class _MessageDelta:
        type = "message_delta"
        delta = _StopDelta()
        usage = _Usage(output_tokens=3)

    class _StreamContext:
        def __init__(self):
            self._events = [_MessageStart(), _Event(), _MessageDelta()]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def __aiter__(self):
            self._iter = iter(self._events)
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    class _Messages:
        def stream(self, **kwargs):
            captured.update(kwargs)
            return _StreamContext()

    class _FakeAnthropic:
        def __init__(self, **_kwargs):
            self.messages = _Messages()

        def close(self):
            pass

    class _FakeAsyncAnthropic(_FakeAnthropic):
        async def close(self):
            pass

    monkeypatch.setattr(llm_client_module, "Anthropic", _FakeAnthropic)
    monkeypatch.setattr(llm_client_module, "AsyncAnthropic", _FakeAsyncAnthropic)
    monkeypatch.setattr(llm_client_module, "_record_anthropic_token_usage", lambda *_args: None)

    client = llm_client_module.ChatAnthropic(
        model="kimi-for-coding",
        base_url="https://api.kimi.com/coding",
        api_key="sk-test",
    )
    try:
        chunks = [chunk async for chunk in client.astream([{"role": "user", "content": "hi"}])]
        assert [chunk.content for chunk in chunks] == ["ok", "", ""]
        assert chunks[1].finish_reason == "stop"
        assert chunks[2].usage_metadata == {"input_tokens": 2, "output_tokens": 3}
        assert "stream" not in captured
        assert captured["model"] == "kimi-for-coding"
    finally:
        await client.aclose()


def test_anthropic_stop_reasons_map_to_openai_finish_reasons():
    assert llm_client_module._anthropic_stop_reason_to_finish_reason("end_turn") == "stop"
    assert llm_client_module._anthropic_stop_reason_to_finish_reason("stop_sequence") == "stop"
    assert llm_client_module._anthropic_stop_reason_to_finish_reason("max_tokens") == "length"
    assert llm_client_module._anthropic_stop_reason_to_finish_reason("tool_use") == "tool_calls"
    assert llm_client_module._anthropic_stop_reason_to_finish_reason("refusal") == "content_filter"
    assert llm_client_module._anthropic_stop_reason_to_finish_reason("pause_turn") == "pause_turn"


@pytest.mark.asyncio
async def test_chat_anthropic_stream_converts_tool_use_to_openai_deltas(monkeypatch):
    captured = {}

    class _ToolBlock:
        type = "tool_use"
        id = "toolu_1"
        name = "lookup"
        input = {}

    class _ToolStart:
        type = "content_block_start"
        index = 0
        content_block = _ToolBlock()

    class _InputDelta:
        type = "input_json_delta"
        partial_json = '{"q":"neko"}'

    class _ToolArgs:
        type = "content_block_delta"
        index = 0
        delta = _InputDelta()

    class _StopDelta:
        stop_reason = "tool_use"

    class _MessageDelta:
        type = "message_delta"
        delta = _StopDelta()
        usage = None

    class _StreamContext:
        def __init__(self):
            self._events = [_ToolStart(), _ToolArgs(), _MessageDelta()]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def __aiter__(self):
            self._iter = iter(self._events)
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    class _Messages:
        def stream(self, **kwargs):
            captured.update(kwargs)
            return _StreamContext()

    class _FakeAnthropic:
        def __init__(self, **_kwargs):
            self.messages = _Messages()

        def close(self):
            pass

    class _FakeAsyncAnthropic(_FakeAnthropic):
        async def close(self):
            pass

    monkeypatch.setattr(llm_client_module, "Anthropic", _FakeAnthropic)
    monkeypatch.setattr(llm_client_module, "AsyncAnthropic", _FakeAsyncAnthropic)
    monkeypatch.setattr(llm_client_module, "_record_anthropic_token_usage", lambda *_args: None)

    client = llm_client_module.ChatAnthropic(
        model="kimi-for-coding",
        base_url="https://api.kimi.com/coding",
        api_key="sk-test",
    )
    try:
        chunks = [
            chunk async for chunk in client.astream(
                [{"role": "user", "content": "hi"}],
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                    },
                }],
                tool_choice={"type": "function", "function": {"name": "lookup"}},
            )
        ]
        assert captured["tools"] == [{
            "name": "lookup",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        }]
        assert captured["tool_choice"] == {"type": "tool", "name": "lookup"}
        assert chunks[0].tool_call_deltas == [{
            "index": 0,
            "id": "toolu_1",
            "type": "function",
            "function": {"name": "lookup", "arguments": ""},
        }]
        assert chunks[1].tool_call_deltas == [{
            "index": 0,
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": '{"q":"neko"}'},
        }]
        assert chunks[2].finish_reason == "tool_calls"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_chat_openai_reuses_default_ssl_context(monkeypatch):
    original_create_default_context = llm_client_module.ssl.create_default_context
    calls = []

    def counting_create_default_context(*args, **kwargs):
        calls.append((args, kwargs))
        return original_create_default_context(*args, **kwargs)

    monkeypatch.setattr(llm_client_module, "_DEFAULT_SSL_CONTEXT", None)
    monkeypatch.setattr(
        llm_client_module.ssl,
        "create_default_context",
        counting_create_default_context,
    )

    clients = [
        llm_client_module.ChatOpenAI(
            model="model-a",
            base_url="https://example.com/v1",
            api_key="sk-test",
        ),
        llm_client_module.ChatOpenAI(
            model="model-b",
            base_url="https://example.com/v1",
            api_key="sk-test",
        ),
    ]
    try:
        assert len(calls) == 1
    finally:
        for client in clients:
            await client.aclose()


@pytest.mark.asyncio
async def test_chat_openai_finalizer_closes_injected_http_clients(monkeypatch):
    client = llm_client_module.ChatOpenAI(
        model="model-a",
        base_url="https://example.com/v1",
        api_key="sk-test",
    )
    close = MagicMock()
    aclose = AsyncMock()
    monkeypatch.setattr(client._client, "close", close)
    monkeypatch.setattr(client._aclient, "close", aclose)
    del client
    gc.collect()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    close.assert_called_once_with()
    aclose.assert_awaited_once_with()
    assert not llm_client_module._PENDING_CLIENT_CLOSE_TASKS


@pytest.mark.asyncio
async def test_chat_openai_sync_close_detaches_finalizer_after_closing_clients(monkeypatch):
    client = llm_client_module.ChatOpenAI(
        model="model-a",
        base_url="https://example.com/v1",
        api_key="sk-test",
    )
    close = MagicMock()
    aclose = AsyncMock()
    monkeypatch.setattr(client._client, "close", close)
    monkeypatch.setattr(client._aclient, "close", aclose)

    finalizer = client._client_finalizer
    client.close()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert not finalizer.alive
    close.assert_called_once_with()
    aclose.assert_awaited_once_with()
    del client
    gc.collect()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    close.assert_called_once_with()
    aclose.assert_awaited_once_with()
    assert not llm_client_module._PENDING_CLIENT_CLOSE_TASKS
