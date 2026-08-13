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
from main_logic.tts_client import _infra as tts_infra_module
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
    openai_tts_sdk_options,
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


def test_openai_tts_endpoint_preserves_trailing_slash_in_query_value():
    configured = "https://speech.example.com/v1?token=abc/"
    assert openai_tts_base_url(configured) == configured
    assert openai_tts_speech_url(configured) == (
        "https://speech.example.com/v1/audio/speech?token=abc/"
    )


def test_openai_tts_sdk_options_separate_query_from_base_url():
    assert openai_tts_sdk_options(
        "https://speech.example.com/v1?tenant=demo&token=x%2By"
    ) == (
        "https://speech.example.com/v1",
        "tenant=demo&token=x%2By",
    )


def test_openai_tts_sdk_options_preserve_repeated_query_parameters():
    assert openai_tts_sdk_options(
        "https://speech.example.com/v1?scope=a&scope=b&token=abc/"
    ) == (
        "https://speech.example.com/v1",
        "scope=a&scope=b&token=abc/",
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
        self.voices = {}
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
        return dict(self.voices)

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
        "safe_error_provider": "custom",
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
async def test_configured_custom_voice_hides_same_id_clone_from_character_catalog(
    monkeypatch,
):
    cm = _CustomTtsConfigManager()
    cm.voices["vendor-voice"] = {
        "voice_id": "vendor-voice",
        "provider": "cosyvoice",
        "source": "clone",
    }
    monkeypatch.setattr(voice_preview_module, "get_config_manager", lambda: cm)

    result = await voice_preview_module.get_voices()

    assert "vendor-voice" not in result["voices"]
    assert result["native_voices"]["vendor-voice"]["provider"] == "custom"
    assert voice_preview_module._is_unpreviewable_selected_preset_voice(
        cm,
        cm.snapshot,
        "vendor-voice",
        cm.voices["vendor-voice"],
    ) is True


@pytest.mark.asyncio
async def test_configured_voice_keeps_case_distinct_clone_in_character_catalog(
    monkeypatch,
):
    cm = _CustomTtsConfigManager()
    cm.voices["Vendor-Voice"] = {
        "voice_id": "Vendor-Voice",
        "provider": "cosyvoice",
        "source": "clone",
    }
    monkeypatch.setattr(voice_preview_module, "get_config_manager", lambda: cm)

    result = await voice_preview_module.get_voices()

    assert result["voices"]["Vendor-Voice"]["provider"] == "cosyvoice"
    assert result["native_voices"]["vendor-voice"]["provider"] == "custom"


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


def test_exact_configured_vllm_voice_wins_over_same_id_clone(monkeypatch):
    cm = _CustomTtsConfigManager()
    cm.raw.update(
        {
            "ttsModelProvider": "vllm_omni",
            "ttsModelUrl": "wss://speech.example.com/v1",
            "ttsModelId": "vendor-tts",
            "ttsVoiceId": "default",
        }
    )
    cm.snapshot.update(cm.raw)
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)
    monkeypatch.setattr(
        tts_client,
        "_get_voice_meta",
        lambda _voice_id: {
            "provider": "vllm_omni",
            "source": "clone",
            "clone_sample_b64": "QUJDRA==",
            "clone_sample_mime": "audio/wav",
        },
    )

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=True,
        voice_id="default",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.vllm_omni_tts_worker
    assert worker.keywords == {
        "base_url": "wss://speech.example.com/v1",
        "model": "vendor-tts",
        "voice": "default",
    }
    assert api_key == "sk-custom"
    assert provider_key == "vllm_omni"


def test_vllm_resolve_keeps_selection_snapshot_when_disk_changes(monkeypatch):
    cm = _CustomTtsConfigManager()
    cm.snapshot.update(
        {
            "ttsModelProvider": "vllm_omni",
            "ttsModelUrl": "wss://snapshot.example.com/v1",
            "ttsModelId": "snapshot-model",
            "ttsVoiceId": "snapshot-voice",
        }
    )
    cm.raw.update(
        {
            "ttsModelProvider": "vllm_omni",
            "ttsModelUrl": "wss://new-disk.example.com/v1",
            "ttsModelId": "new-disk-model",
            "ttsModelApiKey": "new-disk-key",
            "ttsVoiceId": "new-disk-voice",
        }
    )
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)
    monkeypatch.setattr(
        tts_client,
        "_get_voice_meta",
        lambda _voice_id: {
            "provider": "vllm_omni",
            "source": "clone",
            "clone_sample_b64": "QUJDRA==",
            "clone_sample_mime": "audio/wav",
        },
    )

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=True,
        voice_id="snapshot-voice",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.vllm_omni_tts_worker
    assert worker.keywords == {
        "base_url": "wss://snapshot.example.com/v1",
        "model": "snapshot-model",
        "voice": "snapshot-voice",
    }
    # Sensitive credentials are intentionally resolved from raw storage only.
    # 敏感 API Key 不进会话快照，仍只从原始配置解析。
    assert api_key == "new-disk-key"
    assert provider_key == "vllm_omni"


