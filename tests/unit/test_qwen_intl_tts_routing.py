"""阿里国际版默认 TTS 路由回归测试。"""

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
    """qwen_intl 默认 TTS 一律走 qwen_realtime_tts_worker。

    历史曾经基于 "OPENROUTER_URL 解析到 dashscope-us" 推断 key 仅 US 可用并
    fallback 到 dummy worker，但 _test_connectivity_candidates 是 latency race
    挑首响，dual-region intl key 经常被误判到 US 然后被错误静音
    (Codex P1 #3258641929)。同时若 key 真的只在 US 区域可用，coreApi=qwen_intl
    的对话 realtime WS 也会走 dashscope-intl 端点同样失败 —— TTS 静音只是
    掩盖更大的配置问题。
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
    """新的 realtime TTS 模型名不再以 qwen3-tts 开头，配置值应原样进 URL。"""
    _patch_qwen_core_config(monkeypatch, {
        "CORE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "TTS_MODEL": "qwen3-tts-flash-realtime-2026-09-01",
        "REALTIME_MODEL": "qwen3.8-omni-flash-realtime",
    })

    url = _resolve_qwen_realtime_tts_url()

    assert url.endswith("?model=qwen3-tts-flash-realtime-2026-09-01")


def test_qwen_realtime_tts_url_ignores_realtime_model_alias(monkeypatch):
    """TTS_MODEL 默认等于 REALTIME_MODEL，那是全模态模型名，不能拿去连 TTS。"""
    _patch_qwen_core_config(monkeypatch, {
        "CORE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "TTS_MODEL": "qwen3.8-omni-flash-realtime",
        "REALTIME_MODEL": "qwen3.8-omni-flash-realtime",
    })

    url = _resolve_qwen_realtime_tts_url()

    assert url.endswith(f"?model={_QWEN_REALTIME_TTS_MODEL}")


def test_qwen_realtime_tts_url_falls_back_when_tts_model_blank(monkeypatch):
    _patch_qwen_core_config(monkeypatch, {
        "CORE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "TTS_MODEL": "  ",
        "REALTIME_MODEL": "qwen3.8-omni-flash-realtime",
    })

    url = _resolve_qwen_realtime_tts_url()

    assert url.endswith(f"?model={_QWEN_REALTIME_TTS_MODEL}")
