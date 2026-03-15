# -*- coding: utf-8 -*-
"""
Telemetry Server — SQLite 存储

设计：
- events 表：append-only 原始事件日志（审计追踪，不可篡改）
- daily_aggregates 表：预聚合统计（UPSERT 累加）
- devices 表：设备活跃度追踪
- WAL 模式：写不阻塞读

容量评估（20k DAU）：
- 3 进程/设备 × 6 req/h × 8h ≈ 144 req/设备/天
- 20k × 144 ≈ 2.88M events/天（峰值 ~50 req/s）
- SQLite WAL 单线程写入 ~500 req/s，单实例足够
- events 表按 180 天清理，聚合表永久保留
"""
from __future__ import annotations

import csv
import io
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path


class TelemetryStorage:
    """线程安全的 SQLite 遥测存储。"""

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
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def _transaction(self):
        conn = self._get_conn()
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
                    received_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
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
                    updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    UNIQUE(device_id, stat_date, model, call_type)
                );
                CREATE INDEX IF NOT EXISTS idx_agg_device ON daily_aggregates(device_id);
                CREATE INDEX IF NOT EXISTS idx_agg_date   ON daily_aggregates(stat_date);

                CREATE TABLE IF NOT EXISTS devices (
                    device_id    TEXT PRIMARY KEY,
                    app_version  TEXT    NOT NULL DEFAULT 'unknown',
                    first_seen   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    last_seen    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    event_count  INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS seen_batches (
                    batch_id    TEXT PRIMARY KEY,
                    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
            """)
            conn.commit()
            self._initialized = True

    # ----- 写入 -----

    def is_duplicate_batch(self, batch_id: str | None) -> bool:
        """检查 batch_id 是否已处理过。无 batch_id 时不做去重。"""
        if not batch_id:
            return False
        conn = self._get_conn()
        row = conn.execute("SELECT 1 FROM seen_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return row is not None

    def store_event(self, device_id: str, app_version: str, payload_json: str,
                    daily_stats: dict, batch_id: str | None = None):
        today = date.today().isoformat()
        with self._transaction() as conn:
            if batch_id:
                conn.execute(
                    "INSERT OR IGNORE INTO seen_batches (batch_id) VALUES (?)",
                    (batch_id,),
                )
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
            conn.execute("""
                INSERT INTO devices (device_id, app_version, first_seen, last_seen, event_count)
                VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 1)
                ON CONFLICT(device_id) DO UPDATE SET
                    app_version = excluded.app_version,
                    last_seen = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    event_count = event_count + 1
            """, (device_id, app_version))

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
                updated_at        = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """, (device_id, stat_date, model, call_type,
              prompt_tokens, completion_tokens, total_tokens, cached_tokens,
              call_count, error_count))

    # ----- 查询 -----

    def get_global_stats(self, days: int = 30) -> dict:
        conn = self._get_conn()
        cutoff = (date.today() - timedelta(days=days)).isoformat()

        meta = conn.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(event_count), 0) as total FROM devices"
        ).fetchone()

        # 按日汇总
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

        # 按模型汇总
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

        # 按调用类型
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
        """DAU / WAU / MAU / 新增 / 留存率。"""
        conn = self._get_conn()
        today = date.today()

        # --- 每日活跃设备数（DAU 趋势） ---
        cutoff = (today - timedelta(days=days)).isoformat()
        dau_rows = conn.execute("""
            SELECT stat_date, COUNT(DISTINCT device_id) as dau
            FROM daily_aggregates
            WHERE model = '_total' AND call_type = '_total' AND stat_date >= ?
            GROUP BY stat_date ORDER BY stat_date DESC
        """, (cutoff,)).fetchall()
        dau_trend = {r["stat_date"]: r["dau"] for r in dau_rows}

        # --- 今日 DAU ---
        today_str = today.isoformat()
        dau_today = dau_trend.get(today_str, 0)

        # --- 7 日活跃（WAU） ---
        wau_cutoff = (today - timedelta(days=7)).isoformat()
        wau = conn.execute("""
            SELECT COUNT(DISTINCT device_id) as cnt
            FROM daily_aggregates
            WHERE model = '_total' AND call_type = '_total' AND stat_date >= ?
        """, (wau_cutoff,)).fetchone()["cnt"]

        # --- 30 日活跃（MAU） ---
        mau_cutoff = (today - timedelta(days=30)).isoformat()
        mau = conn.execute("""
            SELECT COUNT(DISTINCT device_id) as cnt
            FROM daily_aggregates
            WHERE model = '_total' AND call_type = '_total' AND stat_date >= ?
        """, (mau_cutoff,)).fetchone()["cnt"]

        # --- 每日新增设备 ---
        new_rows = conn.execute("""
            SELECT DATE(first_seen) as join_date, COUNT(*) as cnt
            FROM devices
            WHERE DATE(first_seen) >= ?
            GROUP BY join_date ORDER BY join_date DESC
        """, (cutoff,)).fetchall()
        new_trend = {r["join_date"]: r["cnt"] for r in new_rows}

        # --- 次日留存率（昨天新增中今天还活跃的比例） ---
        yesterday = (today - timedelta(days=1)).isoformat()
        day_before = (today - timedelta(days=2)).isoformat()

        # 前天新增的设备
        cohort = conn.execute("""
            SELECT COUNT(*) as cnt FROM devices
            WHERE DATE(first_seen) = ?
        """, (day_before,)).fetchone()["cnt"]

        # 其中昨天还活跃的
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

        # --- 7 日留存率 ---
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

    # ----- 导出 -----

    def export_daily_csv(self, days: int = 90) -> str:
        """导出按日汇总的 CSV。"""
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
        """导出按模型汇总的 CSV。"""
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

    # ----- 维护 -----

    def prune_old_events(self, max_days: int = 180) -> int:
        cutoff = (date.today() - timedelta(days=max_days)).isoformat()
        with self._transaction() as conn:
            result = conn.execute("DELETE FROM events WHERE event_date < ?", (cutoff,))
            conn.execute("DELETE FROM seen_batches WHERE received_at < ?", (cutoff,))
            return result.rowcount

    def vacuum(self):
        self._get_conn().execute("VACUUM")
