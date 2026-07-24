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
"""SQL-backed chat-message history adapter."""

from __future__ import annotations
import json as _json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from .messages import BaseMessage

class SQLChatMessageHistory:
    """Minimal SQLite message store for memory/timeindex.py.

    Table schema::

        id          INTEGER PRIMARY KEY AUTOINCREMENT
        session_id  TEXT
        message     TEXT   -- JSON-serialized {"type": ..., "data": {"content": ...}}
    """

    _engine_cache: dict = {}

    def __init__(self, connection_string: str, session_id: str, table_name: str = "message_store"):
        from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine

        self.session_id = session_id
        self.table_name = table_name

        if connection_string not in self.__class__._engine_cache:
            self.__class__._engine_cache[connection_string] = create_engine(connection_string)
        self._engine = self.__class__._engine_cache[connection_string]

        metadata = MetaData()
        self._table = Table(
            table_name,
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("session_id", String),
            Column("message", Text),
        )
        metadata.create_all(self._engine)

    def _serialize(self, message: Any) -> str:
        if isinstance(message, BaseMessage):
            return _json.dumps({"type": message.type, "data": {"content": message.content}}, ensure_ascii=False)
        if isinstance(message, dict):
            return _json.dumps(message, ensure_ascii=False)
        return _json.dumps({"type": "system", "data": {"content": str(message)}}, ensure_ascii=False)

    def add_message(self, message: Any) -> None:
        from sqlalchemy import insert

        with self._engine.connect() as conn:
            conn.execute(
                insert(self._table).values(
                    session_id=self.session_id,
                    message=self._serialize(message),
                )
            )
            conn.commit()

    def add_messages(self, messages: list) -> None:
        from sqlalchemy import insert

        rows = [
            {"session_id": self.session_id, "message": self._serialize(m)}
            for m in messages
        ]
        if rows:
            with self._engine.connect() as conn:
                conn.execute(insert(self._table), rows)
                conn.commit()
