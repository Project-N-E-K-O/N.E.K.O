"""MCP 请求规范化器。"""

from __future__ import annotations

from plugin.sdk.adapter.gateway_models import (
    ExternalEnvelope,
    GatewayAction,
    GatewayError,
    GatewayErrorException,
    GatewayRequest,
)


class MCPRequestNormalizer:
    """
    MCP 请求规范化器。
    
    将 MCP JSON-RPC 请求转换为 GatewayRequest。
    """

    async def normalize(self, env: ExternalEnvelope) -> GatewayRequest:
        """
        将 ExternalEnvelope 转换为 GatewayRequest。
        
        MCP 请求格式：
        - action: "tool_call" | "resource_read" | "prompt_get"
        - payload: {"name": "tool_name", "arguments": {...}}
        """
        action = self._parse_action(env.action)
        
        # 提取 MCP 特定字段
        payload = env.payload
        tool_name = self._extract_string(payload, "name")
        arguments = self._extract_dict(payload, "arguments")
        
        # tool_call 和 resource_read 必须有 name
        if action in (GatewayAction.TOOL_CALL, GatewayAction.RESOURCE_READ):
            if tool_name is None:
                raise GatewayErrorException(
                    GatewayError(
                        code="MCP_INVALID_REQUEST",
                        message="'name' field is required for tool_call/resource_read",
                        details={"action": env.action},
                        retryable=False,
                    )
                )
        
        # 提取可选字段
        trace_id = self._extract_string(payload, "id") or env.request_id
        timeout_raw = payload.get("timeout")
        timeout_s = 60.0
        if isinstance(timeout_raw, (int, float)):
            timeout_s = float(timeout_raw)
        
        return GatewayRequest(
            request_id=env.request_id,
            protocol="mcp",
            action=action,
            source_app=env.connection_id,
            trace_id=trace_id,
            params=arguments,
            target_plugin_id=None,  # MCP 模式下由路由器决定
            target_entry_id=tool_name,
            timeout_s=timeout_s,
            metadata=env.metadata,
        )

    def _parse_action(self, raw_action: str) -> GatewayAction:
        """解析 MCP action 到 GatewayAction。"""
        action_map: dict[str, GatewayAction] = {
            "tool_call": GatewayAction.TOOL_CALL,
            "tools/call": GatewayAction.TOOL_CALL,
            "resource_read": GatewayAction.RESOURCE_READ,
            "resources/read": GatewayAction.RESOURCE_READ,
            "event_push": GatewayAction.EVENT_PUSH,
            "prompt_get": GatewayAction.RESOURCE_READ,
            "prompts/get": GatewayAction.RESOURCE_READ,
        }
        
        action = action_map.get(raw_action)
        if action is None:
            raise GatewayErrorException(
                GatewayError(
                    code="MCP_UNSUPPORTED_ACTION",
                    message=f"unsupported MCP action: {raw_action}",
                    details={"action": raw_action},
                    retryable=False,
                )
            )
        return action

    def _extract_string(self, payload: dict[str, object], key: str) -> str | None:
        """从 payload 中提取字符串字段。"""
        value = payload.get(key)
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else None
        raise GatewayErrorException(
            GatewayError(
                code="MCP_INVALID_FIELD",
                message=f"field '{key}' must be string",
                details={"field": key, "actual_type": type(value).__name__},
                retryable=False,
            )
        )

    def _extract_dict(self, payload: dict[str, object], key: str) -> dict[str, object]:
        """从 payload 中提取字典字段。"""
        value = payload.get(key)
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        raise GatewayErrorException(
            GatewayError(
                code="MCP_INVALID_FIELD",
                message=f"field '{key}' must be object",
                details={"field": key, "actual_type": type(value).__name__},
                retryable=False,
            )
        )
