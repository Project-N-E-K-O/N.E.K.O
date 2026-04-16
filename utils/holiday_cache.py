"""Public holiday cache & greeting consumption tracker.

Fetches yearly holiday data from https://date.nager.at/api/v3 at startup,
caches in memory, and provides holiday/weekend hints for proactive greetings
with a per-period consumption budget.  Consumption state is persisted to disk.

Key concepts
------------
- **HolidayPeriod**: consecutive rest days grouped into one period, containing
  both 法定假日 (statutory holidays) and 调休 (adjusted rest days).
- **CN supplement**: patches the Nager API with correct statutory day counts
  and approximate 调休 extensions.

Consumption rules (per character, per period)
---------------------------------------------
- 假日当天 (any statutory holiday day within the period) : budget 3 (shared)
- 假期非假日 (调休 rest days within the period)           : budget 3 (shared)
- Single-day holiday (no 调休)                           : total 3
- Multi-day holiday (with 调休)                          : total 6  (3+3)
- Weekend (no holiday)                                   : budget 2 per day
- When budget is exhausted, the hint is omitted entirely.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TypedDict

import httpx

logger = logging.getLogger(__name__)

# ── language → country code mapping ──────────────────────────────────
_LANG_TO_COUNTRY: dict[str, str] = {
    'zh': 'CN',
    'en': 'US',
    'ja': 'JP',
    'ko': 'KR',
    'ru': 'RU',
}

# ── types ────────────────────────────────────────────────────────────

class HolidayEntry(TypedDict):
    date: str          # "YYYY-MM-DD"
    localName: str     # native name
    name: str          # english name


class HolidayPeriod:
    """A group of consecutive rest days.

    Attributes:
        start / end      — inclusive boundaries of the full rest period
        nominal_date     — the single "名义日期" of the holiday (e.g. 10/1 for
                           国庆节, 初一 for 春节).  Only THIS day qualifies as
                           "假日当天" with an independent 3-use budget.
        name / local_name — display names
    """
    __slots__ = ("name", "local_name", "start", "end", "nominal_date")

    def __init__(self, name: str, local_name: str,
                 start: date, end: date,
                 nominal_date: date | None = None) -> None:
        self.name = name
        self.local_name = local_name
        self.start = start
        self.end = end
        self.nominal_date = nominal_date  # None → treated as start

    @property
    def display_name(self) -> str:
        return self.local_name or self.name

    @property
    def is_multi_day(self) -> bool:
        return self.start != self.end

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    def is_nominal_day(self, d: date) -> bool:
        """Is *d* the holiday's nominal date (假日当天)?"""
        return d == (self.nominal_date or self.start)

    def __repr__(self) -> str:
        return (f"HolidayPeriod({self.local_name!r}, "
                f"{self.start}~{self.end}, "
                f"nominal={self.nominal_date})")


# ── in-memory caches ────────────────────────────────────────────────
_holiday_cache: dict[tuple[str, int], list[HolidayEntry]] = {}
_period_cache: dict[tuple[str, int], list[HolidayPeriod]] = {}
_cache_lock = asyncio.Lock()

_NAGER_API = "https://date.nager.at/api/v3"
_TIMOR_API = "http://timor.tech/api/holiday/year"
_FETCH_TIMEOUT = 10.0

# ── 全局补充节日（所有国家共享，固定日期，API 可能不含） ──────────
# 格式: (month, day, localName_dict)
_GLOBAL_EXTRA_HOLIDAYS: list[tuple[int, int, dict[str, str]]] = [
    (2, 14, {
        'zh': '情人节', 'en': "Valentine's Day", 'ja': 'バレンタインデー',
        'ko': '발렌타인데이', 'ru': 'День святого Валентина',
    }),
    (12, 25, {
        'zh': '圣诞节', 'en': 'Christmas', 'ja': 'クリスマス',
        'ko': '크리스마스', 'ru': 'Рождество',
    }),
]


