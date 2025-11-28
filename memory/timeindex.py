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
        self.engine = {}
        self.recent_history_manager = recent_history_manager
        _, _, _, _, _, _, _, time_store, _, _ = get_config_manager().get_character_data()
        for i in time_store:
            self.engine[i] = create_engine(f"sqlite:///{time_store[i]}")

            _ = SQLChatMessageHistory(
                connection=self.engine[i],
                session_id="",
                table_name=TIME_ORIGINAL_TABLE_NAME,
            )

            _ = SQLChatMessageHistory(
                connection=self.engine[i],
                session_id="",
                table_name=TIME_COMPRESSED_TABLE_NAME,
            )
            self.check_table_schema(i)

    def add_timestamp_column(self, lanlan_name):
        with self.engine[lanlan_name].connect() as conn:
            conn.execute(text(f"ALTER TABLE {TIME_ORIGINAL_TABLE_NAME} ADD COLUMN timestamp DATETIME"))
            conn.execute(text(f"ALTER TABLE {TIME_COMPRESSED_TABLE_NAME} ADD COLUMN timestamp DATETIME"))
            conn.commit()

    def check_table_schema(self, lanlan_name):
        with self.engine[lanlan_name].connect() as conn:
            result = conn.execute(text(f"PRAGMA table_info({TIME_ORIGINAL_TABLE_NAME})"))
            columns = result.fetchall()
            for i in columns:
                if i[1] == 'timestamp':
                    return
            self.add_timestamp_column(lanlan_name)

    async def store_conversation(self, event_id, messages, lanlan_name, timestamp=None):
        # 检查角色是否存在于配置中，如果不存在则创建默认路径
        try:
            _, _, _, _, _, _, _, time_store, _, _ = get_config_manager().get_character_data()
            
            # 如果角色不在配置中，使用默认路径创建
            if lanlan_name not in time_store:
                config_mgr = get_config_manager()
                # 确保memory目录存在
                config_mgr.ensure_memory_directory()
                memory_base = str(config_mgr.memory_dir)
                default_path = os.path.join(memory_base, f'time_indexed_{lanlan_name}')
                time_store[lanlan_name] = default_path
                logger.info(f"[TimeIndexedMemory] 角色 '{lanlan_name}' 不在配置中，使用默认路径: {default_path}")
            
            # 确保数据库引擎存在
            if lanlan_name not in self.engine:
                # 创建数据库引擎和表
                db_path = time_store[lanlan_name]
                self.engine[lanlan_name] = create_engine(f"sqlite:///{db_path}")
                _ = SQLChatMessageHistory(
                    connection=self.engine[lanlan_name],
                    session_id="",
                    table_name=TIME_ORIGINAL_TABLE_NAME,
                )
                _ = SQLChatMessageHistory(
                    connection=self.engine[lanlan_name],
                    session_id="",
                    table_name=TIME_COMPRESSED_TABLE_NAME,
                )
                self.check_table_schema(lanlan_name)
                logger.info(f"[TimeIndexedMemory] 为角色 {lanlan_name} 创建数据库引擎: {db_path}")
        except Exception as e:
            logger.error(f"检查角色配置失败: {e}")
            # 即使配置检查失败，也尝试使用默认路径
            try:
                config_mgr = get_config_manager()
                # 确保memory目录存在
                config_mgr.ensure_memory_directory()
                memory_base = str(config_mgr.memory_dir)
                default_path = os.path.join(memory_base, f'time_indexed_{lanlan_name}')
                if lanlan_name not in self.engine:
                    self.engine[lanlan_name] = create_engine(f"sqlite:///{default_path}")
                    _ = SQLChatMessageHistory(
                        connection=self.engine[lanlan_name],
                        session_id="",
                        table_name=TIME_ORIGINAL_TABLE_NAME,
                    )
                    _ = SQLChatMessageHistory(
                        connection=self.engine[lanlan_name],
                        session_id="",
                        table_name=TIME_COMPRESSED_TABLE_NAME,
                    )
                    self.check_table_schema(lanlan_name)
                    logger.info(f"[TimeIndexedMemory] 使用默认路径创建数据库: {default_path}")
            except Exception as e2:
                logger.error(f"创建默认数据库失败: {e2}")
                return
        
        if timestamp is None:
            timestamp = datetime.now()

        if lanlan_name not in self.engine:
            logger.error(f"角色 '{lanlan_name}' 的数据库引擎不存在")
            return

        origin_history = SQLChatMessageHistory(
            connection=self.engine[lanlan_name],
            session_id=event_id,
            table_name=TIME_ORIGINAL_TABLE_NAME,
        )

        compressed_history = SQLChatMessageHistory(
            connection=self.engine[lanlan_name],
            session_id=event_id,
            table_name=TIME_COMPRESSED_TABLE_NAME,
        )

        origin_history.add_messages(messages)
        compressed_history.add_message(SystemMessage((await self.recent_history_manager.compress_history(messages, lanlan_name))[1]))

        with self.engine[lanlan_name].connect() as conn:
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
        with self.engine[lanlan_name].connect() as conn:
            result = conn.execute(
                text(f"SELECT session_id, message FROM {TIME_COMPRESSED_TABLE_NAME} WHERE timestamp BETWEEN :start_time AND :end_time"),
                {"start_time": start_time, "end_time": end_time}
            )
            return result.fetchall()

    def retrieve_original_by_timeframe(self, lanlan_name, start_time, end_time):
        # 查询指定时间范围内的对话
        with self.engine[lanlan_name].connect() as conn:
            result = conn.execute(
                text(f"SELECT session_id, message FROM {TIME_ORIGINAL_TABLE_NAME} WHERE timestamp BETWEEN :start_time AND :end_time"),
                {"start_time": start_time, "end_time": end_time}
            )
            return result.fetchall()