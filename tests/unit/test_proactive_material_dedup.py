"""主动搭话"素材级去重"契约（ANTI_REPEAT_EXEMPT_SOURCE_TAGS 用）。

素材推送类 channel（MUSIC/MEME）豁免台词级复读判定，改按"素材本身"去重：
MUSIC 看曲目（title|artist），MEME 看搜索关键词（不是图片）。覆盖：

1. _proactive_material_key：MUSIC 取曲目、MEME 取搜索关键词、非素材 channel /
   空素材 → 空 key；归一化（大小写 + 折叠空白）。
2. _is_recent_proactive_material：同素材近期算雷同、异素材不算、空 key 永不算。
3. 近期窗口按 _RECENT_CHAT_MAX_AGE_SECONDS 过期。
4. _record_proactive_material：空 key 不记录；按 source_tag 分桶互不串。
"""
import os
import sys
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_routers import system_router as sr


def _clear(name="测试角色"):
    sr._proactive_material_history.pop(name, None)


# ── 1. material key 计算 ─────────────────────────────────────


def test_material_key_music_is_title_artist():
    key = sr._proactive_material_key("MUSIC", {"title": "Bong Hoa", "artist": "Dat Nguyen"}, None)
    assert key == "bong hoa|dat nguyen"


def test_material_key_meme_is_search_keyword_not_image():
    # MEME 取搜索关键词，与 url/title 无关
    key = sr._proactive_material_key(
        "MEME", None, {"keyword": "Disaster Girl", "url": "http://x/y.png"}
    )
    assert key == "disaster girl"


def test_material_key_normalizes_whitespace_and_case():
    a = sr._proactive_material_key("MEME", None, {"keyword": "  猫   可爱 "})
    b = sr._proactive_material_key("MEME", None, {"keyword": "猫 可爱"})
    assert a == b == "猫 可爱"


def test_material_key_empty_for_non_material_channels():
    assert sr._proactive_material_key("CHAT", None, None) == ""
    assert sr._proactive_material_key("WEB", {"title": "x"}, None) == ""


def test_material_key_empty_when_no_material():
    # 随机热词 fallback 的 MEME（空关键词）→ 空 key
    assert sr._proactive_material_key("MEME", None, {"keyword": ""}) == ""
    # MUSIC 但没选中曲目 → 空 key
    assert sr._proactive_material_key("MUSIC", None, None) == ""


# ── 2. 近期素材去重判定 ──────────────────────────────────────


def test_recent_material_same_is_repeat_different_is_not():
    name = "测试角色"
    _clear(name)
    sr._record_proactive_material(name, "MUSIC", "bong hoa|dat nguyen")
    assert sr._is_recent_proactive_material(name, "MUSIC", "bong hoa|dat nguyen") is True
    assert sr._is_recent_proactive_material(name, "MUSIC", "other song|x") is False


def test_empty_key_never_repeat():
    name = "测试角色"
    _clear(name)
    assert sr._is_recent_proactive_material(name, "MEME", "") is False


def test_record_skips_empty_key():
    name = "测试角色"
    _clear(name)
    sr._record_proactive_material(name, "MEME", "")
    assert name not in sr._proactive_material_history or not sr._proactive_material_history[name].get("MEME")


def test_tags_are_separate_buckets():
    name = "测试角色"
    _clear(name)
    sr._record_proactive_material(name, "MUSIC", "lofi cat")
    # 同字串落在 MEME 桶时不应命中 MUSIC 桶记录
    assert sr._is_recent_proactive_material(name, "MEME", "lofi cat") is False
    assert sr._is_recent_proactive_material(name, "MUSIC", "lofi cat") is True


# ── 3. 近期窗口过期 ──────────────────────────────────────────


def test_recent_material_expires_after_window():
    name = "测试角色"
    _clear(name)
    from collections import deque

    # 手动塞一条"很久以前"的记录，超出 _RECENT_CHAT_MAX_AGE_SECONDS
    stale_ts = time.time() - sr._RECENT_CHAT_MAX_AGE_SECONDS - 10
    sr._proactive_material_history[name] = {
        "MUSIC": deque([(stale_ts, "old song|x")], maxlen=sr._PROACTIVE_MATERIAL_HISTORY_MAX)
    }
    assert sr._is_recent_proactive_material(name, "MUSIC", "old song|x") is False