def _inject_global_extras(entries: list[HolidayEntry], year: int,
                          lang: str) -> list[HolidayEntry]:
    """Append global extra holidays if not already present in *entries*."""
    existing_dates = {e["date"] for e in entries}
    result = list(entries)
    for month, day, names in _GLOBAL_EXTRA_HOLIDAYS:
        iso = f"{year}-{month:02d}-{day:02d}"
        if iso in existing_dates:
            continue
        local = names.get(lang, names['en'])
        result.append({
            "date": iso,
            "localName": local,
            "name": names['en'],
            "_nominal": True,
        })
    return result


# 已知的中国法定节日名（用于从 timor API 的每日条目中归并 period 的展示名）
_CN_KNOWN_HOLIDAYS = {'元旦', '春节', '清明节', '劳动节', '端午节', '中秋节', '国庆节'}
# 春节 period 里初一~初七等条目需要映射回 "春节"；名义日期用初一
_CN_SPRING_NOMINAL = '初一'


# =====================================================================
#  Period grouping
# =====================================================================

def _build_periods(entries: list[HolidayEntry]) -> list[HolidayPeriod]:
    """Group entries into consecutive-day periods, tracking nominal dates."""
    date_map: dict[date, HolidayEntry] = {}
    nominal_dates: set[date] = set()
    for e in entries:
        try:
            d = date.fromisoformat(e["date"])
        except (ValueError, KeyError):
            continue
        if d not in date_map:
            date_map[d] = e
        # _nominal marker from CN supplement; for other countries every
        # entry is its own nominal date (default True)
        if e.get("_nominal", True):
            nominal_dates.add(d)

    if not date_map:
        return []

    sorted_dates = sorted(date_map.keys())
    periods: list[HolidayPeriod] = []

    group_start = sorted_dates[0]
    group_end = sorted_dates[0]
    group_entry = date_map[group_start]
    group_nominal: date | None = group_start if group_start in nominal_dates else None

    for d in sorted_dates[1:]:
        if (d - group_end).days == 1:
            group_end = d
            if d in nominal_dates and group_nominal is None:
                group_nominal = d
        else:
            periods.append(HolidayPeriod(
                name=group_entry["name"],
                local_name=group_entry["localName"],
                start=group_start, end=group_end,
                nominal_date=group_nominal,
            ))
            group_start = d
            group_end = d
            group_entry = date_map[d]
            group_nominal = d if d in nominal_dates else None

    periods.append(HolidayPeriod(
        name=group_entry["name"],
        local_name=group_entry["localName"],
        start=group_start, end=group_end,
        nominal_date=group_nominal,
    ))
    return periods


# =====================================================================
#  API fetching & caching  (lazy: first access warms all countries)
# =====================================================================

async def _fetch_nager(country: str, year: int) -> list[HolidayEntry]:
    """Fetch from Nager.Date API (JP / KR / RU / US etc.)."""
    url = f"{_NAGER_API}/PublicHolidays/{year}/{country}"
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, proxy=None, trust_env=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()  # type: ignore[return-value]


async def _fetch_cn(year: int) -> list[HolidayEntry]:
    """Fetch from timor.tech API for China — complete with 调休."""
    url = f"{_TIMOR_API}/{year}"
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, proxy=None, trust_env=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    raw = data.get("holiday", {})
    if not raw:
        return []

    # Build HolidayEntry list from timor data.
    # Only include holiday=true days (rest days).
    result: list[HolidayEntry] = []
    for _key, info in sorted(raw.items()):
        if not info.get("holiday"):
            continue  # 补班日，跳过
        entry_name: str = info.get("name", "")
        entry_date: str = info.get("date", "")
        wage: int = info.get("wage", 1)

        # Determine display name: map 除夕/初一~初七 → "春节"
        if entry_name in _CN_KNOWN_HOLIDAYS:
            display = entry_name
        elif entry_name in ('除夕', '初一', '初二', '初三', '初四', '初五', '初六', '初七'):
            display = '春节'
        else:
            display = entry_name

        # Nominal date: the canonical day of the holiday.
        # wage=3 entry whose display==holiday name, AND for 春节 specifically
        # the nominal is 初一 (not 除夕).
        is_nominal = False
        if wage == 3:
            if display == '春节':
                is_nominal = (entry_name == _CN_SPRING_NOMINAL)
            elif display in _CN_KNOWN_HOLIDAYS:
                # First wage=3 occurrence will be picked as nominal
                # by _build_periods (it takes the first one it sees)
                is_nominal = True

        result.append({
            "date": entry_date,
            "localName": display,
            "name": display,     # EN name not critical for CN
            "_nominal": is_nominal,
        })

    return result


