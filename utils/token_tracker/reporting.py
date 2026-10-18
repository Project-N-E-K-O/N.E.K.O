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
import os
import secrets
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path
from utils.file_utils import atomic_write_json

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


class ReportingMixin:
    """Remote reporting and periodic persistence methods."""

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
                self.save()
        except Exception:
            # A final instrument flush is best-effort during interpreter shutdown.
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

    def _load_unsent_queue(self):
        """Load remote data that previously failed to report, at startup."""
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return
        try:
            p = self._unsent_queue_path
            if not p.exists():
                return
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            loaded_daily = data.get("daily", {})
            loaded_records = data.get("records", [])
            loaded_batch_seq = data.get("batch_seq")
            if loaded_daily:
                with self._lock:
                    for day_key, day_val in loaded_daily.items():
                        if day_key not in self._unsent_daily:
                            self._unsent_daily[day_key] = day_val
                        else:
                            _merge_day_stats(self._unsent_daily[day_key], day_val)
                    self._unsent_records.extend(loaded_records)
                    if len(self._unsent_records) > 200:
                        self._unsent_records = self._unsent_records[-200:]
                    # Restore batch_seq for the previous unsent window, reusing its sequence
                    # after restart so server seen_batches deduplicates uncertain outcomes
                    # (server may have committed before the client was killed).
                    if isinstance(loaded_batch_seq, str) and loaded_batch_seq:
                        self._pending_batch_seq = loaded_batch_seq
                logger.debug(f"Token tracker: loaded {len(loaded_daily)} days of unsent telemetry from disk")
            # Delete after successful loading to avoid duplicate loads.
            p.unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"Token tracker: failed to load unsent queue: {e}")

    def _save_unsent_queue(self):
        """Persist currently unsent remote data to disk.

        When called:
        1. after a successful save(), if unsent data awaits remote reporting
        2. at the atexit safety net (via save → _report_to_server → failure → persist)

        batch_seq is persisted along: failure + process crash + restart → the retry
        uses the same seq, letting server seen_batches dedupe commits of uncertain
        outcome.
        """
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return
        try:
            with self._lock:
                if not self._unsent_daily:
                    # No data: clean leftover files.
                    self._unsent_queue_path.unlink(missing_ok=True)
                    return
                data = {
                    "daily": copy.deepcopy(self._unsent_daily),
                    "records": list(self._unsent_records[-200:]),
                    "batch_seq": self._pending_batch_seq,
                    "saved_at": time.time(),
                }
            atomic_write_json(self._unsent_queue_path, data)
        except Exception as e:
            logger.debug(f"Token tracker: failed to persist unsent queue: {e}")

    def save(self):
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
        with self._lock:
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
            # No local LLM data to write; check only pending instruments.
            try:
                self._report_to_server(delta_daily, delta_records)
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
                atomic_write_json(self._storage_path, existing)

            # After local save, try remote reporting outside the file lock to avoid blocking other processes.
            try:
                self._report_to_server(delta_daily, delta_records)
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

    def _report_to_server(self, delta_daily: dict, delta_records: list):
        """Report incremental data to the remote telemetry server.

        Data-loss prevention design:
        - _unsent_daily accumulates in memory while also persisted to .telemetry_unsent.json
        - after the process is killed and restarted, _load_unsent_queue() recovers unsent data
        - the unsent queue file is cleared after a successful send
        - on send failure, put back into memory + persisted, retried next time
        """
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return

        # Accumulate unsent data.
        with self._lock:
            for day_key, day_delta in delta_daily.items():
                if day_key not in self._unsent_daily:
                    self._unsent_daily[day_key] = copy.deepcopy(day_delta)
                else:
                    _merge_day_stats(self._unsent_daily[day_key], day_delta)
            self._unsent_records.extend(delta_records)
            if len(self._unsent_records) > 200:
                self._unsent_records = self._unsent_records[-200:]

        # Persist unsent queue against process kills.
        self._save_unsent_queue()

        # Check reporting interval.
        now = time.time()
        if now - self._last_report_time < self._report_interval:
            return

        # Peek accumulated instruments: even without daily_stats (no LLM calls but frontend
        # interaction counters), any instrument data merits a report.
        try:
            from utils.instrument import has_data as _instrument_has_data
            has_instruments = _instrument_has_data()
        except Exception:
            has_instruments = False

        # Retrieve outgoing data, distinguishing:
        #   is_retry=False: fresh window, new batch_seq, normal instrument snapshot.
        #   is_retry=True: retransmission of failed data with the same batch_seq; **exclude**
        #                   new instruments because server seen_batches already knows
        #                   this batch_id, deduplicating the entire batch as duplicate,
        #                   silently discarding any added instruments too.
        #                   Retain them in buffer and send separately in the next fresh window
        #                   with a new batch_seq.
        with self._lock:
            if not self._unsent_daily and not has_instruments:
                return
            send_daily = self._unsent_daily
            send_records = self._unsent_records
            self._unsent_daily = {}
            self._unsent_records = []
            is_retry = self._pending_batch_seq is not None
            if self._pending_batch_seq is None:
                self._pending_batch_seq = secrets.token_hex(8)
            batch_seq = self._pending_batch_seq
        # Track whether daily/records accompany this send; needed to clear stale
        # batch_seq after instrument-only failure (see except comments).
        had_unsent_payload = bool(send_daily or send_records)

        # Snapshot instruments only for fresh windows; retries preserve them for later.
        instruments_snapshot: dict = {}
        if not is_retry:
            try:
                from utils.instrument import snapshot as _instrument_snapshot
                instruments_snapshot = _instrument_snapshot()
            except Exception as e:
                logger.debug(f"Token tracker: instrument snapshot failed (non-critical): {e}")

        try:
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
            with urllib.request.urlopen(req, timeout=_TELEMETRY_TIMEOUT) as resp:
                if resp.status == 200:
                    self._last_report_time = now
                    # Success: clear batch_seq for the next window and remove unsent files.
                    with self._lock:
                        self._pending_batch_seq = None
                    self._unsent_queue_path.unlink(missing_ok=True)
                    logger.debug("Token tracker: telemetry reported successfully")
                    return

            raise Exception(f"HTTP {resp.status}")

        except Exception as e:
            logger.debug(f"Token tracker: telemetry report failed (non-critical): {e}")
            # Failure: restore/persist unsent. Daily-bearing failures **retain**
            # _pending_batch_seq for retries, so server seen_batches deduplicates
            # uncertain outcomes where network timed out after server commit.
            #
            # Instrument-only failures (empty send_daily/send_records,
            # had_unsent_payload=False) must clear batch_seq: instruments are
            # clear-on-read with nothing to restore. Stale sequences make the **next fresh window**
            # reuse a committed batch_id hash, so the server
            # returns duplicate, skipped, silently dropping new data.
            with self._lock:
                for day_key, day_delta in send_daily.items():
                    if day_key not in self._unsent_daily:
                        self._unsent_daily[day_key] = day_delta
                    else:
                        _merge_day_stats(self._unsent_daily[day_key], day_delta)
                restored = send_records + self._unsent_records
                self._unsent_records = restored[-200:]
                if not had_unsent_payload:
                    # No actual retry content: prevent stale sequences harming the next window.
                    self._pending_batch_seq = None
            self._save_unsent_queue()

    def start_periodic_save(self):
        """Start the background periodic save task. Must be called inside an asyncio loop."""
        if self._save_task is None or self._save_task.done():
            self._save_task = asyncio.create_task(self._periodic_save_loop())
            logger.info("Token tracker periodic save started")

    async def _periodic_save_loop(self):
        while True:
            await asyncio.sleep(self._save_interval)
            # Two save() triggers:
            #   (a) self._dirty: LLM token deltas need local writes +remote reporting.
            #   (b) instrument has_data: frontend-only interactions (WS telemetry /
            #       ws_connect / feature counters) never set _dirty=True,
            #       but instruments accumulated a window due on the 60s cadence.
            # save() takes report-only paths for (b), skipping local writes and only calling
            # _report_to_server; (a) takes full write +report.
            need_save = self._dirty
            if not need_save:
                try:
                    from utils.instrument import has_data as _instrument_has_data
                    need_save = _instrument_has_data()
                except Exception:
                    # Under lock, has_data only checks dict truthiness and normally cannot raise;
                    # import failures fall through, skipping this cycle and retrying next.
                    pass
            if need_save:
                await asyncio.to_thread(self.save)
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
