"""Speech-recognition slot: config resolution, routing, requests and tooling."""

from __future__ import annotations

import asyncio
import base64
import io
import wave
from functools import partial
from typing import Any

import httpx
import pytest

import main_logic.asr_client as asr_client
from main_logic.asr_client import offline
from main_logic.asr_client._infra import AsrSessionConfig, _AsrWorkerEvent, _AsrWorkerRequest
from main_logic.asr_client._registry_meta import ASR_PROVIDER_REGISTRY, AsrProviderAvailability
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.asr_client.slot import (
    ASR_SLOT_PROVIDER_KEYS,
    ASR_SLOT_ROUTES,
    AsrSlotEndpoint,
    custom_asr_models_base_url,
    is_asr_model_candidate,
    normalize_custom_asr_url,
    resolve_asr_slot,
)
from main_logic.asr_client.workers import slot_request
from main_logic.asr_client.workers._shared import AsrRequestFailure
from main_routers.config_router import asr_connectivity, model_catalog


# --------------------------------------------------------------------------
# Fakes


class _FakeResponse:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("POST", "https://provider.invalid/")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "rejected",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self) -> Any:
        return self._payload


class _FakeHttpClient:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def aclose(self) -> None:
        self.closed = True


def _slot_config(**fields: Any) -> dict[str, Any]:
    return {"ENABLE_CUSTOM_API": True, **fields}


def _custom_endpoint(**overrides: Any) -> AsrSlotEndpoint:
    values: dict[str, Any] = {
        "vendor": "custom",
        "transport": "request",
        "provider_key": "custom",
        "protocol": "openai_multipart",
        "url": "http://lan-box:8081/v1/audio/transcriptions",
        "model": "gigaam-v3",
        "api_key": "",
    }
    values.update(overrides)
    return AsrSlotEndpoint(**values)


def _qwen_endpoint() -> AsrSlotEndpoint:
    return AsrSlotEndpoint(
        vendor="qwen",
        transport="request",
        provider_key="qwen_request",
        protocol="qwen_chat",
        url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        model="qwen3-asr-flash",
        api_key="qwen-secret",
    )


@pytest.fixture
def core_config(monkeypatch):
    for name in ("ASR_PROVIDER", "ASR_USER_REGION", "SONIOX_API_KEY", "SONIOX_REGION"):
        monkeypatch.delenv(name, raising=False)
    config: dict[str, Any] = {}
    monkeypatch.setattr(asr_client, "_load_core_config", lambda: dict(config))
    return config


# --------------------------------------------------------------------------
# URL normalization and model filtering


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://lan-box:8081", "http://lan-box:8081/v1/audio/transcriptions"),
        ("http://lan-box:8081/", "http://lan-box:8081/v1/audio/transcriptions"),
        ("http://lan-box:8081/v1", "http://lan-box:8081/v1/audio/transcriptions"),
        ("http://lan-box:8081/v1/", "http://lan-box:8081/v1/audio/transcriptions"),
        ("https://asr.example/api/v2", "https://asr.example/api/v2/audio/transcriptions"),
        (
            " https://asr.example/v1/audio/transcriptions ",
            "https://asr.example/v1/audio/transcriptions",
        ),
        ("", ""),
        ("lan-box:8081", ""),
        ("ftp://lan-box/v1", ""),
        ("ws://lan-box:8081/v1", ""),
    ],
)
def test_custom_url_accepts_base_or_full_path(raw, expected) -> None:
    assert normalize_custom_asr_url(raw) == expected


def test_models_base_url_strips_the_transcription_path() -> None:
    assert custom_asr_models_base_url("http://lan-box:8081") == "http://lan-box:8081/v1"
    assert (
        custom_asr_models_base_url("https://asr.example/v1/audio/transcriptions")
        == "https://asr.example/v1"
    )
    assert custom_asr_models_base_url("not a url") == ""


