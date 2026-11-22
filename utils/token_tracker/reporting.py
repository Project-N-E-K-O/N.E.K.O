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
"""Remote telemetry reporting state and TokenTracker reporting methods."""

import asyncio
import copy
import gzip
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
import urllib.request
import urllib.error
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from utils import file_utils as _file_utils
from utils.file_utils import PublishedFileSyncError, atomic_write_json

from ._shared import _file_lock, _merge_day_stats, logger
from .telemetry import (
    _bucket_proactive_interval, _get_anonymous_device_id, _get_app_version_from_changelog,
    _get_device_hw, _get_legacy_device_id, _get_telemetry_branch,
    _get_telemetry_locale, _get_telemetry_metadata, _get_telemetry_timezone,
)

_TELEMETRY_SERVER_URL = "http://118.31.122.91:8099"

if _TELEMETRY_SERVER_URL and not _TELEMETRY_SERVER_URL.startswith(("http://", "https://")):
    logger.warning("Token tracker: invalid telemetry URL scheme, disabling remote reporting")
    _TELEMETRY_SERVER_URL = ""

_TELEMETRY_HMAC_SECRET = "neko-v1-a3f8b2c1d4e5f6789012345678abcdef"  # noqa: S105

_DO_NOT_TRACK = any(
    os.getenv(v, "").strip() in ("1", "true", "yes")
    for v in ("NEKO_DO_NOT_TRACK", "DO_NOT_TRACK")
)

_TELEMETRY_REPORT_INTERVAL = 60

_TELEMETRY_TIMEOUT = 10  # Seconds.

_TELEMETRY_GZIP_THRESHOLD = 1024

_DAY_STAT_KEYS = (
    "total_prompt_tokens",
    "total_completion_tokens",
    "total_tokens",
    "cached_tokens",
    "total_prompt_chars",
    "call_count",
    "error_count",
)
_BUCKET_STAT_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "prompt_chars",
    "call_count",
)


def _is_finite_stat_number(val) -> bool:
    """Return True when ``val`` is an int or finite float (excluding bool)."""
    if isinstance(val, bool):
        return False
    if isinstance(val, int):
        return True
    if isinstance(val, float):
        return math.isfinite(val)
    return False


def _is_valid_batch_seq(value) -> bool:
    """Return True when ``value`` is a non-empty UTF-8 encodable batch sequence."""
    if not isinstance(value, str) or not value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _is_valid_day_stats(day_val) -> bool:
    """Return True when one day's statistics match ``_merge_day_stats`` field types."""
    if not isinstance(day_val, dict):
        return False
    for key in _DAY_STAT_KEYS:
        if key in day_val and not _is_finite_stat_number(day_val[key]):
            return False
    for map_key in ("by_model", "by_call_type"):
        if map_key not in day_val:
            continue
        nested = day_val[map_key]
        if not isinstance(nested, dict):
            return False
        for bucket in nested.values():
            if not isinstance(bucket, dict):
                return False
            for key in _BUCKET_STAT_KEYS:
                if key in bucket and not _is_finite_stat_number(bucket[key]):
                    return False
    return True

# Final saves must not queue behind work whose stop signal is issued by
# shutdown. One shared worker also serializes periodic and shutdown saves.
# The executor is lazy. Normal interpreter shutdown joins its IO before atexit;
# os._exit (including merged-mode exit) skips both, so pending saves may be lost.
_SAVE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="token-save")

