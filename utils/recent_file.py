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

import json
import math
import os
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from utils.file_utils import atomic_write_json

__all__ = [
    "RECENT_IDEMPOTENCY_META_KEY",
    "RETIRED_KEYS_FILENAME",
    "VISIT_DIARY_KEY_RE",
    "RecentFileDeletedError",
    "RecentGenerationConflictError",
    "acquire_recent_file_locks",
    "activate_recent_paths",
    "capture_recent_generation",
    "clear_recent_deletions",
    "clear_recent_redirects",
    "fence_recent_deletions_and_clear_redirects",
    "keyed_entry_keys",
    "prune_retired_keys",
    "read_keyed_payload",
    "strip_idempotency_metadata",
    "read_retired_keys",
    "read_retired_keys_unlocked",
    "retire_removed_keyed_entries",
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
# Key locks by file path, not character name: main_routers writers resolve paths by filename,
# including resolve_recent_file_path's legacy-layout fallback, and lack character-name-to-manager
# mappings. Name-based locks also fail to exclude two characters misconfigured to share one file.
# The file is the resource identity.
#
# Module-level rather than instance-level: reload_memory_components constructs a new
# CompressedRecentHistoryManager while old review/background-compression tasks retain old
# references. Instance locks offer no exclusion during that window, the same reason memory_server/runtime.py
# deliberately reuses EventLog instances.
#
# Use threading.Lock rather than asyncio.Lock:
# 1) The mutually exclusive open() and os.replace() syscalls run in to_thread
#    workers, beyond asyncio locks' cross-thread control.
# 2) Module-level asyncio.Lock binds to the current loop on contention. Recent-history unit tests each
#    use asyncio.run; the second contended test raises RuntimeError and leaves
#    the failed lock held.
# 3) threading.Lock also protects json.dumps (atomic_write_json serializes before
#    writing), preventing residual concurrent mutations from changing dict size during iteration.
# See the same pattern in memory/event_log.py and app/memory_server/gates.py.
#
# Deliberately avoid RLock: every *_unlocked helper requires caller ownership; reentrancy would merely hide
# bugs where an inner nested RMW persists the outer transaction's partially modified state.
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

    Every rewrite of a recent file goes through here, so this is also where
    keyed entries leaving the file are recorded: before the replace, the keys
    of keyed entries present in the old file and absent from ``payload`` are
    written to the ``RETIRED_KEYS_FILENAME`` sidecar
    (:func:`retire_removed_keyed_entries`).
    """
    retire_removed_keyed_entries(path, None, payload)
    atomic_write_json(path, payload, indent=2, ensure_ascii=False)
    with _LOCKS_GUARD:
        key = _resolve_key_unlocked(_lock_key(path))
        _CONTENT_VERSIONS[key] = _CONTENT_VERSIONS.get(key, 0) + 1


def recent_sidecar_path(path: Any, filename: str) -> str:
    """Return a sidecar file that lives next to (and moves with) this recent.json."""
    return os.path.join(os.path.dirname(os.fspath(path)), filename)


def write_recent_sidecar_unlocked(path: Any, filename: str, payload: Any) -> None:
    """Atomically replace a recent.json sidecar. The caller MUST already hold the lock.

    Sidecars (such as theater retraction tombstones) are serialized with the
    recent.json critical section so a check-then-write against the history file
    sees a consistent sidecar. They do not bump the recent content version.
    """
    atomic_write_json(
        recent_sidecar_path(path, filename), payload, indent=2, ensure_ascii=False,
    )


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


# ── keyed entries leaving recent.json ─────────────────────────────────────
# When keyed entries (visit diary records) leave recent.json through compression, trimming, clearing,
# memory-browser saves, cloud replacement, etc., record their keys in a sidecar as positive evidence
# they were written then removed. Keyed memory_server /cache retries treat them as written and never reinsert them.
# Keep recent.json's list-root format unchanged.

RETIRED_KEYS_FILENAME = "recent.retired_keys.json"
"""Sidecar ``{key: retired_at}`` of keyed entries that left this recent file."""

RECENT_IDEMPOTENCY_META_KEY = "idempotency_key"
"""Message metadata key carrying the idempotency key of a keyed ``/cache`` write."""

VISIT_DIARY_KEY_RE = re.compile(r"^visit-debrief:[A-Za-z0-9_-]{22}$")
"""The only keyed recent entries: one visit's diary (an assistant message), ``visit-debrief:{visit_id}``."""

_KEY_MARKER = '"' + RECENT_IDEMPOTENCY_META_KEY + '"'


def _entry_key(entry: Any) -> str | None:
    # Accept only visit diaries: AI messages with complete key formats. Same-named caller metadata on ordinary messages proves nothing.
    if not isinstance(entry, dict):
        return None
    meta = entry.get("metadata")
    if not isinstance(meta, dict):
        data = entry.get("data")
        meta = data.get("metadata") if isinstance(data, dict) else None
    key = meta.get(RECENT_IDEMPOTENCY_META_KEY) if isinstance(meta, dict) else None
    kind = entry.get("type") or ("ai" if entry.get("role") == "assistant" else entry.get("role"))
    if kind != "ai" or not isinstance(key, str) or VISIT_DIARY_KEY_RE.fullmatch(key) is None:
        return None
    return key


def strip_idempotency_metadata(messages: Any) -> None:
    """Drop the reserved ``idempotency_key`` metadata from caller-supplied messages (in place).

    Only the keyed ``/cache`` path stamps it, on the server side; anything a
    caller sent on another write path would otherwise read as "this diary was
    already written" and settle a later real keyed write without storing it.
    """
    for message in messages or ():
        if isinstance(message, dict):
            # Match _entry_key's accepted shapes: top-level metadata or persisted data.metadata; this leaf module does not import llm_client.
            data = message.get("data")
            metas = [message.get("metadata"), data.get("metadata") if isinstance(data, dict) else None]
        else:
            metas = [getattr(message, "metadata", None)]
        for meta in metas:
            if isinstance(meta, dict):
                meta.pop(RECENT_IDEMPOTENCY_META_KEY, None)


def keyed_entry_keys(payload: Any) -> set[str]:
    """Idempotency keys of the keyed entries in a recent payload (a list of stored messages)."""
    if not isinstance(payload, list):
        return set()
    return {key for key in (_entry_key(entry) for entry in payload) if key is not None}


def _read_current_payload_unlocked(path: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        # If the old file is unreadable or invalid UTF-8, removed keys cannot be compared, but replacement writes must proceed
        # because cloud downloads intentionally overwrite corrupt recent.json files.
        return []
    if _KEY_MARKER not in text:
        # Most files contain no keyed entries; detect that without parsing.
        return []
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        # Corrupt old files cannot identify removed keys; write normally, since no entries were readable anyway.
        return []


def read_keyed_payload(path: Any) -> Any:
    """A recent payload read only for its keyed entries (``[]`` when absent, unreadable or without any).

    No lock: for files outside the live recent path (e.g. a staged cloud copy).
    """
    return _read_current_payload_unlocked(path)


def read_retired_keys_unlocked(path: Any) -> dict[str, float]:
    """The retired-key sidecar of ``path`` (``{}`` when absent). The caller MUST hold the lock.

    Raises ``ValueError`` when the sidecar exists but is not a JSON object,
    and ``OSError`` when it cannot be read: callers deciding "was this key
    ever written" must not read a damaged file as "never".
    """
    sidecar = recent_sidecar_path(path, RETIRED_KEYS_FILENAME)
    try:
        with open(sidecar, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{RETIRED_KEYS_FILENAME} is not an object")
    for key, stamp in data.items():
        # Also treat malformed entries, invalid diary keys or nonfinite timestamps as corrupt, never as proof of prior writes.
        if (
            not isinstance(key, str) or VISIT_DIARY_KEY_RE.fullmatch(key) is None
            or isinstance(stamp, bool) or not isinstance(stamp, (int, float))
            # All ints are finite; math.isfinite over huge ints raises OverflowError and blocks recent.json writes, so check floats only.
            or (isinstance(stamp, float) and not math.isfinite(stamp))
        ):
            raise ValueError(f"{RETIRED_KEYS_FILENAME} has a malformed entry")
    return data


def read_retired_keys(path: Any) -> dict[str, float]:
    """:func:`read_retired_keys_unlocked` under the file lock."""
    with recent_file_access(path) as resolved_path:
        return read_retired_keys_unlocked(resolved_path)


def retire_removed_keyed_entries(path: Any, old: Any, new: Any) -> list[str]:
    """Record the keys of keyed entries in ``old`` but not in ``new``. The caller MUST hold the lock.

    ``old=None`` reads the current file. The sidecar is written atomically
    BEFORE the caller replaces (or deletes, ``new=[]``) the recent file: a
    crash between the two leaves the key recorded and the entry possibly
    still there, both evidence that it was written. A key already recorded
    keeps its first ``retired_at``. A sidecar that cannot be read is left
    untouched (its readers keep failing closed) rather than replaced by an
    incomplete one. Returns the newly retired keys.
    """
    if old is None:
        old = _read_current_payload_unlocked(path)
    removed = keyed_entry_keys(old) - keyed_entry_keys(new)
    if not removed:
        return []
    try:
        data = read_retired_keys_unlocked(path)
    except (OSError, ValueError, RecursionError):
        # Corrupt/unreadable sidecars must not block recent.json writes, but do not overwrite them with a new sidecar containing only these entries;
        # that loses old-key evidence and lets retries restore removed diaries. Retain unchanged and fail closed on keyed reads.
        return []
    now = time.time()
    added = sorted(key for key in removed if key not in data)
    if not added:
        return []
    for key in added:
        data[key] = now
    write_recent_sidecar_unlocked(path, RETIRED_KEYS_FILENAME, data)
    return added


def prune_retired_keys(path: Any, should_drop) -> list[str]:
    """Drop sidecar records for which ``should_drop(key, retired_at)`` is true (under the file lock)."""
    with recent_file_access(path) as resolved_path:
        sidecar = recent_sidecar_path(resolved_path, RETIRED_KEYS_FILENAME)
        if not os.path.exists(sidecar):
            return []
        data = read_retired_keys_unlocked(resolved_path)
        dropped = [key for key, stamp in data.items() if should_drop(key, stamp)]
        if dropped:
            for key in dropped:
                data.pop(key, None)
            write_recent_sidecar_unlocked(resolved_path, RETIRED_KEYS_FILENAME, data)
        return dropped