def test_vllm_resolve_does_not_send_new_provider_key_to_stale_snapshot(monkeypatch):
    cm = _CustomTtsConfigManager()
    cm.snapshot.update(
        {
            "ttsModelProvider": "vllm_omni",
            "ttsModelUrl": "wss://snapshot.example.com/v1",
            "ttsModelId": "snapshot-model",
            "ttsVoiceId": "snapshot-voice",
        }
    )
    cm.raw.update(
        {
            "ttsModelProvider": "custom",
            "ttsModelUrl": "https://new-provider.example.com/v1",
            "ttsModelApiKey": "new-provider-secret",
        }
    )
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="snapshot-voice",
    )

    assert worker is tts_infra_module.configured_tts_unavailable_worker
    assert api_key == ""
    assert provider_key == "vllm_omni"


def test_vllm_selection_respects_explicit_empty_snapshot_provider():
    cm = _CustomTtsConfigManager()
    cm.raw["ttsModelProvider"] = "vllm_omni"
    ctx = provider_registry.DispatchContext(
        core_config={"ENABLE_CUSTOM_API": True, "ttsModelProvider": ""},
        cm=cm,
    )

    assert vllm_worker_module._vllm_omni_is_selected(ctx) is False
    assert cm.load_count == 0


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


def test_custom_config_read_failure_stays_owned_until_supervised_fallback(monkeypatch):
    cm = _CustomTtsConfigManager()
    errors = []
    secrets = ("sk-secret-should-not-log", "token=signed-query", "角色原文不能记录")

    def broken_load(*_args, **_kwargs):
        raise OSError(" ".join(secrets))

    cm.load_json_config = broken_load
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)
    monkeypatch.setattr(
        tts_infra_module.logger,
        "error",
        lambda message, *args, **_kwargs: errors.append(message % args),
    )

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=True,
        voice_id="vendor-voice",
    )

    request_queue = queue.Queue()
    response_queue = queue.Queue()
    worker(request_queue, response_queue, api_key, "vendor-voice")

    # Keep ownership until core strips the failed preset identity and credentials.
    # 读取失败不能直接误入 CosyVoice；先保留 custom 身份，再由监管层安全回退。
    assert provider_key == "custom"
    assert api_key == ""
    assert response_queue.get_nowait() == ("__ready__", False)
    assert errors == [
        "code=TTS_CONFIGURED_API_FAILURE provider=custom stage=configuration"
    ]
    assert all(secret not in "\n".join(errors) for secret in secrets)


def test_failed_configured_preset_redispatch_uses_default_voice_route(monkeypatch):
    cm = _CustomTtsConfigManager()
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)

    _worker, _api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="",
        excluded_provider_keys={"custom"},
    )

    assert provider_key == "qwen"


def test_supervised_fallback_uses_default_voice_and_default_credentials(monkeypatch):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    config_slots = []
    dispatch_calls = []
    thread_args = []

    class _FakeThread:
        def __init__(self, *, target, args, daemon):
            _ = target, daemon
            thread_args.append(args)

        def start(self):
            return None

    def get_model_api_config(slot):
        config_slots.append(slot)
        return {"api_key": "sk-default" if slot == "tts_default" else "sk-custom"}

    def get_worker(**kwargs):
        dispatch_calls.append(kwargs)
        return (lambda *_args: None), None, "qwen"

    mgr._config_manager = SimpleNamespace(
        get_core_config=lambda: {},
        get_model_api_config=get_model_api_config,
    )
    mgr.core_api_type = "qwen"
    mgr.voice_id = "vendor-voice"
    mgr._is_free_preset_voice = False
    mgr._tts_fallback_uses_default_voice = True
    mgr._tts_excluded_provider_keys = frozenset({"custom"})
    mgr._build_tts_runtime_key = lambda: ("fallback",)
    monkeypatch.setattr(tts_runtime_module, "Thread", _FakeThread)
    monkeypatch.setattr(tts_runtime_module._core_facade, "get_tts_worker", get_worker)

    LLMSessionManager._start_tts_thread(mgr, preserve_provider_exclusions=True)

    # The replacement sees neither the failed Voice ID nor the custom credential.
    # 替代 worker 只能收到默认路由的空音色和默认凭证，不能复用失败配置。
    assert dispatch_calls[0]["voice_id"] == ""
    assert dispatch_calls[0]["has_custom_voice"] is False
    assert config_slots == ["tts_default"]
    assert thread_args[0][2:] == ("sk-default", "")


