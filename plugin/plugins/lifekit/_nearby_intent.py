"""Deterministic query preparation for conversational nearby searches."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re

from ._i18n import I18n


_INTENT_I18N = I18n(Path(__file__).resolve().parent / "locales")


class NearbyIntentStatus(str, Enum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"


@dataclass(frozen=True)
class NearbyIntentRequest:
    raw_request: str = ""
    proposed_query: str = ""
    proposed_location: str = ""
    proposed_radius: int = 3000
    locale: str = "zh-CN"
    is_conversational: bool | None = None


@dataclass(frozen=True)
class NearbyIntentResolution:
    status: NearbyIntentStatus
    query: str = ""
    category_id: str = ""
    location: str = ""
    radius: int = 3000
    choices: tuple[str, ...] = ()


_PREFIXED_LOCATION = re.compile(
    r"(?:去往|[在到去往找搜查])(?:一下|下)?"
    r"(?P<location>[\u3400-\u9fff]{2,20}?(?:省|市|区|县))"
    r"(?:附近|周边|周邊)"
)
_DIRECT_LOCATION = re.compile(
    r"^(?P<location>[\u3400-\u9fff]{2,20}?(?:省|市|区|县))(?:附近|周边|周邊)"
)
_INVALID_DIRECT_LOCATION_PREFIXES = (
    "我",
    "请",
    "幫",
    "帮",
    "麻烦",
    "麻煩",
    "想",
    "要",
    "能",
)
_EXPLICIT_RADIUS = re.compile(
    r"(\d+(?:\.\d+)?)\s*(公里|千米|km|米|m)",
    re.IGNORECASE,
)
_KEYWORD_TAIL = re.compile(
    r"^(?:请|麻烦)?(?:帮我)?(?:找|搜|查|看看|推荐)(?:一下)?(?:我)?"
    r"(?:(?:在|到|去往|去)?[\u3400-\u9fff]{2,20}?(?:省|市|区|县))?"
    r"(?:附近|周边|周邊)(?:的)?(?P<keyword>[\u3400-\u9fffA-Za-z0-9 ]{2,20})"
    r"[。！!？?]?$"
)
_NEARBY_NOUN_TAIL = re.compile(
    r"^(?:[\u3400-\u9fff]{2,20}?(?:省|市|区|县))?"
    r"(?:附近|周边|周邊|周围|周圍)(?:的)?"
    r"(?P<keyword>[\u3400-\u9fffA-Za-z0-9 ]{2,20})[。！!？?]?$"
)
_EN_KEYWORD_TAIL = re.compile(
    r"^(?:find|search for|show me|recommend)(?: me)?(?: a| an| some)?\s+"
    r"(?P<keyword>[A-Za-z0-9' -]{2,40}?)\s+nearby[.!?]?$",
    re.IGNORECASE,
)
_EN_NEARBY_NOUN_TAIL = re.compile(
    r"^(?:(?:are there|is there)(?:\s+(?:a|an|any|some))?\s+)?"
    r"(?P<keyword>[A-Za-z0-9' -]{2,40}?)\s+nearby[.!?]?$",
    re.IGNORECASE,
)
_AMBIGUOUS_KEYWORD_MARKERS = (
    "有啥",
    "有什么",
    "有没有",
    "哪里",
    "哪儿",
    "地方",
    "可以",
    "适合",
    "適合",
    "场所",
    "場所",
    "好玩",
    "place",
    "places",
    "thing",
    "things",
    "somewhere",
    "anything",
)
_KEYWORD_SEPARATOR = re.compile(r"(?:或者|或是|还是|与|、)")


class NearbyIntentResolver:
    def resolve(self, request: NearbyIntentRequest) -> NearbyIntentResolution:
        raw = request.raw_request.strip()
        proposed_query = request.proposed_query.strip()
        conversational = (
            bool(raw)
            if request.is_conversational is None
            else request.is_conversational
        )
        location = request.proposed_location.strip() or _extract_location(raw)
        radius = _extract_radius(raw if conversational else "", request.proposed_radius)
        if _is_broad_request(raw, request.locale):
            return NearbyIntentResolution(
                status=NearbyIntentStatus.NEEDS_CLARIFICATION,
                location=location,
                radius=radius,
                choices=_default_choices(request.locale),
            )
        keyword_tail = _extract_keyword_tail(raw, request.locale)
        if keyword_tail:
            keyword_choices = _split_keyword_tail(keyword_tail, request.locale)
            if len(keyword_choices) > 1:
                return NearbyIntentResolution(
                    status=NearbyIntentStatus.NEEDS_CLARIFICATION,
                    location=location,
                    radius=radius,
                    choices=keyword_choices,
                )
            category_id = _exact_category_id(keyword_choices[0], request.locale)
            return NearbyIntentResolution(
                status=NearbyIntentStatus.READY,
                query=(
                    _category_query(category_id, request.locale)
                    if category_id
                    else keyword_choices[0]
                ),
                category_id=category_id,
                location=location,
                radius=radius,
            )
        raw_category_ids = _category_id_matches(raw, request.locale)
        raw_categories = tuple(
            _localize_category(category_id, request.locale)
            for category_id in raw_category_ids
        )
        if len(raw_categories) > 1:
            return NearbyIntentResolution(
                status=NearbyIntentStatus.NEEDS_CLARIFICATION,
                location=location,
                radius=radius,
                choices=raw_categories,
            )
        if (
            conversational
            and len(raw_categories) == 1
            and proposed_query == raw
            and not _looks_like_dialogue(raw, request.locale)
        ):
            category_id = _exact_category_id(proposed_query, request.locale)
            return NearbyIntentResolution(
                status=NearbyIntentStatus.READY,
                query=proposed_query,
                category_id=category_id,
                location=location,
                radius=radius,
            )
        if conversational and len(raw_categories) == 1:
            return NearbyIntentResolution(
                status=NearbyIntentStatus.READY,
                query=_category_query(raw_category_ids[0], request.locale),
                category_id=raw_category_ids[0],
                location=location,
                radius=radius,
            )
        if conversational:
            return NearbyIntentResolution(
                status=NearbyIntentStatus.NEEDS_CLARIFICATION,
                location=location,
                radius=radius,
                choices=_default_choices(request.locale),
            )
        proposed_category_ids = _category_id_matches(proposed_query, request.locale)
        proposed_categories = tuple(
            _localize_category(category_id, request.locale)
            for category_id in proposed_category_ids
        )
        if len(proposed_categories) > 1 or not proposed_query:
            return NearbyIntentResolution(
                status=NearbyIntentStatus.NEEDS_CLARIFICATION,
                location=location,
                radius=radius,
                choices=proposed_categories or _default_choices(request.locale),
            )
        category_id = _exact_category_id(proposed_query, request.locale)
        return NearbyIntentResolution(
            status=NearbyIntentStatus.READY,
            query=(
                _category_query(category_id, request.locale)
                if category_id
                else proposed_query
            ),
            category_id=category_id,
            location=location,
            radius=radius,
        )


def _category_aliases(locale: str) -> dict[str, tuple[str, ...]]:
    raw_mappings = _INTENT_I18N.value("nearby.intent_aliases", locale=locale)
    raw_labels = _INTENT_I18N.value("nearby.category_labels", locale=locale)
    combined: dict[str, list[str]] = {}
    if isinstance(raw_labels, dict):
        for canonical, label in raw_labels.items():
            if isinstance(canonical, str) and isinstance(label, str) and label:
                combined.setdefault(canonical, []).append(label)
    if isinstance(raw_mappings, dict):
        for canonical, aliases in raw_mappings.items():
            if not isinstance(canonical, str) or not isinstance(aliases, list):
                continue
            combined.setdefault(canonical, []).extend(
                alias for alias in aliases if isinstance(alias, str) and alias
            )
    return {
        canonical: tuple(dict.fromkeys(aliases))
        for canonical, aliases in combined.items()
    }


def _category_id_matches(text: str, locale: str) -> tuple[str, ...]:
    matches: list[tuple[str, str]] = []
    for canonical, aliases in _category_aliases(locale).items():
        matching_aliases = [alias for alias in aliases if _contains_alias(text, alias)]
        if matching_aliases:
            matches.append((canonical, max(matching_aliases, key=len)))
    return tuple(
        canonical
        for canonical, alias in matches
        if not any(
            alias != other_alias and alias in other_alias for _, other_alias in matches
        )
    )


def _category_matches(text: str, locale: str) -> tuple[str, ...]:
    return tuple(
        _localize_category(category_id, locale)
        for category_id in _category_id_matches(text, locale)
    )


def _exact_category_id(text: str, locale: str) -> str:
    normalized = text.strip().casefold()
    for category_id, aliases in _category_aliases(locale).items():
        if any(normalized == alias.strip().casefold() for alias in aliases):
            return category_id
    return ""


def _localize_category(category: str, locale: str) -> str:
    labels = _INTENT_I18N.value("nearby.category_labels", locale=locale)
    if isinstance(labels, dict):
        label = labels.get(category)
        if isinstance(label, str) and label:
            return label
    return category


def _category_query(category: str, locale: str) -> str:
    queries = _INTENT_I18N.value("nearby.category_queries", locale=locale)
    if isinstance(queries, dict):
        query = queries.get(category)
        if isinstance(query, str) and query:
            return query
    return _localize_category(category, locale)


def _contains_alias(text: str, alias: str) -> bool:
    if alias.isascii():
        return bool(re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE))
    return alias in text


def _looks_like_dialogue(text: str, locale: str) -> bool:
    lowered = text.casefold()
    markers = _INTENT_I18N.value("nearby.intent_dialogue_markers", locale=locale)
    if not isinstance(markers, list):
        return False
    return any(
        isinstance(marker, str) and marker.casefold() in lowered
        for marker in markers
    )


def _is_broad_request(text: str, locale: str) -> bool:
    lowered = text.casefold()
    phrases = _INTENT_I18N.value("nearby.intent_broad_phrases", locale=locale)
    if not isinstance(phrases, list):
        return False
    return any(
        isinstance(phrase, str) and phrase.casefold() in lowered
        for phrase in phrases
    )


def _default_choices(locale: str) -> tuple[str, ...]:
    choice_ids = _INTENT_I18N.value("nearby.intent_choice_ids", locale=locale)
    if isinstance(choice_ids, list) and all(
        isinstance(item, str) for item in choice_ids
    ):
        return tuple(_localize_category(item, locale) for item in choice_ids)
    return ()


def _extract_location(text: str) -> str:
    match = _PREFIXED_LOCATION.search(text)
    if match:
        return match.group("location")
    match = _DIRECT_LOCATION.search(text)
    if not match:
        return ""
    location = match.group("location")
    if location.startswith(_INVALID_DIRECT_LOCATION_PREFIXES):
        return ""
    return location


def _extract_keyword_tail(text: str, locale: str) -> str:
    stripped = text.strip()
    for pattern in (
        _KEYWORD_TAIL,
        _NEARBY_NOUN_TAIL,
        _EN_KEYWORD_TAIL,
        _EN_NEARBY_NOUN_TAIL,
    ):
        match = pattern.fullmatch(stripped)
        if not match:
            continue
        keyword = _strip_keyword_wrappers(match.group("keyword"))
        lowered = keyword.casefold()
        if (
            keyword
            and not _looks_like_dialogue(keyword, locale)
            and not any(
                marker.casefold() in lowered for marker in _AMBIGUOUS_KEYWORD_MARKERS
            )
        ):
            return keyword
    return ""


def _strip_keyword_wrappers(keyword: str) -> str:
    cleaned = keyword.strip()
    has_question_particle = cleaned.endswith(("吗", "嗎", "呢", "么", "麼", "嘛", "呀"))
    for prefix in (
        "有没有",
        "有沒有",
        "有什么",
        "有什麼",
        "有啥",
        "找一家",
        "找个",
        "找個",
        "看看",
        "推荐",
        "推薦",
        "找",
        "搜",
        "查",
    ):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break
    cleaned = cleaned.rstrip("吗嗎呢么麼嘛呀").strip()
    if cleaned.startswith("有"):
        if cleaned.startswith("有机"):
            return cleaned
        if has_question_particle:
            return cleaned[1:].strip()
        return ""
    return cleaned


def _split_keyword_tail(keyword: str, locale: str) -> tuple[str, ...]:
    if locale.startswith("en"):
        alternatives = tuple(
            part.strip()
            for part in re.split(r"\s+or\s+", keyword, flags=re.IGNORECASE)
            if part.strip()
        )
        if len(alternatives) >= 2:
            return alternatives
        conjunctions = tuple(
            part.strip()
            for part in re.split(r"\s+and\s+", keyword, flags=re.IGNORECASE)
            if part.strip()
        )
        if len(conjunctions) >= 2 and all(
            _category_matches(part, locale) for part in conjunctions
        ):
            return conjunctions

    parts = tuple(
        part.strip() for part in _KEYWORD_SEPARATOR.split(keyword) if part.strip()
    )
    if len(parts) < 2:
        for separator in ("和", "或", "跟", "及"):
            if separator not in keyword:
                continue
            parts = tuple(
                part.strip() for part in keyword.rsplit(separator, 1) if part.strip()
            )
            if len(parts) >= 2:
                break
    if len(parts) < 2 or any(len(part) < 2 for part in parts):
        return (keyword,)
    return parts


def _extract_radius(text: str, fallback: int) -> int:
    match = _EXPLICIT_RADIUS.search(text)
    if not match:
        radius = min(max(int(fallback), 500), 50000)
        if any(
            term in text.casefold() for term in ("走路", "步行", "walking distance")
        ):
            return min(radius, 1500)
        return radius
    value = float(match.group(1))
    unit = match.group(2).casefold()
    metres = round(value * 1000) if unit in {"公里", "千米", "km"} else round(value)
    return min(max(metres, 500), 50000)
