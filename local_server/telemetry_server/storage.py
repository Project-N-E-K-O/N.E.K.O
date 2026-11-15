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
Telemetry Server — SQLite storage

Design:
- events table: append-only raw event log (audit trail, tamper-evident)
- daily_aggregates table: pre-aggregated stats (UPSERT accumulation)
- devices table: device activity tracking
- WAL mode: writes do not block reads

Capacity estimate (20k DAU):
- 3 processes/device × 6 req/h × 8h ≈ 144 req/device/day
- 20k × 144 ≈ 2.88M events/day (peak ~50 req/s)
- SQLite WAL single-threaded writes ~500 req/s; a single instance is enough
- events table pruned at 180 days; aggregate tables kept forever
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

_logger = logging.getLogger("telemetry.storage")


def _bucket_quantile(buckets: list, bounds: list, q: float) -> dict:
    """Approximate quantile: find the first bucket whose cumulative sample count >= q*total; return that bucket's upper bound.

    Args:
        buckets: list of bucket counts; the last one is the overflow bucket (> rightmost bound)
        bounds: list of bucket upper bounds, len(bounds) == len(buckets) - 1
        q: quantile (0~1)

    Returns:
        {"upper_bound": number or None, "bucket_index": int}
        upper_bound None means it falls in the overflow bucket (no upper bound).
    """
    total = sum(buckets) if buckets else 0
    if total <= 0:
        return {"upper_bound": None, "bucket_index": -1}
    target = q * total
    cum = 0
    for i, c in enumerate(buckets):
        cum += c
        if cum >= target:
            upper = bounds[i] if i < len(bounds) else None
            return {"upper_bound": upper, "bucket_index": i}
    return {"upper_bound": None, "bucket_index": len(buckets) - 1}


def _as_int_count(x) -> int | None:
    """Normalize an integer-count-semantics value to int; non-integer / NaN / Inf / bool return None.

    A histogram's count and buckets must be integer counts. Accepts int or
    integer-valued float (e.g. ``4.0``, tolerance across serializers); rejects
    ``4.5`` / NaN / Inf / True/False. Normalize first, then run the sum==count check
    and DB write, avoiding inconsistencies from ``int()`` truncation.
    """
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        if math.isfinite(x) and x.is_integer():
            return int(x)
    return None


def normalize_steam_id(raw) -> str:
    """Normalize a steam_user_id from any source to the canonical decimal Steam64; invalid input returns ''.

    Single source of truth: ingest (server.py) and canonical edge building (scanning
    events.payload) share this one implementation, so the two write paths cannot
    drift apart and split one identity into two nodes.

    raw is intentionally untyped (not str): edge building scans raw events.payload
    JSON, where a forged / anomalous row's steam_user_id may be number / null /
    another type; calling .isdigit() directly would throw. Type-guard first, then
    apply the string rules. Ingest passes a Pydantic str, for which the guard is
    harmless.

    Rules (consistent with historical ingest): pure digits + length <= 20 (u64 has
    20 decimal digits; cheap pre-check against oversized-string DoS) +
    0 < int < 2^64 (excludes the '0'/'00' sentinels and out-of-range values) +
    str(int()) to drop leading zeros (otherwise '00076561...' and '76561...' would
    count as two different accounts).
    """
    if not isinstance(raw, str):
        return ""
    # Require isascii: isdigit accepts Unicode superscripts/Arabic-Indic digits,
    # but int('²') raises ValueError. Without guarding, ingest returns 500 and transactional
    # build_edges rolls back without advancing, letting one forged event permanently block canonical rebuilds.
    if not raw.isascii() or not raw.isdigit() or len(raw) > 20:
        return ""
    value = int(raw)
    if 0 < value < (1 << 64):
        return str(value)
    return ""


# Allowlist device_hw at public ingest: open-source HMAC secrets make client strings untrusted;
# forged requests could preserve-known arbitrary 64-character hardware profiles, inflating cardinality
# or injecting PII. Require four os|arch|ram_tier|cpu_tier segments, each from known enums.
_DEVICE_HW_ALLOWED = (
    {"win", "mac", "linux", "other"},
    {"x86_64", "arm64", "other"},
    {"lt8", "8to16", "16to32", "ge32", "unknown"},
    {"le4", "5to8", "9to16", "gt16", "unknown"},
)


