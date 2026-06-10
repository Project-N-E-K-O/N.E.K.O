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
Funnel analytics over the per-character `events.ndjson` log
(memory-evidence-rfc §3.10).

Why a separate module (instead of inside `evidence.py`):
- `evidence.py` is pure functions + math; per RFC §3.8.2 / §7 it must not
  import any stateful class (FastAPI, EventLog, ConfigManager), let alone
  do IO.
- This module scans the disk → it must depend on `ensure_character_dir` +
  `ConfigManager`, hence a sibling module isolating IO from pure math.

V1 scope (RFC §3.10.4):
- Exposes only the single aggregation function `funnel_counts`; no UI / CLI /
  cross-character aggregation
- Linear scan at read time, O(N); the events.ndjson compaction threshold is
  10k lines, a full scan takes <200ms (RFC §3.10.3), no dedicated index needed

Forward compatibility notes (PR-2 / PR-3 coordination):
- `persona_entries_archived` depends on PR-2 writing the `archive_shard_path`
  field into the `persona.fact_added` payload; always 0 until PR-2 lands
- `reflections_merged` / `persona_entries_rewritten` depend on PR-3's
  merge-on-promote path; always 0 until PR-3 lands
- Unknown event types and unrecognized `state_changed.to` values are silently
  skipped (forward-compat: old versions reading a new event stream must not
  crash)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

from utils.config_manager import get_config_manager

logger = logging.getLogger(__name__)

__all__ = ["funnel_counts", "to_naive_local"]


# Bucket schema — the keys in the returned dict, frozen by RFC §3.10.2.
# 列表式定义保证 zero-init 时 11 个 bucket 都有 0，调用方遍历不会 KeyError。
_FUNNEL_BUCKETS: tuple[str, ...] = (
    "facts_added",
    "reflections_synthesized",
    "reflections_confirmed",
    "reflections_promoted",
    "reflections_merged",
    "reflections_denied",
    "reflections_archived",
    "persona_entries_added",
    "persona_entries_rewritten",
    "persona_entries_archived",
)


# `reflection.state_changed.payload['to']` 值 → bucket 名。
# 不在表里的 `to` 值（例如 'pending' / 'promote_blocked' / 未来扩展）
# 静默跳过——funnel 只统计 RFC §3.10.2 列出的终态转换，不是全状态机。
_STATE_TO_BUCKET: dict[str, str] = {
    "confirmed": "reflections_confirmed",
    "promoted": "reflections_promoted",
    "merged": "reflections_merged",
    "denied": "reflections_denied",
    "archived": "reflections_archived",
}


def _events_path(lanlan_name: str) -> str:
    """Return the absolute path of `events.ndjson` for a character.

    Duplicates the path computation of `EventLog._events_path` but does **not**
    import `EventLog` — this module is a read-only scanner: it takes no locks
    and mutates nothing; importing EventLog would drag a stateful class in
    next to evidence.py, violating the §3.8.2 isolation (even though this file
    is an independent sibling, the pure-function evidence.py would still pick
    up an indirect dependency through second-order re-exports).
    """
    # 局部 import 避开 memory/__init__.py ↔ memory/* 的循环依赖
    from memory import ensure_character_dir
    cm = get_config_manager()
    return os.path.join(
        ensure_character_dir(cm.memory_dir, lanlan_name),
        "events.ndjson",
    )