@pytest.mark.parametrize(
    ("vendor", "model_id", "expected"),
    [
        ("qwen", "qwen3-asr-flash", True),
        ("qwen", "qwen3-asr-flash-2026-02-10", True),
        ("qwen", "qwen3-asr-flash-realtime", False),
        ("qwen", "qwen-plus", False),
        ("openai", "gpt-4o-transcribe", True),
        ("openai", "whisper-1", True),
        ("openai", "gpt-realtime", False),
        ("openai", "gpt-4o-mini-tts", False),
        ("glm", "glm-asr-2512", True),
        ("glm", "glm-4.6", False),
        ("grok", "grok-voice-transcribe-2.0", True),
        ("gemini", "gemini-3.1-flash-lite", True),
        ("gemini", "gemini-embedding-001", False),
        ("gemini", "gemini-2.5-flash-live", False),
        ("custom", "gigaam-v3-asr", True),
        ("unknown", "whisper-1", False),
    ],
)
def test_model_candidates_exclude_streaming_and_non_asr_models(vendor, model_id, expected) -> None:
    assert is_asr_model_candidate(vendor, model_id) is expected


# --------------------------------------------------------------------------
# Slot resolution


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"ENABLE_CUSTOM_API": False, "asrModelProvider": "custom", "asrModelUrl": "http://h"},
        _slot_config(),
        _slot_config(asrModelProvider="follow_core"),
        _slot_config(asrModelProvider="deepseek"),
        _slot_config(asrModelProvider="custom", asrModelUrl=""),
        _slot_config(asrModelProvider="custom", asrModelUrl="not-a-url"),
        _slot_config(asrModelProvider="glm", asrModelTransport="stream"),
    ],
)
def test_slot_is_unset_unless_enabled_and_complete(config) -> None:
    assert resolve_asr_slot(config) is None


def test_custom_slot_resolves_with_optional_key() -> None:
    endpoint = resolve_asr_slot(
        _slot_config(
            asrModelProvider="custom",
            asrModelUrl="http://lan-box:8081/v1",
            asrModelId="gigaam-v3",
        )
    )
    assert endpoint is not None
    assert endpoint.provider_key == "custom"
    assert endpoint.url == "http://lan-box:8081/v1/audio/transcriptions"
    assert endpoint.model == "gigaam-v3"
    assert endpoint.api_key == ""
    assert endpoint.availability is AsrProviderAvailability.IMPLEMENTED


def test_vendor_slot_reads_its_key_book_entry_and_default_model() -> None:
    endpoint = resolve_asr_slot(
        _slot_config(asrModelProvider="qwen_intl", ASSIST_API_KEY_QWEN_INTL="intl-key")
    )
    assert endpoint is not None
    assert endpoint.provider_key == "qwen_request"
    assert endpoint.url.startswith("https://dashscope-intl.aliyuncs.com/")
    assert endpoint.model == "qwen3-asr-flash"
    assert endpoint.api_key == "intl-key"


def test_vendor_slot_never_borrows_another_vendors_key() -> None:
    endpoint = resolve_asr_slot(
        _slot_config(asrModelProvider="glm", ASSIST_API_KEY_QWEN="qwen-key", ASR_MODEL_API_KEY="slot")
    )
    assert endpoint is not None
    assert endpoint.api_key == ""
    assert endpoint.availability is AsrProviderAvailability.MISSING_CREDENTIALS


def test_every_slot_route_is_a_non_streaming_registry_key() -> None:
    assert set(ASR_SLOT_ROUTES.values()) == ASR_SLOT_PROVIDER_KEYS
    for provider_key in ASR_SLOT_PROVIDER_KEYS:
        meta = ASR_PROVIDER_REGISTRY[provider_key]
        assert meta.category == "segmented_request", provider_key
        assert meta.supported_endpointing_modes == {"manual"}, provider_key
        assert meta.user_selectable is False, provider_key
        assert meta.offline.mode == "request", provider_key
        policy = resolve_provider_policy(provider_key, "manual")
        assert policy.transport == "segmented"
        assert policy.smart_turn_required is True
        # The watchdog must outlast the worker's own request timeout.
        assert policy.provider_final_timeout_ms > meta.offline.request_timeout_ms


# --------------------------------------------------------------------------
# Realtime selection


def test_slot_overrides_the_free_core(core_config) -> None:
    core_config.update(_slot_config(asrModelProvider="custom", asrModelUrl="http://lan-box:8081"))
    selection = asr_client._resolve_asr_selection("free")
    assert selection.provider_key == "custom"
    assert selection.endpointing_mode == "manual"
    assert selection.availability is AsrProviderAvailability.IMPLEMENTED
    assert isinstance(selection._worker_fn, partial)
    assert selection._worker_fn.keywords["endpoint"].url == (
        "http://lan-box:8081/v1/audio/transcriptions"
    )


