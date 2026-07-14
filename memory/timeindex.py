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

from utils.llm_client import SQLChatMessageHistory, SystemMessage
from sqlalchemy import create_engine, text
from config import TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME
from memory.stop_names import collect_stop_names, strip_stop_names
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
import asyncio
import os

logger = get_module_logger(__name__, "Memory")

class TimeIndexedMemory:
    def __init__(self, recent_history_manager):
        self.engines = {}  # 存储 {lanlan_name: engine}
        self.db_paths = {} # 存储 {lanlan_name: db_path}
        self._engine_readonly_flags = {}  # 存储 {lanlan_name: bool}
        self._writable_bootstrapped = set()  # 存储已完成可写初始化的角色
        self.recent_history_manager = recent_history_manager
        # 懒加载：不在构造器里同步初始化每角色 engine，首次访问时按需创建
        # （MaintenanceModeError 在 _ensure_engine_exists 内部按需处理）

    def _assert_timeindex_writable(self, lanlan_name: str) -> None:
        assert_cloudsave_writable(
            get_config_manager(),
            operation="save",
            target=f"memory/{lanlan_name}/time_indexed.db",
        )

    def _build_sqlite_connection_string(self, db_path: str, *, readonly: bool) -> tuple[str, str]:
        normalized_db_path = os.path.abspath(db_path)
        uri_path = normalized_db_path.replace("\\", "/")
        if readonly:
            sqlite_file_uri = f"file:{uri_path}"
            if os.name == "nt" and not uri_path.startswith("/"):
                sqlite_file_uri = f"file:/{uri_path}"
            return normalized_db_path, f"sqlite:///{sqlite_file_uri}?mode=ro&uri=true"
        if not readonly:
            db_dir = os.path.dirname(normalized_db_path)
            os.makedirs(db_dir, exist_ok=True)
        return normalized_db_path, f"sqlite:///{uri_path}"

    def _resolve_expected_db_path(self, lanlan_name: str, *, readonly: bool) -> str | None:
        """Compute the target path of this character's db under the current memory_dir.

        time_store takes precedence (allowing a character to register its db
        explicitly outside memory_dir), otherwise fall back to
        ``memory_dir/{name}/time_indexed.db``. ``config_manager.memory_dir`` is
        re-read on every call so the path-drift self-check in
        ``_ensure_engine_exists`` can notice in-process memory_dir drift.
        """
        try:
            _, _, _, _, _, _, time_store, _, _ = get_config_manager().get_character_data()
        except Exception as exc:
            logger.warning("[TimeIndexedMemory] get_character_data 失败，回退默认 db_path: %s", exc)
            time_store = {}
        if lanlan_name in time_store:
            return time_store[lanlan_name]
        config_mgr = get_config_manager()
        if readonly:
            return os.path.join(str(config_mgr.memory_dir), lanlan_name, "time_indexed.db")
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(config_mgr.memory_dir, lanlan_name), 'time_indexed.db')

    @staticmethod
    def _db_paths_equivalent(left: str, right: str) -> bool:
        try:
            return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
        except Exception:
            return left == right

    def _ensure_engine_exists(
        self,
        lanlan_name: str,
        db_path: str | None = None,
        readonly: bool = False,
    ) -> bool:
        """Ensure the given character's database engine is initialized, meow~"""
        if not readonly:
            self._assert_timeindex_writable(lanlan_name)
        if lanlan_name in self.engines and lanlan_name in self.db_paths:
            cached_engine = self.engines[lanlan_name]
            cached_db_path = str(self.db_paths[lanlan_name])
            cached_readonly = bool(self._engine_readonly_flags.get(lanlan_name, False))

            # Path-drift defense: 罕见但可能——/reload 期间 storage_policy
            # 重写 selected_root，新实例已经 reload 过但旧实例还在被某条
            # async path 持有；或测试场景里 monkeypatch 了 memory_dir。
            # 一旦 cached db_path 与当前 memory_dir 推导出的目标不一致，
            # 老 SQLAlchemy engine 会继续往旧文件写，前端表象就是 db 永远
            # 不更新（/process 的 except Exception 又把 SQL 错误吞掉）。
            # 嗅探到漂移就 dispose 让下面的新建分支用 expected 重建。
            expected_db_path = db_path
            if expected_db_path is None:
                try:
                    expected_db_path = self._resolve_expected_db_path(lanlan_name, readonly=readonly)
                except Exception as exc:
                    logger.debug("[TimeIndexedMemory] 解析 expected db_path 失败，跳过 drift 检查: %s", exc)
                    expected_db_path = None
            if expected_db_path and not self._db_paths_equivalent(expected_db_path, cached_db_path):
                logger.warning(
                    "[TimeIndexedMemory] 角色 %s 的 db_path 漂移，dispose 重建：cached=%s expected=%s",
                    lanlan_name, cached_db_path, expected_db_path,
                )
                self.dispose_engine(lanlan_name)
                db_path = expected_db_path
                # 落到下面"新建 engine"分支
            elif not readonly and cached_readonly and lanlan_name not in self._writable_bootstrapped:
                logger.info("[TimeIndexedMemory] 角色 %s 当前为只读引擎，切换为可写引擎后再执行迁移", lanlan_name)
                self.dispose_engine(lanlan_name)
                if not db_path:
                    db_path = cached_db_path
            else:
                if readonly or lanlan_name in self._writable_bootstrapped:
                    return True
                try:
                    normalized_db_path, connection_string = self._build_sqlite_connection_string(
                        str(self.db_paths[lanlan_name]),
                        readonly=False,
                    )
                    self._ensure_tables_exist_with(cached_engine, connection_string, lanlan_name)
                    self._check_and_migrate_schema(cached_engine, lanlan_name)
                    self.db_paths[lanlan_name] = normalized_db_path
                    self._writable_bootstrapped.add(lanlan_name)
                    self._engine_readonly_flags[lanlan_name] = False
                    return True
                except Exception:
                    logger.exception(f"补跑角色数据库可写初始化失败: {lanlan_name}")
                    return False

        engine = None
        connection_string = None
        try:
            if not db_path:
                db_path = self._resolve_expected_db_path(lanlan_name, readonly=readonly)
                if not db_path:
                    logger.error(f"[TimeIndexedMemory] 角色 '{lanlan_name}' 无法解析 db_path")
                    return False

            normalized_db_path, connection_string = self._build_sqlite_connection_string(
                db_path,
                readonly=readonly,
            )
            if readonly and not os.path.isfile(normalized_db_path):
                return False
            engine = create_engine(connection_string)
            if not readonly:
                # 先完成所有初始化/迁移，再注册到 self.engines，
                # 避免失败后引擎被标记为"已初始化"而跳过后续修复
                self._ensure_tables_exist_with(engine, connection_string, lanlan_name)
                self._check_and_migrate_schema(engine, lanlan_name)
                self._writable_bootstrapped.add(lanlan_name)
            else:
                self._writable_bootstrapped.discard(lanlan_name)
            self.db_paths[lanlan_name] = normalized_db_path
            self.engines[lanlan_name] = engine
            self._engine_readonly_flags[lanlan_name] = readonly
            return True
        except Exception:
            try:
                if engine is not None:
                    engine.dispose()
            except Exception as cleanup_exc:
                logger.debug(
                    "[TimeIndexedMemory] 初始化失败后的 engine.dispose 清理失败: %s",
                    cleanup_exc,
                )
            try:
                existing_engine = self.engines.get(lanlan_name)
                if existing_engine is engine:
                    self.engines.pop(lanlan_name, None)
                    self.db_paths.pop(lanlan_name, None)
                    self._engine_readonly_flags.pop(lanlan_name, None)
                    self._writable_bootstrapped.discard(lanlan_name)
            except Exception as cleanup_exc:
                logger.debug(
                    "[TimeIndexedMemory] 初始化失败后的缓存回收清理失败(%s): %s",
                    lanlan_name,
                    cleanup_exc,
                )
            if connection_string:
                cached_engine = SQLChatMessageHistory._engine_cache.pop(connection_string, None)
                if cached_engine is not None and cached_engine is not engine:
                    try:
                        cached_engine.dispose()
                    except Exception as cleanup_exc:
                        logger.debug(
                            "[TimeIndexedMemory] 初始化失败后的 SQLChatMessageHistory 引擎清理失败(%s): %s",
                            lanlan_name,
                            cleanup_exc,
                        )
            logger.exception(f"初始化角色数据库引擎失败: {lanlan_name}")
            return False

    async def _aensure_engine_exists(self, lanlan_name: str, db_path: str | None = None) -> bool:
        """Async version: offload the blocking engine creation to the thread pool.

        There used to be an early short-circuit here — ``if lanlan_name in self.engines and lanlan_name in self.db_paths:
        return True`` — keeping the cache-hit check outside the sync
        implementation. That path bypassed the path-drift self-check newly added
        to ``_ensure_engine_exists`` (dispose & rebuild when the cached db_path
        mismatches the expected one derived from the current memory_dir). No
        async caller currently uses this entry, but to keep a future addition
        from silently disabling the drift detection, the short-circuit was
        removed and everything delegates to the sync implementation.
        """
        return await asyncio.to_thread(self._ensure_engine_exists, lanlan_name, db_path)

    def dispose_engine(self, lanlan_name: str):
        """Dispose the given character's database engine resources, meow~"""
        db_path = self.db_paths.pop(lanlan_name, None)
        engine = self.engines.pop(lanlan_name, None)
        self._engine_readonly_flags.pop(lanlan_name, None)
        self._writable_bootstrapped.discard(lanlan_name)
        if engine:
            engine.dispose()
            logger.info(f"[TimeIndexedMemory] 已释放角色 {lanlan_name} 的数据库引擎")
        if db_path:
            normalized_db_path, readonly_connection_string = self._build_sqlite_connection_string(
                str(db_path),
                readonly=True,
            )
            uri_path = normalized_db_path.replace("\\", "/")
            writable_connection_string = f"sqlite:///{uri_path}"
            for connection_string in {readonly_connection_string, writable_connection_string}:
                cached_engine = SQLChatMessageHistory._engine_cache.pop(connection_string, None)
                if cached_engine and cached_engine is not engine:
                    cached_engine.dispose()

    def cleanup(self):
        """Clean up all engine resources, meow~"""
        for name in list(self.engines.keys()):
            self.dispose_engine(name)

    def _ensure_tables_exist_with(self, engine, connection_string: str, lanlan_name: str) -> None:
        """
        Ensure the raw and compressed tables exist, meow~
        Note: this method relies on a side effect of the SQLChatMessageHistory
        constructor (automatic table creation). If the LangChain implementation
        changes in the future, this logic may need adjusting.
        """
        _ = SQLChatMessageHistory(
            connection_string=connection_string,
            session_id="",
            table_name=TIME_ORIGINAL_TABLE_NAME,
        )
        _ = SQLChatMessageHistory(
            connection_string=connection_string,
            session_id="",
            table_name=TIME_COMPRESSED_TABLE_NAME,
        )

        # 验证表是否真的被创建了喵~
        with engine.connect() as conn:
            for table in [TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME]:
                result = conn.execute(text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"))
                if not result.fetchone():
                    logger.error(f"[TimeIndexedMemory] 表 {table} 未能成功创建喵！")

    def _check_and_migrate_schema(self, engine, lanlan_name: str) -> None:
        """Check and backfill the timestamp column table by table; each table handled independently so they can't affect each other."""
        migration_errors = []
        for table_name in [TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME]:
            table = self._validate_table_name(table_name)
            try:
                with engine.connect() as conn:
                    result = conn.execute(text(f"PRAGMA table_info({table})"))
                    columns = [row[1] for row in result.fetchall()]
                    if 'timestamp' not in columns:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN timestamp DATETIME"))
                        conn.commit()
                        logger.info(f"[TimeIndexedMemory] 已为 {lanlan_name} 的表 {table} 补齐 timestamp 列")
            except Exception as exc:
                logger.exception(f"[TimeIndexedMemory] 迁移 {lanlan_name} 表 {table} 失败")
                migration_errors.append(f"{table}: {exc}")
        if migration_errors:
            raise RuntimeError(
                f"[TimeIndexedMemory] 角色 {lanlan_name} schema 迁移失败: {'; '.join(migration_errors)}"
            )

    def store_conversation(self, event_id, messages, lanlan_name, timestamp=None):
        self._assert_timeindex_writable(lanlan_name)
        # 确保数据库引擎和路径存在
        if not self._ensure_engine_exists(lanlan_name):
            logger.error(f"严重错误：无法为角色 {lanlan_name} 创建任何数据库连接")
            return

        if timestamp is None:
            timestamp = datetime.now()

        db_path = self.db_paths[lanlan_name]
        uri_path = db_path.replace("\\", "/")
        connection_string = f"sqlite:///{uri_path}"

        original_table = self._validate_table_name(TIME_ORIGINAL_TABLE_NAME)

        origin_history = SQLChatMessageHistory(
            connection_string=connection_string,
            session_id=event_id,
            table_name=original_table,
        )

        origin_history.add_messages(messages)
        # NOTE: compressed table 写入已废弃，fact/reflection 层已取代其功能

        with self.engines[lanlan_name].connect() as conn:
            conn.execute(
                text(f"UPDATE {original_table} SET timestamp = :timestamp WHERE session_id = :session_id"),
                {"timestamp": timestamp, "session_id": event_id}
            )
            conn.commit()

    async def astore_conversation(self, event_id, messages, lanlan_name, timestamp=None):
        await asyncio.to_thread(
            self.store_conversation, event_id, messages, lanlan_name, timestamp
        )

    def _validate_table_name(self, table_name: str) -> str:
        """Validate that a table name is legal, guarding against SQL injection, meow~"""
        allowed_tables = {TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME}
        if table_name not in allowed_tables:
            raise ValueError(f"不合法的表名: {table_name}")
        return table_name

    def get_last_conversation_time(self, lanlan_name: str) -> datetime | None:
        """Query the timestamp of the given character's last conversation. Returns None when there are no records."""
        try:
            if not self._ensure_engine_exists(lanlan_name, readonly=True):
                return None
        except MaintenanceModeError as exc:
            logger.debug(f"[TimeIndexedMemory] 维护态跳过初始化 {lanlan_name} 的 time_indexed.db: {exc}")
            return None
        table_name = self._validate_table_name(TIME_ORIGINAL_TABLE_NAME)
        try:
            with self.engines[lanlan_name].connect() as conn:
                result = conn.execute(
                    text(f"SELECT MAX(timestamp) FROM {table_name}")
                )
                row = result.fetchone()
                if row and row[0]:
                    ts = row[0]
                    if isinstance(ts, str):
                        try:
                            return datetime.fromisoformat(ts)
                        except ValueError:
                            return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f")
                    if isinstance(ts, datetime):
                        return ts
        except Exception as e:
            logger.warning(f"[TimeIndexedMemory] 查询最后对话时间失败: {e}")
        return None

    async def aget_last_conversation_time(self, lanlan_name: str) -> datetime | None:
        return await asyncio.to_thread(self.get_last_conversation_time, lanlan_name)

    def retrieve_summary_by_timeframe(self, lanlan_name, start_time, end_time):
        """[Deprecated] The compressed table is no longer written; fact/reflection replaced it."""
        return []

    async def aretrieve_summary_by_timeframe(self, lanlan_name, start_time, end_time):
        return []

    def retrieve_original_by_timeframe(self, lanlan_name, start_time, end_time, limit_rows: int | None = None):
        """Read raw conversation rows within the [start_time, end_time] window.

        Returns ``[(timestamp, session_id, message), ...]`` sorted by timestamp
        ASC — guaranteeing the caller can advance its cursor for drainage based
        on the last row's ts.

        When ``limit_rows`` is not None, a LIMIT is added at the SQL level,
        keeping an overlong fallback window from pulling the whole table into
        memory.

        Lazy loading: first access (e.g. reading right after a restart) needs
        engine registration, otherwise the rebuttal loop silently skips until
        store_conversation triggers table creation. The read path is readonly;
        reads are allowed even in maintenance mode.
        """
        try:
            if not self._ensure_engine_exists(lanlan_name, readonly=True):
                return []
        except MaintenanceModeError as exc:
            logger.debug(f"[TimeIndexedMemory] 维护态跳过读取 {lanlan_name} 的历史对话: {exc}")
            return []
        table_name = self._validate_table_name(TIME_ORIGINAL_TABLE_NAME)
        try:
            sql = (
                f"SELECT timestamp, session_id, message FROM {table_name} "
                f"WHERE timestamp BETWEEN :start_time AND :end_time "
                f"ORDER BY timestamp ASC"
            )
            params: dict = {"start_time": start_time, "end_time": end_time}
            if limit_rows is not None and limit_rows > 0:
                sql += " LIMIT :limit_rows"
                params["limit_rows"] = int(limit_rows)
            with self.engines[lanlan_name].connect() as conn:
                result = conn.execute(text(sql), params)
                return result.fetchall()
        except Exception as e:
            logger.warning(f"[TimeIndexedMemory] 按时间范围读取原始对话失败: {e}")
            return []

    async def aretrieve_original_by_timeframe(self, lanlan_name, start_time, end_time, limit_rows: int | None = None):
        return await asyncio.to_thread(
            self.retrieve_original_by_timeframe, lanlan_name, start_time, end_time, limit_rows
        )

    def _fetch_original_timeframe_page(
        self,
        lanlan_name: str,
        start_time,
        end_time,
        *,
        page_size: int,
        cursor: tuple[object, int] | None,
    ) -> tuple[list[tuple[object, object, object]], tuple[object, int] | None]:
        """Fetch one stable keyset page and close its connection before returning."""
        table_name = self._validate_table_name(TIME_ORIGINAL_TABLE_NAME)
        sql = (
            f"SELECT timestamp, rowid, session_id, message FROM {table_name} "
            f"WHERE timestamp BETWEEN :start_time AND :end_time"
        )
        params: dict = {
            "start_time": start_time,
            "end_time": end_time,
            "page_size": page_size,
        }
        if cursor is not None:
            sql += (
                " AND (timestamp > :cursor_timestamp "
                "OR (timestamp = :cursor_timestamp AND rowid > :cursor_rowid))"
            )
            params["cursor_timestamp"], params["cursor_rowid"] = cursor
        sql += " ORDER BY timestamp ASC, rowid ASC LIMIT :page_size"

        # The connection and Result stay entirely inside this call. In
        # particular, async callers run this whole method in ``to_thread`` and
        # only receive the materialized, bounded page after ``__exit__``.
        with self.engines[lanlan_name].connect() as conn:
            result = conn.execute(text(sql), params)
            raw_rows = result.fetchmany(page_size)

        if not raw_rows:
            return [], None
        rows = [(row[0], row[2], row[3]) for row in raw_rows]
        next_cursor = (raw_rows[-1][0], int(raw_rows[-1][1]))
        return rows, next_cursor

    def iter_original_by_timeframe_batches(
        self,
        lanlan_name: str,
        start_time,
        end_time,
        *,
        batch_size: int = 256,
        limit_rows: int | None = None,
    ) -> Iterator[list[tuple[object, object, object]]]:
        """Yield bounded raw-conversation batches in stable chronological order.

        Unlike ``retrieve_original_by_timeframe``, this interface never keeps a
        live SQLAlchemy connection attached to the yielded iterator: every
        keyset page opens, consumes, and closes its connection before the batch
        reaches the caller. ``limit_rows`` is a total cap across all batches;
        non-positive values retain the list API's historical "no LIMIT" meaning.
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        try:
            if not self._ensure_engine_exists(lanlan_name, readonly=True):
                return
        except MaintenanceModeError as exc:
            logger.debug(f"[TimeIndexedMemory] 维护态跳过分批读取 {lanlan_name} 的历史对话: {exc}")
            return

        remaining = int(limit_rows) if limit_rows is not None and limit_rows > 0 else None
        cursor: tuple[object, int] | None = None
        while remaining is None or remaining > 0:
            page_size = batch_size if remaining is None else min(batch_size, remaining)
            try:
                rows, cursor = self._fetch_original_timeframe_page(
                    lanlan_name,
                    start_time,
                    end_time,
                    page_size=page_size,
                    cursor=cursor,
                )
            except Exception as exc:
                logger.warning(f"[TimeIndexedMemory] 分批读取原始对话失败: {exc}")
                return
            if not rows:
                return
            if remaining is not None:
                remaining -= len(rows)
            yield rows

    async def aiter_original_by_timeframe_batches(
        self,
        lanlan_name: str,
        start_time,
        end_time,
        *,
        batch_size: int = 256,
        limit_rows: int | None = None,
    ) -> AsyncIterator[list[tuple[object, object, object]]]:
        """Async batch iterator whose SQLite work is fully contained in workers."""
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        try:
            ready = await asyncio.to_thread(
                self._ensure_engine_exists,
                lanlan_name,
                None,
                True,
            )
            if not ready:
                return
        except MaintenanceModeError as exc:
            logger.debug(f"[TimeIndexedMemory] 维护态跳过异步分批读取 {lanlan_name} 的历史对话: {exc}")
            return

        remaining = int(limit_rows) if limit_rows is not None and limit_rows > 0 else None
        cursor: tuple[object, int] | None = None
        while remaining is None or remaining > 0:
            page_size = batch_size if remaining is None else min(batch_size, remaining)
            try:
                rows, cursor = await asyncio.to_thread(
                    self._fetch_original_timeframe_page,
                    lanlan_name,
                    start_time,
                    end_time,
                    page_size=page_size,
                    cursor=cursor,
                )
            except Exception as exc:
                logger.warning(f"[TimeIndexedMemory] 异步分批读取原始对话失败: {exc}")
                return
            if not rows:
                return
            if remaining is not None:
                remaining -= len(rows)
            yield rows

    # ── FTS5 事实索引 ─────────────────────────────────────────────

    FACTS_FTS_TABLE = "facts_fts"

    def _ensure_fts_table(self, lanlan_name: str, readonly: bool = False) -> bool:
        """Ensure the FTS5 virtual table exists. The unicode61 tokenizer indexes Chinese character-by-character, zero dependencies."""
        if not self._ensure_engine_exists(lanlan_name, readonly=readonly):
            return False
        if readonly:
            try:
                with self.engines[lanlan_name].connect() as conn:
                    result = conn.execute(
                        text(
                            "SELECT name FROM sqlite_master "
                            "WHERE type='table' AND name = :table_name"
                        ),
                        {"table_name": self.FACTS_FTS_TABLE},
                    )
                    return result.fetchone() is not None
            except Exception as e:
                logger.debug(f"[TimeIndexedMemory] 只读检查 FTS5 表失败: {e}")
                return False
        self._assert_timeindex_writable(lanlan_name)
        try:
            with self.engines[lanlan_name].connect() as conn:
                conn.execute(text(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {self.FACTS_FTS_TABLE} "
                    f"USING fts5(fact_id, content, tokenize='unicode61')"
                ))
                conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[TimeIndexedMemory] 创建 FTS5 表失败: {e}")
            return False

    async def a_ensure_fts_table(self, lanlan_name: str) -> None:
        await asyncio.to_thread(self._ensure_fts_table, lanlan_name)

    def index_fact(self, lanlan_name: str, fact_id: str, content: str) -> None:
        """Insert a fact into the FTS5 index.

        master/lanlan + their nicknames are stripped before indexing: these
        tokens appear in nearly every fact, and although BM25 IDF automatically
        down-weights them, leaving them in still lets them noise up dedup
        scores ("主人喜欢猫" vs "主人讨厌狗" would still get nonzero similarity
        through the shared "主人"). Only stripping on both the index side and
        the query side lets BM25 score entirely around substantive content.
        """  # noqa: DOCSTRING_CJK
        self._assert_timeindex_writable(lanlan_name)
        if not self._ensure_engine_exists(lanlan_name):
            return
        if not self._ensure_fts_table(lanlan_name):
            return
        stop_names = collect_stop_names(get_config_manager(), lanlan_name)
        indexed_content = strip_stop_names(content, stop_names)
        try:
            with self.engines[lanlan_name].connect() as conn:
                # 先检查是否已存在
                result = conn.execute(
                    text(f"SELECT fact_id FROM {self.FACTS_FTS_TABLE} WHERE fact_id = :fid"),
                    {"fid": fact_id}
                )
                if result.fetchone():
                    return  # 已索引
                conn.execute(
                    text(f"INSERT INTO {self.FACTS_FTS_TABLE}(fact_id, content) VALUES(:fid, :content)"),
                    {"fid": fact_id, "content": indexed_content}
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"[TimeIndexedMemory] 索引事实失败: {e}")

    async def aindex_fact(self, lanlan_name: str, fact_id: str, content: str) -> None:
        await asyncio.to_thread(self.index_fact, lanlan_name, fact_id, content)

    def search_facts(self, lanlan_name: str, query: str, limit: int = 10) -> list[tuple[str, float]]:
        """Search facts via FTS5 BM25. Returns [(fact_id, bm25_score), ...].

        FTS5 bm25() scores are usually negative; the smaller (more negative),
        the more relevant. master/lanlan + their nicknames are stripped before
        querying: symmetric with ``index_fact`` — only with both the index side
        and the query side rid of these stop-names can BM25 truly
        differentiate similarity around substantive content.
        """
        try:
            if not self._ensure_engine_exists(lanlan_name, readonly=True):
                return []
            if not self._ensure_fts_table(lanlan_name, readonly=True):
                return []
        except MaintenanceModeError as exc:
            logger.debug(f"[TimeIndexedMemory] 维护态跳过搜索 {lanlan_name} 的 FTS 索引初始化: {exc}")
            return []
        stop_names = collect_stop_names(get_config_manager(), lanlan_name)
        normalized_query = strip_stop_names(query, stop_names)
        if not normalized_query.strip():
            # Stripping 后什么都没剩——多半是纯名字查询，不让 FTS5 在空
            # query 上抛 syntax error。
            return []
        try:
            # Sanitize FTS5 special syntax characters to prevent
            # OperationalError. With the unicode61 tokenizer (CJK characters
            # indexed individually), unquoted queries get token-level AND
            # semantics — the desired behavior for similar-fact matching
            # in facts.py. Phrase wrapping would require ordered adjacency
            # and cause recall regression.
            safe_query = (
                normalized_query
                .replace('"', ' ')
                .replace('*', ' ')
                .replace('(', ' ')
                .replace(')', ' ')
                .replace('-', ' ')
                .replace(':', ' ')
            )
            # Wrap each token in double quotes so FTS5 boolean keywords
            # (AND, OR, NOT) are treated as literal search terms instead
            # of operators. With unicode61, single-character CJK tokens
            # behave identically quoted or unquoted.
            tokens = [f'"{t}"' for t in safe_query.split() if t.strip()]
            if not tokens:
                return []
            fts_query = ' '.join(tokens)
            with self.engines[lanlan_name].connect() as conn:
                result = conn.execute(
                    text(
                        f"SELECT fact_id, bm25({self.FACTS_FTS_TABLE}) as score "
                        f"FROM {self.FACTS_FTS_TABLE} "
                        f'WHERE {self.FACTS_FTS_TABLE} MATCH :query '
                        f"ORDER BY score LIMIT :limit"
                    ),
                    {"query": fts_query, "limit": limit}
                )
                return [(row[0], row[1]) for row in result.fetchall()]
        except Exception as e:
            logger.debug(f"[TimeIndexedMemory] FTS5 搜索失败（可能是查询为空或语法）: {e}")
            return []

    async def asearch_facts(self, lanlan_name: str, query: str, limit: int = 10) -> list[tuple[str, float]]:
        return await asyncio.to_thread(self.search_facts, lanlan_name, query, limit)

    def delete_fact_from_index(self, lanlan_name: str, fact_id: str) -> None:
        """Remove a fact from the FTS5 index."""
        self._assert_timeindex_writable(lanlan_name)
        if not self._ensure_engine_exists(lanlan_name):
            return
        if not self._ensure_fts_table(lanlan_name):
            return
        try:
            with self.engines[lanlan_name].connect() as conn:
                conn.execute(
                    text(f"DELETE FROM {self.FACTS_FTS_TABLE} WHERE fact_id = :fid"),
                    {"fid": fact_id}
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"[TimeIndexedMemory] 删除 FTS5 索引失败: {e}")

    async def adelete_fact_from_index(self, lanlan_name: str, fact_id: str) -> None:
        await asyncio.to_thread(self.delete_fact_from_index, lanlan_name, fact_id)
