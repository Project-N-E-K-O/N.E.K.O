"""
Adapter 类型定义

提供强类型的消息和响应结构，支持 IDE 自动补全。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Generic, List, Optional, TypeVar

__all__ = [
    "Protocol",
    "AdapterMessage",
    "AdapterResponse",
    "RouteTarget",
    "RouteRule",
]


class Protocol(str, Enum):
    """支持的协议类型"""
    MCP = "mcp"
    NONEBOT = "nonebot"
    OPENCLAW = "openclaw"
    HTTP = "http"
    WEBSOCKET = "websocket"
    CUSTOM = "custom"


class RouteTarget(str, Enum):
    """路由目标类型"""
    SELF = "self"           # Adapter 自身处理
    PLUGIN = "plugin"       # 转发到指定插件
    BROADCAST = "broadcast" # 广播到所有订阅者
    DROP = "drop"           # 丢弃


# 泛型 Payload 类型
T = TypeVar("T")


@dataclass
class AdapterMessage(Generic[T]):
    """
    统一的 Adapter 消息格式
    
    泛型参数 T 表示 payload 的具体类型，支持 IDE 类型推断。
    
    Attributes:
        id: 消息唯一标识
        protocol: 来源协议
        action: 动作类型 (tool_call, resource_read, message, etc.)
        payload: 消息体（泛型）
        source: 来源标识
        target: 目标插件ID或通配符
        timestamp: 时间戳
        metadata: 元数据
    """
    id: str
    protocol: Protocol
    action: str
    payload: T
    source: str = ""
    target: str = "*"
    timestamp: float = 0.0
    metadata: Dict[str, object] = field(default_factory=dict)
    
    def reply(self, data: object, success: bool = True) -> "AdapterResponse":
        """创建响应消息"""
        return AdapterResponse(
            request_id=self.id,
            success=success,
            data=data,
            protocol=self.protocol,
        )
    
    def error(self, message: str, code: Optional[str] = None) -> "AdapterResponse":
        """创建错误响应"""
        return AdapterResponse(
            request_id=self.id,
            success=False,
            error=message,
            error_code=code,
            protocol=self.protocol,
        )


@dataclass
class AdapterResponse:
    """
    Adapter 响应格式
    
    Attributes:
        request_id: 对应请求的ID
        success: 是否成功
        data: 响应数据
        error: 错误信息
        error_code: 错误代码
        protocol: 协议类型
    """
    request_id: str
    success: bool = True
    data: object = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    protocol: Protocol = Protocol.CUSTOM
    
    def to_dict(self) -> Dict[str, object]:
        """转换为字典"""
        result = {
            "request_id": self.request_id,
            "success": self.success,
        }
        if self.success:
            result["data"] = self.data
        else:
            result["error"] = self.error
            if self.error_code:
                result["error_code"] = self.error_code
        return result


@dataclass
class RouteRule:
    """
    路由规则
    
    Attributes:
        protocol: 匹配的协议
        action: 匹配的动作（支持通配符 *）
        pattern: 匹配模式（支持通配符）
        target: 路由目标类型
        plugin_id: 目标插件ID（当 target=plugin 时）
        entry: 目标入口（当 target=plugin 时）
        priority: 优先级（数字越大越先匹配）
    """
    protocol: str = "*"
    action: str = "*"
    pattern: Optional[str] = None
    target: RouteTarget = RouteTarget.SELF
    plugin_id: Optional[str] = None
    entry: Optional[str] = None
    priority: int = 0
    
    def matches(self, msg: AdapterMessage) -> bool:
        """检查消息是否匹配此规则"""
        # 协议匹配
        if self.protocol != "*" and self.protocol != msg.protocol.value:
            return False
        
        # 动作匹配
        if self.action != "*" and not self._wildcard_match(self.action, msg.action):
            return False
        
        # 模式匹配（如果指定）
        if self.pattern:
            # 从 payload 中提取匹配目标
            match_target = ""
            if isinstance(msg.payload, dict):
                match_target = msg.payload.get("name", "") or msg.payload.get("tool", "")
            elif hasattr(msg.payload, "name"):
                match_target = getattr(msg.payload, "name", "")
            
            if not self._wildcard_match(self.pattern, match_target):
                return False
        
        return True
    
    @staticmethod
    def _wildcard_match(pattern: str, text: str) -> bool:
        """简单的通配符匹配（支持 * 和 ?）"""
        import fnmatch
        return fnmatch.fnmatch(text, pattern)
