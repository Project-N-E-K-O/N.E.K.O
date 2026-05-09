"""Gemini TTS voice 目录的规范化、识别与 UI shape 行为。"""
import os
import sys

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.gemini_tts_voices import (
    GEMINI_TTS_DEFAULT_MALE_VOICE,
    GEMINI_TTS_DEFAULT_VOICE,
    GEMINI_TTS_VOICE_GENDERS,
    get_gemini_tts_voices,
    is_gemini_tts_voice,
    normalize_gemini_tts_voice,
)


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


def test_get_gemini_tts_voices_shape():
    voices = get_gemini_tts_voices()
    assert set(voices.keys()) == set(GEMINI_TTS_VOICE_GENDERS.keys())
    for voice_name, meta in voices.items():
        assert meta["provider"] == "gemini"
        assert meta["builtin"] is True
        assert meta["gender"] == GEMINI_TTS_VOICE_GENDERS[voice_name]
        assert voice_name in meta["prefix"]
        assert meta["gender"] in meta["prefix"]


def test_default_voices_are_in_catalog():
    """default voice 必须真的在 catalog 里 —— 否则 fallback 会撞上
    Gemini API 报 voice 不存在。"""
    assert GEMINI_TTS_DEFAULT_VOICE in GEMINI_TTS_VOICE_GENDERS
    assert GEMINI_TTS_DEFAULT_MALE_VOICE in GEMINI_TTS_VOICE_GENDERS
    assert GEMINI_TTS_VOICE_GENDERS[GEMINI_TTS_DEFAULT_MALE_VOICE] == "Male"
    assert GEMINI_TTS_VOICE_GENDERS[GEMINI_TTS_DEFAULT_VOICE] == "Female"
