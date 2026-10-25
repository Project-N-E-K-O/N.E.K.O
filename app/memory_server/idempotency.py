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

"""Idempotency-key bookkeeping shared by every keyed memory_server write.

Three per-character files live under ``memory_dir/<character>/``:

* ``idempotency_keys.json`` -- ``{key: {state, written_at, ...}}`` with
  ``state`` one of ``pending`` / ``done`` / ``cancelled``. Records are kept
  forever: the caller retries without a bound, so the proof that a key was
  already written must not expire before the retries do.
* ``idempotency_staging/<sha256(key)[:32]>.json`` -- the "generate first,
  apply later" journal of one keyed request (the raw key contains ``:``,
  which Windows file names reject, so the file name is a digest and the key
  itself is stored inside the document).
* ``scoped_tombstones.json`` -- ``{subject_key: {forgotten_at,
  forget_epoch}}``, the largest forget epoch each subject ever received
  (a fan-out target of a forget is fenced at its own epoch, never at the
  requested subject's).

Locking contract (see docs/design/visit-infrastructure.md section 4.6):

* ``idempotency_lock(name)`` is the per-character lock. It only ever wraps
  one read-modify-write of a small JSON file (``update_key`` and the
  tombstone writer) and never an LLM call or a memory-store write, so two
  different keys of one character can never overwrite each other's record.
* ``key_lock(name, key)`` is the per-key lock that a keyed request holds for
  its whole duration, so a retry that arrives while the first attempt is
  still running waits and then sees the final state.
* Order is fixed: key lock first, character lock second (the character lock
  is only taken inside the helpers below). Nothing here takes a key lock
  while holding the character lock, so the two cannot deadlock.

All file I/O runs in worker threads (``scripts/check_async_blocking.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import time
import weakref
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from utils.asyncio_executor import to_thread_through_cancellation
from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.file_utils import atomic_write_json, read_json_tolerating_replace

from ._shared import logger

IDEMPOTENCY_KEYS_FILENAME = "idempotency_keys.json"
# Placeholder produced by clearing unreadable/unclaimed staging; same-key retries count as cancelled.
UNREADABLE_CANCELLED_MARKER = "__unreadable_cancelled__"
STAGING_DIRNAME = "idempotency_staging"
TOMBSTONES_FILENAME = "scoped_tombstones.json"

KEY_STATE_PENDING = "pending"
KEY_STATE_DONE = "done"
KEY_STATE_CANCELLED = "cancelled"
TERMINAL_KEY_STATES = frozenset({KEY_STATE_DONE, KEY_STATE_CANCELLED})
_KNOWN_KEY_STATES = frozenset({KEY_STATE_PENDING}) | TERMINAL_KEY_STATES

STAGING_STATE_GENERATED = "generated"


class IdempotencyStateError(RuntimeError):
    """A bookkeeping file exists but cannot be read as the expected shape.

    Never degraded to "empty": an unreadable key file read as ``{}`` and then
    written back would erase every ``done`` record of the character, and the
    next retry of an already-applied digest would apply it again.
    """


class IdempotencyCorruptError(IdempotencyStateError):
    """The bookkeeping file was read but its content is damaged (not a read failure)."""


# ── locks ────────────────────────────────────────────────────────────────
# asyncio.Lock binds to the loop that first contends on it. Registries are
# keyed per running loop so a lock created under one loop (a previous test,
# a restarted server loop) is never awaited from another.
_character_locks: dict[tuple[int, str], asyncio.Lock] = {}
# Weak-reference key-lock registry: holders/waiters keep references; idle keys collect immediately,
# preventing registry growth with cumulative requests.
_key_locks: "weakref.WeakValueDictionary[tuple[int, str, str], asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)
_fence_locks: "weakref.WeakValueDictionary[tuple[int, str, str], asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)


def _loop_id() -> int:
    return id(asyncio.get_running_loop())


def idempotency_lock(lanlan_name: str) -> asyncio.Lock:
    """Return the per-character read-modify-write lock (same object per name)."""
    registry_key = (_loop_id(), lanlan_name)
    lock = _character_locks.get(registry_key)
    if lock is None:
        lock = asyncio.Lock()
        _character_locks[registry_key] = lock
    return lock


def key_lock(lanlan_name: str, key: str) -> asyncio.Lock:
    """Return the per-key lock (the same object for the same character and key).

    Registered weakly: callers keep the returned lock referenced while they
    hold or wait on it (``async with key_lock(...)`` does), and an idle key's
    lock is dropped.
    """
    registry_key = (_loop_id(), lanlan_name, key)
    lock = _key_locks.get(registry_key)
    if lock is None:
        lock = asyncio.Lock()
        _key_locks[registry_key] = lock
    return lock


def forget_fence(lanlan_name: str, subject_key: str) -> asyncio.Lock:
    """Return the per-subject lock that serializes epoch-tagged forgets of one subject.

    Held from the completed-epoch check through the erase, both cancellation
    passes and the completion marker, so the next forget of the subject only
    checks once the previous one has published (or failed to publish) its
    completed epoch. Taken before any other lock, by forgets only; a forget
    takes the fences of every subject it may erase (the requested one and
    its fan-out targets) in sorted key order, so overlapping forgets never
    wait on each other in a cycle.
    """
    # Separate registry: client idempotency keys are arbitrary valid strings; sharing key-lock namespace could
    # collide with fence names, self-deadlocking clears acquiring same-name key locks while holding fence.
    registry_key = (_loop_id(), lanlan_name, subject_key)
    lock = _fence_locks.get(registry_key)
    if lock is None:
        lock = asyncio.Lock()
        _fence_locks[registry_key] = lock
    return lock


# ── paths ────────────────────────────────────────────────────────────────

def _config_manager():
    # Late binding: tests/reload replace runtime._config_manager.
    from . import runtime

    return runtime._config_manager


def _character_dir(lanlan_name: str) -> str:
    # Build paths only, no directories: reads must not recreate deleted characters;
    # atomic_write_json creates parent directories for writes itself.
    return os.path.join(str(_config_manager().memory_dir), lanlan_name)


def key_digest(key: str) -> str:
    """The 32-hex-char digest used for staging file names and effect keys."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def effect_key_for(key: str, ordinal: int) -> str:
    """Per-effect dedup key: ``sha256(key)[:32] + ':' + ordinal``."""
    return f"{key_digest(key)}:{int(ordinal)}"


