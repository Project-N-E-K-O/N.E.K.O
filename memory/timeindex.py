from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_core.messages import SystemMessage
from sqlalchemy import create_engine, text
from config import TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME
from utils.config_manager import get_config_manager
from datetime import datetime
import logging
import os

logger = logging.getLogger(__name__)

class TimeIndexedMemory:
    def __init__(self, recent_history_manager):
        self.engines = {}  # 存储 {lanlan_name: engine}
        self.db_paths = {} # 存储 {lanlan_name: db_path}
        self.recent_history_manager = recent_history_manager
        _, _, _, _, _, _, _, time_store, _, _ = get_config_manager().get_character_data()
        for name in time_store:
            db_path = time_store[name]
            self.db_paths[name] = db_path
            self.engines[name] = create_engine(f"sqlite:///{db_path}")
            connection_string = f"sqlite:///{db_path}"
            self._ensure_tables_exist(connection_string)
            self.check_table_schema(name)

    def _ensure_tables_exist(self, connection_string: str) -> None:
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

    def add_timestamp_column(self, lanlan_name):
        with self.engines[lanlan_name].connect() as conn:
            conn.execute(text(f"ALTER TABLE {TIME_ORIGINAL_TABLE_NAME} ADD COLUMN timestamp DATETIME"))
            conn.execute(text(f"ALTER TABLE {TIME_COMPRESSED_TABLE_NAME} ADD COLUMN timestamp DATETIME"))
            conn.commit()

    def check_table_schema(self, lanlan_name):
        with self.engines[lanlan_name].connect() as conn:
            result = conn.execute(text(f"PRAGMA table_info({TIME_ORIGINAL_TABLE_NAME})"))
            columns = result.fetchall()
            for i in columns:
                if i[1] == 'timestamp':
                    return
            self.add_timestamp_column(lanlan_name)

    async def store_conversation(self, event_id, messages, lanlan_name, timestamp=None):
        # 确保数据库引擎和路径存在
        if lanlan_name not in self.engines or lanlan_name not in self.db_paths:
            try:
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
                self._ensure_tables_exist(f"sqlite:///{db_path}")
                self.check_table_schema(lanlan_name)
            except Exception:
                logger.exception(f"初始化角色数据库失败: {lanlan_name}")
                # 最后的保底方案：强制使用默认路径
                try:
                    config_mgr = get_config_manager()
                    config_mgr.ensure_memory_directory()
                    db_path = os.path.join(str(config_mgr.memory_dir), f'time_indexed_{lanlan_name}')
                    self.db_paths[lanlan_name] = db_path
                    self.engines[lanlan_name] = create_engine(f"sqlite:///{db_path}")
                    self._ensure_tables_exist(f"sqlite:///{db_path}")
                    self.check_table_schema(lanlan_name)
                except Exception:
                    logger.error(f"严重错误：无法为角色 {lanlan_name} 创建任何数据库连接")
                    return

        if timestamp is None:
            timestamp = datetime.now()

        db_path = self.db_paths[lanlan_name]
        connection_string = f"sqlite:///{db_path}"
        
        origin_history = SQLChatMessageHistory(
            connection_string=connection_string,
            session_id=event_id,
            table_name=TIME_ORIGINAL_TABLE_NAME,
        )

        compressed_history = SQLChatMessageHistory(
            connection_string=connection_string,
            session_id=event_id,
            table_name=TIME_COMPRESSED_TABLE_NAME,
        )

        origin_history.add_messages(messages)
        compressed_history.add_message(SystemMessage((await self.recent_history_manager.compress_history(messages, lanlan_name))[1]))

        with self.engines[lanlan_name].connect() as conn:
            conn.execute(
                text(f"UPDATE {TIME_ORIGINAL_TABLE_NAME} SET timestamp = :timestamp WHERE session_id = :session_id"),
                {"timestamp": timestamp, "session_id": event_id}
            )
            conn.execute(
                text(f"UPDATE {TIME_COMPRESSED_TABLE_NAME} SET timestamp = :timestamp WHERE session_id = :session_id"),
                {"timestamp": timestamp, "session_id": event_id}
            )
            conn.commit()

    def retrieve_summary_by_timeframe(self, lanlan_name, start_time, end_time):
        if lanlan_name not in self.engines:
            return []
        with self.engines[lanlan_name].connect() as conn:
            result = conn.execute(
                text(f"SELECT session_id, message FROM {TIME_COMPRESSED_TABLE_NAME} WHERE timestamp BETWEEN :start_time AND :end_time"),
                {"start_time": start_time, "end_time": end_time}
            )
            return result.fetchall()

    def retrieve_original_by_timeframe(self, lanlan_name, start_time, end_time):
        if lanlan_name not in self.engines:
            return []
        # 查询指定时间范围内的对话
        with self.engines[lanlan_name].connect() as conn:
            result = conn.execute(
                text(f"SELECT session_id, message FROM {TIME_ORIGINAL_TABLE_NAME} WHERE timestamp BETWEEN :start_time AND :end_time"),
                {"start_time": start_time, "end_time": end_time}
            )
            return result.fetchall()