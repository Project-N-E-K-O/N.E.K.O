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
CursorStore — persists the cursors of various periodic scan tasks.

Why it exists: cursors like _last_rebuttal_check used to live only in memory; a
shutdown→restart lost them, the default rescan window was just 1 hour, and rebuttal
conversations from the downtime would never get scanned (fatal issue #2).

Design: per-character cursors.json, key-value pairs {cursor_key: ISO8601 timestamp}.
Provides sync/async twin methods, consistent with the facts.py / reflection.py style.
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
    """Manages reads/writes of the per-character cursor file cursors.json.

    Cursor semantics: a datetime-typed "processed up to here" marker.
    Values are ISO8601 strings; absence = never processed, the caller decides the
    fallback strategy.

    Concurrency: a per-character threading.Lock guards the cache and disk writes.
    get/set both run under the same lock.
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
        """Load a single character's cursors and cache them in memory. Returns an empty
        dict on failure (non-fatal).

        The caller must already hold self._get_lock(name).
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
        """Read the given cursor; returns None when absent."""
        with self._get_lock(name):
            data = self._load_unlocked(name)
            return data.get(key)

    def set_cursor(self, name: str, key: str, value: datetime) -> None:
        """Write a cursor and persist atomically. Multiple keys coexist; updating one key
        never clobbers the others.

        Atomicity: build the serialized dict and write it to disk first, updating the
        in-memory cache **only after success**. If atomic_write_json raises, the
        cache keeps the old value — avoiding cache/disk divergence where a later
        get_cursor in the same process reads an unpersisted dirty value.
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
