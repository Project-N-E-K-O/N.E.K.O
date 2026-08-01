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
from contextlib import contextmanager
from typing import Any, Iterator

from utils.file_utils import atomic_write_json

__all__ = [
    "RecentFileDeletedError",
    "RecentGenerationConflictError",
    "acquire_recent_file_locks",
    "activate_recent_paths",
    "capture_recent_generation",
    "clear_recent_deletions",
    "clear_recent_redirects",
    "fence_recent_deletions_and_clear_redirects",
    "get_recent_pending",
    "get_recent_pending_unlocked",
    "get_recent_content_version_unlocked",
    "recent_file_lock",
    "recent_file_access",
    "recent_file_locks",
    "read_recent_text",
    "read_recent_text_unlocked",
    "release_recent_file_locks",
    "restore_recent_deletions",
    "restore_recent_registry_state",
    "set_recent_pending_unlocked",
    "snapshot_recent_redirects",
    "snapshot_recent_deletions",
    "mark_recent_deleted",
    "merge_recent_pending_snapshot",
    "redirect_recent_paths",
    "restore_recent_redirects",
    "write_recent_payload",
    "write_recent_payload_unlocked",
]


class RecentFileDeletedError(RuntimeError):
    """Raised when a stale writer targets a character deleted in this process."""


class RecentGenerationConflictError(RuntimeError):
    """Raised when rollback no longer owns the identity transition it would undo."""


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
_REDIRECTS: dict[str, str] = {}
_DELETED: set[str] = set()
_GENERATIONS: dict[str, int] = {}
_CONTENT_VERSIONS: dict[str, int] = {}
_NEXT_GENERATION = 1


