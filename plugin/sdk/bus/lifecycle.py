from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from queue import Empty
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from plugin.core.state import state
from plugin.settings import BUS_SDK_POLL_INTERVAL_SECONDS
from .types import BusList, BusOp, BusRecord, GetNode, parse_iso_timestamp

if TYPE_CHECKING:
    from plugin.core.context import PluginContext

@dataclass(frozen=True)
class LifecycleRecord(BusRecord):
    lifecycle_id: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "LifecycleRecord":
        payload = dict(raw) if isinstance(raw, dict) else {"raw": raw}

        typ = payload.get("type")
        typ = str(typ) if typ is not None else "lifecycle"

        ts = parse_iso_timestamp(payload.get("timestamp") or payload.get("time") or payload.get("at"))

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

        lifecycle_id = payload.get("lifecycle_id") or payload.get("trace_id")
        lifecycle_id = str(lifecycle_id) if lifecycle_id is not None else None

        detail = payload.get("detail")
        if not isinstance(detail, dict):
            detail = None

        return LifecycleRecord(
            kind="lifecycle",
            type=typ,
            timestamp=ts,
            plugin_id=plugin_id,
            source=source,
            priority=priority,
            content=content,
            metadata=metadata,
            raw=payload,
            lifecycle_id=lifecycle_id,
            detail=detail,
        )

    def dump(self) -> Dict[str, Any]:
        base = super().dump()
        base["lifecycle_id"] = self.lifecycle_id
        base["detail"] = dict(self.detail) if isinstance(self.detail, dict) else self.detail
        return base


class LifecycleList(BusList[LifecycleRecord]):
    def __init__(
        self,
        items: Sequence[LifecycleRecord],
        *,
        plugin_id: Optional[str] = None,
        ctx: Optional[Any] = None,
        trace: Optional[Sequence[BusOp]] = None,
        plan: Optional[Any] = None,
        fast_mode: bool = False,
    ):
        super().__init__(items, ctx=ctx, trace=trace, plan=plan, fast_mode=fast_mode)
        self.plugin_id = plugin_id

    def merge(self, other: "BusList[LifecycleRecord]") -> "LifecycleList":
        merged = super().merge(other)
        other_pid = getattr(other, "plugin_id", None)
        pid = self.plugin_id if self.plugin_id == other_pid else "*"
        return LifecycleList(
            merged.dump_records(),
            plugin_id=pid,
            ctx=getattr(merged, "_ctx", None),
            trace=merged.trace,
            plan=getattr(merged, "_plan", None),
            fast_mode=merged.fast_mode,
        )

    def __add__(self, other: "BusList[LifecycleRecord]") -> "LifecycleList":
        return self.merge(other)




@dataclass
class LifecycleClient:
    ctx: "PluginContext"

    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        filter: Optional[Dict[str, Any]] = None,
        strict: bool = True,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ) -> LifecycleList:
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.lifecycle.get")

        zmq_client = getattr(self.ctx, "_zmq_ipc_client", None)

        plugin_comm_queue = getattr(self.ctx, "_plugin_comm_queue", None)
        if plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {getattr(self.ctx, 'plugin_id', 'unknown')}. "
                "This method can only be called from within a plugin process."
            )

        req_id = str(uuid.uuid4())
        pid_norm: Optional[str]
        if isinstance(plugin_id, str):
            pid_norm = plugin_id.strip()
        else:
            pid_norm = None
        if pid_norm == "":
            pid_norm = None

        request = {
            "type": "LIFECYCLE_GET",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": req_id,
            "plugin_id": pid_norm,
            "max_count": int(max_count),
            "filter": dict(filter) if isinstance(filter, dict) else None,
            "strict": bool(strict),
            "since_ts": float(since_ts) if since_ts is not None else None,
            "timeout": float(timeout),
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
                        self.ctx.logger.warning("[bus.lifecycle.get] ZeroMQ IPC failed; raising exception (no fallback)")
                    except Exception:
                        pass
                raise TimeoutError(f"LIFECYCLE_GET over ZeroMQ timed out or failed after {timeout}s")
        else:
            try:
                plugin_comm_queue.put(request, timeout=timeout)
            except Exception as e:
                raise RuntimeError(f"Failed to send LIFECYCLE_GET request: {e}") from e

            response = None
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
                        if len(pending) > 1024:
                            pending.clear()
                        pending[rid] = item
                    except Exception:
                        pass
        if response is None:
            response = state.wait_for_plugin_response(req_id, timeout)
        if response is None:
            raise TimeoutError(f"LIFECYCLE_GET timed out after {timeout}s")
        if not isinstance(response, dict):
            raise RuntimeError("Invalid LIFECYCLE_GET response")
        if response.get("error"):
            raise RuntimeError(str(response.get("error")))

        events: List[Any] = []
        result = response.get("result")
        if isinstance(result, dict):
            evs = result.get("events")
            if isinstance(evs, list):
                events = evs
            else:
                events = []
        elif isinstance(result, list):
            events = result
        else:
            events = []

        records: List[LifecycleRecord] = []
        for item in events:
            if isinstance(item, dict):
                records.append(LifecycleRecord.from_raw(item))
            else:
                records.append(LifecycleRecord.from_raw({"raw": item}))

        if pid_norm == "*":
            effective_plugin_id = "*"
        else:
            effective_plugin_id = pid_norm if pid_norm else getattr(self.ctx, "plugin_id", None)

        get_params = {
            "plugin_id": pid_norm,
            "max_count": max_count,
            "filter": dict(filter) if isinstance(filter, dict) else None,
            "strict": bool(strict),
            "since_ts": since_ts,
            "timeout": timeout,
        }
        trace = [BusOp(name="get", params=dict(get_params), at=time.time())]
        plan = GetNode(op="get", params={"bus": "lifecycle", "params": dict(get_params)}, at=time.time())
        return LifecycleList(records, plugin_id=effective_plugin_id, ctx=self.ctx, trace=trace, plan=plan)

    def delete(self, lifecycle_id: str, timeout: float = 5.0) -> bool:
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.lifecycle.delete")

        zmq_client = getattr(self.ctx, "_zmq_ipc_client", None)

        plugin_comm_queue = getattr(self.ctx, "_plugin_comm_queue", None)
        if plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {getattr(self.ctx, 'plugin_id', 'unknown')}. "
                "This method can only be called from within a plugin process."
            )

        lid = str(lifecycle_id).strip() if lifecycle_id is not None else ""
        if not lid:
            raise ValueError("lifecycle_id is required")

        req_id = str(uuid.uuid4())
        request = {
            "type": "LIFECYCLE_DEL",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": req_id,
            "lifecycle_id": lid,
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
                        self.ctx.logger.warning("[bus.lifecycle.delete] ZeroMQ IPC failed; raising exception (no fallback)")
                    except Exception:
                        pass
                raise TimeoutError(f"LIFECYCLE_DEL over ZeroMQ timed out or failed after {timeout}s")
        else:
            try:
                plugin_comm_queue.put(request, timeout=timeout)
            except Exception as e:
                raise RuntimeError(f"Failed to send LIFECYCLE_DEL request: {e}") from e

            response = state.wait_for_plugin_response(req_id, timeout)
        if response is None:
            raise TimeoutError(f"LIFECYCLE_DEL timed out after {timeout}s")
        if not isinstance(response, dict):
            raise RuntimeError("Invalid LIFECYCLE_DEL response")
        if response.get("error"):
            raise RuntimeError(str(response.get("error")))

        result = response.get("result")
        if isinstance(result, dict):
            return bool(result.get("deleted"))
        return False
