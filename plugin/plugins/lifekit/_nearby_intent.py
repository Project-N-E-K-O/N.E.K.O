"""Deterministic query preparation for conversational nearby searches."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


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
    location: str = ""
    radius: int = 3000
    choices: tuple[str, ...] = ()


_BROAD_PHRASES = (
    "有啥地方可去",
    "有什么地方可去",
    "哪里可以去",
    "去哪玩",
    "好玩的地方",
    "随便逛逛",
    "things to do nearby",
    "places to go nearby",
    "somewhere to go",
)
_DIALOGUE_MARKERS = (
    "帮我",
    "麻烦",
    "附近",
    "周边",
    "周邊",
    "周围",
    "周圍",
    "我附近",
    "附近有",
    "有没有",
    "有什么",
    "有啥",
    "哪里",
    "哪儿",
    "推荐",
    "可以",
    "吗",
    "呢",
    "么",
    "find me",
    "can you",
    "are there",
    "what ",
    "where ",
    "recommend ",
    "nearby",
)
_CATEGORY_ALIASES = (
    ("宠物医院", ("宠物医院",)),
    ("咖啡店", ("咖啡店", "咖啡馆", "咖啡廳", "咖啡")),
    ("便利店", ("便利店",)),
    ("购物中心", ("购物中心", "購物中心", "商场", "商場")),
    ("电影院", ("电影院", "電影院", "影院")),
    ("博物馆", ("博物馆", "博物館")),
    ("停车场", ("停车场", "停車場")),
    ("加油站", ("加油站",)),
    ("地铁站", ("地铁站", "地鐵站")),
    ("餐厅", ("餐厅", "餐廳", "饭店", "飯店", "吃什么", "吃什麼")),
    ("超市", ("超市",)),
    ("景点", ("景点", "景點", "景区", "景區")),
    ("公园", ("公园", "公園")),
    ("书店", ("书店", "書店")),
    ("医院", ("医院", "醫院")),
    ("药店", ("药店", "藥店")),
)
_EN_CATEGORY_ALIASES = (
    ("coffee shop", ("coffee shop", "cafe", "café")),
    ("convenience store", ("convenience store",)),
    ("shopping mall", ("shopping mall", "mall")),
    ("movie theater", ("movie theater", "cinema")),
    ("museum", ("museum",)),
    ("parking", ("parking lot", "parking")),
    ("gas station", ("gas station", "petrol station")),
    ("subway station", ("subway station", "metro station")),
    ("restaurant", ("restaurant", "food")),
    ("supermarket", ("supermarket",)),
    ("attraction", ("attraction", "sightseeing")),
    ("bookstore", ("bookstore", "book shop")),
    ("hospital", ("hospital",)),
    ("pharmacy", ("pharmacy", "drugstore")),
    ("park", ("park",)),
)
_ZH_TW_CATEGORIES = {
    "宠物医院": "寵物醫院",
    "购物中心": "購物中心",
    "电影院": "電影院",
    "博物馆": "博物館",
    "停车场": "停車場",
    "地铁站": "地鐵站",
    "餐厅": "餐廳",
    "景点": "景點",
    "公园": "公園",
    "书店": "書店",
    "医院": "醫院",
    "药店": "藥店",
}
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
        if any(phrase in raw.casefold() for phrase in _BROAD_PHRASES):
            return NearbyIntentResolution(
                status=NearbyIntentStatus.NEEDS_CLARIFICATION,
                location=location,
                radius=radius,
                choices=_default_choices(request.locale),
            )
        keyword_tail = _extract_keyword_tail(raw)
        if keyword_tail:
            keyword_choices = _split_keyword_tail(keyword_tail)
            if len(keyword_choices) > 1:
                return NearbyIntentResolution(
                    status=NearbyIntentStatus.NEEDS_CLARIFICATION,
                    location=location,
                    radius=radius,
                    choices=keyword_choices,
                )
            return NearbyIntentResolution(
                status=NearbyIntentStatus.READY,
                query=keyword_choices[0],
                location=location,
                radius=radius,
            )
        raw_categories = _category_matches(raw, request.locale)
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
            and not _looks_like_dialogue(raw)
        ):
            return NearbyIntentResolution(
                status=NearbyIntentStatus.READY,
                query=proposed_query,
                location=location,
                radius=radius,
            )
        if conversational and len(raw_categories) == 1:
            return NearbyIntentResolution(
                status=NearbyIntentStatus.READY,
                query=raw_categories[0],
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
        proposed_categories = _category_matches(proposed_query, request.locale)
        if len(proposed_categories) > 1 or not proposed_query:
            return NearbyIntentResolution(
                status=NearbyIntentStatus.NEEDS_CLARIFICATION,
                location=location,
                radius=radius,
                choices=proposed_categories or _default_choices(request.locale),
            )
        return NearbyIntentResolution(
            status=NearbyIntentStatus.READY,
            query=proposed_query,
            location=location,
            radius=radius,
        )


def _category_matches(text: str, locale: str) -> tuple[str, ...]:
    mappings = _EN_CATEGORY_ALIASES if locale.startswith("en") else _CATEGORY_ALIASES
    matches: list[tuple[str, str]] = []
    for canonical, aliases in mappings:
        matching_aliases = [alias for alias in aliases if _contains_alias(text, alias)]
        if matching_aliases:
            matches.append((canonical, max(matching_aliases, key=len)))
    return tuple(
        _localize_category(canonical, locale)
        for canonical, alias in matches
        if not any(
            alias != other_alias and alias in other_alias for _, other_alias in matches
        )
    )


def _localize_category(category: str, locale: str) -> str:
    if locale == "zh-TW":
        return _ZH_TW_CATEGORIES.get(category, category)
    return category


def _contains_alias(text: str, alias: str) -> bool:
    if alias.isascii():
        return bool(re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE))
    return alias in text


def _looks_like_dialogue(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _DIALOGUE_MARKERS)


def _default_choices(locale: str) -> tuple[str, ...]:
    if locale.startswith("en"):
        return ("park", "attraction", "restaurant", "shopping mall")
    if locale == "zh-TW":
        return ("公園", "景點", "餐廳", "商場")
    return ("公园", "景点", "餐厅", "商场")


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


def _extract_keyword_tail(text: str) -> str:
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
            and not _looks_like_dialogue(keyword)
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


def _split_keyword_tail(keyword: str) -> tuple[str, ...]:
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
