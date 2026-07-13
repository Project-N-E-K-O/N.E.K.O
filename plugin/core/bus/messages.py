from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Coroutine, Dict, List, Optional, Union

from .types import BusOp, BusRecord, GetNode
from ._client_base import (
    _is_in_event_loop, _ensure_rpc, _validate_rpc_response, _parse_bus_items, _PluginBusList,
)

if TYPE_CHECKING:
    from plugin.core.context import PluginContext


@dataclass(frozen=True, slots=True)
class MessageRecord(BusRecord):
    message_id: Optional[str] = None
    message_type: Optional[str] = None
    description: Optional[str] = None

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "MessageRecord":
        ts_raw = raw.get("timestamp")
        if ts_raw is None:
            ts_raw = raw.get("time")

        plugin_id = raw.get("plugin_id")
        source = raw.get("source")
        priority = raw.get("priority")
        content = raw.get("content")
        metadata = raw.get("metadata")
        message_id = raw.get("message_id")
        message_type = raw.get("message_type")
        description = raw.get("description")

        timestamp: Optional[float] = float(ts_raw) if isinstance(ts_raw, (int, float)) else None
        priority_int = priority if isinstance(priority, int) else (int(priority) if isinstance(priority, (float, str)) and priority else 0)

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
        ts = index.get("timestamp")
        timestamp: Optional[float] = float(ts) if isinstance(ts, (int, float)) else None
        priority = index.get("priority")
        priority_int = priority if isinstance(priority, int) else (int(priority) if priority else 0)

        message_id = index.get("id")
        message_type = index.get("type")
        plugin_id = index.get("plugin_id")
        source = index.get("source")

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
        base = BusRecord.dump(self)
        base["message_id"] = self.message_id
        base["message_type"] = self.message_type
        base["description"] = self.description
        return base


class MessageList(_PluginBusList[MessageRecord]):
    pass


# ── MessageClient ──────────────────────────────────────────────────────

