# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Temporal helpers for fact / reflection time decay + the stale block.

Schema v2 contract (shared by fact + reflection; see config.MEMORY_SCHEMA_VERSION_CURRENT):

- ``event_when_raw``: dict | None
    Raw LLM output (relative time, not ISO), shaped::

        {"start": {"offset": <int>, "unit": "minute|hour|day|week|month|year"},
         "end":   {"offset": <int>, "unit": "..."} | None}

    offset is relative to ``added_at`` (i.e. ``created_at``); negative = the
    past, positive = the future, 0 = "now". The LLM always outputs relative
    times, never ISO.
- ``event_start_at`` / ``event_end_at``: ISO str | None
    Computed by the system from ``event_when_raw`` + ``added_at`` and
    persisted, so consumers need not re-parse. For ``state`` / ``episode`` the
    caller falls back to ``added_at`` when missing; ``pattern`` may leave both
    None.

Reflection-specific field:

- ``temporal_scope``: 'pattern' | 'state' | 'episode' | 'past'
    - pattern: ongoing pattern / personality trait / long-term preference, never stale
    - state:   current ongoing situation (e.g. "stressed lately"), stale after STATE_PAST_DAYS
    - episode: one concrete event (e.g. "pulled an all-nighter today"), stale after EPISODE_PAST_DAYS
    - past:    legacy compatibility value (old data may carry it); rendered straight into the stale block

Legacy fallback: when ``schema_version < 2`` or missing, ``temporal_scope`` is
treated as ``pattern`` (conservative, no fade-out) until the slow recheck loop
upgrades the schema version.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta


ALLOWED_UNITS = frozenset({'minute', 'hour', 'day', 'week', 'month', 'year'})

# 月 / 年用平均日近似（reflection scope 不需要日历级别精度，差几小时无影响）
_UNIT_TO_SECONDS = {
    'minute': 60,
    'hour':   3600,
    'day':    86400,
    'week':   86400 * 7,
    'month':  86400 * 30,
    'year':   86400 * 365,
}

# Schema v1 (legacy) temporal_scope 值。render 时按 pattern 兜底。
LEGACY_TEMPORAL_SCOPES = frozenset({'current', 'ongoing'})
# Schema v2 active 标签（LLM 可主动输出的）
ACTIVE_TEMPORAL_SCOPES = frozenset({'pattern', 'state', 'episode'})
# 全部合法 temporal_scope（含 past 派生 / legacy / new + None）
ALL_TEMPORAL_SCOPES = (
    ACTIVE_TEMPORAL_SCOPES | LEGACY_TEMPORAL_SCOPES | {'past'}
)


def cooldown_elapsed(
    last_at_iso: str | None,
    cooldown_seconds: float,
    now: datetime | None = None,
) -> bool:
    """Dead-letter time-based self-healing check: has ``cooldown_seconds`` passed since the last failure?

    For the "frozen after N attempts" dead-letters — reflection synth / schema
    recheck / refine: once frozen, let one probe through per cooldown window so
    that a one-off persistent failure (model down / maintenance mode /
    read-only FS) self-heals after recovery. memory_review does **not** use
    this mechanism (it resets via fingerprint changes and should stay stopped
    while idle).

    Returns True = cooldown elapsed / no time basis; the probe may pass.

    Empty or unparsable ``last_at_iso`` → True: a missing timestamp is usually
    legacy data or pre-first-freeze leftovers; granting one probe is safer
    than freezing forever (a failed probe writes the timestamp back, and the
    next round follows the normal cooldown).
    """
    if not last_at_iso:
        return True
    try:
        last = datetime.fromisoformat(last_at_iso)
    except (ValueError, TypeError):
        return True
    if now is None:
        now = datetime.now()
    # aware/naive 归一：写入路径都用 datetime.now()（naive），但迁移 / import
    # 数据可能塞进 +00:00 / Z 的 aware ISO，直接和 naive now 相减会抛
    # TypeError 中断冷却判定。复用全项目 tz 归一口径 to_naive_local（保瞬时）。
    last = to_naive_local(last)
    now = to_naive_local(now)
    return (now - last).total_seconds() >= cooldown_seconds


# ── offset spec 解析 ──────────────────────────────────────────────────

def _validate_offset_spec(spec: object) -> dict | None:
    """Validate ``{'offset': int, 'unit': str}``. Returns canonical dict or None.

    Tolerant to ``offset=0`` (= now) and negative offsets (= the past).
    """
    if not isinstance(spec, dict):
        return None
    raw_unit = spec.get('unit')
    if raw_unit not in ALLOWED_UNITS:
        return None
    try:
        offset = int(spec.get('offset'))
    except (TypeError, ValueError):
        return None
    return {'offset': offset, 'unit': raw_unit}


