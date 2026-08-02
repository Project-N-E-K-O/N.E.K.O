"""Traditional-Chinese coverage for the Focus keyword tables (issue #2500).

Both tables here are scanned across *every* locale at once — ``scan_vulnerability
_keywords`` and ``detect_topic_switch`` iterate ``.values()`` — so they match
against whatever the user actually typed, with no locale plumbing involved. That
makes the failure silent and total: a Traditional writer's cues matched nothing
at all, and Focus simply never saw them.

No call site changes, and none is needed: the tables either have the characters
or they do not.
"""
from __future__ import annotations

import pytest

from config.prompts import prompts_focus as F

# Characters that only exist in the Traditional orthography, and their Simplified
# counterparts. `里` is deliberately absent: it is correct in both (公里 / 里長),
# so using it as a marker would flag a legitimate Traditional entry later.
TRADITIONAL_ONLY = "憊撐頂盡難開傷壓勁動裡緒喪煩氣夠無惱獨單沒興虛慮過絕堅棄潰馬媽筆滾對話順換說來欸個習憤髒"
SIMPLIFIED_ONLY = "惫撑顶尽难开伤压劲动绪丧烦气够无恼独单没兴虚虑过绝坚弃溃马妈笔滚对话顺换说来诶个习愤脏"

TABLES = ["FOCUS_VULNERABILITY_KEYWORDS_I18N", "FOCUS_TOPIC_SWITCH_MARKERS_I18N"]


@pytest.mark.parametrize("name", TABLES)
def test_every_chinese_table_has_a_traditional_block(name):
    table = getattr(F, name)
    assert "zh" in table, f"{name} 没有 zh block，本用例的前提不成立"
    assert "zh-TW" in table, f"{name} 缺 zh-TW，繁中输入在这张表上一个字都匹配不到"


@pytest.mark.parametrize("name", TABLES)
def test_the_two_blocks_have_the_same_entry_count(name):
    """Entry for entry, so a later edit to one side is visibly odd."""
    table = getattr(F, name)
    assert len(table["zh"]) == len(table["zh-TW"]), (
        f"{name}: zh={len(table['zh'])} zh-TW={len(table['zh-TW'])}"
    )


@pytest.mark.parametrize("name", TABLES)
def test_traditional_block_is_not_a_copy(name):
    """Guards against a block added to satisfy a checklist without converting."""
    table = getattr(F, name)
    assert table["zh"] != table["zh-TW"]
    assert any(ch in TRADITIONAL_ONLY for entry in table["zh-TW"] for ch in entry)


@pytest.mark.parametrize("name", TABLES)
def test_traditional_block_has_no_simplified_only_characters(name):
    table = getattr(F, name)
    offenders = sorted(
        entry for entry in table["zh-TW"]
        if any(ch in SIMPLIFIED_ONLY for ch in entry)
    )
    assert not offenders, f"{name} 的 zh-TW 里混进了简体字：{offenders}"


@pytest.mark.parametrize("text", [
    "我好累", "真的好難過", "壓力好大", "撐不住了", "想哭", "沒人懂",
    "好煩躁", "受夠了", "什麼都不想", "喘不過氣", "好絕望", "情緒低落",
    "心裡難受", "提不起勁", "忍無可忍", "一個人", "好孤獨", "快崩潰",
])
def test_traditional_text_now_scores_vulnerability_cues(text):
    """The whole point: before this, every one of these scored zero."""
    assert F.scan_vulnerability_keywords(text) >= 1


@pytest.mark.parametrize("text", [
    "我好累", "真的好难过", "压力好大", "撑不住了", "想哭", "没人懂",
    "好烦躁", "受够了", "什么都不想",
])
def test_simplified_text_is_unaffected(text):
    assert F.scan_vulnerability_keywords(text) >= 1


@pytest.mark.parametrize("text", ["今天天气不错", "我们去吃饭吧", "hello there", "今天天氣不錯"])
def test_ordinary_talk_still_scores_zero(text):
    """Adding entries must not make the scanner fire on ordinary talk."""
    assert F.scan_vulnerability_keywords(text) == 0


def test_a_phrase_in_both_blocks_counts_once():
    """Both blocks list phrases that are identical in either orthography.

    The scanner counts distinct phrase text, so those cost nothing — but if it
    ever counted per table, a Traditional user's cue would score double and
    silently outrank a Simplified user's.
    """
    assert F.scan_vulnerability_keywords("想哭") == 1
    assert F.scan_vulnerability_keywords("好累") == 1


def test_stacked_cues_still_count_separately():
    """The scorer reads the count as a rough intensity, so this has to stay graded."""
    assert F.scan_vulnerability_keywords("我好累，而且好難過，真的撐不住了") >= 3


@pytest.mark.parametrize("text", [
    "對了，我想問一下", "話說回來", "欸對了", "順便問一下", "換個話題",
    "說起來", "另外", "突然想到",
])
def test_traditional_topic_switch_markers(text):
    assert F.detect_topic_switch(text) is True


@pytest.mark.parametrize("text", ["对了，我想问", "话说回来", "by the way", "ところで"])
def test_other_locales_still_switch_topics(text):
    assert F.detect_topic_switch(text) is True


@pytest.mark.parametrize("text", ["我今天好開心", "然後我就走了", "我想說一件事"])
def test_a_message_without_a_leading_marker_is_not_a_switch(text):
    """The marker has to open the message; mid-sentence is usually incidental."""
    assert F.detect_topic_switch(text) is False


def test_the_other_six_locales_are_untouched():
    """Adding a block must not perturb the tables that were already there."""
    for name in TABLES:
        table = getattr(F, name)
        for locale in ("en", "ja", "ko", "ru", "es", "pt"):
            assert table.get(locale), f"{name} 少了 {locale}"