def normalize_device_hw(raw) -> str:
    """Validate the device_hw composite string (same semantics as the client's _get_device_hw); invalid input falls back to ''.

    Server-boundary anti-forgery: only pass strings with exactly 4 segments, each
    hitting its corresponding enum; everything else becomes '' (including None /
    non-str / wrong segment count / any out-of-range segment). Like steam_user_id,
    whitelisting at the ingest boundary protects device_hw's low-cardinality +
    zero-PII contract.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    parts = raw.split("|")
    if len(parts) != len(_DEVICE_HW_ALLOWED):
        return ""
    if all(p in allowed for p, allowed in zip(parts, _DEVICE_HW_ALLOWED)):
        return raw
    return ""


class TelemetryStorage:
    """Thread-safe SQLite telemetry storage."""

    def __init__(self, db_path: str | Path = "telemetry.db"):
        self._db_path = str(db_path)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialized = False
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.row_factory = sqlite3.Row
            # Disable Python implicit transactions; _transaction explicitly uses BEGIN IMMEDIATE.
            # Default WAL deferred transactions do not take write locks on reads, allowing concurrent
            # writers between histogram read/merge/write steps and losing updates.
            conn.isolation_level = None
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def _transaction(self):
        conn = self._get_conn()
        # BEGIN IMMEDIATE acquires SQLite write locks at transaction start, serializing store_event's
        # read-buckets/Python-merge/writeback against other workers/processes with independent connections,
        # including same-device main/agent/memory simultaneous reports.
        # Otherwise both requests read old buckets, merge independently, and later writes overwrite
        # earlier data, diverging bucket distributions from count/sum (Codex P1). busy_timeout=10000
        # waits for writers instead of immediately reporting database-locked errors.
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _ensure_tables(self):
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            conn = self._get_conn()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    received_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')),
                    device_id   TEXT    NOT NULL,
                    app_version TEXT    NOT NULL DEFAULT 'unknown',
                    payload     TEXT    NOT NULL,
                    event_date  TEXT    NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_device   ON events(device_id);
                CREATE INDEX IF NOT EXISTS idx_events_date     ON events(event_date);

                CREATE TABLE IF NOT EXISTS daily_aggregates (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id         TEXT    NOT NULL,
                    stat_date         TEXT    NOT NULL,
                    model             TEXT    NOT NULL DEFAULT '_total',
                    call_type         TEXT    NOT NULL DEFAULT '_total',
                    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens      INTEGER NOT NULL DEFAULT 0,
                    cached_tokens     INTEGER NOT NULL DEFAULT 0,
                    call_count        INTEGER NOT NULL DEFAULT 0,
                    error_count       INTEGER NOT NULL DEFAULT 0,
                    updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')),
                    UNIQUE(device_id, stat_date, model, call_type)
                );
                CREATE INDEX IF NOT EXISTS idx_agg_device ON daily_aggregates(device_id);
                CREATE INDEX IF NOT EXISTS idx_agg_date   ON daily_aggregates(stat_date);

                CREATE TABLE IF NOT EXISTS devices (
                    device_id     TEXT PRIMARY KEY,
                    app_version   TEXT    NOT NULL DEFAULT 'unknown',
                    branch        TEXT    NOT NULL DEFAULT 'unknown',
                    locale        TEXT    NOT NULL DEFAULT 'unknown',
                    timezone      TEXT    NOT NULL DEFAULT 'unknown',
                    distribution  TEXT    NOT NULL DEFAULT 'unknown',
                    steam_user_id TEXT    NOT NULL DEFAULT '',
                    device_hw     TEXT    NOT NULL DEFAULT '',
                    first_seen    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')),
                    last_seen     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')),
                    event_count   INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS seen_batches (
                    batch_id    TEXT PRIMARY KEY,
                    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours'))
                );

                -- ===== canonical identity 身份聚合层 =====
                -- device⟷steam 观测边（append-only）。边由 build_edges_from_events
                -- 扫 events 产出，不在 ingest 路径里产边。first_seen/last_seen 用事件
                -- 观测时间 events.received_at，可空（纯连通兜底边留 NULL）。
                CREATE TABLE IF NOT EXISTS device_steam_edges (
                    device_id     TEXT NOT NULL,
                    steam_user_id TEXT NOT NULL,        -- 归一化十进制 Steam64
                    first_seen    TEXT,
                    last_seen     TEXT,
                    observe_count INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (device_id, steam_user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_dse_steam ON device_steam_edges(steam_user_id);

                -- device⟷device 别名边（device_id 算法升级导致同机新旧两 ID）。
                -- 边源 = events.payload.device_id_legacy。dev_lo/dev_hi 按字典序存，
                -- 保证 (a,b) 与 (b,a) 去重为同一行。
                CREATE TABLE IF NOT EXISTS device_alias_edges (
                    dev_lo        TEXT NOT NULL,
                    dev_hi        TEXT NOT NULL,
                    first_seen    TEXT,
                    last_seen     TEXT,
                    observe_count INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (dev_lo, dev_hi)
                );
                CREATE INDEX IF NOT EXISTS idx_dae_hi ON device_alias_edges(dev_hi);

                -- 删号防复活硬约束：所有产边路径先查 denylist，命中跳过。
                CREATE TABLE IF NOT EXISTS steam_id_denylist (
                    steam_user_id TEXT PRIMARY KEY,
                    deleted_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours'))
                );

                -- union-find 连通分量落表。entity_type ∈ ('device','steam')。
                CREATE TABLE IF NOT EXISTS canonical_map (
                    entity_type  TEXT NOT NULL,
                    entity_id    TEXT NOT NULL,
                    canonical_id TEXT NOT NULL,
                    PRIMARY KEY (entity_type, entity_id)
                );
                CREATE INDEX IF NOT EXISTS idx_cm_canonical ON canonical_map(canonical_id);

                -- 合并历史，外部引用顺 alias 链解析到当前 canonical（保持一跳）。
                CREATE TABLE IF NOT EXISTS canonical_alias (
                    old_canonical_id TEXT PRIMARY KEY,
                    new_canonical_id TEXT NOT NULL,
                    merged_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours'))
                );

                -- 边构建增量游标（处理到的最大 events.id）。
                CREATE TABLE IF NOT EXISTS edge_build_cursor (
                    id        INTEGER PRIMARY KEY CHECK (id = 1),
                    last_event_id INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO edge_build_cursor (id, last_event_id) VALUES (1, 0);

                -- ---- Instrument aggregates ----
                -- Counter：按 (天, 设备, metric_key) 唯一，value 累加。metric_key
                -- 是客户端 utils/instrument._make_key 的产物，形如 "name" 或
                -- "name|dim1=v1,dim2=v2"。维度切片靠 dashboard 端 SQL LIKE
                -- 或后续加专门的 dims 列做拆分。
                CREATE TABLE IF NOT EXISTS instrument_counters (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    stat_date  TEXT    NOT NULL,
                    device_id  TEXT    NOT NULL,
                    metric_key TEXT    NOT NULL,
                    value      REAL    NOT NULL DEFAULT 0,
                    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')),
                    UNIQUE(stat_date, device_id, metric_key)
                );
                CREATE INDEX IF NOT EXISTS idx_ic_date ON instrument_counters(stat_date);
                CREATE INDEX IF NOT EXISTS idx_ic_key  ON instrument_counters(metric_key);

                -- Histogram：count / sum / buckets（JSON 数组）累加。bounds
                -- 在客户端固定（utils/instrument._HIST_BOUNDS），后续若改
                -- 需要数据迁移；当前用 ON CONFLICT 简单覆盖最新 bounds，
                -- 历史 buckets 维度不一致时由查询端按 metric_key 自检。
                CREATE TABLE IF NOT EXISTS instrument_histograms (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    stat_date  TEXT    NOT NULL,
                    device_id  TEXT    NOT NULL,
                    metric_key TEXT    NOT NULL,
                    count      INTEGER NOT NULL DEFAULT 0,
                    sum        REAL    NOT NULL DEFAULT 0,
                    buckets    TEXT    NOT NULL DEFAULT '[]',
                    bounds     TEXT    NOT NULL DEFAULT '[]',
                    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')),
                    UNIQUE(stat_date, device_id, metric_key)
                );
                CREATE INDEX IF NOT EXISTS idx_ih_date ON instrument_histograms(stat_date);
                CREATE INDEX IF NOT EXISTS idx_ih_key  ON instrument_histograms(metric_key);
            """)
            # Older devices tables predate branch/locale/timezone/distribution/steam_user_id
            # columns. CREATE TABLE IF NOT EXISTS leaves existing schemas unchanged,
            # so add columns explicitly; SQLite ALTER ADD COLUMN is O(1), filling existing rows with DEFAULT.
            # Catch exceptions because concurrent workers/replicas may migrate at initial
            # startup; PRAGMA plus ALTER is not atomic, so after one worker adds a column,
            # another acts on stale PRAGMA results and hits
            # duplicate-column errors. Ignore those to keep concurrent migration idempotent.
            existing_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(devices)").fetchall()
            }
            # (column, default sentinel): missing categorical fields use 'unknown';
            # missing Steam ID uses empty strings, with corresponding UPSERT
            # preserve-known sentinel checks.
            _new_cols = (
                ("branch", "unknown"),
                ("locale", "unknown"),
                ("timezone", "unknown"),
                ("distribution", "unknown"),
                ("steam_user_id", ""),
                ("device_hw", ""),
            )
            for col_name, default in _new_cols:
                if col_name in existing_cols:
                    continue
                try:
                    conn.execute(
                        f"ALTER TABLE devices ADD COLUMN {col_name} TEXT NOT NULL DEFAULT '{default}'"
                    )
                except sqlite3.OperationalError as e:
                    # Swallow duplicate-column-name errors only; propagate other schema failures.
                    if "duplicate column name" not in str(e).lower():
                        raise
            conn.commit()
            self._initialized = True

    # -- Writes --

    def is_duplicate_batch(self, batch_id: str | None) -> bool:
        """Check whether batch_id was already processed. No dedup when batch_id is absent."""
        if not batch_id:
            return False
        conn = self._get_conn()
        row = conn.execute("SELECT 1 FROM seen_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return row is not None

    def store_event(self, device_id: str, app_version: str, payload_json: str,
                    daily_stats: dict, batch_id: str | None = None,
                    branch: str = "unknown", locale: str = "unknown",
                    timezone: str = "unknown", distribution: str = "unknown",
                    steam_user_id: str = "", device_hw: str = "",
                    instruments: dict | None = None):
        today = date.today().isoformat()
        with self._transaction() as conn:
            # Denylist consolidation: deleted Steam64 IDs must never reenter devices through reports.
            # Check within serialized write transactions so committed denylists are visible, replacing IDs with ''.
            if steam_user_id and conn.execute(
                "SELECT 1 FROM steam_id_denylist WHERE steam_user_id = ?", (steam_user_id,)
            ).fetchone():
                steam_user_id = ""
            if batch_id:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO seen_batches (batch_id) VALUES (?)",
                    (batch_id,),
                )
                # True idempotence gate: rowcount=0 means batch_id already exists from races
                # between server prechecks/writes or concurrent retries. BEGIN IMMEDIATE
                # makes this check atomic with downstream writes; second requests short-circuit
                # before events/daily_aggregates/instrument writes,
                # preventing double persistence/counting (CodeRabbit).
                if cur.rowcount == 0:
                    return
            conn.execute(
                "INSERT INTO events (device_id, app_version, payload, event_date) VALUES (?, ?, ?, ?)",
                (device_id, app_version, payload_json, today),
            )
            for stat_date, day_data in daily_stats.items():
                self._upsert_aggregate(
                    conn, device_id, stat_date, "_total", "_total",
                    day_data.get("total_prompt_tokens", 0),
                    day_data.get("total_completion_tokens", 0),
                    day_data.get("total_tokens", 0),
                    day_data.get("cached_tokens", 0),
                    day_data.get("call_count", 0),
                    day_data.get("error_count", 0),
                )
                for model, bucket in day_data.get("by_model", {}).items():
                    self._upsert_aggregate(
                        conn, device_id, stat_date, model, "_total",
                        bucket.get("prompt_tokens", 0), bucket.get("completion_tokens", 0),
                        bucket.get("total_tokens", 0), bucket.get("cached_tokens", 0),
                        bucket.get("call_count", 0), 0,
                    )
                for call_type, bucket in day_data.get("by_call_type", {}).items():
                    self._upsert_aggregate(
                        conn, device_id, stat_date, "_total", call_type,
                        bucket.get("prompt_tokens", 0), bucket.get("completion_tokens", 0),
                        bucket.get("total_tokens", 0), bucket.get("cached_tokens", 0),
                        bucket.get("call_count", 0), 0,
                    )
            # Clients persist stable branch on first launch; one device normally has only one known
            # value. Known values overwrite, including fresh truth after reset/reassignment.
            # locale/timezone/distribution are current readings, likewise overwriting only when
            # known. Older clients omit fields and default to unknown, while transient
            # detection failures such as tzlocal errors must not erase
            # previous good values with unknown.
            # branch/locale/timezone/distribution use 'unknown' sentinels;
            # steam_user_id uses empty strings, never unknown as an apparently valid
            # downstream JOIN ID. Both sentinel types preserve known values,
            # leaving history unchanged for sentinel inputs.
            conn.execute("""
                INSERT INTO devices (device_id, app_version, branch, locale, timezone, distribution, steam_user_id, device_hw,
                                     first_seen, last_seen, event_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                        strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours'),
                        strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours'), 1)
                ON CONFLICT(device_id) DO UPDATE SET
                    app_version   = excluded.app_version,
                    branch        = CASE WHEN excluded.branch        = 'unknown' THEN devices.branch        ELSE excluded.branch        END,
                    locale        = CASE WHEN excluded.locale        = 'unknown' THEN devices.locale        ELSE excluded.locale        END,
                    timezone      = CASE WHEN excluded.timezone      = 'unknown' THEN devices.timezone      ELSE excluded.timezone      END,
                    distribution  = CASE WHEN excluded.distribution  = 'unknown' THEN devices.distribution  ELSE excluded.distribution  END,
                    steam_user_id = CASE WHEN excluded.steam_user_id = ''        THEN devices.steam_user_id ELSE excluded.steam_user_id END,
                    -- device_hw 硬件静态：空 sentinel 不覆写；且"退化" profile（含更多
                    -- unknown 段，多半是某次 psutil/cpu 检测临时失败）不得覆盖已知更完整
                    -- 的 profile —— 用 'unknown' 出现次数（length diff）粗比，incoming 更
                    -- 多就保留历史。检测变好（unknown 更少）或首次（历史空）正常覆写。
                    device_hw     = CASE
                        WHEN excluded.device_hw = '' THEN devices.device_hw
                        WHEN devices.device_hw != ''
                             AND (length(excluded.device_hw) - length(replace(excluded.device_hw, 'unknown', '')))
                               > (length(devices.device_hw) - length(replace(devices.device_hw, 'unknown', '')))
                        THEN devices.device_hw
                        ELSE excluded.device_hw END,
                    last_seen = strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours'),
                    event_count = event_count + 1
            """, (device_id, app_version, branch, locale, timezone, distribution, steam_user_id, device_hw))

            # Accumulate instruments in the same transaction; failure rolls back the whole batch,
            # preventing partially persisted daily_stats after instrument failures.
            if instruments:
                self._apply_instruments(conn, device_id, instruments, fallback_stat_date=today)

    def store_instruments(self, device_id: str, instruments: dict | None,
                          fallback_stat_date: str | None = None) -> None:
        """Standalone entry point for accumulating instrument snapshots (opens its own transaction).

        Normally store_event calls _apply_instruments inside its own transaction;
        this entry point serves standalone tests / offline imports / cross-table
        backfills.
        """
        if not instruments:
            return
        with self._transaction() as conn:
            self._apply_instruments(conn, device_id, instruments, fallback_stat_date)

    def _apply_instruments(self, conn, device_id: str, instruments: dict | None,
                           fallback_stat_date: str | None = None) -> None:
        """Accumulate an instrument snapshot (counter + histogram). Must be called on a conn with an open transaction.

        Args:
            conn: transaction connection held by the caller
            device_id: reporting device
            instruments: output of the client's utils/instrument.Instrument.snapshot(),
                shape: {window_start, window_end, bounds, counters, histograms}
            fallback_stat_date: used as stat_date when instruments carries no time
                window or parsing fails (usually pass 'today').

        Failure handling: a single metric failing to parse does not affect the
        others; a whole-section failure raises sqlite3 errors for the caller's
        transaction to roll back.
        """
        if not instruments or not isinstance(instruments, dict):
            return

        # stat_date precedence, correctly identified by CodeRabbit:
        #   1) Client snapshot stat_date, its local calendar day, matching
        #      daily_stats keys and preventing server-local timezone from splitting
        #      near-midnight reports from one device across dates.
        #   2) Server-timezone day from window_end for older clients.
        #   3) fallback_stat_date, usually today.isoformat().
        # Start with fallback_stat_date, normally today; select candidate dates below,
        # but both sources require the same recency validation.
        today = date.today()
        stat_date = today.isoformat()
        # Live reports anchor to today, while store_instruments also promises offline
        # imports/cross-table backfill. Explicit fallback_stat_date becomes the validation
        # anchor, avoiding mispartitioning valid historical snapshots into today.
        # Live paths explicitly pass today, preserving behavior.
        # Assign fallback to stat_date only after successful parsing, never persisting invalid
        # fallback strings like foo into retention partitions (CodeRabbit).
        recency_anchor = today
        if fallback_stat_date:
            try:
                recency_anchor = date.fromisoformat(fallback_stat_date)
                stat_date = recency_anchor.isoformat()
            except ValueError:
                recency_anchor = today
        candidate: date | None = None

        # Source 1: client snapshot stat_date, its local day matching daily_stats.
        # Open-source HMAC secrets permit forged payload dates like
        # invalid 9999-99-99/abcd-ef-gh or parseable but out-of-range 9999-12-31.
        client_date = instruments.get("stat_date")
        if isinstance(client_date, str) and len(client_date) == 10:
            try:
                candidate = date.fromisoformat(client_date)
            except ValueError:
                candidate = None

        # Source 2: window_end's server-local day for older/missing client_date.
        # Apply identical recency checks because skewed/forged window_end also
        # creates retention-exempt future partitions (Codex).
        if candidate is None:
            window_end = instruments.get("window_end")
            if isinstance(window_end, (int, float)) and window_end > 0:
                try:
                    from datetime import datetime as _dt
                    candidate = _dt.fromtimestamp(window_end).date()
                except (TypeError, ValueError, OSError, OverflowError):
                    candidate = None

        # Shared recency check: instruments are live clear-on-read windows, not historical
        # unsent replay, so dates approximate server today. Allow +/-2 days for up to
        # +/-14-hour client timezones and clock skew. Out-of-range future/broken-clock dates
        # fall back or lexical stat_date<cutoff retention never removes dashboard pollution.
        if candidate is not None and abs((candidate - recency_anchor).days) <= 2:
            stat_date = candidate.isoformat()

        counters = instruments.get("counters") or {}
        histograms = instruments.get("histograms") or {}
        bounds = instruments.get("bounds") or []
        bounds_json = json.dumps(bounds, ensure_ascii=False)

        # ---- counters ----
        for metric_key, value in counters.items():
            if not isinstance(metric_key, str) or not isinstance(value, (int, float)):
                continue
            # Reject NaN/Inf: Python SQLite binds NaN as NULL into REAL NOT NULL,
            # raising IntegrityError and rolling back store_event, including valid
            # daily_stats. Skip individual bad samples rather than abort whole batches (Codex P2).
            if not math.isfinite(value):
                continue
            if len(metric_key) > 256:
                # Reject overlong client keys; truncation could collide with existing
                # UPSERT keys and corrupt data.
                continue
            conn.execute("""
                INSERT INTO instrument_counters (stat_date, device_id, metric_key, value)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(stat_date, device_id, metric_key) DO UPDATE SET
                    value      = value + excluded.value,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')
            """, (stat_date, device_id, metric_key, float(value)))

        # ---- histograms ----
        for metric_key, h in histograms.items():
            if not isinstance(metric_key, str) or not isinstance(h, dict):
                continue
            if len(metric_key) > 256:
                continue
            count = h.get("count", 0)
            hsum = h.get("sum", 0.0)
            raw_buckets = h.get("buckets") or []
            if not isinstance(raw_buckets, list):
                continue
            # Counts/buckets are integer quantities; accept ints or integral floats such
            # as 4.0, rejecting fractional/NaN/Inf/bool values.
            # Otherwise database int(count) truncation makes stored counts disagree with buckets:
            # count=0.5/buckets=[0.5] passes floating sum equality but
            # persists count=0 alongside [0.5], breaking quantiles (CodeRabbit).
            count = _as_int_count(count)
            if count is None:
                continue
            buckets = []
            _bucket_ok = True
            for b in raw_buckets:
                bi = _as_int_count(b)
                if bi is None:
                    _bucket_ok = False
                    break
                buckets.append(bi)
            if not _bucket_ok:
                continue
            # hsum sums observations and may be fractional, e.g. latency, if finite and nonbool.
            if isinstance(hsum, bool) or not isinstance(hsum, (int, float)) or not math.isfinite(hsum):
                continue
            # Validate shape consistency against forged/corrupt payloads silently skewing dashboards:
            # averages use counts, while p50/p95 use buckets, so mismatches
            # contradict metrics. Require nonnegative count/buckets,
            # matching bound length len(bounds)=len(buckets)-1 for overflow, and sum(buckets)=count.
            if count < 0 or any(b < 0 for b in buckets):
                continue
            if bounds and len(bounds) != len(buckets) - 1:
                continue
            if sum(buckets) != count:
                continue

            # SQL lacks convenient elementwise array addition; read old buckets,
            # merge in Python, and write back within one consistent transaction.
            row = conn.execute(
                "SELECT buckets FROM instrument_histograms "
                "WHERE stat_date = ? AND device_id = ? AND metric_key = ?",
                (stat_date, device_id, metric_key),
            ).fetchone()

            merged_buckets = list(buckets)
            if row is not None:
                try:
                    existing = json.loads(row["buckets"])
                    if isinstance(existing, list):
                        # Use maximum length and zero-pad mismatches, accommodating changed
                        # bucket definitions without breaking mixed migration data.
                        n = max(len(existing), len(buckets))
                        merged_buckets = [
                            (existing[i] if i < len(existing) else 0)
                            + (buckets[i] if i < len(buckets) else 0)
                            for i in range(n)
                        ]
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    _logger.debug(f"storage: histogram bucket merge fallback for {metric_key}: {e}")
                    merged_buckets = list(buckets)

            buckets_json = json.dumps(merged_buckets, ensure_ascii=False)
            conn.execute("""
                INSERT INTO instrument_histograms
                    (stat_date, device_id, metric_key, count, sum, buckets, bounds)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stat_date, device_id, metric_key) DO UPDATE SET
                    count      = count + excluded.count,
                    sum        = sum   + excluded.sum,
                    buckets    = excluded.buckets,
                    bounds     = excluded.bounds,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')
            """, (stat_date, device_id, metric_key, int(count), float(hsum),
                  buckets_json, bounds_json))

    @staticmethod
    def _upsert_aggregate(conn, device_id, stat_date, model, call_type,
                          prompt_tokens, completion_tokens, total_tokens,
                          cached_tokens, call_count, error_count):
        conn.execute("""
            INSERT INTO daily_aggregates
                (device_id, stat_date, model, call_type,
                 prompt_tokens, completion_tokens, total_tokens, cached_tokens,
                 call_count, error_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id, stat_date, model, call_type) DO UPDATE SET
                prompt_tokens     = prompt_tokens     + excluded.prompt_tokens,
                completion_tokens = completion_tokens + excluded.completion_tokens,
                total_tokens      = total_tokens      + excluded.total_tokens,
                cached_tokens     = cached_tokens     + excluded.cached_tokens,
                call_count        = call_count        + excluded.call_count,
                error_count       = error_count       + excluded.error_count,
                updated_at        = strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')
        """, (device_id, stat_date, model, call_type,
              prompt_tokens, completion_tokens, total_tokens, cached_tokens,
              call_count, error_count))

    # -- Queries --

    def get_global_stats(self, days: int = 30) -> dict:
        conn = self._get_conn()
        cutoff = (date.today() - timedelta(days=days)).isoformat()

        meta = conn.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(event_count), 0) as total FROM devices"
        ).fetchone()

        # Daily totals.
        rows = conn.execute("""
            SELECT stat_date,
                   SUM(prompt_tokens) as pt, SUM(completion_tokens) as ct,
                   SUM(total_tokens) as tt, SUM(cached_tokens) as cch,
                   SUM(call_count) as cc, SUM(error_count) as ec
            FROM daily_aggregates
            WHERE model = '_total' AND call_type = '_total' AND stat_date >= ?
            GROUP BY stat_date ORDER BY stat_date DESC
        """, (cutoff,)).fetchall()

        daily = {}
        for r in rows:
            daily[r["stat_date"]] = {
                "prompt_tokens": r["pt"], "completion_tokens": r["ct"],
                "total_tokens": r["tt"], "cached_tokens": r["cch"],
                "call_count": r["cc"], "error_count": r["ec"],
            }

        # Model totals.
        model_rows = conn.execute("""
            SELECT model,
                   SUM(prompt_tokens) as pt, SUM(completion_tokens) as ct,
                   SUM(total_tokens) as tt, SUM(cached_tokens) as cch,
                   SUM(call_count) as cc
            FROM daily_aggregates
            WHERE model != '_total' AND call_type = '_total' AND stat_date >= ?
            GROUP BY model ORDER BY tt DESC
        """, (cutoff,)).fetchall()

        by_model = {}
        for r in model_rows:
            by_model[r["model"]] = {
                "prompt_tokens": r["pt"], "completion_tokens": r["ct"],
                "total_tokens": r["tt"], "cached_tokens": r["cch"],
                "call_count": r["cc"],
            }

        # By call type.
        type_rows = conn.execute("""
            SELECT call_type,
                   SUM(prompt_tokens) as pt, SUM(completion_tokens) as ct,
                   SUM(total_tokens) as tt, SUM(cached_tokens) as cch,
                   SUM(call_count) as cc
            FROM daily_aggregates
            WHERE model = '_total' AND call_type != '_total' AND stat_date >= ?
            GROUP BY call_type ORDER BY tt DESC
        """, (cutoff,)).fetchall()

        by_call_type = {}
        for r in type_rows:
            by_call_type[r["call_type"]] = {
                "prompt_tokens": r["pt"], "completion_tokens": r["ct"],
                "total_tokens": r["tt"], "cached_tokens": r["cch"],
                "call_count": r["cc"],
            }

        return {
            "total_devices": meta["cnt"],
            "total_events": meta["total"],
            "daily_totals": daily,
            "by_model": by_model,
            "by_call_type": by_call_type,
        }

    def get_active_devices(self, days: int = 7, limit: int = 200) -> list[dict]:
        conn = self._get_conn()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute("""
            SELECT d.device_id, d.app_version, d.first_seen, d.last_seen, d.event_count,
                   COALESCE(SUM(a.total_tokens), 0) as recent_tokens,
                   COALESCE(SUM(a.cached_tokens), 0) as recent_cached,
                   COALESCE(SUM(a.call_count), 0) as recent_calls
            FROM devices d
            LEFT JOIN daily_aggregates a
              ON d.device_id = a.device_id
              AND a.model = '_total' AND a.call_type = '_total' AND a.stat_date >= ?
            WHERE d.last_seen >= ?
            GROUP BY d.device_id ORDER BY d.last_seen DESC
            LIMIT ?
        """, (cutoff, cutoff, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_user_metrics(self, days: int = 30) -> dict:
        """DAU / WAU / MAU / new devices / retention rate."""
        conn = self._get_conn()
        today = date.today()

        # -- Daily active devices, DAU trends --
        cutoff = (today - timedelta(days=days)).isoformat()
        dau_rows = conn.execute("""
            SELECT stat_date, COUNT(DISTINCT device_id) as dau
            FROM daily_aggregates
            WHERE model = '_total' AND call_type = '_total' AND stat_date >= ?
            GROUP BY stat_date ORDER BY stat_date DESC
        """, (cutoff,)).fetchall()
        dau_trend = {r["stat_date"]: r["dau"] for r in dau_rows}

        # -- Today's DAU --
        today_str = today.isoformat()
        dau_today = dau_trend.get(today_str, 0)

        # -- 7-day active devices, WAU --
        wau_cutoff = (today - timedelta(days=7)).isoformat()
        wau = conn.execute("""
            SELECT COUNT(DISTINCT device_id) as cnt
            FROM daily_aggregates
            WHERE model = '_total' AND call_type = '_total' AND stat_date >= ?
        """, (wau_cutoff,)).fetchone()["cnt"]

        # -- 30-day active devices, MAU --
        mau_cutoff = (today - timedelta(days=30)).isoformat()
        mau = conn.execute("""
            SELECT COUNT(DISTINCT device_id) as cnt
            FROM daily_aggregates
            WHERE model = '_total' AND call_type = '_total' AND stat_date >= ?
        """, (mau_cutoff,)).fetchone()["cnt"]

        # -- Daily new devices --
        new_rows = conn.execute("""
            SELECT DATE(first_seen) as join_date, COUNT(*) as cnt
            FROM devices
            WHERE DATE(first_seen) >= ?
            GROUP BY join_date ORDER BY join_date DESC
        """, (cutoff,)).fetchall()
        new_trend = {r["join_date"]: r["cnt"] for r in new_rows}

        # -- Next-day retention: yesterday's new devices still active today --
        yesterday = (today - timedelta(days=1)).isoformat()
        day_before = (today - timedelta(days=2)).isoformat()

        # Devices added the day before yesterday.
        cohort = conn.execute("""
            SELECT COUNT(*) as cnt FROM devices
            WHERE DATE(first_seen) = ?
        """, (day_before,)).fetchone()["cnt"]

        # Those still active yesterday.
        retained = 0
        if cohort > 0:
            retained = conn.execute("""
                SELECT COUNT(DISTINCT a.device_id) as cnt
                FROM daily_aggregates a
                JOIN devices d ON a.device_id = d.device_id
                WHERE DATE(d.first_seen) = ?
                  AND a.stat_date = ?
                  AND a.model = '_total' AND a.call_type = '_total'
            """, (day_before, yesterday)).fetchone()["cnt"]

        d1_retention = round(retained / cohort * 100, 1) if cohort > 0 else 0.0

        # -- 7-day retention --
        d7_anchor = (today - timedelta(days=8)).isoformat()
        d7_check = (today - timedelta(days=1)).isoformat()
        cohort_7 = conn.execute("""
            SELECT COUNT(*) as cnt FROM devices
            WHERE DATE(first_seen) = ?
        """, (d7_anchor,)).fetchone()["cnt"]
        retained_7 = 0
        if cohort_7 > 0:
            retained_7 = conn.execute("""
                SELECT COUNT(DISTINCT a.device_id) as cnt
                FROM daily_aggregates a
                JOIN devices d ON a.device_id = d.device_id
                WHERE DATE(d.first_seen) = ?
                  AND a.stat_date = ?
                  AND a.model = '_total' AND a.call_type = '_total'
            """, (d7_anchor, d7_check)).fetchone()["cnt"]
        d7_retention = round(retained_7 / cohort_7 * 100, 1) if cohort_7 > 0 else 0.0

        return {
            "dau_today": dau_today,
            "wau": wau,
            "mau": mau,
            "d1_retention": d1_retention,
            "d7_retention": d7_retention,
            "dau_trend": dau_trend,
            "new_device_trend": new_trend,
        }

    # -- Instrument queries --

    def get_top_counters(self, days: int = 7, limit: int = 50) -> list[dict]:
        """Aggregate counter totals across devices for the last N days, descending by total.

        Returns [{"metric_key": ..., "total": ..., "devices": ...}].
        """
        conn = self._get_conn()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute("""
            SELECT metric_key,
                   SUM(value) as total,
                   COUNT(DISTINCT device_id) as devices
            FROM instrument_counters
            WHERE stat_date >= ?
            GROUP BY metric_key
            ORDER BY total DESC
            LIMIT ?
        """, (cutoff, limit)).fetchall()
        return [{"metric_key": r["metric_key"], "total": r["total"], "devices": r["devices"]}
                for r in rows]

    def get_histogram_summary(self, days: int = 7, limit: int = 50) -> list[dict]:
        """Aggregate histograms across devices for the last N days.

        Returns [{"metric_key", "count", "sum", "avg", "p50_bucket", "p95_bucket", "bounds"}].
        p50/p95 buckets are located via cumulative buckets / total samples; precision
        is limited by bucket granularity — fine for monitoring trends; for exact
        quantiles query the raw events table.
        """
        conn = self._get_conn()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        # Merge same-metric buckets across devices by summing raw rows in Python.
        rows = conn.execute("""
            SELECT metric_key, count, sum, buckets, bounds
            FROM instrument_histograms
            WHERE stat_date >= ?
        """, (cutoff,)).fetchall()

        agg: dict = {}
        for r in rows:
            key = r["metric_key"]
            slot = agg.setdefault(key, {"count": 0, "sum": 0.0, "buckets": [], "bounds": None})
            slot["count"] += r["count"] or 0
            slot["sum"] += r["sum"] or 0.0
            try:
                bk = json.loads(r["buckets"]) if r["buckets"] else []
            except (json.JSONDecodeError, TypeError):
                bk = []
            if not isinstance(bk, list):
                bk = []
            # Align lengths as _apply_instruments does.
            n = max(len(slot["buckets"]), len(bk))
            slot["buckets"] = [
                (slot["buckets"][i] if i < len(slot["buckets"]) else 0)
                + (bk[i] if i < len(bk) else 0)
                for i in range(n)
            ]
            if slot["bounds"] is None and r["bounds"]:
                try:
                    bnd = json.loads(r["bounds"])
                    if isinstance(bnd, list):
                        slot["bounds"] = bnd
                except (json.JSONDecodeError, TypeError):
                    # Rare corrupt bounds: leave slot["bounds"]=None,
                    # allowing later valid rows to fill it; query-side quantiles permit missing bounds.
                    pass

        out = []
        for key, slot in agg.items():
            count = slot["count"]
            avg = (slot["sum"] / count) if count > 0 else 0.0
            buckets = slot["buckets"]
            bounds = slot["bounds"] or []
            p50 = _bucket_quantile(buckets, bounds, 0.5)
            p95 = _bucket_quantile(buckets, bounds, 0.95)
            out.append({
                "metric_key": key,
                "count": count,
                "sum": slot["sum"],
                "avg": round(avg, 2),
                "p50_bucket": p50,
                "p95_bucket": p95,
                "bounds": bounds,
            })
        out.sort(key=lambda x: -x["count"])
        return out[:limit]

    # -- Exports --

    def export_daily_csv(self, days: int = 90) -> str:
        """Export the per-day aggregated CSV."""
        conn = self._get_conn()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute("""
            SELECT stat_date, COUNT(DISTINCT device_id) as devices,
                   SUM(prompt_tokens) as prompt_tokens,
                   SUM(completion_tokens) as completion_tokens,
                   SUM(total_tokens) as total_tokens,
                   SUM(cached_tokens) as cached_tokens,
                   SUM(call_count) as call_count,
                   SUM(error_count) as error_count
            FROM daily_aggregates
            WHERE model = '_total' AND call_type = '_total' AND stat_date >= ?
            GROUP BY stat_date ORDER BY stat_date DESC
        """, (cutoff,)).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["date", "devices", "prompt_tokens", "completion_tokens",
                         "total_tokens", "cached_tokens", "call_count", "error_count"])
        for r in rows:
            writer.writerow([r["stat_date"], r["devices"], r["prompt_tokens"],
                             r["completion_tokens"], r["total_tokens"], r["cached_tokens"],
                             r["call_count"], r["error_count"]])
        return output.getvalue()

    def export_model_csv(self, days: int = 90) -> str:
        """Export the per-model aggregated CSV."""
        conn = self._get_conn()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute("""
            SELECT model, stat_date,
                   SUM(prompt_tokens) as pt, SUM(completion_tokens) as ct,
                   SUM(total_tokens) as tt, SUM(cached_tokens) as cch,
                   SUM(call_count) as cc
            FROM daily_aggregates
            WHERE model != '_total' AND call_type = '_total' AND stat_date >= ?
            GROUP BY model, stat_date ORDER BY stat_date DESC, tt DESC
        """, (cutoff,)).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["model", "date", "prompt_tokens", "completion_tokens",
                         "total_tokens", "cached_tokens", "call_count"])
        for r in rows:
            writer.writerow([r["model"], r["stat_date"], r["pt"], r["ct"],
                             r["tt"], r["cch"], r["cc"]])
        return output.getvalue()

    # -- Canonical identity aggregation --

    def add_steam_id_to_denylist(self, steam_user_id: str) -> str:
        """Account deletion: the deleted Steam64 goes into the denylist (hard anti-resurrection constraint) + redact source data + delete edges.

        Historical values inside events.payload expire naturally via the events
        180-day retention and are not rewritten here (preserving HMAC plaintext
        integrity); the denylist guarantees no edges are produced even if a backfill
        scan sees them. Returns the normalized ID ('' for invalid input, in which
        case nothing is done).
        """
        sid = normalize_steam_id(steam_user_id)
        if not sid:
            return ""
        with self._transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO steam_id_denylist (steam_user_id) VALUES (?)", (sid,)
            )
            conn.execute("DELETE FROM device_steam_edges WHERE steam_user_id = ?", (sid,))
            conn.execute("UPDATE devices SET steam_user_id = '' WHERE steam_user_id = ?", (sid,))
        return sid

    def build_edges_from_events(self, batch_limit: int = 5000) -> int:
        """Incrementally build edges from events (device⟷steam + device⟷device aliases).

        First run = full backfill (cursor starts at 0); afterwards incremental by the
        events.id cursor. Edge timestamps use the event observation time
        events.received_at, never wall-clock time (otherwise a backfill would stamp
        all historical edges with the backfill moment, distorting retention
        metrics). steam_user_id reuses normalize_steam_id + denylist filtering, so
        all edge-producing paths are consistent. Returns the number of events
        processed this run.
        """
        conn = self._get_conn()
        row = conn.execute("SELECT last_event_id FROM edge_build_cursor WHERE id = 1").fetchone()
        last_id = row["last_event_id"] if row else 0
        events = conn.execute(
            "SELECT id, device_id, payload, received_at FROM events WHERE id > ? ORDER BY id LIMIT ?",
            (last_id, batch_limit),
        ).fetchall()
        if not events:
            return 0
        max_id = last_id
        processed = 0
        with self._transaction() as c:
            for ev in events:
                max_id = ev["id"]
                processed += 1
                dev = ev["device_id"]
                ts = ev["received_at"]
                if not isinstance(dev, str) or not dev:
                    continue
                try:
                    payload = json.loads(ev["payload"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                sid = normalize_steam_id(payload.get("steam_user_id"))
                if sid:
                    # Check denylists atomically inside INSERT using WHERE NOT EXISTS, not snapshots;
                    # serialized SQLite writers see committed concurrent deletions,
                    # preventing snapshot-read/account-delete/reinsert races.
                    # WHERE also disambiguates INSERT...SELECT versus ON CONFLICT parsing.
                    c.execute("""
                        INSERT INTO device_steam_edges (device_id, steam_user_id, first_seen, last_seen, observe_count)
                        SELECT ?, ?, ?, ?, 1
                        WHERE NOT EXISTS (SELECT 1 FROM steam_id_denylist WHERE steam_user_id = ?)
                        ON CONFLICT(device_id, steam_user_id) DO UPDATE SET
                            first_seen = COALESCE(MIN(device_steam_edges.first_seen, excluded.first_seen), device_steam_edges.first_seen, excluded.first_seen),
                            last_seen  = COALESCE(MAX(device_steam_edges.last_seen,  excluded.last_seen),  device_steam_edges.last_seen,  excluded.last_seen),
                            observe_count = device_steam_edges.observe_count + 1
                    """, (dev, sid, ts, ts, sid))
                legacy = payload.get("device_id_legacy")
                # device_id_legacy comes from payload extras outside Pydantic because models omit
                # it; require device_id's same 16..128 length constraints,
                # or forged huge aliases enlarge edge tables and feed oversized
                # union-find nodes into recompute, exhausting memory/CPU.
                if isinstance(legacy, str) and 16 <= len(legacy) <= 128 and legacy != dev:
                    lo, hi = sorted((dev, legacy))
                    c.execute("""
                        INSERT INTO device_alias_edges (dev_lo, dev_hi, first_seen, last_seen, observe_count)
                        VALUES (?, ?, ?, ?, 1)
                        ON CONFLICT(dev_lo, dev_hi) DO UPDATE SET
                            first_seen = COALESCE(MIN(device_alias_edges.first_seen, excluded.first_seen), device_alias_edges.first_seen, excluded.first_seen),
                            last_seen  = COALESCE(MAX(device_alias_edges.last_seen,  excluded.last_seen),  device_alias_edges.last_seen,  excluded.last_seen),
                            observe_count = device_alias_edges.observe_count + 1
                    """, (lo, hi, ts, ts))
            c.execute("UPDATE edge_build_cursor SET last_event_id = ? WHERE id = 1", (max_id,))
        return processed

    def build_all_pending_edges(self, batch_limit: int = 5000) -> int:
        """Drain in a loop until the cursor catches up, so a single call never eats just one page while the cursor trails ingest forever.

        A full page (processed == batch_limit) means there is more, keep going; a
        partial page means caught up. Returns the total processed.
        """
        total = 0
        while True:
            n = self.build_edges_from_events(batch_limit=batch_limit)
            total += n
            if n < batch_limit:
                break
        return total

    def recompute_canonical(self) -> int:
        """Run union-find over device⟷steam + device⟷device edges and persist canonical_map.

        canonical_id representative rule (deterministic, stable across recomputes):
        nodes get a namespace prefix (steam=``s:``, device=``d:``); the
        representative = the smallest steam node in the component, degrading to the
        smallest device node when there is no steam node. Merge churn is written to
        canonical_alias and path-compressed to stay one hop. Returns the canonical
        count.
        """
        conn = self._get_conn()
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            root = x
            while parent.get(root, root) != root:
                root = parent[root]
            # path halving
            while parent.get(x, x) != root:
                parent[x], x = root, parent[x]
            parent.setdefault(root, root)
            return root

        def union(a: str, b: str) -> None:
            parent.setdefault(a, a)
            parent.setdefault(b, b)
            ra, rb = find(a), find(b)
            if ra != rb:
                # Lexically smaller roots yield stable representatives aligned with selection rules.
                lo, hi = sorted((ra, rb))
                parent[hi] = lo

        for r in conn.execute("SELECT device_id, steam_user_id FROM device_steam_edges"):
            union("d:" + r["device_id"], "s:" + r["steam_user_id"])
        for r in conn.execute("SELECT dev_lo, dev_hi FROM device_alias_edges"):
            union("d:" + r["dev_lo"], "d:" + r["dev_hi"])
        # Include every device; edgeless devices become their own canonical identities for complete metric coverage.
        for r in conn.execute("SELECT device_id FROM devices"):
            parent.setdefault("d:" + r["device_id"], "d:" + r["device_id"])

        # Per component choose minimum Steam node, otherwise minimum device node.
        root_steam: dict[str, str] = {}
        root_device: dict[str, str] = {}
        for node in list(parent.keys()):
            root = find(node)
            if node.startswith("s:"):
                if root not in root_steam or node < root_steam[root]:
                    root_steam[root] = node
            else:
                if root not in root_device or node < root_device[root]:
                    root_device[root] = node
        root_canon = {
            root: root_steam.get(root) or root_device.get(root)
            for root in set(list(root_steam) + list(root_device))
        }
        new_map = {node: root_canon[find(node)] for node in parent}

        old_canon = {
            r["canonical_id"] for r in conn.execute("SELECT DISTINCT canonical_id FROM canonical_map")
        }
        existing_alias_olds = {
            r["old_canonical_id"] for r in conn.execute("SELECT old_canonical_id FROM canonical_alias")
        }
        new_canon_ids = set(root_canon.values())

        with self._transaction() as c:
            c.execute("DELETE FROM canonical_map")
            c.executemany(
                "INSERT INTO canonical_map (entity_type, entity_id, canonical_id) VALUES (?, ?, ?)",
                [
                    ("steam" if node.startswith("s:") else "device", node[2:], canon)
                    for node, canon in new_map.items()
                ],
            )
            # Alias reconciliation re-resolves every previously exposed canonical_id and existing
            # alias key to its live representative. Candidates cover all old IDs callers could
            # pass to resolve_canonical, since only former canonical IDs escape externally.
            #   Nonempty succ different from self: merge into succ and write/update old->succ;
            #     succ is live, naturally keeping aliases one-hop without chains.
            #   succ == self: live canonical again, e.g. device resurrection after deletion splits components;
            #     remove stale outgoing aliases and resolve to self.
            #   succ=None: deletion removed the entity from the graph, leaving no successor;
            #     remove aliases, resolve to self, and return empty canonical_map membership, matching GDPR identity removal.
            # This also repairs incoming aliases pointing to deleted canonicals: candidate keys
            # redirect to current live representatives or disappear, never hanging off dead nodes.
            for old_id in old_canon | existing_alias_olds:
                succ = new_map.get(old_id)
                if succ is not None and succ != old_id:
                    c.execute(
                        "INSERT INTO canonical_alias (old_canonical_id, new_canonical_id) VALUES (?, ?) "
                        "ON CONFLICT(old_canonical_id) DO UPDATE SET new_canonical_id = excluded.new_canonical_id",
                        (old_id, succ),
                    )
                else:
                    c.execute(
                        "DELETE FROM canonical_alias WHERE old_canonical_id = ?", (old_id,)
                    )
        return len(new_canon_ids)

    def resolve_canonical(self, canonical_id: str) -> str:
        """Resolve an old canonical_id to the current one along the alias chain (one hop, guaranteed by path compression)."""
        row = self._get_conn().execute(
            "SELECT new_canonical_id FROM canonical_alias WHERE old_canonical_id = ?", (canonical_id,)
        ).fetchone()
        return row["new_canonical_id"] if row else canonical_id

    def get_canonical_metrics(self, days: int = 30) -> dict:
        """Canonical-basis DAU/WAU/MAU/retention (deduped per real person, coexisting with the device basis).

        device → canonical is mapped via canonical_map; devices not yet in the table
        fall back to 'd:'||device_id (equivalent to being their own canonical),
        so this never crashes when recompute hasn't run — it just doesn't dedup.
        """
        conn = self._get_conn()
        today = date.today()
        # Shared device-to-canonical mapping fragment.
        J = ("LEFT JOIN canonical_map cm ON cm.entity_type='device' AND cm.entity_id=a.device_id")
        C = "COALESCE(cm.canonical_id, 'd:'||a.device_id)"

        def active_count(cutoff: str) -> int:
            return conn.execute(
                f"SELECT COUNT(DISTINCT {C}) AS cnt FROM daily_aggregates a {J} "
                "WHERE a.model='_total' AND a.call_type='_total' AND a.stat_date >= ?",
                (cutoff,),
            ).fetchone()["cnt"]

        cutoff = (today - timedelta(days=days)).isoformat()
        dau_rows = conn.execute(
            f"SELECT a.stat_date, COUNT(DISTINCT {C}) AS dau FROM daily_aggregates a {J} "
            "WHERE a.model='_total' AND a.call_type='_total' AND a.stat_date >= ? "
            "GROUP BY a.stat_date ORDER BY a.stat_date DESC",
            (cutoff,),
        ).fetchall()
        dau_trend = {r["stat_date"]: r["dau"] for r in dau_rows}

        wau = active_count((today - timedelta(days=7)).isoformat())
        mau = active_count((today - timedelta(days=30)).isoformat())

        # Canonical first_seen is the earliest member-device first_seen.
        canon_first_cte = (
            "WITH dc AS ("
            "  SELECT COALESCE(cm.canonical_id, 'd:'||d.device_id) AS canon, d.first_seen "
            "  FROM devices d LEFT JOIN canonical_map cm ON cm.entity_type='device' AND cm.entity_id=d.device_id"
            "), canon_first AS (SELECT canon, MIN(date(first_seen)) AS join_date FROM dc GROUP BY canon)"
        )

        def retention(anchor: str, check: str) -> float:
            cohort = conn.execute(
                canon_first_cte + " SELECT COUNT(*) AS cnt FROM canon_first WHERE join_date = ?",
                (anchor,),
            ).fetchone()["cnt"]
            if cohort == 0:
                return 0.0
            retained = conn.execute(
                canon_first_cte
                + f" SELECT COUNT(DISTINCT {C}) AS cnt FROM daily_aggregates a {J} "
                "JOIN canon_first cf ON cf.canon = COALESCE(cm.canonical_id, 'd:'||a.device_id) "
                "WHERE cf.join_date = ? AND a.stat_date = ? AND a.model='_total' AND a.call_type='_total'",
                (anchor, check),
            ).fetchone()["cnt"]
            return round(retained / cohort * 100, 1)

        d1 = retention((today - timedelta(days=2)).isoformat(), (today - timedelta(days=1)).isoformat())
        d7 = retention((today - timedelta(days=8)).isoformat(), (today - timedelta(days=1)).isoformat())

        new_rows = conn.execute(
            canon_first_cte
            + " SELECT join_date, COUNT(*) AS cnt FROM canon_first WHERE join_date >= ? GROUP BY join_date ORDER BY join_date DESC",
            (cutoff,),
        ).fetchall()

        return {
            "canonical_dau_today": dau_trend.get(today.isoformat(), 0),
            "canonical_wau": wau,
            "canonical_mau": mau,
            "canonical_d1_retention": d1,
            "canonical_d7_retention": d7,
            "canonical_dau_trend": dau_trend,
            "canonical_new_trend": {r["join_date"]: r["cnt"] for r in new_rows},
            "total_canonical": conn.execute(
                "SELECT COUNT(DISTINCT COALESCE(cm.canonical_id, 'd:'||d.device_id)) AS cnt "
                "FROM devices d "
                "LEFT JOIN canonical_map cm ON cm.entity_type='device' AND cm.entity_id=d.device_id"
            ).fetchone()["cnt"],
        }

    # -- Maintenance --

    def prune_old_events(self, max_days: int = 180) -> int:
        cutoff = (date.today() - timedelta(days=max_days)).isoformat()
        with self._transaction() as conn:
            result = conn.execute("DELETE FROM events WHERE event_date < ?", (cutoff,))
            conn.execute("DELETE FROM seen_batches WHERE received_at < ?", (cutoff,))
            return result.rowcount

    def prune_old_instruments(self, max_days: int = 180) -> int:
        """Prune expired instrument_counters / instrument_histograms rows.

        These two tables accumulate by (device_id, stat_date, metric_key) and would
        grow forever without pruning. Aligned with the events table's 180-day
        retention. stat_date is a ``YYYY-MM-DD`` string (client local calendar day),
        directly comparable lexicographically.

        Returns the total number of rows deleted across the two tables.
        """
        cutoff = (date.today() - timedelta(days=max_days)).isoformat()
        with self._transaction() as conn:
            r1 = conn.execute(
                "DELETE FROM instrument_counters WHERE stat_date < ?", (cutoff,)
            )
            r2 = conn.execute(
                "DELETE FROM instrument_histograms WHERE stat_date < ?", (cutoff,)
            )
            return r1.rowcount + r2.rowcount

    def vacuum(self):
        self._get_conn().execute("VACUUM")
