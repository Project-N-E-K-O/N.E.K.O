import asyncio
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
from main_logic.core import LLMSessionManager
from main_logic.core import tts_runtime as tts_runtime_module
from main_logic.tts_client.workers import openai as openai_worker_module
from main_logic.tts_client.workers import vllm_omni as vllm_worker_module
from main_routers.characters_router import voice_preview as voice_preview_module
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

    def get_voices_for_current_api(self, **_kwargs):
        return {}

    async def aensure_region_resolved(self):
        return True

    async def aget_core_config(self):
        return dict(self.snapshot)

    async def aload_characters(self):
        return {"猫娘": {}}


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
        "label": "自定义 TTS API (Custom OpenAI-compatible TTS)",
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


def test_exact_configured_custom_voice_wins_over_same_id_clone():
    cm = _CustomTtsConfigManager()
    ctx = provider_registry.DispatchContext(
        core_config=cm.snapshot,
        cm=cm,
        voice_id="vendor-voice",
        has_custom_voice=True,
        voice_meta_loader=lambda: {"provider": "cosyvoice", "source": "clone"},
    )

    assert openai_worker_module._custom_openai_tts_is_selected(ctx) is True


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


def test_configured_custom_voice_is_exposed_to_character_picker():
    cm = _CustomTtsConfigManager()

    assert provider_registry.preset_catalog_for_ui("custom", cm.snapshot) == {
        "vendor-voice": {
            "prefix": "vendor-voice",
            "provider": "custom",
            "provider_label": "custom",
            "gender": "",
            "display_name": "vendor-voice",
            "builtin": True,
        }
    }


@pytest.mark.asyncio
async def test_voices_endpoint_maps_configured_custom_voice_to_character_catalog(
    monkeypatch,
):
    cm = _CustomTtsConfigManager()
    monkeypatch.setattr(voice_preview_module, "get_config_manager", lambda: cm)

    result = await voice_preview_module.get_voices()

    assert result["native_voices"]["vendor-voice"]["provider"] == "custom"
    assert result["voice_owners"] == {}


@pytest.mark.asyncio
async def test_voices_endpoint_maps_vllm_default_to_custom_api_catalog(monkeypatch):
    cm = _CustomTtsConfigManager()
    cm.raw.update(
        {
            "ttsModelProvider": "vllm_omni",
            "ttsModelUrl": "wss://speech.example.com/v1",
            "ttsVoiceId": "",
        }
    )
    cm.snapshot.update(cm.raw)
    monkeypatch.setattr(voice_preview_module, "get_config_manager", lambda: cm)

    result = await voice_preview_module.get_voices()

    # The catalog source is Custom API, but the runtime owner stays vllm_omni.
    # 目录显示“自定义 API”，实际保存与调度仍归属 vllm_omni。
    assert result["native_voices"]["default"] == {
        "prefix": "default",
        "provider": "vllm_omni",
        "provider_label": "custom",
        "gender": "",
        "display_name": "default",
        "builtin": True,
    }


def test_configured_custom_voice_is_saveable_for_character():
    cm = _CustomTtsConfigManager()

    assert provider_registry.is_selected_preset_voice(
        cm.snapshot,
        cm,
        "vendor-voice",
    )
    assert not provider_registry.is_selected_preset_voice(
        cm.snapshot,
        cm,
        "another-voice",
    )


def test_custom_config_read_failure_uses_existing_default_route(monkeypatch):
    cm = _CustomTtsConfigManager()
    errors = []

    def broken_load(*_args, **_kwargs):
        raise OSError("config unavailable")

    cm.load_json_config = broken_load
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)
    monkeypatch.setattr(
        provider_registry.logger,
        "error",
        lambda message, *args, **_kwargs: errors.append(message % args),
    )

    _worker, _api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=True,
        voice_id="vendor-voice",
    )

    # The legacy dispatch sends a custom character voice to CosyVoice before
    # reaching core-native Qwen. Fallback must preserve that exact order.
    # 这里验证沿用旧保底顺序，不能为了自定义 API 另造一条 Qwen 快捷路径。
    assert provider_key == "cosyvoice"
    assert any("既有顺序尝试保底 provider" in message for message in errors)


def test_excluding_failed_custom_provider_preserves_default_route(monkeypatch):
    cm = _CustomTtsConfigManager()
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)

    _worker, _api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=True,
        voice_id="vendor-voice",
        excluded_provider_keys={"custom"},
    )

    assert provider_key == "cosyvoice"


