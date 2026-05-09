import os
import sys


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_logic.core import LLMSessionManager


class _FakeConfigManager:
    def __init__(self, stored_voice_ids=()):
        self._stored_voice_ids = set(stored_voice_ids)

    def voice_id_exists_in_any_storage(self, voice_id):
        return voice_id in self._stored_voice_ids


def _make_mgr(voice_id, stored_voice_ids=()):
    mgr = object.__new__(LLMSessionManager)
    mgr.core_api_type = "gemini"
    mgr.voice_id = voice_id
    mgr._is_free_preset_voice = False
    mgr._config_manager = _FakeConfigManager(stored_voice_ids)
    return mgr


def test_gemini_alias_checks_canonical_voice_collision():
    mgr = _make_mgr("中文男", stored_voice_ids={"Puck"})

    assert LLMSessionManager._is_gemini_native_voice_id(mgr) is False


def test_gemini_alias_without_collision_uses_native_realtime_voice():
    mgr = _make_mgr("中文男")

    assert LLMSessionManager._is_gemini_native_voice_id(mgr) is True
    assert LLMSessionManager._resolve_realtime_voice(mgr, {}) == "Puck"


def test_voice_mode_gemini_native_uses_realtime_audio_not_external_tts():
    mgr = _make_mgr("Puck")

    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr,
            "audio",
            {"base_url": "https://generativelanguage.googleapis.com"},
            {"ENABLE_CUSTOM_API": True, "TTS_MODEL_URL": "http://localhost:9880"},
        )
        is False
    )


def test_custom_tts_config_requires_gptsovits_enabled():
    mgr = _make_mgr("")
    realtime_config = {"base_url": "https://generativelanguage.googleapis.com"}

    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr,
            "audio",
            realtime_config,
            {
                "ENABLE_CUSTOM_API": True,
                "TTS_MODEL_URL": "http://localhost:9880",
                "GPTSOVITS_ENABLED": False,
            },
        )
        is False
    )
    assert (
        LLMSessionManager._resolve_session_use_tts(
            mgr,
            "audio",
            realtime_config,
            {
                "ENABLE_CUSTOM_API": True,
                "TTS_MODEL_URL": "http://localhost:9880",
                "GPTSOVITS_ENABLED": True,
            },
        )
        is True
    )
