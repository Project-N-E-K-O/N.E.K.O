"""MCP 插件调用器。"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any, Callable

from plugin.sdk.adapter import Err, Ok, Result, TransportError
from plugin.sdk.adapter.gateway_contracts import LoggerLike
from plugin.sdk.adapter.gateway_models import (
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
        plugin_call_fn: Callable[..., object] | None,
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

    def _call_plugin_fn(
        self,
        plugin_id: str,
        entry_id: str,
        params: dict[str, object],
        timeout_s: float,
    ) -> Any:
        """
        调用注入的插件调用函数，兼容旧签名：
        - 新签名: fn(plugin_id, entry_id, params, timeout_s)
        - 旧签名: fn(plugin_id, entry_id, params)
        """
        fn = self._plugin_call_fn
        if fn is None:
            raise RuntimeError("plugin call function not configured")

        try:
            sig = inspect.signature(fn)
            params_meta = list(sig.parameters.values())
            has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params_meta)
            positional_or_kw = [
                p for p in params_meta
                if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
        except Exception:
            # 反射失败时回退旧签名，避免影响现有调用
            has_varargs = False
            positional_or_kw = []

        if has_varargs or len(positional_or_kw) >= 4:
            return fn(plugin_id, entry_id, params, timeout_s)

        return fn(plugin_id, entry_id, params)

    def _resolve_tool_identity(self, entry_id: str) -> tuple[str | None, str]:
        """
        解析 tool 标识：
        - canonical: mcp_{server_name}_{tool_name} -> (server_name, tool_name)
        - raw: tool_name -> (None, tool_name)
        """
        if entry_id.startswith("mcp_"):
            # 按已连接 server 前缀匹配，避免 server_name 含 "_" 时 split 误判
            for server_name in sorted(self._mcp_clients.keys(), key=len, reverse=True):
                prefix = f"mcp_{server_name}_"
                if entry_id.startswith(prefix):
                    tool_name = entry_id[len(prefix):]
                    if tool_name:
                        return server_name, tool_name
        return None, entry_id

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
            return Err(
                TransportError(
                    "route decision is drop",
                    op_name="mcp.invoker.invoke",
                    code="ROUTE_NOT_FOUND",
                )
            )

        if decision.mode == RouteMode.SELF:
            return await self._invoke_mcp_tool(request, decision)

        if decision.mode == RouteMode.PLUGIN:
            return await self._invoke_neko_plugin(request, decision)

        if decision.mode == RouteMode.BROADCAST:
            # 暂不支持广播模式
            return Err(
                TransportError(
                    "broadcast mode not supported yet",
                    op_name="mcp.invoker.invoke",
                    code="UNSUPPORTED_ROUTE_MODE",
                )
            )

        return Err(
            TransportError(
                f"unknown route mode: {decision.mode}",
                op_name="mcp.invoker.invoke",
                code="UNKNOWN_ROUTE_MODE",
            )
        )

    async def _invoke_mcp_tool(
        self,
        request: GatewayRequest,
        decision: RouteDecision,
    ) -> Result[object, TransportError]:
        """调用 MCP tool。"""
        entry_id = decision.entry_id or request.target_entry_id
        if entry_id is None:
            return Err(
                TransportError(
                    "tool name is required for MCP call",
                    op_name="mcp.invoker.invoke_mcp_tool",
                    code="MCP_MISSING_TOOL_NAME",
                )
            )

        server_hint, tool_name = self._resolve_tool_identity(entry_id)
        target_client: MCPClient | None = None

        if server_hint is not None:
            target_client = self._mcp_clients.get(server_hint)
            if target_client is None:
                return Err(
                    TransportError(
                        f"MCP server '{server_hint}' not connected",
                        op_name="mcp.invoker.invoke_mcp_tool",
                        code="MCP_SERVER_NOT_CONNECTED",
                    )
                )
        else:
            # raw tool_name: 在所有已连接 server 中查找
            candidates: list[MCPClient] = []
            for client in self._mcp_clients.values():
                for tool in client.tools:
                    if tool.name == tool_name:
                        candidates.append(client)
                        break

            if len(candidates) == 1:
                target_client = candidates[0]
            elif len(candidates) > 1:
                servers = [client.config.name for client in candidates]
                return Err(
                    TransportError(
                        f"MCP tool '{tool_name}' exists on multiple servers: {servers}",
                        op_name="mcp.invoker.invoke_mcp_tool",
                        code="MCP_TOOL_AMBIGUOUS",
                    )
                )

        if target_client is None:
            return Err(
                TransportError(
                    f"MCP tool '{tool_name}' not found in any connected server",
                    op_name="mcp.invoker.invoke_mcp_tool",
                    code="MCP_TOOL_NOT_FOUND",
                )
            )

        self._logger.debug(
            "Invoking MCP tool '{}' on server '{}', request_id={}",
            tool_name,
            target_client.config.name,
            request.request_id,
        )

        # 调用 MCP tool
        result = await target_client.call_tool(
            tool_name,
            dict(request.params),
            timeout=request.timeout_s,
        )

        if "error" in result:
            return Err(
                TransportError(
                    str(result["error"]),
                    op_name="mcp.invoker.invoke_mcp_tool",
                    code="MCP_TOOL_ERROR",
                )
            )

        return Ok(result.get("result", {}))

    async def _invoke_neko_plugin(
        self,
        request: GatewayRequest,
        decision: RouteDecision,
    ) -> Result[object, TransportError]:
        """调用 NEKO 插件 entry。"""
        plugin_id = decision.plugin_id
        entry_id = decision.entry_id or request.target_entry_id

        if plugin_id is None or entry_id is None:
            return Err(
                TransportError(
                    "plugin_id and entry_id are required for PLUGIN mode",
                    op_name="mcp.invoker.invoke_neko_plugin",
                    code="INVALID_ROUTE_DECISION",
                )
            )

        if self._plugin_call_fn is None:
            return Err(
                TransportError(
                    "plugin call function not configured",
                    op_name="mcp.invoker.invoke_neko_plugin",
                    code="PLUGIN_CALL_NOT_CONFIGURED",
                )
            )

        self._logger.debug(
            "Invoking NEKO plugin '{}' entry '{}', request_id={}",
            plugin_id,
            entry_id,
            request.request_id,
        )

        try:
            result = self._call_plugin_fn(
                plugin_id,
                entry_id,
                dict(request.params),
                float(request.timeout_s),
            )
            # 如果是协程，等待它
            if asyncio.iscoroutine(result):
                result = await result
            return Ok(result)
        except Exception as exc:
            return Err(
                TransportError(
                    str(exc),
                    op_name="mcp.invoker.invoke_neko_plugin",
                    plugin_id=plugin_id,
                    entry_ref=f"{plugin_id}:{entry_id}",
                    code="PLUGIN_CALL_ERROR",
                )
            )
