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
Survey Server — SQLite storage

Design (deliberately simpler than telemetry: surveys are sparse, no aggregation):
- responses table: append-only one row per submission (audit trail)
- seen_batches table: batch_id idempotency (failed-retry dedupe)
- WAL mode: writes do not block reads

Capacity: a survey fires once per user per app version, so even 100k users is
~100k rows total — trivially within SQLite's reach.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


def _csv_safe(value) -> str:
    """Neutralize CSV formula injection: prefix a cell that starts with a formula char
    (= + - @) or any leading control char (< 0x20, covers TAB / CR / LF) with a single
    quote, so spreadsheet apps treat forged metadata (e.g. a signed-but-malicious
    device_id like ``=cmd|...`` or ``\\n=cmd``) as text rather than a formula when an
    admin opens the export.
    """
    s = "" if value is None else str(value)
    if s and (s[0] in ("=", "+", "-", "@") or ord(s[0]) < 0x20):
        return "'" + s
    return s


class SurveyStorage:
    def __init__(self, db_path: str):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
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
            conn.isolation_level = None
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def _transaction(self):
        conn = self._get_conn()
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
                CREATE TABLE IF NOT EXISTS responses (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    received_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')),
                    device_id        TEXT    NOT NULL,
                    device_id_legacy TEXT    NOT NULL DEFAULT '',
                    app_version    TEXT    NOT NULL DEFAULT 'unknown',
                    survey_version TEXT    NOT NULL DEFAULT 'unknown',
                    locale         TEXT    NOT NULL DEFAULT 'unknown',
                    branch         TEXT    NOT NULL DEFAULT 'unknown',
                    distribution   TEXT    NOT NULL DEFAULT 'unknown',
                    steam_user_id  TEXT    NOT NULL DEFAULT '',
                    action         TEXT    NOT NULL DEFAULT 'submit',
                    answers        TEXT    NOT NULL DEFAULT '{}',
                    batch_id       TEXT    NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_resp_survey ON responses(survey_version);
                CREATE INDEX IF NOT EXISTS idx_resp_device ON responses(device_id);

                CREATE TABLE IF NOT EXISTS seen_batches (
                    batch_id    TEXT PRIMARY KEY,
                    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours'))
                );
            """)
            self._initialized = True

    # ----------------------------------------------------------------- ingest

    def store_response(
        self,
        *,
        device_id: str,
        device_id_legacy: str,
        app_version: str,
        survey_version: str,
        locale: str,
        branch: str,
        distribution: str,
        steam_user_id: str,
        action: str,
        answers: dict,
        batch_id: str,
    ) -> bool:
        """Store one response. Returns False (idempotent skip) if the batch_id was already seen.

        Dedup is done **inside** the BEGIN IMMEDIATE transaction via
        ``INSERT OR IGNORE INTO seen_batches`` + rowcount, so the seen-check and the
        responses insert are atomic under one write lock. A separate read-then-write
        (the old is_duplicate_batch pre-check) left a TOCTOU window where two
        concurrent identical batch_ids could both pass the read and double-insert
        into responses. Empty batch_id (legacy clients) skips dedup and always stores.
        """
        answers_json = json.dumps(answers, ensure_ascii=False, sort_keys=True)
        with self._transaction() as conn:
            if batch_id:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO seen_batches (batch_id) VALUES (?)",
                    (batch_id,),
                )
                if cur.rowcount == 0:
                    return False  # 另一并发请求已写入同 batch，幂等跳过
            conn.execute(
                """INSERT INTO responses
                   (device_id, device_id_legacy, app_version, survey_version, locale,
                    branch, distribution, steam_user_id, action, answers, batch_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (device_id, device_id_legacy, app_version, survey_version, locale,
                 branch, distribution, steam_user_id, action, answers_json, batch_id),
            )
        return True

    # ------------------------------------------------------------------ admin

    def get_summary(self, survey_version: str = "") -> dict:
        """Per-survey funnel: submit / skip counts + unique devices."""
        conn = self._get_conn()
        where = ""
        params: tuple = ()
        if survey_version:
            where = "WHERE survey_version = ?"
            params = (survey_version,)
        rows = conn.execute(
            f"""SELECT survey_version,
                       SUM(CASE WHEN action='submit' THEN 1 ELSE 0 END) AS submits,
                       SUM(CASE WHEN action='skip'   THEN 1 ELSE 0 END) AS skips,
                       COUNT(DISTINCT device_id)                        AS unique_devices,
                       COUNT(*)                                         AS total
                FROM responses {where}
                GROUP BY survey_version
                ORDER BY survey_version DESC""",
            params,
        ).fetchall()
        return {"surveys": [dict(r) for r in rows]}

    def get_responses(self, survey_version: str = "", limit: int = 1000) -> list[dict]:
        conn = self._get_conn()
        where = "WHERE action = 'submit'"
        params: list = []
        if survey_version:
            where += " AND survey_version = ?"
            params.append(survey_version)
        # 上下界都 clamp：只夹上界的话 limit=-1 会被原样传给 SQLite，而 LIMIT -1
        # 表示"无限"，反而绕过 5 万行上限把整表读进内存/JSON。
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 1000
        params.append(min(max(limit, 1), 50000))
        rows = conn.execute(
            f"""SELECT received_at, device_id, device_id_legacy, app_version, survey_version,
                       locale, branch, distribution, steam_user_id, answers
                FROM responses {where}
                ORDER BY id DESC LIMIT ?""",
            params,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["answers"] = json.loads(d["answers"])
            except (TypeError, ValueError):
                d["answers"] = {}
            out.append(d)
        return out

    def export_responses_csv(self, survey_version: str = "", limit: int = 50000) -> str:
        """Flat CSV: one row per submission, answers JSON-encoded in one column.

        Per-question columns vary by survey, so we keep answers as a single JSON
        cell; the dashboard pivots it per the survey definition.
        """
        rows = self.get_responses(survey_version=survey_version, limit=limit)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "received_at", "device_id", "device_id_legacy", "app_version", "survey_version",
            "locale", "branch", "distribution", "steam_user_id", "answers",
        ])
        for r in rows:
            writer.writerow([
                _csv_safe(r.get("received_at", "")), _csv_safe(r.get("device_id", "")),
                _csv_safe(r.get("device_id_legacy", "")),
                _csv_safe(r.get("app_version", "")), _csv_safe(r.get("survey_version", "")),
                _csv_safe(r.get("locale", "")), _csv_safe(r.get("branch", "")),
                _csv_safe(r.get("distribution", "")), _csv_safe(r.get("steam_user_id", "")),
                _csv_safe(json.dumps(r.get("answers", {}), ensure_ascii=False, sort_keys=True)),
            ])
        return buf.getvalue()

    def prune_old_responses(self, max_days: int = 365) -> int:
        """Delete submissions older than max_days (kept long by default — surveys are cheap).

        Also garbage-collects seen_batches: any batch_id no longer backed by a
        responses row is dropped, so the idempotency table stays bounded to live
        responses instead of growing forever across versions. A retry of a batch
        that old is implausible (max_days >= 30), so re-acceptance risk is moot.
        """
        days = max(max_days, 30)
        with self._transaction() as conn:
            cur = conn.execute(
                "DELETE FROM responses "
                "WHERE received_at < strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours', ?)",
                (f"-{days} days",),
            )
            deleted = cur.rowcount
            conn.execute(
                "DELETE FROM seen_batches WHERE batch_id NOT IN "
                "(SELECT batch_id FROM responses WHERE batch_id != '')"
            )
            return deleted