_warmed = False


async def _warm_all_once() -> None:
    """Fetch all supported countries for the current year, once."""
    global _warmed
    if _warmed:
        return
    _warmed = True
    year = datetime.now().year

    async def _fetch_one(lang: str, country: str) -> None:
        key = (country, year)
        async with _cache_lock:
            if key in _period_cache:
                return
        try:
            data = await _fetch_cn(year) if country == 'CN' else await _fetch_nager(country, year)
            data = _inject_global_extras(data, year, lang)
            async with _cache_lock:
                _holiday_cache[key] = data
                _period_cache[key] = _build_periods(data)
            logger.info("Holiday cache loaded: %s/%d → %d entries, %d periods",
                        country, year, len(data), len(_period_cache[key]))
        except Exception as e:
            logger.info("Holiday cache fetch skipped for %s/%d: %s", country, year, e)

    await asyncio.gather(*[_fetch_one(l, c) for l, c in _LANG_TO_COUNTRY.items()])


async def _ensure_periods(country: str, year: int) -> list[HolidayPeriod]:
    """Return cached periods. On first call, bulk-fetches ALL countries."""
    await _warm_all_once()
    key = (country, year)
    async with _cache_lock:
        if key in _period_cache:
            return _period_cache[key]
    # Fallback single-country fetch
    try:
        # Reverse-lookup lang for global extras
        lang = next((l for l, c in _LANG_TO_COUNTRY.items() if c == country), 'en')
        data = await _fetch_cn(year) if country == 'CN' else await _fetch_nager(country, year)
        data = _inject_global_extras(data, year, lang)
        periods = _build_periods(data)
        async with _cache_lock:
            _holiday_cache[key] = data
            _period_cache[key] = periods
        return periods
    except Exception as e:
        logger.info("Holiday lazy-fetch failed for %s/%d: %s", country, year, e)
        return []


# =====================================================================
#  Holiday proximity lookup
# =====================================================================

class HolidayProximity:
    """Describes the nearest actionable holiday period relative to today."""
    __slots__ = ("period", "days_away")

    def __init__(self, period: HolidayPeriod, days_away: int) -> None:
        self.period = period
        self.days_away = days_away  # 0 = today is inside the period

    @property
    def is_today(self) -> bool:
        return self.days_away == 0


async def get_nearest_holiday(lang: str) -> HolidayProximity | None:
    """Find the closest holiday period that qualifies for a greeting hint.

    - "Today" matches if today falls *anywhere* inside a period.
    - Advance reminders (≤3 / 7 days) are based on the period's *start*.
    - In late December, also checks next year's periods so that early-
      January holidays (e.g. 元旦) are not missed near year boundaries.
    """
    country = _LANG_TO_COUNTRY.get(lang)
    if not country:
        return None

    today = date.today()
    periods = await _ensure_periods(country, today.year)

    # Near year end, also load next year so 7-day look-ahead can see Jan 1
    if today.month == 12 and today.day >= 24:
        next_year = await _ensure_periods(country, today.year + 1)
        periods = periods + next_year

    if not periods:
        return None

    best: HolidayProximity | None = None

    for period in periods:
        if period.contains(today):
            return HolidayProximity(period, 0)

        diff = (period.start - today).days
        if diff < 0:
            continue
        if diff <= 3 or diff == 7:
            candidate = HolidayProximity(period, diff)
            if best is None or diff < best.days_away:
                best = candidate

    return best