def normalize_event_when(raw: object) -> dict | None:
    """Validate LLM-provided ``event_when`` payload.

    Returns canonical ``{'start': spec|None, 'end': spec|None}`` where at
    least one of start/end is non-None. Returns None if both invalid.
    """
    if not isinstance(raw, dict):
        return None
    start = _validate_offset_spec(raw.get('start'))
    end = _validate_offset_spec(raw.get('end'))
    if start is None and end is None:
        return None
    return {'start': start, 'end': end}


def _offset_to_iso(anchor_iso: str, spec: dict | None) -> str | None:
    """Apply ``{offset, unit}`` to ``anchor_iso``. Returns ISO or None."""
    if not spec:
        return None
    try:
        anchor = datetime.fromisoformat(anchor_iso)
    except (TypeError, ValueError):
        return None
    secs = _UNIT_TO_SECONDS.get(spec['unit'])
    if secs is None:
        return None
    return (anchor + timedelta(seconds=secs * spec['offset'])).isoformat()


def compute_event_timestamps(
    event_when_raw: dict | None,
    added_at_iso: str,
    *,
    fallback_start: bool = True,
    fallback_end: bool = False,
) -> tuple[str | None, str | None]:
    """Compute ``(event_start_at, event_end_at)`` from raw + anchor.

    Fallback semantics (specified by the caller at write time):

    - ``pattern``: fallback_start=True, fallback_end=False
        (an ongoing pattern may lack an end; a missing start still falls back
        to added_at for uniform time labels)
    - ``state`` / ``episode``: fallback_start=True, fallback_end=True
        (the TTL judgment needs an end; a missing end takes the start value =
        "the event ends right then")
    - ``fact``: usually fallback_start=True, fallback_end=False
        (facts have no temporal_scope; the event end is optional)
    """
    norm = normalize_event_when(event_when_raw)
    start_iso = _offset_to_iso(added_at_iso, norm['start']) if norm else None
    end_iso = _offset_to_iso(added_at_iso, norm['end']) if norm else None
    if start_iso is None and fallback_start:
        start_iso = added_at_iso
    if end_iso is None and fallback_end:
        # end 兜底优先用 start，再回退 added_at
        end_iso = start_iso or added_at_iso
    return start_iso, end_iso


# ── past 派生判定 ─────────────────────────────────────────────────────