def keys_path(lanlan_name: str) -> str:
    return os.path.join(_character_dir(lanlan_name), IDEMPOTENCY_KEYS_FILENAME)


def staging_dir(lanlan_name: str) -> str:
    return os.path.join(_character_dir(lanlan_name), STAGING_DIRNAME)


def staging_path(lanlan_name: str, key: str) -> str:
    return os.path.join(staging_dir(lanlan_name), f"{key_digest(key)}.json")


def tombstones_path(lanlan_name: str) -> str:
    return os.path.join(_character_dir(lanlan_name), TOMBSTONES_FILENAME)


# ── raw file helpers (run in worker threads) ─────────────────────────────

def _read_json_object(path: str) -> dict:
    """Read a dict-rooted JSON file; missing means empty, anything else raises."""
    try:
        # Other keys os.replace this shared file: back off/retry Windows replacement-window sharing conflicts,
        # never immediately label state unreadable and cause keyed 503 / clear 500.
        data = read_json_tolerating_replace(path)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        # Read failure (exhausted sharing retries/permissions) differs from corrupt contents; retryable unreadable state.
        raise IdempotencyStateError(f"{os.path.basename(path)} unreadable: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise IdempotencyCorruptError(f"{os.path.basename(path)} unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise IdempotencyCorruptError(f"{os.path.basename(path)} is not an object")
    return data


def _write_json_object(path: str, data: dict) -> None:
    assert_cloudsave_writable(
        _config_manager(),
        operation="save",
        target=f"memory/{os.path.basename(os.path.dirname(path))}/{os.path.basename(path)}",
    )
    atomic_write_json(path, data, ensure_ascii=False, indent=2)


def _cloudsave_target(path: str) -> str:
    try:
        relative = os.path.relpath(path, str(_config_manager().memory_dir))
    except ValueError:
        relative = os.path.basename(path)
    return "memory/" + relative.replace(os.sep, "/")


def _remove_file(path: str) -> bool:
    # Same gate as writes: cloudsave read-only/snapshot import forbids deleting/modifying memory directories.
    assert_cloudsave_writable(_config_manager(), operation="delete", target=_cloudsave_target(path))
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    return True


async def _update_json_object(
    lanlan_name: str,
    path: str,
    mutate: Callable[[dict], bool],
) -> dict:
    """Character-locked read-modify-write of one dict file.

    ``mutate`` edits the freshly read mapping in place and returns whether
    anything changed; nothing is written when it did not.
    """
    async with idempotency_lock(lanlan_name):
        data = await asyncio.to_thread(_read_json_object, path)
        if mutate(data):
            await asyncio.to_thread(_write_json_object, path, data)
        return data


# ── key records ──────────────────────────────────────────────────────────

async def read_key(lanlan_name: str, key: str) -> dict | None:
    """Return the current record of ``key`` (a copy), or ``None``."""
    data = await asyncio.to_thread(_read_json_object, keys_path(lanlan_name))
    if key not in data:
        return None
    record = data[key]
    state = record.get("state") if isinstance(record, dict) else None
    if not isinstance(state, str) or state not in _KNOWN_KEY_STATES:
        # Present key with nonobject record or missing/unknown state cannot mean absent/incomplete;
        # it may have been done/cancelled, and regeneration would rewrite finished/cleared outputs.
        raise IdempotencyStateError(f"idempotency record of {key!r} is malformed")
    return dict(record)


async def update_key(
    lanlan_name: str,
    key: str,
    fn: Callable[[dict | None], dict | None],
) -> dict | None:
    """Change exactly one key record under the character lock.

    Reads the LATEST file, hands ``fn`` a copy of this key's record (or
    ``None``), and writes back only that entry: ``fn`` returning ``None``
    leaves the file untouched, returning an equal record skips the write.
    Every other key in the file is preserved byte-for-byte as read inside
    the lock, so concurrent transitions of different keys cannot lose each
    other. Returns the record now stored for ``key``.
    """
    result: dict[str, Any] = {}

    def _mutate(data: dict) -> bool:
        old = data.get(key)
        old_copy = dict(old) if isinstance(old, dict) else None
        new = fn(None if old_copy is None else dict(old_copy))
        if new is None:
            result["record"] = old_copy
            return False
        new = dict(new)
        result["record"] = new
        if new == old_copy:
            return False
        data[key] = new
        return True

    await _update_json_object(lanlan_name, keys_path(lanlan_name), _mutate)
    return result.get("record")


def transition(state: str, **extra: Any) -> Callable[[dict | None], dict | None]:
    """An ``update_key`` callback moving a record to ``state``.

    Terminal records are never moved again (``done`` stays ``done`` even if a
    late forget tries to cancel it, and a cancelled key is never revived).
    """
    def _fn(old: dict | None) -> dict | None:
        if old is not None and old.get("state") in TERMINAL_KEY_STATES:
            return None
        record = dict(old or {})
        record["state"] = state
        record.setdefault("written_at", time.time())
        record["updated_at"] = time.time()
        for name, value in extra.items():
            if value is not None:
                record[name] = value
        return record

    return _fn


# ── staging ──────────────────────────────────────────────────────────────

def _read_staging_sync(path: str, key: str) -> dict | None:
    try:
        data = read_json_tolerating_replace(path)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as exc:
        raise IdempotencyStateError(f"staging unreadable: {exc}") from exc
    if isinstance(data, dict) and UNREADABLE_CANCELLED_MARKER in data:
        # Staging unreadable/unclaimed during clear had text erased; treat key cancelled (drop_unreadable_orphan_staging).
        # Accept only placeholder with no other fields; marker mixed into ordinary journal is corruption, never grounds to discard unapplied effects.
        if data != {UNREADABLE_CANCELLED_MARKER: True}:
            raise IdempotencyStateError("staging carries a stray cancellation marker")
        return {"key": key, UNREADABLE_CANCELLED_MARKER: True}
    if not isinstance(data, dict) or data.get("key") != key:
        # Filename is digest only; mismatched embedded key (truncation collision/manual change) must never apply
        # another key's output.
        raise IdempotencyStateError("staging document does not belong to this key")
    return data


async def read_staging(lanlan_name: str, key: str) -> dict | None:
    """Return the staging document of ``key`` or ``None`` when there is none."""
    return await asyncio.to_thread(
        _read_staging_sync, staging_path(lanlan_name, key), key,
    )


async def write_staging(lanlan_name: str, key: str, document: dict) -> None:
    """Atomically (re)write the staging document of ``key``.

    Callers hold ``key_lock(lanlan_name, key)``: one writer per key. A
    cancelled caller returns only after the write finished, so the key lock
    is never released while its write is still running in the thread.
    """
    doc = dict(document)
    doc["key"] = key
    await to_thread_through_cancellation(
        _write_json_object, staging_path(lanlan_name, key), doc,
    )


def is_staging_path_of(lanlan_name: str, key: str, path: str) -> bool:
    """Whether ``path`` (from :func:`list_staging`) is the staging file of ``key``."""
    return (
        os.path.normcase(os.path.abspath(staging_path(lanlan_name, key)))
        == os.path.normcase(os.path.abspath(path))
    )


def _read_staging_file(path: str) -> tuple[bool, Any]:
    """``(missing, document_or_None)`` of one staging file read by path."""
    try:
        return False, read_json_tolerating_replace(path)
    except FileNotFoundError:
        return True, None
    except (OSError, ValueError, RecursionError):
        return False, None


async def scrub_misplaced_staging(
    lanlan_name: str, path: str, scrub: Callable[[dict], dict],
) -> bool:
    """Rewrite one staging file whose embedded key does not match its name, in place.

    The file's real owner is looked up by name in the key records (best
    effort) and its key lock held, so an in-flight apply of that key never
    races the rewrite. Returns False when the file changed or vanished meanwhile.
    """
    try:
        records = await asyncio.to_thread(_read_json_object, keys_path(lanlan_name))
    except IdempotencyStateError:
        records = {}
    owner = next(
        (key for key in records if isinstance(key, str) and key and is_staging_path_of(lanlan_name, key, path)),
        None,
    )
    async with (key_lock(lanlan_name, owner) if owner else contextlib.nullcontext()):
        missing, current = await asyncio.to_thread(_read_staging_file, path)
        if missing:
            return False
        if not isinstance(current, dict) or (
            isinstance(current.get("key"), str) and is_staging_path_of(lanlan_name, current["key"], path)
        ):
            # True key rewrote normal staging (or unreadable now): leave to regular flow.
            return False
        await asyncio.to_thread(_write_json_object, path, scrub(current))
    return True


async def drop_unreadable_orphan_staging(lanlan_name: str, path: str) -> bool:
    """Scrub a staging file that no longer parses and that no key record owns.

    Such a file cannot be matched against a forget (its subjects are
    unreadable) nor cancelled through a key record; kept, it would retain
    whatever extracted plaintext it holds, and a later repair would let a
    same-key retry adopt it. It is overwritten in place with a content-free
    marker: the plaintext is gone, and a same-key retry reads the marker as
    a cancelled key (never as "no staging", which would regenerate and
    write the forgotten subject back). Ownership unknown (no or unreadable
    key records) or a transient read error keeps the file. Returns whether
    it was scrubbed.
    """

    def _scrub() -> bool:
        if not os.path.exists(keys_path(lanlan_name)):
            # Staging but no key file: ownership unknown (loss/sync/restore), preserve unchanged.
            return False
        try:
            records = _read_json_object(keys_path(lanlan_name))
        except IdempotencyStateError:
            return False
        if any(isinstance(key, str) and key and is_staging_path_of(lanlan_name, key, path) for key in records):
            # Owned by key record: record-based cancellation handles pending; terminal records never apply again.
            return False
        try:
            document = read_json_tolerating_replace(path)
        except FileNotFoundError:
            return False
        except (ValueError, RecursionError):
            document = None
        except OSError:
            return False
        if isinstance(document, dict):
            return False
        _write_json_object(path, {UNREADABLE_CANCELLED_MARKER: True})
        return True

    # Key read/reread/rewrite share one lock, preventing update_key claiming this path meanwhile.
    async with idempotency_lock(lanlan_name):
        return await asyncio.to_thread(_scrub)


async def delete_staging(lanlan_name: str, key: str) -> bool:
    return await asyncio.to_thread(_remove_file, staging_path(lanlan_name, key))


def _list_staging_sync(directory: str) -> list[tuple[str, dict | None, float]]:
    rows: list[tuple[str, dict | None, float]] = []
    try:
        entries = list(os.scandir(directory))
    except FileNotFoundError:
        return rows
    for entry in entries:
        if not entry.is_file() or not entry.name.endswith(".json"):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        try:
            data = read_json_tolerating_replace(entry.path)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError):
            data = None
        rows.append((entry.path, data if isinstance(data, dict) else None, mtime))
    return rows


