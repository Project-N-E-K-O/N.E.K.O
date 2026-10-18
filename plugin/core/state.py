"""
Plugin runtime-state module

Manage global runtime state for the plugin system.
"""
import asyncio
import itertools
import threading
import time
import uuid
from collections import deque
import multiprocessing
from typing import Any, Callable, Deque, Dict, List, Optional, Set

from plugin._types.events import EventHandler
from plugin.logging_config import get_logger
from plugin.settings import (
    EVENT_QUEUE_MAX,
    LIFECYCLE_QUEUE_MAX,
    resolve_message_plane_rpc_endpoint,
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

from plugin.core.message_plane_transport import format_rpc_error
from contextlib import contextmanager


MAX_DELETED_BUS_IDS = 20000

# Default lock timeout in seconds.
DEFAULT_LOCK_TIMEOUT = 10.0

logger = get_logger("core.state")


class RWLock:
    """
    Reader/writer lock implementation.
    
    Allow concurrent reads but exclusive writes.
    Writers wait for all readers; readers wait for writers.
    """
    
    def __init__(self):
        self._read_ready = threading.Condition(threading.Lock())
        self._readers = 0
        self._writers_waiting = 0
        self._writer_active = False
    
    def acquire_read(self, timeout: float = DEFAULT_LOCK_TIMEOUT) -> bool:
        """Acquire a read lock."""
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
        """Release a read lock."""
        with self._read_ready:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()
    
    def acquire_write(self, timeout: float = DEFAULT_LOCK_TIMEOUT) -> bool:
        """Acquire a write lock."""
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
        """Release a write lock."""
        with self._read_ready:
            self._writer_active = False
            self._read_ready.notify_all()
    
    @contextmanager
    def read_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT, name: str = "unknown"):
        """Read-lock context manager."""
        if not self.acquire_read(timeout):
            logger.error(f"Failed to acquire read lock '{name}' within {timeout}s")
            raise TimeoutError(f"Failed to acquire read lock '{name}' within {timeout}s")
        try:
            yield
        finally:
            self.release_read()
    
    @contextmanager
    def write_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT, name: str = "unknown"):
        """Write-lock context manager."""
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
    Context manager for timed lock acquisition.
    
    Args:
        lock: Lock to acquire.
        timeout: Timeout in seconds.
        name: Lock name for logging.
    
    Raises:
        TimeoutError: If acquisition exceeds timeout.
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


def _start_method_inherits_manager_proxies() -> bool:
    """Whether children inherit this process's global response-map proxies.

    Spawn and forkserver start from fresh module state, and PluginHost does
    not pass these proxies as launch arguments. Fork retains the existing
    shared-map behavior. An unknown context keeps the conservative fallback.
    """
    try:
        return multiprocessing.get_start_method() == "fork"
    except (RuntimeError, ValueError):
        return True


