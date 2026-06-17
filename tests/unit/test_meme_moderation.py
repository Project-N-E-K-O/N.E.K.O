import asyncio
import json
import os
import sys

import httpx
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils import meme_moderation as mm


ENV_KEYS = [
    "NEKO_MEME_MODERATION_ENABLED",
    "MEME_MODERATION_ENABLED",
    "NEKO_UNIAPI_API_KEY",
    "UNIAPI_API_KEY",
    "NEKO_MEME_MODERATION_API_KEY",
    "MEME_MODERATION_API_KEY",
    "NEKO_UNIAPI_BASE_URL",
    "UNIAPI_BASE_URL",
    "NEKO_MEME_MODERATION_MODEL",
    "MEME_MODERATION_MODEL",
    "NEKO_MEME_MODERATION_PROVIDER",
    "MEME_MODERATION_PROVIDER",
    "NEKO_MEME_MODERATION_IMAGE_INPUT_MODE",
    "MEME_MODERATION_IMAGE_INPUT_MODE",
    "NEKO_MEME_MODERATION_TIMEOUT_SECONDS",
    "MEME_MODERATION_TIMEOUT_SECONDS",
    "NEKO_MEME_MODERATION_CACHE_TTL_SECONDS",
    "MEME_MODERATION_CACHE_TTL_SECONDS",
    "NEKO_MEME_MODERATION_FAIL_CLOSED",
    "MEME_MODERATION_FAIL_CLOSED",
    "NEKO_MEME_MODERATION_RATE_LIMIT_BACKOFF_SECONDS",
    "MEME_MODERATION_RATE_LIMIT_BACKOFF_SECONDS",
    "NEKO_MEME_MODERATION_PAYMENT_BACKOFF_SECONDS",
    "MEME_MODERATION_PAYMENT_BACKOFF_SECONDS",
    "NEKO_MEME_MODERATION_ALLOW_SSL_FALLBACK",
    "MEME_MODERATION_ALLOW_SSL_FALLBACK",
    "NEKO_MEME_MODERATION_PORN_THRESHOLD",
    "MEME_MODERATION_PORN_THRESHOLD",
    "NEKO_MEME_MODERATION_HENTAI_THRESHOLD",
    "MEME_MODERATION_HENTAI_THRESHOLD",
    "NEKO_MEME_MODERATION_SEXY_THRESHOLD",
    "MEME_MODERATION_SEXY_THRESHOLD",
]


