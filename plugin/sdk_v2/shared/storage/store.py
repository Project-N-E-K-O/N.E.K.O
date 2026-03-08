"""KV store implementation for SDK v2 shared storage."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

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


class PluginStore:
    """Async-first SQLite-backed KV store."""

    def __init__(
        self,
        *,
        plugin_id: str,
        plugin_dir: Path,
        logger: LoggerLike | None = None,
        enabled: bool = True,
        db_name: str = "store.db",
    ):
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self.logger = logger
        self.enabled = enabled
        self._db_path = self.plugin_dir / db_name
        self._local = threading.local()
        if self.enabled:
            self.plugin_dir.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not self.enabled:
            raise RuntimeError(f"PluginStore is disabled for plugin {self.plugin_id}")
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=10.0)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
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

    def _get_sync(self, key: str, default: JsonValue | None = None) -> JsonValue | None:
        if not self.enabled:
            return default
        row = self._get_conn().execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            value = _unpack(row["value"])
        except Exception:
            return default
        return value if isinstance(value, (str, int, float, bool, list, dict, type(None))) else default

    def _set_sync(self, key: str, value: JsonValue) -> None:
        if not self.enabled:
            return
        now = time.time()
        self._get_conn().execute(
            """
            INSERT INTO kv_store (key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, _pack(value), now, now),
        )
        self._get_conn().commit()

    def _delete_sync(self, key: str) -> bool:
        if not self.enabled:
            return False
        cursor = self._get_conn().execute("DELETE FROM kv_store WHERE key = ?", (key,))
        self._get_conn().commit()
        return cursor.rowcount > 0

    def _exists_sync(self, key: str) -> bool:
        if not self.enabled:
            return False
        row = self._get_conn().execute("SELECT 1 FROM kv_store WHERE key = ?", (key,)).fetchone()
        return row is not None

    def _keys_sync(self, prefix: str = "") -> list[str]:
        if not self.enabled:
            return []
        if prefix:
            rows = self._get_conn().execute("SELECT key FROM kv_store WHERE key LIKE ? ORDER BY key", (f"{prefix}%",)).fetchall()
        else:
            rows = self._get_conn().execute("SELECT key FROM kv_store ORDER BY key").fetchall()
        return [str(row[0]) for row in rows]

    def _clear_sync(self) -> int:
        if not self.enabled:
            return 0
        cursor = self._get_conn().execute("DELETE FROM kv_store")
        self._get_conn().commit()
        return int(cursor.rowcount if cursor.rowcount >= 0 else 0)

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


__all__ = ["PluginStore"]
