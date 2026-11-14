"""Explicitly chosen hosted TTS providers also speak for realtime voice sessions.

Before this change only DeepSeek took over a realtime session whose character
had no voice of its own; MiMo, Doubao and the custom OpenAI-compatible TTS
spoke in text mode but left voice mode to the realtime model's own voice.
"""
import dataclasses
from unittest.mock import Mock

import pytest

import main_logic.tts_client as tts_client
from main_logic.core import LLMSessionManager
from utils.config_manager.voice_storage import VoiceStorageMixin
from utils.tts import provider_registry


@pytest.fixture
def manager(monkeypatch):
    cm = Mock()
    state = {"config": {}}

    def set_config(config):
        state["config"] = config
        cm.get_core_config.return_value = config

    cm.set_config = set_config
    cm.load_json_config.side_effect = lambda _name, _default=None: dict(state["config"])
    cm.get_model_api_config.return_value = {}
    cm.get_voices_for_current_api.return_value = {}
    cm.voice_id_exists_in_any_storage.return_value = False
    cm.get_tts_api_key.side_effect = lambda provider: VoiceStorageMixin.get_tts_api_key(cm, provider)
    set_config({})
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)
    return cm


def _session(manager, voice_id=""):
    session = object.__new__(LLMSessionManager)
    session._config_manager = manager
    session.core_api_type = "qwen"
    session.voice_id = voice_id
    session._is_free_preset_voice = False
    return session


CUSTOM = {
    "ttsModelProvider": "custom", "ENABLE_CUSTOM_API": True,
    "ttsModelUrl": "http://127.0.0.1:8880/v1", "ttsModelId": "kokoro",
}


@pytest.mark.parametrize(("name", "config"), [
    ("mimo", {"ttsModelProvider": "mimo", "TTS_PROVIDER": "mimo", "ASSIST_API_KEY_MIMO": "mimo-key"}),
    ("doubao_tts", {
        "ttsModelProvider": "doubao_tts", "assistApiKeyDoubaoTts": "doubao-key",
        "ttsVoiceId": "zh_female_demo",
    }),
    # A local OpenAI-compatible endpoint needs no API key.
    ("custom", {**CUSTOM, "ttsVoiceId": "af_heart"}),
])
def test_explicit_choice_speaks_for_realtime_session_without_character_voice(manager, name, config):
    manager.set_config(config)
    assert tts_client.get_tts_worker("qwen", False, "")[2] == name
    assert tts_client.selected_realtime_output_tts_provider_key(config, manager, "") == name
    assert _session(manager)._resolve_session_use_tts("audio", {}, config) is True


@pytest.mark.parametrize("assist", ["mimo", "qwen"])
def test_follow_assist_default_keeps_realtime_voice(manager, assist):
    config = {"ttsModelProvider": "follow_assist", "assistApi": assist, "ASSIST_API_KEY_MIMO": "mimo-key"}
    manager.set_config(config)
    assert tts_client.selected_realtime_output_tts_provider_key(config, manager, "") is None
    assert _session(manager)._resolve_session_use_tts("audio", {}, config) is False


def test_exact_catalog_voice_still_routes_assist_default_provider(manager):
    config = {"ttsModelProvider": "follow_assist", "assistApi": "mimo", "ASSIST_API_KEY_MIMO": "mimo-key"}
    manager.set_config(config)
    assert tts_client.selected_realtime_output_tts_provider_key(config, manager, "冰糖") == "mimo"
    assert _session(manager, "冰糖")._resolve_session_use_tts("audio", {}, config) is True


@pytest.mark.parametrize(("config", "voice"), [
    ({"ttsModelProvider": "mimo", "TTS_PROVIDER": "mimo"}, ""),
    ({"ttsModelProvider": "follow_assist", "assistApi": "mimo"}, "冰糖"),
    ({"ttsModelProvider": "doubao_tts", "ttsVoiceId": "zh_female_demo"}, ""),
])
def test_selected_provider_without_key_keeps_realtime_voice(manager, config, voice):
    manager.set_config(config)
    assert tts_client.selected_realtime_output_tts_provider_key(config, manager, voice)
    session = _session(manager, voice)
    assert session._resolve_session_use_tts("audio", {}, config) is False
    assert session._resolve_session_use_tts("text", {}, config) is True


