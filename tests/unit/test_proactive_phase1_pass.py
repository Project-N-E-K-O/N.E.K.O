import os
import sys
from collections import deque

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_routers.system_router import proactive_history as sr
from main_routers.system_router import proactive_sources as sr_sources
from main_routers.system_router import proactive_parsing as sr_parsing
from config.prompts.prompts_proactive import get_proactive_format_sections


def test_parse_unified_phase1_marks_explicit_music_and_meme_pass():
    parsed = sr_parsing._parse_unified_phase1_result(
        """
[MUSIC] PASS
[MEME] [PASS]
"""
    )

    assert parsed["music_keyword"] is None
    assert parsed["meme_keyword"] is None
    assert parsed["music_pass"] is True
    assert parsed["meme_pass"] is True


def test_parse_unified_phase1_keyword_is_not_pass():
    parsed = sr_parsing._parse_unified_phase1_result(
        """
[MUSIC]
关键词：passion fruit
[MEME]
关键词：disaster girl
"""
    )

    assert parsed["music_keyword"] == "passion fruit"
    assert parsed["meme_keyword"] == "disaster girl"
    assert parsed["music_pass"] is False
    assert parsed["meme_pass"] is False


def test_parse_unified_phase1_pass_word_inside_keyword_is_not_pass():
    parsed = sr_parsing._parse_unified_phase1_result(
        """
[MUSIC]
keyword: pass the dutchie
[MEME]
keyword: pass template
"""
    )

    assert parsed["music_keyword"] == "pass the dutchie"
    assert parsed["meme_keyword"] == "pass template"
    assert parsed["music_pass"] is False
    assert parsed["meme_pass"] is False


def test_parse_unified_phase1_keyword_plus_pass_template_line_is_not_pass():
    parsed = sr_parsing._parse_unified_phase1_result(
        """
[MUSIC]
keyword: pass the dutchie
[PASS]
"""
    )

    assert parsed["music_keyword"] == "pass the dutchie"
    assert parsed["music_pass"] is False


def test_strip_proactive_screen_tag_leak_removes_screen_source_label():
    cleaned, tag = sr_parsing._strip_proactive_screen_tag_leak(
        "[Screen]\n看这满屏的符咒，是在给那画中仙重塑筋骨？"
    )

    assert cleaned == "看这满屏的符咒，是在给那画中仙重塑筋骨？"
    # 已知泄漏标签统一归一成 CHAT，下游按普通搭话投递（不再误判无 tag 走 regen/drop）
    assert tag == "CHAT"


def test_strip_proactive_screen_tag_leak_is_case_insensitive():
    for raw in ("[SCREEN]", "[screen]", "[ScReEn]", "[Vision]", "[window]"):
        cleaned, tag = sr_parsing._strip_proactive_screen_tag_leak(f"{raw} 你好呀")
        assert cleaned == "你好呀"
        assert tag == "CHAT"


def test_strip_proactive_screen_tag_leak_recovers_combined_legal_tag():
    # [Screen][CHAT] 组合：剥掉泄漏标签后采用紧随其后的真实来源标签，
    # 避免 [CHAT] 字面作为正文漏给 TTS。
    cleaned, tag = sr_parsing._strip_proactive_screen_tag_leak("[Screen][WEB]\n看这个链接")

    assert cleaned == "看这个链接"
    assert tag == "WEB"


def test_strip_proactive_screen_tag_leak_preserves_legal_source_tags():
    cleaned, tag = sr_parsing._strip_proactive_screen_tag_leak("[CHAT]\n你好呀")

    assert cleaned == "[CHAT]\n你好呀"
    assert tag == ""


def test_strip_proactive_screen_tag_leak_ignores_unknown_bracket_tags():
    # 未知 / 非屏幕泄漏标签保守放行，留给调用方既有的无 tag 处理逻辑。
    cleaned, tag = sr_parsing._strip_proactive_screen_tag_leak("[Foo] 这不是来源标签")

    assert cleaned == "[Foo] 这不是来源标签"
    assert tag == ""


