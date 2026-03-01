"""MCP 插件调用器。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable, Coroutine

from plugin.sdk.adapter.gateway_contracts import LoggerLike
from plugin.sdk.adapter.gateway_models import (
    GatewayError,
    GatewayErrorException,
    GatewayRequest,
    RouteDecision,
    RouteMode,
)

if TYPE_CHECKING:
    from plugin.plugins.mcp_adapter import MCPClient


class MCPPluginInvoker:
    """
    MCP 插件调用器。
    
    根据路由决策调用目标：
    - SELF: 调用本地 MCP tool
    - PLUGIN: 调用 NEKO 插件 entry
    - DROP: 抛出错误
    """

    def __init__(
        self,
        mcp_clients: dict[str, "MCPClient"],
        plugin_call_fn: Callable[[str, str, dict[str, object]], object] | None,
        logger: LoggerLike,
    ):
        """
        初始化调用器。
        
        Args:
            mcp_clients: MCP 客户端映射 {server_name: client}
            plugin_call_fn: NEKO 插件调用函数 (plugin_id, entry_id, params) -> result
            logger: 日志记录器
        """
        self._mcp_clients = mcp_clients
        self._plugin_call_fn = plugin_call_fn
        self._logger = logger

    async def invoke(
        self,
        request: GatewayRequest,
        decision: RouteDecision,
    ) -> object:
        """
        执行调用。
        
        Args:
            request: Gateway 请求
            decision: 路由决策
            
        Returns:
            调用结果
        """
        if decision.mode == RouteMode.DROP:
            raise GatewayErrorException(
                GatewayError(
                    code="ROUTE_NOT_FOUND",
                    message="route decision is drop",
                    details={
                        "request_id": request.request_id,
                        "reason": decision.reason,
                    },
                    retryable=False,
                )
            )

        if decision.mode == RouteMode.SELF:
            return await self._invoke_mcp_tool(request, decision)

        if decision.mode == RouteMode.PLUGIN:
            return await self._invoke_neko_plugin(request, decision)

        if decision.mode == RouteMode.BROADCAST:
            # 暂不支持广播模式
            raise GatewayErrorException(
                GatewayError(
                    code="UNSUPPORTED_ROUTE_MODE",
                    message="broadcast mode not supported yet",
                    details={"mode": decision.mode.value},
                    retryable=False,
                )
            )

        raise GatewayErrorException(
            GatewayError(
                code="UNKNOWN_ROUTE_MODE",
                message=f"unknown route mode: {decision.mode}",
                details={"mode": str(decision.mode)},
                retryable=False,
            )
        )

    async def _invoke_mcp_tool(
        self,
        request: GatewayRequest,
        decision: RouteDecision,
    ) -> object:
        """调用 MCP tool。"""
        entry_id = decision.entry_id or request.target_entry_id
        if entry_id is None:
            raise GatewayErrorException(
                GatewayError(
                    code="MCP_MISSING_TOOL_NAME",
                    message="tool name is required for MCP call",
                    details={"request_id": request.request_id},
                    retryable=False,
                )
            )

        # 查找包含该 tool 的 MCP client
        target_client: MCPClient | None = None
        for client in self._mcp_clients.values():
            for tool in client.tools:
                if tool.name == entry_id:
                    target_client = client
                    break
            if target_client:
                break

        if target_client is None:
            raise GatewayErrorException(
                GatewayError(
                    code="MCP_TOOL_NOT_FOUND",
                    message=f"MCP tool '{entry_id}' not found in any connected server",
                    details={"tool_name": entry_id},
                    retryable=False,
                )
            )

        self._logger.debug(
            "Invoking MCP tool '{}' on server '{}', request_id={}",
            entry_id,
            target_client.config.name,
            request.request_id,
        )

        # 调用 MCP tool
        result = await target_client.call_tool(
            entry_id,
            dict(request.params),
            timeout=request.timeout_s,
        )

        if "error" in result:
            raise GatewayErrorException(
                GatewayError(
                    code="MCP_TOOL_ERROR",
                    message=str(result["error"]),
                    details={"tool_name": entry_id, "server": target_client.config.name},
                    retryable=True,
                )
            )

        return result.get("result", {})

    async def _invoke_neko_plugin(
        self,
        request: GatewayRequest,
        decision: RouteDecision,
    ) -> object:
        """调用 NEKO 插件 entry。"""
        plugin_id = decision.plugin_id
        entry_id = decision.entry_id or request.target_entry_id

        if plugin_id is None or entry_id is None:
            raise GatewayErrorException(
                GatewayError(
                    code="INVALID_ROUTE_DECISION",
                    message="plugin_id and entry_id are required for PLUGIN mode",
                    details={
                        "plugin_id": plugin_id,
                        "entry_id": entry_id,
                    },
                    retryable=False,
                )
            )

        if self._plugin_call_fn is None:
            raise GatewayErrorException(
                GatewayError(
                    code="PLUGIN_CALL_NOT_CONFIGURED",
                    message="plugin call function not configured",
                    details={"plugin_id": plugin_id, "entry_id": entry_id},
                    retryable=False,
                )
            )

        self._logger.debug(
            "Invoking NEKO plugin '{}' entry '{}', request_id={}",
            plugin_id,
            entry_id,
            request.request_id,
        )

        try:
            result = self._plugin_call_fn(plugin_id, entry_id, dict(request.params))
            # 如果是协程，等待它
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as exc:
            raise GatewayErrorException(
                GatewayError(
                    code="PLUGIN_CALL_ERROR",
                    message=str(exc),
                    details={
                        "plugin_id": plugin_id,
                        "entry_id": entry_id,
                        "error_type": type(exc).__name__,
                    },
                    retryable=True,
                )
            ) from exc
