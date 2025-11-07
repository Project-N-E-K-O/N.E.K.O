from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_core.messages import SystemMessage
from sqlalchemy import create_engine, text
from config import TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME
from utils.config_manager import get_config_manager
from datetime import datetime

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
        if timestamp is None:
            timestamp = datetime.now()

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