def test_vllm_config_read_failure_stays_owned_until_supervised_fallback(monkeypatch):
    cm = _CustomTtsConfigManager()
    cm.raw["ttsModelProvider"] = "vllm_omni"
    cm.snapshot.update(cm.raw)
    errors = []
    secrets = ("sk-secret-should-not-log", "token=signed-query", "角色原文不能记录")

    def broken_load(*_args, **_kwargs):
        raise OSError(" ".join(secrets))

    cm.load_json_config = broken_load
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)
    monkeypatch.setattr(
        tts_infra_module.logger,
        "error",
        lambda message, *args, **_kwargs: errors.append(message % args),
    )

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="default",
    )

    request_queue = queue.Queue()
    response_queue = queue.Queue()
    worker(request_queue, response_queue, api_key, "default")

    assert provider_key == "vllm_omni"
    assert api_key == ""
    assert response_queue.get_nowait() == ("__ready__", False)
    assert errors == [
        "code=TTS_CONFIGURED_API_FAILURE provider=vllm_omni stage=configuration"
    ]
    assert all(secret not in "\n".join(errors) for secret in secrets)


def test_vllm_connection_failure_logs_only_safe_provider_stage(monkeypatch):
    logs = []
    secret_key = "sk-secret-should-not-log"
    secret_token = "signed-query-should-not-log"
    raw_text = "角色原文不能记录"

    async def broken_connect(*_args, **_kwargs):
        raise RuntimeError(f"{secret_key} {secret_token} {raw_text}")

    def capture(message, *args, **_kwargs):
        logs.append(message % args)

    monkeypatch.setattr(vllm_worker_module.websockets, "connect", broken_connect)
    monkeypatch.setattr(vllm_worker_module.logger, "error", capture)
    monkeypatch.setattr(vllm_worker_module.logger, "warning", capture)
    monkeypatch.setattr(vllm_worker_module.logger, "info", capture)
    monkeypatch.setattr(tts_infra_module.logger, "error", capture)

    response_queue = queue.Queue()
    vllm_worker_module.vllm_omni_tts_worker(
        queue.Queue(),
        response_queue,
        secret_key,
        "default",
        base_url=f"wss://speech.example.test/v1?token={secret_token}",
    )
    queued = []
    while not response_queue.empty():
        queued.append(response_queue.get_nowait())
    serialized = "\n".join(logs + [repr(item) for item in queued])

    assert "code=TTS_CONFIGURED_API_FAILURE provider=vllm_omni stage=connect" in logs
    assert ("__ready__", False) in queued
    assert secret_key not in serialized
    assert secret_token not in serialized
    assert raw_text not in serialized


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


def test_configured_provider_predicate_failure_redacts_exception(monkeypatch):
    ctx = provider_registry.DispatchContext(core_config={}, cm=SimpleNamespace())
    errors = []
    secrets = ("sk-secret-should-not-log", "token=signed-query", "角色原文不能记录")
    fallback_result = (object(), None, "fallback")
    broken = SimpleNamespace(
        key="custom",
        fallback_on_failure=True,
        is_selected=lambda _ctx: (_ for _ in ()).throw(
            RuntimeError(" ".join(secrets))
        ),
    )
    healthy = SimpleNamespace(
        key="healthy",
        fallback_on_failure=False,
        is_selected=lambda _ctx: True,
        resolve=lambda _ctx: fallback_result,
    )
    monkeypatch.setattr(provider_registry, "all_providers", lambda: [broken, healthy])
    monkeypatch.setattr(
        provider_registry.logger,
        "error",
        lambda message, *args, **_kwargs: errors.append(message % args),
    )

    assert provider_registry.resolve_selected(ctx) == fallback_result
    assert errors == [
        "code=TTS_CONFIGURED_API_FAILURE provider=custom stage=selection"
    ]
    assert all(secret not in "\n".join(errors) for secret in secrets)


