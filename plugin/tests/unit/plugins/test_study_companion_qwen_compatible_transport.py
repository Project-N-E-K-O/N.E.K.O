from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from plugin.plugins.study_companion.qwen_compatible_transport import (
    QwenCompatibleTransport,
    QwenCompatibleTransportError,
    compatible_chat_completions_url,
)


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "base",
    [
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    ],
)
def test_url_allows_only_official_compatible_mode_bases(base: str) -> None:
    assert compatible_chat_completions_url(base).endswith(
        "/compatible-mode/v1/chat/completions"
    )


@pytest.mark.parametrize(
    "base",
    [
        "http://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope.aliyuncs.com.evil.test/compatible-mode/v1",
        "https://evil.test/compatible-mode/v1",
        "https://dashscope.aliyuncs.com:443/compatible-mode/v1",
        "https://user@dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope.aliyuncs.com/api/v1",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "https://dashscope.aliyuncs.com/compatible-mode/v1?next=https://evil.test",
        "https://dashscope.aliyuncs.com/compatible-mode/v1#fragment",
    ],
)
def test_url_rejects_ssrf_and_non_contract_variants(base: str) -> None:
    with pytest.raises(QwenCompatibleTransportError) as raised:
        compatible_chat_completions_url(base)
    assert raised.value.diagnostic == "invalid_endpoint"


@pytest.mark.asyncio
async def test_request_contract_disables_thinking_omits_temperature_and_parses_tokens() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"x-request-id": "req-123"},
            json={
                "model": "qwen-plus",
                "choices": [{"message": {"content": "  Answer  "}}],
                "usage": {"prompt_tokens": 17, "completion_tokens": "9"},
            },
        )

    result = await QwenCompatibleTransport(
        transport=httpx.MockTransport(handler)
    ).chat_completions(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="secret",
        model="qwen-plus",
        messages=[{"role": "user", "content": "Question"}],
        max_tokens=1200,
        timeout_seconds=10,
    )

    assert seen["url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert seen["authorization"] == "Bearer secret"
    assert seen["body"] == {
        "model": "qwen-plus",
        "messages": [{"role": "user", "content": "Question"}],
        "max_tokens": 1200,
        "enable_thinking": False,
        "stream": False,
    }
    assert "temperature" not in seen["body"]
    assert result.text == "Answer"
    assert result.request_id == "req-123"
    assert (result.input_tokens, result.output_tokens) == (17, 9)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "diagnostic"),
    [
        (401, {"error": {"message": "bad key"}}, "authentication_failed"),
        (429, {"error": {"message": "rate limit"}}, "rate_limited"),
        (
            400,
            {"error": {"code": "ModelNotFound", "message": "unknown model"}},
            "model_not_supported",
        ),
        (400, {"error": {"message": "bad request"}}, "invalid_request"),
        (
            400,
            {"error": {"message": "maximum context length exceeded"}},
            "context_limit_exceeded",
        ),
        (503, {"error": {"message": "down"}}, "provider_unavailable"),
        (422, {"error": {"message": "bad input"}}, "llm_call_failed"),
    ],
)
async def test_status_errors_are_diagnostic_and_do_not_expose_body(
    status: int, body: dict[str, object], diagnostic: str
) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            status, headers={"x-request-id": "req-error"}, json=body
        )
    )
    with pytest.raises(QwenCompatibleTransportError) as raised:
        await QwenCompatibleTransport(transport=transport).chat_completions(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="secret",
            model="qwen-plus",
            messages=[],
            max_tokens=1,
            timeout_seconds=1,
        )
    assert raised.value.diagnostic == diagnostic
    assert raised.value.status_code == status
    assert raised.value.request_id == "req-error"
    assert "bad key" not in str(raised.value)


@pytest.mark.asyncio
async def test_invalid_or_empty_success_payload_is_rejected() -> None:
    for response in (
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"choices": []}),
    ):
        transport = httpx.MockTransport(lambda _request, item=response: item)
        with pytest.raises(QwenCompatibleTransportError) as raised:
            await QwenCompatibleTransport(transport=transport).chat_completions(
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                api_key="secret",
                model="qwen-plus",
                messages=[],
                max_tokens=1,
                timeout_seconds=1,
            )
        assert raised.value.diagnostic == "provider_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "diagnostic"),
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (404, "invalid_endpoint"),
        (503, "provider_unavailable"),
    ],
)
async def test_non_json_error_keeps_status_diagnostic(
    status: int, diagnostic: str
) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(status, content=b"<html>error</html>")
    )
    with pytest.raises(QwenCompatibleTransportError) as raised:
        await QwenCompatibleTransport(transport=transport).chat_completions(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="secret",
            model="qwen-plus",
            messages=[],
            max_tokens=1,
            timeout_seconds=1,
        )
    assert raised.value.diagnostic == diagnostic


@pytest.mark.asyncio
async def test_timeout_is_mapped_and_cancellation_propagates() -> None:
    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("late", request=request)

    with pytest.raises(QwenCompatibleTransportError) as raised:
        await QwenCompatibleTransport(
            transport=httpx.MockTransport(timeout_handler)
        ).chat_completions(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="secret",
            model="qwen-plus",
            messages=[],
            max_tokens=1,
            timeout_seconds=1,
        )
    assert raised.value.diagnostic == "timeout"

    entered = asyncio.Event()

    async def blocked(_request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    task = asyncio.create_task(
        QwenCompatibleTransport(transport=httpx.MockTransport(blocked)).chat_completions(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="secret",
            model="qwen-plus",
            messages=[],
            max_tokens=1,
            timeout_seconds=10,
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_request_has_a_wall_clock_deadline() -> None:
    canceled = asyncio.Event()

    async def slow_response(_request: httpx.Request) -> httpx.Response:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            canceled.set()
            raise
        raise AssertionError("unreachable")

    with pytest.raises(QwenCompatibleTransportError) as raised:
        await QwenCompatibleTransport(
            transport=httpx.MockTransport(slow_response)
        ).chat_completions(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="secret",
            model="qwen-plus",
            messages=[],
            max_tokens=1,
            timeout_seconds=0.02,
        )

    assert raised.value.diagnostic == "timeout"
    assert canceled.is_set()


@pytest.mark.asyncio
async def test_credentials_and_model_are_validated_before_network() -> None:
    called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    client = QwenCompatibleTransport(transport=httpx.MockTransport(handler))
    for key, model, diagnostic in (
        ("", "qwen-plus", "authentication_failed"),
        ("secret", "other-model", "model_not_supported"),
    ):
        with pytest.raises(QwenCompatibleTransportError) as raised:
            await client.chat_completions(
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                api_key=key,
                model=model,
                messages=[],
                max_tokens=1,
                timeout_seconds=1,
            )
        assert raised.value.diagnostic == diagnostic
    assert called is False
