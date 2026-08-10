"""Typed nearby intents translated into provider-independent search terms."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, Sequence

from ._nearby_discovery import normalize_search_terms


MAX_PREFERENCE_HINTS = 4

_ZH_LOCATION_PREFIX = re.compile(
    r"^(?:请|麻烦|帮我|给我|想|我要|我想)?(?:查(?:一下)?|找(?:一下)?|看看|推荐)?"
)
_ZH_NEARBY_PARTS = re.compile(
    r"^(?P<location>.*?)(?:附近|周边|旁边|一带)(?P<target>.*)$"
)
_EN_NEARBY_PARTS = re.compile(
    r"^(?P<target>.*?)\b(?:near|around|close\s+to)\s+"
    r"(?P<location>[^,;!?。！？]+)$",
    re.IGNORECASE,
)


PlaceIntent = Literal[
    "food",
    "coffee",
    "shopping",
    "outdoors",
    "culture",
    "family",
    "nightlife",
    "service",
    "explore",
]


@dataclass(frozen=True)
class PlaceIntentDefinition:
    search_terms: tuple[str, ...]
    preference_terms: dict[str, str]
    request_keywords: tuple[str, ...] = ()


PLACE_INTENTS: dict[str, PlaceIntentDefinition] = {
    "food": PlaceIntentDefinition(
        search_terms=("餐厅",),
        preference_terms={
            "火锅": "火锅",
            "火锅店": "火锅",
            "烧烤": "烧烤",
            "烧烤店": "烧烤",
            "川菜": "川菜",
            "日料": "日料",
            "日本料理": "日料",
            "素食": "素食餐厅",
        },
        request_keywords=("好吃", "吃饭", "餐厅", "饭店", "美食", "restaurant", "restaurants", "food", "eat"),
    ),
    "coffee": PlaceIntentDefinition(
        search_terms=("咖啡馆", "茶馆"),
        preference_terms={
            "咖啡": "咖啡馆",
            "茶": "茶馆",
            "甜品": "甜品店",
        },
        request_keywords=("咖啡", "茶馆", "coffee", "cafe", "tea"),
    ),
    "shopping": PlaceIntentDefinition(
        search_terms=("商店", "购物中心"),
        preference_terms={
            "超市": "超市",
            "便利店": "便利店",
            "商场": "购物中心",
            "书店": "书店",
        },
        request_keywords=("购物", "商场", "商店", "超市", "shopping", "mall", "shop"),
    ),
    "outdoors": PlaceIntentDefinition(
        search_terms=("公园", "景点"),
        preference_terms={},
        request_keywords=("公园", "户外", "徒步", "park", "outdoor", "hiking"),
    ),
    "culture": PlaceIntentDefinition(
        search_terms=("博物馆", "美术馆", "书店"),
        preference_terms={
            "博物馆": "博物馆",
            "美术馆": "美术馆",
            "书店": "书店",
        },
        request_keywords=("博物馆", "美术馆", "文化", "museum", "gallery"),
    ),
    "family": PlaceIntentDefinition(
        search_terms=("室内游乐场", "公园", "博物馆"),
        preference_terms={
            "室内": "室内游乐场",
            "游乐场": "室内游乐场",
            "公园": "公园",
            "博物馆": "博物馆",
        },
        request_keywords=("亲子", "儿童", "小孩", "family", "kids", "children"),
    ),
    "nightlife": PlaceIntentDefinition(
        search_terms=("酒吧", "夜店"),
        preference_terms={"酒吧": "酒吧", "夜店": "夜店"},
        request_keywords=("酒吧", "夜店", "bar", "nightclub", "nightlife"),
    ),
    "service": PlaceIntentDefinition(
        search_terms=("医院", "药店", "银行", "停车场"),
        preference_terms={
            "医院": "医院",
            "hospital": "医院",
            "药店": "药店",
            "pharmacy": "药店",
            "银行": "银行",
            "bank": "银行",
            "停车": "停车场",
            "停车场": "停车场",
            "parking": "停车场",
        },
        request_keywords=("医院", "药店", "银行", "停车", "hospital", "pharmacy", "bank", "parking"),
    ),
    "explore": PlaceIntentDefinition(
        search_terms=("景点", "公园", "咖啡馆", "书店"),
        preference_terms={},
    ),
}


def normalize_place_intent(value: object) -> str:
    intent = str(value).strip().casefold()
    return intent if intent in PLACE_INTENTS else "explore"


def infer_location_hint(request: object) -> str:
    """Recover a conservative search center when the host omits a hint.

    This is intentionally small and deterministic: it only accepts explicit
    nearby grammar and never invents a city from the requested place category.
    """
    center, _ = _request_parts(request)
    return center


def has_explicit_nearby_relation(request: object) -> bool:
    """Whether the request itself explicitly separates a target and center."""
    text = str(request or "").strip()
    if not text:
        return False
    zh_text = _ZH_LOCATION_PREFIX.sub("", text).strip()
    return bool(_ZH_NEARBY_PARTS.match(zh_text) or _EN_NEARBY_PARTS.match(text))


def _request_parts(request: object) -> tuple[str, str]:
    """Return the explicit nearby center and requested target phrase."""
    text = str(request or "").strip()
    if not text:
        return "", ""
    zh_text = _ZH_LOCATION_PREFIX.sub("", text).strip()
    if match := _ZH_NEARBY_PARTS.match(zh_text):
        return (
            match.group("location").strip(" ，,。.!！？?"),
            match.group("target").lstrip(" 的").strip(" ，,。.!！？?"),
        )
    if match := _EN_NEARBY_PARTS.match(text):
        location = match.group("location").strip(" ,.!?。！？")
        if location.casefold() in {"me", "my location", "here"}:
            location = ""
        return location, match.group("target").strip(" ,.!?。！？")
    return "", text


def _keyword_matches(text: str, keyword: str) -> bool:
    """Match CJK phrases by text and Latin keywords by complete words."""
    folded_keyword = keyword.casefold()
    if re.search(r"[a-z0-9]", folded_keyword):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(folded_keyword)}(?![a-z0-9])",
                text,
            )
        )
    return folded_keyword in text


def infer_place_intent(request: object) -> str:
    """Classify common explicit category words without an LLM dependency."""
    _, target = _request_parts(request)
    text = target.casefold()
    matches: list[str] = []
    for intent, definition in PLACE_INTENTS.items():
        keywords = (*definition.preference_terms, *definition.request_keywords)
        if any(_keyword_matches(text, keyword) for keyword in keywords):
            matches.append(intent)
    return matches[0] if len(matches) == 1 else "explore"


def infer_explicit_search_terms(request: object) -> tuple[str, ...]:
    """Preserve every explicit provider-searchable target in textual order."""
    _, target = _request_parts(request)
    text = target.casefold()
    matches: list[tuple[int, int, str]] = []
    for definition in PLACE_INTENTS.values():
        for keyword, term in definition.preference_terms.items():
            if not _keyword_matches(text, keyword):
                continue
            position = text.find(keyword.casefold())
            matches.append((position if position >= 0 else len(text), -len(keyword), term))
    ordered: list[str] = []
    seen: set[str] = set()
    for _, _, term in sorted(matches):
        key = term.casefold()
        if key not in seen:
            ordered.append(term)
            seen.add(key)
    return normalize_search_terms(ordered)


def infer_preference_hints(request: object, place_intent: str) -> tuple[str, ...]:
    """Recover only provider-searchable preference terms from the target phrase."""
    intent = normalize_place_intent(place_intent)
    _, target = _request_parts(request)
    text = target.casefold()
    return tuple(
        term
        for keyword, term in PLACE_INTENTS[intent].preference_terms.items()
        if _keyword_matches(text, keyword)
    )[:MAX_PREFERENCE_HINTS]


def infer_fallback_search_term(request: object) -> str:
    """Preserve an otherwise unknown explicit target instead of inventing one."""
    _, target = _request_parts(request)
    target = target.strip()
    if not target or len(target) > 80:
        return ""
    text = target.casefold()
    if any(
        _keyword_matches(text, keyword)
        for definition in PLACE_INTENTS.values()
        for keyword in (*definition.preference_terms, *definition.request_keywords)
    ):
        return ""
    if text in {
        "有什么合适的地方",
        "有什么",
        "找地方",
        "places",
        "place",
        "something",
    }:
        return ""
    return target


def normalize_preference_hints(
    values: Sequence[object] | None,
) -> tuple[str, ...]:
    hints: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        hint = str(value).strip()
        key = hint.casefold()
        if hint and key not in seen:
            hints.append(hint)
            seen.add(key)
    return tuple(hints[:MAX_PREFERENCE_HINTS])


def search_terms_for_hints(
    place_intent: str,
    preference_hints: Sequence[object] | None,
) -> tuple[str, ...]:
    intent = normalize_place_intent(place_intent)
    definition = PLACE_INTENTS[intent]
    preference_terms = tuple(
        definition.preference_terms[key]
        for hint in preference_hints or ()
        if (key := str(hint).strip().casefold()) in definition.preference_terms
    )
    return normalize_search_terms(preference_terms or definition.search_terms)
