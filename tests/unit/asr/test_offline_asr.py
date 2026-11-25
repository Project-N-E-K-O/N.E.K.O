"""Offline (whole-recording) ASR: registry capability, providers, routing.

Every provider is exercised against fakes only (fake HTTP client, fake Gemini
SDK client, fake Whisper model, fake realtime worker); no network is used.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import threading
import wave
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import main_logic.asr_client as asr_client
from main_logic.asr_client import offline
from main_logic.asr_client._infra import _AsrWorkerEvent
from main_logic.asr_client._registry_meta import (
    ASR_PROVIDER_REGISTRY,
    AsrOfflineSpec,
)
from main_logic.asr_client.workers import faster_whisper


SAMPLE_RATE = 16_000
BYTES_PER_SECOND = SAMPLE_RATE * 2


def _pcm(seconds: float) -> bytes:
    return b"\x01\x00" * int(SAMPLE_RATE * seconds)


def _selection(provider_key: str, *, api_key: str = "key", worker_fn: Any = None):
    return asr_client._AsrSelection(
        provider_key=provider_key,
        endpointing_mode="manual",
        _worker_fn=worker_fn,
        _api_key=api_key,
    )


def _transcriber(
    provider_key: str,
    *,
    api_key: str = "key",
    worker_fn: Any = None,
    **spec_changes: Any,
) -> offline.OfflineAsrTranscriber:
    meta = ASR_PROVIDER_REGISTRY[provider_key]
    if spec_changes:
        meta = dataclasses.replace(
            meta, offline=dataclasses.replace(meta.offline, **spec_changes)
        )
    return offline.OfflineAsrTranscriber(
        core_type="test",
        provider_key=provider_key,
        meta=meta,
        _selection=_selection(provider_key, api_key=api_key, worker_fn=worker_fn),
    )


def _wav_frames(wav_bytes: bytes) -> tuple[int, int, int, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        return (
            wav_file.getnchannels(),
            wav_file.getsampwidth(),
            wav_file.getframerate(),
            wav_file.getnframes(),
        )


# --------------------------------------------------------------------------
# Fake HTTP


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
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeHttpClient:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.cancelled = 0

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        outcome = self.outcomes.pop(0) if self.outcomes else "hang"
        if outcome == "hang":
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def http_client(monkeypatch):
    holder: dict[str, _FakeHttpClient] = {}

    def install(*outcomes: Any) -> _FakeHttpClient:
        client = _FakeHttpClient(*outcomes)
        holder["client"] = client
        return client

    monkeypatch.setattr(offline, "_create_http_client", lambda _timeout: holder["client"])
    return install


# --------------------------------------------------------------------------
# Registry capability


def test_registry_declares_offline_capability_for_every_provider() -> None:
    supported = {key for key, meta in ASR_PROVIDER_REGISTRY.items() if meta.offline.supported}
    assert {"glm", "gemini", "faster_whisper", "qwen", "soniox", "openai", "grok"} <= supported
    assert not ASR_PROVIDER_REGISTRY["step"].offline.supported
    assert not ASR_PROVIDER_REGISTRY["free"].offline.supported
    for meta in ASR_PROVIDER_REGISTRY.values():
        if meta.offline.supported:
            assert meta.offline.max_segment_ms and meta.offline.max_segment_ms > 0
            if meta.offline.mode == "request":
                assert meta.provider_key in offline._REQUEST_BACKENDS
        else:
            assert meta.offline.unsupported_reason


def test_segmented_offline_limits_follow_realtime_segment_limit() -> None:
    # GLM/Gemini/faster-whisper already split realtime turns at 27 s; their
    # offline requests must respect the same per-request ceiling.
    for key in ("glm", "gemini", "faster_whisper"):
        meta = ASR_PROVIDER_REGISTRY[key]
        assert meta.offline.max_segment_ms <= meta.max_segment_ms


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "unsupported"},
        {"mode": "request"},
        {"mode": "request", "max_segment_ms": 0},
        {"mode": "session", "max_segment_ms": 1000, "request_timeout_ms": 0},
    ],
)
def test_offline_spec_rejects_incomplete_declarations(kwargs) -> None:
    with pytest.raises(ValueError):
        AsrOfflineSpec(**kwargs)


# --------------------------------------------------------------------------
# Splitting and joining


def test_split_keeps_short_recordings_whole() -> None:
    audio = _pcm(10)
    assert offline.split_pcm16_segments(audio, 27_000) == [audio]


def test_split_long_recording_into_equal_segments_without_losing_samples() -> None:
    audio = _pcm(30) + b"\x02\x00"  # one extra sample: uneven total
    segments = offline.split_pcm16_segments(audio, 27_000)
    assert len(segments) == 2
    assert b"".join(segments) == audio
    assert all(len(segment) % 2 == 0 for segment in segments)
    assert all(len(segment) <= 27 * BYTES_PER_SECOND for segment in segments)
    assert abs(len(segments[0]) - len(segments[1])) <= 2


def test_join_inserts_spaces_only_between_spaced_scripts() -> None:
    assert offline.join_segment_transcripts(["今天天气", "不错"]) == "今天天气不错"
    assert offline.join_segment_transcripts(["hello", "world"]) == "hello world"
    assert offline.join_segment_transcripts(["  ", "hi  there", ""]) == "hi there"
    assert offline.join_segment_transcripts(["你好", "world"]) == "你好world"


# --------------------------------------------------------------------------
# Input validation


@pytest.mark.parametrize(
    ("audio", "rate"),
    [(b"", 16_000), (b"\x00\x00\x00", 16_000), (_pcm(1), 48_000)],
    ids=["empty", "odd-length", "wrong-rate"],
)
async def test_invalid_audio_is_rejected_before_any_request(http_client, audio, rate) -> None:
    client = http_client()
    with pytest.raises(offline.AsrOfflineError) as caught:
        await _transcriber("glm").transcribe(audio, sample_rate_hz=rate)
    assert caught.value.code == "ASR_OFFLINE_INVALID_AUDIO"
    assert client.calls == []


# --------------------------------------------------------------------------
# GLM (shared request with the realtime worker)


async def test_glm_success_uses_registry_model_and_wav(http_client) -> None:
    client = http_client(_FakeResponse({"text": " 你好 "}))
    text = await _transcriber("glm").transcribe(_pcm(3), language="zh")
    assert text == "你好"
    (call,) = client.calls
    assert call["url"] == "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions"
    assert call["data"] == {"model": ASR_PROVIDER_REGISTRY["glm"].offline.model}
    assert call["headers"] == {"Authorization": "Bearer key"}
    name, wav_bytes, mime = call["files"]["file"]
    assert (name, mime) == ("audio.wav", "audio/wav")
    assert _wav_frames(wav_bytes) == (1, 2, 16_000, 3 * SAMPLE_RATE)
    assert client.closed


async def test_glm_splits_thirty_seconds_and_joins_in_order(http_client) -> None:
    client = http_client(_FakeResponse({"text": "前半"}), _FakeResponse({"text": "后半"}))
    text = await _transcriber("glm").transcribe(_pcm(30))
    assert text == "前半后半"
    frames = [_wav_frames(call["files"]["file"][1])[3] for call in client.calls]
    assert frames == [15 * SAMPLE_RATE, 15 * SAMPLE_RATE]


@pytest.mark.parametrize(
    ("outcome", "code", "provider_code"),
    [
        (_FakeResponse({}, status_code=401), "ASR_CREDENTIALS_REJECTED", "ASR_CREDENTIALS_REJECTED"),
        (_FakeResponse({}, status_code=429), "ASR_RATE_LIMITED", "ASR_GLM_ERROR"),
        (_FakeResponse({}, status_code=400), "ASR_OFFLINE_PROVIDER_REJECTED", "ASR_GLM_ERROR"),
        (_FakeResponse({}, status_code=503), "ASR_OFFLINE_PROVIDER_FAILED", "ASR_GLM_ERROR"),
        (httpx.ReadTimeout("slow"), "ASR_OFFLINE_TIMEOUT", "ASR_GLM_TIMEOUT"),
        (_FakeResponse({"no_text": 1}), "ASR_OFFLINE_INVALID_RESPONSE", "ASR_GLM_PROTOCOL_ERROR"),
        (_FakeResponse(ValueError("not json")), "ASR_OFFLINE_INVALID_RESPONSE", "ASR_GLM_PROTOCOL_ERROR"),
        (httpx.ConnectError("down"), "ASR_OFFLINE_PROVIDER_FAILED", "ASR_GLM_WORKER_FAILED"),
    ],
)
async def test_glm_failures_map_to_machine_codes(http_client, outcome, code, provider_code) -> None:
    client = http_client(outcome)
    with pytest.raises(offline.AsrOfflineError) as caught:
        await _transcriber("glm").transcribe(_pcm(1))
    assert caught.value.code == code
    assert caught.value.provider_code == provider_code
    assert caught.value.provider_key == "glm"
    assert client.closed


async def test_glm_hung_request_times_out_on_registry_budget(http_client) -> None:
    client = http_client("hang")
    with pytest.raises(offline.AsrOfflineError) as caught:
        await _transcriber("glm", request_timeout_ms=20).transcribe(_pcm(1))
    assert caught.value.code == "ASR_OFFLINE_TIMEOUT"
    assert client.cancelled == 1


async def test_one_failed_segment_cancels_its_sibling(http_client) -> None:
    client = http_client("hang", _FakeResponse({}, status_code=401))
    with pytest.raises(offline.AsrOfflineError) as caught:
        await _transcriber("glm").transcribe(_pcm(30))
    assert caught.value.code == "ASR_CREDENTIALS_REJECTED"
    assert client.cancelled == 1
    assert client.closed


# --------------------------------------------------------------------------
# OpenAI / xAI native file endpoints


async def test_openai_file_transcription_request(http_client) -> None:
    client = http_client(_FakeResponse({"text": "hello"}))
    assert await _transcriber("openai").transcribe(_pcm(2), language="en-US") == "hello"
    (call,) = client.calls
    assert call["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert call["data"] == {
        "model": ASR_PROVIDER_REGISTRY["openai"].offline.model,
        "response_format": "json",
        "language": "en",
    }


async def test_openai_unsupported_language_falls_back_to_detection(http_client) -> None:
    client = http_client(_FakeResponse({"text": "こんにちは"}))
    await _transcriber("openai").transcribe(_pcm(1), language="ja")
    assert "language" not in client.calls[0]["data"]


async def test_openai_auth_rejection(http_client) -> None:
    http_client(_FakeResponse({}, status_code=401))
    with pytest.raises(offline.AsrOfflineError) as caught:
        await _transcriber("openai").transcribe(_pcm(1))
    assert caught.value.code == "ASR_CREDENTIALS_REJECTED"


async def test_grok_file_transcription_request(http_client) -> None:
    client = http_client(_FakeResponse({"text": "你好", "language": "zh", "duration": 1.0}))
    assert await _transcriber("grok").transcribe(_pcm(1), language="zh-CN") == "你好"
    (call,) = client.calls
    assert call["url"] == "https://api.x.ai/v1/stt"
    assert call["data"] == {
        "model": ASR_PROVIDER_REGISTRY["grok"].offline.model,
        "language": "zh",
    }


async def test_grok_rate_limit(http_client) -> None:
    http_client(_FakeResponse({}, status_code=429))
    with pytest.raises(offline.AsrOfflineError) as caught:
        await _transcriber("grok").transcribe(_pcm(1))
    assert caught.value.code == "ASR_RATE_LIMITED"
    assert caught.value.provider_code == "ASR_GROK_OFFLINE_ERROR"


# --------------------------------------------------------------------------
# Gemini (shared request with the realtime worker)


class _FakeGeminiModels:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if outcome == "hang":
            await asyncio.Event().wait()
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(parsed={"transcript": outcome})


class _FakeGeminiClient:
    def __init__(self, *outcomes: Any) -> None:
        self.models = _FakeGeminiModels(*outcomes)
        self.closed = False
        self.aio = SimpleNamespace(models=self.models, aclose=self._aclose)

    async def _aclose(self) -> None:
        self.closed = True


class _GeminiAuthError(Exception):
    code = 401


async def test_gemini_success_and_split(monkeypatch) -> None:
    client = _FakeGeminiClient("第一段", "第二段")
    monkeypatch.setattr(offline, "_create_gemini_client", lambda key: client)
    assert await _transcriber("gemini").transcribe(_pcm(30)) == "第一段第二段"
    assert [call["model"] for call in client.models.calls] == [
        ASR_PROVIDER_REGISTRY["gemini"].offline.model
    ] * 2
    wav = client.models.calls[0]["contents"][0]["parts"][1]["inline_data"]["data"]
    assert _wav_frames(wav)[2] == 16_000
    assert client.closed


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (_GeminiAuthError("bad key"), "ASR_CREDENTIALS_REJECTED"),
        (RuntimeError("boom"), "ASR_OFFLINE_PROVIDER_FAILED"),
        (ValueError("bad json"), "ASR_OFFLINE_INVALID_RESPONSE"),
    ],
)
async def test_gemini_failures(monkeypatch, outcome, code) -> None:
    monkeypatch.setattr(offline, "_create_gemini_client", lambda key: _FakeGeminiClient(outcome))
    with pytest.raises(offline.AsrOfflineError) as caught:
        await _transcriber("gemini").transcribe(_pcm(1))
    assert caught.value.code == code


async def test_gemini_timeout_uses_registry_budget(monkeypatch) -> None:
    monkeypatch.setattr(offline, "_create_gemini_client", lambda key: _FakeGeminiClient("hang"))
    with pytest.raises(offline.AsrOfflineError) as caught:
        await _transcriber("gemini", request_timeout_ms=20).transcribe(_pcm(1))
    assert caught.value.code == "ASR_OFFLINE_TIMEOUT"
    assert caught.value.provider_code == "ASR_GEMINI_TIMEOUT"


async def test_gemini_missing_sdk(monkeypatch) -> None:
    def missing(_key: str) -> Any:
        raise ImportError("google-genai")

    monkeypatch.setattr(offline, "_create_gemini_client", missing)
    with pytest.raises(offline.AsrOfflineError) as caught:
        await _transcriber("gemini").transcribe(_pcm(1))
    assert caught.value.code == "ASR_DEPENDENCY_MISSING"


# --------------------------------------------------------------------------
# faster-whisper (shared pool, decoder and decode with the realtime worker)


class _FakeWhisperModel:
    def __init__(self, *texts: str, error: Exception | None = None) -> None:
        self.texts = list(texts)
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: Any, **kwargs: Any):
        self.calls.append({"samples": len(audio), **kwargs})
        if self.error is not None:
            raise self.error
        segment = SimpleNamespace(
            text=self.texts.pop(0), no_speech_prob=0.0, avg_logprob=-0.1
        )
        return iter([segment]), SimpleNamespace()


@pytest.fixture
def whisper_pool(monkeypatch):
    for name in ("NEKO_WHISPER_MODEL", "NEKO_WHISPER_DEVICE", "NEKO_WHISPER_COMPUTE"):
        monkeypatch.delenv(name, raising=False)
    pool = faster_whisper._WhisperModelPool(idle_release_seconds=60.0)
    monkeypatch.setattr(faster_whisper, "_MODEL_POOL", pool)
    return pool


async def test_faster_whisper_decodes_each_segment_and_returns_leases(monkeypatch, whisper_pool) -> None:
    model = _FakeWhisperModel("前半", "后半")
    monkeypatch.setattr(faster_whisper, "_load_whisper_model", lambda spec: model)
    text = await _transcriber("faster_whisper", api_key="").transcribe(_pcm(30), language="zh-TW")
    assert text == "前半后半"
    assert [call["samples"] for call in model.calls] == [15 * SAMPLE_RATE] * 2
    assert model.calls[0]["language"] == "zh"
    assert model.calls[0]["initial_prompt"]  # Traditional-script priming reused
    spec = faster_whisper._model_spec_from_env()
    assert whisper_pool.lease_count(spec) == 0
    assert whisper_pool.try_reserve_decode()  # slots were given back
    whisper_pool.release_decode()


async def test_faster_whisper_busy_decoder(monkeypatch, whisper_pool) -> None:
    monkeypatch.setattr(faster_whisper, "_load_whisper_model", lambda spec: _FakeWhisperModel("x"))
    monkeypatch.setattr(whisper_pool, "try_reserve_decode", lambda: False)
    with pytest.raises(offline.AsrOfflineError) as caught:
        await _transcriber("faster_whisper", api_key="").transcribe(_pcm(1))
    assert caught.value.code == "ASR_RATE_LIMITED"
    assert caught.value.provider_code == "ASR_LOCAL_BUSY"
    assert whisper_pool.lease_count(faster_whisper._model_spec_from_env()) == 0


async def test_faster_whisper_load_failure(monkeypatch, whisper_pool) -> None:
    def fail(spec: Any) -> Any:
        raise faster_whisper._LocalAsrFailure("ASR_LOCAL_MODEL_LOAD_FAILED", "no weights")

    monkeypatch.setattr(faster_whisper, "_load_whisper_model", fail)
    with pytest.raises(offline.AsrOfflineError) as caught:
        await _transcriber("faster_whisper", api_key="").transcribe(_pcm(1))
    assert caught.value.code == "ASR_OFFLINE_PROVIDER_FAILED"
    assert caught.value.provider_code == "ASR_LOCAL_MODEL_LOAD_FAILED"


async def test_faster_whisper_load_timeout(monkeypatch, whisper_pool) -> None:
    release = threading.Event()

    def slow(spec: Any) -> Any:
        release.wait(5)
        return _FakeWhisperModel("late")

    monkeypatch.setattr(faster_whisper, "_load_whisper_model", slow)
    meta = dataclasses.replace(
        ASR_PROVIDER_REGISTRY["faster_whisper"], provider_warmup_timeout_ms=20
    )
    transcriber = dataclasses.replace(_transcriber("faster_whisper", api_key=""), meta=meta)
    try:
        with pytest.raises(offline.AsrOfflineError) as caught:
            await transcriber.transcribe(_pcm(1))
    finally:
        release.set()
    assert caught.value.code == "ASR_OFFLINE_TIMEOUT"
    assert caught.value.provider_code == "ASR_LOCAL_MODEL_LOAD_TIMEOUT"


async def test_faster_whisper_decode_failure(monkeypatch, whisper_pool) -> None:
    model = _FakeWhisperModel(error=RuntimeError("cuda"))
    monkeypatch.setattr(faster_whisper, "_load_whisper_model", lambda spec: model)
    with pytest.raises(offline.AsrOfflineError) as caught:
        await _transcriber("faster_whisper", api_key="").transcribe(_pcm(1))
    assert caught.value.provider_code == "ASR_LOCAL_TRANSCRIBE_FAILED"
    assert whisper_pool.lease_count(faster_whisper._model_spec_from_env()) == 0


# --------------------------------------------------------------------------
# Session replay (Qwen / Soniox) through a fake manual-endpointing worker


def _fake_stream_worker(
    *,
    error_code: str | None = None,
    hang: bool = False,
    seen: list[Any] | None = None,
):
    async def worker(request_queue, response_queue, api_key, config) -> None:
        if seen is not None:
            seen.append(config)
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        sizes: dict[tuple[int, int, int], int] = {}
        while True:
            request = await request_queue.get()
            try:
                key = (request.generation, request.buffer_epoch, request.utterance_id)
                if request.kind == "shutdown":
                    await response_queue.put(
                        _AsrWorkerEvent(kind="closed", generation=request.generation)
                    )
                    return
                if request.kind == "audio":
                    sizes[key] = sizes.get(key, 0) + len(request.audio)
                elif request.kind == "commit":
                    if error_code is not None:
                        await response_queue.put(
                            _AsrWorkerEvent(
                                kind="error",
                                generation=request.generation,
                                error_code=error_code,
                                error_message="provider said no",
                            )
                        )
                    elif not hang:
                        await response_queue.put(
                            _AsrWorkerEvent(
                                kind="final",
                                generation=request.generation,
                                buffer_epoch=request.buffer_epoch,
                                utterance_id=request.utterance_id,
                                text=f"seg{request.utterance_id}:{sizes.get(key, 0) // BYTES_PER_SECOND}s",
                            )
                        )
            finally:
                request_queue.task_done()

    return worker


@pytest.mark.parametrize("provider_key", ["qwen", "soniox"])
async def test_session_replay_returns_one_final(provider_key) -> None:
    seen: list[Any] = []
    transcriber = _transcriber(provider_key, worker_fn=_fake_stream_worker(seen=seen))
    text = await transcriber.transcribe(_pcm(5), language="zh-CN")
    assert text.endswith(":5s")
    (config,) = seen
    assert config.endpointing_mode == "manual"


async def test_session_replay_splits_into_one_commit_per_segment() -> None:
    transcriber = _transcriber(
        "qwen", worker_fn=_fake_stream_worker(), max_segment_ms=10_000
    )
    text = await transcriber.transcribe(_pcm(30))
    parts = text.split(" ")
    assert len(parts) == 3
    assert all(part.endswith(":10s") for part in parts)


@pytest.mark.parametrize(
    ("worker_code", "code"),
    [
        ("ASR_CREDENTIALS_REJECTED", "ASR_CREDENTIALS_REJECTED"),
        ("ASR_SONIOX_CONNECTION_LIMIT", "ASR_RATE_LIMITED"),
        ("ASR_QWEN_WORKER_FAILED", "ASR_OFFLINE_PROVIDER_FAILED"),
    ],
)
async def test_session_replay_provider_errors(worker_code, code) -> None:
    transcriber = _transcriber("qwen", worker_fn=_fake_stream_worker(error_code=worker_code))
    with pytest.raises(offline.AsrOfflineError) as caught:
        await transcriber.transcribe(_pcm(2))
    assert caught.value.code == code
    assert caught.value.provider_code == worker_code


async def test_session_replay_timeout() -> None:
    transcriber = _transcriber(
        "soniox", worker_fn=_fake_stream_worker(hang=True), request_timeout_ms=200
    )
    with pytest.raises(offline.AsrOfflineError) as caught:
        await transcriber.transcribe(_pcm(1))
    assert caught.value.code == "ASR_OFFLINE_TIMEOUT"


# --------------------------------------------------------------------------
# Route selection


@pytest.fixture
def core_config(monkeypatch):
    for name in ("ASR_PROVIDER", "ASR_USER_REGION", "SONIOX_API_KEY", "SONIOX_REGION"):
        monkeypatch.delenv(name, raising=False)
    config: dict[str, Any] = {}
    monkeypatch.setattr(asr_client, "_load_core_config", lambda: dict(config))
    return config


def _set_local_dependency(monkeypatch, available: bool) -> None:
    probe = lambda module: (available if module == "faster_whisper" else True)  # noqa: E731
    monkeypatch.setattr(asr_client, "_optional_dependency_available", probe)
    monkeypatch.setattr(offline, "_optional_dependency_available", probe)


@pytest.mark.parametrize(
    ("core", "field", "provider", "mode"),
    [
        ("glm", "ASSIST_API_KEY_GLM", "glm", "request"),
        ("gemini", "ASSIST_API_KEY_GEMINI", "gemini", "request"),
        ("qwen", "ASSIST_API_KEY_QWEN", "qwen", "session"),
        ("qwen_intl", "ASSIST_API_KEY_QWEN_INTL", "qwen", "session"),
        ("openai", "ASSIST_API_KEY_OPENAI", "openai", "request"),
        ("grok", "ASSIST_API_KEY_GROK", "grok", "request"),
    ],
)
def test_core_routes_resolve_to_their_provider(core_config, core, field, provider, mode) -> None:
    core_config[field] = "secret"
    transcriber = offline.resolve_offline_transcriber(core)
    assert transcriber.provider_key == provider
    assert transcriber.spec.mode == mode
    assert transcriber._api_key == "secret"


def test_missing_credentials(core_config) -> None:
    with pytest.raises(offline.AsrOfflineError) as caught:
        offline.resolve_offline_transcriber("glm")
    assert caught.value.code == "ASR_CREDENTIALS_MISSING"


def test_step_is_unsupported_even_with_credentials(core_config) -> None:
    core_config["ASSIST_API_KEY_STEP"] = "secret"
    with pytest.raises(offline.AsrOfflineError) as caught:
        offline.resolve_offline_transcriber("step")
    assert caught.value.code == "ASR_OFFLINE_NOT_SUPPORTED"
    assert offline.get_offline_asr_status("step") == offline.OfflineAsrStatus(
        supported=False, provider_key="step", error_code="ASR_OFFLINE_NOT_SUPPORTED"
    )


def test_unknown_core(core_config) -> None:
    with pytest.raises(offline.AsrOfflineError) as caught:
        offline.resolve_offline_transcriber("nope")
    assert caught.value.code == "ASR_UNKNOWN_CORE"


def test_intl_region_with_soniox_key_prefers_soniox(core_config) -> None:
    core_config["ASSIST_API_KEY_QWEN"] = "qwen"
    core_config["SONIOX_API_KEY"] = "soniox"
    transcriber = offline.resolve_offline_transcriber("qwen", user_region="eu")
    assert transcriber.provider_key == "soniox"
    assert transcriber._selection.soniox_region == "eu"


def test_free_core_without_local_asr_is_unsupported(core_config, monkeypatch) -> None:
    _set_local_dependency(monkeypatch, False)
    status = offline.get_offline_asr_status("free")
    assert status.supported is False
    assert status.error_code == "ASR_OFFLINE_NOT_SUPPORTED"


def test_free_core_falls_back_to_installed_local_asr(core_config, monkeypatch) -> None:
    _set_local_dependency(monkeypatch, True)
    transcriber = offline.resolve_offline_transcriber("free")
    assert transcriber.provider_key == "faster_whisper"


def test_local_preference_wins_on_cloud_core(core_config, monkeypatch) -> None:
    core_config["ASSIST_API_KEY_GLM"] = "secret"
    _set_local_dependency(monkeypatch, True)
    transcriber = offline.resolve_offline_transcriber(
        "glm", provider_preference="faster_whisper"
    )
    assert transcriber.provider_key == "faster_whisper"


def test_local_preference_without_package_reports_missing_dependency(core_config, monkeypatch) -> None:
    _set_local_dependency(monkeypatch, False)
    with pytest.raises(offline.AsrOfflineError) as caught:
        offline.resolve_offline_transcriber("free", provider_preference="faster_whisper")
    assert caught.value.code == "ASR_DEPENDENCY_MISSING"


async def test_transcribe_recording_end_to_end(core_config, http_client) -> None:
    core_config["ASSIST_API_KEY_GLM"] = "secret"
    client = http_client(_FakeResponse({"text": "你好"}))
    text = await offline.transcribe_recording("glm", _pcm(2), language="zh")
    assert text == "你好"
    assert client.calls[0]["headers"] == {"Authorization": "Bearer secret"}


async def test_dev_dummy_override_runs_through_session(core_config, monkeypatch) -> None:
    monkeypatch.setenv("ASR_PROVIDER", "dummy")
    monkeypatch.setenv("ASR_DUMMY_DELAY_MS", "0")
    transcriber = offline.resolve_offline_transcriber("glm")
    assert transcriber.provider_key == "dummy"
    assert isinstance(await transcriber.transcribe(_pcm(1)), str)
