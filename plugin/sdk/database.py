"""
插件数据库管理器

提供基于 SQLAlchemy 的数据库支持，包括：
- 自动创建数据库文件
- ORM 模型定义
- 同步和异步 Session 管理
- 简单易用的 API
"""

import asyncio
from pathlib import Path
from typing import Optional, TYPE_CHECKING, Any, Union, Coroutine, overload
from contextlib import contextmanager, asynccontextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

if TYPE_CHECKING:
    from loguru import Logger as LoguruLogger


class PluginDatabase:
    """
    插件数据库管理器
    
    提供 SQLAlchemy ORM 支持，自动管理数据库文件和 Session。
    支持同步和异步操作。
    
    Usage:
        # 在插件中定义模型
        class User(self.db.Base):
            __tablename__ = 'users'
            id = Column(Integer, primary_key=True)
            name = Column(String(50))
        
        # 创建表
        self.db.create_all()
        
        # 同步操作
        with self.db.session() as session:
            user = User(name="Alice")
            session.add(user)
            session.commit()
        
        # 异步操作
        async with self.db.async_session() as session:
            result = await session.execute(select(User))
            users = result.scalars().all()
    """
    
    def __init__(
        self,
        plugin_id: str,
        plugin_dir: Path,
        logger: Optional["LoguruLogger"] = None,
        enabled: bool = True,
        db_name: Optional[str] = None,
    ):
        """
        初始化数据库管理器
        
        Args:
            plugin_id: 插件 ID
            plugin_dir: 插件目录
            logger: 日志记录器
            enabled: 是否启用数据库（默认 True）
            db_name: 数据库文件名（默认为 {plugin_id}.db）
        """
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self.logger = logger
        self.enabled = enabled
        
        # 数据库文件路径
        if db_name is None:
            db_name = f"{plugin_id}.db"
        self._db_path = self.plugin_dir / db_name
        
        # 创建 Base 类（用于定义模型）
        self.Base = declarative_base()
        
        # 初始化引擎和 Session（仅在启用时）
        self._engine = None
        self._async_engine = None
        self._SessionLocal = None
        self._AsyncSessionLocal = None
        
        if self.enabled:
            self._init_engines()
        else:
            if self.logger:
                self.logger.debug(f"[Database] PluginDatabase disabled for plugin {self.plugin_id}")
    
    def _init_engines(self) -> None:
        """初始化同步和异步引擎
        
        注意：这只是创建引擎对象，不会立即创建数据库文件。
        数据库文件会在首次连接时（调用 create_all() 或 session()）自动创建。
        """
        # 同步引擎
        sync_url = f"sqlite:///{self._db_path}"
        self._engine = create_engine(
            sync_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False,
        )
        
        # 启用 SQLite 外键约束
        @event.listens_for(self._engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        
        # 同步 Session 工厂
        self._SessionLocal = sessionmaker(
            bind=self._engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        
        # 异步引擎
        async_url = f"sqlite+aiosqlite:///{self._db_path}"
        self._async_engine = create_async_engine(
            async_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False,
        )
        
        # 异步 Session 工厂
        self._AsyncSessionLocal = async_sessionmaker(
            bind=self._async_engine,
            class_=AsyncSession,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        
        if self.logger:
            self.logger.debug(f"[Database] Initialized engines (database file will be created on first use)")
    
    def _is_in_event_loop(self) -> bool:
        """检测当前是否在事件循环中运行
        
        Returns:
            True 如果当前在事件循环中，False 如果在 worker 线程或无事件循环环境
        """
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False
    
    def _run_sync(self, coro: Coroutine) -> Any:
        """在同步上下文中运行异步协程
        
        如果在事件循环中调用会抛出异常，避免死锁。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        raise RuntimeError(
            "Cannot use sync methods inside a running event loop; use 'await xxx_async(...)' instead"
        )
    
    def create_all_sync(self) -> None:
        """
        创建所有表（同步）
        
        根据已定义的模型创建数据库表。
        如果表已存在，则不会重复创建。
        """
        if not self.enabled:
            if self.logger:
                self.logger.warning(f"[Database] Cannot create tables: database is disabled")
            return
        
        self.Base.metadata.create_all(bind=self._engine)
        if self.logger:
            self.logger.info(f"[Database] Created tables for plugin {self.plugin_id}")
    
    async def _create_all_async(self) -> None:
        """
        创建所有表（异步内部实现）
        """
        if not self.enabled:
            if self.logger:
                self.logger.warning(f"[Database] Cannot create tables: database is disabled")
            return
        
        async with self._async_engine.begin() as conn:
            await conn.run_sync(self.Base.metadata.create_all)
        
        if self.logger:
            self.logger.info(f"[Database] Created tables (async) for plugin {self.plugin_id}")
    
    @overload
    def create_all(self) -> None: ...  # 同步环境
    @overload
    def create_all(self) -> Coroutine[Any, Any, None]: ...  # 异步环境
    
    def create_all(self) -> "Union[None, Coroutine[Any, Any, None]]":
        """
        创建所有表（智能识别同步/异步）
        
        根据当前执行环境自动选择同步或异步执行。
        在事件循环中返回 Coroutine，否则同步执行。
        
        Usage:
            # 在同步函数中
            self.db.create_all()
            
            # 在异步函数中
            await self.db.create_all()
        """
        coro = self._create_all_async()
        if self._is_in_event_loop():
            return coro
        return self._run_sync(coro)
    
    def drop_all_sync(self) -> None:
        """
        删除所有表（同步）
        
        警告：这会删除所有数据！
        """
        if not self.enabled:
            if self.logger:
                self.logger.warning(f"[Database] Cannot drop tables: database is disabled")
            return
        
        self.Base.metadata.drop_all(bind=self._engine)
        if self.logger:
            self.logger.warning(f"[Database] Dropped all tables for plugin {self.plugin_id}")
    
    async def _drop_all_async(self) -> None:
        """
        删除所有表（异步内部实现）
        """
        if not self.enabled:
            if self.logger:
                self.logger.warning(f"[Database] Cannot drop tables: database is disabled")
            return
        
        async with self._async_engine.begin() as conn:
            await conn.run_sync(self.Base.metadata.drop_all)
        
        if self.logger:
            self.logger.warning(f"[Database] Dropped all tables (async) for plugin {self.plugin_id}")
    
    @overload
    def drop_all(self) -> None: ...  # 同步环境
    @overload
    def drop_all(self) -> Coroutine[Any, Any, None]: ...  # 异步环境
    
    def drop_all(self) -> "Union[None, Coroutine[Any, Any, None]]":
        """
        删除所有表（智能识别同步/异步）
        
        警告：这会删除所有数据！
        
        根据当前执行环境自动选择同步或异步执行。
        
        Usage:
            # 在同步函数中
            self.db.drop_all()
            
            # 在异步函数中
            await self.db.drop_all()
        """
        coro = self._drop_all_async()
        if self._is_in_event_loop():
            return coro
        return self._run_sync(coro)
    
    @contextmanager
    def session(self):
        """
        获取同步数据库 Session（上下文管理器）
        
        Usage:
            with self.db.session() as session:
                user = User(name="Alice")
                session.add(user)
                session.commit()
        
        Yields:
            Session: SQLAlchemy Session 对象
        """
        if not self.enabled:
            raise RuntimeError(f"PluginDatabase is disabled for plugin {self.plugin_id}")
        
        session: Session = self._SessionLocal()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    
    @asynccontextmanager
    async def async_session(self):
        """
        获取异步数据库 Session（异步上下文管理器）
        
        Usage:
            async with self.db.async_session() as session:
                result = await session.execute(select(User))
                users = result.scalars().all()
        
        Yields:
            AsyncSession: SQLAlchemy AsyncSession 对象
        """
        if not self.enabled:
            raise RuntimeError(f"PluginDatabase is disabled for plugin {self.plugin_id}")
        
        session: AsyncSession = self._AsyncSessionLocal()
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
    
    def get_session(self) -> Session:
        """
        获取同步 Session（需要手动管理）
        
        注意：使用此方法需要手动调用 session.close()
        推荐使用 session() 上下文管理器。
        
        Returns:
            Session: SQLAlchemy Session 对象
        """
        if not self.enabled:
            raise RuntimeError(f"PluginDatabase is disabled for plugin {self.plugin_id}")
        
        return self._SessionLocal()
    
    def get_async_session(self) -> AsyncSession:
        """
        获取异步 Session（需要手动管理）
        
        注意：使用此方法需要手动调用 await session.close()
        推荐使用 async_session() 异步上下文管理器。
        
        Returns:
            AsyncSession: SQLAlchemy AsyncSession 对象
        """
        if not self.enabled:
            raise RuntimeError(f"PluginDatabase is disabled for plugin {self.plugin_id}")
        
        return self._AsyncSessionLocal()
    
    def close(self) -> None:
        """
        关闭数据库连接
        
        清理所有引擎和连接池。
        """
        if self._engine:
            self._engine.dispose()
            self._engine = None
        
        if self._async_engine:
            # 异步引擎需要在事件循环中关闭
            # 这里只是标记为 None，实际关闭由 GC 处理
            self._async_engine = None
        
        if self.logger:
            self.logger.debug(f"[Database] Closed database for plugin {self.plugin_id}")
    
    async def close_async(self) -> None:
        """
        异步关闭数据库连接
        
        清理所有引擎和连接池。
        """
        if self._engine:
            self._engine.dispose()
            self._engine = None
        
        if self._async_engine:
            await self._async_engine.dispose()
            self._async_engine = None
        
        if self.logger:
            self.logger.debug(f"[Database] Closed database (async) for plugin {self.plugin_id}")
    
    @property
    def engine(self):
        """获取同步引擎"""
        if not self.enabled:
            raise RuntimeError(f"PluginDatabase is disabled for plugin {self.plugin_id}")
        return self._engine
    
    @property
    def async_engine(self):
        """获取异步引擎"""
        if not self.enabled:
            raise RuntimeError(f"PluginDatabase is disabled for plugin {self.plugin_id}")
        return self._async_engine
    
    @property
    def db_path(self) -> Path:
        """获取数据库文件路径"""
        return self._db_path
    
    @property
    def db_exists(self) -> bool:
        """检查数据库文件是否存在"""
        return self._db_path.exists()
    
    # ========== KV 存储接口 ==========
    
    @property
    def kv(self) -> "PluginKVStore":
        """获取 KV 存储接口
        
        提供类似 localStorage 的简单 KV 存储，基于同一个 SQLite 数据库。
        
        Usage:
            # 基本操作
            self.db.kv.set("key", {"data": 123})
            value = self.db.kv.get("key")
            self.db.kv.delete("key")
            
            # 便捷语法
            self.db.kv["key"] = {"data": 123}
            value = self.db.kv["key"]
            del self.db.kv["key"]
        
        Returns:
            PluginKVStore: KV 存储接口
        """
        if not hasattr(self, "_kv_store") or self._kv_store is None:
            self._kv_store = PluginKVStore(self)
        return self._kv_store


class PluginKVStore:
    """
    基于 PluginDatabase 的 KV 存储接口
    
    提供类似 localStorage 的简单 API，数据存储在同一个 SQLite 数据库中。
    线程安全，支持并发访问。
    
    Note:
        此类是 PluginDatabase 的一部分，通过 db.kv 访问。
        如需独立的 KV 存储，请使用 PluginStore。
    """
    
    # KV 表名
    _TABLE_NAME = "_plugin_kv_store"
    
    def __init__(self, db: PluginDatabase):
        self._db = db
        self._table_created = False
    
    def _ensure_table(self) -> None:
        """确保 KV 表已创建"""
        if self._table_created:
            return
        if not self._db.enabled:
            return
        
        with self._db.session() as session:
            session.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {self._TABLE_NAME} (
                    key TEXT PRIMARY KEY,
                    value BLOB NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """))
            session.commit()
        self._table_created = True
    
    def _serialize(self, value: Any) -> bytes:
        """序列化值"""
        try:
            import ormsgpack as msgpack
            return msgpack.packb(value)
        except ImportError:
            import msgpack
            return msgpack.packb(value, use_bin_type=True)
    
    def _deserialize(self, data: bytes) -> Any:
        """反序列化值"""
        try:
            import ormsgpack as msgpack
            return msgpack.unpackb(data)
        except ImportError:
            import msgpack
            return msgpack.unpackb(data, raw=False)
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取值"""
        if not self._db.enabled:
            return default
        self._ensure_table()
        
        with self._db.session() as session:
            result = session.execute(
                text(f"SELECT value FROM {self._TABLE_NAME} WHERE key = :key"),
                {"key": key}
            ).fetchone()
            if result is None:
                return default
            try:
                return self._deserialize(result[0])
            except Exception:
                return default
    
    def set(self, key: str, value: Any) -> None:
        """设置值"""
        if not self._db.enabled:
            return
        self._ensure_table()
        
        import time
        now = time.time()
        data = self._serialize(value)
        
        with self._db.session() as session:
            session.execute(
                text(f"""
                    INSERT INTO {self._TABLE_NAME} (key, value, created_at, updated_at)
                    VALUES (:key, :value, :now, :now)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                """),
                {"key": key, "value": data, "now": now}
            )
            session.commit()
    
    def delete(self, key: str) -> bool:
        """删除键"""
        if not self._db.enabled:
            return False
        self._ensure_table()
        
        with self._db.session() as session:
            # 先检查是否存在
            exists = session.execute(
                text(f"SELECT 1 FROM {self._TABLE_NAME} WHERE key = :key"),
                {"key": key}
            ).fetchone() is not None
            if exists:
                session.execute(
                    text(f"DELETE FROM {self._TABLE_NAME} WHERE key = :key"),
                    {"key": key}
                )
                session.commit()
            return exists
    
    def exists(self, key: str) -> bool:
        """检查键是否存在"""
        if not self._db.enabled:
            return False
        self._ensure_table()
        
        with self._db.session() as session:
            result = session.execute(
                text(f"SELECT 1 FROM {self._TABLE_NAME} WHERE key = :key"),
                {"key": key}
            ).fetchone()
            return result is not None
    
    def keys(self, prefix: str = "") -> list:
        """获取所有键"""
        if not self._db.enabled:
            return []
        self._ensure_table()
        
        with self._db.session() as session:
            if prefix:
                result = session.execute(
                    text(f"SELECT key FROM {self._TABLE_NAME} WHERE key LIKE :prefix"),
                    {"prefix": prefix + "%"}
                ).fetchall()
            else:
                result = session.execute(
                    text(f"SELECT key FROM {self._TABLE_NAME}")
                ).fetchall()
            return [row[0] for row in result]
    
    def clear(self) -> int:
        """清空所有数据"""
        if not self._db.enabled:
            return 0
        self._ensure_table()
        
        with self._db.session() as session:
            # 先获取数量
            count = session.execute(
                text(f"SELECT COUNT(*) FROM {self._TABLE_NAME}")
            ).scalar() or 0
            session.execute(text(f"DELETE FROM {self._TABLE_NAME}"))
            session.commit()
            return count
    
    def count(self) -> int:
        """获取记录数"""
        if not self._db.enabled:
            return 0
        self._ensure_table()
        
        with self._db.session() as session:
            result = session.execute(
                text(f"SELECT COUNT(*) FROM {self._TABLE_NAME}")
            ).fetchone()
            return result[0] if result else 0
    
    # 便捷语法
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
