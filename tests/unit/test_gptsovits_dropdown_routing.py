"""Dispatch-selection regression tests for GPT-SoVITS moved to the ttsModelProvider dropdown.

During migration ``_gptsovits_is_selected`` honors two signals: the new
``ttsModelProvider == 'gptsovits'`` (same mechanism as vLLM-Omni) and the legacy
``GPTSOVITS_ENABLED`` switch path.
"""

from main_logic import tts_client


class _FakeConfigManager:
    def __init__(self, core_config, raw_json, *, is_custom=False):
        self._core_config = core_config
        self._raw_json = raw_json
        self._is_custom = is_custom

    def get_core_config(self):
        return self._core_config

    def load_json_config(self, name, default=None):
        if name == "core_config.json":
            return self._raw_json
        return default if default is not None else {}

    def get_model_api_config(self, model_type):
        return {"is_custom": self._is_custom, "base_url": "", "model": "", "api_key": ""}

    def get_voices_for_current_api(self, for_listing=False):
        return {}


def _base_core_config(**overrides):
    cfg = {
        "CORE_API_TYPE": "qwen",
        "DISABLE_TTS": False,
        "ENABLE_CUSTOM_API": False,
        "GPTSOVITS_ENABLED": False,
    }
    cfg.update(overrides)
    return cfg


def test_dropdown_provider_selects_gptsovits(monkeypatch):
    """ttsModelProvider=='gptsovits' (the new dropdown signal) selects GPT-SoVITS on its own, no legacy switch needed."""
    cm = _FakeConfigManager(
        core_config=_base_core_config(),
        raw_json={"ttsModelProvider": "gptsovits"},
    )
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)

    worker, api_key_override, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=False, voice_id="",
    )

    assert worker is tts_client.gptsovits_tts_worker
    assert api_key_override is None
    assert provider_key == "gptsovits"


def test_legacy_enabled_flag_still_selects_gptsovits(monkeypatch):
    """The legacy GPTSOVITS_ENABLED switch + tts_custom.is_custom path is still honored during migration."""
    cm = _FakeConfigManager(
        core_config=_base_core_config(GPTSOVITS_ENABLED=True),
        raw_json={},  # 无 ttsModelProvider，走 legacy 分支
        is_custom=True,
    )
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)

    worker, api_key_override, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=False, voice_id="",
    )

    assert worker is tts_client.gptsovits_tts_worker
    assert provider_key == "gptsovits"


def test_no_signal_does_not_select_gptsovits(monkeypatch):
    """With neither the dropdown signal nor the legacy switch, GPT-SoVITS must not be wrongly selected."""
    cm = _FakeConfigManager(
        core_config=_base_core_config(),
        raw_json={"ttsModelProvider": ""},
    )
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)

    _, _, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=False, voice_id="",
    )

    assert provider_key != "gptsovits"