@pytest.fixture(autouse=True)
def clean_moderation_state(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    config_path = tmp_path / "meme_moderation_config.json"
    monkeypatch.setattr(mm, "_get_meme_moderation_config_path", lambda: config_path)
    mm.clear_meme_moderation_cache()
    yield
    mm.clear_meme_moderation_cache()


class FakeResponse:
    def __init__(
        self,
        status_code=200,
        json_data=None,
        headers=None,
        content=b"image-bytes",
    ):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.headers = headers or {}
        self.content = content

    def raise_for_status(self):
        if self.status_code < 400:
            return
        request = httpx.Request("POST", "https://example.test/v1/moderations")
        response = httpx.Response(
            self.status_code,
            headers=self.headers,
            request=request,
        )
        raise httpx.HTTPStatusError(
            f"status {self.status_code}",
            request=request,
            response=response,
        )

    def json(self):
        return self._json_data


class FakeClient:
    def __init__(self, *, post_response=None, get_response=None, post_error=None, get_error=None):
        self.post_response = post_response or FakeResponse(json_data=moderation_json(False))
        self.get_response = get_response or FakeResponse(headers={"Content-Type": "image/jpeg"})
        self.post_error = post_error
        self.get_error = get_error
        self.post_calls = []
        self.get_calls = []

    async def post(self, url, *, headers=None, json=None, timeout=None):
        self.post_calls.append(
            {
                "url": url,
                "headers": headers or {},
                "json": json,
                "timeout": timeout,
            }
        )
        if self.post_error:
            raise self.post_error
        return self.post_response

    async def get(self, url, *, headers=None, timeout=None):
        self.get_calls.append(
            {
                "url": url,
                "headers": headers or {},
                "timeout": timeout,
            }
        )
        if self.get_error:
            raise self.get_error
        return self.get_response


def moderation_json(flagged, *, model="omni-moderation-latest", scores=None, categories=None):
    category_scores = scores if scores is not None else {"porn": 1.0 if flagged else 0.0}
    category_flags = categories if categories is not None else {
        "neutral": not flagged,
        "drawings": False,
        "sexy": False,
        "hentai": False,
        "porn": flagged,
    }
    return {
        "id": "mod-test",
        "model": model,
        "results": [
            {
                "flagged": flagged,
                "categories": category_flags,
                "category_scores": category_scores,
            }
        ],
    }


def run(coro):
    return asyncio.run(coro)


def use_direct_url_payload(monkeypatch):
    monkeypatch.setenv("NEKO_MEME_MODERATION_IMAGE_INPUT_MODE", "url")


def write_config(data):
    mm._get_meme_moderation_config_path().write_text(
        json.dumps(data),
        encoding="utf-8",
    )


def test_disabled_allows_without_request():
    client = FakeClient()

    result = run(mm.moderate_meme_image_url("https://example.com/cat.jpg", http_client=client))

    assert result.allowed is True
    assert result.reason == "disabled"
    assert client.post_calls == []


def test_missing_key_allows_without_request_even_when_enabled():
    client = FakeClient()

    result = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
            enabled=True,
        )
    )

    assert result.allowed is True
    assert result.reason == "disabled"
    assert client.post_calls == []


def test_env_key_auto_enables_default_vapi(monkeypatch):
    monkeypatch.setenv("NEKO_MEME_MODERATION_API_KEY", "test-key")
    image_client = FakeClient(
        get_response=FakeResponse(
            headers={"Content-Type": "image/jpeg"},
            content=b"abc",
        )
    )
    moderation_client = FakeClient(
        post_response=FakeResponse(
            json_data=moderation_json(False, model="nsfw-classifier")
        )
    )
    monkeypatch.setattr(mm, "get_external_http_client", lambda: image_client)

    result = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=moderation_client,
        )
    )

    assert result.allowed is True
    assert result.reason == "pass"
    assert result.model == "nsfw-classifier"
    assert moderation_client.post_calls[0]["url"] == "https://api.gpt.ge/v1/moderations"
    assert moderation_client.post_calls[0]["headers"]["Authorization"] == "Bearer test-key"
    assert moderation_client.post_calls[0]["json"]["model"] == "gi-image-moderation"
    payload_url = moderation_client.post_calls[0]["json"]["input"][0]["image_url"]["url"]
    assert payload_url == "data:image/jpeg;base64,YWJj"


def test_config_file_key_auto_enables_and_overrides_env(monkeypatch):
    use_direct_url_payload(monkeypatch)
    monkeypatch.setenv("NEKO_MEME_MODERATION_API_KEY", "env-key")
    write_config({"api_key": "file-key"})
    client = FakeClient(post_response=FakeResponse(json_data=moderation_json(False)))

    result = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
        )
    )

    assert result.allowed is True
    assert result.reason == "pass"
    assert client.post_calls[0]["headers"]["Authorization"] == "Bearer file-key"


def test_wrapped_config_file_key_is_supported(monkeypatch):
    use_direct_url_payload(monkeypatch)
    write_config({"meme_moderation_config": {"api_key": "wrapped-key"}})
    client = FakeClient(post_response=FakeResponse(json_data=moderation_json(False)))

    result = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
        )
    )

    assert result.allowed is True
    assert client.post_calls[0]["headers"]["Authorization"] == "Bearer wrapped-key"


