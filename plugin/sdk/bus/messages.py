from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from queue import Empty
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Union, Coroutine

from plugin.core.state import state
from plugin.settings import PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS
from plugin.settings import BUS_SDK_POLL_INTERVAL_SECONDS
from plugin.settings import MESSAGE_PLANE_ZMQ_RPC_ENDPOINT
from .types import BusList, BusOp, BusRecord, GetNode, register_bus_change_listener

from plugin.sdk.message_plane_transport import MessagePlaneRpcClient as _MessagePlaneRpcClient
from plugin.sdk.message_plane_transport import format_rpc_error

if TYPE_CHECKING:
    from plugin.core.context import PluginContext


@dataclass(frozen=True, slots=True)
class MessageRecord(BusRecord):
    message_id: Optional[str] = None
    message_type: Optional[str] = None
    description: Optional[str] = None

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "MessageRecord":
        # Ultra-fast path: minimize dict.get() calls and type conversions
        # Extract all fields in one pass, avoid redundant str() calls
        ts_raw = raw.get("timestamp")
        if ts_raw is None:
            ts_raw = raw.get("time")
        
        # Batch extract without intermediate variables when possible
        plugin_id = raw.get("plugin_id")
        source = raw.get("source")
        priority = raw.get("priority")
        content = raw.get("content")
        metadata = raw.get("metadata")
        message_id = raw.get("message_id")
        message_type = raw.get("message_type")
        description = raw.get("description")
        
        # Fast type conversions - avoid unnecessary checks
        timestamp: Optional[float] = float(ts_raw) if isinstance(ts_raw, (int, float)) else None
        priority_int = priority if isinstance(priority, int) else (int(priority) if isinstance(priority, (float, str)) and priority else 0)
        
        # Use message_type as record type, avoid multiple lookups
        if message_type:
            record_type = message_type
        else:
            record_type = raw.get("type", "MESSAGE")

        return MessageRecord(
            kind="message",
            type=record_type if isinstance(record_type, str) else str(record_type),
            timestamp=timestamp,
            plugin_id=plugin_id if isinstance(plugin_id, str) else (str(plugin_id) if plugin_id is not None else None),
            source=source if isinstance(source, str) else (str(source) if source is not None else None),
            priority=priority_int,
            content=content if isinstance(content, str) else (str(content) if content is not None else None),
            metadata=metadata if isinstance(metadata, dict) else {},
            raw=raw,
            message_id=message_id if isinstance(message_id, str) else (str(message_id) if message_id is not None else None),
            message_type=message_type if isinstance(message_type, str) else (str(message_type) if message_type is not None else None),
            description=description if isinstance(description, str) else (str(description) if description is not None else None),
        )

    @staticmethod
    def from_index(index: Dict[str, Any], payload: Optional[Dict[str, Any]] = None) -> "MessageRecord":
        """Fast path: create MessageRecord from pre-extracted index fields.
        
        This avoids re-parsing payload when Rust message_plane already extracted fields.
        """
        # index contains: plugin_id, source, priority, kind, type, timestamp, id
        ts = index.get("timestamp")
        timestamp: Optional[float] = float(ts) if isinstance(ts, (int, float)) else None
        priority = index.get("priority")
        priority_int = priority if isinstance(priority, int) else (int(priority) if priority else 0)
        
        # For messages, id is message_id, type is message_type
        message_id = index.get("id")
        message_type = index.get("type")
        plugin_id = index.get("plugin_id")
        source = index.get("source")
        
        # content and description need payload (not in index)
        content = None
        description = None
        metadata: Dict[str, Any] = {}
        if payload:
            content = payload.get("content")
            description = payload.get("description")
            meta_raw = payload.get("metadata")
            metadata = meta_raw if isinstance(meta_raw, dict) else {}
        
        return MessageRecord(
            kind="message",
            type=message_type if isinstance(message_type, str) else (str(message_type) if message_type else "MESSAGE"),
            timestamp=timestamp,
            plugin_id=plugin_id if isinstance(plugin_id, str) else (str(plugin_id) if plugin_id else None),
            source=source if isinstance(source, str) else (str(source) if source else None),
            priority=priority_int,
            content=content if isinstance(content, str) else (str(content) if content else None),
            metadata=metadata,
            raw=payload or index,
            message_id=message_id if isinstance(message_id, str) else (str(message_id) if message_id else None),
            message_type=message_type if isinstance(message_type, str) else (str(message_type) if message_type else None),
            description=description if isinstance(description, str) else (str(description) if description else None),
        )

    def dump(self) -> Dict[str, Any]:
        base = super().dump()
        base["message_id"] = self.message_id
        base["message_type"] = self.message_type
        base["description"] = self.description
        return base


