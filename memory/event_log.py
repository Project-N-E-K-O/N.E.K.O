# -*- coding: utf-8 -*-
"""
EventLog — per-character append-only audit + reconciliation log.

Why this exists (P2, per memory-event-log-rfc.md):
  P0 persisted the rebuttal cursor; P1 added the outbox so background tasks
  can be replayed after a kill. The remaining structural problem is that
  views (facts.json / reflections.json / persona.json) are the ONLY record
  of state transitions — there is no ordered history, so partial view
  writes are invisible and cross-file invariants are not verifiable.

  This module adds events.ndjson per character: every state transition is
  recorded **before** the view changes. On startup the reconciler compares
  the log tail against a sentinel (events_applied.json) and replays any
  events whose view side didn't make it to disk.

Non-goals:
  - Not full event sourcing (views remain the hand-editable truth).
  - Not a rule engine or state machine DSL.
  - Not cross-character (per-character file; single-writer assumed).

Writing discipline (RFC §3.4):
  Every event-emitting write site MUST go through _record_and_save, which
  runs the whole load → mutate → append → save → sentinel-advance sequence
  inside a per-character threading.Lock, inside a single asyncio.to_thread
  worker. No asyncio.Lock across multiple await boundaries — follows the
  outbox / cursors pattern.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from datetime import datetime
from typing import Awaitable, Callable

from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_text, atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")


# ── Event type constants (RFC §3.3, 12 types) ────────────────────────────

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

ALL_EVENT_TYPES: frozenset[str] = frozenset({
    EVT_FACT_ADDED, EVT_FACT_ABSORBED, EVT_FACT_ARCHIVED,
    EVT_REFLECTION_SYNTHESIZED, EVT_REFLECTION_STATE_CHANGED,
    EVT_REFLECTION_SURFACED, EVT_REFLECTION_REBUTTED,
    EVT_PERSONA_FACT_ADDED, EVT_PERSONA_FACT_MENTIONED, EVT_PERSONA_SUPPRESSED,
    EVT_CORRECTION_QUEUED, EVT_CORRECTION_RESOLVED,
})


# ── Compaction thresholds (RFC §3.6) ─────────────────────────────────────

_COMPACT_LINES_THRESHOLD = 10_000   # file line count
_COMPACT_DAYS_THRESHOLD = 90        # age of oldest entry, days


# ── Type aliases for _record_and_save callbacks (RFC §3.4) ───────────────

SyncLoadView = Callable[[str], object]           # (character_name) -> view_obj
SyncMutateView = Callable[[object], None]        # (view_obj) -> None, mutates in place
SyncSaveView = Callable[[str, object], None]     # (character_name, view_obj) -> None

# Apply handler: takes (character_name, event_payload, view_obj) and mutates view_obj.
# Returns True if the apply actually changed state; False if idempotent no-op.
ApplyHandler = Callable[[str, dict, object], bool]


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
        """Append + flush + fsync. OSError on fsync is non-fatal."""
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError as e:
                logger.debug(f"[EventLog] fsync 失败（可忽略）: {e}")

    def _append_unlocked(self, name: str, event_type: str, payload: dict) -> str:
        """Write one event record under an already-held lock. Returns event_id."""
        if event_type not in ALL_EVENT_TYPES:
            logger.warning(
                f"[EventLog] {name}: 写入未登记事件类型 {event_type!r}（可能是 typo）"
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

    def advance_sentinel(self, name: str, event_id: str | None) -> None:
        """Persist the new sentinel atomically."""
        atomic_write_json(
            self._sentinel_path(name),
            {'last_applied_event_id': event_id, 'ts': datetime.now().isoformat()},
        )

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

    def should_compact(self, name: str) -> bool:
        line_count, oldest_ts = self._scan_head_and_count(self._events_path(name))
        if line_count >= _COMPACT_LINES_THRESHOLD:
            return True
        if oldest_ts is not None:
            age_days = (datetime.now() - oldest_ts).total_seconds() / 86400
            if age_days >= _COMPACT_DAYS_THRESHOLD:
                return True
        return False

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
                    logger.warning(
                        f"[EventLog] {name}: compact seed 使用未登记类型 {event_type!r}"
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

    def _should_compact_unlocked(self, name: str) -> bool:
        line_count, oldest_ts = self._scan_head_and_count(self._events_path(name))
        if line_count >= _COMPACT_LINES_THRESHOLD:
            return True
        if oldest_ts is not None:
            age_days = (datetime.now() - oldest_ts).total_seconds() / 86400
            if age_days >= _COMPACT_DAYS_THRESHOLD:
                return True
        return False

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
        load view → mutate in-memory → append event → save view → advance sentinel.

        All five steps run inside a single per-character threading.Lock so
        no two coroutines can race a read-modify-write cycle. Returns the
        newly-allocated event_id.

        The sync twins (load_X / save_X) are the right choice here: we are
        ALREADY on a worker thread (the _arecord_and_save a-twin hops us
        into one), and using async twins would pointlessly re-schedule
        through asyncio.to_thread and risk event-loop locking anti-patterns.
        """
        with self._get_lock(name):
            view = sync_load_view(name)
            sync_mutate_view(view)
            event_id = self._append_unlocked(name, event_type, payload)
            sync_save_view(name, view)
            # Inline sentinel write: still under the lock, still on this
            # worker thread — safe to use atomic_write_json sync.
            atomic_write_json(
                self._sentinel_path(name),
                {'last_applied_event_id': event_id, 'ts': datetime.now().isoformat()},
            )
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

    async def areconcile(self, name: str, view_provider: Callable[[str, str], object]) -> int:
        """Apply all tail events to views. Returns number of events applied.

        view_provider(name, event_type) returns the relevant view object for
        applying this event (e.g., load_facts / load_reflections / load_persona).
        The handler mutates view_provider's return and the caller is expected
        to save afterwards — but because the normal event-emission path
        already saved the view when the event was first written, reconcile
        is only triggered when that save FAILED. In that case the caller
        must save-after-apply; this scaffold returns the mutated views
        through the handler's True/False return.

        For P2.a.1 scope this is a handler-dispatch skeleton only. P2.b.1/2
        wire concrete handlers.
        """
        last_applied = await self._event_log.aread_sentinel(name)
        tail = await self._event_log.aread_since(name, last_applied)
        applied_count = 0
        for event in tail:
            event_type = event.get('type')
            event_id = event.get('event_id')
            if event_type not in self._handlers:
                logger.info(
                    f"[Reconciler] {name}: 跳过未注册事件类型 {event_type!r} (id={event_id})"
                )
                # Still advance sentinel past unknown types so we don't
                # re-process them next boot.
                await self._event_log.aadvance_sentinel(name, event_id)
                continue
            handler = self._handlers[event_type]
            try:
                view = view_provider(name, event_type)
                changed = handler(name, event.get('payload') or {}, view)
                if changed:
                    applied_count += 1
            except Exception as e:
                logger.warning(
                    f"[Reconciler] {name}/{event_type}/{event_id} handler 失败: {e}；"
                    f"保留 sentinel 在上一条位置，下次重试"
                )
                return applied_count
            await self._event_log.aadvance_sentinel(name, event_id)
        return applied_count
