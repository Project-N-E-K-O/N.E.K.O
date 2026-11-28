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
    monkeypatch.setattr(asr_client, "_read_core_config", lambda: dict(config))
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
        # Listed by DashScope, but compatible mode answers 400 "format is
        # empty": they need the native API, so the slot must not offer them.
        ("qwen", "qwen-audio-3.0-asr-flash", False),
        ("qwen", "fun-asr-flash-2026-06-15", False),
        ("qwen", "qwen3-asr-flash-filetrans", False),
        ("qwen_intl", "qwen-audio-3.0-asr-flash", False),
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
        # A custom server's list is never filtered, so nothing is a "candidate".
        ("custom", "gigaam-v3", False),
        ("unknown", "whisper-1", False),
    ],
)
def test_model_candidates_exclude_streaming_and_non_asr_models(vendor, model_id, expected) -> None:
    assert is_asr_model_candidate(vendor, model_id) is expected


def test_every_vendor_default_model_passes_its_own_filter() -> None:
    from main_logic.asr_client.slot import ASR_SLOT_VENDORS

    for spec in ASR_SLOT_VENDORS.values():
        if spec.filters_models:
            assert is_asr_model_candidate(spec.vendor, spec.default_model), spec.vendor
        else:
            assert spec.vendor == "custom"


# --------------------------------------------------------------------------
# Slot resolution


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"ENABLE_CUSTOM_API": False, "asrModelProvider": "custom", "asrModelUrl": "http://h"},
        _slot_config(),
        _slot_config(asrModelProvider="follow_core"),
    ],
)
def test_slot_is_unset_only_when_off_or_following_the_core(config) -> None:
    assert resolve_asr_slot(config) is None


@pytest.mark.parametrize(
    ("config", "provider_key"),
    [
        (_slot_config(asrModelProvider="deepseek"), "custom"),
        (_slot_config(asrModelProvider="custom", asrModelUrl=""), "custom"),
        (_slot_config(asrModelProvider="custom", asrModelUrl="192.168.1.5:8081"), "custom"),
        (_slot_config(asrModelProvider="glm", asrModelTransport="stream"), "glm_request"),
    ],
)
def test_a_chosen_slot_that_cannot_run_is_misconfigured_not_unset(config, provider_key) -> None:
    # "Configured but unusable" must never read as "no slot": that would send
    # the audio to the Core's provider behind the user's back.
    endpoint = resolve_asr_slot(config)
    assert endpoint is not None
    assert endpoint.availability is AsrProviderAvailability.MISCONFIGURED
    assert endpoint.provider_key == provider_key
    assert endpoint.provider_key in ASR_PROVIDER_REGISTRY


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
        _slot_config(asrModelProvider="qwen_intl", ASR_MODEL_API_KEY="intl-key")
    )
    assert endpoint is not None
    assert endpoint.provider_key == "qwen_request"
    assert endpoint.url.startswith("https://dashscope-intl.aliyuncs.com/")
    assert endpoint.model == "qwen3-asr-flash"
    assert endpoint.api_key == "intl-key"


def test_vendor_slot_ignores_the_derived_assist_keys() -> None:
    # ASSIST_API_KEY_* may hold another vendor's coreApiKey; only the
    # snapshot's ASR_MODEL_API_KEY counts.
    endpoint = resolve_asr_slot(
        _slot_config(asrModelProvider="glm", ASSIST_API_KEY_GLM="borrowed-core-key")
    )
    assert endpoint is not None
    assert endpoint.api_key == ""
    assert endpoint.availability is AsrProviderAvailability.MISSING_CREDENTIALS


def _write_core_config(cm, data: dict) -> None:
    import json

    with open(str(cm.get_config_path("core_config.json")), "w", encoding="utf-8") as f:
        json.dump(data, f)
    cm._core_config_cache = None


@pytest.fixture
def config_manager(clean_user_data_dir):
    from utils.config_manager import get_config_manager

    cm = get_config_manager("N.E.K.O")
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    return cm


@pytest.mark.parametrize(
    ("saved", "expected"),
    [
        # Assist provider is qwen but its Key Book entry is empty: the chat
        # layer borrows the OpenAI coreApiKey, the ASR slot must not.
        ({"coreApi": "openai", "assistApi": "qwen", "coreApiKey": "sk-openai"}, ""),
        ({"coreApi": "openai", "coreApiKey": "sk-openai", "assistApiKeyQwen": "sk-qwen"}, "sk-qwen"),
        # The Core itself is the selected vendor: its key is that vendor's key.
        ({"coreApi": "qwen", "coreApiKey": "sk-core-qwen"}, "sk-core-qwen"),
    ],
)
def test_snapshot_vendor_slot_key_is_scoped_to_the_vendor(config_manager, saved, expected) -> None:
    _write_core_config(
        config_manager,
        {"enableCustomApi": True, "asrModelProvider": "qwen", **saved},
    )
    snapshot = config_manager.get_core_config()
    assert snapshot["ASR_MODEL_API_KEY"] == expected
    endpoint = resolve_asr_slot(snapshot)
    assert endpoint is not None
    assert endpoint.api_key == expected


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
        _slot_config(asrModelProvider="glm", ASR_MODEL_API_KEY="glm-key", ASSIST_API_KEY_QWEN="q")
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


async def _noop_async(*_args: Any) -> None:
    return None


