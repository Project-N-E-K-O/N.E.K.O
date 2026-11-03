"""Material-level dedup contract for proactive chat (ANTI_REPEAT_EXEMPT_SOURCE_TAGS).

Material-push channels (MUSIC/MEME) are exempt from caption-level repeat checks and
deduped on the material itself: MUSIC keys on the track, MEME on the search keyword
(not the image). Coverage:

1. _proactive_material_key: MUSIC -> track, MEME -> search keyword, non-material
   channel / empty material -> empty key; normalized (lowercase + collapsed space).
2. _is_recent_proactive_material: same material recently = repeat, different = not,
   empty key = never a repeat.
3. Recent window expires after _RECENT_CHAT_MAX_AGE_SECONDS.
4. _record_proactive_material: empty key not recorded; per-source_tag buckets stay
   separate.
5. _find_verbatim_recent_proactive_chat: the one caption check fresh MEME material
   keeps -- only a pure repeat (punctuation/space/emoji aside) counts.
"""
import os
import sys
import time
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_routers import system_router as sr
from main_logic.proactive_chat.generation import _score_regenerated_draft


def _clear(name="测试角色"):
    sr._proactive_material_history.pop(name, None)


def test_exempt_regen_has_no_synthetic_bm25_after_score():
    corpus = MagicMock()

    # Returns (score, terms): the terms are what lets a block produced by
    # scoring the REGENERATED draft name that draft's own phrases.
    assert _score_regenerated_draft(
        corpus,
        "测试角色",
        "换成了新的音乐素材",
        exempt=True,
    ) == (None, {})
    corpus.score_draft.assert_not_called()


# -- 1. Calculate the material key --


def test_material_key_music_is_title_artist():
    key = sr._proactive_material_key("MUSIC", {"title": "Bong Hoa", "artist": "Dat Nguyen"}, None)
    assert key == "bong hoa|dat nguyen"


def test_material_key_meme_is_search_keyword_not_image():
    # MEME uses the search keyword, independent of url/title.
    key = sr._proactive_material_key(
        "MEME", None, {"keyword": "Disaster Girl", "url": "https://x/y.png"}
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
    # Random-hotword fallback MEME with an empty keyword -> empty key.
    assert sr._proactive_material_key("MEME", None, {"keyword": ""}) == ""
    # MUSIC with no selected track -> empty key.
    assert sr._proactive_material_key("MUSIC", None, None) == ""


# -- 2. Check recent material deduplication --


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
    # The same string in the MEME bucket must not match MUSIC bucket records.
    assert sr._is_recent_proactive_material(name, "MEME", "lofi cat") is False
    assert sr._is_recent_proactive_material(name, "MUSIC", "lofi cat") is True


# -- 3. Expire the recent window --


def test_recent_material_expires_after_window():
    name = "测试角色"
    _clear(name)
    from collections import deque

    # Insert an old record manually, beyond _RECENT_CHAT_MAX_AGE_SECONDS.
    stale_ts = time.time() - sr._RECENT_CHAT_MAX_AGE_SECONDS - 10
    sr._proactive_material_history[name] = {
        "MUSIC": deque([(stale_ts, "old song|x")], maxlen=sr._PROACTIVE_MATERIAL_HISTORY_MAX)
    }
    assert sr._is_recent_proactive_material(name, "MUSIC", "old song|x") is False


# -- 5. Check verbatim MEME repetition --


def _seed_chat_history(monkeypatch, name, *entries):
    from collections import deque

    from main_logic.proactive_chat import state

    monkeypatch.setitem(
        state._proactive_chat_history,
        name,
        deque(entries, maxlen=state.PROACTIVE_CHAT_HISTORY_MAX),
    )
    return state


def test_verbatim_guard_matches_pure_repeat_only(monkeypatch):
    name = "逐字复读测试"
    state = _seed_chat_history(
        monkeypatch, name, (time.time(), "快看这个，真的笑死我了！😂", "meme")
    )

    # Differences only in punctuation / whitespace / emoji: mere repetition.
    match = state._find_verbatim_recent_proactive_chat(name, "快看这个 真的笑死我了。")
    assert match.is_duplicate is True
    assert match.common_fragment == "快看这个 真的笑死我了。"
    # Similar but not identical, even with SequenceMatcher >=0.90: acceptable.
    assert state._find_verbatim_recent_proactive_chat(
        name, "快看这个，真是笑死我了！"
    ).is_duplicate is False
    assert state._find_verbatim_recent_proactive_chat(
        name, "快看这个，笑死我了！"
    ).is_duplicate is False
    # Emoji variation selectors / ZWJ are not content either; retain real combining characters such as accents.
    state = _seed_chat_history(
        monkeypatch,
        name,
        (time.time(), "快看❤️", "meme"),
        (time.time(), "快看👩\u200d💻", "meme"),
    )
    assert state._verbatim_key("快看❤️") == "快看"
    assert state._verbatim_key("快看👩\u200d💻") == "快看"
    # Regional flags (black flag + tag sequence) are also only emoji.
    assert state._verbatim_key(
        "快看\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F"
    ) == "快看"
    assert state._find_verbatim_recent_proactive_chat(name, "快看").is_duplicate is True
    assert state._verbatim_key("café") != state._verbatim_key("cafe")
    # Exclude compatibility symbols by original category before NFKC expands them into letters; normalize fullwidth letters normally.
    assert state._verbatim_key("快看™") == "快看"
    assert state._verbatim_key("快看℡") == "快看"
    assert state._verbatim_key("Ｋｕａｉ") == "kuai"
    assert state._verbatim_key("cafe\u0301") == state._verbatim_key("café")
    # Punctuation-only drafts have no comparable content.
    assert state._find_verbatim_recent_proactive_chat(name, "！！😂").is_duplicate is False


def test_verbatim_guard_ignores_expired_history(monkeypatch):
    name = "逐字复读过期测试"
    stale_ts = time.time() - sr._RECENT_CHAT_MAX_AGE_SECONDS - 10
    state = _seed_chat_history(monkeypatch, name, (stale_ts, "快看这个，笑死我了", "meme"))

    assert state._find_verbatim_recent_proactive_chat(
        name, "快看这个，笑死我了"
    ).is_duplicate is False
