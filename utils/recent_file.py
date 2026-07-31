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

"""Process-wide serialization for every read and write of ``<character>/recent.json``.

This module is deliberately a leaf: it depends only on ``utils.file_utils``,
``os`` and ``threading``. ``memory``, ``main_routers`` and ``utils`` can all
import it without dragging in the memory god-module and without inverting the
dependency direction.

Every read and every write of a recent file must go through here. Readers are
not optional: on Windows a plain ``open()`` on the target is enough to make a
concurrent ``os.replace()`` fail with ``PermissionError``, so leaving readers
outside the lock would keep breaking writers.

Both helpers block on file IO. Call them from a worker thread
(``asyncio.to_thread``), never directly from the event loop.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from utils.file_utils import atomic_write_json

__all__ = [
    "clear_recent_pending",
    "get_recent_pending",
    "get_recent_pending_unlocked",
    "move_recent_pending",
    "recent_file_lock",
    "read_recent_text",
    "read_recent_text_unlocked",
    "set_recent_pending_unlocked",
    "write_recent_payload",
    "write_recent_payload_unlocked",
]


# ── per-path lock registry ────────────────────────────────────────────────
# 锁按**文件路径**建，不按角色名：main_routers 那几个写者是按 filename 解析路径
# 的（resolve_recent_file_path 还带 legacy 布局回退），它们拿不到「角色名 →
# manager」的映射；而且两个角色误配到同一个文件时，按名字切的锁根本不互斥。
# 资源的身份是文件。
#
# 模块级而不是实例级：reload_memory_components 会构造新的
# CompressedRecentHistoryManager，而旧实例上的 review / 后台压缩 task 还握着旧
# 引用在跑。实例级的锁在那个窗口里零互斥。memory_server/runtime.py 特意复用
# EventLog 实例，理由就是这个。
#
# threading.Lock 而不是 asyncio.Lock：
# 1) 要互斥的是 open() 与 os.replace() 两个 syscall，它们跑在 to_thread 的
#    worker 线程上，asyncio 锁管不到跨线程；
# 2) 模块级 asyncio.Lock 一旦真发生争用就绑定当时的 event loop。本仓库的 recent
#    单测是一个用例一个 asyncio.run，第二个有争用的用例会直接 RuntimeError，而且
#    失败后锁还残留成已持有状态；
# 3) threading.Lock 把 json.dumps 也关进临界区（atomic_write_json 是先 dumps 再
#    写），锁外残留的 mutation 撞不出 "dictionary changed size during iteration"。
# 同一范式见 memory/event_log.py 与 app/memory_server/gates.py。
#
# 故意不用 RLock：本模块所有 *_unlocked 函数都要求调用方已持锁，重入只会掩盖
# 「嵌套 RMW 的内层落盘把外层改了一半的状态写进磁盘」这类真 bug。
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_PENDING: dict[str, list[Any]] = {}
_PENDING_GUARD = threading.Lock()


def _lock_key(path: Any) -> str:
    """Normalize a path into the registry key that identifies the underlying file."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def recent_file_lock(path: Any) -> threading.Lock:
    """Return the process-wide lock guarding one recent.json path."""
    key = _lock_key(path)
    lock = _LOCKS.get(key)
    if lock is not None:
        return lock
    # 双检：happy path 不进 guard；只有第一次见到这个路径才付一次锁开销。
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
    return lock


def get_recent_pending_unlocked(path: Any) -> list[Any]:
    """Return a copy of the unpersisted batch while the path lock is held."""
    with _PENDING_GUARD:
        return list(_PENDING.get(_lock_key(path), ()))


def get_recent_pending(path: Any) -> list[Any]:
    """Return a copy of the unpersisted batch under the path lock."""
    with recent_file_lock(path):
        return get_recent_pending_unlocked(path)


def set_recent_pending_unlocked(path: Any, messages: list[Any]) -> None:
    """Replace the unpersisted batch while the path lock is held."""
    key = _lock_key(path)
    with _PENDING_GUARD:
        if messages:
            _PENDING[key] = list(messages)
        else:
            _PENDING.pop(key, None)


def clear_recent_pending(path: Any) -> None:
    """Discard unpersisted messages after an authoritative delete."""
    with recent_file_lock(path):
        set_recent_pending_unlocked(path, [])


def move_recent_pending(source_path: Any, target_path: Any) -> None:
    """Move unpersisted messages when a character recent file is renamed."""
    source_key = _lock_key(source_path)
    target_key = _lock_key(target_path)
    if source_key == target_key:
        return
    ordered = sorted(
        ((source_key, recent_file_lock(source_path)), (target_key, recent_file_lock(target_path))),
        key=lambda item: item[0],
    )
    with ordered[0][1], ordered[1][1], _PENDING_GUARD:
        source_pending = _PENDING.pop(source_key, None)
        if source_pending:
            _PENDING[target_key] = list(_PENDING.get(target_key, ())) + source_pending


def read_recent_text_unlocked(path: Any, *, encoding: str = "utf-8") -> str:
    """Read the raw file text. The caller MUST already hold ``recent_file_lock(path)``."""
    with open(path, "r", encoding=encoding) as handle:
        return handle.read()


def read_recent_text(path: Any, *, encoding: str = "utf-8") -> str:
    """Read the raw file text under the file lock."""
    with recent_file_lock(path):
        return read_recent_text_unlocked(path, encoding=encoding)


def write_recent_payload_unlocked(path: Any, payload: Any) -> None:
    """Serialize and atomically replace the file. The caller MUST already hold the lock.

    ``atomic_write_json`` creates the parent directory itself, so callers do not
    need a separate ``makedirs`` step.

    A raised exception means the target was NOT replaced: ``_replace_with_busy_retry``
    is the last statement in ``atomic_write_text`` and everything after it only
    cleans up the temp file. Callers rely on that to retry a failed batch without
    any deduplication.
    """
    atomic_write_json(path, payload, indent=2, ensure_ascii=False)


def write_recent_payload(path: Any, payload: Any) -> None:
    """Authoritatively replace the file and invalidate older pending messages."""
    with recent_file_lock(path):
        write_recent_payload_unlocked(path, payload)
        set_recent_pending_unlocked(path, [])
