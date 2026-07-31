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
EventLog — per-character append-only audit + replay log (P2 infrastructure).

Why it exists (see docs/design/memory-event-log-rfc.md):
  P0 persisted the rebuttal cursor; P1 added the outbox so background tasks
  killed mid-flight can be re-run. The remaining structural problem: the
  views (facts.json / reflections.json / persona.json) are the only record
  of "state transitions" — there is no ordered history, so "crashed halfway
  through a view write" is invisible and cross-file invariants cannot be
  checked.

  This module adds events.ndjson (per character): every state transition
  writes the event **first**, then the view. At startup the reconciler
  compares the log tail against the sentinel (events_applied.json) and
  replays into the views any events the views never persisted.

Non-goals:
  - Not full event sourcing (views remain the hand-editable "source of truth").
  - Not a rule engine or state-machine DSL.
  - Not cross-character (per-character files; the architecture assumes a
    single writer).

Write discipline (RFC §3.4):
  Every write site that emits events must go through record_and_save, which
  puts the five steps load → append → mutate → save → sentinel-advance
  inside one per-character threading.Lock, the whole wrapped in one
  asyncio.to_thread worker. Never hold the lock across an await — continuing
  the outbox / cursors pattern.

  Why append precedes mutate: the view returned by load is often a shared
  cache held by the manager; if we mutated first and append then threw
  (e.g. an fsync OSError), the cache would keep "dirty changes with no
  corresponding event", and any later normal save would flush them to
  disk, breaking the event↔view correspondence. Appending successfully
  before mutating guarantees the cache only changes after the event is
  durably on disk.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from datetime import datetime
from typing import Callable

from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_text, atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")


# ── Event type constants (RFC §3.3, 12 legacy + 3 evidence = 15 types) ───

EVT_FACT_ADDED = "fact.added"
EVT_FACT_ABSORBED = "fact.absorbed"
EVT_FACT_ARCHIVED = "fact.archived"
EVT_REFLECTION_SYNTHESIZED = "reflection.synthesized"
EVT_REFLECTION_STATE_CHANGED = "reflection.state_changed"
EVT_REFLECTION_SURFACED = "reflection.surfaced"
EVT_REFLECTION_REBUTTED = "reflection.rebutted"
EVT_PERSONA_FACT_ADDED = "persona.fact_added"
EVT_PERSONA_FACT_MENTIONED = "persona.fact_mentioned"
EVT_PERSONA_SUPPRESSED = "persona.suppressed"
EVT_CORRECTION_QUEUED = "correction.queued"
EVT_CORRECTION_RESOLVED = "correction.resolved"

# memory-evidence-rfc §3.3 — 3 new event types for user-driven evidence.
# All three are full-snapshot payloads (§3.3.5) so handlers are trivially
# idempotent on replay.
EVT_REFLECTION_EVIDENCE_UPDATED = "reflection.evidence_updated"
EVT_PERSONA_EVIDENCE_UPDATED = "persona.evidence_updated"
EVT_PERSONA_ENTRY_UPDATED = "persona.entry_updated"

ALL_EVENT_TYPES: frozenset[str] = frozenset({
    EVT_FACT_ADDED, EVT_FACT_ABSORBED, EVT_FACT_ARCHIVED,
    EVT_REFLECTION_SYNTHESIZED, EVT_REFLECTION_STATE_CHANGED,
    EVT_REFLECTION_SURFACED, EVT_REFLECTION_REBUTTED,
    EVT_PERSONA_FACT_ADDED, EVT_PERSONA_FACT_MENTIONED, EVT_PERSONA_SUPPRESSED,
    EVT_CORRECTION_QUEUED, EVT_CORRECTION_RESOLVED,
    EVT_REFLECTION_EVIDENCE_UPDATED, EVT_PERSONA_EVIDENCE_UPDATED,
    EVT_PERSONA_ENTRY_UPDATED,
})


# memory-evidence-rfc §3.3.7 — source 枚举值，放在模块级常量便于未来扩展。
# 不强制校验（event_log 不过滤），但 reconciler handler / 测试可用来核对。
EVIDENCE_SOURCE_USER_FACT = "user_fact"            # Stage-2 signal (§3.4)
EVIDENCE_SOURCE_USER_CONFIRM = "user_confirm"      # check_feedback confirmed
EVIDENCE_SOURCE_USER_REBUT = "user_rebut"          # check_feedback denied
EVIDENCE_SOURCE_USER_IGNORE = "user_ignore"        # check_feedback ignored
EVIDENCE_SOURCE_USER_KEYWORD_REBUT = "user_keyword_rebut"  # negative-keyword hook
EVIDENCE_SOURCE_MIGRATION_SEED = "migration_seed"  # §5 one-shot migration
EVIDENCE_SOURCE_PROMOTE_MERGE = "promote_merge"    # persona.entry_updated