class MessageList(BusList[MessageRecord]):
    def __init__(
        self,
        items: Sequence[MessageRecord],
        *,
        plugin_id: Optional[str] = None,
        ctx: Optional[Any] = None,
        trace: Optional[Sequence[BusOp]] = None,
        plan: Optional[Any] = None,
        fast_mode: bool = False,
    ):
        super().__init__(items, ctx=ctx, trace=trace, plan=plan, fast_mode=fast_mode)
        self.plugin_id = plugin_id

    def merge(self, other: "BusList[MessageRecord]") -> "MessageList":
        merged = super().merge(other)
        other_pid = getattr(other, "plugin_id", None)
        pid = self.plugin_id if self.plugin_id == other_pid else "*"
        return MessageList(
            merged.dump_records(),
            plugin_id=pid,
            ctx=getattr(merged, "_ctx", None),
            trace=merged.trace,
            plan=getattr(merged, "_plan", None),
            fast_mode=merged.fast_mode,
        )

    def __add__(self, other: "BusList[MessageRecord]") -> "MessageList":
        return self.merge(other)


@dataclass
class _LocalMessageCache:
    maxlen: int = 8192  # Increased from 2048 to reduce RPC calls

    def __post_init__(self) -> None:
        try:
            from collections import deque

            self._q = deque(maxlen=int(self.maxlen))
        except Exception:
            self._q = []

        try:
            import threading

            self._lock = threading.Lock()
        except Exception:
            self._lock = None

    def on_delta(self, _bus: str, op: str, delta: Dict[str, Any]) -> None:
        if str(op) not in ("add", "change"):
            return
        if not isinstance(delta, dict) or not delta:
            return
        try:
            mid = delta.get("message_id")
        except Exception:
            mid = None
        if not isinstance(mid, str) or not mid:
            return

        # Cache more fields to reduce RPC calls
        item: Dict[str, Any] = {"message_id": mid}
        
        # Copy all index fields for better cache hit rate
        for key in ["rev", "priority", "source", "export", "plugin_id", "type", "message_type", "timestamp", "kind"]:
            try:
                if key in delta:
                    val = delta.get(key)
                    if val is not None:
                        item[key] = val
            except Exception:
                pass

        if self._lock is not None:
            with self._lock:
                try:
                    self._q.append(item)
                except Exception:
                    return
            return
        try:
            self._q.append(item)  # type: ignore[attr-defined]
        except Exception:
            return

    def tail(self, n: int) -> List[Dict[str, Any]]:
        nn = int(n)
        if nn <= 0:
            return []
        if self._lock is not None:
            with self._lock:
                try:
                    arr = list(self._q)
                except Exception:
                    return []
        else:
            try:
                arr = list(self._q)
            except Exception:
                return []
        if nn >= len(arr):
            return arr
        return arr[-nn:]


_LOCAL_CACHE: Optional[_LocalMessageCache] = None
_LOCAL_CACHE_UNSUB: Optional[Any] = None

try:
    _LOCAL_CACHE = _LocalMessageCache()
    try:
        _LOCAL_CACHE_UNSUB = register_bus_change_listener("messages", _LOCAL_CACHE.on_delta)
    except Exception:
        _LOCAL_CACHE_UNSUB = None
except Exception:
    _LOCAL_CACHE = None
    _LOCAL_CACHE_UNSUB = None


def _ensure_local_cache() -> _LocalMessageCache:
    global _LOCAL_CACHE, _LOCAL_CACHE_UNSUB
    if _LOCAL_CACHE is not None:
        return _LOCAL_CACHE
    c = _LocalMessageCache()
    _LOCAL_CACHE = c
    try:
        _LOCAL_CACHE_UNSUB = register_bus_change_listener("messages", c.on_delta)
    except Exception:
        _LOCAL_CACHE_UNSUB = None
    return c


