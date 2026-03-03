"""MCP 路由引擎。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Awaitable, Optional

from plugin.sdk.adapter.gateway_contracts import LoggerLike
from plugin.sdk.adapter.gateway_models import (
    GatewayAction,
    GatewayRequest,
    RouteDecision,
    RouteMode,
)

if TYPE_CHECKING:
    from plugin.plugins.mcp_adapter import MCPClient, MCPTool

# 工具注册回调类型
ToolRegisterCallback = Callable[[str, str, str, Optional[dict]], Awaitable[bool]]
ToolUnregisterCallback = Callable[[str], Awaitable[bool]]


class MCPRouteEngine:
    """
    MCP 路由引擎。
    
    根据请求决定路由目标：
    - 如果 target_entry_id 匹配已注册的 MCP tool，路由到 SELF
    - 如果 target_plugin_id 和 target_entry_id 都有，路由到 PLUGIN
    - 否则 DROP
    """

    def __init__(
        self,
        mcp_clients: dict[str, "MCPClient"],
        logger: LoggerLike,
        on_tool_register: Optional[ToolRegisterCallback] = None,
        on_tool_unregister: Optional[ToolUnregisterCallback] = None,
    ):
        """
        初始化路由引擎。
        
        Args:
            mcp_clients: MCP 客户端映射 {server_name: client}
            logger: 日志记录器
            on_tool_register: 工具注册回调（用于通知前端）
            on_tool_unregister: 工具注销回调（用于通知前端）
        """
        self._mcp_clients = mcp_clients
        self._logger = logger
        self._on_tool_register = on_tool_register
        self._on_tool_unregister = on_tool_unregister
        # 缓存 tool name -> server name 映射
        self._tool_index: dict[str, str] = {}
        # 缓存 tool 详情 {tool_id: {name, description, schema}}
        self._tool_details: dict[str, dict] = {}

    def rebuild_tool_index(self) -> None:
        """重建 tool 索引（同步版本，不触发回调）。"""
        self._tool_index.clear()
        self._tool_details.clear()
        for server_name, client in self._mcp_clients.items():
            for tool in client.tools:
                tool_id = f"mcp_{server_name}_{tool.name}"
                self._tool_index[tool_id] = server_name
                self._tool_details[tool_id] = {
                    "name": tool.name,
                    "description": tool.description,
                    "schema": tool.input_schema,
                    "server": server_name,
                }
        self._logger.debug(
            "MCP tool index rebuilt: {} tools from {} servers",
            len(self._tool_index),
            len(self._mcp_clients),
        )
    
    async def register_server_tools(self, server_name: str, client: "MCPClient") -> int:
        """
        注册服务器的所有工具并触发回调。
        
        Args:
            server_name: 服务器名称
            client: MCP 客户端
            
        Returns:
            注册的工具数量
        """
        count = 0
        for tool in client.tools:
            tool_id = f"mcp_{server_name}_{tool.name}"
            self._tool_index[tool_id] = server_name
            self._tool_details[tool_id] = {
                "name": tool.name,
                "description": tool.description,
                "schema": tool.input_schema,
                "server": server_name,
            }
            
            # 触发注册回调
            if self._on_tool_register:
                await self._on_tool_register(
                    tool_id,
                    f"[{server_name}] {tool.name}",
                    tool.description or f"MCP tool from {server_name}",
                    tool.input_schema,
                )
            count += 1
        
        self._logger.info(
            "Registered {} tools from MCP server '{}'",
            count,
            server_name,
        )
        return count
    
    async def unregister_server_tools(self, server_name: str) -> int:
        """
        注销服务器的所有工具并触发回调。
        
        Args:
            server_name: 服务器名称
            
        Returns:
            注销的工具数量
        """
        tools_to_remove = [
            tool_id for tool_id, srv in self._tool_index.items()
            if srv == server_name
        ]
        
        for tool_id in tools_to_remove:
            del self._tool_index[tool_id]
            if tool_id in self._tool_details:
                del self._tool_details[tool_id]
            
            # 触发注销回调
            if self._on_tool_unregister:
                await self._on_tool_unregister(tool_id)
        
        if tools_to_remove:
            self._logger.info(
                "Unregistered {} tools from MCP server '{}'",
                len(tools_to_remove),
                server_name,
            )
        
        return len(tools_to_remove)

    async def decide(self, request: GatewayRequest) -> RouteDecision:
        """
        决定请求路由。
        
        Args:
            request: Gateway 请求
            
        Returns:
            路由决策
        """
        # 如果显式指定了 plugin_id 和 entry_id，路由到 PLUGIN
        if request.target_plugin_id is not None and request.target_entry_id is not None:
            return RouteDecision(
                mode=RouteMode.PLUGIN,
                plugin_id=request.target_plugin_id,
                entry_id=request.target_entry_id,
                reason="explicit plugin target",
            )

        # 如果只有 entry_id，检查是否是 MCP tool
        if request.target_entry_id is not None:
            if request.target_entry_id in self._tool_index:
                return RouteDecision(
                    mode=RouteMode.SELF,
                    entry_id=request.target_entry_id,
                    reason=f"MCP tool on server '{self._tool_index[request.target_entry_id]}'",
                )

            # 对于 TOOL_CALL action，如果找不到 tool，返回 DROP
            if request.action == GatewayAction.TOOL_CALL:
                return RouteDecision(
                    mode=RouteMode.DROP,
                    reason=f"MCP tool '{request.target_entry_id}' not found",
                )

        # 对于 EVENT_PUSH，可以考虑广播（暂时 DROP）
        if request.action == GatewayAction.EVENT_PUSH:
            return RouteDecision(
                mode=RouteMode.DROP,
                reason="event push not routed",
            )

        # 默认 DROP
        return RouteDecision(
            mode=RouteMode.DROP,
            reason="no route target specified",
        )

    def get_tool_server(self, tool_name: str) -> str | None:
        """获取 tool 所在的 server 名称。"""
        return self._tool_index.get(tool_name)

    def list_tools(self) -> list[tuple[str, str]]:
        """列出所有 tool 及其所在 server。"""
        return [(name, server) for name, server in self._tool_index.items()]