class MessageClient:
    def __init__(self, ctx: "PluginContext"):
        self.ctx = ctx

    # ── arg building (shared by sync & async) ──

    def _build_mp_args(
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
    ) -> tuple[str, Dict[str, Any], Optional[str]]:
        """Returns (op_name, rpc_args, pid_norm)."""
        pid_norm: Optional[str] = None
        if isinstance(plugin_id, str):
            pid_norm = plugin_id.strip()
        if pid_norm in ("*", ""):
            pid_norm = None

        topic_norm = str(topic) if isinstance(topic, str) and topic else "all"
        source_norm = str(source) if isinstance(source, str) and source else None
        pr_min = int(priority_min) if priority_min is not None else None
        since = float(since_ts) if since_ts is not None else None

        args: Dict[str, Any] = {
            "store": "messages", "topic": topic_norm,
            "limit": int(max_count) if max_count is not None else 50,
            "plugin_id": pid_norm, "source": source_norm,
            "priority_min": pr_min, "since_ts": since, "light": bool(light),
        }
        if isinstance(filter, dict):
            for k in ("kind", "type", "plugin_id", "source", "priority_min",
                       "since_ts", "until_ts", "conversation_id"):
                if k in filter and args.get(k) is None:
                    args[k] = filter[k]

        if (pid_norm is None and source_norm is None and pr_min is None
                and since is None and not filter and strict and topic_norm == "all"):
            op = "bus.get_recent"
            rpc_args = {"store": "messages", "topic": "all",
                        "limit": int(max_count), "light": bool(light)}
        else:
            op = "bus.query"
            rpc_args = args

        return op, rpc_args, pid_norm

    # ── response parsing (shared by sync & async) ──

    def _parse_mp_response(
        self,
        resp: Any,
        *,
        op_name: str,
        timeout: float,
        light: bool,
        raw: bool,
        plugin_id: Optional[str],
        pid_norm: Optional[str],
        max_count: int,
        priority_min: Optional[int],
        source: Optional[str],
        filter: Optional[Dict[str, Any]],
        strict: bool,
        since_ts: Optional[float],
    ) -> MessageList:
        raw_items = _validate_rpc_response(resp, op_name=op_name, timeout=timeout)

        records: List[MessageRecord] = []
        if light:
            for ev in raw_items:
                if not isinstance(ev, dict):
                    continue
                idx = ev.get("index")
                if not isinstance(idx, dict):
                    idx = {}
                record_type = idx.get("type") or "MESSAGE"
                pid = idx.get("plugin_id")
                src = idx.get("source")
                pr_raw = idx.get("priority")
                pr_i = int(pr_raw or 0) if isinstance(pr_raw, (int, float)) else 0
                mid = idx.get("id")
                records.append(MessageRecord(
                    kind="message",
                    type=record_type if isinstance(record_type, str) else str(record_type),
                    timestamp=None,
                    plugin_id=pid if isinstance(pid, str) else (str(pid) if pid is not None else None),
                    source=src if isinstance(src, str) else (str(src) if src is not None else None),
                    priority=pr_i, content=None, metadata={},
                    raw={"index": idx, "seq": ev.get("seq"), "ts": ev.get("ts")},
                    message_id=mid if isinstance(mid, str) else (str(mid) if mid is not None else None),
                    message_type=record_type if isinstance(record_type, str) else str(record_type),
                    description=None,
                ))
        else:
            records = _parse_bus_items(raw_items, MessageRecord)

        if raw:
            trace_val = None
            plan_val = None
        else:
            get_params: Dict[str, Any] = {
                "plugin_id": plugin_id, "max_count": max_count,
                "priority_min": priority_min, "source": source,
                "filter": dict(filter) if isinstance(filter, dict) else None,
                "strict": strict, "since_ts": since_ts, "timeout": timeout, "raw": raw,
            }
            trace_val = [BusOp(name="get", params=get_params, at=time.time())]
            plan_val = GetNode(op="get", params={"bus": "messages", "params": get_params}, at=time.time())

        effective_pid = "*" if plugin_id == "*" else (pid_norm if pid_norm else getattr(self.ctx, "plugin_id", None))
        return MessageList(records, plugin_id=effective_pid, ctx=self.ctx, trace=trace_val, plan=plan_val)

    # ── public API ──

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
    ) -> Union[MessageList, Coroutine[Any, Any, MessageList]]:
        if _is_in_event_loop():
            return self.get_async(
                plugin_id=plugin_id, max_count=max_count, priority_min=priority_min,
                source=source, filter=filter, strict=strict, since_ts=since_ts,
                timeout=timeout, raw=raw,
            )

        light = bool(raw)
        op, rpc_args, pid_norm = self._build_mp_args(
            plugin_id=plugin_id, max_count=max_count, priority_min=priority_min,
            source=source, filter=filter, strict=strict, since_ts=since_ts,
            timeout=timeout, raw=raw, light=light,
        )
        resp = _ensure_rpc(self.ctx).request(op=op, args=rpc_args, timeout=float(timeout))
        return self._parse_mp_response(
            resp, op_name=op, timeout=timeout, light=light, raw=raw,
            plugin_id=plugin_id, pid_norm=pid_norm, max_count=max_count,
            priority_min=priority_min, source=source, filter=filter,
            strict=strict, since_ts=since_ts,
        )

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
    ) -> MessageList:
        light = bool(raw)
        op, rpc_args, pid_norm = self._build_mp_args(
            plugin_id=plugin_id, max_count=max_count, priority_min=priority_min,
            source=source, filter=filter, strict=strict, since_ts=since_ts,
            timeout=timeout, raw=raw, light=light,
        )
        resp = await _ensure_rpc(self.ctx).request_async(op=op, args=rpc_args, timeout=float(timeout))
        return self._parse_mp_response(
            resp, op_name=op, timeout=timeout, light=light, raw=raw,
            plugin_id=plugin_id, pid_norm=pid_norm, max_count=max_count,
            priority_min=priority_min, source=source, filter=filter,
            strict=strict, since_ts=since_ts,
        )

    def get_by_conversation(
        self,
        conversation_id: str,
        *,
        max_count: int = 50,
        timeout: float = 5.0,
        topic: str = "conversation",
    ) -> Union[MessageList, Coroutine[Any, Any, MessageList]]:
        return self.get(
            filter={"conversation_id": conversation_id},
            max_count=max_count, timeout=timeout,
        )
