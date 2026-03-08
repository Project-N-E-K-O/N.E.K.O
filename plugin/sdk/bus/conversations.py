"""Conversation Bus SDK - 独立的对话上下文存储"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Coroutine, Dict, List, Optional, Union

from plugin.sdk._deprecation import warn_sync_deprecated
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
    """对话 Bus 客户端"""
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
        warn_sync_deprecated("ConversationClient", "get", "get_async")
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

    async def get_by_id_async(
        self,
        conversation_id: str,
        *,
        max_count: int = 50,
        timeout: float = 5.0,
    ) -> ConversationList:
        return await self.get_async(
            conversation_id=conversation_id,
            max_count=max_count,
            timeout=timeout,
        )