# =====================================================================
#  Consumption budget  —  persisted to disk
# =====================================================================
#
# File format (holiday_consumption.json):
# {
#   "<character>": {
#     "periods": {
#       "<period_start_iso>": {
#         "holiday": <remaining>,    // statutory day budget (3)
#         "period": <remaining>      // 调休 day budget (3)
#       }
#     },
#     "weekend_date": "YYYY-MM-DD",
#     "weekend": <remaining>         // daily reset (2)
#   }
# }

_CONSUMPTION_FILENAME = "holiday_consumption.json"
_consumption_data: dict[str, dict] = {}
_consumption_path: Path | None = None

_BUDGET_HOLIDAY = 3   # statutory day budget (shared across all 假日 days)
_BUDGET_PERIOD = 3    # 调休 day budget (shared across all 调休 days)
_BUDGET_WEEKEND = 2   # weekend budget (daily)


def _get_consumption_path() -> Path:
    global _consumption_path
    if _consumption_path is None:
        try:
            from utils.config_manager import get_config_manager
            cm = get_config_manager()
            _consumption_path = Path(str(cm.get_config_path(_CONSUMPTION_FILENAME)))
        except Exception:
            _consumption_path = Path(_CONSUMPTION_FILENAME)
    return _consumption_path


def _load_consumption() -> dict[str, dict]:
    global _consumption_data
    path = _get_consumption_path()
    try:
        if path.exists():
            _consumption_data = json.loads(path.read_text(encoding="utf-8"))
        else:
            _consumption_data = {}
    except Exception as e:
        logger.info("Holiday consumption load skipped: %s", e)
        _consumption_data = {}
    return _consumption_data


def _save_consumption() -> None:
    try:
        from utils.file_utils import atomic_write_json
        atomic_write_json(_get_consumption_path(), _consumption_data)
    except Exception as e:
        logger.debug("Holiday consumption save failed: %s", e)


def _get_char_record(character: str) -> dict:
    """Get or create the consumption record for a character."""
    rec = _consumption_data.get(character)
    if rec is None:
        rec = {"periods": {}}
        _consumption_data[character] = rec
    if "periods" not in rec:
        rec["periods"] = {}
    return rec


def _get_period_bucket(character: str, period: HolidayPeriod,
                       bucket: str, budget: int) -> int:
    """Get remaining uses for a specific bucket of a period."""
    rec = _get_char_record(character)
    period_key = period.start.isoformat()
    period_rec = rec["periods"].get(period_key)
    if period_rec is None:
        return budget  # not initialised = full budget
    return period_rec.get(bucket, budget)


def _consume_period_bucket(character: str, period: HolidayPeriod,
                           bucket: str, budget: int) -> bool:
    """Try to consume 1 use from a period bucket. Returns True if successful."""
    rec = _get_char_record(character)
    period_key = period.start.isoformat()
    if period_key not in rec["periods"]:
        rec["periods"][period_key] = {}
    period_rec = rec["periods"][period_key]

    remaining = period_rec.get(bucket)
    if remaining is None:
        # First use → initialise
        period_rec[bucket] = budget - 1
        _save_consumption()
        return True
    if remaining > 0:
        period_rec[bucket] = remaining - 1
        _save_consumption()
        return True
    return False


def try_consume_holiday(character: str, proximity: HolidayProximity) -> bool:
    """Try to consume 1 hint use for a holiday.

    Budget logic:
    - 假日当天 (nominal date, e.g. 10/1) → "holiday" bucket (3, independent)
    - 假期中非假日当天 (all other days in period) → "period" bucket (3, shared)
    - Advance reminder (≤3 / 7 days) → "period" bucket (3)
    """
    period = proximity.period

    if not proximity.is_today:
        return _consume_period_bucket(character, period, "period", _BUDGET_PERIOD)

    today = date.today()
    if period.is_nominal_day(today):
        return _consume_period_bucket(character, period, "holiday", _BUDGET_HOLIDAY)
    else:
        return _consume_period_bucket(character, period, "period", _BUDGET_PERIOD)


