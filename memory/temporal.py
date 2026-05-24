# -*- coding: utf-8 -*-
"""Temporal helpers for fact / reflection 时间衰减 + 过时 block.

Schema v2 contract (fact + reflection 共用，详见 config.MEMORY_SCHEMA_VERSION_CURRENT)：

- ``event_when_raw``: dict | None
    LLM 原始输出（相对时间，不是 ISO），结构::

        {"start": {"offset": <int>, "unit": "minute|hour|day|week|month|year"},
         "end":   {"offset": <int>, "unit": "..."} | None}

    offset 是相对 ``added_at``（即 ``created_at``）的偏移量，负数表示过去、
    正数表示未来、0 表示"当下"。LLM 一律输出相对时间，绝不要求 ISO。
- ``event_start_at`` / ``event_end_at``: ISO str | None
    系统从 ``event_when_raw`` + ``added_at`` 计算后写盘，便于消费侧无需重新
    解析。``state`` / ``episode`` 在缺失时由调用方兜底成 ``added_at``；
    ``pattern`` 允许两个都为 None。

Reflection 专用字段：

- ``temporal_scope``: 'pattern' | 'state' | 'episode' | 'past'
    - pattern: 持续模式 / 性格特质 / 长期偏好，永不过时
    - state:   当前持续情境（如"最近压力大"），超 STATE_PAST_DAYS 后过时
    - episode: 一次具体事件（如"今天通宵"），超 EPISODE_PAST_DAYS 后过时
    - past:    历史兼容值（legacy 旧数据可能存了），render 时直接进过时 block

旧数据兜底：``schema_version < 2`` 或缺失时，``temporal_scope`` 视为
``pattern``（保守不淡出），等慢速重判循环升版本号修正。
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
    """dead-letter 时间冷却自愈判定：距上次失败是否已过 ``cooldown_seconds``。

    用于 reflection synth / schema recheck / refine 这些"达 N 次冻结"的
    dead-letter——冻结后每过冷却窗口放行一次 probe，让一次性持续故障
    （模型宕机 / 维护态 / 只读 FS）恢复后自愈。memory_review **不**用本机制
    （它靠 fingerprint 变化复位，挂机期间应一直停）。

    返回 True = 冷却已过 / 无时间基准，可放行 probe。

    ``last_at_iso`` 为空或无法解析 → 返回 True：没有时间戳通常是旧数据或
    首次冻结前的遗留，给一次 probe 比永久冻死更安全（probe 失败会写回
    时间戳，下次就按正常冷却计时）。
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

    Tolerant to ``offset=0`` (= 当下) and negative offsets (= 过去)。
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

    Fallback semantics（写入时由调用方指定）:

    - ``pattern``: fallback_start=True, fallback_end=False
        （持续模式可无 end；start 缺失也兜底成 added_at 以便统一时间标签）
    - ``state`` / ``episode``: fallback_start=True, fallback_end=True
        （TTL 判定需要 end；end 缺失时与 start 同值 = "事件当下结束"）
    - ``fact``: 通常 fallback_start=True, fallback_end=False
        （fact 没有 temporal_scope，事件 end 可选）
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
    """aware datetime → 本地 naive（先 astimezone 转本地再剥 tz，保留瞬时
    而非墙钟）；naive / None 原样返回。

    全项目 tz 归一口径：本仓库时间戳都按 naive 本地时钟写盘，但 import /
    迁移路径可能塞进 ``+00:00`` / ``Z`` 的 aware 值。直接 ``replace(tzinfo
    =None)`` 会把 UTC 墙钟当本地用，在非 UTC 机器上整体偏移一个 offset，
    害 day 级窗口/排序在日界处归错天（Codex）。这里统一转换。
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
    """past 判定用的时间锚点（end > start > added > created）。

    优先级和 ``time_since_label`` 一致——past 是"距 anchor 多久"，渲染时也
    用同一 anchor 算"多久前"，保证两者口径一致。
    """
    return (
        _parse_iso_safe(entry.get('event_end_at'))
        or _parse_iso_safe(entry.get('event_start_at'))
        or _parse_iso_safe(entry.get('added_at'))
        or _parse_iso_safe(entry.get('created_at'))
    )


def is_past_for_render(entry: dict, now: datetime | None = None) -> bool:
    """根据 temporal_scope + event 时间戳推导是否进过时 block。

    Rules:

    - ``stored temporal_scope == 'past'`` (legacy 或新写入) → True
    - ``state``    + (event_end_at or event_start_at) 距今 > STATE_PAST_DAYS → True
    - ``episode``  + 同上 > EPISODE_PAST_DAYS → True
    - ``pattern``  → False（持续模式永不过时，除非显式存 past）
    - ``current`` / ``ongoing`` / None (legacy v1) → False（按 pattern 兜底，
      等慢速循环重判）
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
    """Return integer days from anchor to now（向下取整，0 day 也合法）。

    anchor 可能是 tz-aware（import / 迁移路径会写 ``...+00:00`` / ``...Z``），
    而 ``now`` 是 naive 本地时钟——直接相减会 ``TypeError``。这里把 aware
    anchor 先转本地再剥 tz，naive 的照旧，保证两侧同为 naive 可减（Codex）。
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
    """格式化 [距今多久] 标签（Q-α 决策的口径）。

    - 0 天     → "当下"（locale 化）
    - 1-6 天   → "{n} 天前"
    - 7-29 天  → "{n // 7} 周前"
    - 30 天+   → "{n // 30} 月前"

    anchor 无法解析时返回空字符串。
    """
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
    """把单个时间 token 解析成 [start, end) 半开区间（naive 本地时钟）。

    粒度由 token 形态决定（从细到粗）：
      - ``YYYY-MM-DDTHH`` / ``YYYY-MM-DD HH`` → 整点小时 [HH:00, HH+1:00)
      - 带分秒的 ISO（``2026-05-01T14:30:00``）→ 向下取整到所在那一小时
      - ``YYYY-MM-DD`` → 当日 [d 00:00, 次日 00:00)
      - ``YYYY-MM``    → 整月 [月初, 次月初)
      - ``YYYY``       → 整年 [年初, 次年初)

    无法解析返回 None。
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
    """把 recall 的 ``time`` 参数解析成 [start, end) 半开区间。

    支持单 token（见 ``_token_window``，粒度可到小时如 ``2026-05-01T14``）或
    区间——用 ``/`` 或 ``..`` 分隔两个 token，窗口取两端的并集
    [min(start), max(end))，所以 ``2026-05-01/2026-05-07`` 是含两端的整周、
    ``2026-05/2026-06`` 是两个整月、``2026-05-01T09/2026-05-01T18`` 是当天
    9 点到 19 点。任一端解析失败则整体返回 None（调用方据此回退语义检索）。
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
    """无放回加权抽样 k 个。

    使用 Efraimidis–Spirakis 的 reservoir 算法（每条计算 ``random ** (1/w)``
    作为 key，取最大的 k 个）—— O(n) 无需逐次重新归一化权重。

    - ``weights[i] <= 0`` 的条目被强制排除（避免 ZeroDivision / 负权）。
    - ``k >= len(items)`` 直接返回（仍按 key 排序，保证调用方拿到的顺序
      和加权选 1 时一致）。
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