async def list_staging(lanlan_name: str) -> list[tuple[str, dict | None, float]]:
    """``[(path, document_or_None, mtime)]`` for every staging file."""
    return await asyncio.to_thread(_list_staging_sync, staging_dir(lanlan_name))


# ── tombstones ───────────────────────────────────────────────────────────

async def read_tombstones(lanlan_name: str) -> dict:
    return await asyncio.to_thread(
        _read_json_object, tombstones_path(lanlan_name),
    )


async def record_tombstones(
    lanlan_name: str,
    subject_keys: Iterable[str],
    forget_epoch: int,
    *,
    now: float | None = None,
) -> dict:
    """Raise each subject's tombstone to at least ``forget_epoch``.

    Monotonic: a smaller epoch never lowers an existing tombstone (a late or
    replayed forget must not reopen a window a newer forget closed).
    """
    epoch = int(forget_epoch)
    return await record_tombstone_epochs(
        lanlan_name, {str(key): epoch for key in subject_keys if key}, now=now,
    )


async def record_tombstone_epochs(
    lanlan_name: str,
    epochs: Mapping[str, int],
    *,
    now: float | None = None,
) -> dict:
    """Raise each subject's tombstone to at least its own epoch, in one write.

    Each key keeps its own epoch domain (the client counts per subject key),
    so a forget that fans out to linked subjects passes each target's own
    generation instead of copying the requested subject's onto them. Same
    monotonic rule as :func:`record_tombstones`.
    """
    stamp = time.time() if now is None else float(now)
    wanted = {str(key): int(value) for key, value in epochs.items() if key}

    def _mutate(data: dict) -> bool:
        changed = False
        for subject_key in sorted(wanted):
            epoch = wanted[subject_key]
            current = data.get(subject_key)
            current_epoch = (
                current.get("forget_epoch")
                if isinstance(current, dict) else None
            )
            if subject_key in data and (
                not isinstance(current_epoch, int)
                or isinstance(current_epoch, bool)
                or current_epoch < 0
            ):
                # Existing corrupt tombstone may hide a higher fence; never overwrite with lower generation,
                # nor block this clear (erase normally). Preserve unchanged; reads fail closed,
                # returning 503 for this subject's keyed writes until repaired.
                logger.warning(f"[Idempotency] {lanlan_name}: 墓碑 {subject_key!r} 内容损坏，保留不覆盖")
                continue
            if (
                isinstance(current_epoch, int)
                and not isinstance(current_epoch, bool)
                and current_epoch >= epoch
            ):
                continue
            data[subject_key] = {"forgotten_at": stamp, "forget_epoch": epoch}
            changed = True
        return changed

    return await _update_json_object(
        lanlan_name, tombstones_path(lanlan_name), _mutate,
    )


