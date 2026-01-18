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
        self._lock = threading.Lock()
        self._next_id = 1
        self._subs: Dict[str, Dict[int, Callable[[str, Dict[str, Any]], None]]] = {
            "messages": {},
            "events": {},
            "lifecycle": {},
            "runs": {},
            "export": {},
        }

    def subscribe(self, bus: str, callback: Callable[[str, Dict[str, Any]], None]) -> Callable[[], None]:
        b = str(bus).strip()
        if b not in self._subs:
            raise ValueError(f"Unknown bus: {bus!r}")
        with self._lock:
            sid = self._next_id
            self._next_id += 1
            self._subs[b][sid] = callback

        def _unsub() -> None:
            with self._lock:
                self._subs.get(b, {}).pop(sid, None)

        return _unsub

    def emit(self, bus: str, op: str, payload: Dict[str, Any]) -> None:
        b = str(bus).strip()
        if b not in self._subs:
            return
        with self._lock:
            callbacks = list(self._subs[b].values())
        for cb in callbacks:
            try:
                cb(str(op), dict(payload) if isinstance(payload, dict) else {})
            except Exception:
                logging.getLogger("user_plugin_server").debug(
                    f"BusChangeHub callback error for bus={bus}, op={op}", exc_info=True
                )
                continue


class PluginRuntimeState:
    """插件运行时状态"""
    
    def __init__(self):
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
                    logging.getLogger("user_plugin_server").debug(
                        f"Failed to retrieve response event for request_id={rid}", exc_info=True
                    )
            except Exception:
                logging.getLogger("user_plugin_server").debug(
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
        try:
            from plugin.server.message_plane_bridge import publish_record

            publish_record(store="messages", record=dict(record), topic="all")
        except Exception:
            pass
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
        try:
            from plugin.server.message_plane_bridge import publish_record

            for rec in kept:
                if isinstance(rec, dict):
                    publish_record(store="messages", record=dict(rec), topic="all")
        except Exception:
            pass
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

                try:
                    from plugin.server.message_plane_bridge import publish_record

                    for rec in kept_fast:
                        if isinstance(rec, dict):
                            publish_record(store="messages", record=dict(rec), topic="all")
                except Exception:
                    pass

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
        try:
            from plugin.server.message_plane_bridge import publish_record

            for rec in kept:
                if isinstance(rec, dict):
                    publish_record(store="messages", record=dict(rec), topic="all")
        except Exception:
            pass
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
            from plugin.server.message_plane_bridge import publish_record

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
            from plugin.server.message_plane_bridge import publish_record

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
            from plugin.server.message_plane_bridge import publish_record

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
            from plugin.server.message_plane_bridge import publish_record

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
        try:
            from plugin.server.message_plane_bridge import publish_snapshot

            items = self.list_message_records()
            publish_snapshot(store="messages", records=[dict(x) for x in items if isinstance(x, dict)], topic="all", mode="replace")
            return int(len(items))
        except Exception:
            return 0

    def sync_message_plane_events(self) -> int:
        try:
            from plugin.server.message_plane_bridge import publish_snapshot

            items = self.list_event_records()
            publish_snapshot(store="events", records=[dict(x) for x in items if isinstance(x, dict)], topic="all", mode="replace")
            return int(len(items))
        except Exception:
            return 0

    def sync_message_plane_lifecycle(self) -> int:
        try:
            from plugin.server.message_plane_bridge import publish_snapshot

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
            except Exception:
                logging.getLogger("user_plugin_server").debug(
                    f"Failed to remove response event for request_id={request_id}", exc_info=True
                )
            return None
        try:
            event_map = self.plugin_response_event_map
            event_map.pop(rid, None)
        except Exception:

            logging.getLogger("user_plugin_server").debug(
                f"Failed to remove response event for request_id={request_id}", exc_info=True
            )
        # 返回实际的响应数据
        return response_data.get("response")

    def wait_for_plugin_response(self, request_id: str, timeout: float) -> Optional[Dict[str, Any]]:
        """Block until response arrives or timeout, then pop and return it.

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
            except Exception:
                logging.getLogger("user_plugin_server").debug(
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
            logger = logging.getLogger("user_plugin_server")
            logger.debug(f"Error iterating expired responses: {e}")
        
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


# 全局状态实例
state = PluginRuntimeState()

