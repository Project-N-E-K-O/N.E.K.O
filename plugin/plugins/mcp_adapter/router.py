"""MCP routing engine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Awaitable, Optional

from plugin.sdk.adapter import Ok, Result, TransportError
from plugin.sdk.adapter.gateway_contracts import LoggerLike
from plugin.sdk.adapter.gateway_models import (
    GatewayAction,
    GatewayRequest,
    RouteDecision,
    RouteMode,
)

if TYPE_CHECKING:
    from plugin.plugins.mcp_adapter import MCPClient

# Tool registration callback type: (tool_id, display_name, description, schema, server_name, tool_name)
ToolRegisterCallback = Callable[[str, str, str, Optional[dict], str, str], Awaitable[bool]]
ToolUnregisterCallback = Callable[[str], Awaitable[bool]]


class MCPRouteEngine:
    """
    MCP routing engine.
    
    Selects the routing target based on the request:
    - If target_entry_id matches a registered MCP tool, route to SELF
    - If both target_plugin_id and target_entry_id are present, route to PLUGIN
    - Otherwise, DROP
    """

    def __init__(
        self,
        mcp_clients: dict[str, "MCPClient"],
        logger: LoggerLike,
        on_tool_register: Optional[ToolRegisterCallback] = None,
        on_tool_unregister: Optional[ToolUnregisterCallback] = None,
    ):
        """
        Initialize the routing engine.
        
        Args:
            mcp_clients: MCP client mapping {server_name: client}.
            logger: Logger.
            on_tool_register: Tool registration callback (used to notify the frontend)
            on_tool_unregister: Tool unregistration callback (used to notify the frontend)
        """
        self._mcp_clients = mcp_clients
        self._logger = logger
        self._on_tool_register = on_tool_register
        self._on_tool_unregister = on_tool_unregister
        # Cache the tool name -> server name mapping
        self._tool_index: dict[str, str] = {}
        # Cache tool details {tool_id: {name, description, schema}}
        self._tool_details: dict[str, dict] = {}

    def rebuild_tool_index(self) -> None:
        """Rebuild the tool index (synchronous version; does not trigger callbacks)."""
        self._tool_index.clear()
        self._tool_details.clear()
        for server_name, client in self._mcp_clients.items():
            for tool in client.tools:
                tool_id = f"mcp_{server_name}_{tool.name}"
                if tool_id in self._tool_index:
                    self._logger.error(
                        "Duplicate MCP tool_id detected: {} (server='{}', tool='{}'), skip registration",
                        tool_id,
                        server_name,
                        tool.name,
                    )
                    continue
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
        Register all tools from a server and trigger callbacks.
        
        Args:
            server_name: Server name
            client: MCP client
            
        Returns:
            Number of registered tools
        """
        count = 0
        for tool in client.tools:
            tool_id = f"mcp_{server_name}_{tool.name}"
            if tool_id in self._tool_index:
                self._logger.error(
                    "Duplicate MCP tool_id detected: {} (server='{}', tool='{}'), skip registration",
                    tool_id,
                    server_name,
                    tool.name,
                )
                continue
            tool_details = {
                "name": tool.name,
                "description": tool.description,
                "schema": tool.input_schema,
                "server": server_name,
            }

            callback_ok = True
            if self._on_tool_register:
                try:
                    callback_result = await self._on_tool_register(
                        tool_id,
                        f"[{server_name}] {tool.name}",
                        tool.description or f"MCP tool from {server_name}",
                        tool.input_schema,
                        server_name,
                        tool.name,
                    )
                    callback_ok = callback_result is not False
                except Exception:
                    callback_ok = False
                    self._logger.exception(
                        "Tool register callback failed: tool_id={}, server='{}'",
                        tool_id,
                        server_name,
                    )
            if not callback_ok:
                self._logger.warning(
                    "Tool register callback rejected MCP tool '{}', server='{}'",
                    tool.name,
                    server_name,
                )
                continue

            self._tool_index[tool_id] = server_name
            self._tool_details[tool_id] = tool_details
            count += 1
        
        self._logger.info(
            "Registered {} tools from MCP server '{}'",
            count,
            server_name,
        )
        return count
    
    async def unregister_server_tools(self, server_name: str) -> int:
        """
        Unregister all tools from a server and trigger callbacks.
        
        Args:
            server_name: Server name
            
        Returns:
            Number of unregistered tools
        """
        tools_to_remove = [
            tool_id for tool_id, srv in self._tool_index.items()
            if srv == server_name
        ]
        
        removed_count = 0
        for tool_id in tools_to_remove:
            callback_ok = True
            if self._on_tool_unregister:
                try:
                    callback_result = await self._on_tool_unregister(tool_id)
                    callback_ok = callback_result is not False
                except Exception:
                    callback_ok = False
                    self._logger.exception(
                        "Tool unregister callback failed: tool_id={}, server='{}'",
                        tool_id,
                        server_name,
                    )
            if not callback_ok:
                self._logger.warning(
                    "Tool unregister callback rejected MCP tool '{}' from server '{}'",
                    tool_id,
                    server_name,
                )
                continue

            del self._tool_index[tool_id]
            self._tool_details.pop(tool_id, None)
            removed_count += 1
        
        if removed_count > 0:
            self._logger.info(
                "Unregistered {} tools from MCP server '{}'",
                removed_count,
                server_name,
            )
        
        return removed_count

    async def decide(self, request: GatewayRequest) -> Result[RouteDecision, TransportError]:
        """
        Decide how to route a request.
        
        Args:
            request: Gateway request.
            
        Returns:
            Routing decision
        """
        # If both plugin_id and entry_id are specified explicitly, route to PLUGIN
        if request.target_plugin_id is not None and request.target_entry_id is not None:
            return Ok(RouteDecision(
                mode=RouteMode.PLUGIN,
                plugin_id=request.target_plugin_id,
                entry_id=request.target_entry_id,
                reason="explicit plugin target",
            ))

        # If only entry_id is present, check whether it is an MCP tool
        if request.target_entry_id is not None:
            if request.target_entry_id in self._tool_index:
                return Ok(RouteDecision(
                    mode=RouteMode.SELF,
                    entry_id=request.target_entry_id,
                    reason=f"MCP tool on server '{self._tool_index[request.target_entry_id]}'",
                ))

            # For a TOOL_CALL action, return DROP if the tool cannot be found
            if request.action == GatewayAction.TOOL_CALL:
                return Ok(RouteDecision(
                    mode=RouteMode.DROP,
                    reason=f"MCP tool '{request.target_entry_id}' not found",
                ))

        # For EVENT_PUSH, broadcasting could be considered (currently DROP)
        if request.action == GatewayAction.EVENT_PUSH:
            return Ok(RouteDecision(
                mode=RouteMode.DROP,
                reason="event push not routed",
            ))

        # Default: DROP
        return Ok(RouteDecision(
            mode=RouteMode.DROP,
            reason="no route target specified",
        ))

    def get_tool_server(self, tool_name: str) -> str | None:
        """Get the server name that owns a tool."""
        return self._tool_index.get(tool_name)

    def list_tools(self) -> list[tuple[str, str]]:
        """List all tools and their server names."""
        return [(name, server) for name, server in self._tool_index.items()]
