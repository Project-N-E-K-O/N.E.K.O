# -*- coding: utf-8 -*-
from utils.bilingual_speech import (
    BilingualSpeechSplitter,
    chinese_only_for_display,
    dual_language_speech_prompt_block,
    japanese_only_for_tts,
    split_untagged_dual_text,
    strip_bilingual_tags_for_history,
)


def test_bilingual_splitter_routes_ja_to_tts_and_zh_to_display():
    s = BilingualSpeechSplitter()
    c1 = s.feed("<ja>こんにちは。</ja>")
    c2 = s.feed("<zh>你好。</zh>")
    c3 = s.flush()
    assert c1.tts == "こんにちは。"
    assert c1.display == ""
    assert c2.display == "你好。"
    assert c2.tts == ""
    assert c3.display == ""
    assert c3.tts == ""


def test_bilingual_splitter_handles_chunked_tags():
    s = BilingualSpeechSplitter()
    parts = ["<j", "a>こん", "にちは。</ja><zh>你", "好。</z", "h>"]
    display = []
    tts = []
    for p in parts:
        chunk = s.feed(p)
        display.append(chunk.display)
        tts.append(chunk.tts)
    flush = s.flush()
    display.append(flush.display)
    tts.append(flush.tts)
    assert "".join(tts) == "こんにちは。"
    assert "".join(display) == "你好。"


def test_bilingual_splitter_untagged_chinese_goes_to_display_only():
    s = BilingualSpeechSplitter()
    s.feed("纯中文回复")
    flush = s.flush()
    assert flush.display == "纯中文回复"
    assert flush.tts == ""


def test_bilingual_splitter_strips_chinese_stuffed_into_ja():
    s = BilingualSpeechSplitter()
    c = s.feed("<ja>こんにちは。你好。</ja><zh>你好。</zh>")
    c2 = s.flush()
    tts = c.tts + c2.tts
    display = c.display + c2.display
    assert "你好" not in tts
    assert "こんにちは" in tts
    assert display == "你好。"


def test_bilingual_splitter_drops_pure_chinese_ja_channel():
    s = BilingualSpeechSplitter()
    c = s.feed("<ja>今天天气不错呢。</ja><zh>今天天气不错呢。</zh>")
    c2 = s.flush()
    assert (c.tts + c2.tts) == ""
    assert (c.display + c2.display) == "今天天气不错呢。"


def test_untagged_mixed_splits_by_script():
    chunk = split_untagged_dual_text("こんにちは。你好。")
    assert "こんにちは" in chunk.tts
    assert "你好" not in chunk.tts
    assert chunk.display == "你好。"


def test_japanese_only_for_tts_helpers():
    assert japanese_only_for_tts("今天天气不错") == ""
    assert "こんにちは" in japanese_only_for_tts("こんにちは。你好。")
    assert chinese_only_for_display("こんにちは。你好。") == "你好。"


def test_strip_bilingual_tags_for_history_keeps_chinese():
    text = "<ja>今日はいい天気だね。</ja><zh>今天天气不错呢。</zh>"
    assert strip_bilingual_tags_for_history(text) == "今天天气不错呢。"


def test_dual_language_prompt_mentions_same_meaning():
    block = dual_language_speech_prompt_block()
    assert "<ja>" in block and "<zh>" in block
    assert "SAME meaning" in block or "same intent" in block
    assert "kana" in block.lower() or "Chinese-only" in block