def test_configured_preset_ownership_failure_is_redacted_and_non_fatal(monkeypatch):
    errors = []
    secrets = ("sk-secret-should-not-log", "token=signed-query", "角色原文不能记录")
    monkeypatch.setattr(
        provider_registry,
        "selected_preset_provider_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(" ".join(secrets))
        ),
    )
    monkeypatch.setattr(
        tts_infra_module.logger,
        "error",
        lambda message, *args, **_kwargs: errors.append(message % args),
    )

    assert (
        tts_client.selected_configured_tts_preset_provider_key(
            {}, SimpleNamespace(), "configured-voice"
        )
        is None
    )
    assert errors == [
        "code=TTS_CONFIGURED_API_FAILURE provider=configured stage=ownership"
    ]
    assert all(secret not in "\n".join(errors) for secret in secrets)


def test_configured_sentence_worker_redacts_exception_and_input(monkeypatch):
    errors = []
    secrets = ("sk-secret-should-not-log", "token=signed-query", "角色原文不能记录")
    request_queue = queue.Queue()
    response_queue = queue.Queue()

    async def setup(_queue_proxy):
        async def synthesize(_text, _speech_id):
            raise RuntimeError(" ".join(secrets))

        return synthesize, None

    monkeypatch.setattr(
        tts_infra_module.logger,
        "error",
        lambda message, *args, **_kwargs: errors.append(message % args),
    )
    thread = threading.Thread(
        target=tts_infra_module._run_sentence_tts_worker,
        args=(request_queue, response_queue, setup),
        kwargs={"label": "Configured TTS", "safe_error_provider": "custom"},
        daemon=True,
    )
    thread.start()
    assert _wait_for_item(response_queue, lambda item: item == ("__ready__", True))
    request_queue.put(("speech-1", "角色原文不能记录。"))
    error = _wait_for_item(
        response_queue,
        lambda item: isinstance(item, tuple) and item[0] == "__error__",
    )
    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert "TTS_CONFIGURED_API_FAILURE" in error[1]
    assert all(secret not in error[1] for secret in secrets)
    assert all(secret not in "\n".join(errors) for secret in secrets)


def test_configured_tts_failure_switches_to_existing_dispatch_order(monkeypatch):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr._tts_active_provider_key = "custom"
    mgr._tts_excluded_provider_keys = frozenset()
    mgr.tts_request_queue = queue.Queue()
    mgr.current_speech_id = "speech-1"
    mgr.tts_pending_chunks = [("speech-1", "later")]
    mgr._tts_replay_speech_id = "speech-1"
    mgr._tts_replay_chunks = [
        ("old-speech", "stale"),
        ("speech-1", "first"),
        ("speech-1", "second"),
    ]
    mgr._tts_replay_done = True
    mgr._tts_replay_audio_emitted = False
    mgr._tts_done_queued_for_turn = True
    mgr._tts_done_pending_until_ready = False
    mgr._tts_fallback_uses_default_voice = False
    mgr._last_tts_error_code = "API_KEY_REJECTED"
    mgr._tts_retry_notify_count = 2
    mgr._reset_tts_stream_normalizer = lambda: None
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
    assert mgr._tts_fallback_uses_default_voice is True
    assert mgr.tts_pending_chunks == [
        ("speech-1", "first"),
        ("speech-1", "second"),
        ("speech-1", "later"),
    ]
    assert mgr._tts_done_queued_for_turn is False
    assert mgr._tts_done_pending_until_ready is True
    assert mgr._last_tts_error_code == ""
    assert mgr._tts_retry_notify_count == 0
    assert LLMSessionManager._effective_tts_route(mgr) == ("", False)
    assert any("fallback_provider=qwen" in message for message in warnings)


