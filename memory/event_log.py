# -*- coding: utf-8 -*-
"""
EventLog — per-character append-only 审计 + 重放日志（P2 基础设施）。

为什么存在（对应 docs/design/memory-event-log-rfc.md）：
  P0 持久化了 rebuttal 游标；P1 加了 outbox 让后台 task 被 kill 后可以
  补跑。剩下的结构性问题是：视图（facts.json / reflections.json /
  persona.json）是"状态转移"的唯一记录——没有有序历史，所以"写视图一半
  崩"是不可见的，跨文件不变量也无法核查。

  本模块加 events.ndjson（per character）：每次状态转移**先**写事件、
  再写视图。启动期 reconciler 对比 log tail 与哨兵
  (events_applied.json)，把 view 没落盘的事件重放到视图里。

非目标：
  - 不是完整的 event sourcing（视图仍是可手写的"真相源"）。
  - 不是规则引擎或状态机 DSL。
  - 不是跨角色的（per character 文件；架构假设单写者）。

写入纪律（RFC §3.4）：
  所有会发事件的写点必须走 record_and_save，它把
  load → append → mutate → save → sentinel-advance 五步放进一把
  per-character threading.Lock 里，整体包在一个 asyncio.to_thread
  worker 中。不跨 await 持锁——延续 outbox / cursors 的模式。

  append 先于 mutate 的原因：load 返回的 view 常是 manager 持有的共享
  cache；若先 mutate 后 append 而 append 抛错（例如 fsync OSError），
  cache 会留下"无事件对应的脏改动"，后续任一次正常 save 都会把它刷
  盘，破坏 event↔view 对应关系。先 append 成功再 mutate 保证 cache
  只在事件已耐久落盘后才被改动。
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

# Apply handler: takes (character_name, event_payload) and is responsible for
# loading the relevant view, applying the event, AND persisting it before
# returning. Returns True if the apply actually changed state; False if
# idempotent no-op. Critical invariant: sentinel only advances after handler
# returns successfully, so handler MUST persist before returning — otherwise
# a process crash between handler-return and sentinel-write would lose the
# change while marking the event as applied.
#
# Handlers MUST be synchronous (no async/await) and use the sync IO helpers
# (atomic_write_json, not its a-twin): Reconciler.areconcile calls them
# directly without await. An async handler would return a coroutine that
# never runs, silently breaking reconciliation.
ApplyHandler = Callable[[str, dict], bool]


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
        """Append + flush + fsync. fsync 失败需上抛——record_and_save 的
        耐久性合同是"事件先落盘再推进 view"，若 fsync 静默失败，后续
        view.save 仍成功即破坏 event↔view 对应关系、reconciler 也无从补救。
        由调用方（record_and_save）负责处理异常，保证 view 不会被错误推进。"""
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
            f.flush()
            os.fsync(f.fileno())

    def _append_unlocked(self, name: str, event_type: str, payload: dict) -> str:
        """Write one event record under an already-held lock. Returns event_id.

        Fails fast on unknown event_type. 若放行未登记类型，一旦 record_and_save
        在 event append 后、view save 前崩溃，重启后 Reconciler 没有 handler
        只能跳过（且若反推到 pause-on-unknown，整条 tail 都 stuck），
        修复路径昂贵。写入点挡住是最便宜的防线。
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
        """
        last_applied = await self._event_log.aread_sentinel(name)
        tail = await self._event_log.aread_since(name, last_applied)
        applied_count = 0
        for event in tail:
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
            try:
                # Handler自己 load → apply → save，见 ApplyHandler 契约。
                changed = handler(name, event.get('payload') or {})
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
