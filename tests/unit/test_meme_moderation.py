import asyncio
import json
import os
import sys

import httpx
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils import meme_moderation as mm
from utils import api_config_loader as acl


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
    "NEKO_MEME_MODERATION_IMAGE_PAYLOAD_CACHE_MAX_BYTES",
    "MEME_MODERATION_IMAGE_PAYLOAD_CACHE_MAX_BYTES",
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
    monkeypatch.setattr(acl, "_get_meme_moderation_config_path", lambda: config_path)
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
        url="https://img.soutula.com/example.jpg",
    ):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.headers = headers or {}
        self.content = content
        self.url = url

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

    async def aiter_bytes(self):
        yield self.content


class FakeStream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


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

    def stream(self, method, url, *, headers=None, timeout=None, **kwargs):
        self.get_calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "timeout": timeout,
                "kwargs": kwargs,
            }
        )
        if self.get_error:
            raise self.get_error
        return FakeStream(self.get_response)


class SequencePostClient(FakeClient):
    def __init__(self, post_responses):
        super().__init__(post_response=post_responses[0])
        self.post_responses = list(post_responses)

    async def post(self, url, *, headers=None, json=None, timeout=None):
        if not self.post_responses:
            raise AssertionError("Unexpected extra moderation POST call in SequencePostClient")
        self.post_calls.append(
            {
                "url": url,
                "headers": headers or {},
                "json": json,
                "timeout": timeout,
            }
        )
        return self.post_responses.pop(0)


class SequenceStreamClient(FakeClient):
    def __init__(self, get_responses):
        super().__init__(get_response=get_responses[0])
        self.get_responses = list(get_responses)

    def stream(self, method, url, *, headers=None, timeout=None, **kwargs):
        if not self.get_responses:
            raise AssertionError("Unexpected extra image stream call in SequenceStreamClient")
        self.get_calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "timeout": timeout,
                "kwargs": kwargs,
            }
        )
        return FakeStream(self.get_responses.pop(0))


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
    monkeypatch.setattr(mm, "MEME_ALLOWED_HOSTS", [*mm.MEME_ALLOWED_HOSTS, "example.com"])


def test_sequence_clients_report_extra_requests():
    post_client = SequencePostClient([FakeResponse(json_data=moderation_json(False))])
    stream_client = SequenceStreamClient(
        [FakeResponse(headers={"Content-Type": "image/jpeg"}, content=b"abc")]
    )

    run(post_client.post("https://example.test/moderations"))
    with pytest.raises(AssertionError, match="Unexpected extra moderation POST call"):
        run(post_client.post("https://example.test/moderations"))

    stream_client.stream("GET", "https://img.soutula.com/one.jpg")
    with pytest.raises(AssertionError, match="Unexpected extra image stream call"):
        stream_client.stream("GET", "https://img.soutula.com/two.jpg")


def write_config(data):
    acl._get_meme_moderation_config_path().write_text(
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


def test_env_key_auto_enables_default_openai(monkeypatch):
    monkeypatch.setenv("NEKO_MEME_MODERATION_API_KEY", "test-key")
    moderation_client = FakeClient(
        post_response=FakeResponse(
            json_data=moderation_json(False, model="nsfw-classifier")
        )
    )

    result = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=moderation_client,
        )
    )

    assert result.allowed is True
    assert result.reason == "pass"
    assert result.model == "nsfw-classifier"
    assert moderation_client.post_calls[0]["url"] == "https://api.openai.com/v1/moderations"
    assert moderation_client.post_calls[0]["headers"]["Authorization"] == "Bearer test-key"
    assert moderation_client.post_calls[0]["json"]["model"] == "omni-moderation-latest"
    payload_url = moderation_client.post_calls[0]["json"]["input"][0]["image_url"]["url"]
    assert payload_url == "https://img.soutula.com/example.jpg"


def test_config_file_key_auto_enables_and_overrides_env(monkeypatch):
    use_direct_url_payload(monkeypatch)
    monkeypatch.setenv("NEKO_MEME_MODERATION_API_KEY", "env-key")
    write_config(
        {
            "api_key": "file-key",
            "base_url": "https://moderation-config.test/v1",
            "model": "config-moderation-model",
        }
    )
    client = FakeClient(post_response=FakeResponse(json_data=moderation_json(False)))

    result = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
        )
    )

    assert result.allowed is True
    assert result.reason == "pass"
    assert client.post_calls[0]["url"] == "https://moderation-config.test/v1/moderations"
    assert client.post_calls[0]["headers"]["Authorization"] == "Bearer file-key"
    assert client.post_calls[0]["json"]["model"] == "config-moderation-model"