ALL_EVIDENCE_SOURCES: frozenset[str] = frozenset({
    EVIDENCE_SOURCE_USER_FACT, EVIDENCE_SOURCE_USER_CONFIRM,
    EVIDENCE_SOURCE_USER_REBUT, EVIDENCE_SOURCE_USER_IGNORE,
    EVIDENCE_SOURCE_USER_KEYWORD_REBUT, EVIDENCE_SOURCE_MIGRATION_SEED,
    EVIDENCE_SOURCE_PROMOTE_MERGE,
})


# ── Compaction thresholds (RFC §3.6) ─────────────────────────────────────

_COMPACT_LINES_THRESHOLD = 10_000   # file line count
_COMPACT_DAYS_THRESHOLD = 90        # age of oldest entry, days


# ── Type aliases for _record_and_save callbacks (RFC §3.4) ───────────────

SyncLoadView = Callable[[str], object]           # (character_name) -> view_obj
SyncMutateView = Callable[[object], None]        # (view_obj) -> None, mutates in place
SyncSaveView = Callable[[str, object], None]     # (character_name, view_obj) -> None

# Apply handler: takes (character_name, event_payload) and is responsible for
# loading the relevant view, applying the event, AND persisting it before
# returning. Returns True if the apply actually changed state; False if
# idempotent no-op. Critical invariant: sentinel only advances after handler
# returns successfully, so handler MUST persist before returning — otherwise
# a process crash between handler-return and sentinel-write would lose the
# change while marking the event as applied.
#
# Handlers MUST be synchronous (no async/await) and use the sync IO helpers
# (atomic_write_json, not its a-twin): Reconciler.areconcile runs them on a
# worker thread via EventLog.apply_and_advance, without await. An async
# handler would return a coroutine that never runs (and would be truthy, so
# the sentinel would advance over an event that was never applied), silently
# breaking reconciliation. A handler must also never touch an asyncio
# primitive (it is off the event loop) nor call back into EventLog: it runs
# under the per-character lock, which is a plain non-reentrant threading.Lock.
ApplyHandler = Callable[[str, dict], bool]


class SentinelAdvanceError(RuntimeError):
    """The apply handler already succeeded, but the sentinel could not move.

    Raised only by apply_and_advance, and only after apply_fn returned — so
    it is never a handler failure and must not be reported as one. On-disk
    state is "view updated, sentinel not advanced", i.e. the event replays
    again on the next boot (handlers are idempotent).
    """


class SentinelConflictError(RuntimeError):
    """Another writer moved the sentinel while this replay was in flight.

    Deliberately NOT a SentinelAdvanceError. That class means "the view is
    already repaired, only the progress marker is behind"; this one is
    raised BEFORE apply_fn runs, so nothing was applied and nothing on disk
    changed. Catching the two together would report a round that has done
    nothing as a round whose write landed.

    Two facts follow from the conflict, and both matter:

      - Writing our own event_id now would move the sentinel BACKWARDS over
        events the other writer already claimed as applied. Those events
        would return to the tail, and any of them without a registered
        handler would pause replay on every subsequent boot — a permanently
        stuck reconciler.
      - The tail this round was holding is now behind the other writer's
        sentinel. record_and_save writes the id of the line it just
        appended, i.e. the end of the journal, and does so unconditionally
        (see its docstring): the events still queued in this round were
        declared "already applied" by that write even though they were not.
        Replaying them as-is would push older payloads over the newer
        writer's values, and the newer event would not be replayed again to
        correct it.

    The caller's job on this error is therefore not "stop" and not "keep
    going with the stale list" — it is to re-derive the work from the
    journal so the newer events are replayed on top, in journal order. See
    Reconciler.areconcile.
    """