def test_slot_wins_over_local_preference_and_core_route(core_config) -> None:
    core_config.update(
        _slot_config(asrModelProvider="glm", ASSIST_API_KEY_GLM="glm-key", ASSIST_API_KEY_QWEN="q")
    )
    selection = asr_client._resolve_asr_selection("qwen", provider_preference="faster_whisper")
    assert selection.provider_key == "glm_request"
    assert selection._api_key == "glm-key"


def test_vendor_slot_without_key_fails_closed_instead_of_following_the_core(core_config) -> None:
    core_config.update(_slot_config(asrModelProvider="openai", ASSIST_API_KEY_QWEN="q"))
    selection = asr_client._resolve_asr_selection("qwen")
    assert selection.provider_key == "openai_request"
    assert selection.availability is AsrProviderAvailability.MISSING_CREDENTIALS


def test_unset_slot_keeps_the_core_route(core_config) -> None:
    core_config.update(_slot_config(asrModelProvider="follow_core", ASSIST_API_KEY_QWEN="q"))
    assert asr_client._resolve_asr_selection("qwen").provider_key == "qwen"


def test_get_asr_slot_override_never_raises(monkeypatch) -> None:
    def broken() -> dict:
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(asr_client, "_load_core_config", broken)
    assert asr_client.get_asr_slot_override() is None


# --------------------------------------------------------------------------
# Offline (voice messages)


def test_offline_uses_the_slot_on_the_free_core(core_config, monkeypatch) -> None:
    core_config.update(_slot_config(asrModelProvider="custom", asrModelUrl="http://lan-box:8081"))
    transcriber = offline.resolve_offline_transcriber("free")
    assert transcriber.provider_key == "custom"
    assert transcriber.spec.mode == "request"

    client = _FakeHttpClient(_FakeResponse({"text": " privet "}))
    monkeypatch.setattr(offline, "_create_http_client", lambda _timeout: client)
    pcm16 = (b"\x00\x30" + b"\x00\xd0") * 8_000  # 1 s, well above the silence gate
    text = asyncio.run(transcriber.transcribe(pcm16, language="ru-RU"))

    assert text == "privet"
    call = client.calls[0]
    assert call["url"] == "http://lan-box:8081/v1/audio/transcriptions"
    # No key configured: no Authorization header at all.
    assert call["headers"] == {}
    assert call["data"] == {"language": "ru"}


def test_offline_vendor_slot_without_key_reports_missing_credentials(core_config) -> None:
    core_config.update(_slot_config(asrModelProvider="gemini"))
    with pytest.raises(offline.AsrOfflineError) as caught:
        offline.resolve_offline_transcriber("free")
    assert caught.value.code == "ASR_CREDENTIALS_MISSING"


# --------------------------------------------------------------------------
# Requests


async def test_custom_request_sends_model_language_and_bearer_key() -> None:
    client = _FakeHttpClient(_FakeResponse({"text": "hello"}))
    endpoint = _custom_endpoint(api_key="lan-key")
    text = await slot_request.request_slot_transcript(
        client, endpoint, "lan-key", b"RIFF", language="en-US"
    )
    assert text == "hello"
    call = client.calls[0]
    assert call["headers"] == {"Authorization": "Bearer lan-key"}
    assert call["data"] == {"model": "gigaam-v3", "language": "en"}
    assert call["files"]["file"][0] == "audio.wav"


async def test_custom_request_rejects_a_non_json_transcript() -> None:
    client = _FakeHttpClient(_FakeResponse(["not", "a", "transcript"]))
    with pytest.raises(AsrRequestFailure) as caught:
        await slot_request.request_slot_transcript(client, _custom_endpoint(), "", b"RIFF")
    assert caught.value.code == "ASR_CUSTOM_PROTOCOL_ERROR"
    assert caught.value.kind == "invalid_response"


