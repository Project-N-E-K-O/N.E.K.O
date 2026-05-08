# -*- coding: utf-8 -*-
"""
CJK Unicode helpers — single source of truth for character-class checks.

Why a dedicated module:
- TTS chunking (`utils/frontend_utils.py`) needs CJK awareness for speech-time
  estimation — that path is intentionally NOT migrated to tiktoken because
  what it estimates is reading duration, not token cost.
- The token-counter heuristic fallback (`utils/tokenize.py`) needs the same
  ranges for its char-class fallback when tiktoken is unavailable.
- Both used to inline the same magic ranges. This module collapses the
  duplication so range adjustments happen in one place.

Coverage choice:
- Han: U+4E00..U+9FFF — CJK Unified Ideographs (covers ~99% of modern
  Chinese / Japanese kanji / Korean hanja in everyday text).
- Kana: U+3040..U+30FF — Hiragana + Katakana.
- Hangul: U+AC00..U+D7AF — Hangul Syllables (precomposed; modern Korean).

We deliberately do NOT include CJK Compatibility, Extension B+, or full-width
forms. Adding them is a one-line edit if a real bug surfaces, but those
characters are rare enough that over-counting them in the token heuristic
is the wrong default.
"""
from __future__ import annotations


# Single source of truth for the CJK character-class regex range, so callers
# that need a `regex.compile(...)`-friendly pattern stay in sync with the
# function-level helpers below if the ranges ever shift.
CJK_REGEX_CHAR_CLASS = (
    "一-鿿"   # Han
    "぀-ヿ"   # Kana
    "ｦ-ﾟ"   # Halfwidth katakana
    "가-힯"   # Hangul syllables
)


def is_chinese_char(c: str) -> bool:
    """Han / Chinese hanzi / Japanese kanji / Korean hanja (CJK Unified)."""
    return "\u4e00" <= c <= "\u9fff"


def is_kana_char(c: str) -> bool:
    """Japanese hiragana + katakana, including halfwidth katakana
    (U+FF66..U+FF9F: \uff66-\uff9f). Halfwidth kana looks visually narrow but its
    BPE token density is closer to fullwidth katakana than to ASCII;
    classifying it as kana keeps the heuristic fallback honest."""
    return ("\u3040" <= c <= "\u30ff") or ("\uff66" <= c <= "\uff9f")


def is_hangul_char(c: str) -> bool:
    """Korean Hangul syllables (precomposed)."""
    return "\uac00" <= c <= "\ud7af"


def is_cjk_char(c: str) -> bool:
    """Any of Han / Kana / Hangul. Used by the tiktoken heuristic fallback
    where all three are weighted the same. Kana includes halfwidth katakana
    (U+FF66..U+FF9F) \u2014 see is_kana_char."""
    return (
        "\u4e00" <= c <= "\u9fff"
        or "\u3040" <= c <= "\u30ff"
        or "\uff66" <= c <= "\uff9f"
        or "\uac00" <= c <= "\ud7af"
    )


def count_chinese_chars(text: str) -> int:
    """Count Han characters only. Used by TTS speech-time estimator where
    Chinese gets a different per-char duration than kana."""
    return sum(1 for c in text if "\u4e00" <= c <= "\u9fff")


def count_kana_chars(text: str) -> int:
    """Count Japanese kana only \u2014 fullwidth + halfwidth (see is_kana_char)."""
    return sum(
        1 for c in text
        if ("\u3040" <= c <= "\u30ff") or ("\uff66" <= c <= "\uff9f")
    )


def count_hangul_chars(text: str) -> int:
    """Count Korean Hangul syllables only."""
    return sum(1 for c in text if "\uac00" <= c <= "\ud7af")


def count_cjk_chars(text: str) -> int:
    """Count Han + Kana + Hangul as a single bucket. Used by the tiktoken
    heuristic where the three classes share a token-density factor."""
    return sum(1 for c in text if is_cjk_char(c))
