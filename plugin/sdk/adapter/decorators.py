"""
Adapter 事件装饰器

提供强类型、IDE 友好的事件监听机制。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Callable, Optional, TypeVar, ParamSpec, Awaitable, Union

__all__ = [
    "ADAPTER_EVENT_META",
    "AdapterEventMeta",
    "on_adapter_event",
    "on_adapter_startup",
    "on_adapter_shutdown",
]

# 元数据属性名
ADAPTER_EVENT_META = "__adapter_event_meta__"
ADAPTER_LIFECYCLE_META = "__adapter_lifecycle_meta__"

P = ParamSpec("P")
R = TypeVar("R")


@dataclass
class AdapterEventMeta:
    """
    Adapter 事件元数据
    
    存储在被装饰函数上，用于运行时发现和路由。
    """
    protocol: str           # 协议类型 (mcp/nonebot/openclaw/*)
    action: str             # 动作类型 (tool_call/message/*)
    pattern: Optional[str]  # 匹配模式（支持通配符）
    priority: int           # 优先级（数字越大越先执行）
    
    def matches(self, protocol: str, action: str, name: str = "") -> bool:
        """检查是否匹配"""
        import fnmatch
        
        # 协议匹配
        if self.protocol != "*" and self.protocol != protocol:
            return False
        
        # 动作匹配
        if self.action != "*" and not fnmatch.fnmatch(action, self.action):
            return False
        
        # 模式匹配
        if self.pattern and name:
            if not fnmatch.fnmatch(name, self.pattern):
                return False
        
        return True


def on_adapter_event(
    protocol: str = "*",
    action: str = "*",
    pattern: Optional[str] = None,
    priority: int = 0,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    注册 Adapter 事件处理器
    
    用于监听从 Adapter 网关转发过来的事件。
    
    Args:
        protocol: 协议类型 (mcp/nonebot/openclaw/*)，* 表示匹配所有
        action: 动作类型 (tool_call/resource_read/message/*)
        pattern: 可选的匹配模式，支持通配符 (* 和 ?)
        priority: 优先级，数字越大越先执行
    
    Example:
        ```python
        @on_adapter_event(protocol="mcp", action="tool_call", pattern="my_*")
        async def handle_my_tools(self, event: AdapterMessage) -> AdapterResponse:
            tool_name = event.payload.get("name")
            return event.reply({"result": "ok"})
        ```
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        # 存储元数据
        meta = AdapterEventMeta(
            protocol=protocol,
            action=action,
            pattern=pattern,
            priority=priority,
        )
        setattr(func, ADAPTER_EVENT_META, meta)
        
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            return await func(*args, **kwargs)
        
        # 复制元数据到 wrapper
        setattr(wrapper, ADAPTER_EVENT_META, meta)
        return wrapper
    
    return decorator


def on_adapter_startup(
    func: Optional[Callable[P, Awaitable[R]]] = None,
    *,
    priority: int = 0,
) -> Union[Callable[P, Awaitable[R]], Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]]:
    """
    标记 Adapter 启动时执行的方法
    
    Example:
        ```python
        @on_adapter_startup
        async def setup(self):
            self.logger.info("Adapter starting...")
        
        @on_adapter_startup(priority=10)
        async def setup_early(self):
            self.logger.info("Early setup...")
        ```
    """
    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        setattr(fn, ADAPTER_LIFECYCLE_META, {"type": "startup", "priority": priority})
        return fn
    
    if func is not None:
        return decorator(func)
    return decorator


def on_adapter_shutdown(
    func: Optional[Callable[P, Awaitable[R]]] = None,
    *,
    priority: int = 0,
) -> Union[Callable[P, Awaitable[R]], Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]]:
    """
    标记 Adapter 关闭时执行的方法
    
    Example:
        ```python
        @on_adapter_shutdown
        async def cleanup(self):
            self.logger.info("Adapter stopping...")
        
        @on_adapter_shutdown(priority=10)
        async def cleanup_early(self):
            self.logger.info("Early cleanup...")
        ```
    """
    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        setattr(fn, ADAPTER_LIFECYCLE_META, {"type": "shutdown", "priority": priority})
        return fn
    
    if func is not None:
        return decorator(func)
    return decorator


# ========== 协议专用装饰器（语法糖）==========

def on_mcp_tool(
    pattern: str = "*",
    priority: int = 0,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    监听 MCP tool_call 事件
    
    Example:
        ```python
        @on_mcp_tool("get_weather")
        async def handle_weather(self, event: AdapterMessage) -> AdapterResponse:
            city = event.payload.get("arguments", {}).get("city")
            return event.reply({"weather": "sunny"})
        ```
    """
    return on_adapter_event(protocol="mcp", action="tool_call", pattern=pattern, priority=priority)


def on_mcp_resource(
    pattern: str = "*",
    priority: int = 0,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    监听 MCP resource_read 事件
    """
    return on_adapter_event(protocol="mcp", action="resource_read", pattern=pattern, priority=priority)


def on_nonebot_message(
    message_type: str = "*",
    priority: int = 0,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    监听 NoneBot 消息事件
    
    Args:
        message_type: 消息类型 (private/group/*)
        priority: 优先级
    
    Example:
        ```python
        @on_nonebot_message("group")
        async def handle_group(self, event: AdapterMessage) -> AdapterResponse:
            return event.reply("收到群消息")
        ```
    """
    action = f"message.{message_type}" if message_type != "*" else "message.*"
    return on_adapter_event(protocol="nonebot", action=action, priority=priority)
