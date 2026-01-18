from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from queue import Empty
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from plugin.core.state import state
from plugin.settings import MESSAGE_PLANE_ZMQ_RPC_ENDPOINT
from plugin.settings import BUS_SDK_POLL_INTERVAL_SECONDS
from .types import BusList, BusOp, BusRecord, GetNode, parse_iso_timestamp

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

    def request(self, *, op: str, args: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
        if zmq is None:
            return None
        sock = self._get_sock()
        if sock is None:
            return None
        req_id = str(uuid.uuid4())
        req = {"v": 1, "op": str(op), "req_id": req_id, "args": dict(args or {}), "from_plugin": self._plugin_id}

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
        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        deadline = time.time() + max(0.0, float(timeout))
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            try:
                events = dict(poller.poll(timeout=int(remaining * 1000)))
            except Exception:
                return None
            if sock not in events:
                continue
            try:
                resp_raw = sock.recv(flags=0)
            except Exception:
                return None

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

        rpc = _MessagePlaneRpcClient(plugin_id=getattr(self.ctx, "plugin_id", ""), endpoint=str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT))

        args: Dict[str, Any] = {
            "store": "lifecycle",
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
                args={"store": "lifecycle", "topic": "all", "limit": int(max_count), "light": False},
                timeout=float(timeout),
            )
        else:
            op_name = "bus.query"
            mp_resp = rpc.request(op="bus.query", args=args, timeout=float(timeout))

        if not isinstance(mp_resp, dict):
            raise TimeoutError(f"message_plane {op_name} timed out after {timeout}s")
        if mp_resp.get("error"):
            raise RuntimeError(str(mp_resp.get("error")))
        if not mp_resp.get("ok"):
            raise RuntimeError(str(mp_resp.get("error") or "message_plane error"))

        result = mp_resp.get("result")
        items: List[Any] = []
        if isinstance(result, dict) and isinstance(result.get("items"), list):
            items = list(result.get("items") or [])

        lc_records: List[LifecycleRecord] = []
        for item in items:
            if isinstance(item, dict):
                lc_records.append(LifecycleRecord.from_raw(item))
            else:
                lc_records.append(LifecycleRecord.from_raw({"raw": item}))

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
        plan = GetNode(op="get", params={"bus": "lifecycle", "params": dict(get_params)}, at=time.time())
        if isinstance(plugin_id, str) and plugin_id.strip() == "*":
            effective_plugin_id = "*"
        else:
            effective_plugin_id = plugin_id if plugin_id else getattr(self.ctx, "plugin_id", None)
        return LifecycleList(lc_records, plugin_id=effective_plugin_id, ctx=self.ctx, trace=trace, plan=plan)

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