async def mark_tombstone_erased(
    lanlan_name: str, subject_key: str, forget_epoch: int, *, covered_epoch: int | None = None,
) -> None:
    """Record that the erase of ``forget_epoch`` for ``subject_key`` completed (monotonic).

    The tombstone is written BEFORE the erase, so it alone does not prove the
    erase finished; this marker does, and lets a replayed or stale forget of
    an epoch at or below it skip re-erasing writes made after it.

    ``covered_epoch`` is the tombstone fence already in place before this
    erase started (a newer forget may have raised it and then failed before
    erasing). The erase ran behind that fence, so it completes that epoch too.
    """
    epoch = int(forget_epoch)
    if isinstance(covered_epoch, int) and not isinstance(covered_epoch, bool) and covered_epoch > epoch:
        epoch = covered_epoch

    def _mutate(data: dict) -> bool:
        row = data.get(subject_key)
        fence = row.get("forget_epoch") if isinstance(row, dict) else None
        if subject_key in data and (
            not isinstance(fence, int) or isinstance(fence, bool) or fence < 0
        ):
            # Corrupt row: this clear erased subject fully; rebuild with current fence + completion marker
            # (possibly higher corrupt fence lost, weakening earlier-request blocking only). Permanent 503 would stall
            # retraction and memory writes forever, with nothing to repair file.
            logger.warning(f"[Idempotency] {lanlan_name}: 墓碑 {subject_key!r} 损坏，按本次清除重建")
            data[subject_key] = {"forgotten_at": time.time(), "forget_epoch": epoch, "erased_epoch": epoch}
            return True
        if not isinstance(row, dict):
            # Tombstone disappeared during erasure (e.g. startup pruned expired fence not raised this time):
            # never silently succeed without completion, or same-generation replay erases new memory again.
            # Rebuild tombstone at current generation and mark complete.
            data[subject_key] = {"forgotten_at": time.time(), "forget_epoch": epoch, "erased_epoch": epoch}
            return True
        current = row.get("erased_epoch")
        # Completion counts only if valid and ≤fence (as erased_epoch()). Corrupt/over-fence
        # markers cannot block this write, or success would remain unreadable and replays erase again.
        if _non_negative_int(current) and current <= fence and current >= epoch:
            return False
        if epoch > fence:
            # Old tombstone removed during erasure, then lower-generation clear recreated it: current erasure covers higher generation;
            # raise fence monotonically, or completion truncates and higher-generation replay erases again.
            row["forget_epoch"] = epoch
            row["forgotten_at"] = time.time()
        row["erased_epoch"] = epoch
        return True

    path = tombstones_path(lanlan_name)
    # Read/quarantine/rebuild under one lock: concurrent clears cannot have one rebuild file then another skip
    # its own row after reading rebuilt data, returning success without fence/completion.
    async with idempotency_lock(lanlan_name):
        await asyncio.to_thread(_mark_tombstone_erased_sync, path, _mutate)


