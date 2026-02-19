"""
Bus 协议定义模块

从 types.py 拆分出来，包含所有 Protocol 定义。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from plugin.sdk.bus.events import EventList
    from plugin.sdk.bus.lifecycle import LifecycleList
    from plugin.sdk.bus.memory import MemoryList
    from plugin.sdk.bus.messages import MessageList
    from plugin.sdk.bus.conversations import ConversationList


class _MessageClientProto(Protocol):
    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        priority_min: Optional[int] = None,
        timeout: float = 5.0,
    ) -> "MessageList": ...


class _EventClientProto(Protocol):
    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        timeout: float = 5.0,
    ) -> "EventList": ...


class _LifecycleClientProto(Protocol):
    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        timeout: float = 5.0,
    ) -> "LifecycleList": ...


class _MemoryClientProto(Protocol):
    def get(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> "MemoryList": ...


class _ConversationClientProto(Protocol):
    def get(
        self,
        *,
        conversation_id: Optional[str] = None,
        max_count: int = 50,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ) -> "ConversationList": ...
    
    def get_by_id(
        self,
        conversation_id: str,
        *,
        max_count: int = 50,
        timeout: float = 5.0,
    ) -> "ConversationList": ...


class BusHubProtocol(Protocol):
    """Bus Hub 协议，提供对各种 Bus 客户端的访问
    
    Attributes:
        messages: 消息客户端，用于查询消息
        events: 事件客户端，用于查询事件
        lifecycle: 生命周期客户端，用于查询生命周期事件
        memory: 内存客户端，用于查询内存数据
        conversations: 对话客户端，用于查询对话上下文
    """
    messages: _MessageClientProto
    events: _EventClientProto
    lifecycle: _LifecycleClientProto
    memory: _MemoryClientProto
    conversations: _ConversationClientProto


class BusReplayContext(Protocol):
    bus: BusHubProtocol

    # Internal helper used by SDK when running inside plugin process.
    # Exposed here for typing completeness; actual implementation lives on PluginContext.
    def _send_request_and_wait(
        self,
        *,
        method_name: str,
        request_type: str,
        request_data: Dict[str, Any],
        timeout: float,
        wrap_result: bool = True,
        **kwargs: Any,
    ) -> Any: ...
