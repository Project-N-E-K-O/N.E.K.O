"""Adapter decorators implementation for SDK v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable[..., object])

ADAPTER_EVENT_META = "__adapter_event_meta__"
ADAPTER_LIFECYCLE_META = "__adapter_lifecycle_meta__"


@dataclass(slots=True)
class AdapterEventMeta:
    protocol: str
    action: str
    pattern: str | None
    priority: int

    def matches(self, *, protocol: str, action: str) -> bool:
        return (self.protocol in {"*", protocol}) and (self.action in {"*", action})


def _not_impl(*_args: object, **_kwargs: object) -> None:
    return None


def on_adapter_event(protocol: str = "*", action: str = "*", pattern: str | None = None, priority: int = 0) -> Callable[[F], F]:
    _not_impl(protocol, action, pattern, priority)
    def decorator(func: F) -> F:
        setattr(func, ADAPTER_EVENT_META, AdapterEventMeta(protocol=protocol, action=action, pattern=pattern, priority=priority))
        return func
    return decorator


def on_adapter_startup(func: F | None = None, *, priority: int = 0) -> F | Callable[[F], F]:
    _not_impl(func, priority)
    def decorator(inner: F) -> F:
        setattr(inner, ADAPTER_LIFECYCLE_META, {"stage": "startup", "priority": priority})
        return inner
    return decorator if func is None else decorator(func)


def on_adapter_shutdown(func: F | None = None, *, priority: int = 0) -> F | Callable[[F], F]:
    _not_impl(func, priority)
    def decorator(inner: F) -> F:
        setattr(inner, ADAPTER_LIFECYCLE_META, {"stage": "shutdown", "priority": priority})
        return inner
    return decorator if func is None else decorator(func)


def on_mcp_tool(pattern: str = "*", priority: int = 0) -> Callable[[F], F]:
    return on_adapter_event(protocol="mcp", action="tool_call", pattern=pattern, priority=priority)


def on_mcp_resource(pattern: str = "*", priority: int = 0) -> Callable[[F], F]:
    return on_adapter_event(protocol="mcp", action="resource_read", pattern=pattern, priority=priority)


def on_nonebot_message(message_type: str = "*", priority: int = 0) -> Callable[[F], F]:
    action = "message.*" if message_type == "*" else f"message.{message_type}"
    return on_adapter_event(protocol="nonebot", action=action, priority=priority)


__all__ = ["ADAPTER_EVENT_META", "ADAPTER_LIFECYCLE_META", "AdapterEventMeta", "on_adapter_event", "on_adapter_startup", "on_adapter_shutdown", "on_mcp_tool", "on_mcp_resource", "on_nonebot_message"]