def test_custom_without_fallback_voice_keeps_core_voice(manager):
    manager.set_config(dict(CUSTOM))
    assert tts_client.get_tts_worker("qwen", False, "")[2] == "qwen"
    assert tts_client.selected_realtime_output_tts_provider_key(CUSTOM, manager, "") is None
    assert _session(manager)._resolve_session_use_tts("audio", {}, CUSTOM) is False


def test_stored_custom_voice_keeps_established_routing(manager):
    config = {"ttsModelProvider": "mimo", "TTS_PROVIDER": "mimo"}
    manager.set_config(config)
    manager.voice_id_exists_in_any_storage.return_value = True
    assert tts_client.selected_realtime_output_tts_provider_key(config, manager, "my-clone") is None
    # Without a MiMo key the clone must not fall back to the realtime voice.
    assert _session(manager, "my-clone")._resolve_session_use_tts("audio", {}, config) is True


def test_saved_voice_named_like_catalog_voice_keeps_its_route(manager):
    # A saved clone may share a MiMo catalog name while belonging to another provider.
    config = {"ttsModelProvider": "follow_assist", "assistApi": "mimo"}
    manager.set_config(config)
    manager.voice_id_exists_in_any_storage.return_value = True
    assert tts_client.selected_realtime_output_tts_provider_key(config, manager, "冰糖") is None
    assert _session(manager, "冰糖")._resolve_session_use_tts("audio", {}, config) is True


def test_doubao_without_any_voice_keeps_realtime_voice(manager):
    # The Doubao worker rejects an empty voice, so taking over would go silent.
    config = {"ttsModelProvider": "doubao_tts", "assistApiKeyDoubaoTts": "doubao-key"}
    manager.set_config(config)
    assert tts_client.selected_realtime_output_tts_provider_key(config, manager, "") == "doubao_tts"
    assert tts_client.realtime_output_tts_provider_ready(config, manager, "", "doubao_tts") is False
    assert _session(manager)._resolve_session_use_tts("audio", {}, config) is False
    # A character voice alone is enough for the worker.
    assert tts_client.realtime_output_tts_provider_ready(config, manager, "zh_female_demo", "doubao_tts")


def test_custom_with_invalid_endpoint_keeps_realtime_voice(manager):
    # An invalid URL resolves to a placeholder that fails at once; keep the realtime voice.
    config = {**CUSTOM, "ttsModelUrl": "ws://localhost:8000", "ttsVoiceId": "af_heart"}
    manager.set_config(config)
    assert tts_client.selected_realtime_output_tts_provider_key(config, manager, "") == "custom"
    assert tts_client.get_tts_worker("qwen", False, "")[0] is tts_client.configured_tts_unavailable_worker
    assert tts_client.realtime_output_tts_provider_ready(config, manager, "", "custom") is False
    assert _session(manager)._resolve_session_use_tts("audio", {}, config) is False


def test_failing_resolve_counts_as_unavailable(manager, monkeypatch):
    config = {"ttsModelProvider": "mimo", "TTS_PROVIDER": "mimo", "ASSIST_API_KEY_MIMO": "mimo-key"}
    manager.set_config(config)

    def boom(_ctx):
        raise RuntimeError("secret-bearing failure")

    monkeypatch.setitem(
        provider_registry._REGISTRY, "mimo", dataclasses.replace(provider_registry.get("mimo"), resolve=boom),
    )
    assert tts_client.realtime_output_tts_provider_ready(config, manager, "", "mimo") is False


@pytest.mark.parametrize("name", ["custom", "doubao_tts"])
def test_fallback_voice_change_restarts_worker(manager, name):
    base = {**CUSTOM, "ttsVoiceId": "af_heart"} if name == "custom" else {
        "ttsModelProvider": "doubao_tts", "assistApiKeyDoubaoTts": "doubao-key", "ttsVoiceId": "zh_female_demo",
    }
    session = _session(manager)
    session._effective_tts_route = lambda: ("", False)

    manager.set_config(dict(base))
    first = session._build_tts_runtime_key()
    manager.set_config({**base, "ttsVoiceId": "other_voice"})
    second = session._build_tts_runtime_key()

    assert first[0] == second[0] == name
    assert first != second
