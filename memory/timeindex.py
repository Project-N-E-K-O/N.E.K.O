from utils.llm_client import SQLChatMessageHistory, SystemMessage
from sqlalchemy import create_engine, text
from config import TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger
from datetime import datetime
import os

logger = get_module_logger(__name__, "Memory")

class TimeIndexedMemory:
    def __init__(self, recent_history_manager):
        self.engines = {}  # 存储 {lanlan_name: engine}
        self.db_paths = {} # 存储 {lanlan_name: db_path}
        self.recent_history_manager = recent_history_manager
        _, _, _, _, _, _, time_store, _, _ = get_config_manager().get_character_data()
        for name in time_store:
            self._ensure_engine_exists(name, time_store[name])

    def _ensure_engine_exists(self, lanlan_name: str, db_path: str | None = None) -> bool:
        """确保指定角色的数据库引擎已初始化喵~"""
        if lanlan_name in self.engines and lanlan_name in self.db_paths:
            return True

        try:
            if not db_path:
                _, _, _, _, _, _, time_store, _, _ = get_config_manager().get_character_data()
                if lanlan_name in time_store:
                    db_path = time_store[lanlan_name]
                else:
                    from memory import ensure_character_dir
                    config_mgr = get_config_manager()
                    db_path = os.path.join(ensure_character_dir(config_mgr.memory_dir, lanlan_name), 'time_indexed.db')
                    logger.info(f"[TimeIndexedMemory] 角色 '{lanlan_name}' 不在配置中，使用默认路径: {db_path}")

            engine = create_engine(f"sqlite:///{db_path}")
            connection_string = f"sqlite:///{db_path}"
            # 先完成所有初始化/迁移，再注册到 self.engines，
            # 避免失败后引擎被标记为"已初始化"而跳过后续修复
            self._ensure_tables_exist_with(engine, connection_string, lanlan_name)
            self._check_and_migrate_schema(engine, lanlan_name)
            self.db_paths[lanlan_name] = db_path
            self.engines[lanlan_name] = engine
            return True
        except Exception:
            logger.exception(f"初始化角色数据库引擎失败: {lanlan_name}")
            return False

    def dispose_engine(self, lanlan_name: str):
        """释放指定角色的数据库引擎资源喵~"""
        engine = self.engines.pop(lanlan_name, None)
        if engine:
            engine.dispose()
            logger.info(f"[TimeIndexedMemory] 已释放角色 {lanlan_name} 的数据库引擎")
        self.db_paths.pop(lanlan_name, None)

    def cleanup(self):
        """清理所有引擎资源喵~"""
        for name in list(self.engines.keys()):
            self.dispose_engine(name)

    def _ensure_tables_exist_with(self, engine, connection_string: str, lanlan_name: str) -> None:
        """
        确保原始表和压缩表存在喵~
        注意：此方法利用了 SQLChatMessageHistory 构造函数的副作用（自动创建表）。
        如果未来 LangChain 实现变更，此逻辑可能需要调整。
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
        """逐表检查并补齐 timestamp 列，每张表独立处理避免互相影响。"""
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
            except Exception:
                logger.exception(f"[TimeIndexedMemory] 迁移 {lanlan_name} 表 {table} 失败")

    async def store_conversation(self, event_id, messages, lanlan_name, timestamp=None):
        # 确保数据库引擎和路径存在
        if not self._ensure_engine_exists(lanlan_name):
            logger.error(f"严重错误：无法为角色 {lanlan_name} 创建任何数据库连接")
            return

        if timestamp is None:
            timestamp = datetime.now()

        db_path = self.db_paths[lanlan_name]
        connection_string = f"sqlite:///{db_path}"
        
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

    def _validate_table_name(self, table_name: str) -> str:
        """验证表名是否合法，防止 SQL 注入喵~"""
        allowed_tables = {TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME}
        if table_name not in allowed_tables:
            raise ValueError(f"不合法的表名: {table_name}")
        return table_name

    def get_last_conversation_time(self, lanlan_name: str) -> datetime | None:
        """查询指定角色最后一次对话的时间戳。无记录时返回 None。"""
        if not self._ensure_engine_exists(lanlan_name):
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

    def retrieve_summary_by_timeframe(self, lanlan_name, start_time, end_time):
        """[已废弃] compressed table 不再写入，fact/reflection 已取代。"""
        return []

    def retrieve_original_by_timeframe(self, lanlan_name, start_time, end_time):
        if lanlan_name not in self.engines:
            return []
        table_name = self._validate_table_name(TIME_ORIGINAL_TABLE_NAME)
        # 查询指定时间范围内的对话
        with self.engines[lanlan_name].connect() as conn:
            result = conn.execute(
                text(f"SELECT session_id, message FROM {table_name} WHERE timestamp BETWEEN :start_time AND :end_time"),
                {"start_time": start_time, "end_time": end_time}
            )
            return result.fetchall()

    # ── FTS5 事实索引 ─────────────────────────────────────────────

    FACTS_FTS_TABLE = "facts_fts"

    def _ensure_fts_table(self, lanlan_name: str) -> None:
        """确保 FTS5 虚拟表存在。unicode61 分词器对中文做字级别索引，零依赖。"""
        if not self._ensure_engine_exists(lanlan_name):
            return
        try:
            with self.engines[lanlan_name].connect() as conn:
                conn.execute(text(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {self.FACTS_FTS_TABLE} "
                    f"USING fts5(fact_id, content, tokenize='unicode61')"
                ))
                conn.commit()
        except Exception as e:
            logger.warning(f"[TimeIndexedMemory] 创建 FTS5 表失败: {e}")

    def index_fact(self, lanlan_name: str, fact_id: str, content: str) -> None:
        """将事实插入 FTS5 索引。"""
        if not self._ensure_engine_exists(lanlan_name):
            return
        self._ensure_fts_table(lanlan_name)
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
                    {"fid": fact_id, "content": content}
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"[TimeIndexedMemory] 索引事实失败: {e}")

    def search_facts(self, lanlan_name: str, query: str, limit: int = 10) -> list[tuple[str, float]]:
        """通过 FTS5 BM25 搜索事实。返回 [(fact_id, bm25_score), ...]。

        BM25 分数为负值，越接近 0 越相关。
        """
        if not self._ensure_engine_exists(lanlan_name):
            return []
        self._ensure_fts_table(lanlan_name)
        try:
            # 转义 FTS5 特殊字符
            safe_query = query.replace('"', '""')
            with self.engines[lanlan_name].connect() as conn:
                result = conn.execute(
                    text(
                        f"SELECT fact_id, bm25({self.FACTS_FTS_TABLE}) as score "
                        f"FROM {self.FACTS_FTS_TABLE} "
                        f'WHERE {self.FACTS_FTS_TABLE} MATCH :query '
                        f"ORDER BY score LIMIT :limit"
                    ),
                    {"query": safe_query, "limit": limit}
                )
                return [(row[0], row[1]) for row in result.fetchall()]
        except Exception as e:
            logger.debug(f"[TimeIndexedMemory] FTS5 搜索失败（可能是查询为空或语法）: {e}")
            return []

    def delete_fact_from_index(self, lanlan_name: str, fact_id: str) -> None:
        """从 FTS5 索引中移除事实。"""
        if not self._ensure_engine_exists(lanlan_name):
            return
        self._ensure_fts_table(lanlan_name)
        try:
            with self.engines[lanlan_name].connect() as conn:
                conn.execute(
                    text(f"DELETE FROM {self.FACTS_FTS_TABLE} WHERE fact_id = :fid"),
                    {"fid": fact_id}
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"[TimeIndexedMemory] 删除 FTS5 索引失败: {e}")