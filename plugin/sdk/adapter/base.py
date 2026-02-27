"""
Adapter 基类

提供 Adapter 插件的核心功能和生命周期管理。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from plugin.sdk.adapter.gateway_contracts import LoggerLike

if TYPE_CHECKING:
    from plugin.core.context import PluginContext
    from plugin.sdk.adapter.types import AdapterMessage, AdapterResponse, RouteRule

__all__ = [
    "AdapterMode",
    "AdapterConfig",
    "AdapterContext",
    "AdapterBase",
]


class AdapterMode(str, Enum):
    """Adapter 工作模式"""
    GATEWAY = "gateway"   # 网关模式：转发请求到其他插件
    ROUTER = "router"     # 路由模式：直接处理请求
    BRIDGE = "bridge"     # 桥接模式：协议转换
    HYBRID = "hybrid"     # 混合模式：根据规则选择


@dataclass
class AdapterConfig:
    """
    Adapter 配置
    
    从 plugin.toml 的 [adapter] 部分解析。
    
    Attributes:
        mode: 工作模式
        protocols: 协议配置 {protocol_name: config_dict}
        routes: 路由规则列表
        priority: Adapter 启动优先级（数字越小越先启动）
    """
    mode: AdapterMode = AdapterMode.HYBRID
    protocols: Dict[str, Dict[str, object]] = field(default_factory=dict)
    routes: List[Dict[str, object]] = field(default_factory=list)
    priority: int = 0
    
    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "AdapterConfig":
        """从字典创建配置"""
        mode_str = data.get("mode", "hybrid")
        try:
            mode = AdapterMode(str(mode_str))
        except ValueError:
            mode = AdapterMode.HYBRID
        
        # 类型安全的提取
        protocols_raw = data.get("protocols", {})
        protocols = dict(protocols_raw) if isinstance(protocols_raw, dict) else {}
        
        routes_raw = data.get("routes", [])
        routes = list(routes_raw) if isinstance(routes_raw, list) else []
        
        priority_raw = data.get("priority", 0)
        priority = int(priority_raw) if isinstance(priority_raw, (int, float, str)) else 0
        
        return cls(
            mode=mode,
            protocols=protocols,  # type: ignore[arg-type]
            routes=routes,  # type: ignore[arg-type]
            priority=priority,
        )


class AdapterContext:
    """
    Adapter 上下文
    
    提供 Adapter 与 NEKO 系统交互的能力。
    
    Attributes:
        adapter_id: Adapter 的插件ID
        config: Adapter 配置
        logger: 日志记录器
        plugin_ctx: 底层的 PluginContext（如果有）
    """
    
    def __init__(
        self,
        adapter_id: str,
        config: AdapterConfig,
        logger: LoggerLike,
        plugin_ctx: Optional["PluginContext"] = None,
    ):
        self.adapter_id = adapter_id
        self.config = config
        self.logger: LoggerLike = logger
        self._plugin_ctx = plugin_ctx
        self._event_handlers: Dict[str, List[Callable]] = {}
    
    async def call_plugin(
        self,
        plugin_id: str,
        entry: str,
        payload: Dict[str, object],
        timeout: float = 30.0,
    ) -> object:
        """
        调用指定插件的入口
        
        Args:
            plugin_id: 目标插件ID
            entry: 入口ID
            payload: 请求参数
            timeout: 超时时间（秒）
        
        Returns:
            插件返回的结果
        """
        if self._plugin_ctx is None:
            raise RuntimeError("AdapterContext not bound to PluginContext")
        
        # 使用 PluginContext 的 trigger_plugin_event_async 能力
        return await self._plugin_ctx.trigger_plugin_event_async(
            target_plugin_id=plugin_id,
            event_type="adapter_call",
            event_id=entry,
            params=payload,
            timeout=timeout,
        )
    
    async def broadcast_event(
        self,
        event_type: str,
        payload: Dict[str, object],
    ) -> List[object]:
        """
        广播事件到所有订阅的插件
        
        Args:
            event_type: 事件类型
            payload: 事件数据
        
        Returns:
            所有响应的列表
        """
        if self._plugin_ctx is None:
            raise RuntimeError("AdapterContext not bound to PluginContext")
        
        # TODO: 实现广播机制
        self.logger.debug("Broadcasting event: {} with payload: {}", event_type, payload)
        return []
    
    def register_event_handler(
        self,
        event_key: str,
        handler: Callable,
    ) -> None:
        """注册事件处理器"""
        if event_key not in self._event_handlers:
            self._event_handlers[event_key] = []
        self._event_handlers[event_key].append(handler)
    
    def get_event_handlers(self, event_key: str) -> List[Callable]:
        """获取事件处理器列表"""
        return self._event_handlers.get(event_key, [])


class AdapterBase(ABC):
    """
    Adapter 基类
    
    所有 Adapter 插件必须继承此类。
    
    示例:
        ```python
        from plugin.sdk.adapter import AdapterBase, AdapterConfig, AdapterContext
        
        class MyAdapter(AdapterBase):
            async def on_startup(self) -> None:
                self.logger.info("MyAdapter started")
            
            async def on_shutdown(self) -> None:
                self.logger.info("MyAdapter stopped")
        ```
    """
    
    def __init__(self, config: AdapterConfig, ctx: AdapterContext):
        self.config = config
        self.ctx = ctx
        self.logger = ctx.logger
        self._routes: List["RouteRule"] = []
        self._tools: Dict[str, Callable] = {}
        self._resources: Dict[str, Callable] = {}
        self._running = False
    
    @property
    def adapter_id(self) -> str:
        """获取 Adapter ID"""
        return self.ctx.adapter_id
    
    @property
    def mode(self) -> AdapterMode:
        """获取工作模式"""
        return self.config.mode
    
    # ========== 生命周期方法 ==========
    
    @abstractmethod
    async def on_startup(self) -> None:
        """
        Adapter 启动时调用
        
        在此方法中初始化协议连接、注册工具等。
        """
        ...
    
    @abstractmethod
    async def on_shutdown(self) -> None:
        """
        Adapter 关闭时调用
        
        在此方法中清理资源、关闭连接等。
        """
        ...
    
    async def on_message(self, msg: "AdapterMessage") -> Optional["AdapterResponse"]:
        """
        处理收到的消息
        
        默认实现根据路由规则分发消息。子类可以重写此方法。
        
        Args:
            msg: 收到的消息
        
        Returns:
            响应消息，或 None 表示不响应
        """
        from plugin.sdk.adapter.types import RouteTarget
        
        # 查找匹配的路由规则
        route = self._find_matching_route(msg)
        
        if route is None:
            self.logger.debug("No matching route for message: {}", msg.id)
            return None
        
        if route.target == RouteTarget.DROP:
            return None
        
        if route.target == RouteTarget.SELF:
            return await self._handle_locally(msg)
        
        if route.target == RouteTarget.PLUGIN:
            return await self._forward_to_plugin(msg, route)
        
        if route.target == RouteTarget.BROADCAST:
            responses = await self.ctx.broadcast_event(
                f"{msg.protocol.value}.{msg.action}",
                {"message": msg},
            )
            # 返回第一个非空响应
            for resp in responses:
                if resp is not None:
                    return msg.reply(resp)
            return None
        
        return None
    
    # ========== Router 模式 API ==========
    
    def register_tool(
        self,
        name: str,
        handler: Callable,
        schema: Optional[Dict[str, object]] = None,
    ) -> None:
        """
        注册一个工具（Router 模式）
        
        Args:
            name: 工具名称
            handler: 处理函数
            schema: JSON Schema 描述
        """
        self._tools[name] = handler
        self.logger.debug("Registered tool: {}", name)
    
    def register_resource(
        self,
        uri: str,
        handler: Callable,
    ) -> None:
        """
        注册一个资源（Router 模式）
        
        Args:
            uri: 资源 URI
            handler: 处理函数
        """
        self._resources[uri] = handler
        self.logger.debug("Registered resource: {}", uri)
    
    def get_tool(self, name: str) -> Optional[Callable]:
        """获取工具处理函数"""
        return self._tools.get(name)
    
    def get_resource(self, uri: str) -> Optional[Callable]:
        """获取资源处理函数"""
        return self._resources.get(uri)
    
    def list_tools(self) -> List[str]:
        """列出所有注册的工具"""
        return list(self._tools.keys())
    
    def list_resources(self) -> List[str]:
        """列出所有注册的资源"""
        return list(self._resources.keys())
    
    # ========== Gateway 模式 API ==========
    
    async def forward_to_plugin(
        self,
        plugin_id: str,
        entry: str,
        payload: Dict[str, object],
        timeout: float = 30.0,
    ) -> object:
        """
        转发请求到指定插件
        
        Args:
            plugin_id: 目标插件ID
            entry: 入口ID
            payload: 请求参数
            timeout: 超时时间
        
        Returns:
            插件返回的结果
        """
        return await self.ctx.call_plugin(plugin_id, entry, payload, timeout)
    
    async def broadcast(
        self,
        event_type: str,
        payload: Dict[str, object],
    ) -> List[object]:
        """
        广播事件到所有订阅的插件
        
        Args:
            event_type: 事件类型
            payload: 事件数据
        
        Returns:
            所有响应的列表
        """
        return await self.ctx.broadcast_event(event_type, payload)
    
    # ========== 路由管理 ==========
    
    def add_route(self, route: "RouteRule") -> None:
        """添加路由规则"""
        self._routes.append(route)
        # 按优先级排序（高优先级在前）
        self._routes.sort(key=lambda r: -r.priority)
    
    def _find_matching_route(self, msg: "AdapterMessage") -> Optional["RouteRule"]:
        """查找匹配的路由规则"""
        for route in self._routes:
            if route.matches(msg):
                return route
        return None
    
    async def _handle_locally(self, msg: "AdapterMessage") -> Optional["AdapterResponse"]:
        """本地处理消息"""
        # 检查是否有对应的工具
        if msg.action == "tool_call":
            tool_name = msg.payload.get("name") if isinstance(msg.payload, dict) else None
            if tool_name and tool_name in self._tools:
                handler = self._tools[tool_name]
                args = msg.payload.get("arguments", {}) if isinstance(msg.payload, dict) else {}
                try:
                    result = await handler(**args) if callable(handler) else None
                    return msg.reply(result)
                except Exception as e:
                    return msg.error(str(e))
        
        return None
    
    async def _forward_to_plugin(
        self,
        msg: "AdapterMessage",
        route: "RouteRule",
    ) -> Optional["AdapterResponse"]:
        """转发消息到插件"""
        if not route.plugin_id or not route.entry:
            self.logger.warning("Route missing plugin_id or entry: {}", route)
            return msg.error("Invalid route configuration")
        
        try:
            result = await self.forward_to_plugin(
                route.plugin_id,
                route.entry,
                {"message": msg, "payload": msg.payload},
            )
            return msg.reply(result)
        except Exception as e:
            self.logger.exception("Failed to forward message to plugin")
            return msg.error(str(e))
