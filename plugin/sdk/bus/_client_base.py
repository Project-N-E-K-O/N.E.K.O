"""Generic base classes for bus clients that share message_plane RPC patterns."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any, Coroutine, Dict, List, Optional, Sequence, TypeVar, Union

from plugin.settings import MESSAGE_PLANE_ZMQ_RPC_ENDPOINT
from .types import BusList, BusOp, BusRecord, GetNode

from plugin.sdk.message_plane_transport import MessagePlaneRpcClient as _MessagePlaneRpcClient
from plugin.sdk.message_plane_transport import format_rpc_error

if TYPE_CHECKING:
    from plugin.core.context import PluginContext

TRecord = TypeVar("TRecord", bound=BusRecord)


# ── Shared utilities ───────────────────────────────────────────────────

def _is_in_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _ensure_rpc(ctx: "PluginContext") -> _MessagePlaneRpcClient:
    rpc = getattr(ctx, "_mp_rpc_client", None)
    if rpc is None:
        rpc = _MessagePlaneRpcClient(
            plugin_id=getattr(ctx, "plugin_id", ""),
            endpoint=str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT),
        )
        try:
            ctx._mp_rpc_client = rpc
        except Exception:
            pass
    return rpc


def _validate_rpc_response(resp: Any, *, op_name: str, timeout: float) -> List[Any]:
    if not isinstance(resp, dict):
        raise TimeoutError(f"message_plane {op_name} timed out after {timeout}s")
    if resp.get("error"):
        raise RuntimeError(format_rpc_error(resp["error"]))
    if not resp.get("ok"):
        raise RuntimeError(format_rpc_error(resp.get("error")))
    result = resp.get("result")
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return result["items"]
    return []


def _parse_bus_items(raw_items: List[Any], record_cls: type) -> list:
    records: list = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        p = item.get("payload")
        if isinstance(idx, dict):
            records.append(record_cls.from_index(idx, p if isinstance(p, dict) else None))
        elif isinstance(p, dict):
            records.append(record_cls.from_raw(p))
        else:
            records.append(record_cls.from_raw(item))
    return records


# ── _PluginBusList ─────────────────────────────────────────────────────

class _PluginBusList(BusList[TRecord]):
    """BusList that tracks plugin_id through chain operations."""

    def __init__(
        self,
        items: Sequence[TRecord],
        *,
        plugin_id: Optional[str] = None,
        ctx: Optional[Any] = None,
        trace: Optional[Sequence[BusOp]] = None,
        plan: Optional[Any] = None,
        fast_mode: bool = False,
    ):
        super().__init__(items, ctx=ctx, trace=trace, plan=plan, fast_mode=fast_mode)
        self.plugin_id = plugin_id

    def merge(self, other: "BusList[TRecord]") -> "_PluginBusList[TRecord]":
        merged = super().merge(other)
        other_pid = getattr(other, "plugin_id", None)
        if self.plugin_id != other_pid:
            merged.plugin_id = "*"  # type: ignore[attr-defined]
        return merged  # type: ignore[return-value]


# ── BusRpcClientBase ───────────────────────────────────────────────────

class BusRpcClientBase:
    """Base for bus clients using message_plane RPC.

    Subclasses set: _store_name, _record_cls, _list_cls, _policy_prefix.
    """
    _store_name: str
    _record_cls: type
    _list_cls: type
    _policy_prefix: str

    def __init__(self, ctx: "PluginContext"):
        self.ctx = ctx

    def _enforce(self, op: str) -> None:
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy(f"{self._policy_prefix}.{op}")

    def _build_query_args(
        self,
        *,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        filter: Optional[Dict[str, Any]] = None,
        since_ts: Optional[float] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """Build RPC args. Returns (op_name, args)."""
        args: Dict[str, Any] = {
            "store": self._store_name,
            "topic": "all",
            "limit": int(max_count),
            "light": False,
        }
        if since_ts is not None:
            args["since_ts"] = float(since_ts)

        flt = dict(filter) if isinstance(filter, dict) else {}
        if plugin_id is None and isinstance(flt.get("plugin_id"), str) and flt["plugin_id"]:
            plugin_id = str(flt["plugin_id"])
        if isinstance(plugin_id, str) and plugin_id.strip() and plugin_id.strip() != "*":
            args["plugin_id"] = plugin_id.strip()
        for key in ("source", "kind", "type"):
            val = flt.get(key)
            if isinstance(val, str) and val:
                args[key] = val
        if "priority_min" in flt:
            args["priority_min"] = flt["priority_min"]
        if "until_ts" in flt:
            args["until_ts"] = flt["until_ts"]

        has_filters = any(
            args.get(k) is not None
            for k in ("plugin_id", "source", "kind", "type", "priority_min", "since_ts", "until_ts")
        )
        if not has_filters:
            return "bus.get_recent", {
                "store": self._store_name, "topic": "all",
                "limit": int(max_count), "light": False,
            }
        return "bus.query", args

    def _finalize_get(
        self,
        resp: Any,
        *,
        op_name: str,
        plugin_id: Optional[str],
        max_count: int,
        filter: Optional[Dict[str, Any]],
        strict: bool,
        since_ts: Optional[float],
        timeout: float,
    ):
        raw_items = _validate_rpc_response(resp, op_name=op_name, timeout=timeout)
        records = _parse_bus_items(raw_items, self._record_cls)

        get_params: Dict[str, Any] = {
            "plugin_id": plugin_id, "max_count": max_count,
            "filter": dict(filter) if isinstance(filter, dict) else None,
            "strict": bool(strict), "since_ts": since_ts,
            "timeout": timeout, "via": "message_plane.rpc",
        }
        trace = [BusOp(name="get", params=get_params, at=time.time())]
        plan = GetNode(op="get", params={"bus": self._store_name, "params": get_params}, at=time.time())

        if isinstance(plugin_id, str) and plugin_id.strip() == "*":
            effective_pid = "*"
        else:
            effective_pid = plugin_id if plugin_id else getattr(self.ctx, "plugin_id", None)
        return self._list_cls(records, plugin_id=effective_pid, ctx=self.ctx, trace=trace, plan=plan)

    async def get_async(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        filter: Optional[Dict[str, Any]] = None,
        strict: bool = True,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ):
        self._enforce("get")
        op, args = self._build_query_args(plugin_id=plugin_id, max_count=max_count, filter=filter, since_ts=since_ts)
        resp = await _ensure_rpc(self.ctx).request_async(op=op, args=args, timeout=float(timeout))
        return self._finalize_get(
            resp, op_name=op, plugin_id=plugin_id, max_count=max_count,
            filter=filter, strict=strict, since_ts=since_ts, timeout=timeout,
        )

    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        filter: Optional[Dict[str, Any]] = None,
        strict: bool = True,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ) -> Union[Any, Coroutine]:
        if _is_in_event_loop():
            return self.get_async(
                plugin_id=plugin_id, max_count=max_count, filter=filter,
                strict=strict, since_ts=since_ts, timeout=timeout,
            )
        self._enforce("get")
        op, args = self._build_query_args(plugin_id=plugin_id, max_count=max_count, filter=filter, since_ts=since_ts)
        resp = _ensure_rpc(self.ctx).request(op=op, args=args, timeout=float(timeout))
        return self._finalize_get(
            resp, op_name=op, plugin_id=plugin_id, max_count=max_count,
            filter=filter, strict=strict, since_ts=since_ts, timeout=timeout,
        )


# ── BusDeletableClientBase ─────────────────────────────────────────────

class BusDeletableClientBase(BusRpcClientBase):
    """Adds delete capability (events / lifecycle)."""
    _del_type: str
    _id_field: str

    def _prepare_delete(self, record_id: str, timeout: float) -> tuple[str, Dict[str, Any]]:
        self._enforce("delete")
        if getattr(self.ctx, "_plugin_comm_queue", None) is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin "
                f"{getattr(self.ctx, 'plugin_id', 'unknown')}. "
                "This method can only be called from within a plugin process."
            )
        rid = str(record_id).strip() if record_id is not None else ""
        if not rid:
            raise ValueError(f"{self._id_field} is required")
        req_id = str(uuid.uuid4())
        request = {
            "type": self._del_type,
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": req_id,
            self._id_field: rid,
            "timeout": float(timeout),
        }
        return req_id, request

    def _try_zmq_delete(self, request: dict, timeout: float) -> Optional[dict]:
        """Try ZMQ path. Returns response dict, None (no zmq), or raises on ZMQ failure."""
        zmq_client = getattr(self.ctx, "_zmq_ipc_client", None)
        if zmq_client is None:
            return None
        try:
            resp = zmq_client.request(request, timeout=float(timeout))
            if isinstance(resp, dict):
                return resp
        except Exception as exc:
            raise TimeoutError(
                f"{self._del_type} over ZeroMQ timed out or failed after {timeout}s: {exc}"
            ) from exc
        raise TimeoutError(f"{self._del_type} over ZeroMQ timed out or failed after {timeout}s")

    def _check_delete_response(self, response: Any) -> bool:
        if response is None:
            raise TimeoutError(f"{self._del_type} timed out")
        if not isinstance(response, dict):
            raise RuntimeError(f"Invalid {self._del_type} response")
        if response.get("error"):
            raise RuntimeError(str(response["error"]))
        result = response.get("result")
        return bool(result.get("deleted")) if isinstance(result, dict) else False

    def delete(self, record_id: str, timeout: float = 5.0) -> Union[bool, Coroutine[Any, Any, bool]]:
        if _is_in_event_loop():
            return self.delete_async(record_id=record_id, timeout=timeout)
        from plugin.core.state import state
        req_id, request = self._prepare_delete(record_id, timeout)
        resp = self._try_zmq_delete(request, timeout)
        if resp is None:
            try:
                self.ctx._plugin_comm_queue.put(request, timeout=timeout)
            except Exception as e:
                raise RuntimeError(f"Failed to send {self._del_type}: {e}") from e
            resp = state.wait_for_plugin_response(req_id, timeout)
        return self._check_delete_response(resp)

    async def delete_async(self, record_id: str, timeout: float = 5.0) -> bool:
        from plugin.core.state import state
        req_id, request = self._prepare_delete(record_id, timeout)
        resp = self._try_zmq_delete(request, timeout)
        if resp is None:
            try:
                self.ctx._plugin_comm_queue.put(request, timeout=timeout)
            except Exception as e:
                raise RuntimeError(f"Failed to send {self._del_type}: {e}") from e
            loop = asyncio.get_running_loop()
            start = loop.time()
            resp = None
            while loop.time() - start < timeout:
                resp = state.get_plugin_response(req_id)
                if resp is not None:
                    break
                await asyncio.sleep(0.01)
        return self._check_delete_response(resp)