def _mark_tombstone_erased_sync(path: str, mutate: Callable[[dict], bool]) -> None:
    try:
        data = _read_json_object(path)
    except IdempotencyCorruptError:
        # Whole tombstone file corrupt: copy for diagnostics, rebuild this clear's row. Other subject fences
        # are lost, but permanent 503 is worse: retraction never completes and nothing repairs the file.
        # OSError reads do not enter here; sharing conflicts must never quarantine intact files as corruption.
        # Pass cloudsave gate first: read-only/snapshot import leaves memory directory untouched.
        assert_cloudsave_writable(_config_manager(), operation="save", target=_cloudsave_target(path))
        # Copy rather than rename, then atomically replace original: failed replacement (full disk) leaves corrupt original
        # so reads still fail closed, never appearing absent and disabling all fences.
        quarantine = f"{path}.corrupt-{time.time_ns() // 1_000_000}-{os.getpid()}"
        try:
            shutil.copy2(path, quarantine)
            logger.error(f"[Idempotency] 墓碑文件不可读，已留底为 {os.path.basename(quarantine)} 并重建")
        except OSError as exc:
            # Diagnostic copy is optional: failure from insufficient space/missing file cannot block completion recording,
            # or 500 would lead same-generation retries to erase again without recognizing prior completion.
            logger.error(f"[Idempotency] 墓碑文件不可读，留底失败（{exc}），直接重建")
        data = {}
    if mutate(data):
        _write_json_object(path, data)


