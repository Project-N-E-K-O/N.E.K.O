"""Gateway Core 默认组件实现。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from loguru import logger

from plugin.sdk.adapter.gateway_contracts import LoggerLike
from plugin.sdk.adapter.gateway_models import (
    ExternalEnvelope,
    GatewayAction,
    GatewayError,
    GatewayErrorException,
    GatewayRequest,
    GatewayResponse,
    RouteDecision,
    RouteMode,
)


def _to_gateway_action(raw_action: str) -> GatewayAction:
    action_map: dict[str, GatewayAction] = {
        "tool_call": GatewayAction.TOOL_CALL,
        "resource_read": GatewayAction.RESOURCE_READ,
        "event_push": GatewayAction.EVENT_PUSH,
    }
    action = action_map.get(raw_action)
    if action is None:
        raise GatewayErrorException(
            GatewayError(
                code="UNSUPPORTED_ACTION",
                message=f"unsupported action: {raw_action}",
                details={"action": raw_action},
                retryable=False,
            )
        )
    return action


def _string_field(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        clean = value.strip()
        return clean if clean else None
    raise GatewayErrorException(
        GatewayError(
            code="INVALID_ARGUMENT",
            message=f"field '{key}' must be string",
            details={"field": key},
            retryable=False,
        )
    )


def _float_field(payload: dict[str, object], key: str, default: float) -> float:
    value = payload.get(key)
    if value is None:
        return default
    if isinstance(value, int):
        return float(value)
    if isinstance(value, float):
        return value
    raise GatewayErrorException(
        GatewayError(
            code="INVALID_ARGUMENT",
            message=f"field '{key}' must be number",
            details={"field": key},
            retryable=False,
        )
    )


def _dict_field(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise GatewayErrorException(
        GatewayError(
            code="INVALID_ARGUMENT",
            message=f"field '{key}' must be object",
            details={"field": key},
            retryable=False,
        )
    )


class DefaultRequestNormalizer:
    """把 ExternalEnvelope 转换为 GatewayRequest。"""

    async def normalize(self, env: ExternalEnvelope) -> GatewayRequest:
        action = _to_gateway_action(env.action)
        params = _dict_field(env.payload, "params") if "params" in env.payload else env.payload
        target_plugin_id = _string_field(env.payload, "target_plugin_id")
        target_entry_id = _string_field(env.payload, "target_entry_id")
        source_app = _string_field(env.payload, "source_app") or env.connection_id
        trace_id = _string_field(env.payload, "trace_id") or env.request_id
        timeout_s = _float_field(env.payload, "timeout_s", 30.0)

        if action in (GatewayAction.TOOL_CALL, GatewayAction.RESOURCE_READ):
            if target_entry_id is None:
                raise GatewayErrorException(
                    GatewayError(
                        code="INVALID_ARGUMENT",
                        message="target_entry_id is required for callable actions",
                        details={"action": action.value},
                        retryable=False,
                    )
                )

        return GatewayRequest(
            request_id=env.request_id,
            protocol=env.protocol,
            action=action,
            source_app=source_app,
            trace_id=trace_id,
            params=params,
            target_plugin_id=target_plugin_id,
            target_entry_id=target_entry_id,
            timeout_s=timeout_s,
            metadata=env.metadata,
        )


@dataclass(slots=True)
class DefaultPolicyEngine:
    """默认策略：可选 plugin allow-list + payload 大小限制。"""

    allowed_plugin_ids: set[str] | None = None
    max_params_bytes: int = 256 * 1024

    async def authorize(self, request: GatewayRequest) -> None:
        if self.allowed_plugin_ids is not None and request.target_plugin_id is not None:
            if request.target_plugin_id not in self.allowed_plugin_ids:
                raise GatewayErrorException(
                    GatewayError(
                        code="FORBIDDEN",
                        message="target plugin is not allowed",
                        details={"target_plugin_id": request.target_plugin_id},
                        retryable=False,
                    )
                )

        params_size = len(json.dumps(request.params, ensure_ascii=False))
        if params_size > self.max_params_bytes:
            raise GatewayErrorException(
                GatewayError(
                    code="PAYLOAD_TOO_LARGE",
                    message="params too large",
                    details={"size": params_size, "max": self.max_params_bytes},
                    retryable=False,
                )
            )


class DefaultRouteEngine:
    """默认路由：优先显式 target，否则 drop。"""

    async def decide(self, request: GatewayRequest) -> RouteDecision:
        if request.target_plugin_id is not None and request.target_entry_id is not None:
            return RouteDecision(
                mode=RouteMode.PLUGIN,
                plugin_id=request.target_plugin_id,
                entry_id=request.target_entry_id,
                reason="explicit target",
            )

        if request.target_entry_id is not None:
            return RouteDecision(
                mode=RouteMode.SELF,
                entry_id=request.target_entry_id,
                reason="entry only route",
            )

        return RouteDecision(mode=RouteMode.DROP, reason="no route target")


class DefaultResponseSerializer:
    """默认响应序列化器。"""

    async def ok(self, request: GatewayRequest, result: object, latency_ms: float) -> GatewayResponse:
        return GatewayResponse(
            request_id=request.request_id,
            success=True,
            data=result,
            latency_ms=latency_ms,
            metadata={"trace_id": request.trace_id},
        )

    async def fail(self, request: GatewayRequest, error: GatewayError, latency_ms: float) -> GatewayResponse:
        return GatewayResponse(
            request_id=request.request_id,
            success=False,
            error=error,
            latency_ms=latency_ms,
            metadata={"trace_id": request.trace_id},
        )


@dataclass(slots=True)
class CallablePluginInvoker:
    """使用可注入回调实现插件调用，便于渐进接入现有 runtime。"""

    invoke_fn: Callable[[GatewayRequest, RouteDecision], object]
    logger: LoggerLike | None = None

    async def invoke(self, request: GatewayRequest, decision: RouteDecision) -> object:
        logger = self.logger if self.logger is not None else logger

        if decision.mode == RouteMode.DROP:
            raise GatewayErrorException(
                GatewayError(
                    code="ROUTE_NOT_FOUND",
                    message="route decision is drop",
                    details={"request_id": request.request_id, "reason": decision.reason},
                    retryable=False,
                )
            )

        logger.debug(
            "Gateway invoking target: mode={}, plugin_id={}, entry_id={}, request_id={}",
            decision.mode.value,
            decision.plugin_id or "",
            decision.entry_id or "",
            request.request_id,
        )
        return self.invoke_fn(request, decision)
