from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services.theater import llm, model_trace


def test_model_return_capture_is_private_ordered_and_context_scoped():
    """模型正文只写入当前回合采集器，退出后不能继续污染旧 Session 记录。"""  # noqa: DOCSTRING_CJK
    with model_trace.capture_model_returns() as records:
        model_trace.record_model_return(
            call_type="theater_router",
            surface="free_input",
            status="success",
            model="router-model",
            provider_type="openai",
            content='{"route_kind":"idle"}',
        )
        model_trace.record_model_return(
            call_type="theater_actor",
            surface="roleplay_response",
            status="error",
            model="actor-model",
            provider_type="openai",
            error_type="RuntimeError",
        )

    model_trace.record_model_return(
        call_type="theater_repair",
        surface="roleplay_response",
        status="success",
        model="repair-model",
        provider_type="openai",
        content="不应进入旧容器",
    )

    assert [item["call_index"] for item in records] == [0, 1]
    assert records[0]["content"] == '{"route_kind":"idle"}'
    assert records[1]["content"] == ""
    assert records[1]["error_type"] == "RuntimeError"
    serialized = json.dumps(records, ensure_ascii=False)
    assert "不应进入旧容器" not in serialized
    assert "api_key" not in serialized
    assert "base_url" not in serialized
    assert "prompt" not in serialized.lower()


class _FakeAsyncClient:
    """提供统一模型入口测试所需的最小异步客户端。"""  # noqa: DOCSTRING_CJK

    def __init__(self, *, response=None, error: Exception | None = None):
        self._response = response
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def ainvoke(self, _messages):
        if self._error is not None:
            raise self._error
        return self._response


@pytest.mark.asyncio
async def test_model_entry_records_successful_raw_return(monkeypatch):
    """统一入口应保留供应商原始正文，供之后复盘 Router、Actor 和 Repair。"""  # noqa: DOCSTRING_CJK
    response = SimpleNamespace(content='{"dialogue":"原始返回"}')

    async def fake_create_chat_llm_async(*_args, **_kwargs):
        return _FakeAsyncClient(response=response)

    monkeypatch.setattr(llm, "create_chat_llm_async", fake_create_chat_llm_async)

    with model_trace.capture_model_returns() as records:
        result = await llm._invoke_model_once(
            {
                "model": "trace-model",
                "base_url": "https://private.example.invalid",
                "api_key": "private-key",
                "provider_type": "openai",
            },
            "不能落盘的系统提示词",
            "不能落盘的用户提示词",
            call_type="theater_actor",
            surface="roleplay_response",
            timeout_seconds=1.0,
            max_completion_tokens=100,
        )

    assert result is response
    assert len(records) == 1
    assert records[0] == {
        "call_index": 0,
        "call_type": "theater_actor",
        "surface": "roleplay_response",
        "status": "success",
        "model": "trace-model",
        "provider_type": "openai",
        "content": '{"dialogue":"原始返回"}',
        "error_type": "",
        "recorded_at": records[0]["recorded_at"],
    }
    serialized = json.dumps(records, ensure_ascii=False)
    assert "private-key" not in serialized
    assert "private.example.invalid" not in serialized
    assert "不能落盘" not in serialized


@pytest.mark.asyncio
async def test_model_entry_records_error_type_without_exception_message(monkeypatch):
    """失败调用只记异常类型，避免供应商异常意外夹带请求内容或密钥。"""  # noqa: DOCSTRING_CJK

    async def fake_create_chat_llm_async(*_args, **_kwargs):
        return _FakeAsyncClient(error=RuntimeError("不应持久化的异常正文"))

    monkeypatch.setattr(llm, "create_chat_llm_async", fake_create_chat_llm_async)

    with model_trace.capture_model_returns() as records:
        with pytest.raises(RuntimeError, match="不应持久化的异常正文"):
            await llm._invoke_model_once(
                {
                    "model": "trace-model",
                    "base_url": "https://private.example.invalid",
                    "api_key": "private-key",
                    "provider_type": "openai",
                },
                "system prompt",
                "user prompt",
                call_type="theater_router",
                surface="free_input",
                timeout_seconds=1.0,
                max_completion_tokens=100,
            )

    assert len(records) == 1
    assert records[0]["status"] == "error"
    assert records[0]["content"] == ""
    assert records[0]["error_type"] == "RuntimeError"
    assert "不应持久化的异常正文" not in json.dumps(records, ensure_ascii=False)
