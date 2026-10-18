# -*- coding: utf-8 -*-
"""SUMMARY_STALE_HINT must tell the LLM to use `\n\n---\n\n` as the hard boundary between main content and the older-events section.

Mechanism: [memory/recent.py](memory/recent.py), when `gap_hours >= RECENT_SUMMARY_STALE_HOURS`,
injects this hint at the summary prompt's start. Downstream memo injection and [memory_browser
renderChatEdit](static/js/memory_browser.js) split rendering on this divider.
Omitting the `---` instruction in any locale breaks the frontend splitter contract; anchor all seven languages.
"""
from __future__ import annotations

import pytest

from config.prompts.prompts_memory import (
    SUMMARY_STALE_HINT,
    get_summary_stale_hint,
)


_LOCALES = ("zh", "en", "ja", "ko", "ru", "es", "pt")


@pytest.mark.parametrize("lang", _LOCALES)
def test_stale_hint_mentions_triple_dash_divider(lang: str) -> None:
    """Every locale must explicitly tell the LLM to use ``---`` as the divider.

    Check that literal `---` (three ASCII hyphens) appears in prompt text: it is the sole clue used by the frontend
    splitter / next-round injection to identify the older-events section.
    """
    hint = SUMMARY_STALE_HINT[lang]
    assert "---" in hint, f"{lang} hint 未提到 `---` 分界符"


@pytest.mark.parametrize("lang", _LOCALES)
def test_get_summary_stale_hint_substitutes_gap(lang: str) -> None:
    """Replace `{GAP}` while retaining the `---` instruction in the final string."""
    rendered = get_summary_stale_hint(lang, 2.5)
    assert "{GAP}" not in rendered
    assert "2.5" in rendered
    assert "---" in rendered


@pytest.mark.parametrize("lang", _LOCALES)
def test_stale_hint_keeps_six_equal_wrapper(lang: str) -> None:
    """Preserve paired six-equals below/above fences: the PR #1316 security-watermark contract.

    `---` is an instruction within hint content; the outer wrapper still follows
    the paired six-equals convention in `feedback_prompt_delimiters_above_below.md`.
    """
    hint = SUMMARY_STALE_HINT[lang]
    six_eq_count = hint.count("======")
    assert six_eq_count >= 2, f"{lang} hint 应保留 below/above 六等号 wrapper"