def _lock_key(path: Any) -> str:
    """Normalize a path into the registry key that identifies the underlying file."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _resolve_key_unlocked(key: str) -> str:
    seen: set[str] = set()
    while key in _REDIRECTS and key not in seen:
        seen.add(key)
        key = _REDIRECTS[key]
    return key


def recent_file_lock(path: Any) -> threading.Lock:
    """Return the process-wide lock guarding one recent.json path."""
    with _LOCKS_GUARD:
        key = _resolve_key_unlocked(_lock_key(path))
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
    return lock


def _next_generation_unlocked() -> int:
    global _NEXT_GENERATION
    generation = _NEXT_GENERATION
    _NEXT_GENERATION += 1
    return generation


def capture_recent_generation(path: Any) -> tuple[str, int]:
    """Capture the current identity token before admitting a write operation."""
    key = _lock_key(path)
    with _LOCKS_GUARD:
        return (key, _GENERATIONS.get(key, 0))


@contextmanager
def recent_file_access(
    path: Any, *, expected_generation: tuple[str, int] | None = None,
) -> Iterator[str]:
    """Lock a path and retry if a completed rename redirected it while waiting."""
    original_key = _lock_key(path)
    if expected_generation is None:
        expected_generation = capture_recent_generation(path)
    expected_key, expected_value = expected_generation
    if _lock_key(expected_key) != original_key:
        raise ValueError("recent generation token belongs to a different path")
    while True:
        with _LOCKS_GUARD:
            resolved_key = _resolve_key_unlocked(original_key)
            lock = _LOCKS.get(resolved_key)
            if lock is None:
                lock = threading.Lock()
                _LOCKS[resolved_key] = lock
        lock.acquire()
        with _LOCKS_GUARD:
            latest_key = _resolve_key_unlocked(original_key)
            latest_lock = _LOCKS.get(latest_key)
            stable = latest_key == resolved_key and latest_lock is lock
            stale = _GENERATIONS.get(original_key, 0) != expected_value
            deleted = latest_key in _DELETED
        if not stable:
            lock.release()
            continue
        if stale or deleted:
            lock.release()
            reason = "reused character name" if stale else "deleted character"
            raise RecentFileDeletedError(f"recent path belongs to a {reason}: {latest_key}")
        try:
            yield resolved_key
        finally:
            lock.release()
        return


@contextmanager
def recent_file_locks(paths: list[Any]) -> Iterator[None]:
    """Hold several physical recent-file locks in stable key order."""
    locks = acquire_recent_file_locks(paths)
    try:
        yield
    finally:
        release_recent_file_locks(locks)


def acquire_recent_file_locks(paths: list[Any]) -> list[threading.Lock]:
    """Acquire physical path locks for a transaction spanning multiple calls."""
    original_keys = {_lock_key(path) for path in paths}
    while True:
        with _LOCKS_GUARD:
            redirect_keys = _redirect_keys_touching_unlocked(original_keys)
            scoped_keys = original_keys | redirect_keys
            scoped_keys.update(
                _resolve_key_unlocked(key) for key in tuple(scoped_keys)
            )
            locks = []
            for key in sorted(scoped_keys):
                lock = _LOCKS.get(key)
                if lock is None:
                    lock = threading.Lock()
                    _LOCKS[key] = lock
                locks.append(lock)
        for lock in locks:
            lock.acquire()
        with _LOCKS_GUARD:
            latest_redirects = _redirect_keys_touching_unlocked(original_keys)
            latest_scope = original_keys | latest_redirects
            latest_scope.update(
                _resolve_key_unlocked(key) for key in tuple(latest_scope)
            )
            stable = latest_scope == scoped_keys
        if stable:
            return locks
        release_recent_file_locks(locks)


def release_recent_file_locks(locks: list[threading.Lock]) -> None:
    """Release locks returned by ``acquire_recent_file_locks``."""
    for lock in reversed(locks):
        lock.release()


def snapshot_recent_deletions(paths: list[Any]) -> set[str]:
    """Snapshot deletion markers for transaction rollback."""
    keys = {_lock_key(path) for path in paths}
    with _LOCKS_GUARD:
        return keys & _DELETED


def mark_recent_deleted(paths: list[Any]) -> None:
    """Reject stale writers after an authoritative character deletion."""
    with _LOCKS_GUARD:
        _DELETED.update(_lock_key(path) for path in paths)


def clear_recent_deletions(paths: list[Any]) -> None:
    """Allow writes when a character name is authoritatively reused."""
    with _LOCKS_GUARD:
        _DELETED.difference_update(_lock_key(path) for path in paths)


def restore_recent_deletions(paths: list[Any], snapshot: set[str]) -> None:
    """Restore deletion markers after a failed transaction."""
    keys = {_lock_key(path) for path in paths}
    with _LOCKS_GUARD:
        _DELETED.difference_update(keys)
        _DELETED.update(snapshot)


def get_recent_pending_unlocked(path: Any) -> list[Any]:
    """Return a copy of the unpersisted batch while the path lock is held."""
    with _PENDING_GUARD:
        return list(_PENDING.get(_lock_key(path), ()))


def get_recent_pending(path: Any) -> list[Any]:
    """Return a copy of the unpersisted batch under the path lock."""
    with recent_file_access(path) as resolved_path:
        return get_recent_pending_unlocked(resolved_path)


def set_recent_pending_unlocked(path: Any, messages: list[Any]) -> None:
    """Replace the unpersisted batch while the path lock is held."""
    key = _lock_key(path)
    with _PENDING_GUARD:
        if messages:
            _PENDING[key] = list(messages)
        else:
            _PENDING.pop(key, None)


def get_recent_content_version_unlocked(path: Any) -> int:
    """Return the process-local successful-write version while the file lock is held."""
    with _LOCKS_GUARD:
        key = _resolve_key_unlocked(_lock_key(path))
        return _CONTENT_VERSIONS.get(key, 0)


def merge_recent_pending_snapshot(snapshot: dict[Any, list[Any]]) -> None:
    """Restore pre-transaction messages without dropping batches queued later."""
    with recent_file_locks(list(snapshot)):
        for path, messages in snapshot.items():
            current = get_recent_pending_unlocked(path)
            set_recent_pending_unlocked(path, list(messages) + current)


def redirect_recent_paths(source_paths: list[Any], target_path: Any) -> None:
    """Redirect stale source-path users after a committed character rename."""
    target_key = _lock_key(target_path)
    with _LOCKS_GUARD:
        target_key = _resolve_key_unlocked(target_key)
        for source_path in source_paths:
            source_key = _lock_key(source_path)
            if source_key != target_key:
                _REDIRECTS[source_key] = target_key


def _redirect_keys_touching_unlocked(keys: set[str]) -> set[str]:
    def _chain_touches_reused_path(start_key: str) -> bool:
        current = start_key
        seen: set[str] = set()
        while current not in seen:
            if current in keys:
                return True
            seen.add(current)
            target = _REDIRECTS.get(current)
            if target is None:
                return False
            current = target
        return False

    return {
        key for key in _REDIRECTS
        if _chain_touches_reused_path(key)
    }


def snapshot_recent_redirects(paths: list[Any]) -> dict[str, str]:
    """Snapshot every redirect chain that touches one of the supplied paths."""
    keys = {_lock_key(path) for path in paths}
    with _LOCKS_GUARD:
        return {
            key: _REDIRECTS[key]
            for key in _redirect_keys_touching_unlocked(keys)
        }


def clear_recent_redirects(paths: list[Any]) -> dict[str, str]:
    """Forget redirects when an authoritative restore/delete reuses old paths."""
    keys = {_lock_key(path) for path in paths}

    with _LOCKS_GUARD:
        remove_keys = _redirect_keys_touching_unlocked(keys)
        removed = {key: _REDIRECTS.pop(key) for key in remove_keys}
    return removed


def activate_recent_paths(
    paths: list[Any],
) -> tuple[dict[str, str], set[str], set[str], dict[str, tuple[int, int]]]:
    """Atomically activate reused names and invalidate accesses from older identities."""
    keys = {_lock_key(path) for path in paths}
    with _LOCKS_GUARD:
        redirect_keys = _redirect_keys_touching_unlocked(keys)
        activation_scope = keys | redirect_keys
        deletion_snapshot = activation_scope & _DELETED
        generation_snapshot = {}
        redirects = {key: _REDIRECTS.pop(key) for key in redirect_keys}
        _DELETED.difference_update(keys)
        for key in activation_scope:
            previous = _GENERATIONS.get(key, 0)
            activated = _next_generation_unlocked()
            generation_snapshot[key] = (previous, activated)
            _GENERATIONS[key] = activated
    return redirects, activation_scope, deletion_snapshot, generation_snapshot


def fence_recent_deletions_and_clear_redirects(
    paths: list[Any],
) -> tuple[dict[str, str], set[str], set[str]]:
    """Atomically fence deleted targets and every alias that resolves to them."""
    keys = {_lock_key(path) for path in paths}
    with _LOCKS_GUARD:
        redirect_keys = _redirect_keys_touching_unlocked(keys)
        deletion_scope = keys | redirect_keys
        deletion_snapshot = deletion_scope & _DELETED
        _DELETED.update(deletion_scope)
        redirects = {key: _REDIRECTS.pop(key) for key in redirect_keys}
    return redirects, deletion_scope, deletion_snapshot


def restore_recent_redirects(redirects: dict[str, str]) -> None:
    """Restore redirects removed by a transaction that subsequently rolled back."""
    with _LOCKS_GUARD:
        _REDIRECTS.update({
            _lock_key(source): _lock_key(target)
            for source, target in redirects.items()
        })


def restore_recent_registry_state(
    paths: list[Any], redirects: dict[str, str], deletion_snapshot: set[str],
    generation_snapshot: dict[str, tuple[int, int]] | None = None,
) -> None:
    """Atomically restore redirect, deletion, and identity-token state."""
    keys = {_lock_key(path) for path in paths}
    with _LOCKS_GUARD:
        if generation_snapshot is not None:
            conflicts = [
                key
                for key, (_, activated) in generation_snapshot.items()
                if _GENERATIONS.get(_lock_key(key), 0) != activated
            ]
            if conflicts:
                raise RecentGenerationConflictError(
                    "recent identity changed after this transaction activated it"
                )
        for key in _redirect_keys_touching_unlocked(keys):
            _REDIRECTS.pop(key, None)
        _REDIRECTS.update({
            _lock_key(source): _lock_key(target)
            for source, target in redirects.items()
        })
        _DELETED.difference_update(keys)
        _DELETED.update(deletion_snapshot)
        if generation_snapshot is None:
            for key in keys:
                _GENERATIONS[key] = _next_generation_unlocked()
        else:
            for key, (previous, _) in generation_snapshot.items():
                key = _lock_key(key)
                if previous:
                    _GENERATIONS[key] = previous
                else:
                    _GENERATIONS.pop(key, None)


def read_recent_text_unlocked(path: Any, *, encoding: str = "utf-8") -> str:
    """Read the raw file text. The caller MUST already hold ``recent_file_lock(path)``."""
    with open(path, "r", encoding=encoding) as handle:
        return handle.read()


def read_recent_text(path: Any, *, encoding: str = "utf-8") -> str:
    """Read the raw file text under the file lock."""
    with recent_file_access(path) as resolved_path:
        return read_recent_text_unlocked(resolved_path, encoding=encoding)


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
    with _LOCKS_GUARD:
        key = _resolve_key_unlocked(_lock_key(path))
        _CONTENT_VERSIONS[key] = _CONTENT_VERSIONS.get(key, 0) + 1


def write_recent_payload(
    path: Any,
    payload: Any,
    *,
    expected_generation: tuple[str, int] | None = None,
) -> None:
    """Authoritatively replace the file and invalidate older pending messages."""
    with recent_file_access(
        path, expected_generation=expected_generation,
    ) as resolved_path:
        write_recent_payload_unlocked(resolved_path, payload)
        set_recent_pending_unlocked(resolved_path, [])