def test_unflagged_image_passes_and_uses_openai_payload(monkeypatch):
    monkeypatch.setenv("NEKO_UNIAPI_BASE_URL", "https://api.uniapi.io/v1")
    monkeypatch.setenv("NEKO_MEME_MODERATION_MODEL", "omni-moderation-latest")
    client = FakeClient(post_response=FakeResponse(json_data=moderation_json(False)))

    result = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert result.allowed is True
    assert result.reason == "pass"
    assert result.cached is False
    assert result.categories == {
        "neutral": True,
        "drawings": False,
        "sexy": False,
        "hentai": False,
        "porn": False,
    }
    assert client.post_calls[0]["url"] == "https://api.uniapi.io/v1/moderations"
    assert client.post_calls[0]["headers"]["Authorization"] == "Bearer test-key"
    assert client.post_calls[0]["json"] == {
        "model": "omni-moderation-latest",
        "input": [
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/cat.jpg"},
            }
        ],
    }


def test_flagged_image_blocks(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = FakeClient(post_response=FakeResponse(json_data=moderation_json(True)))

    result = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert result.allowed is False
    assert result.reason == "flagged"
    assert result.categories["porn"] is True


def test_flagged_low_scores_are_allowed_to_reduce_false_positives(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = FakeClient(
        post_response=FakeResponse(
            json_data=moderation_json(
                True,
                scores={"porn": 0.10, "hentai": 0.12, "sexy": 0.25},
            )
        )
    )

    result = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert result.allowed is True
    assert result.reason == "pass"


def test_high_scores_block_even_when_provider_does_not_flag(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = FakeClient(
        post_response=FakeResponse(
            json_data=moderation_json(
                False,
                scores={"porn": 0.72, "hentai": 0.01, "sexy": 0.20},
            )
        )
    )

    result = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert result.allowed is False
    assert result.reason == "score_threshold"


def test_request_failure_is_fail_closed_by_default(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = FakeClient(post_error=httpx.ConnectError("network down"))

    result = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert result.allowed is False
    assert result.reason == "request_failed"


def test_rate_limit_sets_backoff(monkeypatch):
    use_direct_url_payload(monkeypatch)
    monkeypatch.setenv("NEKO_MEME_MODERATION_RATE_LIMIT_BACKOFF_SECONDS", "30")
    client = FakeClient(post_response=FakeResponse(status_code=429))

    first = run(
        mm.moderate_meme_image_url(
            "https://example.com/one.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )
    second = run(
        mm.moderate_meme_image_url(
            "https://example.com/two.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert first.allowed is False
    assert first.reason == "rate_limited"
    assert second.allowed is False
    assert second.reason == "rate_limited"
    assert len(client.post_calls) == 1


def test_payment_required_sets_backoff(monkeypatch):
    use_direct_url_payload(monkeypatch)
    monkeypatch.setenv("NEKO_MEME_MODERATION_PAYMENT_BACKOFF_SECONDS", "30")
    client = FakeClient(post_response=FakeResponse(status_code=402))

    first = run(
        mm.moderate_meme_image_url(
            "https://example.com/one.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )
    second = run(
        mm.moderate_meme_image_url(
            "https://example.com/two.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert first.allowed is False
    assert first.reason == "payment_required"
    assert second.allowed is False
    assert second.reason == "payment_required"
    assert len(client.post_calls) == 1


def test_fail_open_option_allows_request_failures(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = FakeClient(post_error=httpx.ConnectError("network down"))

    result = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
            fail_closed=False,
        )
    )

    assert result.allowed is True
    assert result.reason == "request_failed"


def test_successful_results_are_cached(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = FakeClient(post_response=FakeResponse(json_data=moderation_json(False)))

    first = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )
    second = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert first.cached is False
    assert second.cached is True
    assert len(client.post_calls) == 1


def test_api_gpt_ge_defaults_to_data_url(monkeypatch):
    monkeypatch.setenv("NEKO_UNIAPI_BASE_URL", "https://api.gpt.ge/v1")
    monkeypatch.setenv("NEKO_MEME_MODERATION_MODEL", "gi-image-moderation")
    image_client = FakeClient(
        get_response=FakeResponse(
            headers={"Content-Type": "image/jpeg"},
            content=b"abc",
        )
    )
    moderation_client = FakeClient(
        post_response=FakeResponse(
            json_data=moderation_json(False, model="nsfw-classifier")
        )
    )
    monkeypatch.setattr(mm, "get_external_http_client", lambda: image_client)

    result = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=moderation_client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert result.allowed is True
    assert result.model == "nsfw-classifier"
    assert image_client.get_calls[0]["headers"]["Referer"] == "https://fabiaoqing.com/"
    payload_url = moderation_client.post_calls[0]["json"]["input"][0]["image_url"]["url"]
    assert payload_url == "data:image/jpeg;base64,YWJj"
    assert moderation_client.post_calls[0]["url"] == "https://api.gpt.ge/v1/moderations"
    assert moderation_client.post_calls[0]["json"]["model"] == "gi-image-moderation"


def test_image_fetch_failure_blocks_and_skips_post(monkeypatch):
    monkeypatch.setenv("NEKO_UNIAPI_BASE_URL", "https://api.gpt.ge/v1")
    image_client = FakeClient(get_error=httpx.ConnectError("image fetch failed"))
    moderation_client = FakeClient()
    monkeypatch.setattr(mm, "get_external_http_client", lambda: image_client)

    result = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=moderation_client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert result.allowed is False
    assert result.reason == "image_fetch_failed"
    assert moderation_client.post_calls == []


def test_ssl_fallback_is_disabled_by_default(monkeypatch):
    monkeypatch.setenv("NEKO_UNIAPI_BASE_URL", "https://api.gpt.ge/v1")
    ssl_error = "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
    image_client = FakeClient(get_error=httpx.ConnectError(ssl_error))
    moderation_client = FakeClient()
    monkeypatch.setattr(mm, "get_external_http_client", lambda: image_client)

    class UnexpectedRelaxedClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("SSL fallback should be disabled by default")

    monkeypatch.setattr(mm.httpx, "AsyncClient", UnexpectedRelaxedClient)

    result = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=moderation_client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert result.allowed is False
    assert result.reason == "image_fetch_failed"
    assert moderation_client.post_calls == []


def test_ssl_fallback_can_be_enabled(monkeypatch):
    monkeypatch.setenv("NEKO_UNIAPI_BASE_URL", "https://api.gpt.ge/v1")
    monkeypatch.setenv("NEKO_MEME_MODERATION_ALLOW_SSL_FALLBACK", "1")
    ssl_error = "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
    image_client = FakeClient(get_error=httpx.ConnectError(ssl_error))
    moderation_client = FakeClient(
        post_response=FakeResponse(
            json_data=moderation_json(False, model="nsfw-classifier")
        )
    )
    relaxed_client_kwargs = []
    monkeypatch.setattr(mm, "get_external_http_client", lambda: image_client)

    class RelaxedClient:
        def __init__(self, *args, **kwargs):
            relaxed_client_kwargs.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, *, headers=None):
            return FakeResponse(headers={"Content-Type": "image/jpeg"}, content=b"abc")

    monkeypatch.setattr(mm.httpx, "AsyncClient", RelaxedClient)

    result = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=moderation_client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert result.allowed is True
    assert relaxed_client_kwargs[0]["verify"] is False
    payload_url = moderation_client.post_calls[0]["json"]["input"][0]["image_url"]["url"]
    assert payload_url == "data:image/jpeg;base64,YWJj"


def test_non_http_url_is_rejected():
    client = FakeClient()

    result = run(
        mm.moderate_meme_image_url(
            "file:///tmp/cat.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert result.allowed is False
    assert result.reason == "invalid_url"
    assert client.post_calls == []