async def test_qwen_request_posts_input_audio_and_reads_the_message() -> None:
    client = _FakeHttpClient(
        _FakeResponse({"choices": [{"message": {"content": " 你好 "}}]})
    )
    text = await slot_request.request_slot_transcript(
        client, _qwen_endpoint(), "qwen-secret", b"RIFFwav", language="zh-CN"
    )
    assert text == "你好"
    call = client.calls[0]
    assert call["url"].endswith("/compatible-mode/v1/chat/completions")
    assert call["headers"] == {"Authorization": "Bearer qwen-secret"}
    body = call["json"]
    assert body["model"] == "qwen3-asr-flash"
    assert body["stream"] is False
    assert body["asr_options"] == {"enable_itn": False, "language": "zh"}
    part = body["messages"][0]["content"][0]
    assert part["type"] == "input_audio"
    assert part["input_audio"]["data"] == (
        "data:audio/wav;base64," + base64.b64encode(b"RIFFwav").decode("ascii")
    )


async def test_qwen_request_omits_an_unsupported_language_hint() -> None:
    client = _FakeHttpClient(_FakeResponse({"choices": [{"message": {"content": "x"}}]}))
    await slot_request.request_slot_transcript(
        client, _qwen_endpoint(), "k", b"RIFF", language="tlh"
    )
    assert client.calls[0]["json"]["asr_options"] == {"enable_itn": False}


@pytest.mark.parametrize(
    ("status", "code", "kind"),
    [
        (401, "ASR_CREDENTIALS_REJECTED", "auth"),
        (429, "ASR_QWEN_REQUEST_ERROR", "rate_limited"),
        (400, "ASR_QWEN_REQUEST_ERROR", "rejected"),
        (500, "ASR_QWEN_REQUEST_ERROR", "failed"),
    ],
)
async def test_qwen_request_classifies_http_failures(status, code, kind) -> None:
    client = _FakeHttpClient(_FakeResponse({}, status_code=status))
    with pytest.raises(AsrRequestFailure) as caught:
        await slot_request.request_slot_transcript(client, _qwen_endpoint(), "k", b"RIFF")
    assert (caught.value.code, caught.value.kind) == (code, kind)


async def test_qwen_request_rejects_a_missing_message() -> None:
    client = _FakeHttpClient(_FakeResponse({"choices": []}))
    with pytest.raises(AsrRequestFailure) as caught:
        await slot_request.request_slot_transcript(client, _qwen_endpoint(), "k", b"RIFF")
    assert caught.value.code == "ASR_QWEN_REQUEST_PROTOCOL_ERROR"


# --------------------------------------------------------------------------
# Realtime worker


async def _next_event(queue: asyncio.Queue[_AsrWorkerEvent], kind: str) -> _AsrWorkerEvent:
    while True:
        event = await asyncio.wait_for(queue.get(), 1)
        if event.kind == kind:
            return event


async def test_worker_posts_one_wav_per_commit_and_emits_one_final() -> None:
    client = _FakeHttpClient(_FakeResponse({"text": "privet"}))
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        slot_request.slot_request_asr_worker(
            requests,
            responses,
            "",
            AsrSessionConfig(language="ru"),
            endpoint=_custom_endpoint(),
            client_factory=lambda endpoint, key: client,
        )
    )
    try:
        await _next_event(responses, "ready")
        pcm = b"\x01\x02" * 320
        key = {"generation": 0, "buffer_epoch": 0, "utterance_id": 1}
        await requests.put(_AsrWorkerRequest(kind="audio", audio=pcm, **key))
        await requests.put(_AsrWorkerRequest(kind="commit", **key))
        final = await _next_event(responses, "final")
        assert final.text == "privet"

        await requests.put(_AsrWorkerRequest(kind="commit", **key))
        await asyncio.wait_for(requests.join(), 1)
        await asyncio.sleep(0)
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["data"] == {"model": "gigaam-v3", "language": "ru"}
        with wave.open(io.BytesIO(call["files"]["file"][1]), "rb") as wav_file:
            assert wav_file.getframerate() == 16_000
            assert wav_file.readframes(wav_file.getnframes()) == pcm

        await requests.put(_AsrWorkerRequest(kind="shutdown", generation=0))
        await _next_event(responses, "closed")
        await asyncio.wait_for(task, 1)
        assert client.closed is True
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_worker_reports_a_request_failure_and_stops() -> None:
    client = _FakeHttpClient(_FakeResponse({}, status_code=503))
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        slot_request.slot_request_asr_worker(
            requests,
            responses,
            "",
            AsrSessionConfig(),
            endpoint=_custom_endpoint(),
            client_factory=lambda endpoint, key: client,
        )
    )
    try:
        await _next_event(responses, "ready")
        key = {"generation": 0, "buffer_epoch": 0, "utterance_id": 1}
        await requests.put(_AsrWorkerRequest(kind="audio", audio=b"\x01\x02" * 320, **key))
        await requests.put(_AsrWorkerRequest(kind="commit", **key))
        error = await _next_event(responses, "error")
        assert error.error_code == "ASR_CUSTOM_ERROR"
        await _next_event(responses, "closed")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_vendor_worker_without_key_fails_before_any_request() -> None:
    created: list[Any] = []
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    await slot_request.slot_request_asr_worker(
        requests,
        responses,
        "",
        AsrSessionConfig(),
        endpoint=_qwen_endpoint(),
        client_factory=lambda endpoint, key: created.append(key),
    )
    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_CREDENTIALS_MISSING"
    assert created == []


