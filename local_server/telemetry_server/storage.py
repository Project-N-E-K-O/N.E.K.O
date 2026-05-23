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
    """近似分位数：找累计样本数首次 >= q*total 的桶，返回该桶上界。

    Args:
        buckets: 桶 count 列表，最后一个是溢出桶（>最右 bound）
        bounds: 桶上界列表，len(bounds) == len(buckets) - 1
        q: 分位数（0~1）

    Returns:
        {"upper_bound": 数值或 None, "bucket_index": int}
        upper_bound None 表示落在溢出桶（无上界）。
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
    """把整数计数语义的值归一化成 int；非整数 / NaN / Inf / bool 返回 None。

    histogram 的 count 和 bucket 必须是整数计数。接受 int 或整数值 float
    （如 ``4.0``，跨序列化器容差），拒 ``4.5`` / NaN / Inf / True/False。
    归一化后再做 sum==count 校验和写库，避免 ``int()`` 截断造成不一致。
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
    """把任意来源的 steam_user_id 归一化为 canonical 十进制 Steam64，非法返回 ''。

    单一事实源：ingest（server.py）和 canonical 边构建（扫 events.payload）共用
    这一份，避免两条写路径规则漂移把同一身份拆成两个节点。

    raw 不标注 str：边构建扫的是原始 events.payload JSON，伪造 / 异常行的
    steam_user_id 可能是 number / null / 其它类型，直接 .isdigit() 会抛异常。
    先做类型守卫再走字符串规则。ingest 侧传进来的是 Pydantic str，守卫对它无害。

    规则（与历史 ingest 一致）：纯数字 + 长度 <= 20（u64 十进制 20 位，cheap
    pre-check 挡超长串 DoS）+ 0 < int < 2^64（排除 '0'/'00' 哨兵和超界值）+
    str(int()) 去前导零（否则 '00076561...' 会和 '76561...' 被当成两个账号）。
    """
    if not isinstance(raw, str):
        return ""
    # 必须 isascii：str.isdigit() 对 Unicode 数字（如上标 '²'、阿拉伯-印度数字）
    # 返回 True，但 int('²') 抛 ValueError。不挡的话 ingest 会 500、且 build_edges
    # 在事务里抛异常回滚、游标不前进 —— 一条伪造事件就能永久卡死 canonical 重建。
    if not raw.isascii() or not raw.isdigit() or len(raw) > 20:
        return ""
    value = int(raw)
    if 0 < value < (1 << 64):
        return str(value)
    return ""


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
            # 关掉 Python 的隐式事务管理，改由 _transaction 手动 BEGIN IMMEDIATE。
            # 默认 deferred 事务在 WAL 下读不取写锁，histogram 的 read-merge-write
            # 中间会被并发写者插入 → 丢更新。
            conn.isolation_level = None
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def _transaction(self):
        conn = self._get_conn()
        # BEGIN IMMEDIATE：事务一开始就取 SQLite 写锁，让 store_event 里
        # histogram 的"读旧 buckets → Python 合并 → 写回"对并发写者（多 worker /
        # 多进程，各自独立连接，甚至同一 device 的 main/agent/memory 三进程同时
        # POST）串行化。否则两个请求都读到同一份旧 buckets、各自合并、后写者
        # 覆盖前写者，bucket 分布与 count/sum 漂移（Codex P1）。busy_timeout=10000
        # 让并发写者阻塞等待而非立刻 "database is locked"。
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
            # 老库 devices 表上线时还没有 branch/locale/timezone/distribution/steam_user_id
            # 列。CREATE TABLE IF NOT EXISTS 不会动已存在的 schema，所以这里显式
            # 补列；ALTER ADD COLUMN 在 SQLite 上是 O(1)，已有行的列值用 DEFAULT 填。
            # try/except 是必要的：多进程部署（gunicorn workers / 多副本）首次
            # 启动时会同时跑迁移，PRAGMA + ALTER 不是原子的，一个 worker ALTER
            # 成功后第二个 worker 仍按陈旧的 PRAGMA 结果尝试 ALTER，会撞
            # "duplicate column name"。捕获并忽略让迁移在并发下幂等。
            existing_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(devices)").fetchall()
            }
            # (列名, 默认 sentinel) —— 分类字段缺失为 'unknown'，
            # steam_user_id 是 ID 字段缺失为空 string，server 端 UPSERT 用对应
            # sentinel 做 preserve-known 判断。
            _new_cols = (
                ("branch", "unknown"),
                ("locale", "unknown"),
                ("timezone", "unknown"),
                ("distribution", "unknown"),
                ("steam_user_id", ""),
            )
            for col_name, default in _new_cols:
                if col_name in existing_cols:
                    continue
                try:
                    conn.execute(
                        f"ALTER TABLE devices ADD COLUMN {col_name} TEXT NOT NULL DEFAULT '{default}'"
                    )
                except sqlite3.OperationalError as e:
                    # 只吞 "duplicate column name"，其它 schema 错误照常往上抛。
                    if "duplicate column name" not in str(e).lower():
                        raise
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
                    daily_stats: dict, batch_id: str | None = None,
                    branch: str = "unknown", locale: str = "unknown",
                    timezone: str = "unknown", distribution: str = "unknown",
                    steam_user_id: str = "",
                    instruments: dict | None = None):
        today = date.today().isoformat()
        with self._transaction() as conn:
            # denylist 收口：删号后该 Steam64 不得经任何上报写回 devices 列。
            # 在事务内判定（写串行，看得到已提交的 denylist），把它折叠成空串。
            if steam_user_id and conn.execute(
                "SELECT 1 FROM steam_id_denylist WHERE steam_user_id = ?", (steam_user_id,)
            ).fetchone():
                steam_user_id = ""
            if batch_id:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO seen_batches (batch_id) VALUES (?)",
                    (batch_id,),
                )
                # 真幂等闸：rowcount==0 = batch_id 已存在（server.py 预检与本次
                # 写入之间的 TOCTOU / 并发重试）。配合 _transaction 的 BEGIN
                # IMMEDIATE 写锁，此检查与下游写入原子——第二个并发请求在这里
                # 短路，不再继续写 events / daily_aggregates / instrument_*，
                # 避免双写双计（CodeRabbit）。
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
            # branch 在客户端首次启动后落盘并保持稳定，理论上同一 device 只该
            # 看到一个非 unknown 值；非 unknown 时直接覆写（清盘重抽时也只会是
            # 新真值）。locale / timezone / distribution 每次取实时值，同样仅当
            # 非 unknown 才覆写 —— 老客户端没带这些字段时 Pydantic 默认 'unknown'，
            # 或新客户端临时检测失败（例如 tzlocal 抛错）时，都不应该把上一次
            # 已知的好值抹成 'unknown'。
            # branch/locale/timezone/distribution 缺失 sentinel 是 'unknown'，
            # steam_user_id 是空 string（ID 字段不该用 'unknown' 占位 —— 它会
            # 被下游 join 当成合法 ID）。两种 sentinel 都走 preserve-known：
            # incoming 是 sentinel 时不覆写历史。
            conn.execute("""
                INSERT INTO devices (device_id, app_version, branch, locale, timezone, distribution, steam_user_id,
                                     first_seen, last_seen, event_count)
                VALUES (?, ?, ?, ?, ?, ?, ?,
                        strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours'),
                        strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours'), 1)
                ON CONFLICT(device_id) DO UPDATE SET
                    app_version   = excluded.app_version,
                    branch        = CASE WHEN excluded.branch        = 'unknown' THEN devices.branch        ELSE excluded.branch        END,
                    locale        = CASE WHEN excluded.locale        = 'unknown' THEN devices.locale        ELSE excluded.locale        END,
                    timezone      = CASE WHEN excluded.timezone      = 'unknown' THEN devices.timezone      ELSE excluded.timezone      END,
                    distribution  = CASE WHEN excluded.distribution  = 'unknown' THEN devices.distribution  ELSE excluded.distribution  END,
                    steam_user_id = CASE WHEN excluded.steam_user_id = ''        THEN devices.steam_user_id ELSE excluded.steam_user_id END,
                    last_seen = strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours'),
                    event_count = event_count + 1
            """, (device_id, app_version, branch, locale, timezone, distribution, steam_user_id))

            # Instrument 累加（同事务内）：失败回滚整批，daily_stats 不会
            # 在 instruments 失败时半截入库。
            if instruments:
                self._apply_instruments(conn, device_id, instruments, fallback_stat_date=today)

    def store_instruments(self, device_id: str, instruments: dict | None,
                          fallback_stat_date: str | None = None) -> None:
        """累加 instrument snapshot 的独立入口（自己开 transaction）。

        通常由 store_event 在自己的事务里调 _apply_instruments；此入口给
        独立测试 / 离线导入 / 跨表回填等场景用。
        """
        if not instruments:
            return
        with self._transaction() as conn:
            self._apply_instruments(conn, device_id, instruments, fallback_stat_date)

    def _apply_instruments(self, conn, device_id: str, instruments: dict | None,
                           fallback_stat_date: str | None = None) -> None:
        """累加 instrument snapshot（counter + histogram）。须在已开 transaction 的 conn 上调用。

        Args:
            conn: 调用方持有的事务连接
            device_id: 上报设备
            instruments: 客户端 utils/instrument.Instrument.snapshot() 的产物，
                结构: {window_start, window_end, bounds, counters, histograms}
            fallback_stat_date: 若 instruments 不带时间窗口或解析失败，用此值
                作 stat_date（一般传 'today'）。

        失败处理：单条 metric 解析失败不影响其它，整段失败抛 sqlite3 异常
        由调用方 transaction 回滚。
        """
        if not instruments or not isinstance(instruments, dict):
            return

        # stat_date 选取优先级（CodeRabbit 反馈正解）：
        #   1) 客户端 snapshot 里的 ``stat_date``（客户端本地日历天） —
        #      跟客户端的 ``daily_stats`` key 完全同口径，跨时区设备
        #      在午夜附近上报不会被服务端本地时区误拆到两天。
        #   2) window_end 时间戳按服务端时区落天 — 老客户端兼容回退。
        #   3) fallback_stat_date（一般是 today.isoformat()）— 兜底。
        # 兜底：fallback_stat_date（一般 today）。下面按优先级求一个 candidate
        # 日期，但**两条来源都必须过同一个 recency 校验**才采用。
        today = date.today()
        stat_date = today.isoformat()
        # recency anchor：在线实时上报锚到今天；但 store_instruments 文档承诺支持
        # 离线导入 / 跨表回填——调用方显式传了 fallback_stat_date 时，就拿它当
        # anchor 校验历史样本，否则合法的历史快照会被压回今天打歪导入分区。
        # live 路径传的 fallback 就是 today，行为不变。
        # **只有解析成功才把 fallback 回填进 stat_date** —— 否则像 "foo" 这种
        # 无效 fallback 会被原样落库污染 retention 分区（CodeRabbit）。
        recency_anchor = today
        if fallback_stat_date:
            try:
                recency_anchor = date.fromisoformat(fallback_stat_date)
                stat_date = recency_anchor.isoformat()
            except ValueError:
                recency_anchor = today
        candidate: date | None = None

        # 来源 1：客户端 snapshot 的 stat_date（客户端本地日历天，跟 daily_stats
        # 同口径）。HMAC 密钥在开源客户端可读，伪造 payload 可塞
        # "9999-99-99"/"abcd-ef-gh"（解析失败）或 "9999-12-31"（能解析但越界）。
        client_date = instruments.get("stat_date")
        if isinstance(client_date, str) and len(client_date) == 10:
            try:
                candidate = date.fromisoformat(client_date)
            except ValueError:
                candidate = None

        # 来源 2：window_end 时间戳按服务端时区落天（老客户端 / client_date 缺失
        # 时回退）。同样要过下面的 recency 校验——偏斜时钟或伪造的 window_end
        # 也能造远期 stat_date 逃过 retention（Codex）。
        if candidate is None:
            window_end = instruments.get("window_end")
            if isinstance(window_end, (int, float)) and window_end > 0:
                try:
                    from datetime import datetime as _dt
                    candidate = _dt.fromtimestamp(window_end).date()
                except (TypeError, ValueError, OSError, OverflowError):
                    candidate = None

        # 统一 recency 校验：instrument 是实时窗口（snapshot clear-on-read，不从
        # unsent 队列补发历史），stat_date 必≈服务端今天。客户端时区最多 ±14h +
        # 时钟偏差，放宽到 ±2 天。越界（伪造未来日期 / 时钟错乱）一律走 fallback，
        # 否则字典序 retention（stat_date < cutoff）永远 prune 不掉、污染 dashboard。
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
            # 拒 NaN / Inf：SQLite REAL NOT NULL 收到 NaN 会被 Python binding
            # 映射成 NULL → IntegrityError → 整个 store_event 事务回滚，连带
            # 合法的 daily_stats 一起丢。跳过单个坏样本而非炸整批（Codex P2）。
            if not math.isfinite(value):
                continue
            if len(metric_key) > 256:
                # 防御：客户端误传超长 key。截断不合并 —— 截断后可能跟
                # 已有 key 冲突 UPSERT，污染数据。直接丢。
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
            # count 和 bucket 是**整数计数**语义：归一化成 int（接受 int 或
            # 整数值 float 如 4.0），非整数值 / NaN / Inf / bool 一律 reject。
            # 否则后面写库 int(count) 截断会让 count 与 buckets 落库后不一致
            # （如 count=0.5, buckets=[0.5] 能过 sum==count 浮点校验，但写库
            # int(0.5)=0 而 buckets 存 [0.5]，dashboard 分位数算错）（CodeRabbit）。
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
            # hsum 是观测值之和，可以是小数（如延迟求和），只要 finite 非 bool。
            if isinstance(hsum, bool) or not isinstance(hsum, (int, float)) or not math.isfinite(hsum):
                continue
            # 形状自洽校验（防伪造 / 损坏 payload 把 dashboard 静默带歪）：
            # get_histogram_summary 的 avg 用 count、p50/p95 用 buckets，count 与
            # buckets 不一致就会让两个数字打架。要求 count 非负、bucket 非负、
            # bounds 长度匹配（len==buckets-1，溢出桶）、且 sum(buckets)==count。
            if count < 0 or any(b < 0 for b in buckets):
                continue
            if bounds and len(bounds) != len(buckets) - 1:
                continue
            if sum(buckets) != count:
                continue

            # SQL 不擅长 array element-wise 加；读出现有 buckets，
            # Python 端合并，再写回。同一 transaction 内保证一致性。
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
                        # 长度不一致时取 max 并 zero-pad —— 给客户端
                        # 改桶定义留缓冲（迁移期混合数据不爆炸）。
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

    # ----- Instrument 查询 -----

    def get_top_counters(self, days: int = 7, limit: int = 50) -> list[dict]:
        """跨设备汇总最近 N 天的 counter 总量，按总量降序。

        返回 [{"metric_key": ..., "total": ..., "devices": ...}]。
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
        """跨设备汇总最近 N 天的 histogram。

        返回 [{"metric_key", "count", "sum", "avg", "p50_bucket", "p95_bucket", "bounds"}]。
        p50/p95 桶用累积桶 + 桶数除以总样本数定位，精度受桶粒度限制 —— 用于
        监控趋势够用，要精确分位数请查原始 events 表。
        """
        conn = self._get_conn()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        # 同一 metric_key 不同设备的 buckets 合并：拿出原始行后 Python 端 sum。
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
            # 长度对齐合并（同 _apply_instruments 的策略）
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
                    # bounds 列损坏（极少见）：保留 slot["bounds"] = None，
                    # 下个有效行有机会再设；查询端 p50/p95 计算允许 bounds 为空。
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

    # ----- canonical identity 身份聚合 -----

    def add_steam_id_to_denylist(self, steam_user_id: str) -> str:
        """删号：被删 Steam64 进 denylist（防复活硬约束）+ 脱敏源数据 + 删边。

        events.payload 里的历史值按 events 180 天 retention 自然过期，不在此处
        改写（保留 HMAC 原文完整性）；denylist 保证它即便被回填扫到也不产边。
        返回归一化后的 ID（非法输入返回 ''，不做任何操作）。
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
        """扫 events 增量产边（device⟷steam + device⟷device 别名）。

        首跑 = 全量回填（游标从 0 起），之后按 events.id 游标增量。边时间戳用
        事件观测时间 events.received_at，绝不用墙上时间（否则回填把历史边全盖成
        回填时刻，留存指标失真）。steam_user_id 复用 normalize_steam_id + denylist
        过滤，所有产边路径一致。返回本次处理的事件数。
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
                    # denylist 用 WHERE NOT EXISTS 在 INSERT 内原子判定，而非读快照：
                    # SQLite 写事务串行，若并发删号已提交，这里的子查询能看到，
                    # 杜绝"读快照后删号→本批仍把被删 ID 插回复活"的竞态。
                    # WHERE 子句同时消除 INSERT...SELECT 与 ON CONFLICT 的解析歧义。
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
                # device_id_legacy 来自未经 Pydantic 校验的 payload extras（model 不声明
                # 此字段），必须套用与 device_id 相同的长度约束（models.py: min 16 / max
                # 128），否则伪造请求可塞超长串进 device_alias_edges，撑大边表、把超大
                # 节点喂进 recompute 的 union-find 拖垮内存/CPU。
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
        """循环 drain 到游标追平再返回，避免单次只吃一页、游标永远落后于 ingest。

        每页满（processed == batch_limit）说明还有，继续；不满即追平。返回总处理数。
        """
        total = 0
        while True:
            n = self.build_edges_from_events(batch_limit=batch_limit)
            total += n
            if n < batch_limit:
                break
        return total

    def recompute_canonical(self) -> int:
        """对 device⟷steam + device⟷device 边跑 union-find，落表 canonical_map。

        canonical_id 代表元规则（确定性，重算不抖）：节点加命名空间前缀
        （steam=``s:``、device=``d:``）；代表元 = 分量内最小 steam 节点，无 steam
        退化为最小 device 节点。合并 churn 写 canonical_alias 并 path-compress
        保持一跳。返回 canonical 数。
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
                # 让字典序小的当根，稳定且与代表元规则方向一致
                lo, hi = sorted((ra, rb))
                parent[hi] = lo

        for r in conn.execute("SELECT device_id, steam_user_id FROM device_steam_edges"):
            union("d:" + r["device_id"], "s:" + r["steam_user_id"])
        for r in conn.execute("SELECT dev_lo, dev_hi FROM device_alias_edges"):
            union("d:" + r["dev_lo"], "d:" + r["dev_hi"])
        # 把所有 device 都纳入（无边的 device 自成一个 canonical，指标才覆盖全量）
        for r in conn.execute("SELECT device_id FROM devices"):
            parent.setdefault("d:" + r["device_id"], "d:" + r["device_id"])

        # 每个连通分量选代表元：min steam 节点 优先，否则 min device 节点
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
            # alias reconciliation：对每个"曾被发出去过的 canonical_id"（old_canon）和每条
            # 已有 alias 的 key，重新解析到它此刻所属的 live canonical。candidates 覆盖了所有
            # 可能被外部 resolve_canonical 调用的旧 ID（外部只可能持有曾经的 canonical_id）。
            #   succ 非空且 != 自己 → 被合并到 succ，写/更新 old→succ；succ 必是 live 代表元，
            #     天然一跳，不会形成 alias 链。
            #   succ == 自己       → 它又是 live canonical（如删号后分量炸开、旧 device 复活），
            #     删掉任何把它指走的陈旧 alias，resolve 回落到自身。
            #   succ 为 None       → 实体随删号离开图（steam 节点边被删光），旧引用无继承者，
            #     删 alias；resolve 回落自身、查 canonical_map 得空集，即 GDPR "身份已移除"。
            # 关键：这同时清理了"指向已删 canonical 的入边 alias"——它们的 key 在 candidates
            # 里会被重新指到当前 live canonical 或删除，不再悬空指向死节点。
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
        """顺 alias 链解析旧 canonical_id 到当前（一跳即到，path compression 保证）。"""
        row = self._get_conn().execute(
            "SELECT new_canonical_id FROM canonical_alias WHERE old_canonical_id = ?", (canonical_id,)
        ).fetchone()
        return row["new_canonical_id"] if row else canonical_id

    def get_canonical_metrics(self, days: int = 30) -> dict:
        """canonical 口径 DAU/WAU/MAU/留存（按真人去重，与 device 口径并存）。

        device → canonical 经 canonical_map 映射；未落表的 device 回退 'd:'||device_id
        （等价于自成 canonical），保证 recompute 没跑过时也不崩、只是不去重。
        """
        conn = self._get_conn()
        today = date.today()
        # device → canonical 的公共映射片段
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

        # canonical first_seen = 旗下最早 device 的 first_seen
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

    # ----- 维护 -----

    def prune_old_events(self, max_days: int = 180) -> int:
        cutoff = (date.today() - timedelta(days=max_days)).isoformat()
        with self._transaction() as conn:
            result = conn.execute("DELETE FROM events WHERE event_date < ?", (cutoff,))
            conn.execute("DELETE FROM seen_batches WHERE received_at < ?", (cutoff,))
            return result.rowcount

    def prune_old_instruments(self, max_days: int = 180) -> int:
        """清理超期的 instrument_counters / instrument_histograms 行。

        这两张表按 (device_id, stat_date, metric_key) 累加，不清理会随时间
        无限增长。对齐 events 表的 180 天 retention。stat_date 是
        ``YYYY-MM-DD`` 字符串（客户端本地日历天），可直接字典序比较。

        返回两张表删除的总行数。
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
