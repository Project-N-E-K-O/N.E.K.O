from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_core.messages import SystemMessage
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
        _, _, _, _, _, _, _, time_store, _, _ = get_config_manager().get_character_data()
        for name in time_store:
            self._ensure_engine_exists(name, time_store[name])

    def _ensure_engine_exists(self, lanlan_name: str, db_path: str | None = None) -> bool:
        """确保指定角色的数据库引擎已初始化喵~"""
        if lanlan_name in self.engines and lanlan_name in self.db_paths:
            return True

        try:
            if not db_path:
                _, _, _, _, _, _, _, time_store, _, _ = get_config_manager().get_character_data()
                if lanlan_name in time_store:
                    db_path = time_store[lanlan_name]
                else:
                    config_mgr = get_config_manager()
                    config_mgr.ensure_memory_directory()
                    db_path = os.path.join(str(config_mgr.memory_dir), f'time_indexed_{lanlan_name}')
                    logger.info(f"[TimeIndexedMemory] 角色 '{lanlan_name}' 不在配置中，使用默认路径: {db_path}")

            self.db_paths[lanlan_name] = db_path
            self.engines[lanlan_name] = create_engine(f"sqlite:///{db_path}")
            connection_string = f"sqlite:///{db_path}"
            self._ensure_tables_exist(connection_string, lanlan_name)
            self.check_table_schema(lanlan_name)
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

    def _ensure_tables_exist(self, connection_string: str, lanlan_name: str) -> None:
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
        if lanlan_name in self.engines:
            with self.engines[lanlan_name].connect() as conn:
                for table in [TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME]:
                    result = conn.execute(text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"))
                    if not result.fetchone():
                        logger.error(f"[TimeIndexedMemory] 表 {table} 未能成功创建喵！")

    def add_timestamp_column(self, lanlan_name):
        if lanlan_name not in self.engines:
            logger.warning(f"尝试为不存在的引擎 {lanlan_name} 添加列")
            return
        
        original_table = self._validate_table_name(TIME_ORIGINAL_TABLE_NAME)
        compressed_table = self._validate_table_name(TIME_COMPRESSED_TABLE_NAME)
        
        with self.engines[lanlan_name].connect() as conn:
            conn.execute(text(f"ALTER TABLE {original_table} ADD COLUMN timestamp DATETIME"))
            conn.execute(text(f"ALTER TABLE {compressed_table} ADD COLUMN timestamp DATETIME"))
            conn.commit()

    def check_table_schema(self, lanlan_name):
        if lanlan_name not in self.engines:
            return
            
        original_table = self._validate_table_name(TIME_ORIGINAL_TABLE_NAME)
        
        with self.engines[lanlan_name].connect() as conn:
            result = conn.execute(text(f"PRAGMA table_info({original_table})"))
            columns = result.fetchall()
            for i in columns:
                if i[1] == 'timestamp':
                    return
            self.add_timestamp_column(lanlan_name)

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
        compressed_table = self._validate_table_name(TIME_COMPRESSED_TABLE_NAME)
        
        origin_history = SQLChatMessageHistory(
            connection_string=connection_string,
            session_id=event_id,
            table_name=original_table,
        )

        compressed_history = SQLChatMessageHistory(
            connection_string=connection_string,
            session_id=event_id,
            table_name=compressed_table,
        )

        origin_history.add_messages(messages)
        compressed_history.add_message(SystemMessage((await self.recent_history_manager.compress_history(messages, lanlan_name))[1]))

        with self.engines[lanlan_name].connect() as conn:
            conn.execute(
                text(f"UPDATE {original_table} SET timestamp = :timestamp WHERE session_id = :session_id"),
                {"timestamp": timestamp, "session_id": event_id}
            )
            conn.execute(
                text(f"UPDATE {compressed_table} SET timestamp = :timestamp WHERE session_id = :session_id"),
                {"timestamp": timestamp, "session_id": event_id}
            )
            conn.commit()

    def _validate_table_name(self, table_name: str) -> str:
        """验证表名是否合法，防止 SQL 注入喵~"""
        allowed_tables = {TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME}
        if table_name not in allowed_tables:
            raise ValueError(f"不合法的表名: {table_name}")
        return table_name

    def retrieve_summary_by_timeframe(self, lanlan_name, start_time, end_time):
        if lanlan_name not in self.engines:
            return []
        table_name = self._validate_table_name(TIME_COMPRESSED_TABLE_NAME)
        with self.engines[lanlan_name].connect() as conn:
            result = conn.execute(
                text(f"SELECT session_id, message FROM {table_name} WHERE timestamp BETWEEN :start_time AND :end_time"),
                {"start_time": start_time, "end_time": end_time}
            )
            return result.fetchall()

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