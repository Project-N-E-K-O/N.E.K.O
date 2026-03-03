"""
插件持久化 KV 存储

基于 SQLite 的轻量级键值存储，类似 localStorage。
"""

import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

try:
    import ormsgpack as msgpack
    _USE_ORMSGPACK = True
except ImportError:
    import msgpack
    _USE_ORMSGPACK = False

if TYPE_CHECKING:
    from loguru import Logger as LoguruLogger


class PluginStore:
    """
    插件持久化 KV 存储
    
    基于 SQLite 实现，提供类似 localStorage 的简单 API。
    线程安全，支持并发访问。
    
    Usage:
        store = PluginStore(plugin_id, plugin_dir)
        
        # 基本操作
        store.set("key", {"data": 123})
        value = store.get("key")
        store.delete("key")
        
        # 便捷语法
        store["key"] = {"data": 123}
        value = store["key"]
        del store["key"]
    """
    
    def __init__(
        self,
        plugin_id: str,
        plugin_dir: Path,
        logger: Optional["LoguruLogger"] = None,
        enabled: bool = True,
    ):
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self.logger = logger
        self.enabled = enabled
        
        # 数据库文件路径
        self._db_path = self.plugin_dir / "store.db"
        
        # 线程本地连接（每个线程一个连接）
        self._local = threading.local()
        
        # 初始化数据库（仅在启用时）
        if self.enabled:
            self._init_db()
        else:
            if self.logger:
                self.logger.debug(f"[Store] PluginStore disabled for plugin {self.plugin_id}")
    
    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接"""
        if not self.enabled:
            raise RuntimeError(f"PluginStore is disabled for plugin {self.plugin_id}")
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                timeout=10.0,
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn
    
    def _init_db(self) -> None:
        """初始化数据库表"""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value BLOB NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.commit()
    
    def _serialize(self, value: Any) -> bytes:
        """序列化值"""
        if _USE_ORMSGPACK:
            return msgpack.packb(value)
        return msgpack.packb(value, use_bin_type=True)
    
    def _deserialize(self, data: bytes) -> Any:
        """反序列化值"""
        if _USE_ORMSGPACK:
            return msgpack.unpackb(data)
        return msgpack.unpackb(data, raw=False)
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取值
        
        Args:
            key: 键名
            default: 默认值（如果键不存在）
        
        Returns:
            存储的值，如果不存在则返回 default
        """
        if not self.enabled:
            return default
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT value FROM kv_store WHERE key = ?",
            (key,)
        )
        row = cursor.fetchone()
        if row is None:
            return default
        try:
            return self._deserialize(row["value"])
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[Store] Failed to deserialize key '{key}': {e}")
            return default
    
    def set(self, key: str, value: Any) -> None:
        """
        设置值
        
        Args:
            key: 键名
            value: 值（必须可序列化）
        """
        if not self.enabled:
            if self.logger:
                self.logger.warning(f"[Store] Attempted to set key '{key}' but store is disabled")
            return
        import time
        
        conn = self._get_conn()
        now = time.time()
        data = self._serialize(value)
        
        conn.execute("""
            INSERT INTO kv_store (key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
        """, (key, data, now, now))
        conn.commit()
    
    def delete(self, key: str) -> bool:
        """
        删除键
        
        Args:
            key: 键名
        
        Returns:
            True 如果删除成功，False 如果键不存在
        """
        if not self.enabled:
            return False
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM kv_store WHERE key = ?",
            (key,)
        )
        conn.commit()
        return cursor.rowcount > 0
    
    def exists(self, key: str) -> bool:
        """
        检查键是否存在
        
        Args:
            key: 键名
        
        Returns:
            True 如果存在
        """
        if not self.enabled:
            return False
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT 1 FROM kv_store WHERE key = ?",
            (key,)
        )
        return cursor.fetchone() is not None
    
    def keys(self, prefix: str = "") -> List[str]:
        """
        获取所有键（可选前缀过滤）
        
        Args:
            prefix: 键名前缀（可选）
        
        Returns:
            键名列表
        """
        if not self.enabled:
            return []
        conn = self._get_conn()
        if prefix:
            cursor = conn.execute(
                "SELECT key FROM kv_store WHERE key LIKE ?",
                (prefix + "%",)
            )
        else:
            cursor = conn.execute("SELECT key FROM kv_store")
        return [row["key"] for row in cursor.fetchall()]
    
    def clear(self) -> int:
        """
        清空所有数据
        
        Returns:
            删除的记录数
        """
        if not self.enabled:
            return 0
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM kv_store")
        conn.commit()
        return cursor.rowcount
    
    def count(self) -> int:
        """
        获取记录数
        
        Returns:
            记录数量
        """
        if not self.enabled:
            return 0
        conn = self._get_conn()
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM kv_store")
        row = cursor.fetchone()
        return row["cnt"] if row else 0
    
    def dump(self) -> Dict[str, Any]:
        """
        导出所有数据
        
        Returns:
            所有键值对的字典
        """
        if not self.enabled:
            return {}
        conn = self._get_conn()
        cursor = conn.execute("SELECT key, value FROM kv_store")
        result = {}
        for row in cursor.fetchall():
            try:
                result[row["key"]] = self._deserialize(row["value"])
            except Exception:
                pass
        return result
    
    # 便捷语法支持
    def __getitem__(self, key: str) -> Any:
        value = self.get(key)
        if value is None and not self.exists(key):
            raise KeyError(key)
        return value
    
    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)
    
    def __delitem__(self, key: str) -> None:
        if not self.delete(key):
            raise KeyError(key)
    
    def __contains__(self, key: str) -> bool:
        return self.exists(key)
    
    def __len__(self) -> int:
        return self.count()
    
    def close(self) -> None:
        """关闭数据库连接"""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None
