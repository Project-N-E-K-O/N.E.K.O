from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from plugin.core.state import state
from .types import BusList, BusOp, BusRecord, GetNode, parse_iso_timestamp

if TYPE_CHECKING:
    from plugin.core.context import PluginContext

@dataclass(frozen=True)
class EventRecord(BusRecord):
    event_id: Optional[str] = None
    entry_id: Optional[str] = None
    args: Optional[Dict[str, Any]] = None

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "EventRecord":
        payload = dict(raw) if isinstance(raw, dict) else {"raw": raw}

        ev_type = payload.get("type")
        ev_type = str(ev_type) if ev_type is not None else "EVENT"

        ts = parse_iso_timestamp(payload.get("timestamp") or payload.get("received_at") or payload.get("time"))

        plugin_id = payload.get("plugin_id")
        plugin_id = str(plugin_id) if plugin_id is not None else None

        source = payload.get("source")
        source = str(source) if source is not None else None

        priority = payload.get("priority", 0)
        try:
            priority = int(priority)
        except (ValueError, TypeError):
            priority = 0

        entry_id = payload.get("entry_id")
        entry_id = str(entry_id) if entry_id is not None else None

        event_id = payload.get("trace_id") or payload.get("event_id")
        event_id = str(event_id) if event_id is not None else None

        args = payload.get("args")
        if not isinstance(args, dict):
            args = None

        content = payload.get("content")
        if content is None and entry_id:
            content = entry_id
        content = str(content) if content is not None else None

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return EventRecord(
            kind="event",
            type=str(ev_type),
            timestamp=ts,
            plugin_id=plugin_id,
            source=source,
            priority=priority,
            content=content,
            metadata=metadata,
            raw=payload,
            event_id=event_id,
            entry_id=entry_id,
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
        trace: Optional[Sequence[BusOp]] = None,
        plan: Optional[Any] = None,
        fast_mode: bool = False,
    ):
        super().__init__(items, trace=trace, plan=plan, fast_mode=fast_mode)
        self.plugin_id = plugin_id

    def merge(self, other: "EventList") -> "EventList":
        merged = super().merge(other)
        pid = self.plugin_id if self.plugin_id == other.plugin_id else "*"
        if getattr(merged, "plugin_id", None) == pid:
            return merged
        return EventList(
            merged.dump_records(),
            plugin_id=pid,
            trace=merged.trace,
            plan=getattr(merged, "_plan", None),
            fast_mode=merged.fast_mode,
        )

    def __add__(self, other: "EventList") -> "EventList":
        return self.merge(other)



@dataclass
class EventClient:
    ctx: "PluginContext"

    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        timeout: float = 5.0,
    ) -> EventList:
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.events.get")

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
            "type": "EVENT_GET",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": req_id,
            "plugin_id": pid_norm,
            "max_count": int(max_count),
            "timeout": float(timeout),
        }

        try:
            plugin_comm_queue.put(request, timeout=timeout)
        except Exception as e:
            raise RuntimeError(f"Failed to send EVENT_GET request: {e}") from e

        start_time = time.time()
        check_interval = 0.01
        events: List[Any] = []
        while time.time() - start_time < timeout:
            response = state.get_plugin_response(req_id)
            if response is None:
                time.sleep(check_interval)
                continue
            if not isinstance(response, dict):
                time.sleep(check_interval)
                continue
            if response.get("error"):
                raise RuntimeError(str(response.get("error")))

            result = response.get("result")
            if isinstance(result, dict) and isinstance(result.get("events"), list):
                events = result.get("events")
            elif isinstance(result, list):
                events = result
            else:
                events = []
            break
        else:
            _ = state.get_plugin_response(req_id)
            raise TimeoutError(f"EVENT_GET timed out after {timeout}s")

        records: List[EventRecord] = []
        for item in events:
            if isinstance(item, dict):
                records.append(EventRecord.from_raw(item))
            else:
                records.append(EventRecord.from_raw({"raw": item}))

        if pid_norm == "*":
            effective_plugin_id = "*"
        else:
            effective_plugin_id = pid_norm if pid_norm else getattr(self.ctx, "plugin_id", None)

        get_params = {
            "plugin_id": plugin_id,
            "max_count": max_count,
            "timeout": timeout,
        }
        trace = [BusOp(name="get", params=dict(get_params), at=time.time())]
        plan = GetNode(op="get", params={"bus": "events", "params": dict(get_params)}, at=time.time())
        return EventList(records, plugin_id=effective_plugin_id, trace=trace, plan=plan)

    def delete(self, event_id: str, timeout: float = 5.0) -> bool:
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.events.delete")

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

        try:
            plugin_comm_queue.put(request, timeout=timeout)
        except Exception as e:
            raise RuntimeError(f"Failed to send EVENT_DEL request: {e}") from e

        start_time = time.time()
        check_interval = 0.01
        while time.time() - start_time < timeout:
            response = state.get_plugin_response(req_id)
            if response is None:
                time.sleep(check_interval)
                continue
            if not isinstance(response, dict):
                time.sleep(check_interval)
                continue
            if response.get("error"):
                raise RuntimeError(str(response.get("error")))

            result = response.get("result")
            if isinstance(result, dict):
                return bool(result.get("deleted"))
            return False

        _ = state.get_plugin_response(req_id)
        raise TimeoutError(f"EVENT_DEL timed out after {timeout}s")
