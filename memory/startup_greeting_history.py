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
"""Small durable history used to diversify startup greetings.

This is intentionally separate from :mod:`memory.anti_repeat`.  The normal
anti-repeat foreground expires after ten minutes, while a startup greeting is
not eligible until the conversation has already been idle for fifteen minutes.
Keeping a short, feature-specific history gives the startup path a real
same-day avoidance window without changing the scoring semantics of ordinary
replies or scheduled proactive chats.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional
from uuid import uuid4

from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json, read_bytes_tolerating_replace
from utils.logger_config import get_module_logger


logger = get_module_logger(__name__, "Memory")

_SCHEMA_VERSION = 1
_DEFAULT_KEY = "default"
# 召回窗是 3 天，上限必须装得下整个窗口，否则最早那一天会在还该被参考时就被
# 挤掉。容量要按 15 分钟的触发门槛算，不能按 30 分钟的 burst 闸：用户在上次问候
# 之后说过话时 burst 会被豁免，此时相邻两条已提交问候只差 15 分钟。
# 3 天 ÷ 15 分钟 = 288，取 320 留余量。
#
# 这里刻意按条数封顶而不是按时间裁：本模块明确不按墙钟排序（见
# _read_records_from_disk），时钟回拨时 ts 不能作为「该不该丢」的依据。
_MAX_RECORDS = 320
_MAX_STORED_TEXT_CHARS = 160


# One name, used by the write path AND by the cloud-save fence target.
# They were separate literals and two of the three stores had already
# drifted, so a fenced write reported a file that does not exist.
_SIDECAR_FILENAME = "startup_greetings.json"

@dataclass(frozen=True, slots=True)

class StartupGreetingRecord:
    """One startup greeting that was actually committed to the client."""

    ts: float
    text: str
    variant_key: str
    topic_key: str | None = None


StageHandle = tuple[str, dict[str, Any], int]


class _CorruptStartupGreetingHistory(ValueError):
    """The file was read successfully, but its persisted content is unusable."""

    def __init__(self, message: str, persisted_bytes: bytes) -> None:
        super().__init__(message)
        self.persisted_bytes = persisted_bytes


def _resolve_name(name: Optional[str]) -> str:
    return str(name or _DEFAULT_KEY)


def _clean_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > _MAX_STORED_TEXT_CHARS:
        text = text[:_MAX_STORED_TEXT_CHARS].rstrip()
    return text


def _clean_key(value: Any, *, limit: int = 160) -> str:
    key = str(value or "").strip()
    if not key:
        return ""
    return key[:limit]


def _normalize_record(raw: Any) -> StartupGreetingRecord | None:
    if not isinstance(raw, dict):
        return None
    try:
        text = _clean_text(raw.get("text"))
        variant_key = _clean_key(raw.get("variant_key"), limit=64)
        if not text or not variant_key:
            return None
        topic_key = _clean_key(raw.get("topic_key")) or None
        ts = float(raw.get("ts") or 0.0)
        if not math.isfinite(ts) or ts <= 0.0:
            return None
        return StartupGreetingRecord(
            ts=ts,
            text=text,
            variant_key=variant_key,
            topic_key=topic_key,
        )
    except (TypeError, ValueError, OverflowError):
        return None


def _record_payload(record: StartupGreetingRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ts": record.ts,
        "text": record.text,
        "variant_key": record.variant_key,
    }
    if record.topic_key:
        payload["topic_key"] = record.topic_key
    return payload


class StartupGreetingHistory:
    """Per-character rolling history with commit-before-terminal staging."""

    def __init__(self, config_manager=None) -> None:
        self._config_manager = config_manager or get_config_manager()
        self._cache: dict[str, list[StartupGreetingRecord]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._write_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._staged_seq: dict[str, int] = {}
        self._written_seq: dict[str, int] = {}
        self._detached_flushes: set[asyncio.Task] = set()
        self._reservations: dict[str, tuple[str, float]] = {}
        # Names retired by ``retire_character``; see ``_write_file_path``.
        self._retired: set[str] = set()

    def _file_path(self, name: str) -> str:
        """Return the path for READING; it never creates the directory.

        Creation belongs to ``_write_file_path`` alone. Creating it here made a
        cache MISS resurrect a directory that a delete had just removed: the
        eviction dropped the cache, so the very next record re-read from disk
        and ``makedirs`` the tree back before any write was attempted.
        """
        return os.path.join(
            str(self._config_manager.memory_dir),
            name,
            _SIDECAR_FILENAME,
        )

    def _write_file_path(self, name: str) -> str | None:
        """Return the save target, or None for a retired, removed identity.

        The normal path is the lazy ``ensure_character_dir`` every sibling
        memory writer uses. The exception is a name ``retire_character``
        retired: fencing alone only covers snapshots staged BEFORE the
        eviction, so a write staged while a delete or rename-away was still in
        flight would run once the lifecycle operation released its fence and
        ``makedirs`` the directory back into existence -- making a deleted
        identity look like it still has memory.

        A retired name may only write into a directory that already exists; it
        never creates one. Only ``evict_character`` lifts retirement, and only
        callers that KNOW the identity is live reach for it.

        A same-named identity reusing a recreated directory can still pick up
        the old one's data through the cache. It reproduces here too; what
        holds it shut, why it is left as is, and what a new writer has to do
        are recorded once in ``memory/anti_repeat_effects.py``
        ``_write_file_path`` rather than three times over.
        """
        from memory import _is_within_memory_root, ensure_character_dir
        from utils.character_memory import is_character_write_fenced

        # Refused for the WHOLE of an operation that will create this
        # directory partway through. Retirement below only declines to make
        # one, so once a rename's merge has made it, a late write from the
        # identity that used to own the name would land on the history just
        # moved in -- and staging copies the whole payload, so it replaces
        # it rather than adding to it.
        if is_character_write_fenced(name):
            return None

        memory_dir = self._config_manager.memory_dir
        character_dir = os.path.join(str(memory_dir), name)
        if not _is_within_memory_root(str(memory_dir), name, character_dir):
            # A historical unsafe name resolves outside its own directory:
            # "." lands on the memory root itself and ".." escapes it
            # entirely, so the sidecar would be written beside -- or above
            # -- the whole memory tree. Refused for a LIVE name as well as a
            # retired one, and refused BEFORE ensure_character_dir below can
            # create anything.
            return None
        if name in self._retired:
            if not os.path.isdir(character_dir):
                return None
            return os.path.join(character_dir, _SIDECAR_FILENAME)
        return os.path.join(
            ensure_character_dir(memory_dir, name),
            _SIDECAR_FILENAME,
        )

    def _get_lock(self, name: str) -> threading.Lock:
        if name not in self._locks:
            with self._locks_guard:
                self._locks.setdefault(name, threading.Lock())
        return self._locks[name]

    def _get_write_lock(self, name: str) -> threading.Lock:
        if name not in self._write_locks:
            with self._locks_guard:
                self._write_locks.setdefault(name, threading.Lock())
        return self._write_locks[name]

    def _read_records_from_disk(self, name: str) -> list[StartupGreetingRecord]:
        path = self._file_path(name)
        try:
            os.stat(path)
        except FileNotFoundError:
            return []
        persisted_bytes = read_bytes_tolerating_replace(path)
        try:
            raw = json.loads(persisted_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _CorruptStartupGreetingHistory(
                "startup greeting history is not valid UTF-8 JSON",
                persisted_bytes,
            ) from exc
        if not isinstance(raw, dict):
            raise _CorruptStartupGreetingHistory(
                "startup greeting history root must be an object",
                persisted_bytes,
            )
        items = raw.get("records")
        if not isinstance(items, list):
            raise _CorruptStartupGreetingHistory(
                "startup greeting history has an invalid records field",
                persisted_bytes,
            )
        records: list[StartupGreetingRecord] = []
        for index, item in enumerate(items):
            record = _normalize_record(item)
            if record is None:
                raise _CorruptStartupGreetingHistory(
                    f"startup greeting history has an invalid record at index {index}",
                    persisted_bytes,
                )
            records.append(record)
        # JSON list order is commit order.  Do not sort by wall clock: an NTP
        # correction can move time backwards, but the latest commit must remain
        # the latest record and must not be pruned as if it were old.
        return records[-_MAX_RECORDS:]

    def _backup_corrupt_file(
        self,
        name: str,
        persisted_bytes: bytes,
    ) -> str | None:
        """Best-effort, non-destructive backup of invalid persisted bytes.

        The content-derived suffix reuses one backup when the same malformed
        bytes are encountered repeatedly, while keeping later, distinct
        corruptions separate from older recovery artifacts.
        """

        from utils.character_memory import is_character_write_fenced

        # The only write in this store that does not go through
        # ``_write_file_path``, so the fence has to be asked here too --
        # measured: while a rename held the fence up, a corrupt reload still
        # dropped a .bak beside the sidecar and seeded an empty cache for a
        # name that is supposed to be untouchable.
        if is_character_write_fenced(name):
            return None

        path = self._file_path(name)
        digest = hashlib.sha256(persisted_bytes).hexdigest()[:16]
        backup_path = f"{path}.corrupt.{digest}.bak"
        created_backup = False
        try:
            with open(backup_path, "xb") as target:
                created_backup = True
                target.write(persisted_bytes)
                target.flush()
                os.fsync(target.fileno())
        except FileExistsError:
            try:
                with open(backup_path, "rb") as existing:
                    if existing.read() == persisted_bytes:
                        return backup_path
            except OSError:
                pass
            return None
        except OSError:
            if created_backup:
                try:
                    os.unlink(backup_path)
                except OSError:
                    pass
            return None
        return backup_path

    def _load_unlocked(self, name: str) -> list[StartupGreetingRecord]:
        if name not in self._cache:
            self._cache[name] = self._read_records_from_disk(name)
        return self._cache[name]

    def _stage_snapshot_unlocked(self, name: str) -> StageHandle:
        seq = self._staged_seq.get(name, 0) + 1
        self._staged_seq[name] = seq
        payload = {
            "version": _SCHEMA_VERSION,
            "records": [
                _record_payload(record) for record in self._cache.get(name, [])
            ],
        }
        return name, payload, seq

    def _flush_snapshot(self, name: str, payload: dict[str, Any], seq: int) -> None:
        # The write barrier has to be inside the import critical section: a
        # cloud-save import replaces `memory/<name>/` wholesale, and a snapshot
        # staged BEFORE the replacement still carries a seq above
        # `_written_seq`, so evicting afterwards cannot stop it — it takes the
        # write lock, passes the sequence check and writes the old payload back
        # over the imported file, which no later fence can undo.
        # `cloudsave_writable_transaction` raises MaintenanceModeError while the
        # import fence is closed, so the flush is skipped. Same shape as
        # `memory/anti_repeat_effects.py`.
        try:
            from utils.cloudsave_runtime import cloudsave_writable_transaction

            with cloudsave_writable_transaction(
                self._config_manager,
                operation="save",
                target=f"memory/{name}/{_SIDECAR_FILENAME}",
            ):
                with self._get_write_lock(name):
                    if seq <= self._written_seq.get(name, 0):
                        return
                    target = self._write_file_path(name)
                    if target is None:
                        # Directory is gone (deleted or renamed away while this
                        # turn was in flight). Fence the sequence and drop this
                        # snapshot rather than recreating the directory.
                        self._written_seq[name] = seq
                        logger.debug(
                            "[StartupGreetingHistory] skip save for removed "
                            "character %s",
                            name,
                        )
                        return
                    atomic_write_json(
                        target, payload, indent=2, ensure_ascii=False
                    )
                    self._written_seq[name] = seq
        except Exception as exc:
            logger.warning(
                "[StartupGreetingHistory] save failed for %s: %s",
                name,
                exc,
            )

    async def apreload(self, name: str) -> None:
        """Warm the disk-backed history before synchronous commit-point reads."""

        resolved = _resolve_name(name)
        with self._get_lock(resolved):
            if resolved in self._cache:
                return
        try:
            records = await asyncio.to_thread(self._read_records_from_disk, resolved)
        except _CorruptStartupGreetingHistory as exc:
            backup_path = await asyncio.to_thread(
                self._backup_corrupt_file,
                resolved,
                exc.persisted_bytes,
            )
            if backup_path is None:
                logger.warning(
                    "[StartupGreetingHistory] invalid persisted data for %s; "
                    "backup failed, leaving history unavailable",
                    resolved,
                )
                # Never install an empty cache unless the malformed source was
                # preserved.  Without a cache, try_reserve fails closed and no
                # later greeting can overwrite the only recoverable copy.
                return
            try:
                current_bytes = await asyncio.to_thread(
                    read_bytes_tolerating_replace,
                    self._file_path(resolved),
                )
            except OSError:
                logger.warning(
                    "[StartupGreetingHistory] invalid persisted data for %s; "
                    "source changed or became unavailable during recovery",
                    resolved,
                )
                return
            if current_bytes != exc.persisted_bytes:
                logger.warning(
                    "[StartupGreetingHistory] persisted data changed during "
                    "recovery for %s; leaving history unavailable for retry",
                    resolved,
                )
                return
            logger.warning(
                "[StartupGreetingHistory] invalid persisted data for %s; "
                "starting with empty history (backup_saved=True)",
                resolved,
            )
            # Content/schema/encoding damage is deterministic: keeping the
            # cache absent would disable greetings forever.  Start from a
            # known empty in-memory history so the next committed greeting can
            # atomically replace the bad file.  The original bytes remain in
            # the best-effort sibling backup above.
            records = []
        except OSError as exc:
            logger.warning(
                "[StartupGreetingHistory] preload I/O failed for %s: %s",
                resolved,
                exc,
            )
            # A permission/share/replace failure may be temporary.  Do not
            # install an empty cache: try_reserve must fail closed, and a later
            # preload may recover the last-known-good file without overwriting
            # it.
            return
        except Exception as exc:
            logger.warning(
                "[StartupGreetingHistory] preload failed for %s: %s",
                resolved,
                exc,
            )
            # Unknown failures remain conservative as well.
            return
        with self._get_lock(resolved):
            self._cache.setdefault(resolved, records)

    def recent(
        self,
        name: str,
        *,
        now: float | None = None,
        max_age_seconds: float = 24 * 60 * 60,
        limit: int = _MAX_RECORDS,
    ) -> list[StartupGreetingRecord]:
        """Return newest-first committed greetings inside ``max_age_seconds``."""

        if limit <= 0:
            return []
        resolved = _resolve_name(name)
        ref = float(time.time() if now is None else now)
        if not math.isfinite(ref):
            return []
        lower_bound = ref - max(0.0, float(max_age_seconds))
        with self._get_lock(resolved):
            # Production callers explicitly ``apreload``.  If it failed, return
            # an empty view without repeating filesystem I/O on the event loop.
            records = list(self._cache.get(resolved, ()))
        return [
            record for record in reversed(records) if lower_bound < record.ts <= ref
        ][:limit]

    def try_reserve(
        self,
        name: str,
        *,
        now: float | None = None,
        burst_seconds: float = 30 * 60,
        lease_seconds: float = 15 * 60,
        last_user_engagement_at: float | None = None,
    ) -> str | None:
        """Atomically reserve one startup delivery for a character.

        The lease uses ``monotonic`` so wall-clock corrections cannot open a
        second concurrent delivery.  Returning ``None`` means either another
        trigger owns the lease, a committed greeting is still in the burst
        window, or preload did not produce a safe last-known-good cache.
        """

        resolved = _resolve_name(name)
        wall_now = float(time.time() if now is None else now)
        if not math.isfinite(wall_now):
            return None
        monotonic_now = time.monotonic()
        with self._get_lock(resolved):
            if resolved not in self._cache:
                return None
            active = self._reservations.get(resolved)
            if active is not None:
                _active_token, expires_at = active
                if monotonic_now < expires_at:
                    return None
                self._reservations.pop(resolved, None)
            records = self._cache[resolved]
            if records:
                # A future epoch can result from a wall-clock rollback.  Treat it
                # as recent (fail closed) instead of reopening a duplicate burst.
                age = wall_now - records[-1].ts
                user_engaged_after = (
                    last_user_engagement_at is not None
                    and float(last_user_engagement_at) > records[-1].ts
                )
                if not user_engaged_after and age <= max(0.0, float(burst_seconds)):
                    return None
            token = uuid4().hex
            self._reservations[resolved] = (
                token,
                monotonic_now + max(1.0, float(lease_seconds)),
            )
            return token

    def release_reservation(self, name: str, token: str | None) -> None:
        if not token:
            return
        resolved = _resolve_name(name)
        with self._get_lock(resolved):
            active = self._reservations.get(resolved)
            if active is not None and active[0] == token:
                self._reservations.pop(resolved, None)

    def stage_committed(
        self,
        name: str,
        text: str,
        *,
        variant_key: str,
        topic_key: str | None = None,
        committed_at: float | None = None,
        reservation_token: str | None = None,
    ) -> StageHandle | None:
        """Record a visible greeting in memory and return its disk snapshot."""

        cleaned_text = _clean_text(text)
        cleaned_variant = _clean_key(variant_key, limit=64)
        if not cleaned_text or not cleaned_variant:
            return None
        timestamp = float(time.time() if committed_at is None else committed_at)
        if not math.isfinite(timestamp) or timestamp <= 0.0:
            return None
        record = StartupGreetingRecord(
            ts=timestamp,
            text=cleaned_text,
            variant_key=cleaned_variant,
            topic_key=_clean_key(topic_key) or None,
        )
        resolved = _resolve_name(name)
        with self._get_lock(resolved):
            if reservation_token is not None:
                active = self._reservations.get(resolved)
                if active is None or active[0] != reservation_token:
                    return None
                self._reservations.pop(resolved, None)
            records = self._load_unlocked(resolved)
            records.append(record)
            if len(records) > _MAX_RECORDS:
                del records[: len(records) - _MAX_RECORDS]
            self._cache[resolved] = records
            return self._stage_snapshot_unlocked(resolved)

    async def aflush_staged(self, staged: StageHandle | None) -> None:
        if staged is None:
            return
        name, payload, seq = staged
        await asyncio.to_thread(self._flush_snapshot, name, payload, seq)

    def flush_staged_detached(self, staged: StageHandle | None) -> None:
        """Persist a staged commit without adding a caller cancellation point."""

        if staged is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug(
                "[StartupGreetingHistory] detached flush skipped: no running loop"
            )
            return
        task = loop.create_task(self.aflush_staged(staged))
        self._detached_flushes.add(task)

        def _done(finished: asyncio.Task) -> None:
            self._detached_flushes.discard(finished)
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                logger.debug("[StartupGreetingHistory] detached flush failed: %s", exc)

        task.add_done_callback(_done)

    def _evict_unlocked(self, resolved: str) -> None:
        fence = max(
            self._staged_seq.get(resolved, 0),
            self._written_seq.get(resolved, 0),
        )
        self._cache.pop(resolved, None)
        self._reservations.pop(resolved, None)
        self._staged_seq[resolved] = fence
        self._written_seq[resolved] = fence

    def evict_character(self, name: str) -> None:
        """Forget a LIVE identity whose file changed underneath us.

        Distinct from ``clear``, which WIPES the data and persists an empty
        payload. Eviction is for when the file on disk changed underneath us --
        a cloud-save import replaces ``memory/<name>/`` wholesale -- and the
        cache would otherwise shadow the new contents and get flushed back over
        them. The sequence fence stops a snapshot staged before the replacement
        from doing exactly that.

        This is also the explicit "the identity is live" event that lifts
        retirement: a created, imported or renamed-to name is a real character,
        and leaving it retired would deny it the lazy directory creation every
        sibling memory writer gets. Directory existence never lifts retirement;
        only this call does.
        """
        resolved = _resolve_name(name)
        with self._get_lock(resolved):
            with self._get_write_lock(resolved):
                self._evict_unlocked(resolved)
                self._retired.discard(resolved)

    def revive_character(self, name: str) -> None:
        """Mark a name live again WITHOUT dropping its cache or fencing it.

        The cloud APPLY never rewrites this sidecar -- it is not in
        ``MANAGED_MEMORY_FILENAMES`` -- so the cache still matches the file and
        evicting would only raise the sequence fence, silently discarding a
        snapshot that was staged and not yet flushed. What such an import DOES
        need is the retirement lifted: a name reused after an earlier delete
        cannot create its directory until something says it is live again.
        """
        resolved = _resolve_name(name)
        with self._get_lock(resolved):
            with self._get_write_lock(resolved):
                if resolved not in self._retired:
                    # Live identity: the cloud apply never rewrites this
                    # sidecar, so the cache matches the file and the sequence
                    # fence must not move -- moving it discards a snapshot
                    # staged and not yet flushed.
                    return
                # Retired: everything cached or staged under this name belongs
                # to the identity that was deleted -- a decision recorded
                # between the retire and the rmtree repopulates the cache from
                # the still-present file. Dropping and fencing it loses nothing
                # the reused name is entitled to, and keeping it would flush a
                # deleted character's aggregates under the new one.
                self._evict_unlocked(resolved)
                self._retired.discard(resolved)

    def retire_character(self, name: str) -> None:
        """Forget one identity whose directory is being REMOVED, and fence it.

        The sequence fence only covers snapshots staged BEFORE this call.
        Retirement is what stops a write staged while the delete or
        rename-away is still in flight from recreating the directory.
        """
        resolved = _resolve_name(name)
        with self._get_lock(resolved):
            with self._get_write_lock(resolved):
                self._evict_unlocked(resolved)
                self._retired.add(resolved)


# A retirement recorded BEFORE the singleton exists must not be lost. Delete
# and rename retire the identity and only then remove the tree, while the
# singleton is built lazily on the first runtime event -- so a generation
# already in flight could construct a fresh instance with an empty retirement
# set, whose first flush calls ``ensure_character_dir`` and puts the deleted
# directory straight back. Measured: retiring before construction recreated
# ``memory/<name>/`` and its sidecar, retiring after did not.
_PENDING_RETIREMENTS: set[str] = set()


def _record_pending_retirement(character_names, *, retired: bool):
    """Update the pending set and return the singleton, under ONE lock.

    The lock is _GLOBAL_HISTORY_LOCK, deliberately, and not a second lock of its own.
    A builder that had copied the pending set but not yet published would
    otherwise race a concurrent retire/revive: that caller reads ``None``,
    returns early, and leaves its update only in the set the builder had
    already copied -- so the published instance carries stale state, and a
    delete can be resurrected or a live character blocked from creating
    its directory. Sharing the lock makes both interleavings safe: either
    the update lands before the copy, or it sees the published instance.
    """
    with _GLOBAL_HISTORY_LOCK:
        for character_name in character_names:
            if retired:
                _PENDING_RETIREMENTS.add(character_name)
            else:
                # Eviction and revival both LIFT retirement, so they have
                # to clear the pending record too -- otherwise a name
                # retired and revived before construction would stay
                # retired forever.
                _PENDING_RETIREMENTS.discard(character_name)
        return _GLOBAL_HISTORY


_GLOBAL_HISTORY: StartupGreetingHistory | None = None
_GLOBAL_HISTORY_LOCK = threading.Lock()


def evict_cached_startup_greeting_history(*character_names: str) -> None:
    """Evict loaded identities without creating the global history."""
    names = list(dict.fromkeys(character_names))
    history = _record_pending_retirement(names, retired=False)
    if history is None:
        return
    for character_name in names:
        history.evict_character(character_name)

def revive_cached_startup_greeting_history(*character_names: str) -> None:
    """Lift retirement for live identities without touching their caches."""
    names = list(dict.fromkeys(character_names))
    history = _record_pending_retirement(names, retired=False)
    if history is None:
        return
    for character_name in names:
        history.revive_character(character_name)


def retire_cached_startup_greeting_history(*character_names: str) -> None:
    """Retire removed identities without creating the global history."""
    names = list(dict.fromkeys(character_names))
    history = _record_pending_retirement(names, retired=True)
    if history is None:
        return
    for character_name in names:
        history.retire_character(character_name)


def get_startup_greeting_history() -> StartupGreetingHistory:
    global _GLOBAL_HISTORY
    if _GLOBAL_HISTORY is None:
        with _GLOBAL_HISTORY_LOCK:
            if _GLOBAL_HISTORY is None:
                built = StartupGreetingHistory()
                # Already under the lock, so read the set directly: a
                # helper that re-acquired it would deadlock.
                built._retired.update(_PENDING_RETIREMENTS)
                _GLOBAL_HISTORY = built
    return _GLOBAL_HISTORY
