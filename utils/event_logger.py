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
Sparse-event JSONL writer

Purpose: record events like session_start / crash / onboarding_step / app_exit that are
few in number but information-dense per record. Complements token_tracker's daily
aggregates (counter / histogram style) — the latter are numeric rollups, this module is
discrete events with context.

Storage design:
- events-YYYYMMDD.jsonl, one JSON event per line, sharded by day
- append-only: writes never modify existing lines, only append
- Retention: shards older than 7 days are deleted
- hard per-file cap of 500 KB (new events rejected beyond it, preventing a single-day
  burst from trashing the disk)
- hard directory total cap of 20 MB (oldest shards force-deleted beyond it, preventing disk full)

Overhead guarantees:
- emit() goes into an in-memory deque, nanosecond-scale, never blocks the main path
- flush() is invoked once by TokenTracker's 60s periodic save, each disk write < 50 KB
- cleanup() runs alongside flush; cost is a listdir + a few stats

Thread safety: serialized in-process via _lock; multi-process is not strictly
synchronized, relying on the atomicity of a single write syscall while each jsonl line
is < 4KB (guaranteed by both POSIX O_APPEND and Windows append mode).

Fields never persisted: message content, username, master_name, prompt text. Events in
this module should be "what happened" + "which surface" + a few enum tags; do not stuff
business data in.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)


# ---------------------------------------------------------------------------
# Configuration constants.
# ---------------------------------------------------------------------------

_DIR_NAME = "telemetry_events"
_FILE_PREFIX = "events-"
_FILE_SUFFIX = ".jsonl"

# Retention: delete shards older than N days during cleanup.
_RETENTION_DAYS = 7

# Hard per-file limit: stop adding events after exceeding it, preventing abnormal daily volume.
_FILE_SIZE_CAP = 500 * 1024  # 500 KB

# Hard directory-total limit: force-delete oldest shards when exceeded, preventing full disks.
_DIR_SIZE_CAP = 20 * 1024 * 1024  # 20 MB

# Memory-buffer limit: remain bounded even if flush hangs (retain latest events).
_BUFFER_MAX = 1000

# Throttle cleanup: repeated flushes need not listdir/stat every time; run periodically.
_CLEANUP_MIN_INTERVAL = 5 * 60  # 5 min


# ---------------------------------------------------------------------------
# EventLogger singleton.
# ---------------------------------------------------------------------------


