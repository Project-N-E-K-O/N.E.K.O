from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from queue import Empty
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Union, Coroutine

from plugin.core.state import state
from plugin.settings import MESSAGE_PLANE_ZMQ_RPC_ENDPOINT
from plugin.settings import BUS_SDK_POLL_INTERVAL_SECONDS
from .types import BusList, BusOp, BusRecord, GetNode, parse_iso_timestamp

from plugin.sdk.message_plane_transport import MessagePlaneRpcClient as _MessagePlaneRpcClient
from plugin.sdk.message_plane_transport import format_rpc_error

if TYPE_CHECKING:
    from plugin.core.context import PluginContext


@dataclass(frozen=True, slots=True)
class EventRecord(BusRecord):
    event_id: Optional[str] = None
    entry_id: Optional[str] = None
    args: Optional[Dict[str, Any]] = None

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "EventRecord":
        # Fast path: avoid dict copy if already dict
        payload = raw if isinstance(raw, dict) else {"raw": raw}

        # Batch extract all fields
        ev_type = payload.get("type")
        ts_raw = payload.get("timestamp")
        if ts_raw is None:
            ts_raw = payload.get("received_at")
        if ts_raw is None:
            ts_raw = payload.get("time")
        
        plugin_id = payload.get("plugin_id")
        source = payload.get("source")
        priority = payload.get("priority", 0)
        entry_id = payload.get("entry_id")
        event_id = payload.get("trace_id")
        if event_id is None:
            event_id = payload.get("event_id")
        args = payload.get("args")
        content = payload.get("content")
        metadata = payload.get("metadata")

        # Fast type conversions
        ts = parse_iso_timestamp(ts_raw)
        priority_int = priority if isinstance(priority, int) else (int(priority) if isinstance(priority, (float, str)) and priority else 0)

        # Avoid redundant str() calls
        if content is None and entry_id:
            content = entry_id

        return EventRecord(
            kind="event",
            type=ev_type if isinstance(ev_type, str) else (str(ev_type) if ev_type is not None else "EVENT"),
            timestamp=ts,
            plugin_id=plugin_id if isinstance(plugin_id, str) else (str(plugin_id) if plugin_id is not None else None),
            source=source if isinstance(source, str) else (str(source) if source is not None else None),
            priority=priority_int,
            content=content if isinstance(content, str) else (str(content) if content is not None else None),
            metadata=metadata if isinstance(metadata, dict) else {},
            raw=payload,
            event_id=event_id if isinstance(event_id, str) else (str(event_id) if event_id is not None else None),
            entry_id=entry_id if isinstance(entry_id, str) else (str(entry_id) if entry_id is not None else None),
            args=args if isinstance(args, dict) else None,
        )

    @staticmethod
    def from_index(index: Dict[str, Any], payload: Optional[Dict[str, Any]] = None) -> "EventRecord":
        """Fast path: create EventRecord from pre-extracted index fields."""
        ts = index.get("timestamp")
        timestamp: Optional[float] = float(ts) if isinstance(ts, (int, float)) else None
        priority = index.get("priority")
        priority_int = priority if isinstance(priority, int) else (int(priority) if priority else 0)
        
        event_id = index.get("id")
        plugin_id = index.get("plugin_id")
        source = index.get("source")
        ev_type = index.get("type")
        
        entry_id = None
        args = None
        content = None
        metadata: Dict[str, Any] = {}
        if payload:
            entry_id = payload.get("entry_id")
            args = payload.get("args") if isinstance(payload.get("args"), dict) else None
            content = payload.get("content")
            meta_raw = payload.get("metadata")
            metadata = meta_raw if isinstance(meta_raw, dict) else {}
        
        return EventRecord(
            kind="event",
            type=ev_type if isinstance(ev_type, str) else (str(ev_type) if ev_type else "EVENT"),
            timestamp=timestamp,
            plugin_id=plugin_id if isinstance(plugin_id, str) else (str(plugin_id) if plugin_id else None),
            source=source if isinstance(source, str) else (str(source) if source else None),
            priority=priority_int,
            content=content if isinstance(content, str) else (str(content) if content else None),
            metadata=metadata,
            raw=payload or index,
            event_id=event_id if isinstance(event_id, str) else (str(event_id) if event_id else None),
            entry_id=entry_id if isinstance(entry_id, str) else (str(entry_id) if entry_id else None),
            args=args,
        )

    def dump(self) -> Dict[str, Any]:
        base = super().dump()
        base["event_id"] = self.event_id
        base["entry_id"] = self.entry_id
        base["args"] = dict(self.args) if isinstance(self.args, dict) else self.args
        return base


