"""Shared facade for plugin database storage."""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

from plugin.sdk_v2.shared.core._facade import AsyncResultFacadeTemplate
from plugin.sdk_v2.shared.core.types import JsonValue, LoggerLike
from plugin.sdk_v2.shared.logging import get_plugin_logger
from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import InvalidArgumentError, TransportError

from ._template import StorageResultTemplate

try:
    import ormsgpack as _msgpack  # type: ignore

    def _pack(value: object) -> bytes:
        return cast(bytes, _msgpack.packb(value))

    def _unpack(data: bytes) -> object:
        return _msgpack.unpackb(data)
except ImportError:  # pragma: no cover
    import msgpack as _msgpack  # type: ignore

    def _pack(value: object) -> bytes:
        return cast(bytes, _msgpack.packb(value, use_bin_type=True))

    def _unpack(data: bytes) -> object:
        return _msgpack.unpackb(data, raw=False)

DatabaseError = InvalidArgumentError | TransportError


class AsyncSessionProtocol(Protocol):
    """Low-level async wrapper over a dedicated SQLite connection."""

    async def execute(self, statement: object, parameters: object | None = None) -> object: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def close(self) -> None: ...
    async def __aenter__(self) -> "AsyncSessionProtocol": ...
    async def __aexit__(self, exc_type: object | None, exc: object | None, tb: object | None) -> None: ...


class _SqliteAsyncSession:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        on_close: Callable[["_SqliteAsyncSession"], None] | None = None,
        on_close_conn: Callable[[sqlite3.Connection], None] | None = None,
    ):
        self._conn = conn
        self._on_close = on_close
        self._on_close_conn = on_close_conn
        self._closed = False

    async def execute(self, statement: object, parameters: object | None = None) -> object:
        sql = str(statement)
        params = parameters if isinstance(parameters, (tuple, list, dict)) or parameters is None else (parameters,)
        return await asyncio.to_thread(self._conn.execute, sql, params or ())

    async def commit(self) -> None:
        await asyncio.to_thread(self._conn.commit)

    async def rollback(self) -> None:
        await asyncio.to_thread(self._conn.rollback)

    async def close(self) -> None:
        if self._closed:
            return
        await asyncio.to_thread(self._conn.close)
        self._closed = True
        if self._on_close_conn is not None:
            self._on_close_conn(self._conn)
        if self._on_close is not None:
            self._on_close(self)

    async def __aenter__(self) -> "_SqliteAsyncSession":
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.close()


