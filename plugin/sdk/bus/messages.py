from __future__ import annotations

import base64
import time
import uuid
from dataclasses import dataclass
from queue import Empty
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from plugin.core.state import state
from plugin.settings import PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS
from plugin.settings import BUS_SDK_POLL_INTERVAL_SECONDS
from .types import BusList, BusOp, BusRecord, GetNode, register_bus_change_listener

if TYPE_CHECKING:
    from plugin.core.context import PluginContext

@dataclass(frozen=True)
class MessageRecord(BusRecord):
    message_id: Optional[str] = None
    message_type: Optional[str] = None
    description: Optional[str] = None

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "MessageRecord":
        payload = dict(raw) if isinstance(raw, dict) else {"content": raw}

        # Prefer ISO timestamp if provided; keep a best-effort float timestamp for filtering.
        ts_raw = payload.get("timestamp") or payload.get("time")
        timestamp: Optional[float] = None
        if isinstance(ts_raw, (int, float)):
            timestamp = float(ts_raw)

        plugin_id = payload.get("plugin_id")
        plugin_id = str(plugin_id) if plugin_id is not None else None

        source = payload.get("source")
        source = str(source) if source is not None else None

        priority = payload.get("priority", 0)
        try:
            priority = int(priority)
        except (ValueError, TypeError):
            priority = 0

        content = payload.get("content")
        content = str(content) if content is not None else None

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        message_id = payload.get("message_id")
        message_id = str(message_id) if message_id is not None else None

        message_type = payload.get("message_type")
        message_type = str(message_type) if message_type is not None else None

        description = payload.get("description")
        description = str(description) if description is not None else None

        # Use message_type as record type to align filtering with actual content type.
        record_type = str(message_type or payload.get("type") or "MESSAGE")

        return MessageRecord(
            kind="message",
            type=record_type,
            timestamp=timestamp,
            plugin_id=plugin_id,
            source=source,
            priority=priority,
            content=content,
            metadata=metadata,
            raw=payload,
            message_id=message_id,
            message_type=message_type,
            description=description,
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
    maxlen: int = 2048

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

        item: Dict[str, Any] = {"message_id": mid}
        try:
            if "rev" in delta:
                item["rev"] = delta.get("rev")
        except Exception:
            pass
        try:
            if "priority" in delta:
                item["priority"] = delta.get("priority")
        except Exception:
            pass
        try:
            if "source" in delta:
                item["source"] = delta.get("source")
        except Exception:
            pass
        try:
            if "export" in delta:
                item["export"] = delta.get("export")
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
    ) -> MessageList:
        if bool(raw) and (plugin_id is None or str(plugin_id).strip() == "*"):
            if priority_min is None and (source is None or not str(source)) and filter is None and since_ts is None:
                c = _ensure_local_cache()
                cached = c.tail(int(max_count) if max_count is not None else 50)
                if cached:
                    # Local-cache fast path: avoid IPC round-trip.
                    cached_records: List[MessageRecord] = []
                    for item in cached:
                        if isinstance(item, dict):
                            try:
                                record_type = item.get("message_type") or item.get("type") or "MESSAGE"
                            except Exception:
                                record_type = "MESSAGE"
                            try:
                                pid = item.get("plugin_id")
                            except Exception:
                                pid = None
                            try:
                                src = item.get("source")
                            except Exception:
                                src = None
                            try:
                                pr = item.get("priority", 0)
                                pr_i = int(pr) if pr is not None else 0
                            except Exception:
                                pr_i = 0
                            try:
                                mid = item.get("message_id")
                            except Exception:
                                mid = None
                            cached_records.append(
                                MessageRecord(
                                    kind="message",
                                    type=str(record_type),
                                    timestamp=None,
                                    plugin_id=str(pid) if pid is not None else None,
                                    source=str(src) if src is not None else None,
                                    priority=pr_i,
                                    content=None,
                                    metadata={},
                                    raw=item,
                                    message_id=str(mid) if mid is not None else None,
                                    message_type=str(record_type) if record_type is not None else None,
                                    description=None,
                                )
                            )
                    return MessageList(cached_records, plugin_id="*", ctx=self.ctx, trace=None, plan=None)
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.messages.get")

        zmq_client = getattr(self.ctx, "_zmq_ipc_client", None)

        plugin_comm_queue = getattr(self.ctx, "_plugin_comm_queue", None)
        if plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {getattr(self.ctx, 'plugin_id', 'unknown')}. "
                "This method can only be called from within a plugin process."
            )

        req_id = str(uuid.uuid4())
        pid_norm: Optional[str] = None
        if isinstance(plugin_id, str):
            pid_norm = plugin_id.strip()
        else:
            pid_norm = None

        if pid_norm == "":
            pid_norm = None

        request = {
            "type": "MESSAGE_GET",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": req_id,
            "plugin_id": pid_norm,
            "max_count": int(max_count),
            "priority_min": int(priority_min) if priority_min is not None else None,
            "source": str(source) if isinstance(source, str) and source else None,
            "filter": dict(filter) if isinstance(filter, dict) else None,
            "strict": bool(strict),
            "since_ts": float(since_ts) if since_ts is not None else None,
            "timeout": float(timeout),
            "raw": bool(raw),
        }

        if zmq_client is not None:
            try:
                resp = zmq_client.request(request, timeout=float(timeout))
                if isinstance(resp, dict):
                    response = resp
                else:
                    response = None
            except Exception:
                response = None
            if response is None:
                if hasattr(self.ctx, "logger"):
                    try:
                        self.ctx.logger.warning("[bus.messages.get] ZeroMQ IPC failed; raising exception (no fallback)")
                    except Exception:
                        pass
                raise TimeoutError(f"MESSAGE_GET over ZeroMQ timed out or failed after {timeout}s")
        else:
            response = None
            try:
                plugin_comm_queue.put(request, timeout=timeout)
            except Exception as e:
                raise RuntimeError(f"Failed to send MESSAGE_GET request: {e}") from e
        resp_q = getattr(self.ctx, "_response_queue", None)
        pending = getattr(self.ctx, "_response_pending", None)
        if pending is None:
            try:
                pending = {}
                setattr(self.ctx, "_response_pending", pending)
            except Exception:
                pending = None
        if pending is not None:
            try:
                cached = pending.pop(req_id, None)
            except Exception:
                cached = None
            if isinstance(cached, dict):
                response = cached
        if response is None and resp_q is not None:
            deadline = time.time() + max(0.0, float(timeout))
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                try:
                    item = resp_q.get(timeout=remaining)
                except Empty:
                    break
                except Exception:
                    break
                if not isinstance(item, dict):
                    continue
                rid = item.get("request_id")
                if rid == req_id:
                    response = item
                    break
                if isinstance(rid, str) and pending is not None:
                    try:
                        max_pending = 1024
                        while len(pending) >= max_pending:
                            try:
                                oldest_key = next(iter(pending))
                            except StopIteration:
                                break
                            try:
                                pending.pop(oldest_key, None)
                            except Exception:
                                break
                        pending[rid] = item
                    except Exception:
                        pass
        if response is None:
            response = state.wait_for_plugin_response(req_id, timeout)
        if response is None:
            orphan_response = None
            try:
                orphan_response = state.peek_plugin_response(req_id)
            except Exception:
                orphan_response = None
            if PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS and orphan_response is not None and hasattr(self.ctx, "logger"):
                try:
                    self.ctx.logger.warning(
                        f"[PluginContext] Timeout reached, but response was found (likely delayed). "
                        f"Orphan response detected for req_id={req_id}"
                    )
                except Exception:
                    pass
            raise TimeoutError(f"MESSAGE_GET timed out after {timeout}s")
        if not isinstance(response, dict):
            raise RuntimeError("Invalid MESSAGE_GET response")
        if response.get("error"):
            raise RuntimeError(str(response.get("error")))

        messages: List[Any] = []
        result = response.get("result")
        if isinstance(result, dict):
            msgs = result.get("messages")
            if isinstance(msgs, list):
                messages = msgs
            else:
                messages = []
        elif isinstance(result, list):
            messages = result
        else:
            messages = []

        records: List[MessageRecord] = []
        if bool(raw):
            for item in messages:
                if isinstance(item, dict):
                    # Fast path: avoid dict() copy + timestamp parsing + normalization.
                    try:
                        record_type = item.get("message_type") or item.get("type") or "MESSAGE"
                    except Exception:
                        record_type = "MESSAGE"
                    try:
                        pid = item.get("plugin_id")
                    except Exception:
                        pid = None
                    try:
                        src = item.get("source")
                    except Exception:
                        src = None
                    try:
                        pr = item.get("priority", 0)
                        pr_i = int(pr) if pr is not None else 0
                    except Exception:
                        pr_i = 0
                    try:
                        mid = item.get("message_id")
                    except Exception:
                        mid = None
                    records.append(
                        MessageRecord(
                            kind="message",
                            type=str(record_type),
                            timestamp=None,
                            plugin_id=str(pid) if pid is not None else None,
                            source=str(src) if src is not None else None,
                            priority=pr_i,
                            content=None,
                            metadata={},
                            raw=item,
                            message_id=str(mid) if mid is not None else None,
                            message_type=str(record_type) if record_type is not None else None,
                            description=None,
                        )
                    )
                else:
                    records.append(MessageRecord.from_raw({"content": item}))
        else:
            for item in messages:
                if isinstance(item, dict):
                    records.append(MessageRecord.from_raw(item))
                else:
                    records.append(MessageRecord.from_raw({"content": item}))

        trace: Optional[List[BusOp]]
        plan: Optional[Any]
        if bool(raw):
            trace = None
            plan = None
        else:
            get_params = {
                "plugin_id": pid_norm,
                "max_count": max_count,
                "priority_min": priority_min,
                "source": str(source) if isinstance(source, str) and source else None,
                "filter": dict(filter) if isinstance(filter, dict) else None,
                "strict": bool(strict),
                "since_ts": since_ts,
                "timeout": timeout,
            }
            trace = [BusOp(name="get", params=dict(get_params), at=time.time())]
            plan = GetNode(op="get", params={"bus": "messages", "params": dict(get_params)}, at=time.time())
        if pid_norm == "*":
            effective_plugin_id = "*"
        else:
            effective_plugin_id = pid_norm if pid_norm else getattr(self.ctx, "plugin_id", None)
        return MessageList(records, plugin_id=effective_plugin_id, ctx=self.ctx, trace=trace, plan=plan)

    def delete(self, message_id: str, timeout: float = 5.0) -> bool:
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.messages.delete")

        zmq_client = getattr(self.ctx, "_zmq_ipc_client", None)

        plugin_comm_queue = getattr(self.ctx, "_plugin_comm_queue", None)
        if plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {getattr(self.ctx, 'plugin_id', 'unknown')}. "
                "This method can only be called from within a plugin process."
            )

        mid = str(message_id).strip() if message_id is not None else ""
        if not mid:
            raise ValueError("message_id is required")

        req_id = str(uuid.uuid4())
        request = {
            "type": "MESSAGE_DEL",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": req_id,
            "message_id": mid,
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
                        self.ctx.logger.warning("[bus.messages.delete] ZeroMQ IPC failed; raising exception (no fallback)")
                    except Exception:
                        pass
                raise TimeoutError(f"MESSAGE_DEL over ZeroMQ timed out or failed after {timeout}s")
        else:
            try:
                plugin_comm_queue.put(request, timeout=timeout)
            except Exception as e:
                raise RuntimeError(f"Failed to send MESSAGE_DEL request: {e}") from e

            response = state.wait_for_plugin_response(req_id, timeout)
        if response is None:
            orphan_response = None
            try:
                orphan_response = state.peek_plugin_response(req_id)
            except Exception:
                orphan_response = None
            if PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS and orphan_response is not None and hasattr(self.ctx, "logger"):
                try:
                    self.ctx.logger.warning(
                        f"[PluginContext] Timeout reached for MESSAGE_DEL, but response was found (likely delayed). "
                        f"Orphan response detected for req_id={req_id}"
                    )
                except Exception:
                    pass
            raise TimeoutError(f"MESSAGE_DEL timed out after {timeout}s")
        if not isinstance(response, dict):
            raise RuntimeError("Invalid MESSAGE_DEL response")
        if response.get("error"):
            raise RuntimeError(str(response.get("error")))

        result = response.get("result")
        if isinstance(result, dict):
            return bool(result.get("deleted"))
        return False