# --------------------------------------------------------------------------
# Settings tooling


def test_model_list_tags_asr_models_first() -> None:
    result = model_catalog._tag_asr_models(
        {
            "success": True,
            "models": [
                {"id": "qwen-plus"},
                {"id": "qwen3-asr-flash-realtime"},
                {"id": "qwen3-asr-flash"},
            ],
        },
        "qwen",
    )
    assert [entry["id"] for entry in result["models"]] == [
        "qwen3-asr-flash",
        "qwen-plus",
        "qwen3-asr-flash-realtime",
    ]
    assert result["models"][0]["asr"] is True
    assert "asr" not in result["models"][1]


def test_model_list_reads_the_custom_base_url(monkeypatch) -> None:
    target = model_catalog._resolve_custom_target(
        model_catalog.ModelListRequest(
            url="http://lan-box:8081/v1/audio/transcriptions",
            api_key="k",
            model_type="asr",
        ),
        {},
    )
    assert target["urls"] == ["http://lan-box:8081/v1"]


def test_asr_test_endpoint_keeps_a_saved_key_on_its_own_url() -> None:
    saved = {"asrModelUrl": "http://lan-box:8081/v1", "asrModelApiKey": "lan-key"}
    request = asr_connectivity.AsrConnectivityTestRequest(
        provider_key="custom", url="http://lan-box:8081", api_key="__NEKO_SECRET_MASKED__"
    )
    endpoint = asr_connectivity._resolve_test_endpoint(request, saved, {})
    assert isinstance(endpoint, AsrSlotEndpoint)
    assert endpoint.api_key == "lan-key"

    moved = asr_connectivity.AsrConnectivityTestRequest(
        provider_key="custom", url="http://elsewhere:9000", api_key="__NEKO_SECRET_MASKED__"
    )
    refused = asr_connectivity._resolve_test_endpoint(moved, saved, {})
    assert refused["error_code"] == "key_required"


def test_asr_test_endpoint_rejects_unknown_vendors() -> None:
    request = asr_connectivity.AsrConnectivityTestRequest(provider_key="deepseek")
    assert asr_connectivity._resolve_test_endpoint(request, {}, {})["error_code"] == "unsupported"


def test_asr_test_endpoint_sends_silence_through_the_slot_request(monkeypatch) -> None:
    client = _FakeHttpClient(_FakeResponse({"text": ""}))
    monkeypatch.setattr(slot_request, "create_slot_client", lambda endpoint, key: client)
    result = asyncio.run(
        asr_connectivity.test_asr_connectivity(
            asr_connectivity.AsrConnectivityTestRequest(
                provider_key="custom", url="http://lan-box:8081", api_key="k", model="m"
            )
        )
    )
    assert result == {"success": True}
    assert client.calls[0]["url"] == "http://lan-box:8081/v1/audio/transcriptions"
    assert client.closed is True


def test_asr_test_endpoint_maps_auth_failures(monkeypatch) -> None:
    client = _FakeHttpClient(_FakeResponse({}, status_code=401))
    monkeypatch.setattr(slot_request, "create_slot_client", lambda endpoint, key: client)
    result = asyncio.run(
        asr_connectivity.test_asr_connectivity(
            asr_connectivity.AsrConnectivityTestRequest(
                provider_key="custom", url="http://lan-box:8081", api_key="bad"
            )
        )
    )
    assert result["success"] is False
    assert result["error_code"] == "auth_failed"
