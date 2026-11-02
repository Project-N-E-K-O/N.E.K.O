"""MCP request normalizer."""

from __future__ import annotations

from plugin.sdk.adapter import Err, Ok, Result, TransportError
from plugin.sdk.adapter.gateway_models import (
    ExternalRequest,
    GatewayAction,
    GatewayError,
    GatewayErrorException,
    GatewayRequest,
)


class MCPRequestNormalizer:
    """
    MCP request normalizer.
    
    Convert MCP JSON-RPC requests to GatewayRequest.
    """

    async def normalize(self, env: ExternalRequest) -> Result[GatewayRequest, TransportError]:
        """
        Convert ExternalRequest to GatewayRequest.
        
        MCP request format:
        - action: "tool_call" | "resource_read" | "prompt_get"
        - payload: {"name": "tool_name", "arguments": {...}}
        """
        try:
            action = self._parse_action(env.action)

            # Extract MCP-specific fields.
            payload = env.payload
            tool_name = self._extract_string(payload, "name")
            arguments = self._extract_dict(payload, "arguments")

            # tool_call and resource_read require name.
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

            # Extract optional fields.
            trace_id = self._extract_string(payload, "id") or env.request_id
            if "timeout_s" in payload:
                timeout_raw = payload.get("timeout_s")
                if timeout_raw is None:
                    timeout_raw = payload.get("timeout")
            else:
                timeout_raw = payload.get("timeout")
            timeout_s = 60.0
            if isinstance(timeout_raw, bool):
                raise GatewayErrorException(
                    GatewayError(
                        code="MCP_INVALID_FIELD",
                        message="field 'timeout_s' must be number",
                        details={"field": "timeout_s", "actual_type": type(timeout_raw).__name__},
                        retryable=False,
                    )
                )
            if timeout_raw is not None and not isinstance(timeout_raw, (int, float)):
                raise GatewayErrorException(
                    GatewayError(
                        code="MCP_INVALID_FIELD",
                        message="field 'timeout_s' must be number",
                        details={"field": "timeout_s", "actual_type": type(timeout_raw).__name__},
                        retryable=False,
                    )
                )
            if isinstance(timeout_raw, (int, float)):
                timeout_s = float(timeout_raw)

            target_plugin_id = self._extract_string(payload, "target_plugin_id")

            return Ok(
                GatewayRequest(
                    request_id=env.request_id,
                    protocol="mcp",
                    action=action,
                    source_app=env.connection_id,
                    trace_id=trace_id,
                    params=arguments,
                    target_plugin_id=target_plugin_id,
                    target_entry_id=tool_name,
                    timeout_s=timeout_s,
                    metadata=env.metadata,
                )
            )
        except GatewayErrorException as error:
            return Err(
                TransportError(
                    error.error.message,
                    op_name="mcp.normalizer.normalize",
                    code=error.error.code,
                )
            )
        except Exception as error:
            return Err(TransportError(str(error), op_name="mcp.normalizer.normalize"))

    def _parse_action(self, raw_action: str) -> GatewayAction:
        """Parse an MCP action into GatewayAction."""
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
        """Extract a string field from the payload."""
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
        """Extract a dictionary field from the payload."""
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
