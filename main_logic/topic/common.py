"""Shared helpers for the background topic-hook package."""
from __future__ import annotations

import re
from typing import Any


# ⚠️ Include both Simplified and Traditional. These tables remove function words before splitting topics into characters/bigrams for similarity.
# Missing Traditional forms does not crash or yield zero matches; it silently biases scores: all Traditional topics share
# frequent units such as 這 / 個 / 還 / 嗎 / 與, systematically raising pairwise similarity
# and making repetition detection overly aggressive. Observed topic_units('這個遊戲還在更新嗎') gains
# function-word units 這 / 個 / 嗎 / 還 / 這個 / 新嗎 / 還更 (9 → 15 units).
#
# ⚠️⚠️ Deliberately exclude 著 despite being the Traditional counterpart of Simplified aspect marker 着. Their
# roles differ: Simplified uses 着 for aspect and 著 lexically (著名 / 著作), so the baseline includes
# only 着. Traditional 著 serves both roles, inseparable at character level. Including it removes genuine
# lexical content from 著名 / 著作: observed Jaccard for 著名景點推薦 vs 著名景點清單 falls
# from 0.47 to 0.38, affecting decisions around the 0.6 deduplication threshold, and also damages Simplified
# (著名景点推荐 also contains 著), spilling beyond zh-TW scope (Codex P2).
# Tradeoff: tolerate one function word (Traditional aspect marker 著 remains) rather than remove real content.
#
# ZH_LINK_STOP_CHARS intentionally excludes demonstrative/personal pronouns (這/那/我/你/他/她/它); add only function-word forms.
ZH_TOPIC_STOP_CHARS = set("的一是在不了和就都而及与與着或吗嗎呢啊吧呀也很还還再又这這那我你他她它")
ZH_LINK_STOP_CHARS = set("的一是在不了和就都而及与與着或吗嗎呢啊吧呀也很还還再又")


def clean_text(value: Any, *, limit: int | None = 120) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def is_zh_lang(lang: str | None) -> bool:
    return str(lang or "").strip().lower().startswith("zh")


def topic_units(
    text: str,
    *,
    limit: int = 120,
    stop_chars: set[str] | None = None,
    include_cjk_bigrams: bool = True,
) -> set[str]:
    cleaned = clean_text(text, limit=limit).lower()
    effective_stop_chars = ZH_TOPIC_STOP_CHARS if stop_chars is None else stop_chars
    units = {
        token
        for token in re.findall(
            r"[a-z0-9]{3,}|[\u0400-\u04ff]{3,}|[\uac00-\ud7af]{2,}|[\u3040-\u30ffー]{2,}",
            cleaned,
        )
        if token
    }
    chars = [
        char
        for char in cleaned
        if "\u4e00" <= char <= "\u9fff" and char not in effective_stop_chars
    ]
    units.update(chars)
    if include_cjk_bigrams:
        for idx in range(len(chars) - 1):
            units.add(chars[idx] + chars[idx + 1])
    return units
