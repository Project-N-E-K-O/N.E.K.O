# -*- coding: utf-8 -*-
"""
Evidence math and derived-status helpers (docs/design/memory-evidence-rfc.md).

本模块只放纯函数 + 背景辅助，**不放常量**（所有常量统一在
`config/__init__.py`，RFC §3.8.2 与 §7 全局约束）。

核心设计（RFC §3.1.1 / §3.5.1）：
- Decay 是**读时现算**，不是 state transition；调用方每次读 entry 时
  通过 `evidence_score()` / `effective_*()` 拿到 effective 值。
- rein 和 disp 有各自独立的时间戳 `rein_last_signal_at` /
  `disp_last_signal_at`；一侧 signal 不影响另一侧的衰减时钟（§3.1.1
  末段的场景说明）。
- `protected=True` 的 entry（character_card 来源）evidence_score 返回
  `float('inf')`——永不被淘汰 / 归档 / budget 挤出。
"""
from __future__ import annotations

from datetime import datetime

from config import (
    EVIDENCE_ARCHIVE_THRESHOLD,
    EVIDENCE_CONFIRMED_THRESHOLD,
    EVIDENCE_DISP_HALF_LIFE_DAYS,
    EVIDENCE_PROMOTED_THRESHOLD,
    EVIDENCE_REIN_HALF_LIFE_DAYS,
    USER_FACT_REINFORCE_COMBO_BONUS,
    USER_FACT_REINFORCE_COMBO_THRESHOLD,
)

__all__ = [
    "effective_reinforcement",
    "effective_disputation",
    "evidence_score",
    "derive_status",
    "maybe_mark_sub_zero",
    "initial_reinforcement_from_importance",
    "compute_evidence_snapshot",
]

# Note: RFC §3.10 funnel analytics (`funnel_counts`) lives in the sibling
# module `memory.evidence_analytics`, NOT here.  It does file IO and pulls
# in `utils.config_manager`; per RFC §3.8.2 / §7 this module must stay
# pure-function with no stateful imports, so callers must
#   from memory.evidence_analytics import funnel_counts
# directly.


# Source value shared by `aapply_signal` dispatchers to trigger combo logic.
# Kept as a string to avoid circular import with memory/event_log.py
# where EVIDENCE_SOURCE_USER_FACT is defined (evidence.py is loaded earlier
# in the memory package graph).
_SOURCE_USER_FACT = "user_fact"


# 用 tuple 定义更紧凑的分档表，避免 if 链；[(importance_threshold, rein), ...]
# 含义：importance >= threshold → 得到对应的 initial rein seed；
# 从高到低第一条命中的即为结果，其余跳过。
# RFC §3.1.2 本来让所有新 reflection 从 score=0 起步；这里开了个例外：
# 通过 fact importance 给"关键节点"型 reflection（昵称/身份/用户明确说请记住）
# 一个初始鼓励，使其可以用更少 user_fact reinforces 穿越 CONFIRMED/PROMOTED。
# 阈值梯度（用户指定）：10→0.8, 9→0.6, 8→0.4, 7→0.2, ≤6→0.0
_IMPORTANCE_TO_INITIAL_REIN: tuple[tuple[int, float], ...] = (
    (10, 0.8),
    (9, 0.6),
    (8, 0.4),
    (7, 0.2),
)


def initial_reinforcement_from_importance(max_importance: int) -> float:
    """Map the MAX importance among a reflection's source facts to an
    initial `reinforcement` seed.

    Rationale: high-importance facts (nicknames, IDs, critical relationship
    markers, or user-flagged "请记住 X") should fast-track through the
    pending→confirmed→promoted pipeline without waiting for multiple
    natural reinforcement cycles. Low-importance noise still starts at 0.

    Thresholds are MAX-based (not avg / sum) because one high-importance
    fact in the batch is enough to mark the synthesized reflection as
    important; averaging would dilute that signal.
    """
    try:
        imp = int(max_importance)
    except (ValueError, TypeError):
        return 0.0
    for threshold, seed in _IMPORTANCE_TO_INITIAL_REIN:
        if imp >= threshold:
            return seed
    return 0.0


def _age_days(ts: str | None, now: datetime) -> float:
    """Return age in days for an ISO8601 timestamp; 0 if ts is falsy/invalid."""
    if not ts:
        return 0.0
    try:
        parsed = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return 0.0
    delta = (now - parsed).total_seconds()
    if delta <= 0:
        # 时钟回拨或时间戳来自未来（迁移 / 测试）：age=0 不衰减
        return 0.0
    return delta / 86400


def effective_reinforcement(entry: dict, now: datetime) -> float:
    """Compute decayed reinforcement value at `now`.

    独立时间戳：`rein_last_signal_at` 只在 reinforcement 侧被触动时重置；
    `disp` 事件不影响本函数计算（§3.1.1）。
    """
    r = float(entry.get("reinforcement", 0.0) or 0.0)
    if r == 0.0:
        return r
    age = _age_days(entry.get("rein_last_signal_at"), now)
    if age == 0.0:
        return r
    return r * (0.5 ** (age / EVIDENCE_REIN_HALF_LIFE_DAYS))


