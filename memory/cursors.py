# -*- coding: utf-8 -*-
"""
CursorStore — 持久化各类定期扫描任务的游标。

为什么存在：之前 _last_rebuttal_check 等游标只驻留内存，关机→重启后丢失，
默认只回扫 1 小时，关机期间的反驳对话永远不会被扫到（致命点 2）。

设计：per-character cursors.json，键值对 {cursor_key: ISO8601 timestamp}。
提供 sync/async 对偶方法，保持与 facts.py / reflection.py 风格一致。
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime

from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")


# cursor 键名常量，避免字符串魔法值散落
CURSOR_REBUTTAL_CHECKED_UNTIL = "rebuttal_checked_until"
CURSOR_EXTRACTED_UNTIL = "extracted_until"  # 为 P1 outbox / fact_extraction 预留


class CursorStore:
    """管理 per-character 游标文件 cursors.json 的读写。

    cursor 语义：datetime 类型的"处理至此刻"标记。
    值为 ISO8601 字符串；不存在 = 从未处理，调用方决定回退策略。

    并发：per-character threading.Lock 保护缓存与落盘。get/set 都在同一把锁下。
    """

    def __init__(self):
        self._config_manager = get_config_manager()
        self._cache: dict[str, dict[str, datetime]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ── path / lock ─────────────────────────────────────────────

    def _cursor_path(self, name: str) -> str:
        # 延迟 import 避开 memory/__init__.py ↔ memory/cursors.py 循环依赖
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'cursors.json',
        )

    def _get_lock(self, name: str) -> threading.Lock:
        if name not in self._locks:
            with self._locks_guard:
                if name not in self._locks:
                    self._locks[name] = threading.Lock()
        return self._locks[name]

    # ── load / save (锁由调用方持有) ────────────────────────────

    def _load_unlocked(self, name: str) -> dict[str, datetime]:
        """加载单角色 cursor，缓存到内存。失败返回空 dict（非致命）。

        调用方必须已持有 self._get_lock(name)。
        """
        if name in self._cache:
            return self._cache[name]
        data: dict[str, datetime] = {}
        path = self._cursor_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if isinstance(v, str):
                            try:
                                data[k] = datetime.fromisoformat(v)
                            except ValueError:
                                logger.warning(
                                    f"[CursorStore] {name}: 忽略无法解析的游标 {k}={v!r}"
                                )
                else:
                    logger.warning(
                        f"[CursorStore] {name}: cursors.json 非 dict，已忽略"
                    )
            except Exception as e:
                logger.warning(f"[CursorStore] {name}: 读取 cursors.json 失败: {e}")
        self._cache[name] = data
        return data

    # ── public API (sync) ───────────────────────────────────────

    def get_cursor(self, name: str, key: str) -> datetime | None:
        """读取指定游标；不存在返回 None。"""
        with self._get_lock(name):
            data = self._load_unlocked(name)
            return data.get(key)

    def set_cursor(self, name: str, key: str, value: datetime) -> None:
        """写入游标并原子落盘。多个 key 共存，单个 key 的更新不会覆盖其它。

        原子性：先构造 serialized dict 写盘，**成功后**再更新内存 cache。
        若 atomic_write_json 抛异常，cache 保持旧值——避免 cache 与磁盘发散
        导致同进程后续 get_cursor 读到未持久化的脏值。
        """
        with self._get_lock(name):
            data = self._load_unlocked(name)
            serialized: dict[str, str] = {
                k: v.isoformat() for k, v in data.items() if k != key
            }
            serialized[key] = value.isoformat()
            atomic_write_json(self._cursor_path(name), serialized)
            data[key] = value

    # ── public API (async) ──────────────────────────────────────

    async def aget_cursor(self, name: str, key: str) -> datetime | None:
        return await asyncio.to_thread(self.get_cursor, name, key)

    async def aset_cursor(self, name: str, key: str, value: datetime) -> None:
        await asyncio.to_thread(self.set_cursor, name, key, value)
