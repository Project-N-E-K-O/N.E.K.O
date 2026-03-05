from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .types import BusRecord, parse_iso_timestamp
from ._client_base import _PluginBusList, BusDeletableClientBase


@dataclass(frozen=True, slots=True)
class EventRecord(BusRecord):
    event_id: Optional[str] = None
    entry_id: Optional[str] = None
    args: Optional[Dict[str, Any]] = None

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "EventRecord":
        payload = raw if isinstance(raw, dict) else {"raw": raw}

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

        ts = parse_iso_timestamp(ts_raw)
        priority_int = priority if isinstance(priority, int) else (int(priority) if isinstance(priority, (float, str)) and priority else 0)

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


class EventList(_PluginBusList[EventRecord]):
    pass


class EventClient(BusDeletableClientBase):
    _store_name = "events"
    _record_cls = EventRecord
    _list_cls = EventList
    _policy_prefix = "bus.events"
    _del_type = "EVENT_DEL"
    _id_field = "event_id"