def try_consume_weekend(character: str) -> bool:
    """Try to consume 1 use for a weekend hint. Budget 2, resets daily."""
    rec = _get_char_record(character)
    today_iso = date.today().isoformat()

    if rec.get("weekend_date") != today_iso:
        # New day → reset
        rec["weekend_date"] = today_iso
        rec["weekend"] = _BUDGET_WEEKEND - 1
        _save_consumption()
        return True

    remaining = rec.get("weekend", 0)
    if remaining > 0:
        rec["weekend"] = remaining - 1
        _save_consumption()
        return True
    return False


# Startup: load persisted data
try:
    _load_consumption()
except Exception:
    pass


# =====================================================================
#  High-level hint builder
# =====================================================================

_HOLIDAY_HINT_TODAY: dict[str, str] = {
    'zh': '今天是{name}！这是一个特别的日子。',
    'en': 'Today is {name}! It is a special day.',
    'ja': '今日は{name}だ！特別な日だね。',
    'ko': '오늘은 {name}이다! 특별한 날이야.',
    'ru': 'Сегодня {name}! Это особенный день.',
}

_HOLIDAY_HINT_SOON: dict[str, str] = {
    'zh': '再过{days}天就是{name}假期了，可以期待一下。',
    'en': 'The {name} holiday is coming in {days} days — something to look forward to.',
    'ja': 'あと{days}日で{name}の休日だ。楽しみだね。',
    'ko': '{days}일 후면 {name} 연휴다. 기대되네.',
    'ru': 'Через {days} дней начнутся праздники {name} — есть чего ждать.',
}

_HOLIDAY_HINT_WEEK: dict[str, str] = {
    'zh': '这周就是{name}假期了哦。',
    'en': 'The {name} holiday is coming up this week.',
    'ja': '今週は{name}の休日がやってくるよ。',
    'ko': '이번 주에 {name} 연휴가 다가오고 있어.',
    'ru': 'На этой неделе начнутся праздники {name}.',
}

_WEEKEND_HINT: dict[str, str] = {
    'zh': '今天是周末，好好放松吧。',
    'en': 'It is the weekend — time to relax.',
    'ja': '今日は週末だ。ゆっくり過ごしてね。',
    'ko': '오늘은 주말이다. 푹 쉬어.',
    'ru': 'Сегодня выходной — время отдохнуть.',
}


async def get_holiday_or_weekend_hint(lang: str, character: str) -> str | None:
    """Return a holiday/weekend hint string if budget allows.

    Returns ``None`` if budget exhausted or no event — caller should
    produce **no** placeholder text.

    This is a convenience wrapper that **immediately** consumes the budget.
    For deferred consumption (e.g. greeting with abort checkpoints), use
    :func:`preview_holiday_or_weekend_hint` + :func:`commit_holiday_or_weekend_hint`.
    """
    proximity = await get_nearest_holiday(lang)

    if proximity is not None:
        if not try_consume_holiday(character, proximity):
            return None

        name = proximity.period.display_name
        lang_key = lang if lang in _HOLIDAY_HINT_TODAY else 'en'

        if proximity.is_today:
            tpl = _HOLIDAY_HINT_TODAY.get(lang_key, _HOLIDAY_HINT_TODAY['en'])
            return tpl.format(name=name)
        elif proximity.days_away <= 3:
            tpl = _HOLIDAY_HINT_SOON.get(lang_key, _HOLIDAY_HINT_SOON['en'])
            return tpl.format(name=name, days=proximity.days_away)
        else:
            tpl = _HOLIDAY_HINT_WEEK.get(lang_key, _HOLIDAY_HINT_WEEK['en'])
            return tpl.format(name=name)

    # No holiday → check weekend
    if datetime.now().weekday() >= 5:
        if not try_consume_weekend(character):
            return None
        return _WEEKEND_HINT.get(lang, _WEEKEND_HINT.get('en', ''))

    return None


# ── preview / commit (deferred consumption) ────────────────────────

