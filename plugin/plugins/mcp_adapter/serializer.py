"""MCP 响应序列化器。"""

from __future__ import annotations

from plugin.sdk.adapter.gateway_models import (
    GatewayError,
    GatewayRequest,
    GatewayResponse,
)


class MCPResponseSerializer:
    """
    MCP 响应序列化器。
    
    将 Gateway 响应转换为 MCP JSON-RPC 格式。
    """

    async def ok(
        self,
        request: GatewayRequest,
        result: object,
        latency_ms: float,
    ) -> GatewayResponse:
        """
        构造成功响应。
        
        MCP 成功响应格式：
        {
            "jsonrpc": "2.0",
            "id": <request_id>,
            "result": {
                "content": [{"type": "text", "text": "..."}],
                "isError": false
            }
        }
        """
        # 将结果包装为 MCP content 格式
        mcp_result = self._wrap_result(result)
        
        return GatewayResponse(
            request_id=request.request_id,
            success=True,
            data=mcp_result,
            latency_ms=latency_ms,
            metadata={
                "trace_id": request.trace_id,
                "protocol": "mcp",
                "action": request.action.value,
            },
        )

    async def fail(
        self,
        request: GatewayRequest,
        error: GatewayError,
        latency_ms: float,
    ) -> GatewayResponse:
        """
        构造错误响应。
        
        MCP 错误响应格式：
        {
            "jsonrpc": "2.0",
            "id": <request_id>,
            "error": {
                "code": -32000,
                "message": "...",
                "data": {...}
            }
        }
        """
        # 映射错误码到 JSON-RPC 错误码
        jsonrpc_code = self._map_error_code(error.code)
        
        mcp_error = GatewayError(
            code=str(jsonrpc_code),
            message=error.message,
            details={
                "original_code": error.code,
                "data": error.details,
                "retryable": error.retryable,
            },
            retryable=error.retryable,
        )
        
        return GatewayResponse(
            request_id=request.request_id,
            success=False,
            error=mcp_error,
            latency_ms=latency_ms,
            metadata={
                "trace_id": request.trace_id,
                "protocol": "mcp",
                "action": request.action.value if hasattr(request, "action") else "unknown",
            },
        )

    def _wrap_result(self, result: object) -> dict[str, object]:
        """
        将结果包装为 MCP content 格式。
        
        MCP tool 结果格式：
        {
            "content": [
                {"type": "text", "text": "..."}
            ],
            "isError": false
        }
        """
        if isinstance(result, dict):
            # 如果已经是 MCP 格式，直接返回
            if "content" in result and isinstance(result.get("content"), list):
                return result
            
            # 检查是否是 NEKO ok/fail 格式
            if "success" in result and "data" in result:
                inner_data = result.get("data")
                if isinstance(inner_data, dict) and "content" in inner_data:
                    return inner_data
                # 将 data 转为文本
                import json
                text = json.dumps(inner_data, ensure_ascii=False, indent=2)
                return {
                    "content": [{"type": "text", "text": text}],
                    "isError": not result.get("success", True),
                }
            
            # 普通字典，转为 JSON 文本
            import json
            text = json.dumps(result, ensure_ascii=False, indent=2)
            return {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            }
        
        if isinstance(result, str):
            return {
                "content": [{"type": "text", "text": result}],
                "isError": False,
            }
        
        if isinstance(result, (list, tuple)):
            import json
            text = json.dumps(list(result), ensure_ascii=False, indent=2)
            return {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            }
        
        # 其他类型，转为字符串
        return {
            "content": [{"type": "text", "text": str(result)}],
            "isError": False,
        }

    def _map_error_code(self, code: str) -> int:
        """
        映射 Gateway 错误码到 JSON-RPC 错误码。
        
        JSON-RPC 标准错误码：
        - -32700: Parse error
        - -32600: Invalid Request
        - -32601: Method not found
        - -32602: Invalid params
        - -32603: Internal error
        - -32000 to -32099: Server error (reserved)
        """
        code_map: dict[str, int] = {
            "MCP_INVALID_REQUEST": -32600,
            "MCP_UNSUPPORTED_ACTION": -32601,
            "MCP_INVALID_FIELD": -32602,
            "INVALID_ARGUMENT": -32602,
            "ROUTE_NOT_FOUND": -32601,
            "FORBIDDEN": -32600,
            "PAYLOAD_TOO_LARGE": -32600,
            "GATEWAY_INTERNAL_ERROR": -32603,
        }
        return code_map.get(code, -32000)
