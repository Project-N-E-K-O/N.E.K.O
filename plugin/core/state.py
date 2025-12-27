"""
插件运行时状态模块

提供插件系统的全局运行时状态管理。
"""
import asyncio
import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

from plugin.sdk.events import EventHandler
from plugin.settings import EVENT_QUEUE_MAX, LIFECYCLE_QUEUE_MAX, MESSAGE_QUEUE_MAX


class BusChangeHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        self._subs: Dict[str, Dict[int, Callable[[str, Dict[str, Any]], None]]] = {
            "messages": {},
            "events": {},
            "lifecycle": {},
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
        # 保护跨进程通信资源懒加载的锁
        self._plugin_comm_lock = threading.Lock()

        self._bus_store_lock = threading.Lock()
        self._message_store: Deque[Dict[str, Any]] = deque(maxlen=MESSAGE_QUEUE_MAX)
        self._event_store: Deque[Dict[str, Any]] = deque(maxlen=EVENT_QUEUE_MAX)
        self._lifecycle_store: Deque[Dict[str, Any]] = deque(maxlen=LIFECYCLE_QUEUE_MAX)
        self._deleted_message_ids: Set[str] = set()
        self._deleted_event_ids: Set[str] = set()
        self._deleted_lifecycle_ids: Set[str] = set()

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
                    import multiprocessing
                    # 使用 multiprocessing.Queue 因为需要跨进程
                    self._plugin_comm_queue = multiprocessing.Queue()
        return self._plugin_comm_queue
    
    @property
    def plugin_response_map(self):
        """插件响应映射（跨进程共享字典）"""
        if self._plugin_response_map is None:
            with self._plugin_comm_lock:
                if self._plugin_response_map is None:
                    import multiprocessing
                    # 使用 Manager 创建跨进程共享的字典
                    if self._plugin_response_map_manager is None:
                        self._plugin_response_map_manager = multiprocessing.Manager()
                    self._plugin_response_map = self._plugin_response_map_manager.dict()
        return self._plugin_response_map

    def append_message_record(self, record: Dict[str, Any]) -> None:
        if not isinstance(record, dict):
            return
        mid = record.get("message_id")
        if isinstance(mid, str) and mid in self._deleted_message_ids:
            return
        with self._bus_store_lock:
            self._message_store.append(record)
        try:
            self.bus_change_hub.emit("messages", "add", {"record": dict(record)})
        except Exception:
            pass

    def append_event_record(self, record: Dict[str, Any]) -> None:
        if not isinstance(record, dict):
            return
        eid = record.get("event_id") or record.get("trace_id")
        if isinstance(eid, str) and eid in self._deleted_event_ids:
            return
        with self._bus_store_lock:
            self._event_store.append(record)
        try:
            self.bus_change_hub.emit("events", "add", {"record": dict(record)})
        except Exception:
            pass

    def append_lifecycle_record(self, record: Dict[str, Any]) -> None:
        if not isinstance(record, dict):
            return
        lid = record.get("lifecycle_id") or record.get("trace_id")
        if isinstance(lid, str) and lid in self._deleted_lifecycle_ids:
            return
        with self._bus_store_lock:
            self._lifecycle_store.append(record)
        try:
            self.bus_change_hub.emit("lifecycle", "add", {"record": dict(record)})
        except Exception:
            pass

    def list_message_records(self) -> List[Dict[str, Any]]:
        with self._bus_store_lock:
            return list(self._message_store)

    def list_event_records(self) -> List[Dict[str, Any]]:
        with self._bus_store_lock:
            return list(self._event_store)

    def list_lifecycle_records(self) -> List[Dict[str, Any]]:
        with self._bus_store_lock:
            return list(self._lifecycle_store)

    def delete_message(self, message_id: str) -> bool:
        if not isinstance(message_id, str) or not message_id:
            return False
        with self._bus_store_lock:
            self._deleted_message_ids.add(message_id)
            removed = False
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
                    self.bus_change_hub.emit("messages", "del", {"message_id": message_id})
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
        with self._bus_store_lock:
            self._deleted_event_ids.add(event_id)
            removed = False
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
                    self.bus_change_hub.emit("events", "del", {"event_id": event_id})
                except Exception:
                    pass
            return removed

    def delete_lifecycle(self, lifecycle_id: str) -> bool:
        if not isinstance(lifecycle_id, str) or not lifecycle_id:
            return False
        with self._bus_store_lock:
            self._deleted_lifecycle_ids.add(lifecycle_id)
            removed = False
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
                    self.bus_change_hub.emit("lifecycle", "del", {"lifecycle_id": lifecycle_id})
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
        import time
        # 存储响应和过期时间（当前时间 + timeout + 缓冲时间）
        # 缓冲时间用于处理网络延迟等情况
        expire_time = time.time() + timeout + 1.0  # 额外1秒缓冲
        self.plugin_response_map[request_id] = {
            "response": response,
            "expire_time": expire_time
        }
    
    def get_plugin_response(self, request_id: str) -> Optional[Dict[str, Any]]:
        """
        获取并删除插件响应（插件进程调用）
        
        如果响应已过期，会自动清理并返回 None。
        
        Returns:
            响应数据，如果不存在或已过期则返回 None
        """
        import time
        current_time = time.time()
        
        # 先查看响应是否存在（不删除）
        response_data = self.plugin_response_map.get(request_id, None)
        
        if response_data is None:
            return None
        
        # 检查是否过期
        expire_time = response_data.get("expire_time", 0)
        if current_time > expire_time:
            # 响应已过期，删除它
            self.plugin_response_map.pop(request_id, None)
            return None
        
        # 响应有效，删除并返回
        self.plugin_response_map.pop(request_id, None)
        # 返回实际的响应数据
        return response_data.get("response")
    
    def cleanup_expired_responses(self) -> int:
        """
        清理过期的响应（主进程定期调用）
        
        Returns:
            清理的响应数量
        """
        import time
        current_time = time.time()
        expired_ids = []
        
        # 找出所有过期的响应
        try:
            # 使用快照避免迭代时字典被修改导致 RuntimeError
            for request_id, response_data in list(self.plugin_response_map.items()):
                expire_time = response_data.get("expire_time", 0)
                if current_time > expire_time:
                    expired_ids.append(request_id)
        except Exception as e:
            # 如果迭代失败，返回已找到的过期ID数量
            logger = logging.getLogger("user_plugin_server")
            logger.debug(f"Error iterating expired responses: {e}")
        
        # 删除过期的响应
        for request_id in expired_ids:
            self.plugin_response_map.pop(request_id, None)
        
        return len(expired_ids)
    
    def cleanup_plugin_comm_resources(self) -> None:
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
                self._plugin_response_map_manager = None
                logger = logging.getLogger("user_plugin_server")
                logger.debug("Plugin response map manager shut down")
            except Exception as e:
                logger = logging.getLogger("user_plugin_server")
                logger.warning(f"Error shutting down plugin response map manager: {e}")

    def add_user_context_event(self, bucket_id: str, event: Dict[str, Any]) -> None:
        if not isinstance(bucket_id, str) or not bucket_id:
            bucket_id = "default"

        now = time.time()
        payload = dict(event) if isinstance(event, dict) else {"event": event}
        payload.setdefault("_ts", now)

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