def test_fallback_skips_complete_replay_after_audio_reached_playback(monkeypatch):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr._tts_active_provider_key = "custom"
    mgr._tts_excluded_provider_keys = frozenset()
    mgr.tts_request_queue = queue.Queue()
    mgr.current_speech_id = "speech-1"
    mgr.tts_pending_chunks = []
    mgr._tts_replay_speech_id = "speech-1"
    mgr._tts_replay_chunks = [("speech-1", "already heard")]
    mgr._tts_replay_done = True
    mgr._tts_replay_audio_emitted = True
    mgr._tts_replay_progress_supported = False
    mgr._tts_done_queued_for_turn = True
    mgr._tts_done_pending_until_ready = False
    mgr._tts_fallback_uses_default_voice = False
    mgr._reset_tts_stream_normalizer = lambda: None

    def start_fallback(*, preserve_provider_exclusions=False):
        assert preserve_provider_exclusions is True
        mgr._tts_active_provider_key = "qwen"

    mgr._start_tts_thread = start_fallback
    monkeypatch.setattr(tts_runtime_module.logger, "warning", lambda *_args, **_kwargs: None)

    assert LLMSessionManager._activate_configured_tts_fallback(mgr, "运行时") is True
    assert mgr.tts_pending_chunks == []
    assert mgr._tts_done_pending_until_ready is False


def test_http_fallback_replays_only_unconfirmed_sentence_suffix(monkeypatch):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr._tts_active_provider_key = "custom"
    mgr._tts_excluded_provider_keys = frozenset()
    mgr.tts_request_queue = queue.Queue()
    mgr.current_speech_id = "speech-1"
    mgr.tts_pending_chunks = []
    mgr._tts_replay_speech_id = "speech-1"
    mgr._tts_replay_chunks = [("speech-1", "heard.unheard suffix.")]
    mgr._tts_replay_sent_chunks = [("speech-1", "unheard suffix.")]
    mgr._tts_replay_done = True
    mgr._tts_replay_audio_emitted = True
    mgr._tts_replay_sentence_audio_emitted = False
    mgr._tts_replay_progress_supported = True
    mgr._tts_done_queued_for_turn = True
    mgr._tts_done_pending_until_ready = False
    mgr._tts_fallback_uses_default_voice = False
    mgr._last_tts_error_code = "TTS_CONNECTION_FAILED"
    mgr._tts_retry_notify_count = 2
    mgr._reset_tts_stream_normalizer = lambda: None

    def start_fallback(*, preserve_provider_exclusions=False):
        assert preserve_provider_exclusions is True
        mgr._tts_active_provider_key = "qwen"

    mgr._start_tts_thread = start_fallback
    monkeypatch.setattr(tts_runtime_module.logger, "warning", lambda *_args, **_kwargs: None)

    assert LLMSessionManager._activate_configured_tts_fallback(mgr, "运行时") is True
    assert mgr.tts_pending_chunks == [("speech-1", "unheard suffix.")]
    assert mgr._tts_done_pending_until_ready is True
    assert mgr._last_tts_error_code == ""
    assert mgr._tts_retry_notify_count == 0


