"""
Conversation Bus SDK - 独立的对话上下文存储

与 messages bus 分离，专门用于存储和查询对话上下文。
支持通过 conversation_id 查询触发插件的对话历史。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union, Coroutine

from plugin.settings import MESSAGE_PLANE_ZMQ_RPC_ENDPOINT
from .types import BusList, BusOp, BusRecord, GetNode

from plugin.sdk.message_plane_transport import MessagePlaneRpcClient as _MessagePlaneRpcClient
from plugin.sdk.message_plane_transport import format_rpc_error

if TYPE_CHECKING:
    from plugin.core.context import PluginContext


@dataclass(frozen=True, slots=True)
class ConversationRecord(BusRecord):
    """对话记录"""
    conversation_id: Optional[str] = None
    turn_type: Optional[str] = None  # "turn_end" | "session_end" | "renew_session"
    lanlan_name: Optional[str] = None
    message_count: int = 0

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "ConversationRecord":
        """从原始 payload 创建 ConversationRecord"""
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

        # 从 metadata 提取对话相关字段
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
        """从 index 快速创建 ConversationRecord"""
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
    """对话列表"""
    pass


@dataclass
class ConversationClient:
    """对话 Bus 客户端
    
    用于查询独立的 conversations store 中的对话上下文。
    
    Example:
        # 通过 conversation_id 获取对话
        conversations = await ctx.bus.conversations.get_by_id(conversation_id)
        for conv in conversations:
            print(f"[{conv.turn_type}] {conv.content}")
    """
    ctx: "PluginContext"

    def _is_in_event_loop(self) -> bool:
        """检测当前是否在事件循环中运行"""
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def _get_rpc_client(self) -> _MessagePlaneRpcClient:
        """获取或创建 RPC 客户端"""
        rpc = getattr(self.ctx, "_conv_rpc_client", None)
        if rpc is None:
            rpc = _MessagePlaneRpcClient(
                plugin_id=getattr(self.ctx, "plugin_id", ""),
                endpoint=str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT)
            )
            try:
                self.ctx._conv_rpc_client = rpc  # type: ignore
            except Exception:
                pass
        return rpc

    def get_sync(
        self,
        *,
        conversation_id: Optional[str] = None,
        max_count: int = 50,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ) -> ConversationList:
        """同步获取对话列表
        
        Args:
            conversation_id: 对话ID，用于过滤特定对话
            max_count: 最大返回数量
            since_ts: 时间戳过滤
            timeout: 超时时间
        """
        args: Dict[str, Any] = {
            "store": "conversations",
            "topic": "all",
            "limit": int(max_count),
        }
        if conversation_id:
            args["conversation_id"] = conversation_id
        if since_ts is not None:
            args["since_ts"] = float(since_ts)

        rpc = self._get_rpc_client()
        
        # 如果有过滤条件，使用 bus.query
        if conversation_id or since_ts:
            resp = rpc.request(op="bus.query", args=args, timeout=float(timeout))
        else:
            resp = rpc.request(
                op="bus.get_recent",
                args={"store": "conversations", "topic": "all", "limit": int(max_count)},
                timeout=float(timeout),
            )

        if not isinstance(resp, dict):
            raise TimeoutError(f"conversations bus request timed out after {timeout}s")
        if not resp.get("ok"):
            raise RuntimeError(format_rpc_error(resp.get("error")))

        result = resp.get("result")
        items: List[Any] = []
        if isinstance(result, dict):
            got = result.get("items")
            if isinstance(got, list):
                items = got
        elif isinstance(result, list):
            items = result

        records: List[ConversationRecord] = []
        for ev in items:
            if not isinstance(ev, dict):
                continue
            idx = ev.get("index")
            p = ev.get("payload")
            if isinstance(idx, dict):
                records.append(ConversationRecord.from_index(idx, p if isinstance(p, dict) else None))
            elif isinstance(p, dict):
                records.append(ConversationRecord.from_raw(p))

        return ConversationList(records, ctx=self.ctx)

    async def get_async(
        self,
        *,
        conversation_id: Optional[str] = None,
        max_count: int = 50,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ) -> ConversationList:
        """异步获取对话列表"""
        # 在线程池中执行同步调用
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.get_sync(
                conversation_id=conversation_id,
                max_count=max_count,
                since_ts=since_ts,
                timeout=timeout,
            )
        )

    def get(
        self,
        *,
        conversation_id: Optional[str] = None,
        max_count: int = 50,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ) -> Union[ConversationList, Coroutine[Any, Any, ConversationList]]:
        """智能获取对话列表（自动检测同步/异步环境）"""
        if self._is_in_event_loop():
            return self.get_async(
                conversation_id=conversation_id,
                max_count=max_count,
                since_ts=since_ts,
                timeout=timeout,
            )
        return self.get_sync(
            conversation_id=conversation_id,
            max_count=max_count,
            since_ts=since_ts,
            timeout=timeout,
        )

    def get_by_id(
        self,
        conversation_id: str,
        *,
        max_count: int = 50,
        timeout: float = 5.0,
    ) -> Union[ConversationList, Coroutine[Any, Any, ConversationList]]:
        """通过 conversation_id 获取对话
        
        Args:
            conversation_id: 对话ID（由 cross_server 生成）
            max_count: 最大返回数量
            timeout: 超时时间
            
        Example:
            # 在插件 entry 中使用
            ctx = args.get("_ctx", {})
            conversation_id = ctx.get("conversation_id")
            if conversation_id:
                conversations = await self.ctx.bus.conversations.get_by_id(conversation_id)
                for conv in conversations:
                    # conv.content 是 JSON 格式的对话消息列表
                    import json
                    messages = json.loads(conv.content or "[]")
                    for msg in messages:
                        print(f"[{msg['role']}] {msg['text']}")
        """
        return self.get(
            conversation_id=conversation_id,
            max_count=max_count,
            timeout=timeout,
        )
