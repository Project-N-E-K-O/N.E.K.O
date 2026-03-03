"""
插件运行时状态模块

提供插件系统的全局运行时状态管理。
"""
import asyncio
import itertools
import threading
import time
import uuid
from collections import deque
import multiprocessing
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple, cast

from loguru import logger

from plugin._types.events import EventHandler
from plugin.settings import (
    BUS_SDK_POLL_INTERVAL_SECONDS,
    EVENT_QUEUE_MAX,
    LIFECYCLE_QUEUE_MAX,
    MESSAGE_PLANE_ZMQ_RPC_ENDPOINT,
    MESSAGE_QUEUE_MAX,
)

try:
    import zmq
except Exception:  # pragma: no cover
    zmq = None

try:
    import ormsgpack
except Exception:  # pragma: no cover
    ormsgpack = None

from plugin.sdk.message_plane_transport import format_rpc_error
from contextlib import contextmanager


MAX_DELETED_BUS_IDS = 20000

# 默认锁超时时间（秒）
DEFAULT_LOCK_TIMEOUT = 10.0


class RWLock:
    """
    读写锁实现
    
    允许多个读操作并发执行，但写操作是互斥的。
    写操作会等待所有读操作完成，读操作会等待写操作完成。
    """
    
    def __init__(self):
        self._read_ready = threading.Condition(threading.Lock())
        self._readers = 0
        self._writers_waiting = 0
        self._writer_active = False
    
    def acquire_read(self, timeout: float = DEFAULT_LOCK_TIMEOUT) -> bool:
        """获取读锁"""
        deadline = time.time() + timeout
        with self._read_ready:
            while self._writer_active or self._writers_waiting > 0:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                if not self._read_ready.wait(timeout=remaining):
                    return False
            self._readers += 1
        return True
    
    def release_read(self):
        """释放读锁"""
        with self._read_ready:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()
    
    def acquire_write(self, timeout: float = DEFAULT_LOCK_TIMEOUT) -> bool:
        """获取写锁"""
        deadline = time.time() + timeout
        with self._read_ready:
            self._writers_waiting += 1
            try:
                while self._readers > 0 or self._writer_active:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return False
                    if not self._read_ready.wait(timeout=remaining):
                        return False
                self._writer_active = True
            finally:
                self._writers_waiting -= 1
        return True
    
    def release_write(self):
        """释放写锁"""
        with self._read_ready:
            self._writer_active = False
            self._read_ready.notify_all()
    
    @contextmanager
    def read_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT, name: str = "unknown"):
        """读锁上下文管理器"""
        if not self.acquire_read(timeout):
            logger.error(f"Failed to acquire read lock '{name}' within {timeout}s")
            raise TimeoutError(f"Failed to acquire read lock '{name}' within {timeout}s")
        try:
            yield
        finally:
            self.release_read()
    
    @contextmanager
    def write_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT, name: str = "unknown"):
        """写锁上下文管理器"""
        if not self.acquire_write(timeout):
            logger.error(f"Failed to acquire write lock '{name}' within {timeout}s")
            raise TimeoutError(f"Failed to acquire write lock '{name}' within {timeout}s")
        try:
            yield
        finally:
            self.release_write()


@contextmanager
def timed_lock(lock: threading.Lock, timeout: float = DEFAULT_LOCK_TIMEOUT, name: str = "unknown"):
    """
    带超时的锁获取上下文管理器
    
    Args:
        lock: 要获取的锁
        timeout: 超时时间（秒）
        name: 锁名称（用于日志）
    
    Raises:
        TimeoutError: 如果在超时时间内无法获取锁
    """
    acquired = lock.acquire(timeout=timeout)
    if not acquired:
        logger.error(
            "Failed to acquire lock '{}' within {}s - possible deadlock detected",
            name, timeout
        )
        raise TimeoutError(f"Failed to acquire lock '{name}' within {timeout}s")
    try:
        yield
    finally:
        lock.release()


class BusChangeHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: Dict[str, Dict[int, Callable[[str, Dict[str, Any]], None]]] = {}
        self._next_id = 1

    def subscribe(self, bus: str, cb: Callable[[str, Dict[str, Any]], None]) -> Callable[[], None]:
        b = str(bus).strip()
        if not b:
            raise ValueError("bus is required")
        if not callable(cb):
            raise TypeError("callback must be callable")
        with self._lock:
            sid = int(self._next_id)
            self._next_id += 1
            d = self._subs.setdefault(b, {})
            d[sid] = cb

        def _unsub() -> None:
            with self._lock:
                m = self._subs.get(b)
                if not m:
                    return
                m.pop(sid, None)
                if not m:
                    self._subs.pop(b, None)

        return _unsub

    def emit(self, bus: str, op: str, payload: Dict[str, Any]) -> None:
        b = str(bus).strip()
        if not b:
            return
        with self._lock:
            subs = list((self._subs.get(b) or {}).values())
        if not subs:
            return
        for cb in subs:
            try:
                cb(str(op), dict(payload or {}))
            except Exception:
                logger.bind(component="server").debug(
                    f"BusChangeHub callback failed for bus={b}", exc_info=True
                )
                continue