def test_sentence_progress_prunes_only_the_confirmed_prefix():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr._tts_replay_speech_id = "speech-1"
    mgr._tts_replay_sent_chunks = [
        ("speech-1", "first."),
        ("speech-1", "second."),
        ("speech-1", "third."),
    ]

    LLMSessionManager._consume_tts_replay_sentence(mgr, "speech-1", "first.")
    LLMSessionManager._consume_tts_replay_sentence(mgr, "speech-1", "second.")

    assert mgr._tts_replay_sent_chunks == [("speech-1", "third.")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "audio_message",
    [b"audio-prefix", ("__audio__", "speech-1", b"audio-prefix")],
)
async def test_tts_handler_marks_audio_before_runtime_fallback(audio_message):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    old_queue = queue.Queue()
    new_queue = queue.Queue()
    old_queue.put(audio_message)
    old_queue.put(("__error__", "late upstream failure"))
    new_queue.put(("__ready__", True))
    mgr.tts_response_queue = old_queue
    mgr.tts_cache_lock = asyncio.Lock()
    mgr.tts_ready = True
    mgr._tts_replay_audio_emitted = False
    mgr._last_tts_error_code = ""
    mgr._tts_retry_notify_count = 0
    observed = []
    ready_seen = asyncio.Event()

    async def send_speech(*_args, **_kwargs):
        return True

    def activate(_stage):
        observed.append(mgr._tts_replay_audio_emitted)
        mgr.tts_response_queue = new_queue
        return True

    async def flush_pending():
        ready_seen.set()

    mgr.send_speech = send_speech
    mgr._confirm_pending_ai_voice_echo = lambda *_args: None
    mgr._discard_pending_ai_voice_echo = lambda: None
    mgr._activate_configured_tts_fallback = activate
    mgr._flush_tts_pending_chunks = flush_pending

    task = asyncio.create_task(LLMSessionManager.tts_response_handler(mgr))
    await asyncio.wait_for(ready_seen.wait(), timeout=1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert observed == [True]


@pytest.mark.asyncio
async def test_tts_handler_deduplicates_api_error_notice_per_speech():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    response_queue = queue.Queue()
    api_key_error = (
        "OpenAI TTS synthesis failed: Error code: 401 - "
        "invalid_request_error.invalid_api_key"
    )
    for speech_id in ("speech-1", "speech-1", "speech-2", "speech-2"):
        response_queue.put(("__tts_sentence_failed__", speech_id, "failed sentence"))
        response_queue.put(("__error__", api_key_error))

    mgr.tts_response_queue = response_queue
    mgr.tts_cache_lock = asyncio.Lock()
    mgr.tts_ready = True
    mgr.current_speech_id = "speech-2"
    mgr._tts_replay_speech_id = None
    mgr._tts_replay_sentence_audio_emitted = False
    mgr._last_tts_error_code = ""
    mgr._tts_retry_notify_count = 0
    mgr._activate_configured_tts_fallback = lambda _stage: False

    sent_statuses = []
    status_tasks = []

    async def send_status(message):
        sent_statuses.append(json.loads(message))

    def fire_task(coro):
        task = asyncio.create_task(coro)
        status_tasks.append(task)
        return task

    mgr.send_status = send_status
    mgr._fire_task = fire_task

    handler_task = asyncio.create_task(LLMSessionManager.tts_response_handler(mgr))
    deadline = asyncio.get_running_loop().time() + 1
    while not response_queue.empty() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    handler_task.cancel()
    await asyncio.gather(handler_task, return_exceptions=True)
    if status_tasks:
        await asyncio.gather(*status_tasks)

    assert [status["code"] for status in sent_statuses] == [
        "API_KEY_REJECTED",
        "API_KEY_REJECTED",
    ]


@pytest.mark.asyncio
async def test_tts_error_notice_dedup_survives_handler_restart_until_audio_done():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    api_key_error = (
        "OpenAI TTS synthesis failed: Error code: 401 - "
        "invalid_request_error.invalid_api_key"
    )
    mgr.tts_cache_lock = asyncio.Lock()
    mgr.tts_ready = True
    mgr.current_speech_id = "speech-1"
    mgr._tts_replay_speech_id = None
    mgr._tts_replay_sentence_audio_emitted = False
    mgr._last_tts_error_code = ""
    mgr._tts_retry_notify_count = 0
    mgr._tts_notified_error_keys = set()
    mgr._activate_configured_tts_fallback = lambda _stage: False

    sent_statuses = []
    status_tasks = []

    async def send_status(message):
        sent_statuses.append(json.loads(message))

    async def send_audio_done(_speech_id):
        return True

    def fire_task(coro):
        task = asyncio.create_task(coro)
        status_tasks.append(task)
        return task

    async def run_handler(*items):
        response_queue = queue.Queue()
        for item in items:
            response_queue.put(item)
        mgr.tts_response_queue = response_queue
        handler_task = asyncio.create_task(LLMSessionManager.tts_response_handler(mgr))
        deadline = asyncio.get_running_loop().time() + 1
        while not response_queue.empty() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        handler_task.cancel()
        await asyncio.gather(handler_task, return_exceptions=True)
        if status_tasks:
            await asyncio.gather(*status_tasks)

    mgr.send_status = send_status
    mgr.send_audio_done = send_audio_done
    mgr._fire_task = fire_task

    failed_sentence = ("__tts_sentence_failed__", "speech-1", "failed sentence")
    await run_handler(failed_sentence, ("__error__", api_key_error))
    await run_handler(
        failed_sentence,
        ("__error__", api_key_error),
        ("__audio_done__", "speech-1"),
    )
    await run_handler(failed_sentence, ("__error__", api_key_error))

    assert [status["code"] for status in sent_statuses] == [
        "API_KEY_REJECTED",
        "API_KEY_REJECTED",
    ]


def test_configured_tts_failure_replays_ledger_after_current_speech_id_rotates(
    monkeypatch,
):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.core_api_type = "qwen"
    mgr.voice_id = "vendor-voice"
    mgr.current_speech_id = "next-speech"
    mgr._tts_active_provider_key = "custom"
    mgr._tts_excluded_provider_keys = frozenset()
    mgr.tts_request_queue = queue.Queue()
    mgr.tts_pending_chunks = []
    mgr._tts_replay_speech_id = "completed-speech"
    mgr._tts_replay_chunks = [("completed-speech", "complete reply")]
    mgr._tts_replay_done = True
    mgr._tts_done_queued_for_turn = True
    mgr._tts_done_pending_until_ready = False
    mgr._tts_fallback_uses_default_voice = False
    mgr._reset_tts_stream_normalizer = lambda: None
    mgr._start_tts_thread = lambda **_kwargs: None
    monkeypatch.setattr(
        tts_runtime_module._core_facade,
        "tts_provider_falls_back_on_failure",
        lambda provider: provider == "custom",
    )
    monkeypatch.setattr(
        tts_runtime_module._core_facade,
        "tts_provider_uses_configured_preset_voice",
        lambda provider: provider == "custom",
    )

    assert LLMSessionManager._activate_configured_tts_fallback(mgr, "运行时") is True

    assert mgr.tts_pending_chunks == [("completed-speech", "complete reply")]
    assert mgr._tts_done_pending_until_ready is True


@pytest.mark.asyncio
async def test_replayed_utterance_and_done_flush_to_replacement_worker():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.tts_cache_lock = asyncio.Lock()
    mgr.tts_pending_chunks = [
        ("speech-1", "first"),
        ("speech-1", "second"),
        ("speech-1", "later"),
    ]
    mgr._tts_done_pending_until_ready = True
    mgr.tts_thread = SimpleNamespace(is_alive=lambda: True)
    mgr.tts_request_queue = queue.Queue()
    mgr._enqueue_tts_text_chunk = lambda sid, text: mgr.tts_request_queue.put((sid, text))

    def request_done():
        mgr.tts_request_queue.put((None, None))
        mgr._tts_done_pending_until_ready = False
        return "queued"

    mgr._request_tts_done_locked = request_done

    await LLMSessionManager._flush_tts_pending_chunks(mgr)

    assert [mgr.tts_request_queue.get_nowait() for _ in range(4)] == [
        ("speech-1", "first"),
        ("speech-1", "second"),
        ("speech-1", "later"),
        (None, None),
    ]


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

    class _FakeDefaultAsyncHttpxClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    monkeypatch.setattr(
        openai,
        "DefaultAsyncHttpxClient",
        _FakeDefaultAsyncHttpxClient,
    )
    return clients


def _run_worker_once(
    monkeypatch,
    chunks,
    *,
    base_url=None,
    model=None,
    voice_id="character-voice",
    audio_api_key="sk-test",
):
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
        args=(request_queue, response_queue, audio_api_key, voice_id),
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


def test_sentence_worker_reports_audible_boundaries_before_runtime_error():
    request_queue = queue.Queue()
    response_queue = queue.Queue()

    async def setup(queue_proxy):
        async def synthesize(text, _speech_id):
            if text == "First.":
                queue_proxy.put(b"first-audio")
                return
            queue_proxy.put(b"partial-second-audio")
            raise RuntimeError("late failure")

        return synthesize, None

    thread = threading.Thread(
        target=tts_client._run_sentence_tts_worker,
        args=(request_queue, response_queue, setup),
        kwargs={"label": "Mock TTS"},
        daemon=True,
    )
    thread.start()
    assert _wait_for_item(response_queue, lambda item: item == ("__ready__", True)) == (
        "__ready__",
        True,
    )
    request_queue.put(("speech-1", "First.Second."))
    request_queue.put((None, None))

    delivered = []
    while not delivered or delivered[-1][0] != "__error__":
        item = response_queue.get(timeout=5)
        delivered.append(item)

    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert delivered[:5] == [
        b"first-audio",
        ("__tts_sentence_done__", "speech-1", "First."),
        b"partial-second-audio",
        ("__tts_sentence_failed__", "speech-1", "Second."),
        ("__error__", "Mock TTS 合成失败: late failure"),
    ]


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


@pytest.mark.parametrize("audio_api_key", ["", "  \t "])
def test_custom_worker_omits_authorization_for_blank_api_key(
    monkeypatch,
    audio_api_key,
):
    from openai import omit as openai_omit

    clients, request_queue, response_queue, thread = _run_worker_once(
        monkeypatch,
        [b"\x00\x00"],
        base_url="http://127.0.0.1:8000/v1",
        audio_api_key=audio_api_key,
    )
    _wait_for_item(response_queue, lambda item: isinstance(item, bytes))
    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert clients[0].client_kwargs == {
        "api_key": "",
        "base_url": "http://127.0.0.1:8000/v1",
    }
    assert clients[0].create_calls[0]["extra_headers"] == {
        "Authorization": openai_omit,
    }


def test_custom_worker_installs_endpoint_query_request_hook(monkeypatch):
    clients, request_queue, response_queue, thread = _run_worker_once(
        monkeypatch,
        [b"\x00\x00"],
        base_url="https://speech.example.com/v1?tenant=demo&token=x%2By",
    )
    _wait_for_item(response_queue, lambda item: isinstance(item, bytes))
    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert clients[0].client_kwargs["api_key"] == "sk-test"
    assert clients[0].client_kwargs["base_url"] == "https://speech.example.com/v1"
    query_client = clients[0].client_kwargs["http_client"]
    assert len(query_client.client_kwargs["event_hooks"]["request"]) == 1


def test_custom_worker_preserves_repeated_endpoint_query_values(monkeypatch):
    clients, request_queue, response_queue, thread = _run_worker_once(
        monkeypatch,
        [b"\x00\x00"],
        base_url="https://speech.example.com/v1?scope=a&scope=b&token=abc/",
    )
    _wait_for_item(response_queue, lambda item: isinstance(item, bytes))
    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=5)

    assert not thread.is_alive()
    query_client = clients[0].client_kwargs["http_client"]
    assert len(query_client.client_kwargs["event_hooks"]["request"]) == 1


