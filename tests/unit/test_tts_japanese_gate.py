# -*- coding: utf-8 -*-
import pytest

from utils.bilingual_speech import japanese_only_for_tts
from utils.tts_japanese_gate import (
    ensure_chinese_display_text,
    ensure_japanese_tts_text,
    tts_text_looks_japanese,
)


def test_tts_text_looks_japanese():
    assert tts_text_looks_japanese("こんにちは")
    assert not tts_text_looks_japanese("今天天气不错")
    assert not tts_text_looks_japanese("")


@pytest.mark.asyncio
async def test_ensure_japanese_keeps_kana_clauses(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("should not translate")

    monkeypatch.setattr("utils.language_utils.translate_text", boom)
    out = await ensure_japanese_tts_text("こんにちは。你好。")
    assert "こんにちは" in out
    assert "你好" not in out


@pytest.mark.asyncio
async def test_ensure_japanese_translates_chinese(monkeypatch):
    async def fake_translate(text, target_lang, source_lang=None, skip_google=False):
        assert target_lang == "ja"
        assert "今天" in text
        return "今日はいい天気だね。", False

    monkeypatch.setattr("utils.language_utils.translate_text", fake_translate)
    out = await ensure_japanese_tts_text("今天天气不错呢。")
    assert out == "今日はいい天気だね。"
    assert japanese_only_for_tts(out)


@pytest.mark.asyncio
async def test_ensure_chinese_display_from_japanese(monkeypatch):
    async def fake_translate(text, target_lang, source_lang=None, skip_google=False):
        assert target_lang == "zh"
        return "今天天气不错呢。", False

    monkeypatch.setattr("utils.language_utils.translate_text", fake_translate)
    out = await ensure_chinese_display_text("今日はいい天気だね。")
    assert out == "今天天气不错呢。"
