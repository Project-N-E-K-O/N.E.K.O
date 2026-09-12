"""Image infrastructure protocol, configuration, cancellation and isolation tests."""
import asyncio
import base64
import json

import httpx
import pytest

from utils.image_generation import ImageGenerationError, ImageRequest, generate_image
from utils.image_generation.config import resolve_image_config
from utils.config_manager.core_config import CoreConfigMixin


class Manager(CoreConfigMixin):
    def __init__(self, raw, enabled=True):
        self.raw = raw
        self.enabled = enabled

    def get_core_config(self):
        return {"ENABLE_CUSTOM_API": self.enabled, "IMAGE_GENERATION_CONFIG": self.raw}


def manager(provider="openai", **extra):
    return Manager({"imageModelProvider": provider, "assistApiKeyOpenai": "openai-key",
                    "assistApiKeyQwen": "beijing-key", "assistApiKeyQwenIntl": "singapore-key",
                    "imageModelApiKey": "custom-key", **extra})


@pytest.mark.parametrize("provider,key", [("openai", "openai-key"), ("qwen", "beijing-key"), ("qwen_intl", "singapore-key")])
def test_named_provider_uses_own_key_book(provider, key):
    result = manager(provider).get_model_api_config("image")
    assert result["api_key"] == key
    assert result["enabled"]
    assert "key" not in repr(resolve_image_config(manager(provider).raw))


def test_disabled_has_no_chat_fallback():
    assert Manager({}, False).get_model_api_config("image") == {"enabled": False}
    assert Manager({}).get_model_api_config("image") == {"enabled": False}


@pytest.mark.parametrize("url", ["http://example.com/v1", "https://user:pass@example.com", "https://example.com:bad", "https://example.com?token=x"])
def test_custom_endpoint_validation(url):
    with pytest.raises(ValueError):
        resolve_image_config(manager("custom", imageModelUrl=url, imageModelId="model").raw)


def test_named_key_cannot_be_sent_to_custom_endpoint():
    with pytest.raises(ValueError):
        resolve_image_config(manager(imageModelUrl="https://other.example/v1").raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("model,legacy", [("gpt-image-2", False), ("vendor/gpt-image-2:version", False), ("dall-e-3", True), ("prod-image", False)])
async def test_openai_wire_contract(model, legacy):
    calls = []
    def handle(request):
        calls.append(request)
        assert request.url.path == "/v1/images/generations"
        assert request.headers["authorization"] == "Bearer openai-key"
        body = json.loads(request.content)
        assert body["n"] == 1
        assert ("response_format" in body) == legacy
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"image").decode()}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await generate_image(ImageRequest("private prompt"), config_manager=manager(imageModelId=model), client=client)
        assert not client.is_closed
    assert result.image.data == b"image"
    assert len(calls) == 1
    assert "private prompt" not in repr(ImageRequest("private prompt"))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,host,key", [
    ("qwen", "dashscope.aliyuncs.com", "beijing-key"),
    ("qwen_intl", "dashscope-intl.aliyuncs.com", "singapore-key"),
])
async def test_dashscope_create_poll_and_result(provider, host, key):
    calls = []
    def handle(request):
        calls.append(request)
        assert request.url.host == host
        assert request.headers["authorization"] == "Bearer " + key
        if request.method == "POST":
            assert request.headers["x-dashscope-async"] == "enable"
            assert json.loads(request.content)["parameters"] == {"n": 1, "size": "1024*1024"}
            return httpx.Response(200, json={"output": {"task_id": "task-1"}})
        assert request.url.path == "/api/v1/tasks/task-1"
        return httpx.Response(200, json={"output": {"task_status": "SUCCEEDED", "results": [{"url": "https://images.example/result.png"}]}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await generate_image(ImageRequest("cat"), config_manager=manager(provider), client=client)
    assert result.image.url == "https://images.example/result.png"
    assert len(calls) == 2  # Result URL is never fetched.


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload,code", [
    (401, {"error": "secret private prompt"}, "provider_http_error"),
    (302, {}, "provider_http_error"),
    (200, {"data": [{"url": "http://127.0.0.1/private"}]}, "invalid_image_url"),
    (200, {"data": [{"b64_json": "%%%"}]}, "invalid_image_data"),
    (200, {"data": []}, "invalid_response"),
])
async def test_errors_are_sanitized_and_not_retried(status, payload, code):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, json=payload, headers={"Location": "https://other.example/"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        with pytest.raises(ImageGenerationError, match=code) as caught:
            await generate_image(ImageRequest("private prompt"), config_manager=manager(), client=client)
    assert str(caught.value) == code
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_timeout_and_cancellation_do_not_resubmit():
    entered = asyncio.Event()
    release = asyncio.Event()
    async def handle(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ImageGenerationError, match="provider_timeout"):
            await generate_image(ImageRequest("cat"), config_manager=manager(), client=client, timeout=0.01)
        entered.clear()
        task = asyncio.create_task(generate_image(ImageRequest("cat"), config_manager=manager(), client=client))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_disabled_never_calls_provider():
    def handle(request):
        pytest.fail("disabled slot must not make a request")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ImageGenerationError, match="not_configured"):
            await generate_image(ImageRequest("cat"), config_manager=Manager({}), client=client)


@pytest.mark.asyncio
async def test_response_limit(monkeypatch):
    from utils.image_generation import transport
    monkeypatch.setattr(transport, "MAX_RESPONSE_BYTES", 32)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x"*33))) as client:
        with pytest.raises(ImageGenerationError, match="response_too_large"):
            await generate_image(ImageRequest("cat"), config_manager=manager(), client=client)

@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("private proxy"), FileNotFoundError("private cert")])
async def test_owned_client_creation_error_is_sanitized(monkeypatch, error):
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(httpx, "AsyncClient", fail)
    with pytest.raises(ImageGenerationError, match="client_initialization_failed") as caught:
        await generate_image(ImageRequest("cat"), config_manager=manager())
    assert "private" not in str(caught.value)


def test_multiple_trailing_slashes_rejected_at_runtime():
    with pytest.raises(ValueError):
        resolve_image_config(manager("custom", imageModelUrl="https://custom.example/v1//", imageModelId="image").raw)


@pytest.mark.asyncio
async def test_deeply_nested_provider_json_is_normalized():
    import sys
    payload = b'[' * (sys.getrecursionlimit() + 100) + b'0' + b']' * (sys.getrecursionlimit() + 100)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))) as client:
        with pytest.raises(ImageGenerationError, match="^invalid_response$"):
            await generate_image(ImageRequest("test"), config_manager=manager(), client=client)