class GlobalState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        
        # 内存 freeze 存储
        self._frozen_states: Dict[str, bytes] = {}  # plugin_id -> serialized state
        self._frozen_states_lock = threading.Lock()
        
        # 冻结插件跟踪（记录哪些插件处于冻结状态）
        self._frozen_plugins: Set[str] = set()  # plugin_id set
        self._frozen_plugins_lock = threading.Lock()
        
        self._subs: Dict[str, Dict[int, Callable[[str, Dict[str, Any]], None]]] = {
            "messages": {},
            "events": {},
            "lifecycle": {},
            "runs": {},
            "export": {},
        }

        self.plugins: Dict[str, Dict[str, Any]] = {}
        self.plugin_instances: Dict[str, Any] = {}
        self.event_handlers: Dict[str, EventHandler] = {}
        self.plugin_status: Dict[str, Dict[str, Any]] = {}
        self.plugin_hosts: Dict[str, Any] = {}
        self.plugin_status_lock = threading.Lock()
        
        # 使用读写锁替代互斥锁，允许多个读操作并发
        self._plugins_rwlock = RWLock()
        self._plugin_hosts_rwlock = RWLock()
        self._event_handlers_rwlock = RWLock()
        
        # 保留原有互斥锁以保持向后兼容（逐步迁移）
        self.plugins_lock = threading.Lock()  # 保护 plugins 字典的线程安全
        self.plugin_hosts_lock = threading.Lock()  # 保护 plugin_hosts 字典的线程安全
        self.event_handlers_lock = threading.Lock()  # 保护 event_handlers 字典的线程安全
        self._event_queue: Optional[asyncio.Queue] = None
        self._lifecycle_queue: Optional[asyncio.Queue] = None
        self._message_queue: Optional[asyncio.Queue] = None
        self._plugin_comm_queue: Optional[Any] = None
        self._plugin_response_map: Optional[Any] = None
        self._plugin_response_map_manager: Optional[Any] = None
        self._plugin_response_event_map: Optional[Any] = None
        self._plugin_response_notify_event: Optional[Any] = None
        # 保护跨进程通信资源懒加载的锁
        self._plugin_comm_lock = threading.Lock()

        self._plugin_response_queues: Dict[str, Any] = {}
        self._plugin_response_queues_lock = threading.Lock()

        self._bus_store_lock = threading.Lock()
        self._message_store: Deque[Dict[str, Any]] = deque(maxlen=MESSAGE_QUEUE_MAX)
        self._event_store: Deque[Dict[str, Any]] = deque(maxlen=EVENT_QUEUE_MAX)
        self._lifecycle_store: Deque[Dict[str, Any]] = deque(maxlen=LIFECYCLE_QUEUE_MAX)
        self._deleted_message_ids: Set[str] = set()
        self._deleted_event_ids: Set[str] = set()
        self._deleted_lifecycle_ids: Set[str] = set()
        self._deleted_message_ids_order: Deque[str] = deque()
        self._deleted_event_ids_order: Deque[str] = deque()
        self._deleted_lifecycle_ids_order: Deque[str] = deque()

        self._bus_rev_lock = threading.Lock()
        self._bus_rev: Dict[str, int] = {
            "messages": 0,
            "events": 0,
            "lifecycle": 0,
            "runs": 0,
            "export": 0,
        }

        self.bus_change_hub = BusChangeHub()

        self._bus_subscriptions_lock = threading.Lock()
        self._bus_subscriptions: Dict[str, Dict[str, Dict[str, Any]]] = {
            "messages": {},
            "events": {},
            "lifecycle": {},
            "runs": {},
            "export": {},
        }

        self._user_context_lock = threading.Lock()
        self._user_context_store: Dict[str, Deque[Dict[str, Any]]] = {}
        self._user_context_default_maxlen: int = 200
        self._user_context_ttl_seconds: float = 60.0 * 60.0

        self._message_plane_rpc_lock = threading.Lock()
        self._message_plane_rpc: Optional[Any] = None
        
        # 快照缓存机制（减少锁竞争）
        self._snapshot_cache_lock = threading.Lock()
        self._snapshot_cache: Dict[str, Dict[str, Any]] = {
            "plugins": {"data": None, "timestamp": 0.0},
            "hosts": {"data": None, "timestamp": 0.0},
            "handlers": {"data": None, "timestamp": 0.0},
        }
        self._snapshot_cache_ttl: float = 0.5  # 500ms缓存TTL

    # 带超时的锁上下文管理器（用于防止死锁）
    @contextmanager
    def acquire_plugins_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """获取 plugins_lock（带超时）- 已废弃，请使用 acquire_plugins_write_lock"""
        with self._plugins_rwlock.write_lock(timeout, "plugins_write"):
            yield

    @contextmanager
    def acquire_plugin_hosts_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """获取 plugin_hosts_lock（带超时）- 已废弃，请使用 acquire_plugin_hosts_write_lock"""
        with self._plugin_hosts_rwlock.write_lock(timeout, "plugin_hosts_write"):
            yield

    @contextmanager
    def acquire_event_handlers_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """获取 event_handlers_lock（带超时）- 已废弃，请使用 acquire_event_handlers_write_lock"""
        with self._event_handlers_rwlock.write_lock(timeout, "event_handlers_write"):
            yield
    
    # RWLock 写锁上下文管理器（用于修改操作）
    @contextmanager
    def acquire_plugins_write_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """获取 plugins 写锁（用于修改操作）"""
        with self._plugins_rwlock.write_lock(timeout, "plugins_write"):
            yield
    
    @contextmanager
    def acquire_plugin_hosts_write_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """获取 plugin_hosts 写锁（用于修改操作）"""
        with self._plugin_hosts_rwlock.write_lock(timeout, "plugin_hosts_write"):
            yield
    
    @contextmanager
    def acquire_event_handlers_write_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """获取 event_handlers 写锁（用于修改操作）"""
        with self._event_handlers_rwlock.write_lock(timeout, "event_handlers_write"):
            yield
    
    # RWLock 读锁上下文管理器（用于读取操作）
    @contextmanager
    def acquire_plugins_read_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """获取 plugins 读锁（用于读取操作，允许并发）"""
        with self._plugins_rwlock.read_lock(timeout, "plugins_read"):
            yield
    
    @contextmanager
    def acquire_plugin_hosts_read_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """获取 plugin_hosts 读锁（用于读取操作，允许并发）"""
        with self._plugin_hosts_rwlock.read_lock(timeout, "plugin_hosts_read"):
            yield
    
    @contextmanager
    def acquire_event_handlers_read_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """获取 event_handlers 读锁（用于读取操作，允许并发）"""
        with self._event_handlers_rwlock.read_lock(timeout, "event_handlers_read"):
            yield

    def get_plugins_snapshot(self, timeout: float = 2.0) -> Dict[str, Dict[str, Any]]:
        """获取 plugins 的快照（使用读写锁的读锁，允许并发读取）
        
        Args:
            timeout: 锁获取超时时间（秒），默认2秒
            
        Returns:
            plugins 字典的浅拷贝
            
        Note:
            超时会返回空字典而非抛异常
        """
        if not self._plugins_rwlock.acquire_read(timeout=timeout):
            logger.warning("Failed to acquire plugins read lock within {}s, returning empty snapshot", timeout)
            return {}
        try:
            return dict(self.plugins)
        finally:
            self._plugins_rwlock.release_read()

    def get_plugin_hosts_snapshot(self, timeout: float = 2.0) -> Dict[str, Any]:
        """获取 plugin_hosts 的快照（使用读写锁的读锁，允许并发读取）
        
        Args:
            timeout: 锁获取超时时间（秒），默认2秒
            
        Returns:
            plugin_hosts 字典的浅拷贝
            
        Note:
            超时会返回空字典而非抛异常
        """
        if not self._plugin_hosts_rwlock.acquire_read(timeout=timeout):
            logger.warning("Failed to acquire plugin_hosts read lock within {}s, returning empty snapshot", timeout)
            return {}
        try:
            return dict(self.plugin_hosts)
        finally:
            self._plugin_hosts_rwlock.release_read()

    def get_event_handlers_snapshot(self, timeout: float = 2.0) -> Dict[str, EventHandler]:
        """获取 event_handlers 的快照（使用读写锁的读锁，允许并发读取）
        
        Args:
            timeout: 锁获取超时时间（秒），默认2秒
            
        Returns:
            event_handlers 字典的浅拷贝
            
        Note:
            超时会返回空字典而非抛异常
        """
        if not self._event_handlers_rwlock.acquire_read(timeout=timeout):
            logger.warning("Failed to acquire event_handlers read lock within {}s, returning empty snapshot", timeout)
            return {}
        try:
            return dict(self.event_handlers)
        finally:
            self._event_handlers_rwlock.release_read()
    
    def get_plugins_snapshot_cached(self, timeout: float = 2.0, force: bool = False) -> Dict[str, Dict[str, Any]]:
        """获取 plugins 的快照（带缓存，减少锁竞争）
        
        Args:
            timeout: 锁获取超时时间（秒），默认2秒
            force: 是否强制刷新缓存
            
        Returns:
            plugins 字典的浅拷贝
        """
        import time
        now = time.time()
        
        # 检查缓存
        if not force:
            with self._snapshot_cache_lock:
                cache = self._snapshot_cache["plugins"]
                if cache["data"] is not None and (now - cache["timestamp"]) < self._snapshot_cache_ttl:
                    return dict(cache["data"])
        
        # 缓存失效或强制刷新，获取新快照
        snapshot = self.get_plugins_snapshot(timeout=timeout)
        
        # 更新缓存
        with self._snapshot_cache_lock:
            self._snapshot_cache["plugins"]["data"] = snapshot
            self._snapshot_cache["plugins"]["timestamp"] = now
        
        return snapshot
    
    def get_plugin_hosts_snapshot_cached(self, timeout: float = 2.0, force: bool = False) -> Dict[str, Any]:
        """获取 plugin_hosts 的快照（带缓存，减少锁竞争）
        
        Args:
            timeout: 锁获取超时时间（秒），默认2秒
            force: 是否强制刷新缓存
            
        Returns:
            plugin_hosts 字典的浅拷贝
        """
        import time
        now = time.time()
        
        # 检查缓存
        if not force:
            with self._snapshot_cache_lock:
                cache = self._snapshot_cache["hosts"]
                if cache["data"] is not None and (now - cache["timestamp"]) < self._snapshot_cache_ttl:
                    return dict(cache["data"])
        
        # 缓存失效或强制刷新，获取新快照
        snapshot = self.get_plugin_hosts_snapshot(timeout=timeout)
        
        # 更新缓存
        with self._snapshot_cache_lock:
            self._snapshot_cache["hosts"]["data"] = snapshot
            self._snapshot_cache["hosts"]["timestamp"] = now
        
        return snapshot
    
    def get_event_handlers_snapshot_cached(self, timeout: float = 2.0, force: bool = False) -> Dict[str, EventHandler]:
        """获取 event_handlers 的快照（带缓存，减少锁竞争）
        
        Args:
            timeout: 锁获取超时时间（秒），默认2秒
            force: 是否强制刷新缓存
            
        Returns:
            event_handlers 字典的浅拷贝
        """
        import time
        now = time.time()
        
        # 检查缓存
        if not force:
            with self._snapshot_cache_lock:
                cache = self._snapshot_cache["handlers"]
                if cache["data"] is not None and (now - cache["timestamp"]) < self._snapshot_cache_ttl:
                    return dict(cache["data"])
        
        # 缓存失效或强制刷新，获取新快照
        snapshot = self.get_event_handlers_snapshot(timeout=timeout)
        
        # 更新缓存
        with self._snapshot_cache_lock:
            self._snapshot_cache["handlers"]["data"] = snapshot
            self._snapshot_cache["handlers"]["timestamp"] = now
        
        return snapshot
    
    def invalidate_snapshot_cache(self, cache_type: Optional[str] = None) -> None:
        """使快照缓存失效
        
        Args:
            cache_type: 要失效的缓存类型 ('plugins', 'hosts', 'handlers')，None表示全部失效
        """
        with self._snapshot_cache_lock:
            if cache_type is None:
                # 全部失效
                for key in self._snapshot_cache:
                    self._snapshot_cache[key]["timestamp"] = 0.0
            elif cache_type in self._snapshot_cache:
                # 指定类型失效
                self._snapshot_cache[cache_type]["timestamp"] = 0.0
    
    @contextmanager
    def acquire_locks_in_order(self, *lock_names: str, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """按顺序获取多个锁，防止死锁
        
        Args:
            lock_names: 锁名称列表，必须按照规范顺序：'plugins', 'hosts', 'handlers'
            timeout: 每个锁的获取超时时间
            
        Example:
            with state.acquire_locks_in_order('plugins', 'hosts'):
                # 安全地访问 plugins 和 plugin_hosts
                ...
        """
        lock_order = {'plugins': 1, 'hosts': 2, 'handlers': 3}
        lock_map = {
            'plugins': self._plugins_rwlock,
            'hosts': self._plugin_hosts_rwlock,
            'handlers': self._event_handlers_rwlock,
        }
        
        # 验证锁名称和顺序
        for name in lock_names:
            if name not in lock_order:
                raise ValueError(f"Unknown lock name: {name}")
        
        # 检查顺序是否正确
        sorted_names = sorted(lock_names, key=lambda n: lock_order[n])
        if list(lock_names) != sorted_names:
            logger.warning(
                "Lock acquisition order violation detected! Expected: {}, Got: {}",
                sorted_names, list(lock_names)
            )
            raise RuntimeError(f"Lock order violation: {list(lock_names)} should be {sorted_names}")
        
        # 按顺序获取锁
        acquired_locks = []
        try:
            for name in lock_names:
                rwlock = lock_map[name]
                if not rwlock.acquire_write(timeout=timeout):
                    logger.error("Failed to acquire {} lock within {}s", name, timeout)
                    raise TimeoutError(f"Failed to acquire {name} lock within {timeout}s")
                acquired_locks.append(rwlock)
            yield
        finally:
            # 释放所有已获取的锁（逆序释放）
            for rwlock in reversed(acquired_locks):
                try:
                    rwlock.release_write()
                except Exception:
                    pass

    def _bump_bus_rev(self, bus: str) -> int:
        b = str(bus).strip()
        with self._bus_rev_lock:
            cur = int(self._bus_rev.get(b, 0))
            cur += 1
            self._bus_rev[b] = cur
            return cur

    def get_bus_rev(self, bus: str) -> int:
        b = str(bus).strip()
        with self._bus_rev_lock:
            return int(self._bus_rev.get(b, 0))

    @property
    def event_queue(self) -> asyncio.Queue:
        if self._event_queue is None:
            self._event_queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAX)
        return self._event_queue

    @property
    def lifecycle_queue(self) -> asyncio.Queue:
        if self._lifecycle_queue is None:
            self._lifecycle_queue = asyncio.Queue(maxsize=LIFECYCLE_QUEUE_MAX)
        return self._lifecycle_queue

    @property
    def message_queue(self) -> asyncio.Queue:
        if self._message_queue is None:
            self._message_queue = asyncio.Queue(maxsize=MESSAGE_QUEUE_MAX)
        return self._message_queue
    
    @property
    def plugin_comm_queue(self):
        """插件间通信队列（用于插件调用其他插件的 custom_event）"""
        if self._plugin_comm_queue is None:
            with self._plugin_comm_lock:
                if self._plugin_comm_queue is None:
                    # 使用 multiprocessing.Queue 因为需要跨进程
                    self._plugin_comm_queue = multiprocessing.Queue()
        return self._plugin_comm_queue

    def set_plugin_response_queue(self, plugin_id: str, q: Any) -> None:
        pid = str(plugin_id).strip()
        if not pid:
            return
        with self._plugin_response_queues_lock:
            self._plugin_response_queues[pid] = q

    def get_plugin_response_queue(self, plugin_id: str) -> Any:
        pid = str(plugin_id).strip()
        if not pid:
            return None
        with self._plugin_response_queues_lock:
            return self._plugin_response_queues.get(pid)

    def remove_plugin_response_queue(self, plugin_id: str) -> None:
        pid = str(plugin_id).strip()
        if not pid:
            return
        with self._plugin_response_queues_lock:
            self._plugin_response_queues.pop(pid, None)
    
    @property
    def plugin_response_map(self) -> Any:
        """插件响应映射（跨进程共享字典）"""
        if self._plugin_response_map is None:
            with self._plugin_comm_lock:
                if self._plugin_response_map is None:
                    # 使用 Manager 创建跨进程共享的字典
                    if self._plugin_response_map_manager is None:
                        self._plugin_response_map_manager = multiprocessing.Manager()
                    self._plugin_response_map = self._plugin_response_map_manager.dict()
                    # Ensure event map is created on the same Manager early, so forked plugin
                    # processes inherit the same proxies and can wait on the same Events.
                    if self._plugin_response_event_map is None:
                        self._plugin_response_event_map = self._plugin_response_map_manager.dict()
        return self._plugin_response_map

    @property
    def plugin_response_event_map(self) -> Any:
        """跨进程响应通知映射 request_id -> Event."""
        if self._plugin_response_event_map is None:
            # Prefer reusing the existing Manager created for plugin_response_map.
            _ = self.plugin_response_map
        return self._plugin_response_event_map

    @property
    def plugin_response_notify_event(self) -> Any:
        """Single cross-process event used to wake waiters when any response arrives.

        This avoids per-request Event creation which is expensive and can diverge across processes.
        Important: on Linux (fork), this must be created in the parent before plugin processes start.
        """
        if self._plugin_response_notify_event is None:
            with self._plugin_comm_lock:
                if self._plugin_response_notify_event is None:
                    # multiprocessing.Event is backed by a shared semaphore/pipe and works across fork.
                    self._plugin_response_notify_event = multiprocessing.Event()
        return self._plugin_response_notify_event

    def _get_or_create_response_event(self, request_id: str):
        rid = str(request_id)
        # Force init of shared manager + maps (important: do not create a new Manager per process)
        _ = self.plugin_response_map
        try:
            event_map = self.plugin_response_event_map
            ev = event_map.get(rid)
        except Exception:
            ev = None
        if ev is not None:
            return ev
        try:
            mgr = self._plugin_response_map_manager
            if mgr is None:
                _ = self.plugin_response_map
                mgr = self._plugin_response_map_manager
            if mgr is None:
                return None
            ev = mgr.Event()
            try:
                event_map = self.plugin_response_event_map
                try:
                    stored = event_map.setdefault(rid, ev)
                    ev = stored if stored is not None else ev
                except Exception:
                    event_map[rid] = ev
                try:
                    existing = event_map.get(rid)
                    if existing is not None:
                        ev = existing
                except Exception:
                    logger.bind(component="server").debug(
                        f"Failed to retrieve response event for request_id={rid}", exc_info=True
                    )
            except Exception:
                logger.bind(component="server").debug(
                    f"Failed to store response event for request_id={rid}", exc_info=True
                )
            return ev
        except Exception:
            return None

    def append_message_record(self, record: Dict[str, Any]) -> None:
        if not isinstance(record, dict):
            return
        mid = record.get("message_id")
        with self._bus_store_lock:
            if isinstance(mid, str) and mid in self._deleted_message_ids:
                return
            self._message_store.append(record)
        # NOTE: messages are authoritative in message_plane. The control-plane keeps only a cache.
        # Do NOT mirror/forward control-plane messages into message_plane.
        try:
            rev = self._bump_bus_rev("messages")
            payload: Dict[str, Any] = {"rev": rev}
            if isinstance(mid, str) and mid:
                payload["message_id"] = mid
            try:
                payload["priority"] = int(record.get("priority", 0))
            except Exception:
                payload["priority"] = 0
            try:
                src = record.get("source")
                if isinstance(src, str) and src:
                    payload["source"] = src
            except Exception:
                pass
            # Optional visibility/export hint (future use)
            if "export" in record:
                payload["export"] = record.get("export")
            self.bus_change_hub.emit("messages", "add", payload)
        except Exception:
            pass

    def extend_message_records(self, records: List[Dict[str, Any]]) -> int:
        if not isinstance(records, list) or not records:
            return 0
        candidates: List[Dict[str, Any]] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            candidates.append(rec)

        kept: List[Dict[str, Any]] = []
        with self._bus_store_lock:
            for rec in candidates:
                mid = rec.get("message_id")
                if isinstance(mid, str) and mid in self._deleted_message_ids:
                    continue
                self._message_store.append(rec)
                kept.append(rec)
        if not kept:
            return 0
        # NOTE: messages are authoritative in message_plane. The control-plane keeps only a cache.
        # Do NOT mirror/forward control-plane messages into message_plane.
        for rec in kept:
            try:
                rev = self._bump_bus_rev("messages")
                mid = rec.get("message_id")
                payload: Dict[str, Any] = {"rev": rev}
                if isinstance(mid, str) and mid:
                    payload["message_id"] = mid
                try:
                    payload["priority"] = int(rec.get("priority", 0))
                except Exception:
                    payload["priority"] = 0
                try:
                    src = rec.get("source")
                    if isinstance(src, str) and src:
                        payload["source"] = src
                except Exception:
                    pass
                if "export" in rec:
                    payload["export"] = rec.get("export")
                self.bus_change_hub.emit("messages", "add", payload)
            except Exception:
                pass
        return len(kept)

    def extend_message_records_coalesced(self, records: List[Dict[str, Any]]) -> int:
        if not isinstance(records, list) or not records:
            return 0
        # Fast path: no deletions tracked => no need to filter by message_id.
        # This keeps the critical section minimal (single deque.extend).
        try:
            if not self._deleted_message_ids:
                last_mid_fast: Optional[str] = None
                last_priority_fast: int = 0
                last_source_fast: Optional[str] = None
                kept_fast = [r for r in records if isinstance(r, dict)]
                if not kept_fast:
                    return 0
                for rec in kept_fast:
                    mid = rec.get("message_id")
                    if isinstance(mid, str) and mid:
                        last_mid_fast = mid
                    try:
                        last_priority_fast = int(rec.get("priority", last_priority_fast))
                    except Exception:
                        last_priority_fast = last_priority_fast
                    try:
                        src = rec.get("source")
                        if isinstance(src, str) and src:
                            last_source_fast = src
                    except Exception:
                        last_source_fast = last_source_fast

                with self._bus_store_lock:
                    if self._deleted_message_ids:
                        raise RuntimeError("deleted_message_ids changed")
                    self._message_store.extend(kept_fast)

                # NOTE: messages are authoritative in message_plane. The control-plane keeps only a cache.
                # Do NOT mirror/forward control-plane messages into message_plane.

                rev = self._bump_bus_rev("messages")
                payload_fast: Dict[str, Any] = {
                    "rev": rev,
                    "count": int(len(kept_fast)),
                    "batch": True,
                }
                if isinstance(last_mid_fast, str) and last_mid_fast:
                    payload_fast["message_id"] = last_mid_fast
                payload_fast["priority"] = int(last_priority_fast)
                if isinstance(last_source_fast, str) and last_source_fast:
                    payload_fast["source"] = last_source_fast
                self.bus_change_hub.emit("messages", "add", payload_fast)
                return int(len(kept_fast))
        except Exception:
            # Fall back to filtered path.
            pass
        candidates: List[Dict[str, Any]] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            candidates.append(rec)

        kept: List[Dict[str, Any]] = []
        last_mid: Optional[str] = None
        last_priority: int = 0
        last_source: Optional[str] = None
        with self._bus_store_lock:
            for rec in candidates:
                mid = rec.get("message_id")
                if isinstance(mid, str) and mid in self._deleted_message_ids:
                    continue
                kept.append(rec)
                if isinstance(mid, str) and mid:
                    last_mid = mid
                try:
                    last_priority = int(rec.get("priority", 0))
                except Exception:
                    last_priority = last_priority
                try:
                    src = rec.get("source")
                    if isinstance(src, str) and src:
                        last_source = src
                except Exception:
                    last_source = last_source
            try:
                if kept:
                    self._message_store.extend(kept)
            except Exception:
                for rec in kept:
                    self._message_store.append(rec)
        if not kept:
            return 0
        # NOTE: messages are authoritative in message_plane. The control-plane keeps only a cache.
        # Do NOT mirror/forward control-plane messages into message_plane.
        try:
            rev = self._bump_bus_rev("messages")
            payload: Dict[str, Any] = {
                "rev": rev,
                "count": int(len(kept)),
                "batch": True,
            }
            if isinstance(last_mid, str) and last_mid:
                payload["message_id"] = last_mid
            payload["priority"] = int(last_priority)
            if isinstance(last_source, str) and last_source:
                payload["source"] = last_source
            self.bus_change_hub.emit("messages", "add", payload)
        except Exception:
            pass
        return int(len(kept))

    def append_event_record(self, record: Dict[str, Any]) -> None:
        if not isinstance(record, dict):
            return
        eid = record.get("event_id") or record.get("trace_id")
        with self._bus_store_lock:
            if isinstance(eid, str) and eid in self._deleted_event_ids:
                return
            self._event_store.append(record)
        try:
            from plugin.server.messaging.plane_bridge import publish_record

            publish_record(store="events", record=dict(record), topic="all")
        except Exception:
            pass
        try:
            rev = self._bump_bus_rev("events")
            self.bus_change_hub.emit("events", "add", {"record": dict(record), "rev": rev})
        except Exception:
            pass

    def extend_event_records(self, records: List[Dict[str, Any]]) -> int:
        if not isinstance(records, list) or not records:
            return 0
        candidates: List[Dict[str, Any]] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            candidates.append(rec)

        kept: List[Dict[str, Any]] = []
        with self._bus_store_lock:
            for rec in candidates:
                eid = rec.get("event_id") or rec.get("trace_id")
                if isinstance(eid, str) and eid in self._deleted_event_ids:
                    continue
                self._event_store.append(rec)
                kept.append(rec)
        if not kept:
            return 0
        try:
            from plugin.server.messaging.plane_bridge import publish_record

            for rec in kept:
                if isinstance(rec, dict):
                    publish_record(store="events", record=dict(rec), topic="all")
        except Exception:
            pass
        for rec in kept:
            try:
                rev = self._bump_bus_rev("events")
                self.bus_change_hub.emit("events", "add", {"record": dict(rec), "rev": rev})
            except Exception:
                pass
        return len(kept)

    def append_lifecycle_record(self, record: Dict[str, Any]) -> None:
        if not isinstance(record, dict):
            return
        lid = record.get("lifecycle_id") or record.get("trace_id")
        with self._bus_store_lock:
            if isinstance(lid, str) and lid in self._deleted_lifecycle_ids:
                return
            self._lifecycle_store.append(record)
        try:
            from plugin.server.messaging.plane_bridge import publish_record

            publish_record(store="lifecycle", record=dict(record), topic="all")
        except Exception:
            pass
        try:
            rev = self._bump_bus_rev("lifecycle")
            self.bus_change_hub.emit("lifecycle", "add", {"record": dict(record), "rev": rev})
        except Exception:
            pass

    def extend_lifecycle_records(self, records: List[Dict[str, Any]]) -> int:
        if not isinstance(records, list) or not records:
            return 0
        candidates: List[Dict[str, Any]] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            candidates.append(rec)

        kept: List[Dict[str, Any]] = []
        with self._bus_store_lock:
            for rec in candidates:
                lid = rec.get("lifecycle_id") or rec.get("trace_id")
                if isinstance(lid, str) and lid in self._deleted_lifecycle_ids:
                    continue
                self._lifecycle_store.append(rec)
                kept.append(rec)
        if not kept:
            return 0
        try:
            from plugin.server.messaging.plane_bridge import publish_record

            for rec in kept:
                if isinstance(rec, dict):
                    publish_record(store="lifecycle", record=dict(rec), topic="all")
        except Exception:
            pass
        for rec in kept:
            try:
                rev = self._bump_bus_rev("lifecycle")
                self.bus_change_hub.emit("lifecycle", "add", {"record": dict(rec), "rev": rev})
            except Exception:
                pass
        return len(kept)

    def list_message_records(self) -> List[Dict[str, Any]]:
        with self._bus_store_lock:
            return list(self._message_store)

    def list_message_records_tail(self, n: int) -> List[Dict[str, Any]]:
        nn = int(n)
        if nn <= 0:
            return []
        with self._bus_store_lock:
            try:
                tail_rev = list(itertools.islice(reversed(self._message_store), nn))
                tail_rev.reverse()
                return tail_rev
            except Exception:
                return list(self._message_store)

    def _ensure_messages_cache_state(self) -> None:
        # Lazily create cache metadata to avoid touching __init__ layout.
        if not hasattr(self, "_messages_cache_last_sync_ts"):
            try:
                object.__setattr__(self, "_messages_cache_last_sync_ts", 0.0)
            except Exception:
                self._messages_cache_last_sync_ts = 0.0  # type: ignore[attr-defined]
        if not hasattr(self, "_messages_cache_sync_lock"):
            lock = threading.Lock()
            try:
                object.__setattr__(self, "_messages_cache_sync_lock", lock)
            except Exception:
                self._messages_cache_sync_lock = lock  # type: ignore[attr-defined]

    def _message_plane_rpc_get_recent_messages(self, *, limit: int, timeout: float) -> List[Dict[str, Any]]:
        if zmq is None or ormsgpack is None:
            raise RuntimeError("message_plane RPC requires pyzmq and ormsgpack")
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.DEALER)
        try:
            sock.setsockopt(zmq.LINGER, 0)
        except Exception:
            pass
        try:
            sock.connect(str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT))
        except Exception as e:
            try:
                sock.close(0)
            except Exception:
                pass
            raise RuntimeError(f"Failed to connect message_plane RPC: {e}") from e

        try:
            req_id = str(uuid.uuid4())
            req = {
                "v": 1,
                "op": "bus.get_recent",
                "req_id": req_id,
                "from_plugin": "control_plane",
                "args": {"store": "messages", "topic": "all", "limit": int(limit), "light": False},
            }
            raw = ormsgpack.packb(req)
            try:
                sock.send(raw, flags=0)
            except Exception as e:
                raise RuntimeError(f"Failed to send message_plane RPC request: {e}") from e

            deadline = time.time() + max(0.0, float(timeout))
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(f"message_plane bus.get_recent timed out after {timeout}s")
                try:
                    if sock.poll(timeout=int(remaining * 1000), flags=zmq.POLLIN) == 0:
                        continue
                except Exception as e:
                    raise RuntimeError(f"message_plane RPC poll failed: {e}") from e
                try:
                    resp_raw = sock.recv(flags=0)
                except Exception as e:
                    raise RuntimeError(f"message_plane RPC recv failed: {e}") from e
                try:
                    resp = ormsgpack.unpackb(resp_raw)
                except Exception:
                    continue
                if not isinstance(resp, dict):
                    continue
                if resp.get("req_id") != req_id:
                    continue
                if resp.get("error"):
                    raise RuntimeError(format_rpc_error(resp.get("error")))
                if not resp.get("ok"):
                    raise RuntimeError(format_rpc_error(resp.get("error") or "message_plane error"))
                result = resp.get("result")
                if isinstance(result, dict) and isinstance(result.get("items"), list):
                    items = list(result.get("items") or [])
                else:
                    items = []
                out: List[Dict[str, Any]] = []
                for it in items:
                    if isinstance(it, dict):
                        out.append(dict(it))
                return out
        finally:
            try:
                sock.close(0)
            except Exception:
                pass

    def refresh_messages_cache_from_message_plane(
        self,
        *,
        limit: int,
        timeout: float,
        ttl_seconds: float = 0.5,
        force: bool = False,
    ) -> int:
        """Refresh control-plane messages cache from message_plane (authoritative).

        This is intentionally low-frequency: guarded by TTL + mutex to prevent UI request storms.
        """
        self._ensure_messages_cache_state()
        try:
            last_ts = float(getattr(self, "_messages_cache_last_sync_ts", 0.0) or 0.0)
        except Exception:
            last_ts = 0.0

        now_ts = time.time()
        if not force and ttl_seconds is not None and ttl_seconds > 0 and (now_ts - last_ts) < float(ttl_seconds):
            try:
                return int(self.message_store_len())
            except Exception:
                return 0

        lock = getattr(self, "_messages_cache_sync_lock", None)
        if lock is None:
            lock = threading.Lock()
            try:
                object.__setattr__(self, "_messages_cache_sync_lock", lock)
            except Exception:
                self._messages_cache_sync_lock = lock  # type: ignore[attr-defined]

        with lock:
            # Double-check under lock.
            try:
                last_ts2 = float(getattr(self, "_messages_cache_last_sync_ts", 0.0) or 0.0)
            except Exception:
                last_ts2 = 0.0
            now_ts2 = time.time()
            if not force and ttl_seconds is not None and ttl_seconds > 0 and (now_ts2 - last_ts2) < float(ttl_seconds):
                try:
                    return int(self.message_store_len())
                except Exception:
                    return 0

            items = self._message_plane_rpc_get_recent_messages(limit=int(limit), timeout=float(timeout))
            with self._bus_store_lock:
                try:
                    self._message_store.clear()
                except Exception:
                    self._message_store = deque(maxlen=self._message_store.maxlen)
                for rec in items:
                    self._message_store.append(rec)
            try:
                object.__setattr__(self, "_messages_cache_last_sync_ts", float(time.time()))
            except Exception:
                self._messages_cache_last_sync_ts = float(time.time())  # type: ignore[attr-defined]
            return int(len(items))

    def message_store_len(self) -> int:
        with self._bus_store_lock:
            return len(self._message_store)

    def iter_message_records_reverse(self):
        with self._bus_store_lock:
            snap = list(self._message_store)
        return reversed(snap)

    def list_event_records(self) -> List[Dict[str, Any]]:
        with self._bus_store_lock:
            return list(self._event_store)

    def list_event_records_tail(self, n: int) -> List[Dict[str, Any]]:
        nn = int(n)
        if nn <= 0:
            return []
        with self._bus_store_lock:
            try:
                tail_rev = list(itertools.islice(reversed(self._event_store), nn))
                tail_rev.reverse()
                return tail_rev
            except Exception:
                return list(self._event_store)

    def event_store_len(self) -> int:
        with self._bus_store_lock:
            return len(self._event_store)

    def iter_event_records_reverse(self):
        with self._bus_store_lock:
            snap = list(self._event_store)
        return reversed(snap)

    def list_lifecycle_records(self) -> List[Dict[str, Any]]:
        with self._bus_store_lock:
            return list(self._lifecycle_store)

    def list_lifecycle_records_tail(self, n: int) -> List[Dict[str, Any]]:
        nn = int(n)
        if nn <= 0:
            return []
        with self._bus_store_lock:
            try:
                tail_rev = list(itertools.islice(reversed(self._lifecycle_store), nn))
                tail_rev.reverse()
                return tail_rev
            except Exception:
                return list(self._lifecycle_store)

    def lifecycle_store_len(self) -> int:
        with self._bus_store_lock:
            return len(self._lifecycle_store)

    def iter_lifecycle_records_reverse(self):
        with self._bus_store_lock:
            snap = list(self._lifecycle_store)
        return reversed(snap)

    def sync_message_plane_messages(self) -> int:
        # Reverse sync: messages are authoritative in message_plane; control_plane keeps a cache.
        try:
            return int(
                self.refresh_messages_cache_from_message_plane(
                    limit=500,
                    timeout=1.0,
                    ttl_seconds=0.0,
                    force=True,
                )
            )
        except Exception:
            return 0

    def sync_message_plane_events(self) -> int:
        try:
            from plugin.server.messaging.plane_bridge import publish_snapshot

            items = self.list_event_records()
            publish_snapshot(store="events", records=[dict(x) for x in items if isinstance(x, dict)], topic="all", mode="replace")
            return int(len(items))
        except Exception:
            return 0

    def sync_message_plane_lifecycle(self) -> int:
        try:
            from plugin.server.messaging.plane_bridge import publish_snapshot

            items = self.list_lifecycle_records()
            publish_snapshot(store="lifecycle", records=[dict(x) for x in items if isinstance(x, dict)], topic="all", mode="replace")
            return int(len(items))
        except Exception:
            return 0

    def delete_message(self, message_id: str) -> bool:
        if not isinstance(message_id, str) or not message_id:
            return False
        removed = False
        with self._bus_store_lock:
            if message_id not in self._deleted_message_ids:
                self._deleted_message_ids.add(message_id)
                self._deleted_message_ids_order.append(message_id)
                while len(self._deleted_message_ids) > MAX_DELETED_BUS_IDS:
                    old = self._deleted_message_ids_order.popleft()
                    self._deleted_message_ids.discard(old)
            # 重建 deque，排除要删除的记录
            new_store = deque(maxlen=self._message_store.maxlen)
            for rec in self._message_store:
                if isinstance(rec, dict) and rec.get("message_id") == message_id:
                    removed = True
                else:
                    new_store.append(rec)
            self._message_store = new_store
        if removed:
            try:
                rev = self._bump_bus_rev("messages")
                self.bus_change_hub.emit("messages", "del", {"message_id": message_id, "rev": rev})
            except Exception:
                pass
        return removed

    def add_bus_subscription(self, bus: str, sub_id: str, info: Dict[str, Any]) -> None:
        b = str(bus).strip()
        if b not in self._bus_subscriptions:
            raise ValueError(f"Unknown bus: {bus!r}")
        sid = str(sub_id).strip()
        if not sid:
            raise ValueError("sub_id is required")
        payload = dict(info) if isinstance(info, dict) else {}
        with self._bus_subscriptions_lock:
            self._bus_subscriptions[b][sid] = payload

    def remove_bus_subscription(self, bus: str, sub_id: str) -> bool:
        b = str(bus).strip()
        sid = str(sub_id).strip()
        if b not in self._bus_subscriptions or not sid:
            return False
        with self._bus_subscriptions_lock:
            return self._bus_subscriptions[b].pop(sid, None) is not None

    def get_bus_subscriptions(self, bus: str) -> Dict[str, Dict[str, Any]]:
        b = str(bus).strip()
        if b not in self._bus_subscriptions:
            return {}
        with self._bus_subscriptions_lock:
            return {k: dict(v) for k, v in self._bus_subscriptions[b].items()}

    def delete_event(self, event_id: str) -> bool:
        if not isinstance(event_id, str) or not event_id:
            return False
        removed = False
        with self._bus_store_lock:
            if event_id not in self._deleted_event_ids:
                self._deleted_event_ids.add(event_id)
                self._deleted_event_ids_order.append(event_id)
                while len(self._deleted_event_ids) > MAX_DELETED_BUS_IDS:
                    old = self._deleted_event_ids_order.popleft()
                    self._deleted_event_ids.discard(old)
            new_store = deque(maxlen=self._event_store.maxlen)
            for rec in self._event_store:
                rid = rec.get("event_id") or rec.get("trace_id") if isinstance(rec, dict) else None
                if rid == event_id:
                    removed = True
                else:
                    new_store.append(rec)
            self._event_store = new_store
        if removed:
            try:
                rev = self._bump_bus_rev("events")
                self.bus_change_hub.emit("events", "del", {"event_id": event_id, "rev": rev})
            except Exception:
                pass
        return removed

    def delete_lifecycle(self, lifecycle_id: str) -> bool:
        if not isinstance(lifecycle_id, str) or not lifecycle_id:
            return False
        removed = False
        with self._bus_store_lock:
            if lifecycle_id not in self._deleted_lifecycle_ids:
                self._deleted_lifecycle_ids.add(lifecycle_id)
                self._deleted_lifecycle_ids_order.append(lifecycle_id)
                while len(self._deleted_lifecycle_ids) > MAX_DELETED_BUS_IDS:
                    old = self._deleted_lifecycle_ids_order.popleft()
                    self._deleted_lifecycle_ids.discard(old)
            new_store = deque(maxlen=self._lifecycle_store.maxlen)
            for rec in self._lifecycle_store:
                rid = rec.get("lifecycle_id") or rec.get("trace_id") if isinstance(rec, dict) else None
                if rid == lifecycle_id:
                    removed = True
                else:
                    new_store.append(rec)
            self._lifecycle_store = new_store
        if removed:
            try:
                rev = self._bump_bus_rev("lifecycle")
                self.bus_change_hub.emit("lifecycle", "del", {"lifecycle_id": lifecycle_id, "rev": rev})
            except Exception:
                pass
        return removed
    
    def set_plugin_response(self, request_id: str, response: Dict[str, Any], timeout: float = 10.0) -> None:
        """
        设置插件响应（主进程调用）
        
        Args:
            request_id: 请求ID
            response: 响应数据
            timeout: 超时时间（秒），用于计算过期时间
        """
        rid = str(request_id).strip()
        if not rid:
            return
        # 存储响应和过期时间（当前时间 + timeout + 缓冲时间）
        # 缓冲时间用于处理网络延迟等情况
        expire_time = time.time() + timeout + 1.0  # 额外1秒缓冲
        resp_map = self.plugin_response_map
        resp_map[rid] = {
            "response": response,
            "expire_time": expire_time
        }

        try:
            ev = self._get_or_create_response_event(rid)
            if ev is not None:
                ev.set()
        except Exception:
            pass

        try:
            self.plugin_response_notify_event.set()
        except Exception:
            pass
    
    def get_plugin_response(self, request_id: str) -> Optional[Dict[str, Any]]:
        """
        获取并删除插件响应（插件进程调用）
        
        如果响应已过期，会自动清理并返回 None。
        
        Returns:
            响应数据，如果不存在或已过期则返回 None
        """
        current_time = time.time()

        rid = str(request_id).strip()
        if not rid:
            return None

        resp_map = self.plugin_response_map
        response_data = resp_map.pop(rid, None)
        if response_data is None and request_id != rid:
            # Backward-compatible: tolerate legacy non-string keys stored previously.
            response_data = resp_map.pop(request_id, None)

        if response_data is None:
            return None

        expire_time = response_data.get("expire_time", 0)
        if current_time > expire_time:
            try:
                event_map = self.plugin_response_event_map
                event_map.pop(rid, None)
                # 同时清理原始 request_id 的事件（如果不同）
                if request_id != rid:
                    event_map.pop(request_id, None)
            except Exception:
                logger.bind(component="server").debug(
                    f"Failed to remove response event for request_id={request_id}", exc_info=True
                )
            return None
        try:
            event_map = self.plugin_response_event_map
            event_map.pop(rid, None)
            # 同时清理原始 request_id 的事件（如果不同）
            if request_id != rid:
                event_map.pop(request_id, None)
        except Exception:
            logger.bind(component="server").debug(
                f"Failed to remove response event for request_id={request_id}", exc_info=True
            )
        # 返回实际的响应数据
        return response_data.get("response")

    async def wait_for_plugin_response_async(self, request_id: str, timeout: float) -> Optional[Dict[str, Any]]:
        """异步版本:等待响应到达,完全消除轮询,使用纯事件驱动
        
        相比同步版本,异步版本完全避免了 time.sleep 轮询,
        使用 asyncio.Event 实现零延迟响应。
        """
        rid = str(request_id).strip()
        if not rid:
            return None
        
        # 快速路径:先检查响应是否已到达
        got = self.get_plugin_response(rid)
        if got is not None:
            return got
        
        # 创建异步事件用于等待
        async_event = asyncio.Event()
        
        # 注册回调:响应到达时立即通知
        def check_and_notify():
            response = self.get_plugin_response(rid)
            if response is not None:
                async_event.set()
                return response
            return None
        
        # 使用 asyncio.wait_for 实现超时
        try:
            # 启动后台任务轮询(但使用很短的间隔)
            async def wait_with_polling():
                # 先检查一次
                result = check_and_notify()
                if result is not None:
                    return result
                
                # 等待事件或使用短轮询作为后备
                while True:
                    # 尝试等待同步事件(如果可用)
                    per_req_ev = None
                    try:
                        per_req_ev = self._get_or_create_response_event(rid)
                    except Exception:
                        pass
                    
                    if per_req_ev is not None:
                        # 使用线程事件等待(在线程池中执行)
                        loop = asyncio.get_running_loop()
                        ev = per_req_ev  # 捕获变量避免 None 类型问题
                        await loop.run_in_executor(
                            None,
                            lambda ev=ev: ev.wait(timeout=0.01)
                        )
                    else:
                        # 后备:短暂休眠
                        await asyncio.sleep(0.001)  # 1ms,比同步版本的 10ms 快 10 倍
                    
                    # 检查响应
                    result = check_and_notify()
                    if result is not None:
                        return result
                    
                    # 避免事件已被消费但仍保持 set 导致的空转
                    await asyncio.sleep(0.005)
            
            return await asyncio.wait_for(wait_with_polling(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
    
    def wait_for_plugin_response(self, request_id: str, timeout: float) -> Optional[Dict[str, Any]]:
        """同步版本:Block until response arrives or timeout, then pop and return it.

        This avoids client-side polling loops.
        """
        rid = str(request_id).strip()
        if not rid:
            return None
        deadline = time.time() + max(0.0, float(timeout))
        per_req_ev = None
        try:
            per_req_ev = self._get_or_create_response_event(rid)
        except Exception:
            per_req_ev = None

        # Fast path: check once before waiting.
        got = self.get_plugin_response(rid)
        if got is not None:
            return got

        while True:
            # Fast path: check again before waiting.
            got = self.get_plugin_response(rid)
            if got is not None:
                return got

            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            if per_req_ev is None:
                # Fallback to short sleep if per-request event is unavailable.
                time.sleep(min(0.01, remaining))
            else:
                try:
                    per_req_ev.wait(timeout=min(0.1, remaining))

                    got = self.get_plugin_response(rid)
                    if got is not None:
                        return got

                    # If the event is left in a signaled state (e.g. another waiter consumed the response),
                    # wait() would return immediately and cause a tight loop. Back off briefly.
                    time.sleep(min(0.01, remaining))
                except Exception:
                    time.sleep(min(0.01, remaining))

            got = self.get_plugin_response(rid)
            if got is not None:
                return got

    async def peek_plugin_response_async(self, request_id: str) -> Optional[Dict[str, Any]]:
        """异步版本:获取但不删除插件响应"""
        # peek 操作不涉及等待,直接调用同步版本即可
        return self.peek_plugin_response(request_id)
    
    def peek_plugin_response(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取但不删除插件响应（插件进程调用）

        与 get_plugin_response() 类似，但不会 pop。
        主要用于超时场景下判断响应是否已经到达（孤儿响应检测）。

        注意：如果响应已过期，会自动清理该响应条目。

        Returns:
            响应数据，如果不存在或已过期则返回 None
        """
        current_time = time.time()

        rid = str(request_id).strip()
        if not rid:
            return None

        response_data = self.plugin_response_map.get(rid, None)
        if response_data is None and request_id != rid:
            # Backward-compatible: tolerate legacy non-string keys stored previously.
            response_data = self.plugin_response_map.get(request_id, None)
        if response_data is None:
            return None

        expire_time = response_data.get("expire_time", 0)
        if current_time > expire_time:
            self.plugin_response_map.pop(rid, None)
            if request_id != rid:
                self.plugin_response_map.pop(request_id, None)
            try:
                event_map = self.plugin_response_event_map
                event_map.pop(rid, None)
                # 同时清理原始 request_id 的事件（如果不同）
                if request_id != rid:
                    event_map.pop(request_id, None)
            except Exception:
                logger.bind(component="server").debug(
                    f"Failed to remove response event for request_id={request_id}", exc_info=True
                )
            return None

        return response_data.get("response")
    
    def cleanup_expired_responses(self) -> int:
        """
        清理过期的响应（主进程定期调用）
        
        Returns:
            清理的响应数量
        """
        current_time = time.time()
        expired_ids = []
        
        # 找出所有过期的响应
        try:
            # 使用快照避免迭代时字典被修改导致 RuntimeError
            resp_map = self.plugin_response_map
            for request_id, response_data in list(resp_map.items()):
                expire_time = response_data.get("expire_time", 0)
                if current_time > expire_time:
                    expired_ids.append(request_id)
        except Exception as e:
            # 如果迭代失败，返回已找到的过期ID数量
            logger.bind(component="server").debug(f"Error iterating expired responses: {e}")
        
        # 删除过期的响应
        resp_map = self.plugin_response_map
        for request_id in expired_ids:
            resp_map.pop(request_id, None)
            try:
                event_map = self.plugin_response_event_map
                rid = str(request_id).strip()
                if rid:
                    event_map.pop(rid, None)
                if request_id != rid:
                    event_map.pop(request_id, None)
            except Exception:
                pass
        
        return len(expired_ids)
    
    def close_plugin_resources(self) -> None:
        """
        清理插件间通信资源（主进程关闭时调用）
        
        包括：
        - 关闭插件间通信队列
        - 清理响应映射
        - 关闭 Manager（如果存在）
        """
        # 清理插件间通信队列
        if self._plugin_comm_queue is not None:
            try:
                self._plugin_comm_queue.cancel_join_thread()  # 防止卡住
                self._plugin_comm_queue.close()
                # self._plugin_comm_queue.join_thread() # 不需要 join，已经 cancel 了
                logger.bind(component="server").debug("Plugin communication queue closed")
            except Exception as e:
                logger.bind(component="server").warning(f"Error closing plugin communication queue: {e}")
            finally:
                self._plugin_comm_queue = None
        
        # 清理响应映射和 Manager
        if self._plugin_response_map_manager is not None:
            try:
                # Manager 的 shutdown() 方法会关闭所有共享对象
                self._plugin_response_map_manager.shutdown()
                self._plugin_response_map = None
                self._plugin_response_event_map = None
                self._plugin_response_notify_event = None
                self._plugin_response_map_manager = None
                logger.bind(component="server").debug("Plugin response map manager shut down")
            except Exception as e:
                logger.bind(component="server").debug(f"Error shutting down plugin response map manager: {e}")

    def cleanup_plugin_comm_resources(self) -> None:
        """Backward-compatible alias for shutdown code paths."""
        self.close_plugin_resources()
    
    def save_frozen_state_memory(self, plugin_id: str, state_data: bytes) -> None:
        """保存插件的冻结状态到内存"""
        with self._frozen_states_lock:
            self._frozen_states[plugin_id] = state_data
    
    def get_frozen_state_memory(self, plugin_id: str) -> Optional[bytes]:
        """从内存获取插件的冻结状态"""
        with self._frozen_states_lock:
            return self._frozen_states.get(plugin_id)
    
    def clear_frozen_state_memory(self, plugin_id: str) -> None:
        """清除插件的内存冻结状态"""
        with self._frozen_states_lock:
            self._frozen_states.pop(plugin_id, None)
    
    def has_frozen_state_memory(self, plugin_id: str) -> bool:
        """检查插件是否有内存冻结状态"""
        with self._frozen_states_lock:
            return plugin_id in self._frozen_states
    
    # ========== 冻结插件状态跟踪 ==========
    
    def mark_plugin_frozen(self, plugin_id: str) -> None:
        """标记插件为冻结状态"""
        with self._frozen_plugins_lock:
            self._frozen_plugins.add(plugin_id)
    
    def unmark_plugin_frozen(self, plugin_id: str) -> None:
        """取消插件的冻结状态标记"""
        with self._frozen_plugins_lock:
            self._frozen_plugins.discard(plugin_id)
    
    def is_plugin_frozen(self, plugin_id: str) -> bool:
        """检查插件是否处于冻结状态"""
        with self._frozen_plugins_lock:
            return plugin_id in self._frozen_plugins
    
    def get_frozen_plugins(self) -> List[str]:
        """获取所有冻结的插件ID列表"""
        with self._frozen_plugins_lock:
            return list(self._frozen_plugins)

    def add_user_context_event(self, bucket_id: str, event: Dict[str, Any]) -> None:
        if not isinstance(bucket_id, str) or not bucket_id:
            bucket_id = "default"

        now = time.time()
        payload: Dict[str, Any] = dict(event) if isinstance(event, dict) else {"event": event}
        payload.setdefault("_ts", float(now))

        with self._user_context_lock:
            dq = self._user_context_store.get(bucket_id)
            if dq is None:
                dq = deque(maxlen=self._user_context_default_maxlen)
                self._user_context_store[bucket_id] = dq
            dq.append(payload)

            ttl = self._user_context_ttl_seconds
            if ttl > 0 and dq:
                cutoff = now - ttl
                while dq and float((dq[0] or {}).get("_ts", 0.0)) < cutoff:
                    dq.popleft()

    def get_user_context(self, bucket_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        if not isinstance(bucket_id, str) or not bucket_id:
            bucket_id = "default"

        n = int(limit) if isinstance(limit, int) else 20
        if n <= 0:
            return []

        now = time.time()
        with self._user_context_lock:
            dq = self._user_context_store.get(bucket_id)
            if not dq:
                return []

            ttl = self._user_context_ttl_seconds
            if ttl > 0 and dq:
                cutoff = now - ttl
                while dq and float((dq[0] or {}).get("_ts", 0.0)) < cutoff:
                    dq.popleft()

            items = list(dq)[-n:]
            return [dict(x) for x in items if isinstance(x, dict)]


# Backward-compatible export name
PluginRuntimeState = GlobalState

# 全局状态实例
state = GlobalState()

