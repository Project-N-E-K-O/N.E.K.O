"""Traditional-Chinese coverage for the negative-keyword scan (issue #2500).

``scan_negative_keywords`` is Layer 1 of the pushback pipeline: it decides
whether a user turn is worth spending a cheap-tier LLM call on to find *which*
observation the user is pushing back against. Its table was Simplified-only, so
a Traditional writer's "別說了" matched nothing and the whole pipeline never
ran for them — a structural 100% miss, not a low score.

The call site is the part that can silently undo this. ``scan_negative_keywords``
strips the region suffix by contract ("unknown language -> treat as Chinese"),
so ``zh-TW`` is already ``zh`` by the time the lookup happens: a per-locale
``NEGATIVE_KEYWORDS_I18N['zh-TW']`` entry would be unreachable data. The fix is
the union at the lookup, which is why the assertions below deliberately pass
``lang="zh"`` as well as ``lang="zh-TW"``.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import pytest

from config.prompts import prompts_directives as D

# Characters that exist only in one orthography, used as "did anyone actually
# convert this" markers. Every entry here is a character the *other* block uses.
TRADITIONAL_ONLY = "別說講換話題閉煩討厭無這個問"
SIMPLIFIED_ONLY = "别说讲换话题闭烦讨厌无这个问"


def test_table_has_both_chinese_blocks():
    assert "zh" in D.NEGATIVE_KEYWORDS_I18N
    assert "zh-TW" in D.NEGATIVE_KEYWORDS_I18N, (
        "缺 zh-TW block，繁中输入在这张表上一个词都匹配不到"
    )


def test_the_two_blocks_have_the_same_entry_count():
    """Entry for entry, so a one-sided later edit is visibly odd."""
    zh = D.NEGATIVE_KEYWORDS_I18N["zh"]
    tw = D.NEGATIVE_KEYWORDS_I18N["zh-TW"]
    assert len(zh) == len(tw), f"zh={len(zh)} zh-TW={len(tw)}"


def test_traditional_block_is_not_a_copy():
    zh = D.NEGATIVE_KEYWORDS_I18N["zh"]
    tw = D.NEGATIVE_KEYWORDS_I18N["zh-TW"]
    assert zh != tw
    assert any(ch in TRADITIONAL_ONLY for entry in tw for ch in entry)


def test_traditional_block_has_no_simplified_only_characters():
    offenders = sorted(
        entry
        for entry in D.NEGATIVE_KEYWORDS_I18N["zh-TW"]
        if any(ch in SIMPLIFIED_ONLY for ch in entry)
    )
    assert not offenders, f"zh-TW 里混进了简体字：{offenders}"


def test_no_single_character_entries_in_either_block():
    """`別` is the back half of 個別 / 區別 / 特別; `煩` of 麻煩. Same standard
    as the Simplified block, which the table comment already spells out."""  # noqa: DOCSTRING_CJK
    for key in ("zh", "zh-TW"):
        shorties = sorted(e for e in D.NEGATIVE_KEYWORDS_I18N[key] if len(e) < 2)
        assert not shorties, f"{key} 收了单字词条：{shorties}"


TRADITIONAL_HITS = [
    "別說了",
    "別再說",
    "不要再提這件事",
    "別講了好嗎",
    "換個話題吧",
    "聊點別的",
    "閉嘴",
    "別問了",
    "好煩",
    "真討厭",
    "無語",
]


@pytest.mark.parametrize("text", TRADITIONAL_HITS)
@pytest.mark.parametrize("lang", ["zh-TW", "zh-Hant", "zh", "zh-CN", "", None])
def test_traditional_pushback_is_detected_for_every_chinese_lang(text, lang):
    """The union is keyed off the *normalized* short code, so every Chinese
    variant — and the unknown-language fallback — sees both scripts.

    Passing ``lang="zh"`` here is the point: it is the only assertion that goes
    red if the lookup is reverted to a plain per-locale ``.get(short, ...)``.
    """
    assert D.scan_negative_keywords(text, lang) is True


@pytest.mark.parametrize(
    "text",
    ["别说了", "换个话题", "闭嘴", "好烦", "真讨厌", "无语"],
)
@pytest.mark.parametrize("lang", ["zh", "zh-CN", "zh-TW"])
def test_simplified_pushback_still_detected(text, lang):
    """Adding Traditional must not cost the Simplified side anything, and a
    Traditional UI with Simplified input (pasted content) still works."""
    assert D.scan_negative_keywords(text, lang) is True


@pytest.mark.parametrize(
    "text",
    [
        "今天天氣真好",
        "我們來聊聊這個專案吧",
        "剛剛那個 bug 我修好了",
        "你覺得這樣寫可以嗎",
    ],
)
def test_ordinary_traditional_talk_does_not_trigger(text):
    assert D.scan_negative_keywords(text, "zh-TW") is False


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("no idea what you mean", "en"),
        ("stop talking about it", "en"),
        ("その話はやめて", "ja"),
        ("nada de mais", "pt"),
    ],
)
def test_other_locales_are_untouched(text, lang):
    """The union is Chinese-only: other locales keep their own table."""
    expected = any(kw.lower() in text.lower() for kw in D.NEGATIVE_KEYWORDS_I18N[lang])
    assert D.scan_negative_keywords(text, lang) is expected


def test_union_constant_is_precomputed_not_rebuilt_per_call():
    """Hot path: post_turn runs this per user message per turn."""
    assert isinstance(D._ZH_SCAN_KEYWORDS, frozenset)
    assert D._ZH_SCAN_KEYWORDS == (
        D.NEGATIVE_KEYWORDS_I18N["zh"] | D.NEGATIVE_KEYWORDS_I18N["zh-TW"]
    )


def test_target_check_prompt_is_traditional_for_traditional_users():
    """Layer 2 runs immediately after a Layer 1 hit. Now that Traditional users
    can reach it at all, the prompt it uses has to be Traditional too."""
    tw = D.get_negative_target_check_prompt("zh-TW")
    cn = D.get_negative_target_check_prompt("zh-CN")
    assert tw != cn
    assert "使用者" in tw
    assert "简短理由" not in tw