def test_misconfigured_slot_fails_closed_on_the_free_core(core_config) -> None:
    core_config.update(_slot_config(asrModelProvider="custom", asrModelUrl="192.168.1.5:8081"))
    selection = asr_client._resolve_asr_selection("free")
    assert selection.provider_key == "custom"
    assert selection.availability is AsrProviderAvailability.MISCONFIGURED
    assert selection._worker_fn is None
    with pytest.raises(RuntimeError, match="ASR_INVALID_CONFIG"):
        asr_client._create_asr_session_from_selection(
            "free",
            selection=selection,
            on_input_transcript=_noop_async,
            on_connection_error=_noop_async,
        )


def test_get_asr_slot_override_propagates_read_failures(monkeypatch) -> None:
    # "Could not read" must stay distinguishable from "no slot".
    def broken() -> dict:
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(asr_client, "_read_core_config", broken)
    with pytest.raises(RuntimeError, match="config unreadable"):
        asr_client.get_asr_slot_override()


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


def test_offline_misconfigured_slot_reports_invalid_config(core_config) -> None:
    core_config.update(_slot_config(asrModelProvider="custom", asrModelUrl="lan-box:8081"))
    with pytest.raises(offline.AsrOfflineError) as caught:
        offline.resolve_offline_transcriber("free")
    assert caught.value.code == "ASR_INVALID_CONFIG"


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
        (500, "ASR_QWEN_REQUEST_ERROR", "server_error"),
        (503, "ASR_QWEN_REQUEST_ERROR", "rate_limited"),
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


def test_qwen_model_list_offers_only_what_the_slot_can_call() -> None:
    # Ids observed in a live DashScope /models listing (2026-11-28): the
    # slot's default qwen3-asr-flash is missing and two other ASR models need
    # the native API.
    listed = {"success": True, "models": [
        {"id": "qwen-plus"},
        {"id": "qwen-audio-3.0-asr-flash"},
        {"id": "fun-asr-flash-2026-06-15"},
        {"id": "qwen3-asr-flash-realtime"},
    ]}
    result = model_catalog._tag_asr_models(listed, "qwen")
    assert result["asr_filtered"] is True
    tagged = [entry["id"] for entry in result["models"] if entry.get("asr")]
    assert tagged == ["qwen3-asr-flash"]
    assert result["models"][0] == {"id": "qwen3-asr-flash", "asr": True}
    # Nothing is hidden: the rest stays reachable behind "show all".
    assert len(result["models"]) == 5


def test_every_vendor_listing_includes_its_default_model() -> None:
    from main_logic.asr_client.slot import ASR_SLOT_VENDORS

    for spec in ASR_SLOT_VENDORS.values():
        result = model_catalog._tag_asr_models({"success": True, "models": []}, spec.vendor)
        ids = [entry["id"] for entry in result["models"]]
        assert ids == ([spec.default_model] if spec.filters_models else []), spec.vendor


def test_custom_model_list_is_returned_untagged() -> None:
    listed = {"success": True, "models": [{"id": "gigaam-v3"}, {"id": "sensevoice-small"}]}
    result = model_catalog._tag_asr_models(listed, "custom")
    assert result["asr_filtered"] is False
    assert result["models"] == listed["models"]


def test_failed_model_list_is_passed_through() -> None:
    failure = {"success": False, "error_code": "timeout"}
    assert model_catalog._tag_asr_models(failure, "qwen") == failure


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


def test_model_list_keeps_the_saved_asr_key_across_url_spellings() -> None:
    saved = {
        "asrModelUrl": "https://asr.example/v1/audio/transcriptions",
        "asrModelApiKey": "saved-key",
    }
    target = model_catalog._resolve_custom_target(
        model_catalog.ModelListRequest(
            url="https://asr.example/v1", api_key="__NEKO_SECRET_MASKED__", model_type="asr"
        ),
        saved,
    )
    assert target["api_key"] == "saved-key"
    assert target["urls"] == ["https://asr.example/v1"]

    moved = model_catalog._resolve_custom_target(
        model_catalog.ModelListRequest(
            url="https://other.example/v1", api_key="__NEKO_SECRET_MASKED__", model_type="asr"
        ),
        saved,
    )
    assert moved["error_code"] == "key_required"


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


def test_every_failure_kind_has_an_offline_code_and_a_test_message() -> None:
    from typing import get_args

    from main_logic.asr_client.workers._shared import AsrRequestFailureKind

    kinds = set(get_args(AsrRequestFailureKind))
    assert kinds == set(offline._FAILURE_KIND_CODES)
    assert kinds == set(asr_connectivity._FAILURE_KIND_ERRORS)


@pytest.mark.parametrize(
    ("status", "kind"),
    [(400, "rejected"), (404, "rejected"), (429, "rate_limited"), (500, "server_error"),
     (502, "server_error"), (503, "rate_limited"), (None, "failed")],
)
def test_http_failure_kind_is_shared(status, kind) -> None:
    from main_logic.asr_client.workers._shared import http_failure_kind

    assert http_failure_kind(status) == kind


@pytest.mark.parametrize(
    ("outcome", "error_code"),
    [
        (_FakeResponse({}, status_code=500), "server_error"),
        (httpx.ConnectError("refused"), "connection_refused"),
        (RuntimeError("bug"), "unknown"),
    ],
)
def test_asr_test_endpoint_tells_server_errors_from_unreachable(monkeypatch, outcome, error_code) -> None:
    client = _FakeHttpClient(outcome)
    monkeypatch.setattr(slot_request, "create_slot_client", lambda endpoint, key: client)
    result = asyncio.run(
        asr_connectivity.test_asr_connectivity(
            asr_connectivity.AsrConnectivityTestRequest(
                provider_key="custom", url="http://lan-box:8081", api_key="k"
            )
        )
    )
    assert result["error_code"] == error_code


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