def _parse_iso_safe(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def to_naive_local(dt: datetime | None) -> datetime | None:
    """aware datetime → local naive (astimezone to local first, then strip tz —
    preserving the instant rather than the wall clock); naive / None returned
    as-is.

    Project-wide tz normalization: this repo persists timestamps as naive
    local-clock values, but import / migration paths may inject aware values
    with ``+00:00`` / ``Z``. A bare ``replace(tzinfo=None)`` would treat the
    UTC wall clock as local, shifting everything by an offset on non-UTC
    machines and mis-bucketing day-level windows/sorts at day boundaries
    (Codex). Convert uniformly here.
    """
    if dt is not None and dt.tzinfo is not None:
        try:
            return dt.astimezone().replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            # 边界 aware 值（如 0001-01-01+14:00 / 9999-12-31-14:00）astimezone
            # 加减 offset 会越过 datetime.min/max 抛 OverflowError，不能让它冒到
            # parse_time_window / 渲染链路（Codex）。退而求其次直接剥 tz（保墙钟）。
            return dt.replace(tzinfo=None)
    return dt


def _past_anchor(entry: dict) -> datetime | None:
    """Time anchor for past judgment (end > start > added > created).

    The priority matches ``time_since_label`` — past is "how long since the
    anchor", and rendering computes "how long ago" with the same anchor,
    keeping the two consistent.
    """
    return (
        _parse_iso_safe(entry.get('event_end_at'))
        or _parse_iso_safe(entry.get('event_start_at'))
        or _parse_iso_safe(entry.get('added_at'))
        or _parse_iso_safe(entry.get('created_at'))
    )


def is_past_for_render(entry: dict, now: datetime | None = None) -> bool:
    """Derive from temporal_scope + event timestamps whether the entry goes into the stale block.

    Rules:

    - ``stored temporal_scope == 'past'`` (legacy or newly written) → True
    - ``state``    + (event_end_at or event_start_at) older than STATE_PAST_DAYS → True
    - ``episode``  + same, older than EPISODE_PAST_DAYS → True
    - ``pattern``  → False (ongoing patterns never go stale unless explicitly stored as past)
    - ``current`` / ``ongoing`` / None (legacy v1) → False (falls back to
      pattern until the slow loop re-judges)
    """
    from config import MEMORY_STATE_PAST_DAYS, MEMORY_EPISODE_PAST_DAYS
    if now is None:
        now = datetime.now()
    now = to_naive_local(now)
    ts = entry.get('temporal_scope')
    if ts == 'past':
        return True
    ttl_by_scope = {
        'state':   MEMORY_STATE_PAST_DAYS,
        'episode': MEMORY_EPISODE_PAST_DAYS,
    }
    ttl_days = ttl_by_scope.get(ts)
    if ttl_days is None:
        return False
    # to_naive_local：anchor 可能是 import/迁移写进来的 aware 值，和 naive
    # now 相减会 TypeError 把过时判定/渲染链路打断（CodeRabbit）。
    anchor = to_naive_local(_past_anchor(entry))
    if anchor is None:
        return False
    return (now - anchor).total_seconds() > ttl_days * 86400


# ── 距今多久 label（per Q-α: 0-6d 天 / 7-29d 周 / 30d+ 月） ──────────

def days_since(anchor_iso: str | None, now: datetime | None = None) -> int | None:
    """Return integer days from anchor to now (floored; 0 days is valid).

    anchor may be tz-aware (import / migration paths write ``...+00:00`` /
    ``...Z``) while ``now`` is the naive local clock — subtracting directly
    would ``TypeError``. Convert an aware anchor to local then strip tz, leave
    naive ones alone, so both sides are naive and subtractable (Codex).
    """
    if now is None:
        now = datetime.now()
    anchor = to_naive_local(_parse_iso_safe(anchor_iso))
    if anchor is None:
        return None
    now = to_naive_local(now)
    return max(0, int((now - anchor).total_seconds() // 86400))


_TIME_LABELS = {
    'zh': {'now': '当下',  'day': '{n} 天前',   'week': '{n} 周前',   'month': '{n} 月前'},
    'en': {'now': 'now',    'day': '{n}d ago',   'week': '{n}w ago',   'month': '{n}mo ago'},
    'ja': {'now': '今',    'day': '{n} 日前',   'week': '{n} 週間前', 'month': '{n} ヶ月前'},
    'ko': {'now': '지금',  'day': '{n}일 전',   'week': '{n}주 전',   'month': '{n}개월 전'},
    'ru': {'now': 'сейчас', 'day': '{n} дн назад', 'week': '{n} нед назад', 'month': '{n} мес назад'},
    'es': {'now': 'ahora', 'day': 'hace {n}d',  'week': 'hace {n}sem', 'month': 'hace {n}mes'},
    'pt': {'now': 'agora', 'day': 'há {n}d',   'week': 'há {n}sem',  'month': 'há {n}mês'},
}


def time_since_label(
    anchor_iso: str | None,
    *,
    now: datetime | None = None,
    lang: str = 'zh',
) -> str:
    """Format the [time since] label (semantics decided in Q-α).

    - 0 days    → "当下" (localized "right now")
    - 1-6 days  → "{n} 天前" (n days ago)
    - 7-29 days → "{n // 7} 周前" (weeks ago)
    - 30+ days  → "{n // 30} 月前" (months ago)

    Returns an empty string when the anchor cannot be parsed.
    """  # noqa: DOCSTRING_CJK
    days = days_since(anchor_iso, now=now)
    if days is None:
        return ""
    table = _TIME_LABELS.get(lang) or _TIME_LABELS['zh']
    if days == 0:
        return table['now']
    if days < 7:
        return table['day'].format(n=days)
    if days < 30:
        return table['week'].format(n=days // 7)
    return table['month'].format(n=days // 30)


# ── 时间窗口解析（recall_memory 的 time 参数 / 按时间回溯反思） ────────

def _token_window(token: str) -> tuple[datetime, datetime] | None:
    """Parse a single time token into a [start, end) half-open interval (naive local clock).

    Granularity follows the token shape (fine to coarse):
      - ``YYYY-MM-DDTHH`` / ``YYYY-MM-DD HH`` → the whole hour [HH:00, HH+1:00)
      - ISO with minutes/seconds (``2026-05-01T14:30:00``) → floored to its hour
      - ``YYYY-MM-DD`` → that day [d 00:00, next day 00:00)
      - ``YYYY-MM``    → the whole month [month start, next month start)
      - ``YYYY``       → the whole year [year start, next year start)

    Returns None when unparsable.
    """
    token = (token or "").strip()
    if not token:
        return None

    def _next_month(x: datetime) -> datetime:
        return x.replace(year=x.year + 1, month=1) if x.month == 12 \
            else x.replace(month=x.month + 1)

    def _commit(start: datetime, end_fn) -> tuple[datetime, datetime] | None:
        # 一旦某个格式 strptime 命中，就锁定该粒度：右界运算（+1 小时/天 /
        # 年月进位）越过 datetime.max 抛 OverflowError/ValueError 时返回 None，
        # 不再降级到更细粒度（否则 9999-12-31 会被下面的小时兜底误救成 1 小时
        # 窗），也不把异常冒到上层（Codex）。
        try:
            return (start, end_fn(start))
        except (ValueError, OverflowError):
            return None

    # 精确格式从粗到细试，strptime 命中即 _commit 锁定粒度。
    for fmt, end_fn in (
        ('%Y-%m-%d',     lambda x: x + timedelta(days=1)),    # 整日
        ('%Y-%m-%dT%H',  lambda x: x + timedelta(hours=1)),   # 整点小时（T）
        ('%Y-%m-%d %H',  lambda x: x + timedelta(hours=1)),   # 整点小时（空格）
        ('%Y-%m',        _next_month),                        # 整月
        ('%Y',           lambda x: x.replace(year=x.year + 1)),  # 整年
    ):
        try:
            start = datetime.strptime(token, fmt)
        except ValueError:
            continue
        return _commit(start, end_fn)

    # 兜底：带分秒的完整 ISO（含 tz）→ 向下取整到所在那一小时，精度到小时。
    parsed = to_naive_local(_parse_iso_safe(token))
    if parsed is not None:
        hour = parsed.replace(minute=0, second=0, microsecond=0)
        return _commit(hour, lambda x: x + timedelta(hours=1))
    return None


def parse_time_window(spec: str | None) -> tuple[datetime, datetime] | None:
    """Parse recall's ``time`` argument into a [start, end) half-open interval.

    Supports a single token (see ``_token_window``; granularity down to the
    hour, e.g. ``2026-05-01T14``) or a range — two tokens separated by ``/`` or
    ``..``, the window being the union of both ends [min(start), max(end)), so
    ``2026-05-01/2026-05-07`` is the full week including both ends,
    ``2026-05/2026-06`` is two whole months, ``2026-05-01T09/2026-05-01T18`` is
    9:00 to 19:00 that day. If either end fails to parse, the whole call
    returns None (callers then fall back to semantic recall).
    """
    if not isinstance(spec, str):
        return None
    s = spec.strip()
    if not s:
        return None
    sep = '/' if '/' in s else ('..' if '..' in s else None)
    if sep:
        left, _, right = s.partition(sep)
        lw = _token_window(left)
        rw = _token_window(right)
        if lw is None or rw is None:
            return None
        return (min(lw[0], rw[0]), max(lw[1], rw[1]))
    return _token_window(s)


# ── weighted followup sampling (Q1) ───────────────────────────────────

def weighted_sample_no_replace(
    items: list,
    weights: list[float],
    k: int,
    *,
    rng: random.Random | None = None,
) -> list:
    """Weighted sampling of k items without replacement.

    Uses the Efraimidis–Spirakis reservoir algorithm (compute
    ``random ** (1/w)`` as the key per item, take the k largest) — O(n), no
    repeated weight renormalization.

    - Items with ``weights[i] <= 0`` are forcibly excluded (avoiding
      ZeroDivision / negative weights).
    - ``k >= len(items)`` returns directly (still sorted by key, so callers
      get the same ordering as a weighted pick-1).
    """
    if not items:
        return []
    if rng is None:
        rng = random.Random()
    filtered = [(it, w) for it, w in zip(items, weights) if w > 0]
    if not filtered:
        return []
    keyed = []
    for it, w in filtered:
        u = rng.random()
        # u can be 0 with vanishingly small probability; clamp to epsilon to
        # avoid log(0).
        if u <= 0:
            u = 1e-12
        key = math.log(u) / w  # equiv to u ** (1/w) sort key (monotonic)
        keyed.append((key, it))
    keyed.sort(key=lambda kv: kv[0], reverse=True)
    return [it for _, it in keyed[:k]]
