"""Traditional-Chinese coverage for the mini-game invite reply keywords (#2500).

``_match_mini_game_invite_keyword`` scans ``MINI_GAME_INVITE_KEYWORDS.values()``
— every locale block at once — against whatever the user typed, so there is no
locale plumbing to change: the block either has the characters or it does not.
Before this block existed, a Traditional writer answering "來吧" or "沒空" got
``None``, i.e. the invite silently went unanswered either way.

Because the scan is a union over all locales, a Traditional entry also runs
against Japanese / Korean / Latin input. Japanese kanji overlap heavily with
Traditional, which is a real collision surface and is covered below.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import pytest

from config.prompts.prompts_proactive import MINI_GAME_INVITE_KEYWORDS as KW
from main_logic.proactive_chat.mini_game_invite import _match_mini_game_invite_keyword

# Paired one-for-one. `拒` / `玩` / `等` and friends are deliberately absent:
# they are identical in both orthographies, so using them as markers would flag
# a perfectly good entry.
TRADITIONAL_ONLY = "來絕沒會點過"
SIMPLIFIED_ONLY = "来绝没会点过"

BUCKETS = ("accept", "decline", "later")


def test_table_has_both_chinese_blocks():
    assert "zh" in KW
    assert "zh-TW" in KW, "缺 zh-TW block，繁中回覆一个都匹配不到"


@pytest.mark.parametrize("bucket", BUCKETS)
def test_the_two_blocks_have_the_same_bucket_sizes(bucket):
    """One-to-one with zh so a later one-sided edit reads as odd."""
    assert len(KW["zh"][bucket]) == len(KW["zh-TW"][bucket]), (
        f"{bucket}: zh={len(KW['zh'][bucket])} zh-TW={len(KW['zh-TW'][bucket])}"
    )


def test_traditional_block_is_not_a_copy():
    assert KW["zh"] != KW["zh-TW"]
    words = [w for bucket in BUCKETS for w in KW["zh-TW"][bucket]]
    assert any(ch in TRADITIONAL_ONLY for w in words for ch in w)


def test_traditional_block_has_no_simplified_only_characters():
    offenders = sorted(
        w
        for bucket in BUCKETS
        for w in KW["zh-TW"][bucket]
        if any(ch in SIMPLIFIED_ONLY for ch in w)
    )
    assert not offenders, f"zh-TW 里混进了简体字：{offenders}"


def test_no_substring_matched_accept_phrase_is_contained_in_a_decline_phrase():
    """The design rule the table comment states, checked on every locale that
    actually takes the substring branch.

    ``_keyword_matches`` only falls back to bare ``in`` for keywords outside the
    letters/digits/Cyrillic class; those with word boundaries (``sure`` vs ``not
    sure``) are safe by ``\\b`` and rescued by decline-priority anyway. For CJK
    there is no boundary, so an accept phrase living inside a decline phrase is
    unrecoverable — which is why '來玩' is not in the accept list ('我不來玩'
    would read as accept).

    Deriving the set from ``_LETTER_ONLY_KW_RE`` rather than listing locale names
    keeps the guard alive for any locale added later.
    """  # noqa: DOCSTRING_CJK
    from main_logic.proactive_chat.mini_game_invite import _LETTER_ONLY_KW_RE

    checked = 0
    for locale, buckets in KW.items():
        declines = buckets.get("decline", []) + buckets.get("later", [])
        for accept in buckets.get("accept", []):
            if _LETTER_ONLY_KW_RE.fullmatch(accept):
                continue  # word-boundary matched, not substring
            checked += 1
            bad = [d for d in declines if accept in d]
            assert not bad, f"{locale}: accept {accept!r} 被 {bad!r} 包含"
    assert checked, "没有一条 accept 走子串分支，本用例没在检查任何东西"


TRADITIONAL_CASES = [
    ("來吧", "accept"),
    ("好啊一起玩", "accept"),
    ("拒絕", "decline"),
    ("我沒空", "decline"),
    ("不想玩", "decline"),
    ("等會", "later"),
    ("待會兒", "later"),
    ("晚點吧", "later"),
    ("稍後再說", "later"),
    ("過會兒", "later"),
]


@pytest.mark.parametrize(("text", "expected"), TRADITIONAL_CASES)
def test_traditional_replies_are_matched(text, expected):
    assert _match_mini_game_invite_keyword(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("来吧", "accept"), ("没空", "decline"), ("回头", "later"), ("晚点", "later")],
)
def test_simplified_replies_still_matched(text, expected):
    """Adding Traditional must not cost the Simplified side anything."""
    assert _match_mini_game_invite_keyword(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # Japanese: 回 + 頭 are the same glyphs in Japanese, so a bare '回頭'
        # entry would fire on all of these. That is why the Traditional 'later'
        # bucket uses '待會' instead.
        "今回頭が痛い",
        "前回頭を打った",
        "次回頭に入れておく",
        "毎回頭を使う作業だ",
        "結構です",
        "大丈夫だよ",
        # Korean / English / Spanish
        "머리가 아파",
        "I have no idea",
        "no tengo ni idea",
    ],
)
def test_traditional_entries_do_not_fire_on_other_languages(text):
    assert _match_mini_game_invite_keyword(text) is None


@pytest.mark.parametrize("text", ["不錯啊", "我先看看", "這個遊戲叫什麼"])
def test_ordinary_traditional_replies_are_not_forced_into_a_bucket(text):
    assert _match_mini_game_invite_keyword(text) is None


def test_decline_still_wins_over_accept_in_traditional():
    """Negation priority is what keeps '好啊，但我沒空' out of accept."""  # noqa: DOCSTRING_CJK
    assert _match_mini_game_invite_keyword("好啊，但我沒空") == "decline"
