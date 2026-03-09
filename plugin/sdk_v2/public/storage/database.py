"""Database implementation for SDK v2 shared storage."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Protocol

from plugin.sdk_v2.shared.core.types import JsonValue, LoggerLike
from plugin.sdk_v2.shared.models import Err, Ok, Result

try:
    import ormsgpack as _msgpack  # type: ignore

    def _pack(value: object) -> bytes:
        return _msgpack.packb(value)

    def _unpack(data: bytes) -> object:
        return _msgpack.unpackb(data)
except ImportError:  # pragma: no cover
    import msgpack as _msgpack  # type: ignore

    def _pack(value: object) -> bytes:
        return _msgpack.packb(value, use_bin_type=True)

    def _unpack(data: bytes) -> object:
        return _msgpack.unpackb(data, raw=False)


class AsyncSessionProtocol(Protocol):
    async def execute(self, statement: object, parameters: object | None = None) -> object: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def close(self) -> None: ...


class _SqliteAsyncSession:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    async def execute(self, statement: object, parameters: object | None = None) -> object:
        sql = str(statement)
        params = parameters if isinstance(parameters, (tuple, list, dict)) or parameters is None else (parameters,)
        return self._conn.execute(sql, params or ())

    async def commit(self) -> None:
        self._conn.commit()

    async def rollback(self) -> None:
        self._conn.rollback()

    async def close(self) -> None:
        return None


class PluginDatabase:
    """Async-first SQLite-backed plugin database facade."""

    def __init__(
        self,
        *,
        plugin_id: str,
        plugin_dir: Path,
        logger: LoggerLike | None = None,
        enabled: bool = True,
        db_name: str | None = None,
    ):
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self.logger = logger
        self.enabled = enabled
        self.db_name = db_name or "plugin.db"
        self._db_path = self.plugin_dir / self.db_name
        self._local = threading.local()
        self._kv_store: PluginKVStore | None = None
        if self.enabled:
            self.plugin_dir.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not self.enabled:
            raise RuntimeError(f"PluginDatabase is disabled for plugin {self.plugin_id}")
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=10.0)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

    async def create_all(self) -> Result[None, Exception]:
        try:
            if self.enabled:
                self._init_db()
            return Ok(None)
        except Exception as error:
            return Err(error)

    async def drop_all(self) -> Result[None, Exception]:
        try:
            if not self.enabled:
                return Ok(None)
            if self._db_path.exists():
                self._db_path.unlink()
            conn = getattr(self._local, "conn", None)
            if conn is not None:
                self._local.conn = None
            self._kv_store = None
            return Ok(None)
        except Exception as error:
            return Err(error)

    async def close(self) -> Result[None, Exception]:
        try:
            conn = getattr(self._local, "conn", None)
            if conn is not None:
                conn.close()
                self._local.conn = None
            return Ok(None)
        except Exception as error:
            return Err(error)

    async def create_all_async(self) -> None:
        (await self.create_all()).raise_for_err()

    async def drop_all_async(self) -> None:
        (await self.drop_all()).raise_for_err()

    async def close_async(self) -> None:
        (await self.close()).raise_for_err()

    async def session(self) -> Result[AsyncSessionProtocol, Exception]:
        try:
            return Ok(_SqliteAsyncSession(self._get_conn()))
        except Exception as error:
            return Err(error)

    @property
    def kv(self) -> PluginKVStore:
        if self._kv_store is None:
            self._kv_store = PluginKVStore(database=self)
        return self._kv_store


class PluginKVStore:
    """DB-backed KV storage facade."""

    _TABLE_NAME = "_plugin_kv_store"

    def __init__(self, *, database: PluginDatabase):
        self._db = database
        self._table_created = False

    def _ensure_table(self) -> None:
        if self._table_created or not self._db.enabled:
            return
        conn = self._db._get_conn()
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._TABLE_NAME} (
                key TEXT PRIMARY KEY,
                value BLOB NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.commit()
        self._table_created = True

    def _get_sync(self, key: str, default: JsonValue | None = None) -> JsonValue | None:
        if not self._db.enabled:
            return default
        self._ensure_table()
        row = self._db._get_conn().execute(f"SELECT value FROM {self._TABLE_NAME} WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            value = _unpack(row[0])
        except Exception:
            return default
        return value if isinstance(value, (str, int, float, bool, list, dict, type(None))) else default

    def _set_sync(self, key: str, value: JsonValue) -> None:
        if not self._db.enabled:
            return
        self._ensure_table()
        now = time.time()
        conn = self._db._get_conn()
        conn.execute(
            f"""
            INSERT INTO {self._TABLE_NAME} (key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, _pack(value), now, now),
        )
        conn.commit()

    def _delete_sync(self, key: str) -> bool:
        if not self._db.enabled:
            return False
        self._ensure_table()
        cursor = self._db._get_conn().execute(f"DELETE FROM {self._TABLE_NAME} WHERE key = ?", (key,))
        self._db._get_conn().commit()
        return cursor.rowcount > 0

    def _exists_sync(self, key: str) -> bool:
        if not self._db.enabled:
            return False
        self._ensure_table()
        row = self._db._get_conn().execute(f"SELECT 1 FROM {self._TABLE_NAME} WHERE key = ?", (key,)).fetchone()
        return row is not None

    def _keys_sync(self, prefix: str = "") -> list[str]:
        if not self._db.enabled:
            return []
        self._ensure_table()
        if prefix:
            rows = self._db._get_conn().execute(f"SELECT key FROM {self._TABLE_NAME} WHERE key LIKE ? ORDER BY key", (f"{prefix}%",)).fetchall()
        else:
            rows = self._db._get_conn().execute(f"SELECT key FROM {self._TABLE_NAME} ORDER BY key").fetchall()
        return [str(row[0]) for row in rows]

    def _clear_sync(self) -> int:
        if not self._db.enabled:
            return 0
        self._ensure_table()
        cursor = self._db._get_conn().execute(f"DELETE FROM {self._TABLE_NAME}")
        self._db._get_conn().commit()
        return int(cursor.rowcount if cursor.rowcount >= 0 else 0)

    def _count_sync(self) -> int:
        if not self._db.enabled:
            return 0
        self._ensure_table()
        row = self._db._get_conn().execute(f"SELECT COUNT(*) FROM {self._TABLE_NAME}").fetchone()
        return int(row[0]) if row is not None else 0

    async def get(self, key: str, default: JsonValue | None = None) -> Result[JsonValue | None, Exception]:
        try:
            return Ok(self._get_sync(key, default))
        except Exception as error:
            return Err(error)

    async def set(self, key: str, value: JsonValue) -> Result[None, Exception]:
        try:
            self._set_sync(key, value)
            return Ok(None)
        except Exception as error:
            return Err(error)

    async def delete(self, key: str) -> Result[bool, Exception]:
        try:
            return Ok(self._delete_sync(key))
        except Exception as error:
            return Err(error)

    async def exists(self, key: str) -> Result[bool, Exception]:
        try:
            return Ok(self._exists_sync(key))
        except Exception as error:
            return Err(error)

    async def keys(self, prefix: str = "") -> Result[list[str], Exception]:
        try:
            return Ok(self._keys_sync(prefix))
        except Exception as error:
            return Err(error)

    async def clear(self) -> Result[int, Exception]:
        try:
            return Ok(self._clear_sync())
        except Exception as error:
            return Err(error)

    async def count(self) -> Result[int, Exception]:
        try:
            return Ok(self._count_sync())
        except Exception as error:
            return Err(error)

    async def get_async(self, key: str, default: JsonValue | None = None) -> JsonValue | None:
        return (await self.get(key, default)).unwrap_or(default)

    async def set_async(self, key: str, value: JsonValue) -> None:
        (await self.set(key, value)).raise_for_err()

    async def delete_async(self, key: str) -> bool:
        return (await self.delete(key)).unwrap_or(False)

    async def exists_async(self, key: str) -> bool:
        return (await self.exists(key)).unwrap_or(False)

    async def keys_async(self, prefix: str = "") -> list[str]:
        return (await self.keys(prefix)).unwrap_or([])

    async def clear_async(self) -> int:
        return (await self.clear()).unwrap_or(0)

    async def count_async(self) -> int:
        return (await self.count()).unwrap_or(0)


__all__ = ["AsyncSessionProtocol", "PluginDatabase", "PluginKVStore"]
