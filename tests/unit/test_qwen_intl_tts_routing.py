"""阿里国际版默认 TTS 路由回归测试。"""

from main_logic import tts_client


class _FakeConfigManager:
    def get_core_config(self):
        return {
            "CORE_API_TYPE": "qwen_intl",
            "OPENROUTER_URL": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
            "CORE_API_KEY": "sk-stale-core",
            "AUDIO_API_KEY": "sk-us-only",
            "ASSIST_API_KEY_QWEN_INTL": "sk-us-only",
            "DISABLE_TTS": False,
        }

    def get_model_api_config(self, model_type):
        return {
            "api_key": "sk-us-only",
            "base_url": "",
            "model": "",
        }

    def get_voices_for_current_api(self, for_listing=False):
        return {}


def test_qwen_intl_us_compatible_only_key_skips_realtime_tts(monkeypatch):
    """美国 compatible-mode TTS Key 不能尝试连接新加坡 realtime TTS。"""
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _FakeConfigManager())

    worker, api_key_override, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen_intl",
        has_custom_voice=False,
        voice_id="",
    )

    assert worker is tts_client.dummy_tts_worker
    assert api_key_override is None
    assert provider_key is None