def to_naive_local(dt: datetime) -> datetime:
    """Normalize any datetime to naive local-clock for comparison with
    event-log timestamps.

    `event_log.py` writes `ts` via `datetime.now().isoformat()` — naive,
    in local clock. `funnel_counts` therefore compares everything in that
    same convention. If a caller supplies an aware datetime (e.g. parsed
    from `2026-04-23T00:00:00Z` via `datetime.fromisoformat`), comparing
    it directly against a naive parsed `ts` would raise TypeError.

    Convention: aware → convert to local timezone, then drop tzinfo.
    Naive → return unchanged (assumed already local-clock per the
    event-log convention).

    Public-ish (re-exported via `__all__`) so the HTTP endpoint in
    `memory_server.py` can normalize bounds *before* its own
    `since_dt > until_dt` validation, not just inside `funnel_counts`.
    Otherwise mixed aware/naive bounds (e.g. `?until=...Z` with `since`
    defaulted to a naive `datetime.now()`) would still raise TypeError
    at the comparison and surface as 500 instead of the intended 400.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone().replace(tzinfo=None)


# Backward-compat alias for any internal callers that imported the
# private name; the underscore form is kept so we don't have to rewrite
# in-module references in this same patch.
_to_naive_local = to_naive_local


def _parse_ts(ts: object) -> Optional[datetime]:
    """Parse an ISO8601 timestamp into a naive local-clock datetime.

    `datetime.fromisoformat` will return aware iff the input string carries
    an offset (e.g. `Z` / `+08:00`). We always normalize to naive local
    clock to match the comparison convention enforced in `funnel_counts`
    — see `_to_naive_local`."""
    if not isinstance(ts, str):
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return _to_naive_local(parsed)


def funnel_counts(
    lanlan_name: str,
    since: datetime,
    until: datetime,
) -> dict:
    """Linear-scan `events.ndjson` for `lanlan_name`, aggregate per-stage
    counts within the [since, until] inclusive window.

    Returns a dict with all 10 buckets defined in RFC §3.10.2:
        facts_added, reflections_synthesized,
        reflections_{confirmed,promoted,merged,denied,archived},
        persona_entries_{added,rewritten,archived}

    Timezone handling:
        `since`, `until`, and each event `ts` are all normalized to naive
        local-clock before comparison (see `_to_naive_local`). The
        underlying convention comes from `event_log.py` which writes `ts`
        via `datetime.now().isoformat()` — naive, local. Aware inputs are
        accepted (converted to local then stripped of tzinfo) so callers
        can pass either flavor without mixing-aware-and-naive TypeErrors.

    Behavior:
        - If events.ndjson does not exist → all zeros.
        - Events outside [since, until] are skipped (timestamp inclusive
          on both ends, after tz normalization).
        - Events with malformed JSON / missing `type` / unparseable `ts`
          are skipped with a single WARN per scan call (not per line —
          avoids log spam on a corrupted log).
        - Unknown event types are silently ignored (forward-compat:
          a future event type added by a later PR shouldn't break this
          analytics call on old binaries).
        - `persona.fact_added` is split between `persona_entries_added`
          (main view, default) and `persona_entries_archived` (when the
          payload carries `archive_shard_path`, the archive convention
          PR-2 introduces).

    Performance: O(N) over file lines, no indexing.  RFC §3.10.3 budgets
    <200ms for the 10k-line compaction threshold.
    """
    counts: dict = {bucket: 0 for bucket in _FUNNEL_BUCKETS}
    path = _events_path(lanlan_name)
    if not os.path.exists(path):
        return counts

    # Normalize bounds once up front. Each event ts is normalized inside
    # `_parse_ts`; doing the same to the bounds keeps all three operands
    # in the same convention (naive local clock) and guarantees no
    # offset-naive vs offset-aware TypeError mid-scan.
    since = _to_naive_local(since)
    until = _to_naive_local(until)

    warned_malformed = False

    try:
        fh = open(path, encoding="utf-8")
    except OSError as e:
        logger.warning(
            f"[FunnelAnalytics] {lanlan_name}: 打开 events.ndjson 失败: {e}; "
            f"返回全零计数"
        )
        return counts

    with fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                if not warned_malformed:
                    logger.warning(
                        f"[FunnelAnalytics] {lanlan_name}: events.ndjson 含损坏行，"
                        f"已跳过（后续损坏行不再重复告警）"
                    )
                    warned_malformed = True
                continue
            if not isinstance(rec, dict):
                continue
            ts = _parse_ts(rec.get("ts"))
            if ts is None:
                # 没法落进时间窗 → 直接跳，不计入任何 bucket
                continue
            if ts < since or ts > until:
                continue

            evt_type = rec.get("type")
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}

            if evt_type == "fact.added":
                counts["facts_added"] += 1
            elif evt_type == "reflection.synthesized":
                counts["reflections_synthesized"] += 1
            elif evt_type == "reflection.state_changed":
                bucket = _STATE_TO_BUCKET.get(payload.get("to"))
                if bucket is not None:
                    counts[bucket] += 1
                # else: 未识别的 state 值（pending / promote_blocked / 未来扩展）
                # 静默跳过——funnel 只统计 RFC §3.10.2 enumerate 的终态
            elif evt_type == "persona.fact_added":
                # PR-2 引入的 archive 路径在 payload 里带 `archive_shard_path`
                # 字段；缺字段一律视作主 view 添加（PR-2 未合并时分支永远走
                # main 即可，archive 桶恒为 0）
                if payload.get("archive_shard_path"):
                    counts["persona_entries_archived"] += 1
                else:
                    counts["persona_entries_added"] += 1
            elif evt_type == "persona.entry_updated":
                counts["persona_entries_rewritten"] += 1
            # else: 未知事件类型 → 静默跳过（forward-compat）

    return counts