@pytest.mark.asyncio
async def test_openai_sdk_request_omits_authorization_for_auth_free_endpoint():
    from openai import AsyncOpenAI, omit as openai_omit

    requests = []

    async def handle_request(request):
        requests.append(request)
        return httpx.Response(
            200,
            content=b"\x00\x00",
            headers={"content-type": "application/octet-stream"},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
    client = AsyncOpenAI(
        **openai_worker_module._openai_auth_client_kwargs(AsyncOpenAI, ""),
        base_url="http://127.0.0.1:8000/v1",
        http_client=http_client,
    )
    try:
        async with client.audio.speech.with_streaming_response.create(
            model="tts-model",
            voice="voice-a",
            input="hello",
            response_format="pcm",
            extra_headers={"Authorization": openai_omit},
        ) as response:
            async for _chunk in response.iter_bytes():
                pass
    finally:
        await client.close()

    assert len(requests) == 1
    assert "Authorization" not in requests[0].headers


@pytest.mark.asyncio
async def test_openai_sdk_request_preserves_endpoint_query():
    from openai import AsyncOpenAI

    requests = []

    async def handle_request(request):
        requests.append(request)
        return httpx.Response(
            200,
            content=b"\x00\x00",
            headers={"content-type": "application/octet-stream"},
        )

    from openai import DefaultAsyncHttpxClient

    sdk_base_url, endpoint_query = openai_tts_sdk_options(
        "http://127.0.0.1:8000/v1?scope=a&scope=b&token=abc/"
    )
    http_client = openai_worker_module._openai_endpoint_query_http_client(
        partial(
            DefaultAsyncHttpxClient,
            transport=httpx.MockTransport(handle_request),
        ),
        endpoint_query,
    )
    client = AsyncOpenAI(
        api_key="sk-test",
        base_url=sdk_base_url,
        http_client=http_client,
    )
    try:
        async with client.audio.speech.with_streaming_response.create(
            model="tts-model",
            voice="voice-a",
            input="hello",
            response_format="pcm",
        ) as response:
            async for _chunk in response.iter_bytes():
                pass
    finally:
        await client.close()

    assert len(requests) == 1
    assert requests[0].url.path == "/v1/audio/speech"
    assert requests[0].url.params.multi_items() == [
        ("scope", "a"),
        ("scope", "b"),
        ("token", "abc/"),
    ]
    assert requests[0].url.query == b"scope=a&scope=b&token=abc/"


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