class GlobalState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        
        # In-memory freeze storage.
        self._frozen_states: Dict[str, bytes] = {}  # plugin_id -> serialized state
        self._frozen_states_lock = threading.Lock()
        
        # Track frozen plugins.
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
        
        # Replace mutexes with reader/writer locks for concurrent reads.
        self._plugins_rwlock = RWLock()
        self._plugin_hosts_rwlock = RWLock()
        self._event_handlers_rwlock = RWLock()

        self._event_queue: Optional[asyncio.Queue] = None
        self._lifecycle_queue: Optional[asyncio.Queue] = None
        self._message_queue: Optional[asyncio.Queue] = None
        self._plugin_comm_queue: Optional[asyncio.Queue] = None
        self._plugin_response_map: Optional[Any] = None
        self._plugin_response_map_manager: Optional[Any] = None
        self._plugin_response_event_map: Optional[Any] = None
        self._plugin_response_notify_event: Optional[Any] = None
        self._is_plugin_child_process = False
        # True means response/event maps are ordinary process-local dicts without Manager. Then
        # _get_or_create_response_event must create threading.Event instead of mgr.Event(),
        # or absent managers yield None and degrade event wakeups to short polling.
        self._response_maps_are_local = False
        self._plugin_comm_lock = threading.Lock()

        # Per-plugin downlink senders for routing plugin-to-plugin responses
        self._plugin_downlink_senders: Dict[str, Any] = {}
        self._plugin_downlink_senders_lock = threading.Lock()

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
        from config import PLUGIN_USER_CONTEXT_MAX_ITEMS
        self._user_context_default_maxlen: int = PLUGIN_USER_CONTEXT_MAX_ITEMS
        self._user_context_ttl_seconds: float = 60.0 * 60.0

        self._message_plane_rpc_lock = threading.Lock()
        self._message_plane_rpc: Optional[Any] = None
        
        # Snapshot caching reduces lock contention.
        self._snapshot_cache_lock = threading.Lock()
        self._snapshot_cache: Dict[str, Dict[str, Any]] = {
            "plugins": {"data": None, "timestamp": 0.0},
            "hosts": {"data": None, "timestamp": 0.0},
            "handlers": {"data": None, "timestamp": 0.0},
        }
        self._snapshot_cache_ttl: float = 0.5  # 500 ms cache TTL.
        # Increment generation on invalidation; readers capture it before snapshotting and refuse stale
        # cache writes if intervening mutation changed generations.
        self._snapshot_cache_gen: Dict[str, int] = {key: 0 for key in self._snapshot_cache}

    # RWLock write context for mutations.
    @contextmanager
    def acquire_plugins_write_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """Acquire plugins write lock for mutations."""
        with self._plugins_rwlock.write_lock(timeout, "plugins_write"):
            yield
    
    @contextmanager
    def acquire_plugin_hosts_write_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """Acquire plugin_hosts write lock for mutations."""
        with self._plugin_hosts_rwlock.write_lock(timeout, "plugin_hosts_write"):
            yield
    
    @contextmanager
    def acquire_event_handlers_write_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """Acquire event_handlers write lock for mutations."""
        with self._event_handlers_rwlock.write_lock(timeout, "event_handlers_write"):
            yield
    
    # RWLock read context for reads.
    @contextmanager
    def acquire_plugins_read_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """Acquire plugins read lock for concurrent reads."""
        with self._plugins_rwlock.read_lock(timeout, "plugins_read"):
            yield
    
    @contextmanager
    def acquire_plugin_hosts_read_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """Acquire plugin_hosts read lock for concurrent reads."""
        with self._plugin_hosts_rwlock.read_lock(timeout, "plugin_hosts_read"):
            yield
    
    @contextmanager
    def acquire_event_handlers_read_lock(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """Acquire event_handlers read lock for concurrent reads."""
        with self._event_handlers_rwlock.read_lock(timeout, "event_handlers_read"):
            yield

    def get_plugins_snapshot(self, timeout: float = 2.0) -> Dict[str, Dict[str, Any]]:
        """Snapshot plugins using reader/writer read locks, allowing concurrent reads.
        
        Args:
            timeout: Acquisition timeout in seconds, default 2.
            
        Returns:
            Shallow copy of the plugins dict.
            
        Note:
            Timeout returns an empty dict rather than raising.
        """
        if not self._plugins_rwlock.acquire_read(timeout=timeout):
            logger.warning("Failed to acquire plugins read lock within {}s, returning empty snapshot", timeout)
            return {}
        try:
            return dict(self.plugins)
        finally:
            self._plugins_rwlock.release_read()

    def get_plugin_hosts_snapshot(self, timeout: float = 2.0) -> Dict[str, Any]:
        """Snapshot plugin_hosts using reader/writer read locks, allowing concurrent reads.
        
        Args:
            timeout: Acquisition timeout in seconds, default 2.
            
        Returns:
            Shallow copy of the plugin_hosts dict.
            
        Note:
            Timeout returns an empty dict rather than raising.
        """
        if not self._plugin_hosts_rwlock.acquire_read(timeout=timeout):
            logger.warning("Failed to acquire plugin_hosts read lock within {}s, returning empty snapshot", timeout)
            return {}
        try:
            return dict(self.plugin_hosts)
        finally:
            self._plugin_hosts_rwlock.release_read()

    def get_event_handlers_snapshot(self, timeout: float = 2.0) -> Dict[str, EventHandler]:
        """Snapshot event_handlers using reader/writer read locks, allowing concurrent reads.
        
        Args:
            timeout: Acquisition timeout in seconds, default 2.
            
        Returns:
            Shallow copy of the event_handlers dict.
            
        Note:
            Timeout returns an empty dict rather than raising.
        """
        if not self._event_handlers_rwlock.acquire_read(timeout=timeout):
            logger.warning("Failed to acquire event_handlers read lock within {}s, returning empty snapshot", timeout)
            return {}
        try:
            return dict(self.event_handlers)
        finally:
            self._event_handlers_rwlock.release_read()
    
    def _snapshot_cache_generation(self, cache_type: str) -> int:
        with self._snapshot_cache_lock:
            return self._snapshot_cache_gen[cache_type]

    def get_event_handlers_revision(self) -> int:
        """Increment on entry registration/removal, including dynamic enablement, not execution.

        Acquire only the short snapshot-cache lock, never handler RWLocks; safe synchronously on event loops.
        """
        return self._snapshot_cache_generation("handlers")

    def _store_snapshot_cache(self, cache_type: str, snapshot: Dict[str, Any], now: float, generation: int) -> None:
        """Cache snapshots only if no invalidation occurred while collecting them."""
        with self._snapshot_cache_lock:
            if self._snapshot_cache_gen[cache_type] != generation:
                return
            self._snapshot_cache[cache_type]["data"] = snapshot
            self._snapshot_cache[cache_type]["timestamp"] = now

    def _get_snapshot_nowait(self, cache_type: str, rwlock: "RWLock", source: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Cached snapshots never wait for RWLocks and are safe on event-loop threads.

        Return fresh cache immediately; otherwise try a nonblocking read lock once and refresh on success.
        If writers hold/queue the lock, return prior possibly stale snapshots,
        or None if never cached. Contention never stores empty dictionaries in shared caches.
        """
        now = time.time()
        with self._snapshot_cache_lock:
            cache = self._snapshot_cache[cache_type]
            cached = cache["data"]
            if cached is not None and (now - cache["timestamp"]) < self._snapshot_cache_ttl:
                return dict(cached)
            generation = self._snapshot_cache_gen[cache_type]
        if not rwlock.acquire_read(timeout=0):
            return dict(cached) if cached is not None else None
        try:
            snapshot = dict(source)
        finally:
            rwlock.release_read()
        self._store_snapshot_cache(cache_type, snapshot, now, generation)
        return dict(snapshot)

    def get_plugins_snapshot_nowait(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """Nonwaiting plugins snapshot; see _get_snapshot_nowait semantics."""
        return self._get_snapshot_nowait("plugins", self._plugins_rwlock, self.plugins)

    def get_plugins_snapshot_cached(self, timeout: float = 2.0, force: bool = False) -> Dict[str, Dict[str, Any]]:
        """Cached plugins snapshot reducing contention.
        
        Args:
            timeout: Lock-acquisition timeout in seconds, default 2.
            force: Force cache refresh.
            
        Returns:
            Shallow copy of plugins.
        """
        import time
        now = time.time()
        
        # Check the cache
        if not force:
            with self._snapshot_cache_lock:
                cache = self._snapshot_cache["plugins"]
                if cache["data"] is not None and (now - cache["timestamp"]) < self._snapshot_cache_ttl:
                    return dict(cache["data"])
        
        # Cache invalid/force refresh: obtain a fresh snapshot.
        generation = self._snapshot_cache_generation("plugins")
        snapshot = self.get_plugins_snapshot(timeout=timeout)
        
        # Update cache unless invalidated while snapshotting.
        self._store_snapshot_cache("plugins", snapshot, now, generation)
        
        return snapshot
    
    def get_plugin_hosts_snapshot_cached(self, timeout: float = 2.0, force: bool = False) -> Dict[str, Any]:
        """Cached plugin_hosts snapshot reducing contention.
        
        Args:
            timeout: Lock-acquisition timeout in seconds, default 2.
            force: Force cache refresh.
            
        Returns:
            Shallow copy of plugin_hosts.
        """
        import time
        now = time.time()
        
        # Check the cache
        if not force:
            with self._snapshot_cache_lock:
                cache = self._snapshot_cache["hosts"]
                if cache["data"] is not None and (now - cache["timestamp"]) < self._snapshot_cache_ttl:
                    return dict(cache["data"])
        
        # Cache invalid/force refresh: obtain a fresh snapshot.
        generation = self._snapshot_cache_generation("hosts")
        snapshot = self.get_plugin_hosts_snapshot(timeout=timeout)
        
        # Update cache unless invalidated while snapshotting.
        self._store_snapshot_cache("hosts", snapshot, now, generation)
        
        return snapshot
    
    def get_plugin_hosts_snapshot_nowait(self) -> Optional[Dict[str, Any]]:
        """Nonwaiting plugin_hosts snapshot; see _get_snapshot_nowait semantics."""
        return self._get_snapshot_nowait("hosts", self._plugin_hosts_rwlock, self.plugin_hosts)

    def get_event_handlers_snapshot_cached(self, timeout: float = 2.0, force: bool = False) -> Dict[str, EventHandler]:
        """Cached event_handlers snapshot reducing contention.
        
        Args:
            timeout: Lock-acquisition timeout in seconds, default 2.
            force: Force cache refresh.
            
        Returns:
            Shallow copy of event_handlers.
        """
        import time
        now = time.time()
        
        # Check the cache
        if not force:
            with self._snapshot_cache_lock:
                cache = self._snapshot_cache["handlers"]
                if cache["data"] is not None and (now - cache["timestamp"]) < self._snapshot_cache_ttl:
                    return dict(cache["data"])
        
        # Cache invalid/force refresh: obtain a fresh snapshot.
        snapshot = self.get_event_handlers_snapshot(timeout=timeout)
        
        # Update cache.
        with self._snapshot_cache_lock:
            self._snapshot_cache["handlers"]["data"] = snapshot
            self._snapshot_cache["handlers"]["timestamp"] = now
        
        return snapshot
    
    def invalidate_snapshot_cache(self, cache_type: Optional[str] = None) -> None:
        """Invalidate snapshot caches.
        
        Args:
            cache_type: plugins/hosts/handlers, or None for all.
        """
        with self._snapshot_cache_lock:
            if cache_type is None:
                # Invalidate everything.
                for key in self._snapshot_cache:
                    self._snapshot_cache[key]["timestamp"] = 0.0
                    self._snapshot_cache_gen[key] += 1
            elif cache_type in self._snapshot_cache:
                # Invalidate a specified type.
                self._snapshot_cache[cache_type]["timestamp"] = 0.0
                self._snapshot_cache_gen[cache_type] += 1

    def register_event_handler(self, plugin_id: str, handler: EventHandler) -> None:
        meta = getattr(handler, "meta", None)
        event_type = getattr(meta, "event_type", None) or "plugin_entry"
        entry_id = getattr(meta, "id", None)
        if not isinstance(plugin_id, str) or not plugin_id:
            raise ValueError("plugin_id is required")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("handler.meta.id is required")

        with self.acquire_event_handlers_write_lock():
            if event_type == "plugin_entry":
                self.event_handlers[f"{plugin_id}.{entry_id}"] = handler
                self.event_handlers[f"{plugin_id}:plugin_entry:{entry_id}"] = handler
            else:
                self.event_handlers[f"{plugin_id}:{event_type}:{entry_id}"] = handler
        self.invalidate_snapshot_cache("handlers")

    def unregister_event_handler(self, plugin_id: str, entry_id: str, event_type: str = "plugin_entry") -> None:
        if not isinstance(plugin_id, str) or not plugin_id:
            raise ValueError("plugin_id is required")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("entry_id is required")

        with self.acquire_event_handlers_write_lock():
            if event_type == "plugin_entry":
                self.event_handlers.pop(f"{plugin_id}.{entry_id}", None)
                self.event_handlers.pop(f"{plugin_id}:plugin_entry:{entry_id}", None)
            else:
                self.event_handlers.pop(f"{plugin_id}:{event_type}:{entry_id}", None)
        self.invalidate_snapshot_cache("handlers")
    
    @contextmanager
    def acquire_locks_in_order(self, *lock_names: str, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """Acquire multiple locks in order to prevent deadlocks.
        
        Args:
            lock_names: Names in required plugins/hosts/handlers order.
            timeout: Acquisition timeout per lock.
            
        Example:
            with state.acquire_locks_in_order('plugins', 'hosts'):
                # Safely access plugins and plugin_hosts.
                ...
        """
        lock_order = {'plugins': 1, 'hosts': 2, 'handlers': 3}
        lock_map = {
            'plugins': self._plugins_rwlock,
            'hosts': self._plugin_hosts_rwlock,
            'handlers': self._event_handlers_rwlock,
        }
        
        # Validate lock names/order.
        for name in lock_names:
            if name not in lock_order:
                raise ValueError(f"Unknown lock name: {name}")
        
        # Check correct ordering.
        sorted_names = sorted(lock_names, key=lambda n: lock_order[n])
        if list(lock_names) != sorted_names:
            logger.warning(
                "Lock acquisition order violation detected! Expected: {}, Got: {}",
                sorted_names, list(lock_names)
            )
            raise RuntimeError(f"Lock order violation: {list(lock_names)} should be {sorted_names}")
        
        # Acquire locks in order.
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
            # Release acquired locks in reverse order.
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
    def plugin_comm_queue(self) -> asyncio.Queue:
        """Central in-process queue for plugin-to-plugin requests (asyncio.Queue)."""
        if self._plugin_comm_queue is None:
            with self._plugin_comm_lock:
                if self._plugin_comm_queue is None:
                    self._plugin_comm_queue = asyncio.Queue(maxsize=MESSAGE_QUEUE_MAX)
        return self._plugin_comm_queue

    def clear_inherited_plugin_references(self) -> None:
        """Drop parent-only references without acquiring inherited locks after fork."""
        self.plugin_hosts.clear()
        self._plugin_downlink_senders.clear()
        self._plugin_downlink_senders_lock = threading.Lock()

    def register_downlink_sender(self, plugin_id: str, sender: Any) -> None:
        """Register a comm_manager's ``send_plugin_response`` coroutine for routing."""
        pid = str(plugin_id).strip()
        if not pid:
            return
        with self._plugin_downlink_senders_lock:
            self._plugin_downlink_senders[pid] = sender

    def get_downlink_sender(self, plugin_id: str) -> Any:
        pid = str(plugin_id).strip()
        if not pid:
            return None
        with self._plugin_downlink_senders_lock:
            return self._plugin_downlink_senders.get(pid)

    def remove_downlink_sender(self, plugin_id: str) -> None:
        pid = str(plugin_id).strip()
        if not pid:
            return
        with self._plugin_downlink_senders_lock:
            self._plugin_downlink_senders.pop(pid, None)
    
    def _initialize_response_maps(self) -> None:
        if self._is_plugin_child_process or not _start_method_inherits_manager_proxies():
            self._plugin_response_map = {}
            self._response_maps_are_local = True
            if self._plugin_response_event_map is None:
                self._plugin_response_event_map = {}
        else:
            if self._plugin_response_map_manager is None:
                self._plugin_response_map_manager = multiprocessing.Manager()
            self._plugin_response_map = self._plugin_response_map_manager.dict()
            if self._plugin_response_event_map is None:
                self._plugin_response_event_map = self._plugin_response_map_manager.dict()

    @property
    def plugin_response_map(self) -> Any:
        """Shared response maps under fork, otherwise process-local maps."""
        if self._plugin_response_map is None:
            with self._plugin_comm_lock:
                if self._plugin_response_map is None:
                    self._initialize_response_maps()
        return self._plugin_response_map

    def mark_plugin_child_process(self) -> None:
        """Mark this process as a plugin child so it never starts its own Manager."""
        self._is_plugin_child_process = True

    @property
    def plugin_response_event_map(self) -> Any:
        """Cross-process response notifications mapping request_id to Event."""
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
            if mgr is None and not self._response_maps_are_local:
                _ = self.plugin_response_map
                mgr = self._plugin_response_map_manager
            if mgr is None:
                if self._is_plugin_child_process or not self._response_maps_are_local:
                    # Child-plugin responses use ZMQ, so nobody sets this event; preserve caller short-poll
                    # fallback as before.
                    return None
                # Server processes with spawn-local maps use threading.Event.
                # Its set/wait/clear API matches mgr.Event, and all waiting happens in
                # this process's thread pool, as documented at call sites,
                # so thread events suffice while avoiding per-wait/set manager round trips.
                ev = threading.Event()
            else:
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
            # Prefer environment ports: runners relocate planes on conflicts,
            # while module constants freeze at import time. See
            # settings.resolve_message_plane_rpc_endpoint。
            sock.connect(resolve_message_plane_rpc_endpoint())
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
            # Rebuild deque excluding deleted records.
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
        Set plugin responses from the main process.
        
        Args:
            request_id: Request ID.
            response: Response data.
            timeout: Seconds until expiry.
        """
        rid = str(request_id).strip()
        if not rid:
            return
        # Store responses with current time + timeout + buffer expiration.
        # Buffer accommodates network latency etc.
        expire_time = time.time() + timeout + 1.0  # Extra 1 s buffer.
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
        Get and remove plugin responses from plugin processes.
        
        Expired responses are cleaned automatically and return None.
        
        Returns:
            Response data, or None if missing/expired.
        """
        current_time = time.time()

        rid = str(request_id).strip()
        if not rid:
            return None

        resp_map = self.plugin_response_map
        response_data = resp_map.pop(rid, None)

        if response_data is None:
            return None

        expire_time = response_data.get("expire_time", 0)
        if current_time > expire_time:
            try:
                event_map = self.plugin_response_event_map
                event_map.pop(rid, None)
                # Also clear original request_id events when different.
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
            # Also clear original request_id events when different.
            if request_id != rid:
                event_map.pop(request_id, None)
        except Exception:
            logger.bind(component="server").debug(
                f"Failed to remove response event for request_id={request_id}", exc_info=True
            )
        # Return actual response data.
        return response_data.get("response")

    async def wait_for_plugin_response_async(self, request_id: str, timeout: float) -> Optional[Dict[str, Any]]:
        """Async response waiting eliminates polling through pure event-driven behavior.
        
        Unlike synchronous time.sleep polling, use asyncio.Event
        for zero-delay response notification.
        """
        rid = str(request_id).strip()
        if not rid:
            return None
        
        # Fast path checks already-arrived responses first.
        got = self.get_plugin_response(rid)
        if got is not None:
            return got
        
        # Create async waiting events.
        async_event = asyncio.Event()
        
        # Register callbacks for immediate arrival notification.
        def check_and_notify():
            response = self.get_plugin_response(rid)
            if response is not None:
                async_event.set()
                return response
            return None
        
        # Enforce timeout with asyncio.wait_for.
        try:
            # Start short-interval background polling.
            async def wait_with_polling():
                # Check once first.
                result = check_and_notify()
                if result is not None:
                    return result
                
                # Await events or use short polling as fallback.
                while True:
                    # Try synchronous events when available.
                    per_req_ev = None
                    try:
                        per_req_ev = self._get_or_create_response_event(rid)
                    except Exception:
                        pass
                    
                    if per_req_ev is not None:
                        # Wait on thread events through the thread pool.
                        loop = asyncio.get_running_loop()
                        ev = per_req_ev  # Capture variables against None-type problems.
                        await loop.run_in_executor(
                            None,
                            lambda ev=ev: ev.wait(timeout=0.01)
                        )
                    else:
                        # Fallback: brief sleep.
                        await asyncio.sleep(0.001)  # 1 ms, ten times faster than the synchronous 10 ms interval.
                    
                    # Check responses.
                    result = check_and_notify()
                    if result is not None:
                        return result
                    
                    # Avoid spinning on set events whose responses were consumed.
                    await asyncio.sleep(0.005)
            
            return await asyncio.wait_for(wait_with_polling(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
    
    def wait_for_plugin_response(self, request_id: str, timeout: float) -> Optional[Dict[str, Any]]:
        """Synchronous: block until response arrives or timeout, then pop and return it.

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
        """Async: get plugin responses without removing them."""
        # Peek never waits, so call the synchronous version directly.
        return self.peek_plugin_response(request_id)
    
    def peek_plugin_response(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get plugin responses without removal, called by plugin processes.

        Like get_plugin_response(), without pop.
        Mainly checks whether responses already arrived on timeout, detecting orphan responses.

        Expired response entries are still removed automatically.

        Returns:
            Response data, or None if missing/expired.
        """
        current_time = time.time()

        rid = str(request_id).strip()
        if not rid:
            return None

        response_data = self.plugin_response_map.get(rid, None)
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
                # Also clear original request_id events when different.
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
        Clean expired responses, periodically called by main processes.
        
        Returns:
            Number removed.
        """
        current_time = time.time()
        expired_ids = []
        
        # Find all expired responses.
        try:
            # Snapshot to avoid dictionary-mutation RuntimeError during iteration.
            resp_map = self.plugin_response_map
            for request_id, response_data in list(resp_map.items()):
                expire_time = response_data.get("expire_time", 0)
                if current_time > expire_time:
                    expired_ids.append(request_id)
        except Exception as e:
            # If iteration fails, return the expired-ID count already found.
            logger.bind(component="server").debug(f"Error iterating expired responses: {e}")
        
        # Delete expired responses.
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
        Clean inter-plugin communication resources on main-process shutdown.
        
        Includes:
        - Closing inter-plugin queues.
        - Clearing response maps.
        - Shutting down Manager, if present.
        """
        # Reset the in-process comm queue
        self._plugin_comm_queue = None
        with self._plugin_downlink_senders_lock:
            self._plugin_downlink_senders.clear()
        
        # Both local maps and Manager proxies belong to this server run.
        with self._plugin_comm_lock:
            try:
                if self._plugin_response_map_manager is not None:
                    self._plugin_response_map_manager.shutdown()
                    logger.bind(component="server").debug("Plugin response map manager shut down")
            except Exception as e:
                logger.bind(component="server").debug(f"Error shutting down plugin response map manager: {e}")
            finally:
                self._plugin_response_map = None
                self._plugin_response_event_map = None
                self._plugin_response_notify_event = None
                self._plugin_response_map_manager = None
                self._response_maps_are_local = False

    def save_frozen_state_memory(self, plugin_id: str, state_data: bytes) -> None:
        """Store plugin frozen state in memory."""
        with self._frozen_states_lock:
            self._frozen_states[plugin_id] = state_data
    
    def get_frozen_state_memory(self, plugin_id: str) -> Optional[bytes]:
        """Get plugin frozen state from memory."""
        with self._frozen_states_lock:
            return self._frozen_states.get(plugin_id)
    
    def clear_frozen_state_memory(self, plugin_id: str) -> None:
        """Clear plugin frozen state from memory."""
        with self._frozen_states_lock:
            self._frozen_states.pop(plugin_id, None)
    
    def has_frozen_state_memory(self, plugin_id: str) -> bool:
        """Check whether plugin frozen state exists in memory."""
        with self._frozen_states_lock:
            return plugin_id in self._frozen_states
    
    # -- Frozen-plugin tracking --
    
    def mark_plugin_frozen(self, plugin_id: str) -> None:
        """Mark plugins frozen."""
        with self._frozen_plugins_lock:
            self._frozen_plugins.add(plugin_id)
    
    def unmark_plugin_frozen(self, plugin_id: str) -> None:
        """Unmark plugins frozen."""
        with self._frozen_plugins_lock:
            self._frozen_plugins.discard(plugin_id)
    
    def is_plugin_frozen(self, plugin_id: str) -> bool:
        """Check whether plugins are frozen."""
        with self._frozen_plugins_lock:
            return plugin_id in self._frozen_plugins
    
    def get_frozen_plugins(self) -> List[str]:
        """List all frozen plugin IDs."""
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

# Global state instance.
state = GlobalState()


# Self-register the user-context bus consumer with main_logic's event bus.
# Why
# ---
# ``main_logic.core._publish_user_utterance_to_plugin_bus`` previously called
# ``state.add_user_context_event`` directly — a layering inversion (L2 → L4)
# now banned by ``scripts/check_module_layering.py``. After the inversion was
# removed, ``main_logic.core`` calls ``dispatch_user_utterance`` instead, which
# fans out to whatever the higher layers subscribed. Registering ourselves here
# (rather than only in ``app/runtime_bindings.py``) keeps the dispatcher path
# alive for any context that imports ``plugin.core.state`` directly — testbench
# / ad-hoc scripts that previously relied on the chained-import side effect.
# ``register_user_utterance_sink`` dedupes on identity, so the explicit
# wiring in ``app/runtime_bindings.py`` ends up as a no-op when this self-
# registration has already fired.
#
# Wrapped in try/except: plugin subprocess workers can be loaded in
# environments where ``main_logic`` isn't on sys.path; in that case the bus
# isn't reachable and we silently skip — matching the historical
# "main_logic-less" plugin runtime contract.
try:
    from main_logic.agent_event_bus import register_user_utterance_sink as _register_sink
    _register_sink(state.add_user_context_event)
except Exception as _exc:
    # Distinguish expected partial-env from real regression:
    #   * ``ModuleNotFoundError(name='main_logic'...)`` → plugin subprocess
    #     worker without main_logic on sys.path. Legit; stay silent.
    #   * Anything else (transitive import broken inside
    #     ``main_logic.agent_event_bus``, ``register_*`` signature
    #     change, etc.) → regression worth logging. Codex P2 catch.
    _expected_absent = {"main_logic", "main_logic.agent_event_bus"}
    _is_expected_absent = (
        isinstance(_exc, ModuleNotFoundError)
        and getattr(_exc, "name", None) in _expected_absent
    )
    if not _is_expected_absent:
        logger.warning(
            "plugin.core.state: failed to self-register user_utterance_sink",
            exc_info=True,
        )
