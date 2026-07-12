"""Gemini TTS voice 目录的规范化、识别与 UI shape 行为。"""
import os
import sys

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.gemini_tts_voices import (
    GEMINI_PROVIDER,
    GEMINI_TTS_DEFAULT_MALE_VOICE,
    GEMINI_TTS_DEFAULT_VOICE,
    GEMINI_TTS_VOICE_GENDERS,
    is_gemini_tts_voice,
    normalize_gemini_tts_voice,
)
from utils.native_voice_registry import get_native_voice_catalog_for_ui
from utils.tts.providers import gemini as gemini_provider


@pytest.mark.parametrize(
    ("voice_id", "expected"),
    [
        ("Puck", "Puck"),
        ("Leda", "Leda"),
        ("Charon", "Charon"),
        ("Zubenelgenubi", "Zubenelgenubi"),
    ],
)
def test_exact_match_returns_canonical(voice_id, expected):
    name, recognized = normalize_gemini_tts_voice(voice_id)
    assert (name, recognized) == (expected, True)


@pytest.mark.parametrize(
    ("voice_id", "expected"),
    [
        ("puck", "Puck"),
        ("PUCK", "Puck"),
        ("pUcK", "Puck"),
        ("  Leda  ", "Leda"),
        ("zephyr", "Zephyr"),
    ],
)
def test_casefold_and_whitespace_normalized(voice_id, expected):
    name, recognized = normalize_gemini_tts_voice(voice_id)
    assert (name, recognized) == (expected, True)


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("中文男", GEMINI_TTS_DEFAULT_MALE_VOICE),
        ("男", GEMINI_TTS_DEFAULT_MALE_VOICE),
        ("男声", GEMINI_TTS_DEFAULT_MALE_VOICE),
        ("male", GEMINI_TTS_DEFAULT_MALE_VOICE),
        ("MALE", GEMINI_TTS_DEFAULT_MALE_VOICE),
        ("Masculine", GEMINI_TTS_DEFAULT_MALE_VOICE),
        ("中文女", GEMINI_TTS_DEFAULT_VOICE),
        ("女", GEMINI_TTS_DEFAULT_VOICE),
        ("female", GEMINI_TTS_DEFAULT_VOICE),
        ("Feminine", GEMINI_TTS_DEFAULT_VOICE),
    ],
)
def test_aliases_resolve_to_default_per_gender(alias, expected):
    name, recognized = normalize_gemini_tts_voice(alias)
    assert (name, recognized) == (expected, True)


@pytest.mark.parametrize("voice_id", ["", "   ", None])
def test_empty_input_is_unrecognized(voice_id):
    """空输入用 default 兜底，但 recognized=False —— 调用方据此区分
    "用户显式选了 voice" 与 "我们替他默认选了一个"。"""
    name, recognized = normalize_gemini_tts_voice(voice_id)
    assert name == GEMINI_TTS_DEFAULT_VOICE
    assert recognized is False


@pytest.mark.parametrize("voice_id", ["foo", "cosyvoice-v2-xyz", "gsv:abc", "中文"])
def test_unknown_input_falls_back_unrecognized(voice_id):
    name, recognized = normalize_gemini_tts_voice(voice_id)
    assert name == GEMINI_TTS_DEFAULT_VOICE
    assert recognized is False


def test_is_gemini_tts_voice_matches_recognized():
    assert is_gemini_tts_voice("Puck") is True
    assert is_gemini_tts_voice("中文男") is True
    assert is_gemini_tts_voice("puck") is True
    assert is_gemini_tts_voice("foo") is False
    assert is_gemini_tts_voice("") is False
    assert is_gemini_tts_voice(None) is False
    assert is_gemini_tts_voice("   ") is False


def test_gemini_voice_catalog_for_ui_shape():
    voices = get_native_voice_catalog_for_ui("gemini")
    assert voices is not None
    assert set(voices.keys()) == set(GEMINI_TTS_VOICE_GENDERS.keys())
    for voice_name, meta in voices.items():
        assert meta["provider"] == "gemini"
        assert meta["builtin"] is True
        assert meta["gender"] == GEMINI_TTS_VOICE_GENDERS[voice_name]
        assert meta["display_name"] == voice_name
        assert voice_name in meta["prefix"]
        assert meta["gender"] in meta["prefix"]


def test_gemini_provider_registered_with_expected_metadata():
    assert GEMINI_PROVIDER.key == "gemini"
    assert GEMINI_PROVIDER.default_voice == GEMINI_TTS_DEFAULT_VOICE
    assert GEMINI_PROVIDER.default_male_voice == GEMINI_TTS_DEFAULT_MALE_VOICE
    assert GEMINI_PROVIDER.catalog_prefix == "Gemini"


def test_default_voices_are_in_catalog():
    """default voice 必须真的在 catalog 里 —— 否则 fallback 会撞上
    Gemini API 报 voice 不存在。"""
    assert GEMINI_TTS_DEFAULT_VOICE in GEMINI_TTS_VOICE_GENDERS
    assert GEMINI_TTS_DEFAULT_MALE_VOICE in GEMINI_TTS_VOICE_GENDERS
    assert GEMINI_TTS_VOICE_GENDERS[GEMINI_TTS_DEFAULT_MALE_VOICE] == "Male"
    assert GEMINI_TTS_VOICE_GENDERS[GEMINI_TTS_DEFAULT_VOICE] == "Female"


def test_partial_config_defaults_drive_gemini_fallback_aliases(monkeypatch):
    catalog = {"ConfiguredFemale": "Female", "ConfiguredMale": "Male"}
    monkeypatch.setattr(gemini_provider, "_CFG", {"voices": catalog})
    monkeypatch.setattr(gemini_provider, "GEMINI_TTS_VOICE_GENDERS", catalog)
    monkeypatch.setattr(
        gemini_provider, "GEMINI_TTS_DEFAULT_VOICE", "ConfiguredFemale"
    )
    monkeypatch.setattr(
        gemini_provider, "GEMINI_TTS_DEFAULT_MALE_VOICE", "ConfiguredMale"
    )

    provider = gemini_provider._create_provider()

    assert provider.normalize("female") == ("ConfiguredFemale", True)
    assert provider.normalize("male") == ("ConfiguredMale", True)


def test_partial_free_intl_config_defaults_drive_fallback_aliases(monkeypatch):
    cfg = {
        "voices": {"IntlFemale": "Female", "IntlMale": "Male"},
        "default_voice": "IntlFemale",
        "default_male_voice": "IntlMale",
    }
    monkeypatch.setattr(
        gemini_provider,
        "get_native_tts_voice_provider_config",
        lambda provider_key: cfg if provider_key == "free_intl" else {},
    )

    provider = gemini_provider._create_free_intl_provider()

    assert provider.normalize("female") == ("IntlFemale", True)
    assert provider.normalize("male") == ("IntlMale", True)
    assert provider.normalize("default") == ("IntlFemale", True)
