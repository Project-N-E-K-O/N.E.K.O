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
Outbox — per-character persistent background task queue.

Why it exists (P1 fix for fatal issue #1):
  _spawn_background_task(extract_facts/synth/...) was memory-only; tasks in
  flight when the process got killed had no re-run mechanism. User rebuts →
  killed mid-LLM-call → after restart facts.json never gets that rebuttal
  fact, and the whole rebuttal → reflection → persona chain is dead from the
  start.

Design:
  - `outbox.ndjson` per character, append-only, one JSON record per line.
  - Each op has a pending and a done record, paired by op_id. On startup the
    file is scanned; ops that are "pending with no matching done" count as
    unfinished and need re-running.
  - The Outbox itself does no handler registration / task dispatch — that's
    the memory_server orchestration layer's job. This module only persists,
    scans, and compacts.

Idempotency: the caller must ensure handler(name, payload) is naturally
idempotent (facts SHA-256 dedup, deterministic reflection ids, mark_absorbed
only flipping False→True, etc.). The Outbox makes no at-most-once guarantee;
in replay scenarios a handler may fire multiple times.

No SQLite / Redis, per the CLAUDE constraints. Just ndjson + per-character
threading.Lock + asyncio.to_thread for the sync/async twin implementation.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from datetime import datetime

from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_text
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")


# op_type 常量，避免魔法字符串散落。
#
# 字符串值是 outbox.ndjson 里持久化的 op_type 字面值——视为不可变 wire-format
# schema id（同数据库列名），重命名 Python 符号时**不能**改值，否则旧机器上
# 残留的 pending op 在 replay 时 ``_OUTBOX_HANDLERS.get(op_type)`` 拿不到 handler
# 会被静默 skip。``OP_POST_TURN_SIGNALS`` 的值仍是 ``"extract_facts"``：PR-1
# 引入时 handler 主操作是 Stage-1 fact 抽取，PR #1346 把 Stage-1（ON-mode）剥离
# 到 ``_periodic_signal_extraction_loop`` 后，handler 实际只做 counter bump +
# 复读嗅探 + check_feedback + OFF-mode Stage-1 fallback——符号名随之更新，值保留。
OP_POST_TURN_SIGNALS = "extract_facts"
OP_SYNTH_REFLECTION = "synth_reflection"
OP_CHECK_FEEDBACK = "check_feedback"
OP_RESOLVE_CORRECTIONS = "resolve_corrections"


# pending 记录在 outbox 中积累超过此阈值时触发自动 compact（启动期调用）
_COMPACT_LINES_THRESHOLD = 1000


class Outbox:
    """Per-character append-only ndjson job log.

    Public API:
      - append_pending(name, op_type, payload) → op_id
      - append_done(name, op_id)
      - pending_ops(name) → list[record]
      - compact(name) → int (number of lines dropped)

    Every method has an async twin (a-prefix).
    """

    def __init__(self):
        self._config_manager = get_config_manager()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ── path / lock ─────────────────────────────────────────────

    def _outbox_path(self, name: str) -> str:
        # 延迟 import 避开 memory/__init__.py ↔ memory/outbox.py 循环依赖
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'outbox.ndjson',
        )

    def _get_lock(self, name: str) -> threading.Lock:
        if name not in self._locks:
            with self._locks_guard:
                if name not in self._locks:
                    self._locks[name] = threading.Lock()
        return self._locks[name]

    # ── append (sync) ───────────────────────────────────────────

    def _write_line(self, path: str, line: str) -> None:
        """Single O_APPEND write + fsync, best-effort durability.

        The lock is held by the caller; write+flush+fsync happen in one go.
        fsync failure (some filesystems lack support) degrades to a warning,
        no raise.
        """
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError as e:
                logger.debug(f"[Outbox] fsync 失败（可忽略）: {e}")

    def append_pending(self, name: str, op_type: str, payload: dict) -> str:
        """Register a pending op; returns the newly assigned op_id."""
        op_id = str(uuid.uuid4())
        record = {
            'op_id': op_id,
            'type': op_type,
            'payload': payload,
            'status': 'pending',
            'ts': datetime.now().isoformat(),
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._get_lock(name):
            self._write_line(self._outbox_path(name), line)
        return op_id

    def append_done(self, name: str, op_id: str) -> None:
        """Mark an op done. The done record carries no payload (the pending line is the source of truth)."""
        record = {
            'op_id': op_id,
            'status': 'done',
            'ts': datetime.now().isoformat(),
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._get_lock(name):
            self._write_line(self._outbox_path(name), line)

    def append_attempt(self, name: str, op_id: str) -> None:
        """Record one failed handler attempt (Site 7 liveness fallback).

        Scans tally attempts per op_id; the caller (memory_server._run_outbox_op)
        appends done as a dead-letter and abandons the op once the tally reaches
        ``MEMORY_LIVENESS_MAX_ATTEMPTS``. Otherwise a poison op (a payload that
        makes the handler raise forever) re-runs on every restart and never
        leaves pending → ``compact`` blocks forever → outbox.ndjson grows
        linearly.
        """
        record = {
            'op_id': op_id,
            'status': 'attempt',
            'ts': datetime.now().isoformat(),
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._get_lock(name):
            self._write_line(self._outbox_path(name), line)

    # ── scan ────────────────────────────────────────────────────

    def _read_all_records(self, path: str) -> list[dict]:
        """Read the whole file; returns all parsable records, skipping corrupt lines with a warning."""
        if not os.path.exists(path):
            return []
        records: list[dict] = []
        with open(path, encoding='utf-8') as f:
            for lineno, raw in enumerate(f, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    records.append(json.loads(raw))
                except json.JSONDecodeError:
                    logger.warning(
                        f"[Outbox] {path} 第 {lineno} 行无法解析，跳过: {raw[:120]!r}"
                    )
        return records

    def pending_ops(self, name: str) -> list[dict]:
        """Return op records that are pending with no matching done (in registration order).

        Each returned record carries the non-persistent field ``_attempt_count``
        (int), the number of ``status='attempt'`` lines counted during the scan.
        The caller uses it to judge the dead-letter threshold. The returned
        dicts are fresh instances JSON-loaded by this round's
        ``_read_all_records``; attaching ``_attempt_count`` cannot contaminate
        the on-disk pending lines.
        """
        path = self._outbox_path(name)
        with self._get_lock(name):
            records = self._read_all_records(path)

        pending: dict[str, dict] = {}
        attempts: dict[str, int] = {}
        for rec in records:
            op_id = rec.get('op_id')
            status = rec.get('status')
            if not op_id:
                logger.warning(
                    f"[Outbox] 跳过缺 op_id 的记录（字段集: {sorted(rec.keys())!r}）"
                )
                continue
            if status == 'pending':
                pending[op_id] = rec
            elif status == 'done':
                pending.pop(op_id, None)
                attempts.pop(op_id, None)
            elif status == 'attempt':
                attempts[op_id] = attempts.get(op_id, 0) + 1
        for op_id, rec in pending.items():
            rec['_attempt_count'] = attempts.get(op_id, 0)
        return list(pending.values())

    # ── compact ─────────────────────────────────────────────────

    def compact(self, name: str) -> int:
        """Rewrite outbox.ndjson keeping only unfinished pending lines + their
        attempt lines. Returns the number of lines dropped.

        Atomic replacement via atomic_write_text. Appends blocked on the lock
        during compaction continue into the new file after the rename.

        Attempt-line handling (Site 7 liveness): attempt lines of still-pending
        ops are kept (the attempt tally decides dead-letter timing; losing it
        would reset the counter after a restart); for done ops the matching
        attempt lines are dropped too (nobody reads the tally after done).
        """
        path = self._outbox_path(name)
        with self._get_lock(name):
            records = self._read_all_records(path)
            pending: dict[str, dict] = {}
            attempts_by_op: dict[str, list[dict]] = {}
            for rec in records:
                op_id = rec.get('op_id')
                status = rec.get('status')
                if not op_id:
                    continue
                if status == 'pending':
                    pending[op_id] = rec
                elif status == 'done':
                    pending.pop(op_id, None)
                    attempts_by_op.pop(op_id, None)
                elif status == 'attempt':
                    attempts_by_op.setdefault(op_id, []).append(rec)

            kept_records: list[dict] = []
            for rec in pending.values():
                kept_records.append(rec)
            for op_id, attempt_recs in attempts_by_op.items():
                if op_id in pending:
                    kept_records.extend(attempt_recs)

            total_lines = len(records)
            kept = len(kept_records)
            if total_lines == kept:
                return 0  # 没有可丢弃的行，避免无用 IO

            if kept == 0:
                # 全部已完成 —— 直接清空
                atomic_write_text(path, '', encoding='utf-8')
            else:
                body = '\n'.join(
                    json.dumps(r, ensure_ascii=False) for r in kept_records
                ) + '\n'
                atomic_write_text(path, body, encoding='utf-8')
            return total_lines - kept

    def maybe_compact(self, name: str) -> int:
        """Compact only above the threshold (called at startup or by the low-frequency scan)."""
        path = self._outbox_path(name)
        if not os.path.exists(path):
            return 0
        try:
            # 只数行数，不解析
            line_count = 0
            with open(path, encoding='utf-8') as f:
                for _ in f:
                    line_count += 1
            if line_count < _COMPACT_LINES_THRESHOLD:
                return 0
        except OSError as e:
            logger.debug(f"[Outbox] {name}: 行数统计失败: {e}")
            return 0
        return self.compact(name)

    # ── async duals ─────────────────────────────────────────────

    async def aappend_pending(self, name: str, op_type: str, payload: dict) -> str:
        return await asyncio.to_thread(self.append_pending, name, op_type, payload)

    async def aappend_done(self, name: str, op_id: str) -> None:
        await asyncio.to_thread(self.append_done, name, op_id)

    async def aappend_attempt(self, name: str, op_id: str) -> None:
        await asyncio.to_thread(self.append_attempt, name, op_id)

    async def apending_ops(self, name: str) -> list[dict]:
        return await asyncio.to_thread(self.pending_ops, name)

    async def acompact(self, name: str) -> int:
        return await asyncio.to_thread(self.compact, name)

    async def amaybe_compact(self, name: str) -> int:
        return await asyncio.to_thread(self.maybe_compact, name)
