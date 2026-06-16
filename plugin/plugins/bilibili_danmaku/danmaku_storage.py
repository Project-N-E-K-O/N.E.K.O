"""
Bilibili 弹幕历史存储模块（SQLite）

对标 MagicalDanmaku 的 SQLite 架构，持久化弹幕、礼物、进场/关注、舰长记录。
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# -- 建表 SQL（对标 MagicalDanmaku tables）-----------------------------------

_CREATE_DANMU = """\
CREATE TABLE IF NOT EXISTS danmu (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL,
    uname TEXT NOT NULL,
    uid TEXT NOT NULL,
    msg TEXT NOT NULL,
    ulevel INTEGER,
    admin BOOLEAN,
    guard INTEGER,
    anchor_room_id TEXT,
    medal_name TEXT,
    medal_level INTEGER,
    medal_up TEXT,
    price INTEGER,
    create_time TIMESTAMP NOT NULL DEFAULT (datetime('now', 'localtime'))
)"""

_CREATE_GIFT = """\
CREATE TABLE IF NOT EXISTS gift (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL,
    uname TEXT NOT NULL,
    uid TEXT NOT NULL,
    gift_name TEXT NOT NULL,
    gift_id INTEGER NOT NULL DEFAULT 0,
    gift_type INTEGER,
    coin_type TEXT,
    total_coin INTEGER NOT NULL DEFAULT 0,
    number INTEGER NOT NULL DEFAULT 1,
    ulevel INTEGER,
    admin BOOLEAN,
    guard INTEGER,
    anchor_room_id TEXT,
    medal_name TEXT,
    medal_level INTEGER,
    medal_up TEXT,
    create_time TIMESTAMP NOT NULL DEFAULT (datetime('now', 'localtime'))
)"""

_CREATE_INTERACT = """\
CREATE TABLE IF NOT EXISTS interact (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL,
    uname TEXT NOT NULL,
    uid TEXT NOT NULL,
    msg_type INTEGER NOT NULL,
    admin BOOLEAN,
    guard INTEGER,
    anchor_room_id TEXT,
    medal_name TEXT,
    medal_level INTEGER,
    medal_up TEXT,
    special INTEGER,
    spread_desc TEXT,
    spread_info TEXT,
    create_time TIMESTAMP NOT NULL DEFAULT (datetime('now', 'localtime'))
)"""

_CREATE_GUARD = """\
CREATE TABLE IF NOT EXISTS guard (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL,
    uname TEXT NOT NULL,
    uid TEXT NOT NULL,
    gift_name TEXT NOT NULL,
    gift_id INTEGER NOT NULL DEFAULT 0,
    guard_level INTEGER NOT NULL DEFAULT 1,
    price INTEGER,
    number INTEGER DEFAULT 1,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    create_time TIMESTAMP NOT NULL DEFAULT (datetime('now', 'localtime'))
)"""

_ALL_TABLES = [
    ("danmu", _CREATE_DANMU),
    ("gift", _CREATE_GIFT),
    ("interact", _CREATE_INTERACT),
    ("guard", _CREATE_GUARD),
]


class DanmakuStorage:
    """弹幕数据 SQLite 存储服务"""

    def __init__(self, db_path: Path, logger=None):
        self._db_path = db_path
        self._logger = logger
        self._conn: Optional[sqlite3.Connection] = None
        self._write_lock = asyncio.Lock()
        self._pending_writes: int = 0
        self._all_done = asyncio.Event()
        self._all_done.set()

    def open(self):
        db_path_str = str(self._db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path_str, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_tables()
        if self._logger:
            self._logger.info(f"SQLite 存储已打开: {db_path_str}")

    async def close(self):
        """关闭连接前等待所有正在进行的写入完成"""
        if self._pending_writes > 0 and self._logger:
            self._logger.info(f"等待 {self._pending_writes} 个 SQLite 写入完成...")
        await self._all_done.wait()
        if self._conn:
            self._conn.close()
            self._conn = None
            if self._logger:
                self._logger.info("SQLite 存储已关闭")

    async def _execute_async(self, sql: str, params: tuple = ()):
        """串行化的异步写入（加锁 + execute+commit 原子）"""
        if not self._conn:
            return
        self._pending_writes += 1
        self._all_done.clear()
        try:
            async with self._write_lock:
                conn = self._conn
                if not conn:
                    return
                await asyncio.to_thread(conn.execute, sql, params)
                await asyncio.to_thread(conn.commit)
        except Exception as e:
            if self._logger:
                self._logger.error(f"SQLite 写入失败: {e} | SQL: {sql[:120]}")
        finally:
            self._pending_writes -= 1
            if self._pending_writes <= 0:
                self._all_done.set()

    # ── 建表 ──────────────────────────────────────────────────────

    def _init_tables(self):
        for name, sql in _ALL_TABLES:
            self._conn.execute(sql)
        self._conn.commit()

    # ── 通用执行 ───────────────────────────────────────────────────

    def _execute(self, sql: str, params: tuple = ()):
        if not self._conn:
            return
        try:
            self._conn.execute(sql, params)
            self._conn.commit()
        except Exception as e:
            if self._logger:
                self._logger.error(f"SQLite 写入失败: {e} | SQL: {sql[:120]}")

    # ── 插入方法 ──────────────────────────────────────────────────

    async def insert_danmaku(
        self,
        room_id: str,
        uid: str,
        uname: str,
        msg: str,
        *,
        ulevel: int = 0,
        admin: bool = False,
        guard: int = 0,
        anchor_room_id: str = "",
        medal_name: str = "",
        medal_level: int = 0,
        medal_up: str = "",
        price: int = 0,
    ):
        await self._execute_async(
            "INSERT INTO danmu(room_id, uname, uid, msg, ulevel, admin, guard, "
            "anchor_room_id, medal_name, medal_level, medal_up, price) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (room_id, uname, uid, msg, ulevel, int(admin), guard,
             anchor_room_id, medal_name, medal_level, medal_up, price),
        )

    async def insert_gift(
        self,
        room_id: str,
        uid: str,
        uname: str,
        gift_name: str,
        *,
        gift_id: int = 0,
        gift_type: int = 0,
        coin_type: str = "silver",
        total_coin: int = 0,
        number: int = 1,
        ulevel: int = 0,
        admin: bool = False,
        guard: int = 0,
        anchor_room_id: str = "",
        medal_name: str = "",
        medal_level: int = 0,
        medal_up: str = "",
    ):
        await self._execute_async(
            "INSERT INTO gift(room_id, uname, uid, gift_name, gift_id, gift_type, "
            "coin_type, total_coin, number, ulevel, admin, guard, "
            "anchor_room_id, medal_name, medal_level, medal_up) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (room_id, uname, uid, gift_name, gift_id, gift_type,
             coin_type, total_coin, number, ulevel, int(admin), guard,
             anchor_room_id, medal_name, medal_level, medal_up),
        )

    async def insert_interact(
        self,
        room_id: str,
        uid: str,
        uname: str,
        msg_type: int,
        *,
        admin: bool = False,
        guard: int = 0,
        anchor_room_id: str = "",
        medal_name: str = "",
        medal_level: int = 0,
        medal_up: str = "",
        special: int = 0,
        spread_desc: str = "",
        spread_info: str = "",
    ):
        await self._execute_async(
            "INSERT INTO interact(room_id, uname, uid, msg_type, admin, guard, "
            "anchor_room_id, medal_name, medal_level, medal_up, "
            "special, spread_desc, spread_info) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (room_id, uname, uid, msg_type, int(admin), guard,
             anchor_room_id, medal_name, medal_level, medal_up,
             special, spread_desc, spread_info),
        )

    async def insert_guard(
        self,
        room_id: str,
        uid: str,
        uname: str,
        gift_name: str,
        *,
        gift_id: int = 0,
        guard_level: int = 1,
        price: int = 0,
        number: int = 1,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ):
        await self._execute_async(
            "INSERT INTO guard(room_id, uname, uid, gift_name, gift_id, "
            "guard_level, price, number, start_time, end_time) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (room_id, uname, uid, gift_name, gift_id,
             guard_level, price, number, start_time or "", end_time or ""),
        )

    # ── 查询方法 ───────────────────────────────────────────────────

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        if not self._conn:
            return []
        try:
            cursor = self._conn.execute(sql, params)
            columns = [col[0] for col in cursor.description] if cursor.description else []
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            if self._logger:
                self._logger.error(f"SQLite 查询失败: {e}")
            return []

    async def query_async(self, sql: str, params: tuple = ()) -> list[dict]:
        return await asyncio.to_thread(self._query, sql, params)

    # ── 预置查询 ──────────────────────────────────────────────────

    async def query_danmaku_by_user(self, room_id: str, uid: str, limit: int = 500) -> list[dict]:
        """查询指定用户的弹幕历史"""
        return await self.query_async(
            "SELECT uid, uname, msg, ulevel, admin, guard, "
            "medal_name, medal_level, create_time "
            "FROM danmu WHERE room_id=? AND uid=? "
            "ORDER BY create_time DESC LIMIT ?",
            (room_id, uid, limit),
        )

    async def query_danmaku_by_keyword(self, room_id: str, keyword: str, limit: int = 500) -> list[dict]:
        """按关键词搜索弹幕"""
        return await self.query_async(
            "SELECT uid, uname, msg, ulevel, admin, guard, "
            "medal_name, medal_level, create_time "
            "FROM danmu WHERE room_id=? AND msg LIKE ? "
            "ORDER BY create_time DESC LIMIT ?",
            (room_id, f"%{keyword}%", limit),
        )

    async def query_danmaku_recent(self, room_id: str, limit: int = 200) -> list[dict]:
        """查询最近弹幕"""
        return await self.query_async(
            "SELECT uid, uname, msg, ulevel, admin, guard, "
            "medal_name, medal_level, create_time "
            "FROM danmu WHERE room_id=? "
            "ORDER BY create_time DESC LIMIT ?",
            (room_id, limit),
        )

    async def query_repeat_danmaku(self, room_id: str, min_count: int = 2, limit: int = 200) -> list[dict]:
        """统计重复弹幕"""
        return await self.query_async(
            "SELECT msg, count(*) as count "
            "FROM danmu WHERE room_id=? "
            "GROUP BY msg HAVING count >= ? "
            "ORDER BY count DESC LIMIT ?",
            (room_id, min_count, limit),
        )

    async def query_daily_danmaku_count(self, room_id: str, days: int = 30) -> list[dict]:
        """每日弹幕量统计"""
        return await self.query_async(
            "SELECT date(create_time) as day, count(*) as count "
            "FROM danmu WHERE room_id=? AND create_time >= date('now', ? || ' days') "
            "GROUP BY day ORDER BY day DESC",
            (room_id, f"-{days}"),
        )

    async def query_user_danmaku_ranking(self, room_id: str, limit: int = 50) -> list[dict]:
        """用户弹幕数量排行"""
        return await self.query_async(
            "SELECT uid, uname, count(*) as count, "
            "datetime(max(create_time)) as last_time, "
            "max(ulevel) as level, max(guard) as guard "
            "FROM danmu WHERE room_id=? "
            "GROUP BY uid ORDER BY count DESC LIMIT ?",
            (room_id, limit),
        )

    async def query_sc_history(self, room_id: str, limit: int = 200) -> list[dict]:
        """SC 历史"""
        return await self.query_async(
            "SELECT uid, uname, msg, price, create_time "
            "FROM danmu WHERE room_id=? AND price > 0 "
            "ORDER BY create_time DESC LIMIT ?",
            (room_id, limit),
        )

    async def query_gift_history(self, room_id: str, limit: int = 200) -> list[dict]:
        """礼物历史"""
        return await self.query_async(
            "SELECT uid, uname, gift_name, number, total_coin, coin_type, "
            "create_time FROM gift WHERE room_id=? "
            "ORDER BY create_time DESC LIMIT ?",
            (room_id, limit),
        )

    async def query_gift_revenue_summary(self, room_id: str) -> list[dict]:
        """用户送礼总额排行（金瓜子）"""
        return await self.query_async(
            "SELECT uid, uname, sum(total_coin)/1000.0 as rmb, "
            "count(*) as gift_count, "
            "datetime(min(create_time)) as first_time "
            "FROM gift WHERE room_id=? AND coin_type='gold' "
            "GROUP BY uid ORDER BY rmb DESC",
            (room_id,),
        )

    async def query_daily_gift_revenue(self, room_id: str, days: int = 30) -> list[dict]:
        """每日礼物流水"""
        return await self.query_async(
            "SELECT date(create_time) as day, "
            "sum(total_coin)/1000.0 as rmb "
            "FROM gift WHERE room_id=? AND coin_type='gold' "
            "AND create_time >= date('now', ? || ' days') "
            "GROUP BY day ORDER BY day DESC",
            (room_id, f"-{days}"),
        )

    async def query_entry_history(self, room_id: str, limit: int = 200) -> list[dict]:
        """进场记录"""
        return await self.query_async(
            "SELECT uid, uname, guard, medal_name, medal_level, "
            "create_time FROM interact WHERE room_id=? AND msg_type=1 "
            "ORDER BY create_time DESC LIMIT ?",
            (room_id, limit),
        )

    async def query_entry_ranking(self, room_id: str, limit: int = 100) -> list[dict]:
        """进场次数排行"""
        return await self.query_async(
            "SELECT uid, uname, count(*) as count, "
            "max(guard) as guard, "
            "datetime(max(create_time)) as last_time "
            "FROM interact WHERE room_id=? AND msg_type=1 "
            "GROUP BY uid ORDER BY count DESC LIMIT ?",
            (room_id, limit),
        )

    async def query_follow_history(self, room_id: str, limit: int = 200) -> list[dict]:
        """关注记录"""
        return await self.query_async(
            "SELECT uid, uname, create_time "
            "FROM interact WHERE room_id=? AND msg_type=2 "
            "ORDER BY create_time DESC LIMIT ?",
            (room_id, limit),
        )

    async def query_guard_history(self, room_id: str, limit: int = 100) -> list[dict]:
        """舰长记录"""
        return await self.query_async(
            "SELECT uid, uname, gift_name, guard_level, price, number, "
            "datetime(start_time) as start, datetime(end_time) as end, "
            "create_time FROM guard WHERE room_id=? "
            "ORDER BY create_time DESC LIMIT ?",
            (room_id, limit),
        )

    # ── 统计方法 ───────────────────────────────────────────────────

    async def get_total_counts(self, room_id: str) -> dict:
        """获取各表总条目数"""
        counts = {}
        for table in ("danmu", "gift", "interact", "guard"):
            try:
                result = await self.query_async(
                    f"SELECT count(*) as cnt FROM {table} WHERE room_id=?",
                    (room_id,),
                )
                counts[table] = result[0]["cnt"] if result else 0
            except Exception:
                counts[table] = 0
        return counts


# ── 内部助手 ──────────────────────────────────────────────────────
