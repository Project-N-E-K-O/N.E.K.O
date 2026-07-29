import json
import queue
import threading
import time
from functools import partial
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from main_logic import tts_client
from main_logic.tts_client.workers import openai as openai_worker_module
from main_routers.config_router.connectivity import _test_connectivity_candidates
from utils.openai_tts import (
    OPENAI_TTS_PCM_SAMPLE_RATE,
    OpenAITtsConfigError,
    build_openai_tts_payload,
    openai_tts_base_url,
    openai_tts_extra_body,
    openai_tts_speech_url,
)
from utils.tts import provider_registry


@pytest.mark.parametrize(
    ("configured", "expected_base", "expected_endpoint"),
    [
        (
            "https://speech.example.com",
            "https://speech.example.com/v1",
            "https://speech.example.com/v1/audio/speech",
        ),
        (
            "https://speech.example.com/v1",
            "https://speech.example.com/v1",
            "https://speech.example.com/v1/audio/speech",
        ),
        (
            "https://speech.example.com/openai/v1/audio/speech/",
            "https://speech.example.com/openai/v1",
            "https://speech.example.com/openai/v1/audio/speech",
        ),
        (
            "http://127.0.0.1:8000/v1",
            "http://127.0.0.1:8000/v1",
            "http://127.0.0.1:8000/v1/audio/speech",
        ),
    ],
)
def test_openai_tts_url_normalization(configured, expected_base, expected_endpoint):
    assert openai_tts_base_url(configured) == expected_base
    assert openai_tts_speech_url(configured) == expected_endpoint


@pytest.mark.parametrize("configured", ["", "ws://speech.example.com/v1", "speech.example.com/v1"])
def test_openai_tts_url_rejects_non_http(configured):
    with pytest.raises(OpenAITtsConfigError):
        openai_tts_speech_url(configured)


def test_openai_tts_endpoint_preserves_query_after_path_normalization():
    assert openai_tts_speech_url("https://speech.example.com/v1?tenant=demo") == (
        "https://speech.example.com/v1/audio/speech?tenant=demo"
    )


def test_openai_tts_payload_is_strict_pcm():
    assert build_openai_tts_payload("hello", "tts-model", "voice-a") == {
        "model": "tts-model",
        "input": "hello",
        "voice": "voice-a",
        "response_format": "pcm",
    }


@pytest.mark.parametrize(("model", "voice"), [("", "voice-a"), ("tts-model", "")])
def test_openai_tts_payload_requires_model_and_voice(model, voice):
    with pytest.raises(OpenAITtsConfigError):
        build_openai_tts_payload("hello", model, voice)


def test_siliconflow_pins_streaming_pcm_sample_rate_without_polluting_other_providers():
    assert openai_tts_extra_body("https://api.siliconflow.cn/v1") == {
        "sample_rate": OPENAI_TTS_PCM_SAMPLE_RATE,
        "stream": True,
    }
    assert openai_tts_extra_body("https://speech.example.com/v1") == {}


class _CustomTtsConfigManager:
    def __init__(self):
        self.load_count = 0
        self.raw = {
            "enableCustomApi": True,
            "ttsModelProvider": "custom",
            "ttsModelUrl": "https://speech.example.com/v1",
            "ttsModelId": "vendor-tts",
            "ttsModelApiKey": "sk-custom",
            "ttsVoiceId": "vendor-voice",
        }
        self.snapshot = {
            "ENABLE_CUSTOM_API": True,
            **self.raw,
        }

    def get_core_config(self):
        return dict(self.snapshot)

    def load_json_config(self, _name, _default):
        self.load_count += 1
        return dict(self.raw)

    def get_voices_for_current_api(self):
        return {}


def test_custom_openai_tts_dispatch_binds_config(monkeypatch):
    cm = _CustomTtsConfigManager()
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="gemini",
        has_custom_voice=True,
        voice_id="vendor-voice",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.openai_tts_worker
    assert worker.keywords == {
        "base_url": "https://speech.example.com/v1",
        "model": "vendor-tts",
        "voice": "vendor-voice",
    }
    assert api_key == "sk-custom"
    assert provider_key == "custom"


def test_custom_openai_tts_selection_uses_snapshot_without_disk_read():
    cm = _CustomTtsConfigManager()
    ctx = provider_registry.DispatchContext(
        core_config=cm.snapshot,
        cm=cm,
        voice_id="vendor-voice",
        has_custom_voice=True,
    )

    assert openai_worker_module._custom_openai_tts_is_selected(ctx) is True
    assert cm.load_count == 0