def _compute_telemetry_signature(payload_json: str, timestamp: float) -> str:
    """Compute the HMAC-SHA256 signature for telemetry reporting."""
    body_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    message = f"{timestamp}|{body_hash}"
    return hmac.new(
        _TELEMETRY_HMAC_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

def record_settings_state() -> None:
    """Read the current proactive-chat / privacy-mode settings and emit one settings_state counter.

    Trigger timing: **only** at app start (record_app_start, main_server process only).

    Semantics (finalized after CodeRabbit feedback): the server-side instrument_counters
    UPSERTs cumulatively by (stat_date, device_id, metric_key). This function only emits
    at startup, so one record = "the user's settings combo at this launch". N launches
    per device per day add +N — an **observation count**, not a gauge-style "current
    final state" — but good enough for analyzing "which tiers heavy users habitually
    use": per device, the combo with the highest count is its habitual tier.

    Deliberately **not** instrumented in save_global_conversation_settings: that would
    +1 a new combo every time a user flips a setting within a day, polluting the
    distribution into a "switching trajectory". Precise per-device-per-day final state
    would need server-side gauge/overwrite semantics, which the current instrument
    pipeline doesn't support and this analysis doesn't require.

    Use: the server slices out heavy users by device active days / event_count, then
    looks at the distribution of their settings_state dim combos — i.e. "where do heavy
    users set proactive chat / privacy mode".

    Dims are all low-cardinality enums; interval is bucketed, raw seconds not sent:
    - proactive: on / off (proactiveChatEnabled)
    - interval: <10s / 10-30s / ... / >=300s ("off" when off)
    - vision_chat: on / off (proactiveVisionChatEnabled)
    - privacy: on / off (privacy mode = inverse of proactiveVisionEnabled, default off)
    """
    if _DO_NOT_TRACK:
        return
    try:
        from utils.preferences import load_global_conversation_settings
        from utils.instrument import counter as _c
        s = load_global_conversation_settings()
        proactive_on = bool(s.get("proactiveChatEnabled", False))
        _c(
            "settings_state", 1,
            proactive="on" if proactive_on else "off",
            interval=(_bucket_proactive_interval(s.get("proactiveChatInterval", 0))
                      if proactive_on else "off"),
            vision_chat="on" if s.get("proactiveVisionChatEnabled", False) else "off",
            # Privacy mode is the inverse of proactiveVisionEnabled (default True means privacy off).
            privacy="on" if not s.get("proactiveVisionEnabled", True) else "off",
        )
    except Exception:
        # Telemetry failures silently leave business behavior unaffected.
        pass


AVATAR_TELEMETRY_TYPES = ("live2d", "vrm", "mmd", "pngtuber")


def resolve_avatar_telemetry_type(catgirl: dict) -> str:
    """Map one character config to a low-cardinality avatar type: live2d / vrm / mmd / pngtuber / unknown.

    Mirrors the renderer choice in ``/page_config``: a missing model_type means Live2D,
    legacy ``vrm`` means Live3D, and Live3D picks VRM vs MMD from ``live3d_sub_type``
    with a model-path fallback (MMD path first, then VRM path).
    """
    from utils.config_manager.reserved_schema import get_reserved

    if not isinstance(catgirl, dict):
        return "unknown"
    model_type = str(
        get_reserved(catgirl, "avatar", "model_type", default="live2d", legacy_keys=("model_type",))
        or "live2d"
    ).strip().lower()
    if model_type == "vrm":
        model_type = "live3d"
    if model_type in ("live2d", "pngtuber"):
        return model_type
    if model_type != "live3d":
        return "unknown"
    sub_type = str(
        get_reserved(catgirl, "avatar", "live3d_sub_type", default="", legacy_keys=("live3d_sub_type",))
        or ""
    ).strip().lower()
    if sub_type in ("vrm", "mmd"):
        return sub_type
    if get_reserved(catgirl, "avatar", "mmd", "model_path", default=""):
        return "mmd"
    if get_reserved(catgirl, "avatar", "vrm", "model_path", default="", legacy_keys=("vrm",)):
        return "vrm"
    return "unknown"


def record_avatar_state() -> None:
    """Emit which avatar types (Live2D / VRM / MMD / PNGTuber) this install uses, once per app start.

    Two counters, both with a single ``type`` dim (live2d / vrm / mmd / pngtuber / unknown):
    - ``avatar_type_active``: the type of the current character. Server-side
      ``COUNT(DISTINCT device_id)`` per type = how many users actually run each type.
    - ``avatar_type_owned``: one entry per type present among all characters, value =
      number of characters of that type. Distinct devices per type = how many users have
      at least one character of that type (a device can count under several types).

    Same observation-count semantics as ``settings_state`` (main_server start only).
    Only the enum type is sent — never character names or model paths.
    """
    if _DO_NOT_TRACK:
        return
    try:
        from utils.config_manager import get_config_manager
        from utils.instrument import counter as _c

        characters = get_config_manager().load_characters()
        if not isinstance(characters, dict):
            return
        catgirls = characters.get("猫娘")
        if not isinstance(catgirls, dict) or not catgirls:
            return

        owned: dict[str, int] = {}
        for cfg in catgirls.values():
            t = resolve_avatar_telemetry_type(cfg)
            owned[t] = owned.get(t, 0) + 1
        for t, n in owned.items():
            _c("avatar_type_owned", n, type=t)

        current = characters.get("当前猫娘", "")
        if current not in catgirls:
            # Same fallback as get_character_data: first character when the current one is invalid.
            current = next(iter(catgirls))
        _c("avatar_type_active", 1, type=resolve_avatar_telemetry_type(catgirls.get(current)))
    except Exception:
        # Telemetry failures silently leave business behavior unaffected.
        pass


class ReportingMixin:
    """Remote reporting and periodic persistence methods."""

    def _get_report_lock(self) -> threading.Lock:
        """Return the per-instance lock serializing remote telemetry reporting."""
        lock = getattr(self, "_report_lock", None)
        if lock is None:
            with self._lock:
                lock = getattr(self, "_report_lock", None)
                if lock is None:
                    lock = threading.Lock()
                    self._report_lock = lock
        return lock

    @property
    def _unsent_queue_path(self) -> Path:
        """Persistence file for the unsent remote-reporting queue.

        _unsent_daily is lost when the process is killed (pure memory).
        Writing the queue to this file lets a restart recover and resend it.
        """
        return self._config_manager.config_dir / ".telemetry_unsent.json"

    def _atexit_save(self):
        """atexit safety net: a final best effort to save before process exit.

        Covers: SIGTERM / uncaught exceptions / normal exit / sys.exit()
        Not covered: SIGKILL (kill -9) / power loss — at most 60s of data lost then

        Ordering matters: first emit session_end into the instrument buffer, then
        save(). save() → _report_to_server snapshots through instrument, so the emit
        must happen first; otherwise session_end's counter/histogram enters the buffer
        but never gets snapshotted, and the remote dashboard never sees session_end —
        the paired session_start is visible while session_end is forever missing, so
        the dashboard's "abnormal exit rate" gets miscomputed as 100%. The event goes
        separately through event_logger.flush into local jsonl.
        """
        # Move global declarations to function entry: step 3b reads _TELEMETRY_SERVER_URL;
        # Python requires global before any use, otherwise SyntaxError.
        global _TELEMETRY_SERVER_URL
        # ── 1) Emit session_end to instrument buffer before save() includes it ──
        try:
            from utils.instrument import (
                event as _instr_event,
                counter as _instr_counter,
                histogram as _instr_histogram,
            )
            duration = (time.time() - self._session_start_ts) if self._session_start_ts > 0 else 0.0
            _instr_event(
                "session_end",
                process=self._session_process,
                duration_sec=round(duration, 1),
            )
            _instr_counter("session_end", process=self._session_process)
            if duration > 0:
                # Pass seconds directly; instrument bounds are unit-agnostic numbers.
                _instr_histogram("session_duration_sec", duration, process=self._session_process)
            # User-message turns this session (unconditional, including zero): zero means no-message session.
            # Compare session_duration_sec: short +zero means opened and left; long +zero means
            # idle without interaction. Core D1 distinction between sampling and engagement.
            _instr_histogram("session_turn_count", self._session_msg_count, process=self._session_process)
        except Exception:
            # Instrument import/emit failures must not block process exit; losing one entry is preferable
            # to atexit exceptions, which alter SIGTERM exit codes.
            pass

        # ── 2) Bypass 60s throttle: atexit is the final opportunity ──
        # _report_to_server's ``now - self._last_report_time < interval``
        # prevents reports for short sessions exiting within 60s, leaving freshly emitted
        # session_end counters/histograms in buffers forever. Explicitly reset
        # to zero so that condition never matches. Theoretical side effect: another report if last
        # successful reporting was <60s before atexit; server seen_batches deduplicates
        # by batch_seq, preventing double counting.
        with self._lock:
            self._last_report_time = 0.0
            self._bypass_report_interval = True

        # ── 3) save() sends daily_stats plus freshly emitted instrument snapshots ──
        try:
            # save() first: persists delta to disk and attempts remote report
            # (best-effort final push). Then disable remote URL so no further
            # network calls happen during interpreter teardown.
            self.save()
        except Exception:
            # save failures do not escape atexit; unsent already persisted on failure,
            # retried next process startup.
            pass

        # ── 3b) First save may retransmit (old failed _pending_batch_seq):
        # _report_to_server skips instrument snapshots on is_retry, leaving new
        # session_end/session_duration_sec buffered. If networking recovers before exit,
        # retry succeeds and clears batch_seq, but without another send,
        # session metrics disappear before URL closure (Codex). Check remaining instruments
        # plus cleared batch_seq (successful retry; next fresh window snapshots)
        # and bypass throttle for one more send.
        try:
            from utils.instrument import has_data as _instrument_has_data
            if (_instrument_has_data() and self._pending_batch_seq is None
                    and _TELEMETRY_SERVER_URL and not _DO_NOT_TRACK):
                with self._lock:
                    self._last_report_time = 0.0
                    self._bypass_report_interval = True
                self.save()
        except Exception:
            # A final instrument flush is best-effort during interpreter shutdown.
            pass

        # ── 3c) Flush any deferred records queued while save() held self._lock ──
        # A stream generator finalized while step 3 / 3b held self._lock queues
        # into _pending_records via non-blocking acquire to avoid self-deadlock.
        # Persist those trailing entries before exit (bounded so a live daemon
        # thread cannot stall shutdown).
        try:
            for _ in range(3):
                if not getattr(self, "_pending_records", None):
                    break
                with self._lock:
                    self._last_report_time = 0.0
                    self._bypass_report_interval = True
                self.save()
        except Exception:
            # Best-effort trailing flush during interpreter shutdown.
            pass

        # ── 4) Flush event_logger: local jsonl, outside remote instrument delivery ──
        try:
            from utils.event_logger import EventLogger
            EventLogger.get_instance().flush()
        except Exception:
            # Failed event_logger flush loses sparse local jsonl events without startup
            # recovery, but counters/histograms already went through instruments.
            # Only diagnostic details are affected; never block atexit.
            pass
        finally:
            _TELEMETRY_SERVER_URL = ""

    @staticmethod
    def _is_valid_unsent_window(days, records) -> bool:
        """Return True when an unsent queue window is mergeable, finite, and UTF-8 JSON encodable."""
        if (
            not isinstance(days, dict)
            or not isinstance(records, list)
            or not all(_is_valid_day_stats(day_val) for day_val in days.values())
        ):
            return False
        try:
            json.dumps(
                {"daily": days, "records": records},
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError):
            return False
        return True

    def _load_unsent_queue_locked(self) -> bool:
        """Load persisted unsent queue while the caller holds ``self._get_report_lock()``.

        Returns True once the disk state has been loaded (or is confirmed absent or
        unrecoverable syntax/schema), and False on transient ``OSError`` so later
        saves retry recovery instead of overwriting the unconfirmed disk queue.
        """
        if self._unsent_queue_loaded:
            return True
        try:
            data = json.loads(self._unsent_queue_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("invalid telemetry queue")
        except FileNotFoundError:
            self._unsent_queue_loaded = True
            return True
        except OSError as exc:
            logger.debug("Token tracker: failed to load unsent queue: %s", exc)
            return False
        except (ValueError, AttributeError, TypeError) as exc:
            logger.debug("Token tracker: ignoring invalid unsent queue: %s", exc)
            self._unsent_queue_loaded = True
            return True

        raw_daily, raw_records = data.get("daily", {}), data.get("records", [])
        raw_next_daily, raw_next_records = data.get("next_daily", {}), data.get("next_records", [])
        raw_batch_seq = data.get("batch_seq")
        has_batch_seq = _is_valid_batch_seq(raw_batch_seq)
        has_next_window = "next_daily" in data or "next_records" in data
        is_numbered_snapshot = has_batch_seq or has_next_window or raw_batch_seq is not None
        primary_valid = self._is_valid_unsent_window(raw_daily, raw_records)
        next_valid = (
            self._is_valid_unsent_window(raw_next_daily, raw_next_records)
            if is_numbered_snapshot
            else False
        )

        with self._lock:
            if is_numbered_snapshot:
                if has_batch_seq and primary_valid:
                    pending_seq: str | None = data["batch_seq"]
                    pending_daily, pending_records = raw_daily, raw_records
                else:
                    logger.debug(
                        "Token tracker: discarding malformed pending batch window (%r)",
                        data.get("batch_seq"),
                    )
                    pending_seq = None
                    pending_daily, pending_records = {}, []

                if next_valid:
                    recovered_daily, recovered_records = raw_next_daily, raw_next_records
                else:
                    if has_next_window:
                        logger.debug("Token tracker: discarding malformed next unsent queue window")
                    recovered_daily, recovered_records = {}, []
            else:
                pending_seq = None
                pending_daily, pending_records = {}, []
                if primary_valid:
                    recovered_daily, recovered_records = raw_daily, raw_records
                else:
                    logger.debug("Token tracker: discarding malformed unsent queue window")
                    recovered_daily, recovered_records = {}, []

            try:
                merged_daily = copy.deepcopy(recovered_daily)
                for day_key, day_delta in self._unsent_daily.items():
                    if day_key not in merged_daily:
                        merged_daily[day_key] = copy.deepcopy(day_delta)
                    else:
                        _merge_day_stats(merged_daily[day_key], day_delta)
                merged_records = (list(recovered_records) + list(self._unsent_records))[-200:]
                json.dumps(
                    {"daily": merged_daily, "records": merged_records},
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError, AttributeError, UnicodeEncodeError, OverflowError) as exc:
                logger.debug("Token tracker: ignoring malformed unsent queue totals: %s", exc)
                merged_daily = copy.deepcopy(self._unsent_daily)
                merged_records = list(self._unsent_records)

            self._pending_batch_seq = pending_seq
            self._pending_report_daily = pending_daily
            self._pending_report_records = pending_records
            self._unsent_daily = merged_daily
            self._unsent_records = merged_records
            self._unsent_queue_loaded = True
        return True

    def _load_unsent_queue(self):
        """Load remote data that previously failed to report, at startup."""
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return
        with self._get_report_lock():
            self._load_unsent_queue_locked()

    def _save_unsent_queue(self) -> bool:
        """Persist both windows atomically while the caller holds the report lock."""
        if not self._unsent_queue_loaded:
            return False
        data = {
            "batch_seq": self._pending_batch_seq,
            "daily": self._unsent_daily,
            "records": self._unsent_records,
        }
        if self._pending_batch_seq:
            data.update(daily=self._pending_report_daily, records=self._pending_report_records,
                        next_daily=self._unsent_daily, next_records=self._unsent_records)
        try:
            if self._pending_batch_seq or self._unsent_daily or self._unsent_records:
                atomic_write_json(self._unsent_queue_path, data)
            else:
                self._unsent_queue_path.unlink(missing_ok=True)
            return True
        except OSError as exc:
            logger.debug("Token tracker: failed to persist unsent queue: %s", exc)
            return False

    def submit_save(self, *, report_timeout: float | None = None) -> Future:
        """Queue a save even when shutdown has no asynchronous wait budget left.

        Submission is synchronous, but disk/network IO runs on the independent
        worker. The concurrent future owns queued work without an event loop.
        Normal interpreter shutdown joins the executor before atexit. Merged
        mode and owner-death exits use os._exit, bypassing both; saves that
        shutdown stopped waiting for are best effort and may be lost.
        """
        return _SAVE_EXECUTOR.submit(self.save, report_timeout=report_timeout)

    async def asave(self, *, report_timeout: float | None = None):
        """Save independently of application workers without cancelling queued IO."""
        await asyncio.shield(asyncio.wrap_future(self.submit_save(report_timeout=report_timeout)))

    def save(self, *, report_timeout: float | None = None):
        """Persist incremental data to disk. Multi-process safe.

        Flow:
        1. take a delta snapshot and clear it inside the thread lock (swap pattern)
        2. read-merge-write inside the file lock
        3. on write failure, put the delta back into memory

        Not-dirty must still trigger remote reporting: for users with pure frontend
        interaction (counter/histogram, no LLM calls), self._dirty stays False forever;
        the old logic's early return kept the instrument accumulation window from ever
        being sent. Skip the local disk write but still call _report_to_server, letting
        it decide internally via has_data() whether to actually POST.
        """
        report_options = {} if report_timeout is None else {"timeout": report_timeout}
        with self._lock:
            self._drain_pending_locked()
            needs_dir_sync = bool(getattr(self, "_storage_dir_sync_pending", False))
            dir_sync_epoch = int(getattr(self, "_storage_dir_sync_epoch", 0))
            if not self._dirty:
                report_only = True
                delta_daily: dict = {}
                delta_records: list = []
            else:
                report_only = False
                # Extract deltas via swap; do not return them on success.
                delta_daily = self._delta_daily
                delta_records = list(self._delta_records)
                self._delta_daily = {}
                self._delta_records.clear()
                self._dirty = False

        if report_only:
            if needs_dir_sync:
                try:
                    _file_utils._fsync_directory(self._storage_path.parent)
                except OSError as exc:
                    logger.warning("Token usage directory sync retry failed: %s", exc)
                else:
                    with self._lock:
                        if int(getattr(self, "_storage_dir_sync_epoch", 0)) == dir_sync_epoch:
                            self._storage_dir_sync_pending = False
            # No local LLM data to write; check only pending instruments.
            try:
                self._report_to_server(delta_daily, delta_records, **report_options)
            except Exception:
                # Remote failures do not affect idle paths: no local writes remain, only network
                # errors retried next 60s cycle / atexit. _report_to_server already persists
                # failed unsent data; avoid duplicate logging.
                pass
            return

        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)

            with _file_lock(self._lock_file_path):
                # Read existing data.
                existing = self._load_file(self._storage_path)
                if not existing:
                    existing = self._empty_file_data()

                # Merge deltas into existing.
                for day_key, day_delta in delta_daily.items():
                    if day_key not in existing["daily_stats"]:
                        existing["daily_stats"][day_key] = day_delta
                    else:
                        _merge_day_stats(existing["daily_stats"][day_key], day_delta)

                # Merge recent_records.
                existing["recent_records"].extend(delta_records)
                existing["recent_records"] = self._dedupe_records(existing["recent_records"])

                # Clean records older than 90 days.
                cutoff = (date.today() - timedelta(days=90)).isoformat()
                old_keys = [k for k in existing["daily_stats"] if k < cutoff]
                for k in old_keys:
                    del existing["daily_stats"][k]

                existing["last_saved"] = datetime.now().isoformat()
                with self._lock:
                    write_sync_epoch = int(getattr(self, "_storage_dir_sync_epoch", 0))
                try:
                    atomic_write_json(self._storage_path, existing)
                except PublishedFileSyncError as exc:
                    # The totals are already visible to the next read-merge-write.
                    # Restoring this delta would add it to the same file twice.
                    # Mark parent directory sync pending so a later save retries fsync only.
                    with self._lock:
                        self._storage_dir_sync_epoch = int(
                            getattr(self, "_storage_dir_sync_epoch", 0)
                        ) + 1
                        self._storage_dir_sync_pending = True
                    logger.warning("Token usage was published with uncertain durability: %s", exc)
                else:
                    with self._lock:
                        if int(getattr(self, "_storage_dir_sync_epoch", 0)) == write_sync_epoch:
                            self._storage_dir_sync_pending = False

            # After local save, try remote reporting outside the file lock to avoid blocking other processes.
            try:
                self._report_to_server(delta_daily, delta_records, **report_options)
            except Exception:
                pass  # Remote-report failures silently leave local saves unaffected.

        except Exception as e:
            logger.warning(f"Failed to save token usage data: {e}")
            # Write failure: restore deltas in memory for retry.
            with self._lock:
                for day_key, day_delta in delta_daily.items():
                    if day_key not in self._delta_daily:
                        self._delta_daily[day_key] = day_delta
                    else:
                        _merge_day_stats(self._delta_daily[day_key], day_delta)
                # Restore records oldest first, newest last.
                restored = delta_records + list(self._delta_records)
                self._delta_records.clear()
                self._delta_records.extend(restored[-200:])
                self._dirty = True

    def _report_to_server(self, delta_daily: dict, delta_records: list, *, timeout: float | None = None):
        """Report incremental data to the remote telemetry server.

        Data-loss prevention design:
        - _unsent_daily accumulates in memory while also persisted to .telemetry_unsent.json
        - after the process is killed and restarted, _load_unsent_queue() recovers unsent data
        - the unsent queue file is cleared after a successful send
        - on send failure, retain the numbered window and keep new deltas separate
        """
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return

        with self._get_report_lock():
            if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
                return

            queue_loaded = self._load_unsent_queue_locked()

            # Accumulate unsent data.
            now = time.time()
            with self._lock:
                for day_key, day_delta in delta_daily.items():
                    if day_key not in self._unsent_daily:
                        self._unsent_daily[day_key] = copy.deepcopy(day_delta)
                    else:
                        _merge_day_stats(self._unsent_daily[day_key], day_delta)
                self._unsent_records.extend(delta_records)
                if len(self._unsent_records) > 200:
                    self._unsent_records = self._unsent_records[-200:]
                last_report_time = self._last_report_time
                bypass_interval = (
                    bool(getattr(self, "_bypass_report_interval", False))
                    if queue_loaded
                    else False
                )
                if bypass_interval:
                    self._bypass_report_interval = False

            if not queue_loaded:
                return

            if not bypass_interval and now - last_report_time < self._report_interval:
                if delta_daily or delta_records:
                    self._save_unsent_queue()
                return

            # Peek accumulated instruments: even without daily_stats (no LLM calls but frontend
            # interaction counters), any instrument data merits a report.
            try:
                from utils.instrument import has_data as _instrument_has_data
                has_instruments = _instrument_has_data()
            except Exception:
                has_instruments = False

            is_retry = self._pending_batch_seq is not None
            if not (is_retry or self._unsent_daily or self._unsent_records or has_instruments):
                return
            while True:
                if not is_retry and (self._unsent_daily or self._unsent_records):
                    self._pending_report_daily, self._unsent_daily = self._unsent_daily, {}
                    self._pending_report_records, self._unsent_records = self._unsent_records, []
                    self._pending_batch_seq = secrets.token_hex(8)
                if self._pending_batch_seq and not self._save_unsent_queue():
                    return
                try:
                    self._send_report(
                        self._pending_report_daily, self._pending_report_records,
                        self._pending_batch_seq or secrets.token_hex(8), is_retry, timeout,
                    )
                except Exception as exc:
                    logger.debug("Token tracker: telemetry report failed (non-critical): %s", exc)
                    return
                with self._lock:
                    self._last_report_time = now
                if self._pending_batch_seq is None:
                    return  # Instrument-only reports have no disk queue.
                self._pending_batch_seq = None
                self._pending_report_daily, self._pending_report_records = {}, []
                if not (self._unsent_daily or self._unsent_records):
                    self._save_unsent_queue()
                    return
                # The acknowledged window and queued deltas stay on disk until
                # the next numbered window replaces them before its first POST.
                is_retry = False

    def _send_report(self, send_daily, send_records, batch_seq, is_retry, timeout):
        """Send one batch. Only a confirmed response allows its queue to advance."""
        # Snapshot instruments only for fresh windows; retries preserve them for later.
        instruments_snapshot: dict = {}
        if not is_retry:
            try:
                from utils.instrument import snapshot as _instrument_snapshot
                instruments_snapshot = _instrument_snapshot()
            except Exception as e:
                logger.debug(f"Token tracker: instrument snapshot failed (non-critical): {e}")

        if not self._device_id:
            self._device_id = _get_anonymous_device_id()
        if not self._branch:
            self._branch = _get_telemetry_branch(self._config_manager.config_dir)

        app_version = _get_app_version_from_changelog()
        telemetry_locale = _get_telemetry_locale()
        telemetry_timezone = _get_telemetry_timezone()
        # Get distribution +steam_user_id in one call from one source, avoiding
        # two GetSteamID calls crossing SDK readiness and producing inconsistent
        # release +nonempty Steam64.
        telemetry_distribution, telemetry_steam_user_id = _get_telemetry_metadata()
        telemetry_device_hw = _get_device_hw()

        payload = {
            "device_id": self._device_id,
            # During migration include the old-algorithm ID for server matching in events.payload
            # for future legacy->new mappings that fold historical cohorts. Current server
            # Pydantic models omit this field and ignore it by default; HMAC
            # covers canonical JSON of the entire payload dict,
            # so server verification automatically includes it without changes.
            "device_id_legacy": _get_legacy_device_id(),
            "app_version": app_version,
            "branch": self._branch,
            "locale": telemetry_locale,
            "timezone": telemetry_timezone,
            "distribution": telemetry_distribution,
            # Populate only when Steamworks SDK is ready and Steam64 available; otherwise use
            # empty string. Server preserve-known semantics retain historical values on empties.
            "steam_user_id": telemetry_steam_user_id,
            # Device hardware profile (low-cardinality composite enums). Server preserve-known
            # UPSERTs device attributes for retention joins analyzing day-one low-end-device churn.
            "device_hw": telemetry_device_hw,
            "daily_stats": send_daily,
            "recent_records": send_records,
        }
        # Instrument snapshots use optional fields: old servers ignore them; new servers
        # store unchanged in events.payload for later dashboard parsing. HMAC
        # covers the whole payload, so added fields preserve verification.
        if instruments_snapshot:
            payload["instruments"] = instruments_snapshot
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        ts = time.time()
        sig = _compute_telemetry_signature(payload_json, ts)

        # batch_id supports server seen_batches idempotent deduplication with two goals:
        #   (a) Failed retries / network timeouts after server commit without client 200
        #       must preserve batch_id when resending the same daily data,
        #       letting the server recognize and skip duplicate commits.
        #   (b) Different windows, including instrument-only ones without daily data,
        #       need unique batch_ids, or prior seen_batches entries
        #       wrongly discard all subsequent instruments.
        #
        # batch_seq meets both: allocate on first entry to a window; preserve on retries,
        # including after killed-process restarts; clear after 200. Hash the sequence,
        # without hashing daily/records/instruments themselves, especially clear-on-read
        # instruments that cannot recur on retry and would otherwise
        # violate (a).
        #
        # batch_core uses **only retry-stable fields**: device_id +batch_seq.
        # Deliberately exclude app_version, read live from changelog each report;
        # updating the app between retries would change the same unsent batch's ID,
        # bypassing seen_batches on new-version restart after timeout-after-commit
        # and double-counting committed daily_stats (Codex P1). Exclude device_id_legacy
        # too, dependent on unstable multi-interface uuid.getnode() enumeration.
        # batch_seq is unique per window and stable across retries; device_id prevents
        # cross-device collisions. Enough; HMAC still covers the entire payload including app_version.
        batch_core = {
            "device_id": payload["device_id"],
            "batch_seq": batch_seq,
        }
        batch_id = hashlib.sha256(
            json.dumps(batch_core, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:32]
        submission = {
            "timestamp": ts,
            "signature": sig,
            "payload": payload,
            "batch_id": batch_id,
        }
        body = json.dumps(submission, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        # Gzip only >=1KB: smaller payloads are not worth it (see _TELEMETRY_GZIP_THRESHOLD).
        # mtime=0 makes identical bodies produce identical compressed bytes, avoiding timestamp
        # differences resembling duplicate reports during diff debugging/fuzzing.
        if len(body) >= _TELEMETRY_GZIP_THRESHOLD:
            body = gzip.compress(body, compresslevel=6, mtime=0)
            headers["Content-Encoding"] = "gzip"

        req = urllib.request.Request(
            f"{_TELEMETRY_SERVER_URL}/api/v1/telemetry",
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(
            req, timeout=_TELEMETRY_TIMEOUT if timeout is None else timeout,
        ) as resp:
            if resp.status != 200:
                raise OSError(f"Telemetry HTTP {resp.status}")

    def _needs_periodic_save(self) -> bool:
        """Return whether the periodic save loop has local sync, telemetry, or instrument work."""
        with self._lock:
            if (
                self._dirty
                or bool(getattr(self, "_pending_records", None))
                or getattr(self, "_storage_dir_sync_pending", False)
            ):
                return True
            if self._pending_batch_seq or self._unsent_daily or self._unsent_records:
                return True
            if not self._unsent_queue_loaded and _TELEMETRY_SERVER_URL and not _DO_NOT_TRACK:
                return True
        try:
            from utils.instrument import has_data as _instrument_has_data
            return bool(_instrument_has_data())
        except Exception:
            # Under lock, has_data only checks dict truthiness and normally cannot raise;
            # import failures fall through, skipping this cycle and retrying next.
            return False

    def start_periodic_save(self):
        """Start the background periodic save task. Must be called inside an asyncio loop."""
        if self._save_task is None or self._save_task.done():
            self._save_task = asyncio.create_task(self._periodic_save_loop())
            logger.info("Token tracker periodic save started")

    async def _periodic_save_loop(self):
        while True:
            await asyncio.sleep(self._save_interval)
            # Two save() triggers (plus pending directory fsync / unread queue recovery):
            #   (a) self._dirty: LLM token deltas need local writes + remote reporting.
            #   (b) instrument has_data: frontend-only interactions (WS telemetry /
            #       ws_connect / feature counters) never set _dirty=True,
            #       but instruments accumulated a window due on the 60s cadence.
            # save() takes report-only paths for (b), skipping local writes and only calling
            # _report_to_server; (a) takes full write + report.
            if self._needs_periodic_save():
                await self.asave()
            # Also persist sparse event_logger buffers and run retention cleanup.
            # Even without token_tracker dirty state, event_logger may hold
            # session/crash events. Do not gate on self._dirty, or frontend-only
            # interactions without LLM calls leave events buffered forever.
            # event_logger.flush throttles itself (cleanup every 5min); nothing-to-do
            # paths take roughly microseconds.
            try:
                from utils.event_logger import EventLogger
                await asyncio.to_thread(EventLogger.get_instance().flush)
            except Exception as e:
                logger.debug(f"Token tracker: event_logger flush failed (non-critical): {e}")
