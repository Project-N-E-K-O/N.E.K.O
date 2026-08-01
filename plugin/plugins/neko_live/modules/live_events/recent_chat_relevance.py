"""Deterministic local relevance scoring for short recent-chat retrieval."""

from __future__ import annotations

import re
from typing import Callable


_ASCII_WORD_RE = re.compile(r"[a-z0-9]{2,}")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_SENSITIVE_QUERY_RE = re.compile(
    r"\b(cookie|token|signature|authorization|sessionid|webcast_sign)\b\s*[:=]",
    re.IGNORECASE,
)
_QUERY_FILLERS = (
    "刚刚",
    "刚才",
    "最近",
    "最新",
    "弹幕",
    "观众",
    "直播间",
    "有人",
    "说了",
    "说的",
    "什么",
    "关于",
    "room",
    "chat",
    "viewer",
    "latest",
    "recent",
    "said",
    "about",
    "what",
)


def clean_relevance_query(value: object, *, max_length: int = 80) -> str:
    if not isinstance(value, str):
        return ""
    compact = " ".join(value.casefold().split())[: max(0, int(max_length))]
    if "[redacted]" in compact or _SENSITIVE_QUERY_RE.search(compact):
        return ""
    for filler in _QUERY_FILLERS:
        compact = (
            re.sub(rf"\b{re.escape(filler)}\b", " ", compact)
            if filler.isascii()
            else compact.replace(filler, " ")
        )
    return " ".join(compact.split())


def relevance_score(query: object, text: object) -> int:
    scorer = build_relevance_scorer(query)
    return scorer(text) if scorer is not None else 0


def build_relevance_scorer(query: object) -> Callable[[object], int] | None:
    clean_query = clean_relevance_query(query)
    if not clean_query:
        return None
    dense_query = _dense(clean_query)
    if len(dense_query) < 2:
        return None
    query_features = _features(clean_query)
    ascii_query_features = {item for item in query_features if item.isascii()}
    cjk_query_features = query_features - ascii_query_features
    needs_dense_match = dense_query != clean_query

    def score(text: object) -> int:
        clean_text = (
            " ".join(text.casefold().split()) if isinstance(text, str) else ""
        )
        if not clean_text:
            return 0
        if clean_query in clean_text or (
            needs_dense_match and dense_query in _dense(clean_text)
        ):
            return 100 + min(len(dense_query), 20)
        overlap = {
            feature for feature in cjk_query_features if feature in clean_text
        }
        if ascii_query_features:
            overlap.update(
                ascii_query_features & set(_ASCII_WORD_RE.findall(clean_text))
            )
        if not overlap:
            return 0
        value = sum(8 + min(len(feature), 6) for feature in overlap)
        value += round(20 * len(overlap) / max(1, len(query_features)))
        return value

    return score


def _features(value: str) -> set[str]:
    features = set(_ASCII_WORD_RE.findall(value))
    for run in _CJK_RUN_RE.findall(value):
        if len(run) <= 4:
            features.add(run)
        features.update(run[index : index + 2] for index in range(len(run) - 1))
    return features


def _dense(value: str) -> str:
    return "".join(
        char
        for char in value
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )


__all__ = ["build_relevance_scorer", "clean_relevance_query", "relevance_score"]
