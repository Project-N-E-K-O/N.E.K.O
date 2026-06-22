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


@pytest.mark.asyncio
async def test_chat_anthropic_stream_helper_does_not_forward_stream_kwarg(monkeypatch):
    captured = {}

    class _TextDelta:
        type = "text_delta"
        text = "ok"

    class _Event:
        type = "content_block_delta"
        delta = _TextDelta()

    class _StreamContext:
        def __init__(self):
            self._events = [_Event()]

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

    client = llm_client_module.ChatAnthropic(
        model="kimi-for-coding",
        base_url="https://api.kimi.com/coding",
        api_key="sk-test",
    )
    try:
        chunks = [chunk async for chunk in client.astream([{"role": "user", "content": "hi"}])]
        assert [chunk.content for chunk in chunks] == ["ok"]
        assert "stream" not in captured
        assert captured["model"] == "kimi-for-coding"
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