def test_vllm_config_read_failure_logs_and_uses_existing_default_route(monkeypatch):
    cm = _CustomTtsConfigManager()
    cm.raw["ttsModelProvider"] = "vllm_omni"
    cm.snapshot.update(cm.raw)
    errors = []

    def broken_load(*_args, **_kwargs):
        raise OSError("config unavailable")

    cm.load_json_config = broken_load
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)
    monkeypatch.setattr(
        vllm_worker_module.logger,
        "error",
        lambda message, *args, **_kwargs: errors.append(message % args),
    )

    _worker, _api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="default",
    )

    assert provider_key == "qwen"
    assert any("既有保底流程" in message for message in errors)


def test_unconfigured_custom_tts_keeps_existing_native_route(monkeypatch):
    cm = _CustomTtsConfigManager()
    cm.snapshot["ENABLE_CUSTOM_API"] = False
    cm.snapshot["enableCustomApi"] = False
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)

    _worker, _api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="",
    )

    assert provider_key == "qwen"


def test_resolve_selected_skips_broken_predicate_like_catalog_selection(monkeypatch):
    ctx = provider_registry.DispatchContext(
        core_config={},
        cm=SimpleNamespace(),
    )
    warnings = []
    fallback_result = (object(), None, "fallback")
    broken = SimpleNamespace(
        key="broken",
        is_selected=lambda _ctx: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    healthy = SimpleNamespace(
        key="healthy",
        is_selected=lambda _ctx: True,
        resolve=lambda _ctx: fallback_result,
    )
    monkeypatch.setattr(provider_registry, "all_providers", lambda: [broken, healthy])
    monkeypatch.setattr(
        provider_registry.logger,
        "warning",
        lambda message, *args, **_kwargs: warnings.append(message % args),
    )

    assert provider_registry.resolve_selected(ctx) == fallback_result
    assert any("'broken' is_selected 判定异常" in message for message in warnings)


def test_configured_tts_failure_switches_to_existing_dispatch_order(monkeypatch):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr._tts_active_provider_key = "custom"
    mgr._tts_excluded_provider_keys = frozenset()
    mgr.tts_request_queue = queue.Queue()
    starts = []
    warnings = []

    def start_fallback(*, preserve_provider_exclusions=False):
        starts.append(preserve_provider_exclusions)
        mgr._tts_active_provider_key = "qwen"

    mgr._start_tts_thread = start_fallback
    monkeypatch.setattr(
        tts_runtime_module.logger,
        "warning",
        lambda message, *args, **_kwargs: warnings.append(message % args),
    )

    assert LLMSessionManager._activate_configured_tts_fallback(mgr, "测试") is True
    assert mgr._tts_excluded_provider_keys == frozenset({"custom"})
    assert mgr.tts_request_queue.get_nowait() == ("__shutdown__", None)
    assert starts == [True]
    assert any("fallback_provider=qwen" in message for message in warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_message", "expected_stage"),
    [
        (("__ready__", False), "初始化"),
        (("__error__", "upstream connection failed"), "运行时"),
    ],
)
async def test_tts_handler_follows_fallback_worker_queue(failure_message, expected_stage):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    old_queue = queue.Queue()
    new_queue = queue.Queue()
    old_queue.put(failure_message)
    new_queue.put(("__ready__", True))
    mgr.tts_response_queue = old_queue
    mgr.tts_cache_lock = asyncio.Lock()
    mgr.tts_ready = False
    mgr._last_tts_error_code = ""
    mgr._tts_retry_notify_count = 0
    stages = []
    ready_seen = asyncio.Event()

    def activate(stage):
        stages.append(stage)
        mgr.tts_response_queue = new_queue
        return True

    async def flush_pending():
        ready_seen.set()

    mgr._activate_configured_tts_fallback = activate
    mgr._flush_tts_pending_chunks = flush_pending

    task = asyncio.create_task(LLMSessionManager.tts_response_handler(mgr))
    await asyncio.wait_for(ready_seen.wait(), timeout=1)
    task.cancel()
    result = await asyncio.gather(task, return_exceptions=True)

    assert isinstance(result[0], asyncio.CancelledError)
    assert stages == [expected_stage]
    assert mgr.tts_ready is True


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
