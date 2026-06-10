import pytest

from utils.mimo_tts_voices import normalize_mimo_tts_voice
from utils.native_voice_registry import get_provider, is_native_voice


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