def _non_negative_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def erased_epoch(tombstones: dict, subject_key: str) -> int | None:
    """Highest forget epoch whose erase completed for ``subject_key`` (None when unknown).

    Only a well-formed row counts: the fence must be valid and the marker may
    not exceed it. A damaged row must never let a forget be skipped.
    """
    row = tombstones.get(subject_key)
    if not isinstance(row, dict):
        return None
    fence, value = row.get("forget_epoch"), row.get("erased_epoch")
    if not _non_negative_int(fence) or not _non_negative_int(value) or value > fence:
        return None
    return value


def tombstone_epoch(tombstones: dict, subject_keys: Iterable[str]) -> int | None:
    """Largest forget epoch recorded for any of ``subject_keys`` (or None)."""
    best: int | None = None
    for subject_key in subject_keys:
        if subject_key not in tombstones:
            continue
        row = tombstones[subject_key]
        epoch = row.get("forget_epoch") if isinstance(row, dict) else None
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
            # Existing corrupt tombstone cannot mean absent; otherwise old requests restore cleared memory.
            raise IdempotencyStateError(f"tombstone of {subject_key!r} is malformed")
        if best is None or epoch > best:
            best = epoch
    return best


# ── startup cleanup ──────────────────────────────────────────────────────

async def cleanup_expired(
    lanlan_names: Iterable[str],
    *,
    ttl_s: float | None = None,
    now: float | None = None,
) -> dict:
    """Drop staging leftovers and tombstones older than the retention period.

    Key records are never touched (``done`` / ``cancelled`` / ``pending`` are
    kept forever), and the staging of a ``pending`` key is kept too: it is
    the only copy of the generated products and of the apply progress; so
    are the tombstones of subjects such a staging document references.
    Characters being released (rename / delete draining) are skipped through
    the same lifecycle admission the write endpoints use. Best-effort per
    character: one unreadable file is logged and skipped, never aborts the
    sweep.
    """
    if ttl_s is None:
        from config import MEMORY_IDEMPOTENCY_TTL_S

        ttl_s = MEMORY_IDEMPOTENCY_TTL_S
    current = time.time() if now is None else float(now)
    cutoff = current - float(ttl_s)
    report = {"staging_removed": 0, "tombstones_removed": 0, "retired_removed": 0}
    from . import runtime

    for name in lanlan_names:
        # Same character lifecycle admission as writes: do not touch characters draining for delete/rename,
        # or tombstone writeback recreates just-deleted directories.
        lease = runtime._begin_character_request(name)
        if lease is None:
            continue
        try:
            await _cleanup_one(name, cutoff, report)
        finally:
            runtime._end_character_request(name, lease)
    if report["staging_removed"] or report["tombstones_removed"] or report["retired_removed"]:
        logger.info(
            "[Idempotency] 启动清理：暂存 %d、墓碑 %d、recent 退役记录 %d",
            report["staging_removed"],
            report["tombstones_removed"],
            report["retired_removed"],
        )
    return report