def test_env_endpoint_and_model_override_api_providers_fallback(monkeypatch):
    use_direct_url_payload(monkeypatch)
    monkeypatch.setenv("NEKO_MEME_MODERATION_API_KEY", "env-key")
    monkeypatch.setenv("NEKO_UNIAPI_BASE_URL", "https://env-provider.test/v9")
    monkeypatch.setenv("NEKO_MEME_MODERATION_MODEL", "env-model")
    monkeypatch.setattr(
        acl,
        "get_config",
        lambda: {
            "meme_moderation_config": {
                "api_key": "",
                "base_url": "https://fallback-config.test/v1",
                "model": "fallback-model",
            }
        },
    )
    client = FakeClient(post_response=FakeResponse(json_data=moderation_json(False)))

    result = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
        )
    )

    assert result.allowed is True
    assert client.post_calls[0]["url"] == "https://env-provider.test/v9/moderations"
    assert client.post_calls[0]["json"]["model"] == "env-model"


def test_moderation_specific_env_key_precedes_generic_uniapi_key(monkeypatch):
    use_direct_url_payload(monkeypatch)
    monkeypatch.setenv("NEKO_UNIAPI_API_KEY", "generic-key")
    monkeypatch.setenv("NEKO_MEME_MODERATION_API_KEY", "moderation-key")
    client = FakeClient(post_response=FakeResponse(json_data=moderation_json(False)))

    result = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
        )
    )

    assert result.allowed is True
    assert client.post_calls[0]["headers"]["Authorization"] == "Bearer moderation-key"


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


def test_api_providers_config_key_is_fallback(monkeypatch):
    use_direct_url_payload(monkeypatch)
    monkeypatch.setattr(
        acl,
        "get_config",
        lambda: {
            "meme_moderation_config": {
                "api_key": "fallback-key",
                "base_url": "https://fallback-config.test/v1",
                "model": "fallback-moderation-model",
            }
        },
    )
    client = FakeClient(post_response=FakeResponse(json_data=moderation_json(False)))

    result = run(
        mm.moderate_meme_image_url(
            "https://example.com/cat.jpg",
            http_client=client,
        )
    )

    assert result.allowed is True
    assert client.post_calls[0]["url"] == "https://fallback-config.test/v1/moderations"
    assert client.post_calls[0]["headers"]["Authorization"] == "Bearer fallback-key"
    assert client.post_calls[0]["json"]["model"] == "fallback-moderation-model"


def test_unflagged_image_passes_and_uses_openai_payload(monkeypatch):
    use_direct_url_payload(monkeypatch)
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
    assert client.post_calls[0]["url"] == "https://api.openai.com/v1/moderations"
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


