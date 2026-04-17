# -*- coding: utf-8 -*-
"""
Outbox — per-character 持久化后台任务队列。

为什么存在（P1 修复致命点 1）：
  _spawn_background_task(extract_facts/synth/...) 纯内存，进程被 kill
  途中的 task 没有任何补跑机制。用户说反驳 → LLM 调用中 kill → 重启后
  facts.json 里永远不会有那条反驳 fact，整条 rebuttal → reflection →
  persona 链路从起点就死。

设计：
  - `outbox.ndjson` per character，append-only，每行一条 JSON 记录。
  - 每个 op 有 pending 和 done 两条记录；op_id 配对。启动时扫描文件，
    "pending 且无对应 done" 的 op 视为未完成，需补跑。
  - Outbox 本身不管 handler 注册 / task 派发——那是 memory_server 编排层
    的事。本模块只负责落盘、扫描、compact。

幂等：caller 必须保证 handler(name, payload) 天然幂等（facts SHA-256 dedup、
reflection 确定性 id、mark_absorbed 只做 False→True 等）。Outbox 不做
at-most-once 保证，重放场景下 handler 可能被多次触发。

不引入 SQLite / Redis，遵循 CLAUDE 约束。只用 ndjson + per-character
threading.Lock + asyncio.to_thread 实现同步/异步对偶。
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


# op_type 常量，避免魔法字符串散落
OP_EXTRACT_FACTS = "extract_facts"
OP_SYNTH_REFLECTION = "synth_reflection"
OP_CHECK_FEEDBACK = "check_feedback"
OP_RESOLVE_CORRECTIONS = "resolve_corrections"


# pending 记录在 outbox 中积累超过此阈值时触发自动 compact（启动期调用）
_COMPACT_LINES_THRESHOLD = 1000


class Outbox:
    """Per-character append-only ndjson job log.

    公共 API：
      - append_pending(name, op_type, payload) → op_id
      - append_done(name, op_id)
      - pending_ops(name) → list[record]
      - compact(name) → int（被丢弃的行数）

    每个方法都有对偶 async 版本（a-prefix）。
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
        """单次 O_APPEND 写入 + fsync 尽力持久化。

        lock 由调用方持有；write+flush+fsync 一次性完成。
        fsync 失败（某些 fs 不支持）降级为 warning，不抛。
        """
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError as e:
                logger.debug(f"[Outbox] fsync 失败（可忽略）: {e}")

    def append_pending(self, name: str, op_type: str, payload: dict) -> str:
        """登记一个 pending op，返回新分配的 op_id。"""
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
        """标记 op 完成。done 记录不需要再带 payload（pending 行是真相源）。"""
        record = {
            'op_id': op_id,
            'status': 'done',
            'ts': datetime.now().isoformat(),
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._get_lock(name):
            self._write_line(self._outbox_path(name), line)

    # ── scan ────────────────────────────────────────────────────

    def _read_all_records(self, path: str) -> list[dict]:
        """读取整个文件，返回所有可解析记录；损坏行跳过并 warn。"""
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
        """返回 pending 且无对应 done 的 op 记录（按登记顺序）。"""
        path = self._outbox_path(name)
        with self._get_lock(name):
            records = self._read_all_records(path)

        pending: dict[str, dict] = {}
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
        return list(pending.values())

    # ── compact ─────────────────────────────────────────────────

    def compact(self, name: str) -> int:
        """重写 outbox.ndjson，只保留未完成的 pending 行。返回丢弃行数。

        通过 atomic_write_text 原子替换。compact 期间被 lock 阻塞的 append
        会在 rename 完成后继续到新文件。
        """
        path = self._outbox_path(name)
        with self._get_lock(name):
            records = self._read_all_records(path)
            pending: dict[str, dict] = {}
            for rec in records:
                op_id = rec.get('op_id')
                status = rec.get('status')
                if not op_id:
                    continue
                if status == 'pending':
                    pending[op_id] = rec
                elif status == 'done':
                    pending.pop(op_id, None)

            total_lines = len(records)
            kept = len(pending)
            if total_lines == kept:
                return 0  # 没有可丢弃的行，避免无用 IO

            if kept == 0:
                # 全部已完成 —— 直接清空
                atomic_write_text(path, '', encoding='utf-8')
            else:
                body = '\n'.join(
                    json.dumps(r, ensure_ascii=False) for r in pending.values()
                ) + '\n'
                atomic_write_text(path, body, encoding='utf-8')
            return total_lines - kept

    def maybe_compact(self, name: str) -> int:
        """超过阈值才 compact（启动期或低频扫描时调用）。"""
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

    async def apending_ops(self, name: str) -> list[dict]:
        return await asyncio.to_thread(self.pending_ops, name)

    async def acompact(self, name: str) -> int:
        return await asyncio.to_thread(self.compact, name)

    async def amaybe_compact(self, name: str) -> int:
        return await asyncio.to_thread(self.maybe_compact, name)