class EventLogger:
    """Process-wide singleton sparse-event JSONL writer.

    Typical usage:
        EventLogger.get_instance().emit("session_start", surface="pet_widget")

    flush() is invoked by TokenTracker's periodic_save_loop; callers need not care.
    """

    _instance: Optional["EventLogger"] = None
    _init_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "EventLogger":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffer: deque = deque(maxlen=_BUFFER_MAX)
        self._last_cleanup_ts: float = 0.0
        # Disk-write failure count: become silent after N consecutive failures; log only on transition,
        # preventing spam while disks are full / permissions fail.
        self._fail_count: int = 0
        # Daily shard sealed flag: exceeding _FILE_SIZE_CAP stops writes for the day; roll the next day.
        self._sealed_days: set = set()

    # ---- Paths ----

    @property
    def _dir(self) -> Path:
        return get_config_manager().config_dir / _DIR_NAME

    def _file_for(self, day: str) -> Path:
        return self._dir / f"{_FILE_PREFIX}{day}{_FILE_SUFFIX}"

    # ---- Public API ----

    def emit(self, name: str, **fields) -> None:
        """Record a sparse event. Thread-safe, non-blocking.

        Args:
            name: event name (snake_case, ideally < 32 chars)
            **fields: business fields. Never include message content / PII / master_name.

        Implementation: appends to an in-memory deque; flush() writes to disk in one
        go. emit() itself does no I/O, so even hundreds of calls per second add no
        perceptible overhead.
        """
        if not name:
            return
        rec = {"ts": time.time(), "name": name}
        # Flatten fields directly into rec; ts/name win conflicts to protect semantic fields from callers.
        if fields:
            for k, v in fields.items():
                if k in ("ts", "name"):
                    continue
                rec[k] = v
        with self._lock:
            self._buffer.append(rec)

    def flush(self) -> int:
        """Flush the buffer to disk + clean up expired shards along the way. Thread-safe.

        Returns:
            Number of events actually written to disk. 0 means empty buffer or failed write.

        When called: by TokenTracker._periodic_save_loop every 60s.
        Also triggered indirectly at atexit by token_tracker's _atexit_save (when wired up).
        """
        # Under lock, only retrieve buffer and decide cleanup. Perform all I/O outside,
        # so emit() is not blocked by disk I/O.
        with self._lock:
            buf = list(self._buffer)
            self._buffer.clear()
            now_mono = time.monotonic()
            should_cleanup = (now_mono - self._last_cleanup_ts) >= _CLEANUP_MIN_INTERVAL
            if should_cleanup:
                self._last_cleanup_ts = now_mono

        written = 0
        if buf:
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                self._log_fail(f"event_logger: mkdir failed: {e}")
                # Write failure: return events to buffer for retry. deque maxlen bounds buffer even during long
                # failures by evicting oldest events.
                self._push_back(buf)
                # Still run cleanup: full disks may recover space by deleting old shards.
            else:
                # Group by day key (flushes crossing midnight may contain multiple days).
                by_day: dict = {}
                for rec in buf:
                    day = self._day_of(rec["ts"])
                    by_day.setdefault(day, []).append(rec)

                # Failed days return to buffer for retry; successful days are already persisted
                # and must not push_back, avoiding duplicate writes next flush. Distinguish _append_day paths:
                # raise means I/O failure; return 0 means
                # normal sealed/empty discard.
                unwritten: list = []
                for day, recs in by_day.items():
                    if day in self._sealed_days:
                        continue  # Today is sealed by volume protection; discard new events.
                    try:
                        written += self._append_day(day, recs)
                    except OSError:
                        # _append_day already used throttled _log_fail logging; do not duplicate it here.
                        # Just return data to the buffer for the next retry.
                        unwritten.extend(recs)
                if unwritten:
                    self._push_back(unwritten)

        if should_cleanup:
            self._cleanup()

        return written

    # ---- Internal: disk writes ----

    @staticmethod
    def _day_of(ts: float) -> str:
        """Timestamp → YYYYMMDD (local timezone, aligned with token_usage.json's date.today)."""
        return datetime.fromtimestamp(ts).strftime("%Y%m%d")

    def _append_day(self, day: str, recs: list) -> int:
        """Append recs to the shard for day. Returns the number of records actually written."""
        path = self._file_for(day)

        # Per-file cap: seal today when exceeded and discard remaining events.
        try:
            current_size = path.stat().st_size if path.exists() else 0
        except OSError:
            current_size = 0
        if current_size >= _FILE_SIZE_CAP:
            self._sealed_days.add(day)
            logger.info(
                f"event_logger: sealed {path.name} (size={current_size} >= cap), dropping {len(recs)} events"
            )
            return 0

        # Assemble jsonl bytes.
        # ensure_ascii=False allows Chinese/Japanese event names and values, saving bytes.
        lines = []
        remaining = _FILE_SIZE_CAP - current_size
        for rec in recs:
            try:
                line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
                b = line.encode("utf-8")
            except (TypeError, ValueError) as e:
                # Unserializable fields: silently discard only that event, preserving others.
                logger.debug(f"event_logger: drop unserializable event {rec.get('name')}: {e}")
                continue
            if remaining < len(b):
                self._sealed_days.add(day)
                logger.info(f"event_logger: sealing {path.name} mid-batch (would exceed cap)")
                break
            lines.append(b)
            remaining -= len(b)

        if not lines:
            return 0

        payload = b"".join(lines)
        # Append-only: rely on OS O_APPEND atomicity; one write per line <4KB is atomic.
        # Raise OSError on I/O failure so the caller (flush) push_back retries recs;
        # use raise rather than return 0 to distinguish sealed/empty paths, whose intentional
        # discards must not occupy retry buffer.
        try:
            with open(path, "ab") as f:
                f.write(payload)
            self._fail_count = 0
            return len(lines)
        except OSError as e:
            self._log_fail(f"event_logger: write failed for {path.name}: {e}")
            raise

    def _push_back(self, recs: list) -> None:
        """Put events back into the buffer when a disk write fails. Respects maxlen; old data dequeues automatically."""
        with self._lock:
            # Oldest first, newest last: deque.extendleft reverses order, so use ordinary extend
            # after moving the current (newer) buffer contents to the end.
            current = list(self._buffer)
            self._buffer.clear()
            for r in recs:
                self._buffer.append(r)
            for r in current:
                self._buffer.append(r)

    def _log_fail(self, msg: str) -> None:
        """On consecutive failures only log on transitions, preventing log spam."""
        self._fail_count += 1
        if self._fail_count == 1:
            logger.warning(msg)
        elif self._fail_count % 100 == 0:
            logger.warning(f"{msg} (×{self._fail_count} since last log)")

    # ---- Internal: cleanup ----

    def _cleanup(self) -> None:
        """Delete shards older than 7 days; force-delete the oldest when the directory total exceeds 20MB.

        Trigger frequency: throttled to 5min (_CLEANUP_MIN_INTERVAL). Even flushing
        every 60s only really listdirs once per 5min; negligible cost.
        """
        try:
            if not self._dir.exists():
                return
            # List all shards, sorting filenames (days) oldest first.
            cutoff_day = (date.today() - timedelta(days=_RETENTION_DAYS)).strftime("%Y%m%d")
            entries: list = []
            for entry in self._dir.iterdir():
                name = entry.name
                if not (name.startswith(_FILE_PREFIX) and name.endswith(_FILE_SUFFIX)):
                    continue
                day_key = name[len(_FILE_PREFIX): -len(_FILE_SUFFIX)]
                # Require eight digits to prevent accidental deletion.
                if len(day_key) != 8 or not day_key.isdigit():
                    continue
                try:
                    size = entry.stat().st_size
                except OSError:
                    continue
                entries.append((day_key, entry, size))

            entries.sort(key=lambda x: x[0])  # Oldest first.

            # First pass: retention deletion.
            kept: list = []
            for day_key, path, size in entries:
                if day_key < cutoff_day:
                    try:
                        path.unlink()
                        logger.debug(f"event_logger: pruned {path.name} (age > {_RETENTION_DAYS}d)")
                    except OSError as e:
                        logger.debug(f"event_logger: failed to prune {path.name}: {e}")
                        kept.append((day_key, path, size))
                else:
                    kept.append((day_key, path, size))

            # Second pass: directory cap; delete oldest until within limit.
            total = sum(s for _, _, s in kept)
            while total > _DIR_SIZE_CAP and kept:
                day_key, path, size = kept.pop(0)
                try:
                    path.unlink()
                    total -= size
                    logger.info(
                        f"event_logger: force-pruned {path.name} (dir size > {_DIR_SIZE_CAP}B)"
                    )
                except OSError as e:
                    logger.debug(f"event_logger: failed to force-prune {path.name}: {e}")
                    break  # Do not loop forever on deletion failures.

            # Remove expired sealed_days entries so the next cycle can write again.
            today_key = date.today().strftime("%Y%m%d")
            self._sealed_days = {d for d in self._sealed_days if d == today_key}

        except Exception as e:
            logger.debug(f"event_logger: cleanup error (non-critical): {e}")


# ---------------------------------------------------------------------------
# Convenient module-level function.
# ---------------------------------------------------------------------------


def emit(name: str, **fields) -> None:
    """Record a sparse event. Equivalent to EventLogger.get_instance().emit(name, **fields)."""
    EventLogger.get_instance().emit(name, **fields)


def flush() -> int:
    """Flush the buffer to disk. Called by TokenTracker periodic save; business code usually doesn't need it."""
    return EventLogger.get_instance().flush()