def test_flagged_outside_threshold_categories_still_block(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = FakeClient(
        post_response=FakeResponse(
            json_data=moderation_json(
                True,
                scores={"porn": 0.10, "hentai": 0.12, "sexy": 0.25, "violence": 0.01},
                categories={"porn": False, "hentai": False, "sexy": False, "violence": True},
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
    assert result.reason == "flagged"


def test_flagged_sexual_minors_blocks_even_below_threshold(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = FakeClient(
        post_response=FakeResponse(
            json_data=moderation_json(
                True,
                scores={"sexual/minors": 0.65},
                categories={"sexual/minors": True},
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
    assert result.reason == "flagged"


def test_flagged_openai_scores_block_when_no_local_threshold_keys(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = FakeClient(
        post_response=FakeResponse(
            json_data=moderation_json(
                True,
                scores={
                    "sexual": 0.99,
                    "sexual/minors": 0.0,
                    "violence": 0.0,
                },
                categories={
                    "sexual": True,
                    "sexual/minors": False,
                    "violence": False,
                },
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
    assert result.reason == "flagged"


def test_openai_scores_block_even_when_provider_does_not_flag(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = FakeClient(
        post_response=FakeResponse(
            json_data=moderation_json(
                False,
                scores={
                    "sexual": 0.99,
                    "sexual/minors": 0.0,
                    "violence": 0.0,
                },
                categories={
                    "sexual": False,
                    "sexual/minors": False,
                    "violence": False,
                },
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


def test_openai_sexual_minors_score_blocks_even_when_provider_does_not_flag(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = FakeClient(
        post_response=FakeResponse(
            json_data=moderation_json(
                False,
                scores={
                    "sexual": 0.0,
                    "sexual/minors": 0.99,
                    "violence": 0.0,
                },
                categories={
                    "sexual": False,
                    "sexual/minors": False,
                    "violence": False,
                },
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


def test_rate_limit_backoff_is_scoped_to_active_config(monkeypatch):
    use_direct_url_payload(monkeypatch)
    monkeypatch.setenv("NEKO_MEME_MODERATION_RATE_LIMIT_BACKOFF_SECONDS", "30")
    first_client = FakeClient(post_response=FakeResponse(status_code=429))

    first = run(
        mm.moderate_meme_image_url(
            "https://example.com/one.jpg",
            http_client=first_client,
            enabled=True,
            api_key="stale-key",
        )
    )

    monkeypatch.setenv("NEKO_UNIAPI_BASE_URL", "https://healthy-provider.test/v1")
    second_client = FakeClient(post_response=FakeResponse(json_data=moderation_json(False)))
    second = run(
        mm.moderate_meme_image_url(
            "https://example.com/two.jpg",
            http_client=second_client,
            enabled=True,
            api_key="fresh-key",
        )
    )

    assert first.allowed is False
    assert first.reason == "rate_limited"
    assert second.allowed is True
    assert second.reason == "pass"
    assert len(first_client.post_calls) == 1
    assert len(second_client.post_calls) == 1


def test_backoff_expiry_resets_when_fingerprint_changes(monkeypatch):
    use_direct_url_payload(monkeypatch)
    monkeypatch.setattr(mm.time, "monotonic", lambda: 1000.0)
    monkeypatch.setenv("NEKO_MEME_MODERATION_PAYMENT_BACKOFF_SECONDS", "600")
    first_client = FakeClient(post_response=FakeResponse(status_code=402))

    first = run(
        mm.moderate_meme_image_url(
            "https://example.com/one.jpg",
            http_client=first_client,
            enabled=True,
            api_key="stale-key",
        )
    )
    first_until = mm._provider_backoff_until

    monkeypatch.setenv("NEKO_UNIAPI_BASE_URL", "https://healthy-provider.test/v1")
    monkeypatch.setenv("NEKO_MEME_MODERATION_RATE_LIMIT_BACKOFF_SECONDS", "3")
    second_client = FakeClient(post_response=FakeResponse(status_code=429))
    second = run(
        mm.moderate_meme_image_url(
            "https://example.com/two.jpg",
            http_client=second_client,
            enabled=True,
            api_key="fresh-key",
        )
    )

    assert first.allowed is False
    assert first.reason == "payment_required"
    assert second.allowed is False
    assert second.reason == "rate_limited"
    assert first_until == 1600.0
    assert mm._provider_backoff_until == 1003.0


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


def test_fail_open_option_allows_request_timeout(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = FakeClient(post_error=httpx.ReadTimeout("moderation timeout"))

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
    write_config({"base_url": "https://api.gpt.ge/v1"})
    image_client = FakeClient(
        get_response=FakeResponse(headers={"Content-Type": "image/jpeg"}, content=b"abc")
    )
    client = FakeClient(post_response=FakeResponse(json_data=moderation_json(False)))
    monkeypatch.setattr(mm, "get_external_http_client", lambda: image_client)

    first = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/cat.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )
    second = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/cat.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert first.cached is False
    assert second.cached is True
    assert len(client.post_calls) == 1
    assert len(image_client.get_calls) == 2


def test_direct_url_payload_does_not_cache_allowed_verdict(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = SequencePostClient(
        [
            FakeResponse(json_data=moderation_json(False)),
            FakeResponse(json_data=moderation_json(True)),
        ]
    )

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

    assert first.allowed is True
    assert first.cached is False
    assert second.allowed is False
    assert second.cached is False
    assert len(client.post_calls) == 2


def test_direct_url_payload_rejects_non_meme_hosts_before_post(monkeypatch):
    use_direct_url_payload(monkeypatch)
    client = FakeClient()

    result = run(
        mm.moderate_meme_image_url(
            "https://127.0.0.1/private.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert result.allowed is False
    assert result.reason == "image_fetch_failed"
    assert client.post_calls == []


def test_successful_cache_is_scoped_to_moderation_policy(monkeypatch):
    write_config({"base_url": "https://api.gpt.ge/v1"})
    monkeypatch.setenv("NEKO_MEME_MODERATION_MODEL", "policy-one")
    image_client = FakeClient(
        get_response=FakeResponse(headers={"Content-Type": "image/jpeg"}, content=b"abc")
    )
    client = FakeClient(post_response=FakeResponse(json_data=moderation_json(False)))
    monkeypatch.setattr(mm, "get_external_http_client", lambda: image_client)

    first = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/cat.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )
    monkeypatch.setenv("NEKO_MEME_MODERATION_MODEL", "policy-two")
    second = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/cat.jpg",
            http_client=client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert first.cached is False
    assert second.cached is False
    assert len(client.post_calls) == 2
    assert len(image_client.get_calls) == 2
    assert client.post_calls[0]["json"]["model"] == "policy-one"
    assert client.post_calls[1]["json"]["model"] == "policy-two"


def test_api_gpt_ge_defaults_to_data_url(monkeypatch):
    write_config(
        {
            "base_url": "https://api.gpt.ge/v1",
            "model": "gi-image-moderation",
        }
    )
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


def test_data_url_payload_is_cached_after_moderation_timeout(monkeypatch):
    write_config({"base_url": "https://api.gpt.ge/v1"})
    image_client = SequenceStreamClient(
        [
            FakeResponse(
                headers={"Content-Type": "image/jpeg", "ETag": '"v1"'},
                content=b"abc",
            ),
            FakeResponse(
                status_code=304,
                headers={"ETag": '"v1"'},
            ),
        ]
    )
    monkeypatch.setattr(mm, "get_external_http_client", lambda: image_client)

    class FlakyModerationClient(FakeClient):
        async def post(self, url, *, headers=None, json=None, timeout=None):
            self.post_calls.append(
                {
                    "url": url,
                    "headers": headers or {},
                    "json": json,
                    "timeout": timeout,
                }
            )
            if len(self.post_calls) == 1:
                raise httpx.ReadTimeout("moderation timeout")
            return FakeResponse(json_data=moderation_json(False))

    moderation_client = FlakyModerationClient()

    first = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=moderation_client,
            enabled=True,
            api_key="test-key",
            fail_closed=False,
        )
    )
    second = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=moderation_client,
            enabled=True,
            api_key="test-key",
            fail_closed=False,
        )
    )

    assert first.allowed is True
    assert first.reason == "request_failed"
    assert second.allowed is True
    assert second.reason == "pass"
    assert len(image_client.get_calls) == 2
    assert image_client.get_calls[1]["headers"]["If-None-Match"] == '"v1"'
    assert len(moderation_client.post_calls) == 2


def test_data_url_cached_payload_revalidates_unchanged_image(monkeypatch):
    write_config({"base_url": "https://api.gpt.ge/v1"})
    image_client = SequenceStreamClient(
        [
            FakeResponse(
                headers={"Content-Type": "image/jpeg", "ETag": '"v1"'},
                content=b"abc",
            ),
            FakeResponse(
                status_code=304,
                headers={"ETag": '"v1"'},
            ),
        ]
    )
    moderation_client = SequencePostClient(
        [
            FakeResponse(json_data=moderation_json(False)),
        ]
    )
    monkeypatch.setattr(mm, "get_external_http_client", lambda: image_client)

    first = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=moderation_client,
            enabled=True,
            api_key="test-key",
        )
    )
    second = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=moderation_client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert first.cached is False
    assert second.cached is True
    assert len(image_client.get_calls) == 2
    assert image_client.get_calls[1]["headers"]["If-None-Match"] == '"v1"'
    assert len(moderation_client.post_calls) == 1


def test_data_url_mutable_image_refreshes_verdict_cache_key(monkeypatch):
    write_config({"base_url": "https://api.gpt.ge/v1"})
    image_client = SequenceStreamClient(
        [
            FakeResponse(
                headers={"Content-Type": "image/jpeg", "ETag": '"v1"'},
                content=b"benign",
            ),
            FakeResponse(
                headers={"Content-Type": "image/jpeg", "ETag": '"v2"'},
                content=b"blocked",
            ),
        ]
    )
    moderation_client = SequencePostClient(
        [
            FakeResponse(json_data=moderation_json(False)),
            FakeResponse(json_data=moderation_json(True)),
        ]
    )
    monkeypatch.setattr(mm, "get_external_http_client", lambda: image_client)

    first = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=moderation_client,
            enabled=True,
            api_key="test-key",
        )
    )
    second = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=moderation_client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert first.allowed is True
    assert second.allowed is False
    assert second.reason == "flagged"
    assert len(image_client.get_calls) == 2
    assert image_client.get_calls[1]["headers"]["If-None-Match"] == '"v1"'
    assert len(moderation_client.post_calls) == 2


def test_data_url_payload_cache_obeys_byte_budget(monkeypatch):
    write_config({"base_url": "https://api.gpt.ge/v1"})
    monkeypatch.setenv("NEKO_MEME_MODERATION_IMAGE_PAYLOAD_CACHE_MAX_BYTES", "8")
    image_client = FakeClient(
        get_response=FakeResponse(
            headers={"Content-Type": "image/jpeg"},
            content=b"abc",
        )
    )
    monkeypatch.setattr(mm, "get_external_http_client", lambda: image_client)
    first_moderation_client = FakeClient(post_error=httpx.ReadTimeout("moderation timeout"))
    second_moderation_client = FakeClient(
        post_response=FakeResponse(json_data=moderation_json(False))
    )

    first = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=first_moderation_client,
            enabled=True,
            api_key="test-key",
            fail_closed=False,
        )
    )
    second = run(
        mm.moderate_meme_image_url(
            "https://img.soutula.com/example.jpg",
            http_client=second_moderation_client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert first.allowed is True
    assert first.reason == "request_failed"
    assert second.allowed is True
    assert len(image_client.get_calls) == 2


def test_image_fetch_failure_blocks_and_skips_post(monkeypatch):
    write_config({"base_url": "https://api.gpt.ge/v1"})
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


def test_data_url_fetch_rejects_non_meme_hosts_before_request(monkeypatch):
    write_config({"base_url": "https://api.gpt.ge/v1"})
    image_client = FakeClient()
    moderation_client = FakeClient()
    monkeypatch.setattr(mm, "get_external_http_client", lambda: image_client)

    result = run(
        mm.moderate_meme_image_url(
            "https://127.0.0.1/metadata.jpg",
            http_client=moderation_client,
            enabled=True,
            api_key="test-key",
        )
    )

    assert result.allowed is False
    assert result.reason == "image_fetch_failed"
    assert image_client.get_calls == []
    assert moderation_client.post_calls == []


def test_data_url_fetch_rejects_redirect_target_outside_meme_hosts(monkeypatch):
    write_config({"base_url": "https://api.gpt.ge/v1"})
    image_client = FakeClient(
        get_response=FakeResponse(
            headers={"Content-Type": "image/jpeg"},
            url="http://127.0.0.1/private.jpg",
        )
    )
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
    assert len(image_client.get_calls) == 1
    assert moderation_client.post_calls == []


def test_data_url_fetch_rejects_blocked_redirect_hop_before_request(monkeypatch):
    write_config({"base_url": "https://api.gpt.ge/v1"})
    image_client = SequenceStreamClient(
        [
            FakeResponse(
                status_code=302,
                headers={"Location": "http://127.0.0.1/private.jpg"},
                url="https://img.soutula.com/example.jpg",
            ),
        ]
    )
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
    assert len(image_client.get_calls) == 1
    assert image_client.get_calls[0]["url"] == "https://img.soutula.com/example.jpg"
    assert image_client.get_calls[0]["kwargs"]["follow_redirects"] is False
    assert moderation_client.post_calls == []


def test_data_url_fetch_rejects_oversized_content_length(monkeypatch):
    write_config({"base_url": "https://api.gpt.ge/v1"})
    image_client = FakeClient(
        get_response=FakeResponse(
            headers={
                "Content-Type": "image/jpeg",
                "Content-Length": str(11 * 1024 * 1024),
            },
            content=b"x",
        )
    )
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
    write_config({"base_url": "https://api.gpt.ge/v1"})
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
    write_config({"base_url": "https://api.gpt.ge/v1"})
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

        def stream(self, method, url, *, headers=None, timeout=None, **kwargs):
            return FakeStream(
                FakeResponse(headers={"Content-Type": "image/jpeg"}, content=b"abc")
            )

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