async def _cleanup_one(name: str, cutoff: float, report: dict) -> None:
    protected: set[str] = set()
    if not await asyncio.to_thread(os.path.isdir, _character_dir(name)):
        return
    await _cleanup_retired_keys(name, cutoff, report)
    try:
        # Infer owning key from filename digest, not potentially corrupted/tampered embedded key;
        # only key records establish staging as pending key's sole copy.
        records = await asyncio.to_thread(_read_json_object, keys_path(name))
        owner_of_path = {
            os.path.normcase(os.path.abspath(staging_path(name, key))): key
            for key in records
            if isinstance(key, str) and key
        }
        for path, document, mtime in await list_staging(name):
            created = (
                document.get("created_at") if isinstance(document, dict) else None
            )
            age_anchor = (
                float(created)
                if isinstance(created, (int, float)) and not isinstance(created, bool)
                else mtime
            )
            if age_anchor >= cutoff:
                continue
            key = owner_of_path.get(os.path.normcase(os.path.abspath(path)))
            if key is None:
                embedded = document.get("key") if isinstance(document, dict) else None
                if not (
                    isinstance(embedded, str) and embedded
                    and os.path.normcase(os.path.abspath(staging_path(name, embedded)))
                    == os.path.normcase(os.path.abspath(path))
                ):
                    # No key record matches and embedded key mismatches filename (unreadable/corrupt):
                    # unclaimable, so delete on expiry, avoiding long-lived extraction source on disk.
                    if await asyncio.to_thread(_remove_file, path):
                        report["staging_removed"] += 1
                    continue
                # Same-key retries can claim orphan staging, rebuilding pending then applying. Above records are
                # pre-enumeration snapshots; acquire key lock/reread before deletion, preserving newly claimed pending files.
                key = embedded
            if key_lock(name, key).locked():
                # Same-key request holds lock, likely awaiting LLM; cleanup holds character lease and must not queue behind it,
                # delaying rename/delete drain. Leave file until next startup scan.
                continue
            # Mutually exclusive with in-flight same-key requests potentially about to apply staging.
            async with key_lock(name, key):
                try:
                    record = await read_key(name, key)
                except IdempotencyStateError as exc:
                    # Corrupt individual key record: preserve only its corresponding staging, possibly pending's sole copy,
                    # never halt entire character cleanup and retain every expired file.
                    logger.warning(f"[Idempotency] {name}: 键记录 {key!r} 不可读，保留其暂存: {exc}")
                    continue
                if record is not None and record.get("state") == KEY_STATE_PENDING:
                    # Pending staging alone holds generated output/application progress, even unreadable or bad embedded keys
                    # (same-key retries fail closed). Deleting forces regeneration;
                    # mismatched sequence effect_keys block wrong facts and omit unapplied ones.
                    continue
                if await asyncio.to_thread(_remove_file, path):
                    report["staging_removed"] += 1
    except Exception as exc:  # noqa: BLE001 - startup sweep is best-effort
        logger.warning(f"[Idempotency] {name}: 暂存清理失败（跳过）: {exc}")
    try:
        removed: list[str] = []

        # Subjects referenced by retained pending staging: tombstones alone durably prove staging predates clear,
        # so cannot expire before staging.
        for _path, document, _mtime in await list_staging(name):
            protected.update(_staged_subjects(document))
        # Pending precedes staging: crash between leaves unstaged pending keys uncancellable by clear;
        # their request-subject tombstones likewise are sole evidence.
        try:
            records = await asyncio.to_thread(_read_json_object, keys_path(name))
        except Exception:  # noqa: BLE001 - Unreadable key records mean no tombstones deleted.
            return
        for record in records.values():
            if isinstance(record, dict) and record.get("state") == KEY_STATE_PENDING:
                request = record.get("request")
                wire_keys = request.get("wire_keys") if isinstance(request, dict) else None
                if isinstance(wire_keys, list):
                    protected.update(str(k) for k in wire_keys)
                routed = record.get("routed_keys")
                if isinstance(routed, list):
                    protected.update(str(k) for k in routed)

        def _drop_expired(data: dict) -> bool:
            for subject_key, row in list(data.items()):
                if subject_key in protected:
                    continue
                stamp = row.get("forgotten_at") if isinstance(row, dict) else None
                if (
                    isinstance(stamp, (int, float))
                    and not isinstance(stamp, bool)
                    and stamp < cutoff
                ):
                    data.pop(subject_key, None)
                    removed.append(subject_key)
            return bool(removed)

        if await asyncio.to_thread(os.path.exists, tombstones_path(name)):
            await _update_json_object(name, tombstones_path(name), _drop_expired)
        report["tombstones_removed"] += len(removed)
    except Exception as exc:  # noqa: BLE001 - startup sweep is best-effort
        logger.warning(f"[Idempotency] {name}: 墓碑清理失败（跳过）: {exc}")


