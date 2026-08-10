"""Resolve holiday names without guessing a country from UI language."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping, Sequence

DEFAULT_CONTEXT_TIMEOUT_SECONDS = 0.25

_FIXED_DATES: Mapping[str, tuple[int, int]] = {
    "元旦": (1, 1),
    "new year": (1, 1),
    "情人节": (2, 14),
    "valentine": (2, 14),
    "妇女节": (3, 8),
    "women's day": (3, 8),
    "愚人节": (4, 1),
    "april fools": (4, 1),
    "劳动节": (5, 1),
    "儿童节": (6, 1),
    "children's day": (6, 1),
    "万圣节": (10, 31),
    "halloween": (10, 31),
    "平安夜": (12, 24),
    "christmas eve": (12, 24),
    "圣诞节": (12, 25),
    "christmas": (12, 25),
    "跨年": (12, 31),
    "new year's eve": (12, 31),
}

_COUNTRY_ALIASES: Mapping[str, str] = {
    "cn": "CN",
    "china": "CN",
    "中国": "CN",
    "tw": "TW",
    "taiwan": "TW",
    "台湾": "TW",
    "us": "US",
    "usa": "US",
    "united states": "US",
    "美国": "US",
}


@dataclass(frozen=True)
class HolidayCandidate:
    country: str
    target: date

    def as_dict(self) -> dict[str, str]:
        return {"country": self.country, "date": self.target.isoformat()}


@dataclass(frozen=True)
class HolidayResolution:
    target: date | None = None
    assumed_country: str = ""
    alternatives: tuple[HolidayCandidate, ...] = ()


class HolidayResolver:
    """Resolve fixed and regional holiday names with disclosed ambiguity."""

    def __init__(self, today_provider: Callable[[], date] = date.today) -> None:
        self._today_provider = today_provider

    def resolve(
        self,
        text: str,
        *,
        country_hint: str = "",
    ) -> HolidayResolution:
        name = text.strip().casefold()
        today = self._today_provider()
        if fixed := _FIXED_DATES.get(name):
            return HolidayResolution(target=_next_fixed_date(today, *fixed))

        candidates = self._regional_candidates(name, today)
        if not candidates:
            return HolidayResolution()

        country = normalize_country(country_hint)
        if country:
            selected = next(
                (item for item in candidates if item.country == country),
                None,
            )
            if selected:
                return HolidayResolution(
                    target=selected.target,
                    assumed_country=selected.country,
                )

        ordered = sorted(candidates, key=lambda item: (item.target, item.country))
        selected = ordered[0]
        return HolidayResolution(
            target=selected.target,
            assumed_country=selected.country,
            alternatives=tuple(ordered[1:]),
        )

    @staticmethod
    def _regional_candidates(
        name: str,
        today: date,
    ) -> tuple[HolidayCandidate, ...]:
        if name == "labor day":
            return (
                HolidayCandidate("CN", _next_fixed_date(today, 5, 1)),
                HolidayCandidate("TW", _next_fixed_date(today, 5, 1)),
                HolidayCandidate("US", _next_us_labor_day(today)),
            )
        if name in {"national day", "国庆节", "國慶節"}:
            return (
                HolidayCandidate("CN", _next_fixed_date(today, 10, 1)),
                HolidayCandidate("TW", _next_fixed_date(today, 10, 10)),
                HolidayCandidate("US", _next_fixed_date(today, 7, 4)),
            )
        return ()


def normalize_country(value: object) -> str:
    text = str(value or "").strip().casefold()
    return _COUNTRY_ALIASES.get(text, text.upper())


async def default_saved_country(
    plugin: Any,
    *,
    timeout_seconds: float = DEFAULT_CONTEXT_TIMEOUT_SECONDS,
) -> str:
    """Read a saved default country's metadata without geocoding or IP calls."""
    loader = getattr(plugin, "_load_saved_locations_for_ui", None)
    if not callable(loader):
        return ""
    try:
        records = await asyncio.wait_for(loader(), timeout=timeout_seconds)
    except Exception:
        return ""
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return ""
    default = next(
        (
            item
            for item in records
            if isinstance(item, Mapping) and item.get("is_default")
        ),
        None,
    )
    if not isinstance(default, Mapping):
        return ""
    return normalize_country(default.get("country_code") or default.get("country"))


def _next_fixed_date(today: date, month: int, day: int) -> date:
    target = date(today.year, month, day)
    return target if target >= today else date(today.year + 1, month, day)


def _next_us_labor_day(today: date) -> date:
    def labor_day(year: int) -> date:
        september_first = date(year, 9, 1)
        return date(year, 9, 1 + (-september_first.weekday()) % 7)

    target = labor_day(today.year)
    return target if target >= today else labor_day(today.year + 1)