def test_recent_proactive_prompt_has_strong_paired_boundaries():
    lanlan = "测试娘"
    snapshot = sr._proactive_chat_history.get(lanlan)
    sr._proactive_chat_history[lanlan] = deque(
        [(sr.time.time(), "最近忙啥呢，这么久没见。", "chat")],
        maxlen=10,
    )
    try:
        rendered = sr._format_recent_proactive_chats(lanlan, "zh")
    finally:
        if snapshot is None:
            sr._proactive_chat_history.pop(lanlan, None)
        else:
            sr._proactive_chat_history[lanlan] = snapshot

    assert "======以下为近期搭话记录" in rendered
    assert "想不到新切入点就必须 [PASS]" in rendered
    assert "======以上为近期搭话记录" in rendered
    assert "雷同则 [PASS]" in rendered


def test_recent_proactive_similarity_blocks_at_90_percent():
    lanlan = "测试娘-repeat"
    snapshot = sr._proactive_chat_history.get(lanlan)
    sr._proactive_chat_history[lanlan] = deque(
        [(sr.time.time(), "最近别太累啦，记得喝口水休息一下。", "chat")],
        maxlen=10,
    )
    old_threshold = sr._PROACTIVE_SIMILARITY_THRESHOLD
    sr._PROACTIVE_SIMILARITY_THRESHOLD = 0.90
    try:
        is_duplicate, score = sr._is_similar_to_recent_proactive_chat(
            lanlan,
            "最近别太累啦，记得喝口水休息一下!",
        )
    finally:
        sr._PROACTIVE_SIMILARITY_THRESHOLD = old_threshold
        if snapshot is None:
            sr._proactive_chat_history.pop(lanlan, None)
        else:
            sr._proactive_chat_history[lanlan] = snapshot

    assert is_duplicate is True
    assert score >= 0.90


def test_format_sections_omit_music_tag_without_playable_track():
    # 没有可播曲目时（Phase 1 链接去重清空 / 无 track），上游不会构造 music_section，
    # has_music=False。output-format 必须不暴露 [MUSIC] 选项——从模型视角等同"用户
    # 没碰过音乐分享"，杜绝模型在无歌可投时仍押 [MUSIC]（发了 [MUSIC] 转译不出）。
    _src, fmt = get_proactive_format_sections(
        has_screen=False, has_web=True, has_music=False, has_meme=False, lang="zh",
    )
    assert "[MUSIC]" not in fmt
    assert "[MEME]" not in fmt
    assert "[WEB]" in fmt  # 其它有副作用通道仍正常列出


def test_format_sections_expose_music_tag_with_playable_track():
    # 有可播曲目（selected_music_link 非空 → music_section 非空 → has_music=True）时，
    # output-format 才列出 [MUSIC] 选项。
    _src, fmt = get_proactive_format_sections(
        has_screen=False, has_web=False, has_music=True, has_meme=False, lang="zh",
    )
    assert "[MUSIC]" in fmt
    assert "[WEB]" not in fmt
    assert "[MEME]" not in fmt


def test_format_sections_no_side_effect_tags_is_tagless():
    # 完全没有副作用素材时走 _of_none：纯文本无 tag，更不会出现 [MUSIC]。
    _src, fmt = get_proactive_format_sections(
        has_screen=True, has_web=False, has_music=False, has_meme=False, lang="zh",
    )
    assert "[MUSIC]" not in fmt
    assert "[WEB]" not in fmt
    assert "[MEME]" not in fmt
    assert "[CHAT]" not in fmt


def test_recent_proactive_similarity_ignores_expired_history():
    lanlan = "测试娘-expired"
    snapshot = sr._proactive_chat_history.get(lanlan)
    sr._proactive_chat_history[lanlan] = deque(
        [(sr.time.time() - sr._RECENT_CHAT_MAX_AGE_SECONDS - 1, "同一句话", "chat")],
        maxlen=10,
    )
    try:
        is_duplicate, score = sr._is_similar_to_recent_proactive_chat(lanlan, "同一句话")
    finally:
        if snapshot is None:
            sr._proactive_chat_history.pop(lanlan, None)
        else:
            sr._proactive_chat_history[lanlan] = snapshot

    assert is_duplicate is False
    assert score == 0.0