def test_custom_openai_tts_does_not_override_stored_clone():
    cm = _CustomTtsConfigManager()
    ctx = provider_registry.DispatchContext(
        core_config=cm.snapshot,
        cm=cm,
        voice_id="clone-voice",
        has_custom_voice=True,
        voice_meta_loader=lambda: {"provider": "cosyvoice", "source": "clone"},
    )
    assert openai_worker_module._custom_openai_tts_is_selected(ctx) is False


def test_custom_openai_tts_uses_character_voice_without_configured_fallback():
    cm = _CustomTtsConfigManager()
    cm.snapshot["ttsVoiceId"] = ""
    ctx = provider_registry.DispatchContext(
        core_config=cm.snapshot,
        cm=cm,
        voice_id="character-voice",
        has_custom_voice=False,
    )

    assert openai_worker_module._custom_openai_tts_is_selected(ctx) is True


def test_custom_openai_tts_requires_an_effective_voice():
    cm = _CustomTtsConfigManager()
    cm.snapshot["ttsVoiceId"] = ""
    ctx = provider_registry.DispatchContext(
        core_config=cm.snapshot,
        cm=cm,
        voice_id="",
        has_custom_voice=False,
    )

    assert openai_worker_module._custom_openai_tts_is_selected(ctx) is False


def _wait_for_item(q, predicate, timeout=5.0):
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        try:
            item = q.get(timeout=max(0.01, deadline - time.time()))
        except queue.Empty:
            continue
        seen.append(item)
        if predicate(item):
            return item
    raise AssertionError(f"timed out waiting for queue item; seen={seen!r}")


def _install_fake_openai(monkeypatch, chunks):
    import openai

    clients = []

    class _FakeStreamingResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

        async def iter_bytes(self, chunk_size=4096):
            del chunk_size
            for chunk in chunks:
                yield chunk

    class _FakeCreate:
        def __init__(self, calls):
            self._calls = calls

        def create(self, **kwargs):
            self._calls.append(kwargs)
            return _FakeStreamingResponse()

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            self.create_calls = []
            self.closed = False
            self.audio = SimpleNamespace(
                speech=SimpleNamespace(
                    with_streaming_response=_FakeCreate(self.create_calls),
                )
            )
            clients.append(self)

        async def close(self):
            self.closed = True

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    return clients


def _run_worker_once(monkeypatch, chunks, *, base_url=None, model=None, voice_id="character-voice"):
    clients = _install_fake_openai(monkeypatch, chunks)
    request_queue = queue.Queue()
    response_queue = queue.Queue()
    kwargs = {}
    if base_url is not None:
        kwargs["base_url"] = base_url
    if model is not None:
        kwargs["model"] = model
    thread = threading.Thread(
        target=tts_client.openai_tts_worker,
        args=(request_queue, response_queue, "sk-test", voice_id),
        kwargs=kwargs,
        daemon=True,
    )
    thread.start()
    assert _wait_for_item(response_queue, lambda item: item == ("__ready__", True)) == (
        "__ready__",
        True,
    )
    request_queue.put(("speech-1", "hello world."))
    request_queue.put((None, None))
    return clients, request_queue, response_queue, thread


def test_custom_worker_uses_openai_sdk_and_siliconflow_extensions(monkeypatch):
    monkeypatch.setattr(
        openai_worker_module,
        "_resample_audio",
        lambda audio, *_args, last=False: b"" if last else audio,
    )
    clients, request_queue, response_queue, thread = _run_worker_once(
        monkeypatch,
        [np.array([1, -2, 3], dtype="<i2").tobytes()],
        base_url="https://api.siliconflow.cn/v1/audio/speech",
        model="FunAudioLLM/CosyVoice2-0.5B",
    )
    audio = _wait_for_item(response_queue, lambda item: isinstance(item, np.ndarray))
    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=5)

    assert audio.tolist() == [1, -2, 3]
    assert clients[0].client_kwargs == {
        "api_key": "sk-test",
        "base_url": "https://api.siliconflow.cn/v1",
    }
    assert clients[0].create_calls == [{
        "model": "FunAudioLLM/CosyVoice2-0.5B",
        "voice": "character-voice",
        "input": "hello world.",
        "response_format": "pcm",
        "extra_body": {"sample_rate": 24000, "stream": True},
    }]
    assert clients[0].closed is True


