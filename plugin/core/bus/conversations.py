"""Conversation Bus SDK - independent conversation context storage"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Coroutine, Dict, Optional, Union

from .types import BusList, BusRecord
from ._client_base import _is_in_event_loop, _ensure_rpc, _validate_rpc_response, _parse_bus_items

if TYPE_CHECKING:
    from plugin.core.context import PluginContext


@dataclass(frozen=True, slots=True)
class ConversationRecord(BusRecord):
    conversation_id: Optional[str] = None
    turn_type: Optional[str] = None
    lanlan_name: Optional[str] = None
    message_count: int = 0

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "ConversationRecord":
        payload = raw if isinstance(raw, dict) else {"raw": raw}

        ts_raw = payload.get("timestamp")
        if ts_raw is None:
            ts_raw = payload.get("time")
        timestamp: Optional[float] = float(ts_raw) if isinstance(ts_raw, (int, float)) else None

        plugin_id = payload.get("plugin_id")
        source = payload.get("source")
        priority = payload.get("priority", 0)
        priority_int = priority if isinstance(priority, int) else (int(priority) if isinstance(priority, (float, str)) and priority else 0)

        content = payload.get("content")
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        conversation_id = metadata.get("conversation_id")
        turn_type = metadata.get("turn_type")
        lanlan_name = metadata.get("lanlan_name")
        message_count = metadata.get("message_count", 0)

        return ConversationRecord(
            kind="conversation",
            type=payload.get("message_type") or payload.get("type") or "conversation",
            timestamp=timestamp,
            plugin_id=plugin_id if isinstance(plugin_id, str) else (str(plugin_id) if plugin_id is not None else None),
            source=source if isinstance(source, str) else (str(source) if source is not None else None),
            priority=priority_int,
            content=content if isinstance(content, str) else (str(content) if content is not None else None),
            metadata=metadata,
            raw=payload,
            conversation_id=conversation_id if isinstance(conversation_id, str) else None,
            turn_type=turn_type if isinstance(turn_type, str) else None,
            lanlan_name=lanlan_name if isinstance(lanlan_name, str) else None,
            message_count=int(message_count) if message_count else 0,
        )

    @staticmethod
    def from_index(index: Dict[str, Any], payload: Optional[Dict[str, Any]] = None) -> "ConversationRecord":
        ts = index.get("timestamp")
        timestamp: Optional[float] = float(ts) if isinstance(ts, (int, float)) else None
        priority = index.get("priority")
        priority_int = priority if isinstance(priority, int) else (int(priority) if priority else 0)

        plugin_id = index.get("plugin_id")
        source = index.get("source")
        conversation_id = index.get("conversation_id")

        content = None
        metadata: Dict[str, Any] = {}
        turn_type = None
        lanlan_name = None
        message_count = 0

        if payload:
            content = payload.get("content")
            meta_raw = payload.get("metadata")
            metadata = meta_raw if isinstance(meta_raw, dict) else {}
            turn_type = metadata.get("turn_type")
            lanlan_name = metadata.get("lanlan_name")
            message_count = metadata.get("message_count", 0)

        return ConversationRecord(
            kind="conversation",
            type=index.get("type") or "conversation",
            timestamp=timestamp,
            plugin_id=plugin_id if isinstance(plugin_id, str) else (str(plugin_id) if plugin_id is not None else None),
            source=source if isinstance(source, str) else (str(source) if source is not None else None),
            priority=priority_int,
            content=content if isinstance(content, str) else (str(content) if content is not None else None),
            metadata=metadata,
            raw={"index": index, "payload": payload},
            conversation_id=conversation_id if isinstance(conversation_id, str) else None,
            turn_type=turn_type if isinstance(turn_type, str) else None,
            lanlan_name=lanlan_name if isinstance(lanlan_name, str) else None,
            message_count=int(message_count) if message_count else 0,
        )


class ConversationList(BusList[ConversationRecord]):
    pass


class ConversationClient:
    """Conversation Bus client"""
    def __init__(self, ctx: "PluginContext"):
        self.ctx = ctx

    def _get_impl(
        self,
        *,
        conversation_id: Optional[str] = None,
        max_count: int = 50,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ) -> ConversationList:
        args: Dict[str, Any] = {
            "store": "conversations",
            "topic": "all",
            "limit": int(max_count),
        }
        if conversation_id:
            args["conversation_id"] = conversation_id
        if since_ts is not None:
            args["since_ts"] = float(since_ts)

        rpc = _ensure_rpc(self.ctx)

        if conversation_id or since_ts:
            resp = rpc.request(op="bus.query", args=args, timeout=float(timeout))
            op_name = "bus.query"
        else:
            resp = rpc.request(
                op="bus.get_recent",
                args={"store": "conversations", "topic": "all", "limit": int(max_count)},
                timeout=float(timeout),
            )
            op_name = "bus.get_recent"

        raw_items = _validate_rpc_response(resp, op_name=op_name, timeout=timeout)
        records = _parse_bus_items(raw_items, ConversationRecord)
        return ConversationList(records, ctx=self.ctx)

    def get(
        self,
        *,
        conversation_id: Optional[str] = None,
        max_count: int = 50,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ) -> Union[ConversationList, Coroutine[Any, Any, ConversationList]]:
        if _is_in_event_loop():
            return self.get_async(
                conversation_id=conversation_id, max_count=max_count,
                since_ts=since_ts, timeout=timeout,
            )
        return self._get_impl(
            conversation_id=conversation_id, max_count=max_count,
            since_ts=since_ts, timeout=timeout,
        )

    async def get_async(
        self,
        *,
        conversation_id: Optional[str] = None,
        max_count: int = 50,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ) -> ConversationList:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._get_impl(
                conversation_id=conversation_id, max_count=max_count,
                since_ts=since_ts, timeout=timeout,
            ),
        )

    def get_by_id(
        self,
        conversation_id: str,
        *,
        max_count: int = 50,
        timeout: float = 5.0,
    ) -> Union[ConversationList, Coroutine[Any, Any, ConversationList]]:
        return self.get(
            conversation_id=conversation_id,
            max_count=max_count,
            timeout=timeout,
        )


class ConversationV2Client:
    """Message-plane wire adapter; decoding belongs to the SDK."""

    def __init__(self, ctx: "PluginContext"):
        self.ctx = ctx

    def register_watcher(self, sub_id: str, watcher: Any) -> None:
        self.ctx._register_conversation_watcher(sub_id, watcher)

    def unregister_watcher(self, sub_id: str) -> None:
        self.ctx._unregister_conversation_watcher(sub_id)

    def activate_watcher(self, watcher: Any) -> None:
        from plugin.core.bus.rev import activate_watcher

        activate_watcher(watcher)

    async def _request(self, op: str, args: Dict[str, Any], timeout: float) -> Any:
        import math
        import time
        from plugin.core.message_plane_transport import format_rpc_error
        from plugin.sdk.shared.models.exceptions import TransportError

        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        deadline = time.monotonic() + timeout
        cancelled = _threading.Event()
        loop = asyncio.get_running_loop()
        if not _v2_slots.acquire(blocking=False):
            raise TransportError("conversations_v2 request capacity exhausted", code="BUSY")

        def invoke():
            response = None
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or cancelled.is_set():
                    return None
                response = _ensure_rpc(self.ctx).request_sync(op=op, args=args, timeout=remaining)
                return response
            finally:
                _v2_slots.release()
                if isinstance(response, dict) and response.get("ok") is True and not response.get("error"):
                    from plugin.core.bus.rev import notify_conversation_read_capacity

                    notify_conversation_read_capacity()

        try:
            future = loop.run_in_executor(None, invoke)
        except BaseException:
            _v2_slots.release()
            raise
        try:
            response = await asyncio.wait_for(asyncio.shield(future), max(0, deadline - time.monotonic()))
        except asyncio.TimeoutError as exc:
            cancelled.set()
            raise TimeoutError(f"message_plane {op} timed out after {timeout}s") from exc
        except asyncio.CancelledError:
            cancelled.set()
            raise
        if not isinstance(response, dict):
            raise TimeoutError(f"message_plane {op} timed out after {timeout}s")
        if not response.get("ok") or response.get("error"):
            error = response.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            details = error.get("details") if isinstance(error, dict) else None
            raise TransportError(format_rpc_error(error), code=code, details=details)
        return response.get("result")

    async def get(
        self,
        *,
        character: str | None = None,
        role: str | None = None,
        kind: str | None = None,
        input_source: str | None = None,
        origin_kind: str | None = None,
        max_count: int = 50,
        order: str = "asc",
        since_ts: float | None = None,
        timeout: float = 5.0,
    ) -> list[dict[str, Any]]:
        snapshot = await self.get_snapshot(
            character=character, role=role, kind=kind, input_source=input_source,
            origin_kind=origin_kind, max_count=max_count, order=order,
            since_ts=since_ts, timeout=timeout,
        )
        return [{key: value for key, value in item.items() if key != "_revision"} for item in snapshot["items"]]

    async def get_snapshot(
        self,
        *,
        character: str | None = None,
        role: str | None = None,
        kind: str | None = None,
        input_source: str | None = None,
        origin_kind: str | None = None,
        max_count: int = 50,
        order: str = "asc",
        since_ts: float | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        from plugin.message_plane.protocol import ConversationGetArgs

        values = {
            "character": character,
            "role": role,
            "kind": kind,
            "input_source": input_source,
            "origin_kind": origin_kind,
            "max_count": max_count,
            "order": order,
            "since_ts": since_ts,
        }
        args = ConversationGetArgs.model_validate(values).model_dump(exclude_none=True)
        result = await self._request("bus.conversations.get", args, timeout)
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise RuntimeError("invalid conversations_v2 response")
        return result

    async def get_message(self, message_id: str, *, timeout: float = 5.0) -> dict[str, Any] | None:
        from plugin.message_plane.protocol import ConversationGetMessageArgs

        args = ConversationGetMessageArgs(message_id=message_id).model_dump()
        result = await self._request("bus.conversations.get_message", args, timeout)
        if result is not None and not isinstance(result, dict):
            raise RuntimeError("invalid conversations_v2 response")
        return result


import threading as _threading
# Control RPC also borrows the default executor for queue.put(). Keep a slot
# free even in Python 3.11's smallest standard default pool (five threads).
_v2_slots = _threading.BoundedSemaphore(4)