class EventList(BusList[EventRecord]):
    def __init__(
        self,
        items: Sequence[EventRecord],
        *,
        plugin_id: Optional[str] = None,
        ctx: Optional[Any] = None,
        trace: Optional[Sequence[BusOp]] = None,
        plan: Optional[Any] = None,
        fast_mode: bool = False,
    ):
        super().__init__(items, ctx=ctx, trace=trace, plan=plan, fast_mode=fast_mode)
        self.plugin_id = plugin_id

    def merge(self, other: "BusList[EventRecord]") -> "EventList":
        merged = super().merge(other)
        other_pid = getattr(other, "plugin_id", None)
        pid = self.plugin_id if self.plugin_id == other_pid else "*"
        return EventList(
            merged.dump_records(),
            plugin_id=pid,
            ctx=getattr(merged, "_ctx", None),
            trace=merged.trace,
            plan=getattr(merged, "_plan", None),
            fast_mode=merged.fast_mode,
        )

    def __add__(self, other: "BusList[EventRecord]") -> "EventList":
        return self.merge(other)



@dataclass
class EventClient:
    ctx: "PluginContext"
    
    def _is_in_event_loop(self) -> bool:
        """检测当前是否在事件循环中运行"""
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def get_sync(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        filter: Optional[Dict[str, Any]] = None,
        strict: bool = True,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ) -> EventList:
        """同步版本:获取事件列表"""
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.events.get")

        # Reuse RPC client to avoid creating new ZMQ socket on every call
        rpc = getattr(self.ctx, "_mp_rpc_client", None)
        if rpc is None:
            rpc = _MessagePlaneRpcClient(plugin_id=getattr(self.ctx, "plugin_id", ""), endpoint=str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT))
            try:
                self.ctx._mp_rpc_client = rpc
            except Exception:
                pass

        args: Dict[str, Any] = {
            "store": "events",
            "topic": "all",
            "limit": int(max_count),
            "light": False,
        }
        if since_ts is not None:
            args["since_ts"] = float(since_ts)

        flt = dict(filter) if isinstance(filter, dict) else {}
        if plugin_id is None and isinstance(flt.get("plugin_id"), str) and flt.get("plugin_id"):
            plugin_id = str(flt.get("plugin_id"))
        if isinstance(plugin_id, str) and plugin_id.strip() and plugin_id.strip() != "*":
            args["plugin_id"] = plugin_id.strip()
        if isinstance(flt.get("source"), str) and flt.get("source"):
            args["source"] = str(flt.get("source"))
        if isinstance(flt.get("kind"), str) and flt.get("kind"):
            args["kind"] = str(flt.get("kind"))
        if isinstance(flt.get("type"), str) and flt.get("type"):
            args["type"] = str(flt.get("type"))
        if "priority_min" in flt:
            args["priority_min"] = flt.get("priority_min")
        if "until_ts" in flt:
            args["until_ts"] = flt.get("until_ts")

        # Fast path: for the common "recent" case with no filters, use get_recent.
        if (
            args.get("plugin_id") is None
            and args.get("source") is None
            and args.get("kind") is None
            and args.get("type") is None
            and args.get("priority_min") is None
            and args.get("since_ts") is None
            and args.get("until_ts") is None
            and str(args.get("topic") or "") == "all"
        ):
            op_name = "bus.get_recent"
            mp_resp = rpc.request(
                op="bus.get_recent",
                args={"store": "events", "topic": "all", "limit": int(max_count), "light": False},
                timeout=float(timeout),
            )
        else:
            op_name = "bus.query"
            mp_resp = rpc.request(op="bus.query", args=args, timeout=float(timeout))

        if not isinstance(mp_resp, dict):
            raise TimeoutError(f"message_plane {op_name} timed out after {timeout}s")
        if mp_resp.get("error"):
            raise RuntimeError(format_rpc_error(mp_resp.get("error")))
        if not mp_resp.get("ok"):
            raise RuntimeError(format_rpc_error(mp_resp.get("error")))

        result = mp_resp.get("result")
        items: List[Any] = []
        if isinstance(result, dict) and isinstance(result.get("items"), list):
            items = list(result.get("items") or [])

        ev_records: List[EventRecord] = []
        for item in items:
            if isinstance(item, dict):
                idx = item.get("index")
                p = item.get("payload")
                # Fast path: use from_index when available
                if isinstance(idx, dict):
                    ev_records.append(EventRecord.from_index(idx, p if isinstance(p, dict) else None))
                elif isinstance(p, dict):
                    ev_records.append(EventRecord.from_raw(p))
                else:
                    ev_records.append(EventRecord.from_raw(item))

        get_params = {
            "plugin_id": plugin_id,
            "max_count": max_count,
            "filter": dict(filter) if isinstance(filter, dict) else None,
            "strict": bool(strict),
            "since_ts": since_ts,
            "timeout": timeout,
            "via": "message_plane.rpc",
        }
        trace = [BusOp(name="get", params=dict(get_params), at=time.time())]
        plan = GetNode(op="get", params={"bus": "events", "params": dict(get_params)}, at=time.time())
        if isinstance(plugin_id, str) and plugin_id.strip() == "*":
            effective_plugin_id = "*"
        else:
            effective_plugin_id = plugin_id if plugin_id else getattr(self.ctx, "plugin_id", None)
        return EventList(ev_records, plugin_id=effective_plugin_id, ctx=self.ctx, trace=trace, plan=plan)
    
    async def get_async(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        filter: Optional[Dict[str, Any]] = None,
        strict: bool = True,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ) -> EventList:
        """异步版本:获取事件列表"""
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.events.get")

        rpc = getattr(self.ctx, "_mp_rpc_client", None)
        if rpc is None:
            rpc = _MessagePlaneRpcClient(plugin_id=getattr(self.ctx, "plugin_id", ""), endpoint=str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT))
            try:
                self.ctx._mp_rpc_client = rpc
            except Exception:
                pass

        args: Dict[str, Any] = {
            "store": "events",
            "topic": "all",
            "limit": int(max_count),
            "light": False,
        }
        if since_ts is not None:
            args["since_ts"] = float(since_ts)

        flt = dict(filter) if isinstance(filter, dict) else {}
        if plugin_id is None and isinstance(flt.get("plugin_id"), str) and flt.get("plugin_id"):
            plugin_id = str(flt.get("plugin_id"))
        if isinstance(plugin_id, str) and plugin_id.strip() and plugin_id.strip() != "*":
            args["plugin_id"] = plugin_id.strip()
        if isinstance(flt.get("source"), str) and flt.get("source"):
            args["source"] = str(flt.get("source"))
        if isinstance(flt.get("kind"), str) and flt.get("kind"):
            args["kind"] = str(flt.get("kind"))
        if isinstance(flt.get("type"), str) and flt.get("type"):
            args["type"] = str(flt.get("type"))
        if "priority_min" in flt:
            args["priority_min"] = flt.get("priority_min")
        if "until_ts" in flt:
            args["until_ts"] = flt.get("until_ts")

        if (
            args.get("plugin_id") is None
            and args.get("source") is None
            and args.get("kind") is None
            and args.get("type") is None
            and args.get("priority_min") is None
            and args.get("since_ts") is None
            and args.get("until_ts") is None
            and str(args.get("topic") or "") == "all"
        ):
            op_name = "bus.get_recent"
            mp_resp = await rpc.request_async(
                op="bus.get_recent",
                args={"store": "events", "topic": "all", "limit": int(max_count), "light": False},
                timeout=float(timeout),
            )
        else:
            op_name = "bus.query"
            mp_resp = await rpc.request_async(op="bus.query", args=args, timeout=float(timeout))

        if not isinstance(mp_resp, dict):
            raise TimeoutError(f"message_plane {op_name} timed out after {timeout}s")
        if mp_resp.get("error"):
            raise RuntimeError(format_rpc_error(mp_resp.get("error")))
        if not mp_resp.get("ok"):
            raise RuntimeError(format_rpc_error(mp_resp.get("error")))

        result = mp_resp.get("result")
        items: List[Any] = []
        if isinstance(result, dict) and isinstance(result.get("items"), list):
            items = list(result.get("items") or [])

        ev_records: List[EventRecord] = []
        for item in items:
            if isinstance(item, dict):
                idx = item.get("index")
                p = item.get("payload")
                if isinstance(idx, dict):
                    ev_records.append(EventRecord.from_index(idx, p if isinstance(p, dict) else None))
                elif isinstance(p, dict):
                    ev_records.append(EventRecord.from_raw(p))
                else:
                    ev_records.append(EventRecord.from_raw(item))

        get_params = {
            "plugin_id": plugin_id,
            "max_count": max_count,
            "filter": dict(filter) if isinstance(filter, dict) else None,
            "strict": bool(strict),
            "since_ts": since_ts,
            "timeout": timeout,
            "via": "message_plane.rpc",
        }
        trace = [BusOp(name="get", params=dict(get_params), at=time.time())]
        plan = GetNode(op="get", params={"bus": "events", "params": dict(get_params)}, at=time.time())
        if isinstance(plugin_id, str) and plugin_id.strip() == "*":
            effective_plugin_id = "*"
        else:
            effective_plugin_id = plugin_id if plugin_id else getattr(self.ctx, "plugin_id", None)
        return EventList(ev_records, plugin_id=effective_plugin_id, ctx=self.ctx, trace=trace, plan=plan)
    
    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        filter: Optional[Dict[str, Any]] = None,
        strict: bool = True,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ) -> Union[EventList, Coroutine[Any, Any, EventList]]:
        """智能版本:自动检测执行环境,选择同步或异步执行方式
        
        Returns:
            在事件循环中返回协程,否则返回 EventList
        """
        if self._is_in_event_loop():
            return self.get_async(
                plugin_id=plugin_id,
                max_count=max_count,
                filter=filter,
                strict=strict,
                since_ts=since_ts,
                timeout=timeout,
            )
        return self.get_sync(
            plugin_id=plugin_id,
            max_count=max_count,
            filter=filter,
            strict=strict,
            since_ts=since_ts,
            timeout=timeout,
        )

    def delete_sync(self, event_id: str, timeout: float = 5.0) -> bool:
        """同步版本:删除事件"""
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.events.delete")

        zmq_client = getattr(self.ctx, "_zmq_ipc_client", None)

        plugin_comm_queue = getattr(self.ctx, "_plugin_comm_queue", None)
        if plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {getattr(self.ctx, 'plugin_id', 'unknown')}. "
                "This method can only be called from within a plugin process."
            )

        eid = str(event_id).strip() if event_id is not None else ""
        if not eid:
            raise ValueError("event_id is required")

        req_id = str(uuid.uuid4())
        request = {
            "type": "EVENT_DEL",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": req_id,
            "event_id": eid,
            "timeout": float(timeout),
        }

        if zmq_client is not None:
            response = None
            try:
                resp = zmq_client.request(request, timeout=float(timeout))
                if isinstance(resp, dict):
                    response = resp
            except Exception:
                response = None
            if response is None:
                if hasattr(self.ctx, "logger"):
                    try:
                        self.ctx.logger.warning("[bus.events.delete] ZeroMQ IPC failed; raising exception (no fallback)")
                    except Exception:
                        pass
                raise TimeoutError(f"EVENT_DEL over ZeroMQ timed out or failed after {timeout}s")
        else:
            try:
                plugin_comm_queue.put(request, timeout=timeout)
            except Exception as e:
                raise RuntimeError(f"Failed to send EVENT_DEL request: {e}") from e

            response = state.wait_for_plugin_response(req_id, timeout)
        if response is None:
            raise TimeoutError(f"EVENT_DEL timed out after {timeout}s")
        if not isinstance(response, dict):
            raise RuntimeError("Invalid EVENT_DEL response")
        if response.get("error"):
            raise RuntimeError(str(response.get("error")))

        result = response.get("result")
        if isinstance(result, dict):
            return bool(result.get("deleted"))
        return False
    
    async def delete_async(self, event_id: str, timeout: float = 5.0) -> bool:
        """异步版本:删除事件"""
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.events.delete")

        zmq_client = getattr(self.ctx, "_zmq_ipc_client", None)

        plugin_comm_queue = getattr(self.ctx, "_plugin_comm_queue", None)
        if plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {getattr(self.ctx, 'plugin_id', 'unknown')}. "
                "This method can only be called from within a plugin process."
            )

        eid = str(event_id).strip() if event_id is not None else ""
        if not eid:
            raise ValueError("event_id is required")

        req_id = str(uuid.uuid4())
        request = {
            "type": "EVENT_DEL",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": req_id,
            "event_id": eid,
            "timeout": float(timeout),
        }

        if zmq_client is not None:
            response = None
            try:
                resp = zmq_client.request(request, timeout=float(timeout))
                if isinstance(resp, dict):
                    response = resp
            except Exception:
                response = None
            if response is None:
                if hasattr(self.ctx, "logger"):
                    try:
                        self.ctx.logger.warning("[bus.events.delete] ZeroMQ IPC failed; raising exception (no fallback)")
                    except Exception:
                        pass
                raise TimeoutError(f"EVENT_DEL over ZeroMQ timed out or failed after {timeout}s")
        else:
            try:
                plugin_comm_queue.put(request, timeout=timeout)
            except Exception as e:
                raise RuntimeError(f"Failed to send EVENT_DEL request: {e}") from e

            # 异步等待响应
            start_time = asyncio.get_event_loop().time()
            check_interval = 0.01
            while asyncio.get_event_loop().time() - start_time < timeout:
                response = state.get_plugin_response(req_id)
                if response is not None:
                    break
                await asyncio.sleep(check_interval)
            else:
                response = None
        
        if response is None:
            raise TimeoutError(f"EVENT_DEL timed out after {timeout}s")
        if not isinstance(response, dict):
            raise RuntimeError("Invalid EVENT_DEL response")
        if response.get("error"):
            raise RuntimeError(str(response.get("error")))

        result = response.get("result")
        if isinstance(result, dict):
            return bool(result.get("deleted"))
        return False
    
    def delete(self, event_id: str, timeout: float = 5.0) -> Union[bool, Coroutine[Any, Any, bool]]:
        """智能版本:自动检测执行环境,选择同步或异步执行方式
        
        Returns:
            在事件循环中返回协程,否则返回 bool
        """
        if self._is_in_event_loop():
            return self.delete_async(event_id=event_id, timeout=timeout)
        return self.delete_sync(event_id=event_id, timeout=timeout)
