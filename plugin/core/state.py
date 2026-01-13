"""
插件运行时状态模块

提供插件系统的全局运行时状态管理。
"""
import asyncio
import logging
import threading
import time
from collections import deque
import itertools
import multiprocessing
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple, cast

from plugin.sdk.events import EventHandler
from plugin.settings import EVENT_QUEUE_MAX, LIFECYCLE_QUEUE_MAX, MESSAGE_QUEUE_MAX


MAX_DELETED_BUS_IDS = 20000


class BusChangeHub:
    def __init__(self) -> None:
        """
        Initialize the BusChangeHub internal state.
        
        Creates a thread lock, the next subscription id counter, and empty subscription mappings for the three supported buses: "messages", "events", and "lifecycle".
        """
        self._lock = threading.Lock()
        self._next_id = 1
        self._subs: Dict[str, Dict[int, Callable[[str, Dict[str, Any]], None]]] = {
            "messages": {},
            "events": {},
            "lifecycle": {},
        }

    def subscribe(self, bus: str, callback: Callable[[str, Dict[str, Any]], None]) -> Callable[[], None]:
        """
        Subscribe a callback to a named bus and return an unsubscribe function.
        
        Parameters:
            bus (str): Bus name to subscribe to (one of "messages", "events", "lifecycle").
            callback (Callable[[str, Dict[str, Any]], None]): Function called for each bus change; receives an operation string and a payload dict.
        
        Returns:
            Callable[[], None]: A function that, when called, removes this subscription.
        
        Raises:
            ValueError: If `bus` is not a recognized bus name.
        """
        b = str(bus).strip()
        if b not in self._subs:
            raise ValueError(f"Unknown bus: {bus!r}")
        with self._lock:
            sid = self._next_id
            self._next_id += 1
            self._subs[b][sid] = callback

        def _unsub() -> None:
            """
            Unsubscribe the previously registered callback for the captured bus and subscription id.
            
            Removes the subscription identified by the captured `sid` from the captured bus `b` if present. Operation is performed with the instance lock to ensure thread-safety.
            """
            with self._lock:
                self._subs.get(b, {}).pop(sid, None)

        return _unsub

    def emit(self, bus: str, op: str, payload: Dict[str, Any]) -> None:
        """
        Dispatches an operation and payload to all subscribers of the specified bus.
        
        Parameters:
        	bus (str): Name of the bus to emit to; unknown buses are ignored.
        	op (str): Operation name passed to each subscriber callback.
        	payload (Dict[str, Any]): Data passed to callbacks; a shallow copy is given to each callback if a dict, otherwise an empty dict is passed.
        
        Notes:
        	Errors raised by individual callbacks are caught and logged at debug level; other callbacks continue to be invoked.
        """
        b = str(bus).strip()
        if b not in self._subs:
            return
        with self._lock:
            callbacks = list(self._subs[b].values())
        for cb in callbacks:
            try:
                cb(str(op), dict(payload) if isinstance(payload, dict) else {})
            except Exception:
                import logging
                logging.getLogger("user_plugin_server").debug(
                    f"BusChangeHub callback error for bus={bus}, op={op}", exc_info=True
                )
                continue