def test_builtin_openai_worker_keeps_sdk_default_endpoint_and_body(monkeypatch):
    monkeypatch.setattr(
        openai_worker_module,
        "_resample_audio",
        lambda audio, *_args, last=False: b"" if last else audio,
    )
    clients, request_queue, response_queue, thread = _run_worker_once(
        monkeypatch,
        [b"\x00\x00"],
    )
    _wait_for_item(response_queue, lambda item: isinstance(item, np.ndarray))
    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=5)

    assert clients[0].client_kwargs == {"api_key": "sk-test"}
    assert clients[0].create_calls == [{
        "model": "gpt-4o-mini-tts",
        "voice": "character-voice",
        "input": "hello world.",
        "response_format": "pcm",
    }]


def test_openai_tts_worker_reuses_one_resampler_across_transport_chunks(monkeypatch):
    pcm = np.arange(5000, dtype="<i2").tobytes()
    resample_calls = []

    def fake_resample(audio, _src_rate, _dst_rate, resampler, *, last=False):
        resample_calls.append((audio.copy(), resampler, last))
        return b"" if last else audio.tobytes()

    monkeypatch.setattr(openai_worker_module, "_resample_audio", fake_resample)
    _, request_queue, response_queue, thread = _run_worker_once(
        monkeypatch,
        [pcm[:3001], pcm[3001:7002], pcm[7002:]],
        base_url="https://speech.example.com/v1",
    )
    _wait_for_item(response_queue, lambda item: isinstance(item, bytes) and bool(item))
    deadline = time.time() + 5
    while time.time() < deadline and not any(call[2] for call in resample_calls):
        time.sleep(0.01)
    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=5)

    assert len(resample_calls) >= 4
    assert len({id(call[1]) for call in resample_calls}) == 1
    assert all(not call[2] for call in resample_calls[:-1])
    assert resample_calls[-1][2] is True
    assert np.concatenate([call[0] for call in resample_calls[:-1]]).tobytes() == pcm


@pytest.mark.parametrize(
    ("content", "expected_error"),
    [(b"", "empty PCM response"), (b"\x01", "truncated PCM sample")],
)
def test_openai_tts_worker_rejects_empty_or_truncated_pcm(content, expected_error, monkeypatch):
    _, request_queue, response_queue, thread = _run_worker_once(
        monkeypatch,
        [content],
        base_url="https://speech.example.com/v1",
    )
    error = _wait_for_item(
        response_queue,
        lambda item: isinstance(item, tuple) and item[0] == "__error__",
    )
    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=5)

    assert expected_error in error[1]


def test_openai_tts_worker_reports_invalid_url_as_not_ready():
    request_queue = queue.Queue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.openai_tts_worker,
        args=(request_queue, response_queue, "", "voice-a"),
        kwargs={"base_url": "ws://speech.example.com/v1"},
        daemon=True,
    )
    thread.start()

    assert _wait_for_item(response_queue, lambda item: item == ("__ready__", False)) == (
        "__ready__",
        False,
    )
    thread.join(timeout=5)
    assert not thread.is_alive()


@pytest.mark.asyncio
async def test_connectivity_dispatches_siliconflow_compatible_tts_probe(monkeypatch):
    requests = []

    async def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, content=b"\x00\x00")

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    result = await _test_connectivity_candidates(
        ["https://api.siliconflow.cn/v1"],
        "sk-probe",
        "FunAudioLLM/CosyVoice2-0.5B",
        "tts",
        False,
        sub_type="openai_tts",
        voice_id="FunAudioLLM/CosyVoice2-0.5B:anna",
    )

    assert result["success"] is True
    assert result["resolved_url"] == "https://api.siliconflow.cn/v1"
    assert str(requests[0].url) == "https://api.siliconflow.cn/v1/audio/speech"
    assert requests[0].headers["authorization"] == "Bearer sk-probe"
    assert json.loads(requests[0].content) == {
        "model": "FunAudioLLM/CosyVoice2-0.5B",
        "input": "测试",
        "voice": "FunAudioLLM/CosyVoice2-0.5B:anna",
        "response_format": "pcm",
        "sample_rate": 24000,
        "stream": True,
    }