class PluginKVStore:
    """DB-backed KV storage facade."""

    _TABLE_NAME = "_plugin_kv_store"

    def __init__(self, *, database: "PluginDatabase"):
        self._db = database
        self._table_created = False

    @staticmethod
    def _validate_key(key: str) -> Result[None, InvalidArgumentError]:
        if not isinstance(key, str) or key == "":
            return Err(InvalidArgumentError("key must be non-empty"))
        return _OK_NONE

    @classmethod
    def _is_json_compatible(cls, value: object) -> bool:
        if value is None or isinstance(value, (str, int, float, bool)):
            return True
        if isinstance(value, list):
            return all(cls._is_json_compatible(item) for item in value)
        if isinstance(value, dict):
            return all(isinstance(key, str) and cls._is_json_compatible(item) for key, item in value.items())
        return False

    @classmethod
    def _validate_value(cls, value: object) -> Result[None, InvalidArgumentError]:
        if cls._is_json_compatible(value):
            return _OK_NONE
        return Err(InvalidArgumentError("value must be JSON-compatible"))

    async def _run_local_result_threaded(self, operation: str, call, /, *args) -> Result:
        try:
            if asyncio.iscoroutinefunction(call):
                raise TypeError("_run_local_result requires a synchronous callable; coroutine functions are not supported by asyncio.to_thread")
            result = await asyncio.to_thread(call, *args)
            if inspect.isawaitable(result):
                raise TypeError("_run_local_result requires a synchronous callable; coroutine results are not supported by asyncio.to_thread")
            return Ok(result)
        except Exception as error:
            self._db._log_failure(operation, error)
            normalized = error if isinstance(error, (InvalidArgumentError, TransportError)) else TransportError(str(error), op_name=operation)
            return Err(normalized)

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

    def _read_value(self, key: str, default: JsonValue | None = None) -> JsonValue | None:
        if not self._db.enabled:
            return default
        self._ensure_table()
        row = self._db._get_conn().execute(f"SELECT value FROM {self._TABLE_NAME} WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        value = _unpack(row[0])
        return cast(JsonValue | None, value if self._is_json_compatible(value) else default)

    def _write_value(self, key: str, value: JsonValue) -> None:
        value_ok = self._validate_value(value)
        if isinstance(value_ok, Err):
            raise value_ok.error
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

    def _delete_value(self, key: str) -> bool:
        if not self._db.enabled:
            return False
        self._ensure_table()
        conn = self._db._get_conn()
        cursor = conn.execute(f"DELETE FROM {self._TABLE_NAME} WHERE key = ?", (key,))
        conn.commit()
        return cursor.rowcount > 0

    def _has_key(self, key: str) -> bool:
        if not self._db.enabled:
            return False
        self._ensure_table()
        row = self._db._get_conn().execute(f"SELECT 1 FROM {self._TABLE_NAME} WHERE key = ?", (key,)).fetchone()
        return row is not None

    def _list_keys(self, prefix: str = "") -> list[str]:
        if not self._db.enabled:
            return []
        self._ensure_table()
        if prefix:
            escaped_prefix = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = self._db._get_conn().execute(
                f"SELECT key FROM {self._TABLE_NAME} WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
                (f"{escaped_prefix}%",),
            ).fetchall()
        else:
            rows = self._db._get_conn().execute(f"SELECT key FROM {self._TABLE_NAME} ORDER BY key").fetchall()
        return [str(row[0]) for row in rows]

    def _clear_values(self) -> int:
        if not self._db.enabled:
            return 0
        self._ensure_table()
        conn = self._db._get_conn()
        cursor = conn.execute(f"DELETE FROM {self._TABLE_NAME}")
        conn.commit()
        return int(cursor.rowcount if cursor.rowcount >= 0 else 0)

    def _count_values(self) -> int:
        if not self._db.enabled:
            return 0
        self._ensure_table()
        row = self._db._get_conn().execute(f"SELECT COUNT(*) FROM {self._TABLE_NAME}").fetchone()
        return int(row[0]) if row is not None else 0

    async def get(self, key: str, default: JsonValue | None = None) -> Result[JsonValue | None, DatabaseError]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return cast(Result[JsonValue | None, DatabaseError], key_ok)
        return await self._run_local_result_threaded("storage.database.kv.get", self._read_value, key, default)

    async def set(self, key: str, value: JsonValue) -> Result[None, DatabaseError]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return cast(Result[None, DatabaseError], key_ok)
        value_ok = self._validate_value(value)
        if isinstance(value_ok, Err):
            return cast(Result[None, DatabaseError], value_ok)
        return await self._run_local_result_threaded("storage.database.kv.set", self._write_value, key, value)

    async def delete(self, key: str) -> Result[bool, DatabaseError]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return cast(Result[bool, DatabaseError], key_ok)
        return await self._run_local_result_threaded("storage.database.kv.delete", self._delete_value, key)

    async def exists(self, key: str) -> Result[bool, DatabaseError]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return cast(Result[bool, DatabaseError], key_ok)
        return await self._run_local_result_threaded("storage.database.kv.exists", self._has_key, key)

    async def keys(self, prefix: str = "") -> Result[list[str], TransportError]:
        return await self._run_local_result_threaded("storage.database.kv.keys", self._list_keys, prefix)

    async def clear(self) -> Result[int, TransportError]:
        return await self._run_local_result_threaded("storage.database.kv.clear", self._clear_values)

    async def count(self) -> Result[int, TransportError]:
        return await self._run_local_result_threaded("storage.database.kv.count", self._count_values)


class PluginDatabase(StorageResultTemplate):
    """Async-first SQLite-backed plugin database facade."""

    def __init__(
        self,
        *,
        plugin_id: str,
        plugin_dir: Path,
        logger: LoggerLike | None = None,
        enabled: bool = False,
        db_name: str | None = None,
    ):
        super().__init__(logger=logger or get_plugin_logger(plugin_id, "storage.database"))
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self.enabled = enabled
        raw_db_name = db_name or "plugin.db"
        db_path = Path(raw_db_name)
        if db_path.is_absolute() or any(part == ".." for part in db_path.parts):
            raise ValueError("db_name must stay within plugin_dir")
        safe_name = db_path.name
        if safe_name.strip() in {"", ".", ".."}:
            raise ValueError("db_name must be a valid filename")
        self.db_name = safe_name
        self._db_path = self.plugin_dir / safe_name
        self._local = threading.local()
        self._kv_store: PluginKVStore | None = None
        self._all_conns: set[sqlite3.Connection] = set()
        self._conn_lock = threading.Lock()
        self._active_sessions: set[_SqliteAsyncSession] = set()
        self._session_lock = threading.Lock()

    def _reset_kv_store(self) -> None:
        kv_store = self._kv_store
        if kv_store is None:
            return
        if hasattr(kv_store, "_table_created"):
            kv_store._table_created = False
        if hasattr(kv_store, "_conn"):
            kv_store._conn = None
        if hasattr(kv_store, "_initialized"):
            kv_store._initialized = False

    def _connect(self) -> sqlite3.Connection:
        if not self.enabled:
            raise RuntimeError(f"PluginDatabase is disabled for plugin {self.plugin_id}")
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=10.0)
        try:
            conn.row_factory = sqlite3.Row
            self._init_db(conn)
        except Exception:
            conn.close()
            raise
        self._register_conn(conn)
        return conn

    def _register_conn(self, conn: sqlite3.Connection) -> None:
        with self._conn_lock:
            self._all_conns.add(conn)

    def _unregister_conn(self, conn: object) -> None:
        with self._conn_lock:
            self._all_conns.discard(cast(sqlite3.Connection, conn))

    def _snapshot_conns(self) -> list[sqlite3.Connection]:
        with self._conn_lock:
            return list(self._all_conns)

    def _is_tracked_conn(self, conn: object) -> bool:
        with self._conn_lock:
            return conn in self._all_conns

    def _close_one_conn(self, conn: sqlite3.Connection) -> None:
        conn.close()
        self._unregister_conn(conn)
        if getattr(self._local, "conn", None) is conn:
            self._local.conn = None

    def _close_all_connections(self) -> None:
        first_error: Exception | None = None
        closed_ids: set[int] = set()
        local_conn = getattr(self._local, "conn", None)
        if local_conn is not None:
            try:
                self._close_one_conn(local_conn)
            except Exception as error:
                if first_error is None:
                    first_error = error
            closed_ids.add(id(local_conn))
        for conn in self._snapshot_conns():
            if id(conn) in closed_ids:
                continue
            try:
                self._close_one_conn(conn)
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def _register_session(self, session: _SqliteAsyncSession) -> None:
        with self._session_lock:
            self._active_sessions.add(session)

    def _unregister_session(self, session: _SqliteAsyncSession) -> None:
        with self._session_lock:
            self._active_sessions.discard(session)

    def _snapshot_active_sessions(self) -> list[_SqliteAsyncSession]:
        with self._session_lock:
            return list(self._active_sessions)

    def _create_session(self) -> AsyncSessionProtocol:
        session = _SqliteAsyncSession(
            self._connect(),
            on_close=self._unregister_session,
            on_close_conn=self._unregister_conn,
        )
        self._register_session(session)
        return session

    def _get_conn(self) -> sqlite3.Connection:
        if not self.enabled:
            raise RuntimeError(f"PluginDatabase is disabled for plugin {self.plugin_id}")
        conn = getattr(self._local, "conn", None)
        if conn is not None and not self._is_tracked_conn(conn):
            self._local.conn = None
            conn = None
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def _init_db(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

    def _create_schema(self) -> None:
        if self.enabled:
            self._get_conn()

    def _drop_schema(self) -> None:
        if not self.enabled:
            return
        self._close_all_connections()
        if self._db_path.exists():
            self._db_path.unlink()
        self._reset_kv_store()
        self._kv_store = None

    def _close_connection(self) -> None:
        self._close_all_connections()
        self._reset_kv_store()

    async def _close_active_sessions(self) -> None:
        first_error: Exception | None = None
        for session in self._snapshot_active_sessions():
            try:
                await session.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    async def _close_active_sessions_result(self, operation: str) -> Result[None, TransportError]:
        try:
            await self._close_active_sessions()
            return Ok(None)
        except Exception as error:
            self._log_failure(operation, error)
            normalized = error if isinstance(error, TransportError) else TransportError(str(error), op_name=operation)
            return Err(normalized)

    async def create_all(self) -> Result[None, TransportError]:
        return await self._run_local_result("storage.database.create_all", self._create_schema)

    async def drop_all(self) -> Result[None, TransportError]:
        cleanup = await self._close_active_sessions_result("storage.database.drop_all.close_sessions")
        if isinstance(cleanup, Err):
            return cleanup
        return await self._run_local_result("storage.database.drop_all", self._drop_schema)

    async def close(self) -> Result[None, TransportError]:
        cleanup = await self._close_active_sessions_result("storage.database.close.close_sessions")
        if isinstance(cleanup, Err):
            return cleanup
        return await self._run_local_result("storage.database.close", self._close_connection)

    async def session(self) -> Result[AsyncSessionProtocol, TransportError]:
        return await self._run_local_result(
            "storage.database.session",
            self._create_session,
        )

    @property
    def kv(self) -> PluginKVStore:
        if self._kv_store is None:
            self._kv_store = PluginKVStore(database=self)
        return self._kv_store


_OK_NONE = Ok(None)

__all__ = ["AsyncSessionProtocol", "PluginDatabase", "PluginKVStore"]
