from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass
from queue import Empty
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from plugin.core.state import state
from plugin.settings import PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS
from plugin.settings import BUS_SDK_POLL_INTERVAL_SECONDS
from plugin.settings import MESSAGE_PLANE_ZMQ_RPC_ENDPOINT
from .types import BusList, BusOp, BusRecord, GetNode, register_bus_change_listener

import ormsgpack

if TYPE_CHECKING:
    from plugin.core.context import PluginContext


try:
    import zmq
except Exception:  # pragma: no cover
    zmq = None


class _MessagePlaneRpcClient:
    def __init__(self, *, plugin_id: str, endpoint: str) -> None:
        if zmq is None:
            raise RuntimeError("pyzmq is not available")
        self._plugin_id = str(plugin_id)
        self._endpoint = str(endpoint)
        try:
            import threading

            self._tls = threading.local()
        except Exception:
            self._tls = None

    def _get_sock(self):
        if self._tls is not None:
            sock = getattr(self._tls, "sock", None)
            if sock is not None:
                return sock
        if zmq is None:
            return None
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.DEALER)
        ident = f"mp:{self._plugin_id}:{int(time.time() * 1000)}".encode("utf-8")
        try:
            sock.setsockopt(zmq.IDENTITY, ident)
        except Exception:
            pass
        try:
            sock.setsockopt(zmq.LINGER, 0)
        except Exception:
            pass
        sock.connect(self._endpoint)
        if self._tls is not None:
            try:
                self._tls.sock = sock
            except Exception:
                pass
        return sock

    def _next_req_id(self) -> str:
        if self._tls is not None:
            try:
                n = int(getattr(self._tls, "req_seq", 0) or 0) + 1
                self._tls.req_seq = n
                return f"{self._plugin_id}:{n}"
            except Exception:
                pass
        return str(uuid.uuid4())

    def request(self, *, op: str, args: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
        if zmq is None:
            return None
        sock = self._get_sock()
        if sock is None:
            return None
        req_id = self._next_req_id()
        req = {"v": 1, "op": str(op), "req_id": req_id, "args": dict(args or {}), "from_plugin": self._plugin_id}

        # Prefer msgpack for performance; fall back to JSON for compatibility.
        enc = "msgpack"
        try:
            raw = ormsgpack.packb(req)
        except Exception:
            enc = "json"
            try:
                raw = json.dumps(req, ensure_ascii=False).encode("utf-8")
            except Exception:
                return None
        try:
            sock.send(raw, flags=0)
        except Exception:
            return None
        deadline = time.time() + max(0.0, float(timeout))
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            try:
                if sock.poll(timeout=int(remaining * 1000), flags=zmq.POLLIN) == 0:
                    continue
            except Exception:
                return None
            try:
                resp_raw = sock.recv(flags=0)
            except Exception:
                return None

            # Try decode using the same encoding; also accept the other encoding for server compatibility.
            resp = None
            if enc == "msgpack":
                try:
                    resp = ormsgpack.unpackb(resp_raw)
                except Exception:
                    resp = None
            if resp is None:
                try:
                    resp = json.loads(resp_raw.decode("utf-8"))
                except Exception:
                    resp = None
            if not isinstance(resp, dict):
                continue
            if resp.get("req_id") != req_id:
                continue
            return resp

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
        if zmq is None:
            raise RuntimeError("pyzmq is not available")

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
            for k in ("kind", "type", "plugin_id", "source", "priority_min", "since_ts", "until_ts"):
                if k in filter and args.get(k) is None:
                    args[k] = filter.get(k)
        if not bool(strict):
            # message_plane query is strict by nature; keep the parameter for API parity.
            pass

        rpc = _MessagePlaneRpcClient(plugin_id=getattr(self.ctx, "plugin_id", ""), endpoint=str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT))

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
            raise RuntimeError(str(resp.get("error") or "message_plane error"))
        result = resp.get("result")
        items: List[Any] = []
        if isinstance(result, dict):
            got = result.get("items")
            if isinstance(got, list):
                items = got

        records: List[MessageRecord] = []
        if bool(light):
            # Light mode: message_plane returns only seq/index, without payload.
            for ev in items:
                if not isinstance(ev, dict):
                    continue
                idx = ev.get("index")
                if not isinstance(idx, dict):
                    idx = {}
                try:
                    record_type = idx.get("type") or "MESSAGE"
                except Exception:
                    record_type = "MESSAGE"
                try:
                    pid = idx.get("plugin_id")
                except Exception:
                    pid = None
                try:
                    src = idx.get("source")
                except Exception:
                    src = None
                try:
                    pr_i = int(idx.get("priority") or 0)
                except Exception:
                    pr_i = 0
                try:
                    mid = idx.get("id")
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
                        raw={"index": idx, "seq": ev.get("seq"), "ts": ev.get("ts")},
                        message_id=str(mid) if mid is not None else None,
                        message_type=str(record_type) if record_type is not None else None,
                        description=None,
                    )
                )
        else:
            payloads: List[Dict[str, Any]] = []
            for ev in items:
                if not isinstance(ev, dict):
                    continue
                p = ev.get("payload")
                if isinstance(p, dict):
                    payloads.append(p)
            for p in payloads:
                records.append(MessageRecord.from_raw(p))

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
        trace = None if bool(raw) else [BusOp(name="get", params=dict(get_params), at=time.time())]
        plan = None if bool(raw) else GetNode(op="get", params={"bus": "messages", "params": dict(get_params)}, at=time.time())
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
        if zmq is None:
            raise RuntimeError("pyzmq is not available")

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
                raise RuntimeError(str(resp.get("error") or "message_plane error"))
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
    ) -> MessageList:
        # Fastest path: for the common "recent" read used by load testing, prefer local cache
        # (no IPC round-trip) when the request is effectively "latest N across all plugins".
        if bool(raw) and (plugin_id is None or str(plugin_id).strip() == "*"):
            if priority_min is None and (source is None or not str(source)) and filter is None and since_ts is None:
                c = _ensure_local_cache()
                cached = c.tail(int(max_count) if max_count is not None else 50)
                if cached:
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

        # Prefer message_plane with msgpack encoding; when raw=True and the request is a simple
        # "recent" fetch, use light-index mode to avoid transferring full payloads.
        light = False
        if bool(raw):
            if (plugin_id is None or str(plugin_id).strip() == "*"):
                if priority_min is None and (source is None or not str(source)) and filter is None and since_ts is None:
                    light = True
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
        )
        return msg_list