class EventLog:
    """Per-character append-only event journal with reconciliation support.

    Public API is dual (sync + async twins). Sync methods are safe to call
    from async def code paths ONLY via asyncio.to_thread — they do blocking
    file IO. The _record_and_save helper and its a-twin are the normal
    entry points for wiring into existing save sites.
    """

    def __init__(self):
        self._config_manager = get_config_manager()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ── paths / locks ───────────────────────────────────────────

    def _events_path(self, name: str) -> str:
        # Late import avoids memory/__init__.py ↔ memory/event_log.py cycle
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'events.ndjson',
        )

    def _sentinel_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'events_applied.json',
        )

    def _get_lock(self, name: str) -> threading.Lock:
        if name not in self._locks:
            with self._locks_guard:
                if name not in self._locks:
                    self._locks[name] = threading.Lock()
        return self._locks[name]

    # ── low-level append (no lock — caller must hold it) ────────

    def _write_line_unlocked(self, path: str, line: str) -> None:
        """Append + flush + fsync. fsync failures must propagate — record_and_save's
        durability contract is "the event hits disk before the view advances"; if
        fsync failed silently while the later view.save succeeded, the event↔view
        correspondence would break and the reconciler couldn't repair it.
        The caller (record_and_save) handles the exception, guaranteeing the view
        is never wrongly advanced."""
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
            f.flush()
            os.fsync(f.fileno())

    def _append_unlocked(self, name: str, event_type: str, payload: dict) -> str:
        """Write one event record under an already-held lock. Returns event_id.

        Fails fast on unknown event_type. If unregistered types were let through and
        record_and_save crashed after the event append but before the view save, the
        Reconciler would have no handler on restart and could only skip it (and if
        it were pushed toward pause-on-unknown instead, the whole tail would be
        stuck) — an expensive repair path. Blocking at the write site is the
        cheapest line of defense.
        """
        if event_type not in ALL_EVENT_TYPES:
            raise ValueError(
                f"[EventLog] {name}: unknown event type {event_type!r}; "
                f"refusing to write unreplayable event"
            )
        event_id = str(uuid.uuid4())
        record = {
            'event_id': event_id,
            'type': event_type,
            'ts': datetime.now().isoformat(),
            'payload': payload,
        }
        line = json.dumps(record, ensure_ascii=False)
        self._write_line_unlocked(self._events_path(name), line)
        return event_id

    # ── public API: standalone append (no view coupling) ────────

    def append(self, name: str, event_type: str, payload: dict) -> str:
        """Append a single event. Prefer _record_and_save for writes that
        also mutate a view — this standalone API is for tests / migrations /
        events without a corresponding view update."""
        with self._get_lock(name):
            return self._append_unlocked(name, event_type, payload)

    # ── read_since / sentinel ───────────────────────────────────

    def _read_all_records(self, path: str) -> list[dict]:
        """Parse every line; skip corrupt ones with a warning. Caller holds lock."""
        if not os.path.exists(path):
            return []
        records: list[dict] = []
        with open(path, encoding='utf-8') as f:
            for lineno, raw in enumerate(f, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(
                        f"[EventLog] {path} 第 {lineno} 行无法解析，跳过: {raw[:120]!r}"
                    )
                    continue
                if not isinstance(rec, dict) or 'event_id' not in rec:
                    logger.warning(
                        f"[EventLog] {path} 第 {lineno} 行缺 event_id，跳过"
                    )
                    continue
                records.append(rec)
        return records

    def read_since(self, name: str, after_event_id: str | None) -> list[dict]:
        """Return events after the sentinel, in file-position order.

        If after_event_id is None or not found in the current body, return
        ALL records (safe default per RFC §3.5 — apply handlers are
        idempotent; the worst case is re-applying the compacted snapshot
        seed set, which is bounded by live-entity count).
        """
        with self._get_lock(name):
            records = self._read_all_records(self._events_path(name))
        if after_event_id is None:
            return records
        for i, rec in enumerate(records):
            if rec.get('event_id') == after_event_id:
                return records[i + 1:]
        # Sentinel points to an event no longer in the body (compacted away).
        # Safe default: replay everything currently in the body.
        logger.info(
            f"[EventLog] {name}: sentinel event_id {after_event_id} 不在当前 body，"
            f"回退到全量 replay（{len(records)} 条）"
        )
        return records

    def read_sentinel(self, name: str) -> str | None:
        """Load last_applied_event_id from sentinel file. Safe defaults per RFC §3.5."""
        path = self._sentinel_path(name)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[EventLog] {name}: sentinel 读取失败 {e}；视作 null")
            return None
        if not isinstance(data, dict):
            logger.warning(f"[EventLog] {name}: sentinel 格式异常（非 dict），视作 null")
            return None
        last = data.get('last_applied_event_id')
        if last is not None and not isinstance(last, str):
            return None
        return last

    def _write_sentinel_unlocked(self, name: str, event_id: str | None) -> None:
        """Unconditionally overwrite events_applied.json. Caller holds the lock.

        Unconditional means exactly that: this helper does NOT enforce
        monotonicity, and cannot. event_id is a uuid4 — two ids carry no
        order relative to each other; the only ordering that exists is the
        position in events.ndjson, which this helper never reads. Writers
        that must not move the sentinel backwards have to establish the
        ordering themselves (see apply_and_advance's compare-and-set).
        """
        atomic_write_json(
            self._sentinel_path(name),
            {'last_applied_event_id': event_id, 'ts': datetime.now().isoformat()},
        )

    def advance_sentinel(self, name: str, event_id: str | None) -> None:
        """Persist the new sentinel atomically, under the per-character lock.

        Sentinel writers must serialise against record_and_save, which
        rewrites the same file at the tail of its own critical section.
        Callers that already hold the lock must use _write_sentinel_unlocked
        instead — threading.Lock is not reentrant.

        Guarantees mutual exclusion only, NOT monotonicity: the write is
        still unconditional, so a caller holding a stale event_id can move
        the sentinel backwards. record_and_save is safe because the id it
        writes is the one it just appended (the newest event by
        construction); everyone else has to reason about it.
        """
        # 这个公开方法在生产里已无调用方（reconcile 走 apply_and_advance，
        # record_and_save 走 _write_sentinel_unlocked），锁留在这里是为了
        # 「默认安全」：将来任何新调用点都不会再意外造出一个无锁写者。
        with self._get_lock(name):
            self._write_sentinel_unlocked(name, event_id)

    def apply_and_advance(
        self,
        name: str,
        event_id: str | None,
        apply_fn: Callable[[], bool],
        *,
        expected_sentinel: str | None,
        advance: bool = True,
    ) -> bool:
        """Run one replay apply and advance the sentinel in ONE critical section.

        apply_fn is a reconciler apply-handler already bound to its
        (character, payload); it loads → mutates → persists its own view.
        expected_sentinel is the sentinel value the replay based its tail
        on — the previous event's id, or the value read when the round
        started. It is checked against the on-disk sentinel BEFORE apply_fn
        runs, so a stale event is never applied (see the conflict branch
        below). advance=False skips both the check and the write: the caller
        has already learned the sentinel is no longer theirs to move and has
        re-derived its work list from the journal, so re-checking would only
        raise again. Running the pair here — inside the same per-character
        lock record_and_save uses, on a worker thread — buys three things:

          1. Once a live writer has entered record_and_save's critical
             section, its read-modify-write can no longer interleave with
             the handler's read-modify-write on the same view file. This is
             a partial guarantee, and the boundary matters: the reflection
             and persona write paths load their view snapshot BEFORE
             entering (memory/reflection/evidence_flow.py loads, then hands
             the snapshot to record_and_save as sync_load_view), so a live
             writer parked between its own load and its own lock acquisition
             can still save a pre-replay snapshot over the repair. What keeps
             that from happening in production is the startup ordering —
             reconciliation completes before any live writer is resumed (see
             app/memory_server/runtime.py) — and this lock is the second
             line of defence, not the first.
          2. The sentinel write joins the handler in the same critical
             section: the pair is atomic with respect to other writers, and
             the sentinel can never advance without the handler's write
             having landed first.
          3. The handler's file IO leaves the event loop, which is what
             makes it eligible for file_utils' busy-retry backoff (that
             backoff is deliberately disabled on the loop) and stops its
             fsync from blocking the loop.

        Returns whatever apply_fn returned (True = the view actually
        changed). Raises:
          - SentinelConflictError, before apply_fn is called at all, if the
            on-disk sentinel is no longer expected_sentinel (someone
            advanced it while this replay was in flight). Nothing is applied
            and the sentinel is left alone;
          - whatever apply_fn raised, with the sentinel untouched — the
            caller's pause-and-retry semantics;
          - SentinelAdvanceError if the sentinel write itself fails, so the
            caller can tell an IO problem from a handler problem.
        """
        with self._get_lock(name):
            if advance:
                # 单调性 compare-and-set：event_id 是 uuid4，无法互相比大小，
                # 「新的哨兵是否更靠后」只能靠「盘上哨兵还是我出发时那个」来判。
                # 不判就会出现审阅复现的死局：live writer 追加了一条本进程没注册
                # handler 的事件并把哨兵推过去，重放再把哨兵写回旧 id → 那条事件
                # 回到尾巴 → 之后每次开机都撞「未注册事件类型，暂停 replay」。
                #
                # 判定必须排在 apply_fn **之前**。handler 是「读盘 → 改目标条目的
                # 字段 → 整覆盖」，先跑一遍就已经把这条陈旧事件的字段写进 view 了；
                # 如果那个更新的写者动过同一条目的同一批字段，就是拿旧值盖新值，
                # 等 CAS 事后报冲突已经晚了（而且它那条更新的事件在哨兵后面，
                # 不会再被重放回来纠正）。
                # 检查和 apply 之间不会被进程内的写者插进来：record_and_save /
                # compact_if_needed / advance_sentinel 全都要拿这把锁，而这两步
                # 都在我们已经握住它的这段临界区里。
                current = self.read_sentinel(name)
                if current != expected_sentinel:
                    raise SentinelConflictError(
                        f"{name}: sentinel moved to {current!r} before replaying "
                        f"{event_id!r} (expected {expected_sentinel!r}); refusing "
                        f"to apply a tail the sentinel already passed"
                    )
            changed = apply_fn()
            if not advance:
                return bool(changed)
            try:
                self._write_sentinel_unlocked(name, event_id)
            except Exception as e:
                # handler 已经落盘了，这里失败纯粹是哨兵写的 IO 问题。
                # 包成专用异常，免得调用方把它记成「handler 失败」。
                raise SentinelAdvanceError(
                    f"{name}: apply of {event_id!r} persisted but the sentinel "
                    f"write failed: {e}"
                ) from e
        return bool(changed)

    # ── compaction (RFC §3.6) ───────────────────────────────────

    def _scan_head_and_count(self, path: str) -> tuple[int, datetime | None]:
        """Return (line_count, oldest_ts). Reads only the first line fully.

        Edge cases:
          - file missing or empty → (0, None)
          - first line missing / unparseable → (line_count, None) + warn
          - unreadable (OSError) → (0, None) + warn; compaction skipped
        """
        if not os.path.exists(path):
            return 0, None
        try:
            with open(path, encoding='utf-8') as f:
                oldest_ts: datetime | None = None
                line_count = 0
                first_line: str | None = None
                for i, raw in enumerate(f):
                    if i == 0:
                        first_line = raw.strip()
                    line_count += 1
                if first_line:
                    try:
                        rec = json.loads(first_line)
                        ts_str = rec.get('ts') if isinstance(rec, dict) else None
                        if isinstance(ts_str, str):
                            try:
                                oldest_ts = datetime.fromisoformat(ts_str)
                            except ValueError:
                                logger.warning(
                                    f"[EventLog] {path} 首行 ts 解析失败，年龄阈值暂不生效"
                                )
                    except json.JSONDecodeError:
                        logger.warning(
                            f"[EventLog] {path} 首行损坏，年龄阈值暂不生效"
                        )
                return line_count, oldest_ts
        except OSError as e:
            logger.warning(f"[EventLog] {path} 读取失败（跳过 compact）: {e}")
            return 0, None

    def _should_compact_unlocked(self, name: str) -> bool:
        """Lock-free version. Caller must already hold self._get_lock(name),
        OR call from a path where concurrent mutation is impossible (e.g.
        startup before handlers are registered)."""
        line_count, oldest_ts = self._scan_head_and_count(self._events_path(name))
        if line_count >= _COMPACT_LINES_THRESHOLD:
            return True
        if oldest_ts is not None:
            age_days = (datetime.now() - oldest_ts).total_seconds() / 86400
            if age_days >= _COMPACT_DAYS_THRESHOLD:
                return True
        return False

    def should_compact(self, name: str) -> bool:
        """Public check. Acquires the per-character lock before reading,
        so the line-count + head-scan see a consistent file."""
        with self._get_lock(name):
            return self._should_compact_unlocked(name)

    def compact_if_needed(
        self,
        name: str,
        seed_events_provider: Callable[[], list[tuple[str, dict]]],
    ) -> int:
        """Rewrite events.ndjson as a fresh body of snapshot-start events iff
        thresholds exceeded. Returns number of lines dropped (0 if skipped).

        Atomicity: a single atomic_write_text (tempfile + os.replace) swaps
        the new body onto events.ndjson. No intermediate events.snapshot
        file — RFC §3.6.

        After the swap succeeds we reset the sentinel. A crash between swap
        and sentinel reset is safe: the old sentinel's last_applied_event_id
        won't be in the new body, so read_since falls through to full
        replay (bounded by snapshot-start count).

        seed_events_provider: callable that re-derives the full set of
        snapshot-start events (event_type, payload) pairs from the CURRENT
        view files. Caller decides what to include (e.g., only live facts,
        only non-absorbed).
        """
        with self._get_lock(name):
            if not self._should_compact_unlocked(name):
                return 0
            old_line_count = self._count_lines_unlocked(name)
            seeds = seed_events_provider()
            lines = []
            now_iso = datetime.now().isoformat()
            for event_type, payload in seeds:
                if event_type not in ALL_EVENT_TYPES:
                    raise ValueError(
                        f"[EventLog] {name}: compact seed uses unknown event type "
                        f"{event_type!r}; refusing to rewrite log body with "
                        f"unreplayable seeds"
                    )
                rec = {
                    'event_id': str(uuid.uuid4()),
                    'type': event_type,
                    'ts': now_iso,
                    'payload': payload,
                }
                lines.append(json.dumps(rec, ensure_ascii=False))
            body = ('\n'.join(lines) + '\n') if lines else ''
            atomic_write_text(self._events_path(name), body, encoding='utf-8')
            # Reset sentinel to null — next reconciliation will apply the seeds
            # (all idempotent).
            atomic_write_json(
                self._sentinel_path(name),
                {'last_applied_event_id': None, 'ts': now_iso},
            )
        dropped = old_line_count - len(lines)
        if dropped < 0:
            dropped = 0
        return dropped

    def _count_lines_unlocked(self, name: str) -> int:
        path = self._events_path(name)
        if not os.path.exists(path):
            return 0
        try:
            with open(path, encoding='utf-8') as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    # ── _record_and_save (RFC §3.4) ─────────────────────────────

    def record_and_save(
        self,
        name: str,
        event_type: str,
        payload: dict,
        *,
        sync_load_view: SyncLoadView,
        sync_mutate_view: SyncMutateView,
        sync_save_view: SyncSaveView,
    ) -> str:
        """The canonical event-emitting write:
        load view → append event → mutate view → save view → advance sentinel.

        All five steps run inside a single per-character threading.Lock so
        no two coroutines can race a read-modify-write cycle. Returns the
        newly-allocated event_id.

        Append runs BEFORE mutate to avoid dirtying the shared cache if the
        event fails to persist; see the block comment inside the method
        body for the full rationale.

        The sync twins (load_X / save_X) are the right choice here: we are
        ALREADY on a worker thread (the _arecord_and_save a-twin hops us
        into one), and using async twins would pointlessly re-schedule
        through asyncio.to_thread and risk event-loop locking anti-patterns.

        KNOWN LIMITATION — the sentinel write is an unconditional claim.
        The final step writes the sentinel to the id just appended, which
        by definition is the last line of the journal, so this method can
        never move the sentinel backwards. What it does not check is
        whether everything BEFORE that line was applied. Setting the
        sentinel to the journal end asserts exactly that, and the assertion
        is false whenever an unapplied tail is still sitting on disk: those
        events fall behind the sentinel and no later boot replays them.
        Whoever hits that has lost them silently.

        It is reachable. Replay pauses (and leaves a tail) on an
        unregistered event type or a raising handler; the very next live
        write then orphans whatever was left. Startup ordering does not
        help — this has nothing to do with reconciliation running
        concurrently.

        Closing it properly means the sentinel can no longer be "an event
        id": a writer would have to know whether the journal ahead of its
        own line is drained, which is a full scan on every write, or the
        sentinel has to become a position/applied-set with an on-disk
        format migration. Both are RFC-level changes
        (docs/design/memory-event-log-rfc.md, concurrency section) and are
        deliberately out of scope here. Do NOT read "the sentinel only ever
        moves forward" as "no event can be skipped".
        """
        with self._get_lock(name):
            view = sync_load_view(name)
            # 顺序：load → append（可能 fsync 失败抛出）→ mutate → save。
            # append 先于 mutate 的原因：sync_load_view 常返回 manager 持有的
            # 共享 cache，若先 mutate 再 append 而 append 抛出，cache 已脏但
            # 事件没落盘，后续任一次正常 save 都会把"无事件对应的变更"刷盘，
            # 破坏 event↔view 对应关系。先 append 成功再 mutate 则保证：
            #   - append 失败：view/cache 未动，无状态泄露
            #   - mutate/save 失败：事件已在 log，reconciler 会补齐
            event_id = self._append_unlocked(name, event_type, payload)
            sync_mutate_view(view)
            sync_save_view(name, view)
            # Inline sentinel write: still under the lock, still on this
            # worker thread — the unlocked helper, because advance_sentinel
            # takes the same non-reentrant lock we are already holding.
            self._write_sentinel_unlocked(name, event_id)
        return event_id

    # ── async duals ─────────────────────────────────────────────

    async def aappend(self, name: str, event_type: str, payload: dict) -> str:
        return await asyncio.to_thread(self.append, name, event_type, payload)

    async def aread_since(self, name: str, after_event_id: str | None) -> list[dict]:
        return await asyncio.to_thread(self.read_since, name, after_event_id)

    async def aread_sentinel(self, name: str) -> str | None:
        return await asyncio.to_thread(self.read_sentinel, name)

    async def aadvance_sentinel(self, name: str, event_id: str | None) -> None:
        await asyncio.to_thread(self.advance_sentinel, name, event_id)

    async def aapply_and_advance(
        self,
        name: str,
        event_id: str | None,
        apply_fn: Callable[[], bool],
        *,
        expected_sentinel: str | None,
        advance: bool = True,
    ) -> bool:
        return await asyncio.to_thread(
            self.apply_and_advance, name, event_id, apply_fn,
            expected_sentinel=expected_sentinel, advance=advance,
        )

    async def ashould_compact(self, name: str) -> bool:
        return await asyncio.to_thread(self.should_compact, name)

    async def acompact_if_needed(
        self,
        name: str,
        seed_events_provider: Callable[[], list[tuple[str, dict]]],
    ) -> int:
        return await asyncio.to_thread(self.compact_if_needed, name, seed_events_provider)

    async def arecord_and_save(
        self,
        name: str,
        event_type: str,
        payload: dict,
        *,
        sync_load_view: SyncLoadView,
        sync_mutate_view: SyncMutateView,
        sync_save_view: SyncSaveView,
    ) -> str:
        return await asyncio.to_thread(
            self.record_and_save, name, event_type, payload,
            sync_load_view=sync_load_view,
            sync_mutate_view=sync_mutate_view,
            sync_save_view=sync_save_view,
        )


# ── Reconciler scaffolding (RFC §3.5) ─────────────────────────────────────

# 哨兵冻结之后每扫完一轮就再朝日志末尾探一次，这是探的次数上限。只有「上一轮扫描
# 期间又有写者落地」才会消耗一次，所以正常启动一次都用不到；给 8 次是为了让写入
# 停下来的那一刻能收尾，同时不至于在持续写入下让启动永远卡在这里。
_MAX_FROZEN_RESCANS = 8


class Reconciler:
    """Applies event-log tail onto views on startup.

    Handlers for each event type are registered externally (by memory_server
    in P2.b). Unknown event types are logged and skipped (forward
    compatibility: an older binary can keep running against a newer log).
    """

    def __init__(self, event_log: EventLog):
        self._event_log = event_log
        self._handlers: dict[str, ApplyHandler] = {}

    def register(self, event_type: str, handler: ApplyHandler) -> None:
        if event_type not in ALL_EVENT_TYPES:
            logger.warning(
                f"[Reconciler] 注册未登记事件类型 {event_type!r}（handler 仍生效，但请检查 typo）"
            )
        self._handlers[event_type] = handler

    async def areconcile(self, name: str) -> int:
        """P2.a.1 scaffold: dispatch tail events to registered handlers,
        advance sentinel per event, preserve sentinel on handler raise.
        P2.b wires concrete save paths.

        Handler contract (ApplyHandler): each handler MUST load → apply →
        save its own view before returning. The Reconciler only advances
        the sentinel after handler returns; if handler skipped the save,
        a crash between handler-return and sentinel-write would silently
        lose the change. Modeled off record_and_save — the per-event-type
        equivalent is per-handler responsibility.

        Concurrency: the primary guarantee is exclusivity, not locking.
        The only production caller (app/memory_server/runtime.py) awaits
        this loop to completion BEFORE it resumes the outbox, so no live
        writer is running while the tail is replayed. That ordering is what
        closes the loss window, because a live writer that loaded its view
        snapshot before the replay would otherwise save it back over the
        repair — with the sentinel already past the event, so no later boot
        replays it again: silent, permanent loss.

        Underneath, handler-call and sentinel-advance are one critical
        section (EventLog.apply_and_advance) on the per-character lock that
        record_and_save uses, on a worker thread. That is defence in depth
        for the residual window (writers that already read their snapshot)
        and for any future caller that reconciles outside the startup path.

        Failure semantics (intentional): if a handler raises, we STOP the
        whole reconcile loop for this character and leave the sentinel on
        the last successfully-applied event. Rationale — compound
        transitions (reflection.state_changed followed by persona.fact_added)
        have a causal dependency; applying downstream events past a failed
        upstream one would produce an inconsistent view. Per-character
        reconciliation resumes on next boot. Unknown event types ALSO pause
        the loop: advancing past an unknown event would permanently lose it
        (a later version that adds the handler couldn't recover it), and
        applying subsequent known events could silently fork the view from
        the unreplayed mutation. Writes are gated by _append_unlocked's
        ALL_EVENT_TYPES check, so an unknown type here means a rollback
        to an older binary — operator must upgrade back or manually
        surgery the log.

        Two things that are NOT handler failures and are handled apart from
        them, because they leave different state on disk:
          - the sentinel moved under us (another writer advanced it past
            what this round assumed). The tail this round is carrying is
            now stale: it sits behind that writer's sentinel, and applying
            it as-is would push older payloads over newer values with no
            later replay to correct them. So the round re-reads the journal
            from its own last-applied position through to the END of the
            file and replays that, in journal order — the queued repairs
            still land, and the other writer's events land after them, so
            the newest payload for any given entry wins. The sentinel is
            frozen from then on: writing our older id back would return the
            other writer's events to the tail, and one of them without a
            registered handler would wedge every future boot. Nothing needs
            to be written at the end either — the other writer already
            parked the sentinel at the journal end.
            That end is a snapshot, so the frozen pass re-probes for newly
            appended events every time it drains, up to _MAX_FROZEN_RESCANS
            times. An event landing after the snapshot is not in the tail,
            yet the stale payloads queued ahead of it still are: replaying
            those would push older values over the view it just wrote, and
            with the sentinel now parked on its id no later boot replays it
            back.
          - the sentinel write failed (IO). The handler already persisted;
            only the progress marker is behind, so the event replays
            idempotently next boot. Logged as such, not as a handler fault.

        Re-applying events that were already applied is safe by the same
        property the whole design leans on elsewhere (read_since falls back
        to a full replay when the sentinel is missing or compacted away):
        handlers carry full-snapshot payloads and are idempotent.
        """
        last_applied = await self._event_log.aread_sentinel(name)
        tail = await self._event_log.aread_since(name, last_applied)
        applied_count = 0
        # 每条事件都要 CAS 一次，而且是在跑 handler **之前**：expected 是「本轮
        # replay 认为盘上哨兵应该处在的位置」，第一条是出发时读到的 last_applied，
        # 之后是上一条刚写进去的 event_id。对不上说明有别的写者动过哨兵，这时
        # 手上的尾巴已经陈旧，一条都不能应用，见 apply_and_advance。
        expected_sentinel = last_applied
        # 一旦发现哨兵已经不归本轮 replay 管，就冻结哨兵，同时把待办重新从日志里
        # 取一遍（见下面 except 分支）：手上这份尾巴已经陈旧，不能照原样应用。
        sentinel_frozen = False
        # 冻结之后哨兵不再前进，于是「重扫到日志末尾」这句话里的「末尾」是一次快照，
        # 而写者还在往后追加。快照之后落地的那条事件不在手上这份 tail 里，可它前面
        # 的陈旧 payload 却还在等着被重放 —— 重放下去就是拿旧值盖掉它刚写好的 view，
        # 而哨兵此刻停在**它**的 id 上，下次开机 read_since 从它之后开始读，那条事件
        # 不会被重放回来纠正。丢的不是这一轮的进度，是那个写者的内容，永久性的。
        # 所以扫完一轮要再朝末尾探一次，直到探不到新东西为止。
        rescans_left = _MAX_FROZEN_RESCANS
        i = 0
        while True:
            if i >= len(tail):
                if not sentinel_frozen:
                    return applied_count
                extra = await self._event_log.aread_since(name, expected_sentinel)
                if not extra:
                    return applied_count
                if rescans_left <= 0:
                    # 写入一直不停时必须有个头，否则这一轮永远收不了尾、启动卡死。
                    # 停下来是有代价的（剩下那些在哨兵前面，下次开机也不会重放），
                    # 所以报出来，而不是静悄悄地截断。
                    logger.warning(
                        f"[Reconciler] {name}: 哨兵冻结后连续 {_MAX_FROZEN_RESCANS} 轮"
                        f"都有新事件追加，停止重扫；仍有 {len(extra)} 条未重放且位于"
                        f"哨兵之前，下次开机不会自动补上"
                    )
                    return applied_count
                rescans_left -= 1
                tail = extra
                i = 0
                continue
            event = tail[i]
            event_type = event.get('type')
            event_id = event.get('event_id')
            if event_type not in self._handlers:
                logger.warning(
                    f"[Reconciler] {name}: 遇到未注册事件类型 {event_type!r} "
                    f"(id={event_id})，暂停 replay，sentinel 保留在上一条已应用事件；"
                    f"请检查是否需要升级到支持该类型的版本"
                )
                return applied_count
            handler = self._handlers[event_type]
            payload = event.get('payload') or {}
            try:
                # Handler自己 load → apply → save，见 ApplyHandler 契约。
                # 整段（handler + 哨兵推进）交给 apply_and_advance：在
                # per-character 锁内、worker 线程上跑完，与 record_and_save
                # 收口成同一个写者。handler 抛出时锁内的哨兵写不会执行，
                # 异常穿过 to_thread 由下面的 except 接住 —— 失败语义不变。
                changed = await self._event_log.aapply_and_advance(
                    name, event_id, lambda: handler(name, payload),
                    expected_sentinel=expected_sentinel,
                    advance=not sentinel_frozen,
                )
                if changed:
                    applied_count += 1
                expected_sentinel = event_id
                i += 1
            except SentinelConflictError as e:
                # 不是失败，而且这一条并没有被应用（冲突判定排在 handler 之前）。
                # 别的写者已经把哨兵推到更靠后的位置了 —— 它写哨兵时就等于声称
                # 在它之前的都已应用，而手上这份尾巴恰恰在它后面，所以这份尾巴
                # 现在是陈旧的：照原样应用会拿旧 payload 盖掉它刚写好的值，而它
                # 那条更新的事件在哨兵前面，不会再被重放回来纠正。
                #
                # 所以不是「停下」也不是「照旧应用」，而是按日志顺序重取待办：
                # 从本轮自己的已应用位置（expected_sentinel）一直读到文件末尾。
                # 这样排队的修复照样落盘，那个写者的事件排在它们后面被重放一遍，
                # 同一条目的最新 payload 最后写入 —— 顺序由日志里的行位置决定，
                # 正是事件日志本来的语义。重复应用无害：handler 是全量快照赋值、
                # 幂等，read_since 在哨兵丢失时本来就会全量重放。
                #
                # 哨兵从此冻结（advance=False）。写回旧 id 会让那个写者的事件回到
                # 尾巴，其中任何一条没注册 handler 的都会让此后每次开机都停在
                # 「未注册事件类型」上，该角色再也 reconcile 不了。收尾也不用补写：
                # 对方已经把哨兵停在日志末尾，正是本轮重扫的终点。
                if sentinel_frozen:
                    # 结构上到不了：冻结之后 advance=False，apply_and_advance
                    # 不再做 CAS，也就不会再抛这个异常。留一条兜底，免得将来
                    # 有人改动 advance 的语义时把这里变成死循环。
                    logger.warning(
                        f"[Reconciler] {name}/{event_type}/{event_id}: 哨兵冻结后"
                        f"仍报冲突，停止本轮 replay: {e}"
                    )
                    return applied_count
                sentinel_frozen = True
                logger.warning(
                    f"[Reconciler] {name}/{event_type}/{event_id}: 哨兵已被其他写者"
                    f"推进，手上的尾巴已陈旧；改为从本轮已应用位置重扫到日志末尾并"
                    f"按日志顺序重放，此后不再推进哨兵: {e}"
                )
                tail = await self._event_log.aread_since(name, expected_sentinel)
                i = 0
            except SentinelAdvanceError as e:
                # handler 成功、哨兵写失败。归因必须和 handler 失败分开：盘上
                # 是「view 已改、哨兵没动」，下次开机幂等重放这一条。
                logger.warning(
                    f"[Reconciler] {name}/{event_type}/{event_id} 哨兵写入失败"
                    f"（handler 已成功应用）: {e}；下次开机重放该条"
                )
                return applied_count
            except Exception as e:
                logger.warning(
                    f"[Reconciler] {name}/{event_type}/{event_id} handler 失败: {e}；"
                    f"保留 sentinel 在上一条位置，下次重试"
                )
                return applied_count
