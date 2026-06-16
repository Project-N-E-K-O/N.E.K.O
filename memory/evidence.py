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

"""
Evidence math and derived-status helpers (docs/design/memory-evidence-rfc.md).

This module holds pure functions + background helpers only — **no constants**
(all constants live in `config/__init__.py`, RFC §3.8.2 and the §7 global
constraints).

Core design (RFC §3.1.1 / §3.5.1):
- Decay is **computed at read time**, not a state transition; each time a
  caller reads an entry it obtains effective values via `evidence_score()` /
  `effective_*()`.
- rein and disp have independent timestamps `rein_last_signal_at` /
  `disp_last_signal_at`; a signal on one side does not affect the other
  side's decay clock (scenario described at the end of §3.1.1).
- Entries with `protected=True` (character_card source) get `float('inf')`
  from evidence_score — never evicted / archived / squeezed out by budget.
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
    markers, or user-flagged "请记住 X" / "remember X") should fast-track through
    the pending→confirmed→promoted pipeline without waiting for multiple
    natural reinforcement cycles. Low-importance noise still starts at 0.

    Thresholds are MAX-based (not avg / sum) because one high-importance
    fact in the batch is enough to mark the synthesized reflection as
    important; averaging would dilute that signal.
    """  # noqa: DOCSTRING_CJK
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

    Independent timestamps: `rein_last_signal_at` is only reset when the
    reinforcement side is touched; `disp` events do not affect this
    computation (§3.1.1).
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

    Entries with `protected=True` return `float('inf')` — character_card
    entries are never archived / budget-evicted; semantics in §3.5.7.
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
      only the touched side's last_signal_at is reset.
    Disputation is non-negative:
      per §3.1.5 only reinforcement may go negative; disputation is always >= 0.
    User_fact combo bonus (RFC §3.1.8):
      base rein delta 0.5; once the cumulative user_fact reinforce count
      exceeds USER_FACT_REINFORCE_COMBO_THRESHOLD (default 2), each new
      signal adds an extra USER_FACT_REINFORCE_COMBO_BONUS (default 0.5).
      Triggers only for `source='user_fact'` + `delta.reinforcement > 0`.
      The count is never reset (the combo accumulates for life, symmetric
      with §3.5.3's "archive more aggressively" idea: positive accumulation
      is also lifelong, while decay still follows rein_last_signal_at).

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

    PR-1 SCAFFOLD: the signature is frozen here for forward-compat; the real
    archive-trigger logic (§3.5.3) lands in PR-2 along with wiring into the
    background loop. The current implementation only does:
    - `score >= 0` → untouched (the tally never rolls back; "archive more
      aggressively" — §3.5.3)
    - `score < 0` and not yet counted today → `sub_zero_days += 1` +
      `sub_zero_last_increment_date = today`

    Returns True if `sub_zero_days` was incremented this call.

    Protected entries: always return False without incrementing
    (evidence_score returns inf).
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