def effective_disputation(entry: dict, now: datetime) -> float:
    """Compute decayed disputation value at `now`. Symmetric to rein."""
    d = float(entry.get("disputation", 0.0) or 0.0)
    if d == 0.0:
        return d
    age = _age_days(entry.get("disp_last_signal_at"), now)
    if age == 0.0:
        return d
    return d * (0.5 ** (age / EVIDENCE_DISP_HALF_LIFE_DAYS))


def evidence_score(entry: dict, now: datetime) -> float:
    """Net evidence strength (+rein -disp) at `now`.

    `protected=True` 的 entry 返回 `float('inf')`——character_card 条目
    永不被归档 / 预算淘汰，语义见 §3.5.7。
    """
    if entry.get("protected"):
        return float("inf")
    return effective_reinforcement(entry, now) - effective_disputation(entry, now)


def derive_status(entry: dict, now: datetime) -> str:
    """Map evidence_score to derived status tier (§3.1.4 table).

    Returns one of: 'archive_candidate' | 'pending' | 'confirmed' | 'promoted'.
    Note: 'archive_candidate' is a DERIVED semantic label, not a storage
    field; actual archival requires `sub_zero_days >= EVIDENCE_ARCHIVE_DAYS`
    (§3.5.3), which is orthogonal.
    """
    s = evidence_score(entry, now)
    if s >= EVIDENCE_PROMOTED_THRESHOLD:
        return "promoted"
    if s >= EVIDENCE_CONFIRMED_THRESHOLD:
        return "confirmed"
    if s <= EVIDENCE_ARCHIVE_THRESHOLD:
        return "archive_candidate"
    return "pending"


def compute_evidence_snapshot(
    entry: dict, delta: dict, now_iso: str, source: str,
) -> dict:
    """Apply a delta to an entry's evidence and return the full-snapshot
    payload for the outgoing event.

    Shared by `PersonaManager.aapply_signal` and
    `ReflectionEngine.aapply_signal` — both need identical semantics and
    the same combo logic for user_fact reinforces.

    Independent clocks (RFC §3.1.1):
      只重置被触动的一侧的 last_signal_at。
    Disputation非负：
      §3.1.5 只有 reinforcement 允许为负，disputation 永远 >= 0。
    User_fact combo bonus (RFC §3.1.8)：
      base rein delta 0.5；累计 user_fact reinforce count 超过
      USER_FACT_REINFORCE_COMBO_THRESHOLD（默认 2）后，每条新 signal
      额外加 USER_FACT_REINFORCE_COMBO_BONUS（默认 0.5）。
      只对 `source='user_fact'` + `delta.reinforcement > 0` 触发。
      Count 永不清零（combo 是终生累计，配合 §3.5.3 "归档更积极"对称
      思路：正向累计也终生生效，但 decay 仍按 rein_last_signal_at 发生）。

    Returns a dict containing:
      reinforcement, disputation, rein_last_signal_at, disp_last_signal_at,
      sub_zero_days, user_fact_reinforce_count
    """
    rein_delta = float(delta.get("reinforcement", 0.0) or 0.0)
    disp_delta = float(delta.get("disputation", 0.0) or 0.0)
    new_rein = float(entry.get("reinforcement", 0.0) or 0.0) + rein_delta
    new_disp = float(entry.get("disputation", 0.0) or 0.0) + disp_delta
    if new_disp < 0:
        new_disp = 0.0

    # Combo logic: only on user_fact reinforces (indirect positive signal).
    # Counter increments BEFORE threshold check so the threshold reads as
    # "this is the N-th signal"；第 3 条起满足 count > 2 条件。
    new_count = int(entry.get("user_fact_reinforce_count", 0) or 0)
    if source == _SOURCE_USER_FACT and rein_delta > 0:
        new_count += 1
        if new_count > USER_FACT_REINFORCE_COMBO_THRESHOLD:
            new_rein += USER_FACT_REINFORCE_COMBO_BONUS

    return {
        "reinforcement": new_rein,
        "disputation": new_disp,
        "rein_last_signal_at": now_iso if rein_delta != 0.0
            else entry.get("rein_last_signal_at"),
        "disp_last_signal_at": now_iso if disp_delta != 0.0
            else entry.get("disp_last_signal_at"),
        "sub_zero_days": int(entry.get("sub_zero_days", 0) or 0),
        "user_fact_reinforce_count": new_count,
    }


def maybe_mark_sub_zero(entry: dict, now: datetime) -> bool:
    """Background-loop helper; called by `_periodic_archive_sweep_loop`.

    PR-1 SCAFFOLD: the signature is frozen here for forward-compat; real
    归档触发逻辑（§3.5.3）在 PR-2 补完 + wire 进背景循环。当前实现只做：
    - `score >= 0` → 不动（累计不回退，"归档更积极"——§3.5.3）
    - `score < 0` 且今天还没累计过 → `sub_zero_days += 1` +
      `sub_zero_last_increment_date = today`

    Returns True if `sub_zero_days` was incremented this call.

    Protected 条目：永远 return False 不累加（evidence_score 返回 inf）。
    """
    if entry.get("protected"):
        return False
    score = evidence_score(entry, now)
    if score >= 0:
        return False
    last_incr = entry.get("sub_zero_last_increment_date")
    today = now.date().isoformat()
    if last_incr == today:
        return False
    entry["sub_zero_days"] = int(entry.get("sub_zero_days", 0) or 0) + 1
    entry["sub_zero_last_increment_date"] = today
    return True
