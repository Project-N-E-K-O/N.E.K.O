"""Alibaba international default TTS routing regression tests."""

import pytest

from main_logic import tts_client
from main_logic.tts_client.workers.qwen import (
    _QWEN_REALTIME_TTS_MODEL,
    _resolve_qwen_realtime_tts_url,
)


class _FakeConfigManager:
    def get_core_config(self):
        return {
            "CORE_API_TYPE": "qwen_intl",
            "OPENROUTER_URL": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
            "CORE_API_KEY": "sk-stale-core",
            "AUDIO_API_KEY": "sk-intl-dual-region",
            "ASSIST_API_KEY_QWEN_INTL": "sk-intl-dual-region",
            "DISABLE_TTS": False,
        }

    def get_model_api_config(self, model_type):
        return {
            "api_key": "sk-intl-dual-region",
            "base_url": "",
            "model": "",
        }

    def get_voices_for_current_api(self, for_listing=False):
        return {}


def test_qwen_intl_default_routes_to_realtime_tts(monkeypatch):
    """qwen_intl default TTS always uses qwen_realtime_tts_worker.

    Historically, resolving OPENROUTER_URL to dashscope-us was interpreted as a US-only key,
    falling back to a dummy worker. But _test_connectivity_candidates races latency
    and chooses the first response, often misclassifying dual-region international keys as US-only and muting them
    (Codex P1 #3258641929). For genuinely US-only keys, coreApi=qwen_intl
    conversation realtime WebSockets also use dashscope-intl and fail likewise: muting TTS merely
    hides the larger configuration problem.
    """
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _FakeConfigManager())

    worker, api_key_override, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen_intl",
        has_custom_voice=False,
        voice_id="",
    )

    assert worker is tts_client.qwen_realtime_tts_worker
    assert api_key_override is None
    assert provider_key == 'qwen'


def _patch_qwen_core_config(monkeypatch, core_config):
    class _CM:
        def get_core_config(self):
            return core_config

    monkeypatch.setattr(
        "main_logic.tts_client.workers.qwen.get_config_manager",
        lambda: _CM(),
    )


def test_qwen_realtime_tts_url_uses_configured_model_without_legacy_prefix(monkeypatch):
    """A Qwen TTS model outside the qwen3-tts prefix is passed through unchanged."""
    _patch_qwen_core_config(monkeypatch, {
        "CORE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "TTS_MODEL": "qwen3.5-tts-flash-realtime",
        "REALTIME_MODEL": "qwen3.8-omni-flash-realtime",
    })

    url = _resolve_qwen_realtime_tts_url()

    assert url.endswith("?model=qwen3.5-tts-flash-realtime")


@pytest.mark.parametrize("tts_model", [
    # Default: TTS_MODEL copies the omni realtime model name.
    "qwen3.8-omni-flash-realtime",
    # Fallback after another provider failed: its model id is still configured.
    "tts-1",
    "cosyvoice-v3-plus",
    "  ",
])
def test_qwen_realtime_tts_url_falls_back_for_non_qwen_tts_models(monkeypatch, tts_model):
    """Only Qwen TTS model ids may reach the DashScope realtime TTS endpoint."""
    _patch_qwen_core_config(monkeypatch, {
        "CORE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "TTS_MODEL": tts_model,
        "REALTIME_MODEL": "qwen3.8-omni-flash-realtime",
    })

    url = _resolve_qwen_realtime_tts_url()

    assert url.endswith(f"?model={_QWEN_REALTIME_TTS_MODEL}")
