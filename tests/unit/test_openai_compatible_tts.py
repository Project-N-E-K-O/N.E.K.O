import json
import queue
import threading
import time
from functools import partial

import httpx
import numpy as np
import pytest

from main_logic import tts_client
from main_logic.tts_client.workers import openai as openai_worker_module
from main_routers.config_router.connectivity import _test_connectivity_candidates
from utils.openai_tts import (
    OpenAITtsConfigError,
    build_openai_tts_payload,
    openai_tts_speech_url,
)
from utils.tts import provider_registry


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("https://speech.example.com", "https://speech.example.com/v1/audio/speech"),
        ("https://speech.example.com/v1", "https://speech.example.com/v1/audio/speech"),
        (
            "https://speech.example.com/openai/v1/audio/speech/",
            "https://speech.example.com/openai/v1/audio/speech",
        ),
        ("http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1/audio/speech"),
    ],
)
def test_openai_tts_speech_url_normalization(configured, expected):
    assert openai_tts_speech_url(configured) == expected


@pytest.mark.parametrize("configured", ["", "ws://speech.example.com/v1", "speech.example.com/v1"])
def test_openai_tts_speech_url_rejects_non_http(configured):
    with pytest.raises(OpenAITtsConfigError):
        openai_tts_speech_url(configured)


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


def test_configured_custom_voice_is_exposed_as_preset():
    cm = _CustomTtsConfigManager()
    catalog = provider_registry.preset_catalog_for_ui("custom", cm.snapshot)
    assert catalog == {
        "vendor-voice": {
            "prefix": "vendor-voice",
            "provider": "custom",
            "provider_label": "custom",
            "gender": "",
            "display_name": "vendor-voice",
            "builtin": True,
        }
    }
    assert provider_registry.is_preset_voice("custom", "vendor-voice", cm.snapshot) is True


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


def test_openai_tts_worker_posts_configured_endpoint_and_streams_pcm(monkeypatch):
    requests = []

    async def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, content=np.array([1, -2, 3], dtype="<i2").tobytes())

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        openai_worker_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(
        openai_worker_module,
        "_resample_audio",
        lambda audio, *_args, last=False: b"" if last else audio,
    )

    request_queue = queue.Queue()
    response_queue = queue.Queue()
    worker = partial(
        tts_client.openai_tts_worker,
        base_url="https://speech.example.com/v1",
        model="vendor-tts",
        voice="fallback-voice",
    )
    thread = threading.Thread(
        target=worker,
        args=(request_queue, response_queue, "sk-test", "character-voice"),
        daemon=True,
    )
    thread.start()
    assert _wait_for_item(response_queue, lambda item: item == ("__ready__", True)) == ("__ready__", True)

    request_queue.put(("speech-1", "hello world."))
    request_queue.put((None, None))
    audio = _wait_for_item(response_queue, lambda item: isinstance(item, np.ndarray))
    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=5)

    assert audio.tolist() == [1, -2, 3]
    assert len(requests) == 1
    assert str(requests[0].url) == "https://speech.example.com/v1/audio/speech"
    assert requests[0].headers["authorization"] == "Bearer sk-test"
    assert json.loads(requests[0].content) == {
        "model": "vendor-tts",
        "input": "hello world.",
        "voice": "character-voice",
        "response_format": "pcm",
    }


class _ChunkedPcmStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


def test_openai_tts_worker_reuses_one_resampler_across_transport_chunks(monkeypatch):
    pcm = np.arange(5000, dtype="<i2").tobytes()

    async def handler(_request: httpx.Request):
        return httpx.Response(
            200,
            stream=_ChunkedPcmStream([pcm[:3001], pcm[3001:7002], pcm[7002:]]),
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        openai_worker_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    resample_calls = []

    def fake_resample(audio, _src_rate, _dst_rate, resampler, *, last=False):
        resample_calls.append((audio.copy(), resampler, last))
        return b"" if last else audio.tobytes()

    monkeypatch.setattr(openai_worker_module, "_resample_audio", fake_resample)

    request_queue = queue.Queue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.openai_tts_worker,
        args=(request_queue, response_queue, "sk-test", "character-voice"),
        kwargs={"base_url": "https://speech.example.com/v1"},
        daemon=True,
    )
    thread.start()
    _wait_for_item(response_queue, lambda item: item == ("__ready__", True))
    request_queue.put(("speech-1", "hello world."))
    request_queue.put((None, None))
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
    async def handler(_request: httpx.Request):
        return httpx.Response(200, content=content)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        openai_worker_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    request_queue = queue.Queue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.openai_tts_worker,
        args=(request_queue, response_queue, "sk-test", "character-voice"),
        kwargs={"base_url": "https://speech.example.com/v1"},
        daemon=True,
    )
    thread.start()
    _wait_for_item(response_queue, lambda item: item == ("__ready__", True))
    request_queue.put(("speech-1", "hello world."))
    request_queue.put((None, None))
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
async def test_connectivity_dispatches_openai_tts_probe(monkeypatch):
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
        ["https://speech.example.com/v1"],
        "sk-probe",
        "vendor-tts",
        "tts",
        False,
        sub_type="openai_tts",
        voice_id="vendor-voice",
    )

    assert result["success"] is True
    assert result["resolved_url"] == "https://speech.example.com/v1"
    assert str(requests[0].url) == "https://speech.example.com/v1/audio/speech"
    assert json.loads(requests[0].content)["response_format"] == "pcm"