@dataclass
class MessageClient:
    ctx: "PluginContext"
    
    def _is_in_event_loop(self) -> bool:
        """检测当前是否在事件循环中运行"""
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def _get_via_message_plane(
        self,
        *,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        priority_min: Optional[int] = None,
        source: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        strict: bool = True,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
        raw: bool = False,
        light: bool = False,
        topic: str = "all",
    ) -> MessageList:
        """Fetch messages via message_plane ZMQ RPC."""
        pid_norm: Optional[str] = None
        if isinstance(plugin_id, str):
            pid_norm = plugin_id.strip()
        if pid_norm == "*":
            pid_norm = None
        if pid_norm == "":
            pid_norm = None

        topic_norm = str(topic) if isinstance(topic, str) and topic else "all"
        source_norm = str(source) if isinstance(source, str) and source else None
        pr_min_norm = int(priority_min) if priority_min is not None else None
        since_norm = float(since_ts) if since_ts is not None else None

        args: Dict[str, Any] = {
            "store": "messages",
            "topic": topic_norm,
            "limit": int(max_count) if max_count is not None else 50,
            "plugin_id": pid_norm,
            "source": source_norm,
            "priority_min": pr_min_norm,
            "since_ts": since_norm,
            "light": bool(light),
        }
        if isinstance(filter, dict):
            # Only pass through fields supported by message_plane query.
            # 支持 conversation_id 过滤
            for k in ("kind", "type", "plugin_id", "source", "priority_min", "since_ts", "until_ts", "conversation_id"):
                if k in filter and args.get(k) is None:
                    args[k] = filter.get(k)
        if not bool(strict):
            # message_plane query is strict by nature; keep the parameter for API parity.
            pass

        # Reuse RPC client to avoid creating new ZMQ socket on every call
        rpc = getattr(self.ctx, "_mp_rpc_client", None)
        if rpc is None:
            rpc = _MessagePlaneRpcClient(plugin_id=getattr(self.ctx, "plugin_id", ""), endpoint=str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT))
            try:
                self.ctx._mp_rpc_client = rpc
            except Exception:
                pass

        # Fast path: for the common "recent messages" case with no filters, use get_recent which
        # avoids full-store scan/sort in message_plane.
        if (
            pid_norm is None
            and source_norm is None
            and pr_min_norm is None
            and since_norm is None
            and (not filter)
            and bool(strict)
            and topic_norm == "all"
        ):
            op_name = "bus.get_recent"
            resp = rpc.request(
                op="bus.get_recent",
                args={"store": "messages", "topic": "all", "limit": int(max_count), "light": bool(light)},
                timeout=float(timeout),
            )
        else:
            op_name = "bus.query"
            resp = rpc.request(op="bus.query", args=args, timeout=float(timeout))
        if not isinstance(resp, dict):
            raise TimeoutError(f"message_plane {op_name} timed out after {timeout}s")
        if not resp.get("ok"):
            raise RuntimeError(format_rpc_error(resp.get("error")))
        result = resp.get("result")
        items: List[Any] = []
        if isinstance(result, dict):
            got = result.get("items")
            if isinstance(got, list):
                items = got

        records: List[MessageRecord] = []
        if bool(light):
            # Light mode: message_plane returns only seq/index, without payload.
            # Ultra-fast path: minimize str() calls and allocations
            for ev in items:
                if not isinstance(ev, dict):
                    continue
                idx = ev.get("index")
                if not isinstance(idx, dict):
                    idx = {}
                
                # Batch extract all fields
                record_type = idx.get("type") or "MESSAGE"
                pid = idx.get("plugin_id")
                src = idx.get("source")
                priority_raw = idx.get("priority")
                pr_i = int(priority_raw or 0) if isinstance(priority_raw, (int, float)) else 0
                mid = idx.get("id")
                
                # Avoid creating intermediate dict for raw
                records.append(
                    MessageRecord(
                        kind="message",
                        type=record_type if isinstance(record_type, str) else str(record_type),
                        timestamp=None,
                        plugin_id=pid if isinstance(pid, str) else (str(pid) if pid is not None else None),
                        source=src if isinstance(src, str) else (str(src) if src is not None else None),
                        priority=pr_i,
                        content=None,
                        metadata={},
                        raw={"index": idx, "seq": ev.get("seq"), "ts": ev.get("ts")},
                        message_id=mid if isinstance(mid, str) else (str(mid) if mid is not None else None),
                        message_type=record_type if isinstance(record_type, str) else str(record_type),
                        description=None,
                    )
                )
        else:
            for ev in items:
                if not isinstance(ev, dict):
                    continue
                idx = ev.get("index")
                p = ev.get("payload")
                # Fast path: use from_index when index is available (Rust already extracted fields)
                if isinstance(idx, dict):
                    records.append(MessageRecord.from_index(idx, p if isinstance(p, dict) else None))
                elif isinstance(p, dict):
                    records.append(MessageRecord.from_raw(p))

        # Optimization: skip trace/plan creation when raw=True (common in benchmarks)
        if bool(raw):
            trace = None
            plan = None
        else:
            get_params = {
                "plugin_id": plugin_id,
                "max_count": max_count,
                "priority_min": priority_min,
                "source": source,
                "filter": dict(filter) if isinstance(filter, dict) else None,
                "strict": bool(strict),
                "since_ts": since_ts,
                "timeout": timeout,
                "raw": bool(raw),
            }
            trace = [BusOp(name="get", params=get_params, at=time.time())]
            plan = GetNode(op="get", params={"bus": "messages", "params": get_params}, at=time.time())
        
        effective_plugin_id = "*" if plugin_id == "*" else (pid_norm if pid_norm else getattr(self.ctx, "plugin_id", None))
        return MessageList(records, plugin_id=effective_plugin_id, ctx=self.ctx, trace=trace, plan=plan)
    
    async def _get_via_message_plane_async(
        self,
        *,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        priority_min: Optional[int] = None,
        source: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        strict: bool = True,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
        raw: bool = False,
        light: bool = False,
        topic: str = "all",
    ) -> MessageList:
        """异步版本:通过 message_plane ZMQ RPC 获取消息"""
        pid_norm: Optional[str] = None
        if isinstance(plugin_id, str):
            pid_norm = plugin_id.strip()
        if pid_norm == "*":
            pid_norm = None
        if pid_norm == "":
            pid_norm = None

        topic_norm = str(topic) if isinstance(topic, str) and topic else "all"
        source_norm = str(source) if isinstance(source, str) and source else None
        pr_min_norm = int(priority_min) if priority_min is not None else None
        since_norm = float(since_ts) if since_ts is not None else None

        args: Dict[str, Any] = {
            "store": "messages",
            "topic": topic_norm,
            "limit": int(max_count) if max_count is not None else 50,
            "plugin_id": pid_norm,
            "source": source_norm,
            "priority_min": pr_min_norm,
            "since_ts": since_norm,
            "light": bool(light),
        }
        if isinstance(filter, dict):
            # 支持 conversation_id 过滤
            for k in ("kind", "type", "plugin_id", "source", "priority_min", "since_ts", "until_ts", "conversation_id"):
                if k in filter and args.get(k) is None:
                    args[k] = filter.get(k)
        if not bool(strict):
            pass

        rpc = getattr(self.ctx, "_mp_rpc_client", None)
        if rpc is None:
            rpc = _MessagePlaneRpcClient(plugin_id=getattr(self.ctx, "plugin_id", ""), endpoint=str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT))
            try:
                self.ctx._mp_rpc_client = rpc
            except Exception:
                pass

        if (
            pid_norm is None
            and source_norm is None
            and pr_min_norm is None
            and since_norm is None
            and (not filter)
            and bool(strict)
            and topic_norm == "all"
        ):
            op_name = "bus.get_recent"
            resp = await rpc.request_async(
                op="bus.get_recent",
                args={"store": "messages", "topic": "all", "limit": int(max_count), "light": bool(light)},
                timeout=float(timeout),
            )
        else:
            op_name = "bus.query"
            resp = await rpc.request_async(op="bus.query", args=args, timeout=float(timeout))
        
        if not isinstance(resp, dict):
            raise TimeoutError(f"message_plane {op_name} timed out after {timeout}s")
        if not resp.get("ok"):
            raise RuntimeError(format_rpc_error(resp.get("error")))
        result = resp.get("result")
        items: List[Any] = []
        if isinstance(result, dict):
            got = result.get("items")
            if isinstance(got, list):
                items = got

        records: List[MessageRecord] = []
        if bool(light):
            for ev in items:
                if not isinstance(ev, dict):
                    continue
                idx = ev.get("index")
                if not isinstance(idx, dict):
                    idx = {}
                
                record_type = idx.get("type") or "MESSAGE"
                pid = idx.get("plugin_id")
                src = idx.get("source")
                priority_raw = idx.get("priority")
                pr_i = int(priority_raw or 0) if isinstance(priority_raw, (int, float)) else 0
                mid = idx.get("id")
                
                records.append(
                    MessageRecord(
                        kind="message",
                        type=record_type if isinstance(record_type, str) else str(record_type),
                        timestamp=None,
                        plugin_id=pid if isinstance(pid, str) else (str(pid) if pid is not None else None),
                        source=src if isinstance(src, str) else (str(src) if src is not None else None),
                        priority=pr_i,
                        content=None,
                        metadata={},
                        raw={"index": idx, "seq": ev.get("seq"), "ts": ev.get("ts")},
                        message_id=mid if isinstance(mid, str) else (str(mid) if mid is not None else None),
                        message_type=record_type if isinstance(record_type, str) else str(record_type),
                        description=None,
                    )
                )
        else:
            for ev in items:
                if not isinstance(ev, dict):
                    continue
                idx = ev.get("index")
                p = ev.get("payload")
                if isinstance(idx, dict):
                    records.append(MessageRecord.from_index(idx, p if isinstance(p, dict) else None))
                elif isinstance(p, dict):
                    records.append(MessageRecord.from_raw(p))

        if bool(raw):
            trace = None
            plan = None
        else:
            get_params = {
                "plugin_id": plugin_id,
                "max_count": max_count,
                "priority_min": priority_min,
                "source": source,
                "filter": dict(filter) if isinstance(filter, dict) else None,
                "strict": bool(strict),
                "since_ts": since_ts,
                "timeout": timeout,
                "raw": bool(raw),
            }
            trace = [BusOp(name="get", params=get_params, at=time.time())]
            plan = GetNode(op="get", params={"bus": "messages", "params": get_params}, at=time.time())
        
        effective_plugin_id = "*" if plugin_id == "*" else (pid_norm if pid_norm else getattr(self.ctx, "plugin_id", None))
        return MessageList(records, plugin_id=effective_plugin_id, ctx=self.ctx, trace=trace, plan=plan)

    def get_message_plane_all(
        self,
        *,
        plugin_id: Optional[str] = None,
        source: Optional[str] = None,
        priority_min: Optional[int] = None,
        after_seq: int = 0,
        page_limit: int = 200,
        max_items: int = 5000,
        timeout: float = 5.0,
        raw: bool = False,
        topic: str = "*",
    ) -> MessageList:
        pid_norm: Optional[str] = None
        if isinstance(plugin_id, str):
            pid_norm = plugin_id.strip()
        if pid_norm == "*":
            pid_norm = None
        if pid_norm == "":
            pid_norm = None

        rpc = _MessagePlaneRpcClient(plugin_id=getattr(self.ctx, "plugin_id", ""), endpoint=str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT))

        out_payloads: List[Dict[str, Any]] = []
        last_seq = int(after_seq) if after_seq is not None else 0
        limit_i = int(page_limit) if page_limit is not None else 200
        if limit_i <= 0:
            limit_i = 200

        hard_max = int(max_items) if max_items is not None else 0
        if hard_max <= 0:
            hard_max = 5000

        while len(out_payloads) < hard_max:
            args: Dict[str, Any] = {
                "store": "messages",
                "topic": str(topic) if isinstance(topic, str) and topic else "*",
                "after_seq": int(last_seq),
                "limit": int(min(limit_i, hard_max - len(out_payloads))),
            }
            resp = rpc.request(op="bus.get_since", args=args, timeout=float(timeout))
            if not isinstance(resp, dict):
                raise TimeoutError(f"message_plane bus.get_since timed out after {timeout}s")
            if not resp.get("ok"):
                raise RuntimeError(format_rpc_error(resp.get("error")))
            result = resp.get("result")
            items: List[Any] = []
            if isinstance(result, dict):
                got = result.get("items")
                if isinstance(got, list):
                    items = got

            if not items:
                break

            progressed = False
            for ev in items:
                if not isinstance(ev, dict):
                    continue
                try:
                    seq = int(ev.get("seq") or 0)
                except Exception:
                    seq = 0
                if seq > last_seq:
                    last_seq = seq
                    progressed = True
                p = ev.get("payload")
                if not isinstance(p, dict):
                    continue
                if pid_norm is not None and p.get("plugin_id") != pid_norm:
                    continue
                if isinstance(source, str) and source and p.get("source") != source:
                    continue
                if priority_min is not None:
                    try:
                        if int(p.get("priority") or 0) < int(priority_min):
                            continue
                    except Exception:
                        continue
                out_payloads.append(p)
                if len(out_payloads) >= hard_max:
                    break

            if not progressed:
                break
            if len(items) < int(args.get("limit") or 0):
                break

        records: List[MessageRecord] = []
        for p in out_payloads:
            records.append(MessageRecord.from_raw(p))

        effective_plugin_id = "*" if plugin_id == "*" else (pid_norm if pid_norm else getattr(self.ctx, "plugin_id", None))
        get_params = {
            "plugin_id": plugin_id,
            "max_count": int(len(records)),
            "priority_min": priority_min,
            "source": source,
            "filter": None,
            "strict": True,
            "since_ts": None,
            "timeout": timeout,
            "raw": bool(raw),
        }
        trace = None if bool(raw) else [BusOp(name="get", params=dict(get_params), at=time.time())]
        plan = None if bool(raw) else GetNode(op="get", params={"bus": "messages", "params": dict(get_params)}, at=time.time())
        return MessageList(records, plugin_id=effective_plugin_id, ctx=self.ctx, trace=trace, plan=plan)

    def get_sync(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        priority_min: Optional[int] = None,
        source: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        strict: bool = True,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
        raw: bool = False,
        no_fallback: bool = False,
    ) -> MessageList:
        """同步版本:获取消息列表"""
        # Fastest path: for the common "recent" read used by load testing, prefer local cache
        # (no IPC round-trip) when the request is effectively "latest N across all plugins".
        if bool(raw) and (plugin_id is None or str(plugin_id).strip() == "*"):
            if priority_min is None and (source is None or not str(source)) and filter is None and since_ts is None:
                c = _ensure_local_cache()
                cached = c.tail(int(max_count) if max_count is not None else 50)
                if cached:
                    cached_records: List[MessageRecord] = []
                    for item in cached:
                        if not isinstance(item, dict):
                            continue
                        
                        # Batch extract all fields without try/except overhead
                        message_type = item.get("message_type")
                        if not message_type:
                            message_type = item.get("type")
                        record_type = message_type if message_type else "MESSAGE"
                        
                        pid = item.get("plugin_id")
                        src = item.get("source")
                        pr = item.get("priority", 0)
                        mid = item.get("message_id")
                        
                        # Fast type conversions
                        pr_i = pr if isinstance(pr, int) else (int(pr) if isinstance(pr, (float, str)) and pr else 0)
                        
                        cached_records.append(
                            MessageRecord(
                                kind="message",
                                type=record_type if isinstance(record_type, str) else str(record_type),
                                timestamp=None,
                                plugin_id=pid if isinstance(pid, str) else (str(pid) if pid is not None else None),
                                source=src if isinstance(src, str) else (str(src) if src is not None else None),
                                priority=pr_i,
                                content=None,
                                metadata={},
                                raw=item,
                                message_id=mid if isinstance(mid, str) else (str(mid) if mid is not None else None),
                                message_type=message_type if isinstance(message_type, str) else (str(message_type) if message_type is not None else None),
                                description=None,
                            )
                        )
                    return MessageList(cached_records, plugin_id="*", ctx=self.ctx, trace=None, plan=None)

        # Prefer message_plane with msgpack encoding; when raw=True, use light-index mode
        # to avoid transferring full payloads (content/description/metadata).
        # Light mode is safe when we only need index fields for filtering/display.
        light = bool(raw)
        msg_list = self._get_via_message_plane(
            plugin_id=plugin_id,
            max_count=max_count,
            priority_min=priority_min,
            source=source,
            filter=filter,
            strict=strict,
            since_ts=since_ts,
            timeout=timeout,
            raw=raw,
            light=light,
        )
        return msg_list
    
    async def get_async(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        priority_min: Optional[int] = None,
        source: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        strict: bool = True,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
        raw: bool = False,
        no_fallback: bool = False,
    ) -> MessageList:
        """异步版本:获取消息列表"""
        # Fast path: local cache for raw queries
        if bool(raw) and (plugin_id is None or str(plugin_id).strip() == "*"):
            if priority_min is None and (source is None or not str(source)) and filter is None and since_ts is None:
                c = _ensure_local_cache()
                cached = c.tail(int(max_count) if max_count is not None else 50)
                if cached:
                    cached_records: List[MessageRecord] = []
                    for item in cached:
                        if not isinstance(item, dict):
                            continue
                        
                        message_type = item.get("message_type")
                        if not message_type:
                            message_type = item.get("type")
                        record_type = message_type if message_type else "MESSAGE"
                        
                        pid = item.get("plugin_id")
                        src = item.get("source")
                        pr = item.get("priority", 0)
                        mid = item.get("message_id")
                        
                        pr_i = pr if isinstance(pr, int) else (int(pr) if isinstance(pr, (float, str)) and pr else 0)
                        
                        cached_records.append(
                            MessageRecord(
                                kind="message",
                                type=record_type if isinstance(record_type, str) else str(record_type),
                                timestamp=None,
                                plugin_id=pid if isinstance(pid, str) else (str(pid) if pid is not None else None),
                                source=src if isinstance(src, str) else (str(src) if src is not None else None),
                                priority=pr_i,
                                content=None,
                                metadata={},
                                raw=item,
                                message_id=mid if isinstance(mid, str) else (str(mid) if mid is not None else None),
                                message_type=message_type if isinstance(message_type, str) else (str(message_type) if message_type is not None else None),
                                description=None,
                            )
                        )
                    return MessageList(cached_records, plugin_id="*", ctx=self.ctx, trace=None, plan=None)

        light = bool(raw)
        msg_list = await self._get_via_message_plane_async(
            plugin_id=plugin_id,
            max_count=max_count,
            priority_min=priority_min,
            source=source,
            filter=filter,
            strict=strict,
            since_ts=since_ts,
            timeout=timeout,
            raw=raw,
            light=light,
        )
        return msg_list
    
    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        priority_min: Optional[int] = None,
        source: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        strict: bool = True,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
        raw: bool = False,
        no_fallback: bool = False,
    ) -> Union[MessageList, Coroutine[Any, Any, MessageList]]:
        """智能版本:自动检测执行环境,选择同步或异步执行方式
        
        Returns:
            在事件循环中返回协程,否则返回 MessageList
        """
        if self._is_in_event_loop():
            return self.get_async(
                plugin_id=plugin_id,
                max_count=max_count,
                priority_min=priority_min,
                source=source,
                filter=filter,
                strict=strict,
                since_ts=since_ts,
                timeout=timeout,
                raw=raw,
                no_fallback=no_fallback,
            )
        return self.get_sync(
            plugin_id=plugin_id,
            max_count=max_count,
            priority_min=priority_min,
            source=source,
            filter=filter,
            strict=strict,
            since_ts=since_ts,
            timeout=timeout,
            raw=raw,
            no_fallback=no_fallback,
        )

    def get_by_conversation(
        self,
        conversation_id: str,
        *,
        max_count: int = 50,
        timeout: float = 5.0,
        topic: str = "conversation",
    ) -> Union[MessageList, Coroutine[Any, Any, MessageList]]:
        """通过 conversation_id 获取对话消息
        
        Args:
            conversation_id: 对话ID（由 cross_server 生成）
            max_count: 最大返回数量
            timeout: 超时时间
            topic: 话题名称，默认为 "conversation"
            
        Returns:
            在事件循环中返回协程，否则返回 MessageList
            
        Example:
            # 在插件 entry 中使用
            ctx = args.get("_ctx", {})
            conversation_id = ctx.get("conversation_id")
            if conversation_id:
                messages = await self.ctx.bus.messages.get_by_conversation(conversation_id)
                for msg in messages:
                    print(f"[{msg.metadata.get('turn_type')}] {msg.content}")
        """
        return self.get(
            filter={"conversation_id": conversation_id},
            max_count=max_count,
            timeout=timeout,
        )
