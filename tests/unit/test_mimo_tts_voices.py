import pytest

from utils.mimo_tts_voices import normalize_mimo_tts_voice
from utils.native_voice_registry import (
    get_active_realtime_native_provider_for_ui,
    get_provider,
    is_native_voice,
    is_saveable_native_voice,
)


class _CM:
    def __init__(self, core_config, tts_custom_config=None):
        self._core_config = core_config
        self._tts_custom_config = tts_custom_config or {"is_custom": False}

    def get_core_config(self):
        return self._core_config

    def get_model_api_config(self, model_type):
        if model_type == "realtime":
            return {"api_type": "qwen", "base_url": ""}
        if model_type == "tts_custom":
            return self._tts_custom_config
        raise AssertionError(model_type)


@pytest.mark.unit
def test_mimo_native_voice_provider_is_registered():
    provider = get_provider("mimo")
    assert provider is not None
    assert provider.default_voice == "mimo_default"
    assert provider.default_male_voice == "Milo"


@pytest.mark.unit
def test_mimo_voice_aliases_normalize_to_catalog_ids():
    assert normalize_mimo_tts_voice("默认") == ("mimo_default", True)
    assert normalize_mimo_tts_voice("中文女") == ("冰糖", True)
    assert normalize_mimo_tts_voice("english male") == ("Milo", True)
    assert is_native_voice("冰糖", "mimo") is True
    assert is_native_voice("not-a-mimo-voice", "mimo") is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "core_config",
    [
        {"CORE_API_TYPE": "qwen", "ttsProvider": "mimo"},
        {"CORE_API_TYPE": "qwen", "assistApi": "mimo"},
    ],
)
def test_mimo_tts_route_exposes_native_voice_catalog(core_config):
    assert get_active_realtime_native_provider_for_ui(_CM(core_config)) == "mimo"


@pytest.mark.unit
def test_mimo_assist_api_takes_priority_over_tts_provider_for_voice_catalog():
    cm = _CM({"CORE_API_TYPE": "qwen", "ttsProvider": "step", "assistApi": "mimo"})

    assert get_active_realtime_native_provider_for_ui(cm) == "mimo"


@pytest.mark.unit
def test_mimo_voice_catalog_is_hidden_when_gptsovits_custom_tts_wins():
    cm = _CM(
        {"CORE_API_TYPE": "qwen", "assistApi": "mimo", "GPTSOVITS_ENABLED": True},
        {"is_custom": True},
    )

    assert get_active_realtime_native_provider_for_ui(cm) is None
    assert is_saveable_native_voice(cm, "Milo") is False


@pytest.mark.unit
def test_mimo_tts_route_makes_builtin_voices_saveable():
    cm = _CM({"CORE_API_TYPE": "qwen", "ttsProvider": "mimo"})

    assert is_saveable_native_voice(cm, "Milo") is True
