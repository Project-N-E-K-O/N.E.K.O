"""Shared helpers for the background topic-hook package."""
from __future__ import annotations

import re
from typing import Any


ZH_TOPIC_STOP_CHARS = set("的一是在不了和就都而及与着或吗呢啊吧呀也很还再又这那我你他她它")
ZH_LINK_STOP_CHARS = set("的一是在不了和就都而及与着或吗呢啊吧呀也很还再又")


def clean_text(value: Any, *, limit: int = 120) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) > limit:
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
    units = {
        token
        for token in re.findall(r"[a-z0-9]{3,}", cleaned)
        if token
    }
    chars = [
        char
        for char in cleaned
        if "\u4e00" <= char <= "\u9fff" and char not in (stop_chars or ZH_TOPIC_STOP_CHARS)
    ]
    units.update(chars)
    if include_cjk_bigrams:
        for idx in range(len(chars) - 1):
            units.add(chars[idx] + chars[idx + 1])
    return units
