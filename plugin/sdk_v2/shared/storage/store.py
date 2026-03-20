"""Shared facade for plugin KV storage."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import cast

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

StoreError = InvalidArgumentError | TransportError


class PluginStore(StorageResultTemplate):
    """Async-first SQLite-backed KV store."""

    def __init__(
        self,
        *,
        plugin_id: str,
        plugin_dir: Path,
        logger: LoggerLike | None = None,
        enabled: bool = False,
        db_name: str = "store.db",
    ):
        super().__init__(logger=logger or get_plugin_logger(plugin_id, "storage.store"))
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self.enabled = enabled
        db_path = Path(db_name)
        if db_path.is_absolute() or any(part == ".." for part in db_path.parts):
            raise ValueError("db_name must stay within plugin_dir")
        safe_name = db_path.name
        if safe_name != db_name or safe_name.strip() in {"", ".", ".."}:
            raise ValueError("db_name must be a plain filename within plugin_dir")
        self.db_name = safe_name
        self._db_path = self.plugin_dir / safe_name
        self._local = threading.local()
        self._conn_lock = threading.Lock()
        self._all_conns: set[sqlite3.Connection] = set()

    @staticmethod
    def _is_json_value(value: object) -> bool:
        if value is None or isinstance(value, (str, int, float, bool)):
            return True
        if isinstance(value, list):
            return all(PluginStore._is_json_value(item) for item in value)
        if isinstance(value, dict):
            return all(isinstance(key, str) and PluginStore._is_json_value(item) for key, item in value.items())
        return False

    @staticmethod
    def _validate_key(key: str) -> Result[None, InvalidArgumentError]:
        if not isinstance(key, str) or key == "":
            return Err(InvalidArgumentError("key must be non-empty"))
        return _OK_NONE

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

    def _get_conn(self) -> sqlite3.Connection:
        if not self.enabled:
            raise RuntimeError(f"PluginStore is disabled for plugin {self.plugin_id}")
        conn = getattr(self._local, "conn", None)
        if conn is not None and not self._is_tracked_conn(conn):
            self._local.conn = None
            conn = None
        if conn is None:
            self.plugin_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=10.0)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
            self._register_conn(conn)
            self._init_db(conn)
        return conn

    def _init_db(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value BLOB NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.commit()

    def _read_value(self, key: str, default: JsonValue | None = None) -> JsonValue | None:
        if not self.enabled:
            return default
        row = self._get_conn().execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        value = _unpack(row["value"])
        return value if self._is_json_value(value) else default

    def _write_value(self, key: str, value: JsonValue) -> None:
        if not self.enabled:
            return
        if not self._is_json_value(value):
            raise TypeError("value must be JSON-compatible")
        now = time.time()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO kv_store (key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, _pack(value), now, now),
        )
        conn.commit()

    def _delete_value(self, key: str) -> bool:
        if not self.enabled:
            return False
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM kv_store WHERE key = ?", (key,))
        conn.commit()
        return cursor.rowcount > 0

    def _has_key(self, key: str) -> bool:
        if not self.enabled:
            return False
        row = self._get_conn().execute("SELECT 1 FROM kv_store WHERE key = ?", (key,)).fetchone()
        return row is not None

    def _list_keys(self, prefix: str = "") -> list[str]:
        if not self.enabled:
            return []
        if prefix:
            escaped_prefix = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = self._get_conn().execute(
                "SELECT key FROM kv_store WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
                (f"{escaped_prefix}%",),
            ).fetchall()
        else:
            rows = self._get_conn().execute("SELECT key FROM kv_store ORDER BY key").fetchall()
        return [str(row[0]) for row in rows]

    def _clear_values(self) -> int:
        if not self.enabled:
            return 0
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM kv_store")
        conn.commit()
        return int(cursor.rowcount if cursor.rowcount >= 0 else 0)

    def _count_values(self) -> int:
        if not self.enabled:
            return 0
        row = self._get_conn().execute("SELECT COUNT(*) FROM kv_store").fetchone()
        return int(row[0]) if row is not None else 0

    def _dump_values(self) -> dict[str, JsonValue]:
        if not self.enabled:
            return {}
        rows = self._get_conn().execute("SELECT key, value FROM kv_store ORDER BY key").fetchall()
        result: dict[str, JsonValue] = {}
        for row in rows:
            key = str(row[0])
            value = _unpack(row[1])
            if self._is_json_value(value):
                result[key] = value
        return result

    def _close_connection(self) -> None:
        first_error: Exception | None = None
        local_conn = getattr(self._local, "conn", None)
        if local_conn is not None:
            try:
                local_conn.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
            else:
                self._unregister_conn(local_conn)
                self._local.conn = None
        for conn in self._snapshot_conns():
            if conn is local_conn:
                continue
            try:
                conn.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
            else:
                self._unregister_conn(conn)
        if first_error is not None:
            raise first_error

    async def get(self, key: str, default: JsonValue | None = None) -> Result[JsonValue | None, StoreError]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return cast(Result[JsonValue | None, StoreError], key_ok)
        return await self._run_local_result("storage.store.get", self._read_value, key, default)

    async def set(self, key: str, value: JsonValue) -> Result[None, StoreError]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return cast(Result[None, StoreError], key_ok)
        return await self._run_local_result("storage.store.set", self._write_value, key, value)

    async def delete(self, key: str) -> Result[bool, StoreError]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return cast(Result[bool, StoreError], key_ok)
        return await self._run_local_result("storage.store.delete", self._delete_value, key)

    async def exists(self, key: str) -> Result[bool, StoreError]:
        key_ok = self._validate_key(key)
        if isinstance(key_ok, Err):
            return cast(Result[bool, StoreError], key_ok)
        return await self._run_local_result("storage.store.exists", self._has_key, key)

    async def keys(self, prefix: str = "") -> Result[list[str], TransportError]:
        return await self._run_local_result("storage.store.keys", self._list_keys, prefix)

    async def clear(self) -> Result[int, TransportError]:
        return await self._run_local_result("storage.store.clear", self._clear_values)

    async def count(self) -> Result[int, TransportError]:
        return await self._run_local_result("storage.store.count", self._count_values)

    async def dump(self) -> Result[dict[str, JsonValue], TransportError]:
        return await self._run_local_result("storage.store.dump", self._dump_values)

    async def close(self) -> Result[None, TransportError]:
        return await self._run_local_result("storage.store.close", self._close_connection)


_OK_NONE = Ok(None)

__all__ = ["PluginStore"]
