"""MCP 插件调用器。"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Callable

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
    ) -> object:
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
            if has_varargs or len(positional_or_kw) >= 4:
                return fn(plugin_id, entry_id, params, timeout_s)
        except Exception:
            # 反射失败时回退旧签名，避免影响现有调用
            pass

        return fn(plugin_id, entry_id, params)

    def _resolve_tool_identity(self, entry_id: str) -> tuple[str | None, str]:
        """
        解析 tool 标识：
        - canonical: mcp_{server_name}_{tool_name} -> (server_name, tool_name)
        - raw: tool_name -> (None, tool_name)
        """
        if entry_id.startswith("mcp_"):
            # 按已连接 server 前缀匹配，避免 server_name 含 "_" 时 split 误判
            for server_name in self._mcp_clients.keys():
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

        server_hint, tool_name = self._resolve_tool_identity(entry_id)
        target_client: MCPClient | None = None

        if server_hint is not None:
            target_client = self._mcp_clients.get(server_hint)
            if target_client is None:
                raise GatewayErrorException(
                    GatewayError(
                        code="MCP_SERVER_NOT_CONNECTED",
                        message=f"MCP server '{server_hint}' not connected",
                        details={"entry_id": entry_id, "server": server_hint},
                        retryable=True,
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
                raise GatewayErrorException(
                    GatewayError(
                        code="MCP_TOOL_AMBIGUOUS",
                        message=f"MCP tool '{tool_name}' exists on multiple servers",
                        details={"tool_name": tool_name, "servers": servers},
                        retryable=False,
                    )
                )

        if target_client is None:
            raise GatewayErrorException(
                GatewayError(
                    code="MCP_TOOL_NOT_FOUND",
                    message=f"MCP tool '{tool_name}' not found in any connected server",
                    details={"tool_name": tool_name, "entry_id": entry_id},
                    retryable=False,
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
            raise GatewayErrorException(
                GatewayError(
                    code="MCP_TOOL_ERROR",
                    message=str(result["error"]),
                    details={"tool_name": tool_name, "server": target_client.config.name},
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
            result = self._call_plugin_fn(
                plugin_id,
                entry_id,
                dict(request.params),
                float(request.timeout_s),
            )
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
