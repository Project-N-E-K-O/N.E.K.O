"""
NekoAdapterPlugin 基类

结合 NekoPluginBase 和 AdapterBase 的能力，为 Adapter 类型插件提供统一的基类。
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.adapter.base import AdapterConfig, AdapterContext, AdapterMode

if TYPE_CHECKING:
    from plugin.sdk.adapter.types import AdapterMessage, AdapterResponse, RouteRule


class NekoAdapterPlugin(NekoPluginBase):
    """
    NEKO Adapter 插件基类
    
    结合 NekoPluginBase 的插件能力和 AdapterBase 的 Adapter 能力。
    
    使用方式：
    ```python
    from plugin.sdk.adapter import NekoAdapterPlugin
    from plugin.sdk import neko_plugin, lifecycle
    
    @neko_plugin
    class MyAdapter(NekoAdapterPlugin):
        @lifecycle(id="startup")
        async def on_startup(self):
            await self.adapter_startup()
            # 自定义启动逻辑
        
        @lifecycle(id="shutdown")
        async def on_shutdown(self):
            await self.adapter_shutdown()
            # 自定义关闭逻辑
    ```
    """
    
    __freezable__: List[str] = []
    
    def __init__(self, ctx):
        super().__init__(ctx)
        
        # Adapter 特有属性
        self._adapter_config: Optional[AdapterConfig] = None
        self._adapter_context: Optional[AdapterContext] = None
        self._adapter_routes: List["RouteRule"] = []
        self._adapter_tools: Dict[str, Callable[..., object]] = {}
        self._adapter_resources: Dict[str, Callable[..., object]] = {}
        self._adapter_running = False
    
    @property
    def adapter_config(self) -> AdapterConfig:
        """获取 Adapter 配置"""
        if self._adapter_config is None:
            raise RuntimeError("Adapter config not initialized. Call adapter_startup() first.")
        return self._adapter_config
    
    @property
    def adapter_context(self) -> AdapterContext:
        """获取 Adapter 上下文"""
        if self._adapter_context is None:
            raise RuntimeError("Adapter context not initialized. Call adapter_startup() first.")
        return self._adapter_context
    
    @property
    def adapter_mode(self) -> AdapterMode:
        """获取 Adapter 工作模式"""
        return self.adapter_config.mode
    
    @property
    def adapter_id(self) -> str:
        """获取 Adapter ID（等同于 plugin_id）"""
        return self.ctx.plugin_id
    
    async def adapter_startup(self) -> None:
        """
        Adapter 启动初始化
        
        在 @lifecycle(id="startup") 方法中调用此方法。
        """
        # 加载 adapter 配置
        config = await self.config.dump()
        adapter_conf = config.get("adapter", {})
        
        self._adapter_config = AdapterConfig.from_dict(adapter_conf)
        self._adapter_context = AdapterContext(
            adapter_id=self.ctx.plugin_id,
            config=self._adapter_config,
            logger=self.ctx.logger,
            plugin_ctx=self.ctx,
        )
        self._adapter_running = True
        
        self.ctx.logger.info(
            "Adapter '{}' initialized with mode={}",
            self.adapter_id,
            self._adapter_config.mode.value,
        )
    
    async def adapter_shutdown(self) -> None:
        """
        Adapter 关闭清理
        
        在 @lifecycle(id="shutdown") 方法中调用此方法。
        """
        self._adapter_running = False
        self._adapter_tools.clear()
        self._adapter_resources.clear()
        self._adapter_routes.clear()
        
        self.ctx.logger.info("Adapter '{}' shutdown", self.adapter_id)
    
    # ========== 工具注册 API ==========
    
    def register_adapter_tool(
        self,
        name: str,
        handler: Callable[..., object],
        schema: Optional[Dict[str, object]] = None,
    ) -> None:
        """
        注册一个 Adapter 工具（仅内部使用，不会出现在前端管理面板）
        
        Args:
            name: 工具名称
            handler: 处理函数
            schema: JSON Schema 描述
        """
        self._adapter_tools[name] = handler
        self.ctx.logger.debug("Registered adapter tool: {}", name)
    
    async def register_adapter_tool_as_entry(
        self,
        name: str,
        handler: Callable[..., object],
        display_name: str = "",
        description: str = "",
        schema: Optional[Dict[str, object]] = None,
    ) -> bool:
        """
        注册一个 Adapter 工具并作为动态 entry 暴露
        
        这个方法会同时：
        1. 将工具注册到 _adapter_tools（供 Gateway Core 使用）
        2. 将工具注册为动态 entry（出现在前端管理面板）
        
        Args:
            name: 工具名称（也是 entry_id）
            handler: 处理函数
            display_name: 显示名称
            description: 描述信息
            schema: JSON Schema 描述
        
        Returns:
            True 如果注册成功
        """
        # 注册到 adapter tools
        self._adapter_tools[name] = handler
        
        # 同时注册为动态 entry
        return await self.register_dynamic_entry(
            entry_id=name,
            handler=handler,
            name=display_name or name,
            description=description,
            input_schema=schema,
            kind="action",
        )
    
    async def unregister_adapter_tool_entry(self, name: str) -> bool:
        """
        注销一个 Adapter 工具及其对应的动态 entry
        
        Args:
            name: 工具名称
        
        Returns:
            True 如果注销成功
        """
        # 从 adapter tools 中移除
        if name in self._adapter_tools:
            del self._adapter_tools[name]
        
        # 注销动态 entry
        return await self.unregister_dynamic_entry(name)
    
    def register_adapter_resource(
        self,
        uri: str,
        handler: Callable[..., object],
    ) -> None:
        """
        注册一个 Adapter 资源
        
        Args:
            uri: 资源 URI
            handler: 处理函数
        """
        self._adapter_resources[uri] = handler
        self.ctx.logger.debug("Registered adapter resource: {}", uri)
    
    def get_adapter_tool(self, name: str) -> Optional[Callable[..., object]]:
        """获取工具处理函数"""
        return self._adapter_tools.get(name)
    
    def get_adapter_resource(self, uri: str) -> Optional[Callable[..., object]]:
        """获取资源处理函数"""
        return self._adapter_resources.get(uri)
    
    def list_adapter_tools(self) -> List[str]:
        """列出所有注册的工具"""
        return list(self._adapter_tools.keys())
    
    def list_adapter_resources(self) -> List[str]:
        """列出所有注册的资源"""
        return list(self._adapter_resources.keys())
    
    # ========== 路由管理 ==========
    
    def add_adapter_route(self, route: "RouteRule") -> None:
        """添加路由规则"""
        self._adapter_routes.append(route)
        # 按优先级排序（高优先级在前）
        self._adapter_routes.sort(key=lambda r: -r.priority)
    
    def find_matching_route(self, msg: "AdapterMessage") -> Optional["RouteRule"]:
        """查找匹配的路由规则"""
        for route in self._adapter_routes:
            if route.matches(msg):
                return route
        return None
    
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
        return await self.ctx.trigger_plugin_event_async(
            target_plugin_id=plugin_id,
            event_type="adapter_call",
            event_id=entry,
            params=dict(payload),
            timeout=timeout,
        )
    
    # ========== 消息处理 ==========
    
    async def handle_adapter_message(
        self,
        msg: "AdapterMessage",
    ) -> Optional["AdapterResponse"]:
        """
        处理 Adapter 消息
        
        默认实现根据路由规则分发消息。子类可以重写此方法。
        
        Args:
            msg: 收到的消息
        
        Returns:
            响应消息，或 None 表示不响应
        """
        from plugin.sdk.adapter.types import RouteTarget
        
        # 查找匹配的路由规则
        route = self.find_matching_route(msg)
        
        if route is None:
            self.ctx.logger.debug("No matching route for message: {}", msg.id)
            return None
        
        if route.target == RouteTarget.DROP:
            return None
        
        if route.target == RouteTarget.SELF:
            return await self._handle_locally(msg)
        
        if route.target == RouteTarget.PLUGIN:
            return await self._forward_to_plugin(msg, route)
        
        return None
    
    async def _handle_locally(
        self,
        msg: "AdapterMessage",
    ) -> Optional["AdapterResponse"]:
        """本地处理消息"""
        import asyncio
        
        # 检查是否有对应的工具
        if msg.action == "tool_call":
            tool_name = msg.payload.get("name") if isinstance(msg.payload, dict) else None
            if tool_name and tool_name in self._adapter_tools:
                handler = self._adapter_tools[tool_name]
                args = msg.payload.get("arguments", {}) if isinstance(msg.payload, dict) else {}
                if isinstance(args, dict) and callable(handler):
                    result = handler(**args)
                    # 如果是协程，等待它
                    if asyncio.iscoroutine(result):
                        result = await result
                    return msg.reply(result)
        
        return None
    
    async def _forward_to_plugin(
        self,
        msg: "AdapterMessage",
        route: "RouteRule",
    ) -> Optional["AdapterResponse"]:
        """转发消息到插件"""
        if not route.plugin_id or not route.entry:
            self.ctx.logger.warning("Route missing plugin_id or entry: {}", route)
            return msg.error("Invalid route configuration")
        
        result = await self.forward_to_plugin(
            route.plugin_id,
            route.entry,
            {"message": msg, "payload": msg.payload},
        )
        return msg.reply(result)