def _has_holiday_budget(character: str, proximity: HolidayProximity) -> bool:
    """Check whether budget is available WITHOUT consuming or mutating state."""
    rec = _consumption_data.get(character)
    if rec is None:
        return True  # no record → full budget
    period_key = proximity.period.start.isoformat()
    period_rec = rec.get("periods", {}).get(period_key)
    if period_rec is None:
        return True  # not initialised → full budget

    if not proximity.is_today:
        return period_rec.get("period", _BUDGET_PERIOD) > 0
    if proximity.period.is_nominal_day(date.today()):
        return period_rec.get("holiday", _BUDGET_HOLIDAY) > 0
    return period_rec.get("period", _BUDGET_PERIOD) > 0


def _has_weekend_budget(character: str) -> bool:
    """Check whether weekend budget is available WITHOUT consuming or mutating state."""
    rec = _consumption_data.get(character)
    if rec is None:
        return True  # no record → full budget
    today_iso = date.today().isoformat()
    if rec.get("weekend_date") != today_iso:
        return True  # new day → full budget
    return rec.get("weekend", 0) > 0


def _build_holiday_hint_text(lang: str, proximity: HolidayProximity) -> str:
    """Build hint text from a proximity object (no side effects)."""
    name = proximity.period.display_name
    lang_key = lang if lang in _HOLIDAY_HINT_TODAY else 'en'
    if proximity.is_today:
        tpl = _HOLIDAY_HINT_TODAY.get(lang_key, _HOLIDAY_HINT_TODAY['en'])
        return tpl.format(name=name)
    elif proximity.days_away <= 3:
        tpl = _HOLIDAY_HINT_SOON.get(lang_key, _HOLIDAY_HINT_SOON['en'])
        return tpl.format(name=name, days=proximity.days_away)
    else:
        tpl = _HOLIDAY_HINT_WEEK.get(lang_key, _HOLIDAY_HINT_WEEK['en'])
        return tpl.format(name=name)


# Token: ("holiday", HolidayProximity) | ("weekend",)

async def preview_holiday_or_weekend_hint(
    lang: str, character: str,
) -> tuple[str | None, tuple | None]:
    """Get hint text WITHOUT consuming budget.

    Returns ``(hint_text, token)``.  Pass *token* to
    :func:`commit_holiday_or_weekend_hint` after successful delivery.
    ``(None, None)`` when no event or budget exhausted.
    """
    proximity = await get_nearest_holiday(lang)

    if proximity is not None:
        if not _has_holiday_budget(character, proximity):
            return None, None
        return _build_holiday_hint_text(lang, proximity), ("holiday", proximity)

    # No holiday → check weekend
    if datetime.now().weekday() >= 5:
        if not _has_weekend_budget(character):
            return None, None
        text = _WEEKEND_HINT.get(lang, _WEEKEND_HINT.get('en', ''))
        return text, ("weekend",)

    return None, None


def commit_holiday_or_weekend_hint(character: str, token: tuple) -> bool:
    """Consume budget for a previously previewed hint.

    Returns ``True`` if the budget was successfully decremented.
    Should be called only once per preview, after the hint was actually
    delivered to the user.
    """
    if not token:
        return False
    kind = token[0]
    if kind == "holiday":
        return try_consume_holiday(character, token[1])
    if kind == "weekend":
        return try_consume_weekend(character)
    return False


# =====================================================================
#  Memory-context helper (called from memory_server, no consumption)
# =====================================================================

def get_holiday_context_line(lang: str) -> str | None:
    """Return the holiday name if today is inside a holiday period.

    Independent of consumption — always returns the name if applicable.

    Note: this is a synchronous read of ``_period_cache`` and does NOT
    trigger the lazy warm.  In practice the cache is always populated
    before this is called, because ``trigger_greeting`` (which invokes
    ``get_nearest_holiday`` → ``_warm_all_once``) runs on every client
    connect, ahead of the ``new_dialog`` memory-context fetch.  If the
    cache happens to be cold (e.g. greeting was skipped), the function
    gracefully returns ``None`` — holiday context is omitted but nothing
    breaks.
    """
    country = _LANG_TO_COUNTRY.get(lang)
    if not country:
        return None
    today = date.today()
    periods = _period_cache.get((country, today.year), [])
    for period in periods:
        if period.contains(today):
            return period.display_name
    return None
