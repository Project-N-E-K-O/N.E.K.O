# -*- coding: utf-8 -*-
"""SUMMARY_STALE_HINT 必须告知 LLM 用 `\n\n---\n\n` 作为主体与"较久前"段的硬分界。

机制：[memory/recent.py](memory/recent.py) 在 `gap_hours >= RECENT_SUMMARY_STALE_HOURS`
时把该 hint 注入 summary prompt 头部。下游 memo 注入 + [memory_browser
renderChatEdit](static/js/memory_browser.js) 都按这个 divider 切段渲染。
任一 locale 漏掉 `---` 指示会破坏前端 splitter 的语义约定，所以全 7 语言都要锚。
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
    """每条 locale 都要明确告诉 LLM 用 ``---`` 作分界。

    检查字面 `---`（三个 ASCII 连字符）出现在 prompt 文本里——这是前端
    splitter / 下次 round 注入时识别"较久前"段的唯一线索。
    """
    hint = SUMMARY_STALE_HINT[lang]
    assert "---" in hint, f"{lang} hint 未提到 `---` 分界符"


@pytest.mark.parametrize("lang", _LOCALES)
def test_get_summary_stale_hint_substitutes_gap(lang: str) -> None:
    """`{GAP}` 占位符必须被替换，且 `---` 指示仍在最终字符串里。"""
    rendered = get_summary_stale_hint(lang, 2.5)
    assert "{GAP}" not in rendered
    assert "2.5" in rendered
    assert "---" in rendered


@pytest.mark.parametrize("lang", _LOCALES)
def test_stale_hint_keeps_six_equal_wrapper(lang: str) -> None:
    """六等号 below/above 对偶分隔符保留——这是 PR #1316 安全水印约定。

    `---` 是 hint **内容**里给 LLM 的指令；外层 wrapper 仍按
    `feedback_prompt_delimiters_above_below.md` 走六等号对偶。
    """
    hint = SUMMARY_STALE_HINT[lang]
    six_eq_count = hint.count("======")
    assert six_eq_count >= 2, f"{lang} hint 应保留 below/above 六等号 wrapper"
