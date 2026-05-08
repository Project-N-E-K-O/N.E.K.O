import os
import sys
from collections import deque

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_routers import system_router as sr


def test_parse_unified_phase1_marks_explicit_music_and_meme_pass():
    parsed = sr._parse_unified_phase1_result(
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
    parsed = sr._parse_unified_phase1_result(
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