class PluginRuntimeState:
    """插件运行时状态"""
    
    def __init__(self):
        """
        Initialize the PluginRuntimeState internal structures and synchronization primitives.
        
        Sets up thread-safe registries and locks for plugins, instances, event handlers, hosts, and plugin status; lazily-backed asyncio and cross-process communication placeholders for messages, events, lifecycle and plugin responses; in-memory bounded stores and deletion tracking for messages/events/lifecycle; per-bus revision counters and a BusChangeHub for change notifications; per-bus subscription containers; and a per-bucket user-context store with default retention settings.
        """
        self.plugins: Dict[str, Dict[str, Any]] = {}
        self.plugin_instances: Dict[str, Any] = {}
        self.event_handlers: Dict[str, EventHandler] = {}
        self.plugin_status: Dict[str, Dict[str, Any]] = {}
        self.plugin_hosts: Dict[str, Any] = {}
        self.plugin_status_lock = threading.Lock()
        self.plugins_lock = threading.Lock()  # 保护 plugins 字典的线程安全
        self.event_handlers_lock = threading.Lock()  # 保护 event_handlers 字典的线程安全
        self.plugin_hosts_lock = threading.Lock()  # 保护 plugin_hosts 字典的线程安全
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
        }

        self.bus_change_hub = BusChangeHub()

        self._bus_subscriptions_lock = threading.Lock()
        self._bus_subscriptions: Dict[str, Dict[str, Dict[str, Any]]] = {
            "messages": {},
            "events": {},
            "lifecycle": {},
        }

        self._user_context_lock = threading.Lock()
        self._user_context_store: Dict[str, Deque[Dict[str, Any]]] = {}
        self._user_context_default_maxlen: int = 200
        self._user_context_ttl_seconds: float = 60.0 * 60.0

    def _bump_bus_rev(self, bus: str) -> int:
        """
        Increment the revision counter for the specified bus and return the new revision.
        
        Parameters:
            bus (str): Bus name (for example "messages", "events", or "lifecycle").
        
        Returns:
            int: The updated revision number for the given bus.
        """
        b = str(bus).strip()
        with self._bus_rev_lock:
            cur = int(self._bus_rev.get(b, 0))
            cur += 1
            self._bus_rev[b] = cur
            return cur

    def get_bus_rev(self, bus: str) -> int:
        """
        Get the current revision counter for the specified bus.
        
        Parameters:
            bus (str): Bus name (converted to string and trimmed); leading/trailing whitespace is ignored.
        
        Returns:
            int: The current revision number for the bus, or 0 if the bus has no recorded revisions.
        """
        b = str(bus).strip()
        with self._bus_rev_lock:
            return int(self._bus_rev.get(b, 0))

    @property
    def event_queue(self) -> asyncio.Queue:
        """
        Get the asyncio Queue used for incoming event records, creating it lazily with the configured maximum size if it does not exist.
        
        Returns:
            asyncio.Queue: The queue instance for event records (maxsize set to EVENT_QUEUE_MAX).
        """
        if self._event_queue is None:
            self._event_queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAX)
        return self._event_queue

    @property
    def lifecycle_queue(self) -> asyncio.Queue:
        """
        Provide the lifecycle asyncio queue, creating it on first access.
        
        Returns:
            asyncio.Queue: Queue used to hold lifecycle records, initialized with max size LIFECYCLE_QUEUE_MAX.
        """
        if self._lifecycle_queue is None:
            self._lifecycle_queue = asyncio.Queue(maxsize=LIFECYCLE_QUEUE_MAX)
        return self._lifecycle_queue

    @property
    def message_queue(self) -> asyncio.Queue:
        """
        Lazily create and return the asyncio queue used for message records.
        
        Returns:
            asyncio.Queue: Queue for message records with a configured max size of MESSAGE_QUEUE_MAX.
        """
        if self._message_queue is None:
            self._message_queue = asyncio.Queue(maxsize=MESSAGE_QUEUE_MAX)
        return self._message_queue
    
    @property
    def plugin_comm_queue(self):
        """
        Provide a cross-process queue used for plugin-to-plugin communication.
        
        This property returns a multiprocessing.Queue that is created on first access and is safe to use across processes for sending plugin communication messages (e.g., custom_event calls).
        
        Returns:
            multiprocessing.Queue: The shared queue instance for plugin communication.
        """
        if self._plugin_comm_queue is None:
            with self._plugin_comm_lock:
                if self._plugin_comm_queue is None:
                    # 使用 multiprocessing.Queue 因为需要跨进程
                    self._plugin_comm_queue = multiprocessing.Queue()
        return self._plugin_comm_queue

    def set_plugin_response_queue(self, plugin_id: str, q: Any) -> None:
        """
        Register a response queue for a plugin identifier.
        
        Stores the provided queue-like object in the per-plugin response queue registry (replacing any existing entry). If `plugin_id` is empty after stripping, the call is ignored. The operation is performed under a lock for thread safety.
        
        Parameters:
            plugin_id (str): Identifier of the plugin.
            q (Any): Queue-like object to receive responses for the plugin.
        """
        pid = str(plugin_id).strip()
        if not pid:
            return
        with self._plugin_response_queues_lock:
            self._plugin_response_queues[pid] = q

    def get_plugin_response_queue(self, plugin_id: str) -> Any:
        """
        Retrieve the response queue associated with a plugin identifier.
        
        Parameters:
            plugin_id (str): Plugin identifier; leading/trailing whitespace is ignored. If empty after stripping, no queue is returned.
        
        Returns:
            Any: The plugin's response queue object, or `None` if no queue exists for the given `plugin_id` or if `plugin_id` is empty.
        """
        pid = str(plugin_id).strip()
        if not pid:
            return None
        with self._plugin_response_queues_lock:
            return self._plugin_response_queues.get(pid)

    def remove_plugin_response_queue(self, plugin_id: str) -> None:
        """
        Remove the stored response queue for a plugin, if one exists.
        
        If `plugin_id` is empty after conversion to string and trimming, this is a no-op.
        Otherwise the entry for the plugin is removed from the internal response-queue registry without raising an error.
        
        Parameters:
            plugin_id (str): Identifier of the plugin whose response queue should be removed.
        """
        pid = str(plugin_id).strip()
        if not pid:
            return
        with self._plugin_response_queues_lock:
            self._plugin_response_queues.pop(pid, None)
    
    @property
    def plugin_response_map(self) -> Any:
        """
        Lazily initialize and return the cross-process shared mapping used to store plugin responses.
        
        Ensures a multiprocessing.Manager is created and used to produce a dict proxy for storing response entries.
        Also guarantees the per-request event map is created on the same Manager so forked plugin processes share the same proxy objects.
        
        Returns:
            A multiprocessing.Manager-backed dict proxy for plugin responses.
        """
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
        """
        Provide the cross-process mapping from request IDs to Event objects used for plugin responses.
        
        Lazily initializes the backing Manager-based map when first accessed and returns the map proxy used to signal per-request responses across processes.
        
        Returns:
            Any: A mapping-like object (typically a multiprocessing.Manager proxy) keyed by `request_id` with values that are `Event` objects or event-like proxies.
        """
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
        """
        Ensure a cross-process Event exists for the given request_id and return it.
        
        Attempts to reuse the shared multiprocessing.Manager-backed event map; if an event already exists for the request_id that event is returned. If no event exists, tries to create one via the shared Manager and store it in the event map. Returns None if the shared Manager or event map cannot be obtained.
        
        Parameters:
            request_id (str): Identifier used as the key for the per-request event; will be converted to a string.
        
        Returns:
            multiprocessing.synchronize.Event or None: The Event associated with `request_id`, or `None` when a Manager-based event cannot be created or accessed.
        """
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
                event_map[rid] = ev
            except Exception:
                pass
            return ev
        except Exception:
            return None

    def append_message_record(self, record: Dict[str, Any]) -> None:
        """
        Append a message record to the internal message store, update the messages revision, and notify subscribers of the new record.
        
        Parameters:
            record (dict): Message record to store. Expected optional keys:
                - "message_id" (str): identifier; records whose `message_id` is marked deleted are ignored.
                - "priority" (int|str): numeric priority; non-convertible values are treated as 0.
                - "source" (str): origin identifier to include in the notification payload.
                - "export": optional visibility/export hint included verbatim in the notification payload.
        
        Behavior:
            - If `record` is not a dict or its `message_id` is in the deleted set, the call is a no-op.
            - On success the function appends the record to the in-memory message store, increments the messages revision counter, and emits a "messages" bus "add" event with a payload containing `rev` and any of `message_id`, `priority`, `source`, and `export` when available.
        """
        if not isinstance(record, dict):
            return
        mid = record.get("message_id")
        if isinstance(mid, str) and mid in self._deleted_message_ids:
            return
        with self._bus_store_lock:
            self._message_store.append(record)
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
        """
        Append multiple message records to the message store and emit a bus "add" change for each appended record.
        
        Each item in `records` that is a dict and whose "message_id" is not tracked as deleted will be appended to the internal message store. For every appended record the method increments the messages revision, builds a payload and emits an "add" change on the "messages" bus. The payload always includes `rev`; it includes `message_id` when present and a non-empty string, `priority` coerced to an int with a fallback of 0 on error, `source` when it is a non-empty string, and `export` when the key is present in the record. Non-dict entries and records whose message_id is marked deleted are ignored. Invalid or empty input returns 0.
        
        Parameters:
            records (List[Dict[str, Any]]): Candidate message records to append.
        
        Returns:
            int: The number of records actually appended and emitted to the bus.
        """
        if not isinstance(records, list) or not records:
            return 0
        kept: List[Dict[str, Any]] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            mid = rec.get("message_id")
            if isinstance(mid, str) and mid in self._deleted_message_ids:
                continue
            kept.append(rec)
        if not kept:
            return 0
        with self._bus_store_lock:
            for rec in kept:
                self._message_store.append(rec)
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

    def append_event_record(self, record: Dict[str, Any]) -> None:
        """
        Append a single event record to the in-memory event store and notify subscribers of the addition.
        
        If `record` is not a dict or its `event_id`/`trace_id` matches an ID previously deleted, the call is ignored. The record is appended to the internal event deque under a lock, the events bus revision is incremented, and a bus change with op `"add"` is emitted containing a copy of the record and the new revision.
        
        Parameters:
            record (dict): Event payload; if present, `event_id` or `trace_id` is used to detect soft-deleted records.
        """
        if not isinstance(record, dict):
            return
        eid = record.get("event_id") or record.get("trace_id")
        if isinstance(eid, str) and eid in self._deleted_event_ids:
            return
        with self._bus_store_lock:
            self._event_store.append(record)
        try:
            rev = self._bump_bus_rev("events")
            self.bus_change_hub.emit("events", "add", {"record": dict(record), "rev": rev})
        except Exception:
            pass

    def extend_event_records(self, records: List[Dict[str, Any]]) -> int:
        """
        Append multiple event records to the event store and emit an "add" notification for each record that is kept.
        
        Validates that `records` is a non-empty list, skips non-dictionary entries, and omits records whose `event_id` or `trace_id` is present in the deleted-event set. Appends kept records to the internal event store under a lock, then increments the events bus revision and emits a per-record change payload containing a copy of the record and its revision. Failures during emission are ignored for individual records.
        
        Parameters:
            records (List[Dict[str, Any]]): Candidate event records; each record should include `event_id` or `trace_id` when applicable.
        
        Returns:
            int: The number of records actually appended and emitted.
        """
        if not isinstance(records, list) or not records:
            return 0
        kept: List[Dict[str, Any]] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            eid = rec.get("event_id") or rec.get("trace_id")
            if isinstance(eid, str) and eid in self._deleted_event_ids:
                continue
            kept.append(rec)
        if not kept:
            return 0
        with self._bus_store_lock:
            for rec in kept:
                self._event_store.append(rec)
        for rec in kept:
            try:
                rev = self._bump_bus_rev("events")
                self.bus_change_hub.emit("events", "add", {"record": dict(rec), "rev": rev})
            except Exception:
                pass
        return len(kept)

    def append_lifecycle_record(self, record: Dict[str, Any]) -> None:
        """
        Append a lifecycle record to the in-memory lifecycle store and notify subscribers.
        
        If `record` is not a `dict` the function returns immediately. If the record contains a `lifecycle_id`
        or `trace_id` that has been marked deleted, the record is ignored. Otherwise the record is appended
        to the lifecycle store under the internal bus store lock, the "lifecycle" bus revision is incremented,
        and a change is emitted on the `bus_change_hub` with payload `{"record": dict(record), "rev": rev}`.
        Exceptions raised while emitting the change are caught and suppressed.
        
        Parameters:
            record (dict): Lifecycle record to store; may include `lifecycle_id` or `trace_id`.
        """
        if not isinstance(record, dict):
            return
        lid = record.get("lifecycle_id") or record.get("trace_id")
        if isinstance(lid, str) and lid in self._deleted_lifecycle_ids:
            return
        with self._bus_store_lock:
            self._lifecycle_store.append(record)
        try:
            rev = self._bump_bus_rev("lifecycle")
            self.bus_change_hub.emit("lifecycle", "add", {"record": dict(record), "rev": rev})
        except Exception:
            pass

    def extend_lifecycle_records(self, records: List[Dict[str, Any]]) -> int:
        """
        Append multiple lifecycle records to the lifecycle store and emit a bus "add" event for each appended record.
        
        Parameters:
            records (List[Dict[str, Any]]): Sequence of candidate lifecycle records. Non-dict entries are ignored. Records whose `lifecycle_id` or `trace_id` is present in the deleted lifecycle ID set are skipped.
        
        Returns:
            int: Number of records actually appended and emitted.
        """
        if not isinstance(records, list) or not records:
            return 0
        kept: List[Dict[str, Any]] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            lid = rec.get("lifecycle_id") or rec.get("trace_id")
            if isinstance(lid, str) and lid in self._deleted_lifecycle_ids:
                continue
            kept.append(rec)
        if not kept:
            return 0
        with self._bus_store_lock:
            for rec in kept:
                self._lifecycle_store.append(rec)
        for rec in kept:
            try:
                rev = self._bump_bus_rev("lifecycle")
                self.bus_change_hub.emit("lifecycle", "add", {"record": dict(rec), "rev": rev})
            except Exception:
                pass
        return len(kept)

    def list_message_records(self) -> List[Dict[str, Any]]:
        """
        Get a snapshot of all message records in insertion order.
        
        Returns:
            List[Dict[str, Any]]: A list of message record dictionaries (most-recently appended last).
        """
        with self._bus_store_lock:
            return list(self._message_store)

    def list_message_records_tail(self, n: int) -> List[Dict[str, Any]]:
        """
        Return the last `n` message records in chronological order.
        
        Parameters:
            n (int): Maximum number of most-recent records to return. If `n` is less than or equal to 0, an empty list is returned.
        
        Returns:
            List[Dict[str, Any]]: A list of up to `n` message records ordered from oldest to newest among the selected tail. If an internal iteration error occurs, returns a snapshot of the entire message store.
        """
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

    def message_store_len(self) -> int:
        """
        Return the number of messages currently retained in the message store.
        
        Returns:
            int: The count of message records stored.
        """
        with self._bus_store_lock:
            return len(self._message_store)

    def iter_message_records_reverse(self):
        """
        Return an iterator over a snapshot of message records in reverse chronological order.
        
        Returns:
            iterator: An iterator yielding message record dicts from newest to oldest based on the current in-memory message store snapshot.
        """
        with self._bus_store_lock:
            snap = list(self._message_store)
        return reversed(snap)

    def list_event_records(self) -> List[Dict[str, Any]]:
        """
        Get a thread-safe snapshot of all stored event records.
        
        Returns:
            List[Dict[str, Any]]: A shallow-copied list of event record dictionaries. Modifying the returned list does not alter the internal store; modifying the individual record dictionaries will affect the stored records.
        """
        with self._bus_store_lock:
            return list(self._event_store)

    def list_event_records_tail(self, n: int) -> List[Dict[str, Any]]:
        """
        Return the last n event records in chronological order.
        
        Parameters:
            n (int): Maximum number of most recent event records to return.
        
        Returns:
            List[Dict[str, Any]]: A list of up to `n` event record dictionaries, ordered from oldest to newest within the returned slice; returns an empty list if `n` is less than or equal to zero.
        """
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
        """
        Get the current number of stored event records.
        
        Returns:
            int: The number of event records in the in-memory event store.
        """
        with self._bus_store_lock:
            return len(self._event_store)

    def iter_event_records_reverse(self):
        """
        Iterate over stored event records from newest to oldest.
        
        Returns:
            iterator: An iterator that yields event record dictionaries in reverse insertion order (newest first).
        """
        with self._bus_store_lock:
            snap = list(self._event_store)
        return reversed(snap)

    def list_lifecycle_records(self) -> List[Dict[str, Any]]:
        """
        Return a snapshot list of all lifecycle records in insertion order.
        
        The returned list is a shallow copy of the internal lifecycle store at the time of the call.
        
        Returns:
            List[Dict[str, Any]]: Lifecycle record dictionaries in insertion order.
        """
        with self._bus_store_lock:
            return list(self._lifecycle_store)

    def list_lifecycle_records_tail(self, n: int) -> List[Dict[str, Any]]:
        """
        Return up to the last `n` lifecycle records in chronological order.
        
        Parameters:
            n (int): Maximum number of most-recent lifecycle records to return. If `n` is less than or equal to zero, an empty list is returned.
        
        Returns:
            List[Dict[str, Any]]: A list of lifecycle record dictionaries containing up to `n` most-recent records, ordered from oldest to newest within the returned slice. If fewer than `n` records exist, all available records are returned. If an internal error occurs while slicing, a snapshot of the entire lifecycle store is returned.
        """
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
        """
        Get the number of lifecycle records currently stored.
        
        Returns:
            int: The number of lifecycle records in the lifecycle store.
        """
        with self._bus_store_lock:
            return len(self._lifecycle_store)

    def iter_lifecycle_records_reverse(self):
        """
        Iterate lifecycle records from most recent to oldest.
        
        Returns:
            iterator: An iterator that yields a snapshot of lifecycle record dictionaries in reverse chronological order (most recent first).
        """
        with self._bus_store_lock:
            snap = list(self._lifecycle_store)
        return reversed(snap)

    def delete_message(self, message_id: str) -> bool:
        """
        Mark a message ID as deleted and remove any matching records from the in-memory message store.
        
        Parameters:
            message_id (str): Non-empty message identifier to delete.
        
        Returns:
            bool: `true` if one or more records were removed from the store, `false` otherwise.
        
        Notes:
            - If records are removed, the messages bus revision is incremented and a "del" change is emitted for subscribers.
            - If `message_id` is not a non-empty string, the function returns `false` without modifying state.
        """
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
        """
        Add or update a subscription entry for the specified bus.
        
        Creates or replaces the subscription identified by `sub_id` under `bus` using a shallow copy of `info` as the stored payload. The operation is thread-safe.
        
        Parameters:
        	bus (str): Bus name (e.g., "messages", "events", "lifecycle").
        	sub_id (str): Subscription identifier; must be non-empty.
        	info (Dict[str, Any]): Subscription metadata to store (copied before storing).
        
        Raises:
        	ValueError: If `bus` is unknown or `sub_id` is empty.
        """
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
        """
        Remove a previously registered subscription from the specified bus.
        
        Parameters:
            bus (str): The bus name to remove the subscription from (expected: "messages", "events", or "lifecycle").
            sub_id (str): The subscription identifier returned when the subscription was created.
        
        Returns:
            bool: `true` if a subscription with `sub_id` was found and removed from `bus`, `false` otherwise.
        """
        b = str(bus).strip()
        sid = str(sub_id).strip()
        if b not in self._bus_subscriptions or not sid:
            return False
        with self._bus_subscriptions_lock:
            return self._bus_subscriptions[b].pop(sid, None) is not None

    def get_bus_subscriptions(self, bus: str) -> Dict[str, Dict[str, Any]]:
        """
        Return a snapshot of subscriptions for the given bus.
        
        Parameters:
            bus (str): Bus name (`"messages"`, `"events"`, or `"lifecycle"`). Leading/trailing whitespace is ignored.
        
        Returns:
            Dict[str, Dict[str, Any]]: A shallow copy mapping subscription IDs to their subscription info dictionaries; empty if the bus has no subscriptions.
        """
        b = str(bus).strip()
        if b not in self._bus_subscriptions:
            return {}
        with self._bus_subscriptions_lock:
            return {k: dict(v) for k, v in self._bus_subscriptions[b].items()}

    def delete_event(self, event_id: str) -> bool:
        """
        Remove an event by its identifier and record the deletion.
        
        Parameters:
            event_id (str): The event's identifier (event_id or trace_id) to delete.
        
        Returns:
            bool: `True` if one or more stored events matching the identifier were removed, `False` if the identifier was invalid or no stored event matched.
        """
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
        """
        Mark a lifecycle record as deleted and remove matching entries from the in-memory lifecycle store.
        
        If a non-empty string `lifecycle_id` matches a record's `lifecycle_id` or `trace_id`, that record is removed, the ID is recorded in the deletion tombstone set, and a bus deletion event is emitted for the lifecycle bus. Invalid or empty `lifecycle_id` values are ignored.
        
        Parameters:
            lifecycle_id (str): The lifecycle identifier (or trace identifier) to delete.
        
        Returns:
            bool: `True` if one or more records were removed, `False` otherwise.
        """
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
        Store a plugin response for a request ID and notify any waiters.
        
        Stores `response` in the shared plugin response map with an expiration time computed as current time + `timeout` + 1 second buffer, then sets the per-request event (if present) and the global notify event to wake waiting consumers. Exceptions raised while signalling events are suppressed.
        
        Parameters:
            request_id (str): The request identifier associated with the response.
            response (Dict[str, Any]): The response payload to store.
            timeout (float): Time in seconds used to compute the response expiration (an additional 1 second buffer is added).
        """
        # 存储响应和过期时间（当前时间 + timeout + 缓冲时间）
        # 缓冲时间用于处理网络延迟等情况
        expire_time = time.time() + timeout + 1.0  # 额外1秒缓冲
        resp_map = self.plugin_response_map
        resp_map[request_id] = {
            "response": response,
            "expire_time": expire_time
        }

        try:
            ev = self._get_or_create_response_event(request_id)
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
        Retrieve and remove a stored plugin response by request ID.
        
        If the stored response is expired or missing, the entry is removed (if expired) and None is returned. On successful retrieval, the response is removed from the shared response map and any associated per-request event is cleared.
        
        Parameters:
            request_id (str): Identifier for the plugin request whose response is being retrieved.
        
        Returns:
            dict: The stored response data, or `None` if the response does not exist or has expired.
        """
        current_time = time.time()
        
        # 先查看响应是否存在（不删除）
        resp_map = self.plugin_response_map
        response_data = resp_map.get(request_id, None)
        
        if response_data is None:
            return None
        
        # 检查是否过期
        expire_time = response_data.get("expire_time", 0)
        if current_time > expire_time:
            # 响应已过期，删除它
            resp_map.pop(request_id, None)
            return None
        
        # 响应有效，删除并返回
        resp_map.pop(request_id, None)
        try:
            event_map = self.plugin_response_event_map
            event_map.pop(request_id, None)
        except Exception:
            pass
        # 返回实际的响应数据
        return response_data.get("response")

    def wait_for_plugin_response(self, request_id: str, timeout: float) -> Optional[Dict[str, Any]]:
        """
        Waits until a plugin response for the given request_id is available or the timeout elapses; when found removes and returns it.
        
        Returns:
            Optional[Dict[str, Any]]: The response dictionary for request_id if received before the timeout, `None` if the timeout elapsed.
        """
        rid = str(request_id)
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

                    # Clear to avoid immediate returns on the next wait() if we woke up spuriously.
                    try:
                        per_req_ev.clear()
                    except Exception:
                        pass
                except Exception:
                    time.sleep(min(0.01, remaining))

            got = self.get_plugin_response(rid)
            if got is not None:
                return got

    def peek_plugin_response(self, request_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a stored plugin response without removing it.
        
        If a response exists for the given request_id and has not expired, return its `response` value.
        If no response exists or the stored entry has expired, remove the expired entry (if any) and return `None`.
        
        Returns:
            The stored response dict if present and not expired, `None` otherwise.
        """
        current_time = time.time()
        response_data = self.plugin_response_map.get(request_id, None)
        if response_data is None:
            return None

        expire_time = response_data.get("expire_time", 0)
        if current_time > expire_time:
            self.plugin_response_map.pop(request_id, None)
            return None

        return response_data.get("response")
    
    def cleanup_expired_responses(self) -> int:
        """
        Remove expired plugin responses from the shared response map.
        
        Scans the plugin response map for entries whose stored `expire_time` is earlier than the current time and removes those entries and their associated per-request events if present.
        
        Returns:
            int: Number of response entries removed.
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
            logger = logging.getLogger("user_plugin_server")
            logger.debug(f"Error iterating expired responses: {e}")
        
        # 删除过期的响应
        resp_map = self.plugin_response_map
        for request_id in expired_ids:
            resp_map.pop(request_id, None)
            try:
                event_map = self.plugin_response_event_map
                event_map.pop(request_id, None)
            except Exception:
                pass
        
        return len(expired_ids)
    
    def close_plugin_resources(self) -> None:
        """
        Release and shut down inter-process plugin communication resources used by the runtime.
        
        This clears and closes the internal plugin communication queue (if created), shuts down the multiprocessing Manager that backs shared response/event maps (if created), and clears related in-memory references so resources can be reclaimed. The method swallows and logs cleanup errors instead of raising them.
        """
        # 清理插件间通信队列
        if self._plugin_comm_queue is not None:
            try:
                self._plugin_comm_queue.cancel_join_thread()  # 防止卡住
                self._plugin_comm_queue.close()
                # self._plugin_comm_queue.join_thread() # 不需要 join，已经 cancel 了
                logger = logging.getLogger("user_plugin_server")
                logger.debug("Plugin communication queue closed")
            except Exception as e:
                logger = logging.getLogger("user_plugin_server")
                logger.warning(f"Error closing plugin communication queue: {e}")
        
        # 清理响应映射和 Manager
        if self._plugin_response_map_manager is not None:
            try:
                # Manager 的 shutdown() 方法会关闭所有共享对象
                self._plugin_response_map_manager.shutdown()
                self._plugin_response_map = None
                self._plugin_response_event_map = None
                self._plugin_response_notify_event = None
                self._plugin_response_map_manager = None
                logger = logging.getLogger("user_plugin_server")
                logger.debug("Plugin response map manager shut down")
            except Exception as e:
                logger = logging.getLogger("user_plugin_server")
                logger.debug(f"Error shutting down plugin response map manager: {e}")

    def cleanup_plugin_comm_resources(self) -> None:
        """Backward-compatible alias for shutdown code paths."""
        self.close_plugin_resources()

    def add_user_context_event(self, bucket_id: str, event: Dict[str, Any]) -> None:
        """
        Add an event to a per-bucket user context history.
        
        If `bucket_id` is not a non-empty string, the event is stored under the "default" bucket. If `event` is a dict it is copied; otherwise it is wrapped into a dict under the `"event"` key. A `_ts` timestamp (seconds since epoch) is set if missing. The event is appended to a fixed-size per-bucket deque (created if absent) and any entries older than the configured TTL are removed.
        
        Parameters:
            bucket_id (str): Identifier for the user-context bucket; uses "default" when falsy.
            event (Dict[str, Any]): Event payload or arbitrary value to store (non-dict values are wrapped).
        """
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
        """
        Retrieve the most recent user-context events for a bucket, filtered by TTL and limited in count.
        
        This returns up to `limit` most-recent entries from the named `bucket_id` (defaults to "default" if empty or not a string). Entries older than the configured user-context TTL are removed before selecting the most recent items.
        
        Parameters:
            bucket_id (str): Bucket identifier to read from; if falsy or not a string, "default" is used.
            limit (int): Maximum number of recent entries to return; values <= 0 yield an empty list.
        
        Returns:
            List[Dict[str, Any]]: A list of event dictionaries (most-recent last) up to `limit`; expired or non-dict entries are excluded.
        """
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


# 全局状态实例
state = PluginRuntimeState()