async def _cleanup_retired_keys(name: str, cutoff: float, report: dict) -> None:
    """Drop ``recent.retired_keys.json`` records older than the cutoff whose key is terminal.

    A record of a key still ``pending`` (or without any key record) is kept
    however old: it is the only proof that a keyed diary entry reached
    ``recent.json`` before being moved out, and a later same-key retry would
    otherwise write it back.
    """
    from utils.recent_file import RETIRED_KEYS_FILENAME, prune_retired_keys, recent_sidecar_path

    recent_path = os.path.join(_character_dir(name), "recent.json")
    if not await asyncio.to_thread(os.path.exists, recent_sidecar_path(recent_path, RETIRED_KEYS_FILENAME)):
        return
    loaded: dict[str, dict] = {}

    def _records() -> dict:
        # Read under recent-file lock (_drop called locked by prune); cloud restore shares lock replacing recent/deleting key records,
        # so stale unlocked snapshots would delete retirement records still needed after restore.
        if "records" not in loaded:
            loaded["records"] = _read_json_object(keys_path(name))
        return loaded["records"]

    def _drop(key: str, stamp: Any) -> bool:
        record = _records().get(key)
        terminal = isinstance(record, dict) and record.get("state") in TERMINAL_KEY_STATES
        return (
            terminal
            and isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
            and stamp < cutoff
        )

    try:
        assert_cloudsave_writable(_config_manager(), operation="save", target=_cloudsave_target(
            os.path.join(_character_dir(name), RETIRED_KEYS_FILENAME)))
        dropped = await asyncio.to_thread(prune_retired_keys, recent_path, _drop)
    except Exception as exc:  # noqa: BLE001 - Best-effort startup sweep (unreadable key records: delete none).
        logger.warning(f"[Idempotency] {name}: recent 退役记录清理失败（跳过）: {exc}")
        return
    report["retired_removed"] += len(dropped)


def _staged_subjects(document: Any) -> set[str]:
    """Every subject key a staging document references: the ``subjects`` index plus each segment.

    The index alone is not enough: a document whose index is missing or
    damaged still names its subjects segment by segment (wire key and routed
    destination), and recovery replays it from those, so their tombstones
    must outlive it too (same reading as the forget-side cancellation scan).
    """
    if not isinstance(document, dict):
        return set()
    subjects = document.get("subjects")
    keys = {str(s) for s in subjects} if isinstance(subjects, list) else set()
    segments = document.get("segments")
    for segment in segments if isinstance(segments, list) else []:
        if not isinstance(segment, dict):
            continue
        if segment.get("wire_key"):
            keys.add(str(segment["wire_key"]))
        subject = segment.get("subject")
        if isinstance(subject, dict) and subject.get("subject_kind") and subject.get("subject_id"):
            keys.add(f"{subject['subject_kind']}:{subject['subject_id']}")
    return keys
