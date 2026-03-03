"""Gateway Core 抽象契约（Protocol）。"""

from __future__ import annotations

from typing import Protocol

from plugin.sdk.adapter.gateway_models import (
    ExternalEnvelope,
    GatewayError,
    GatewayRequest,
    GatewayResponse,
    RouteDecision,
)


class LoggerLike(Protocol):
    """
    NEKO logger 兼容接口。
    
    兼容 loguru Logger 和标准 logging.Logger。
    使用 **kwargs 使签名更宽松。
    """

    def debug(self, __message: str, *args: object, **kwargs: object) -> object: ...

    def info(self, __message: str, *args: object, **kwargs: object) -> object: ...

    def warning(self, __message: str, *args: object, **kwargs: object) -> object: ...

    def error(self, __message: str, *args: object, **kwargs: object) -> object: ...

    def exception(self, __message: str, *args: object, **kwargs: object) -> object: ...


class TransportAdapter(Protocol):
    """外部协议传输层。"""

    protocol_name: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def recv(self) -> ExternalEnvelope: ...

    async def send(self, response: GatewayResponse) -> None: ...


class RequestNormalizer(Protocol):
    """把外部包转换为 GatewayRequest。"""

    async def normalize(self, env: ExternalEnvelope) -> GatewayRequest: ...


class PolicyEngine(Protocol):
    """策略校验层，不通过时抛出 GatewayErrorException。"""

    async def authorize(self, request: GatewayRequest) -> None: ...


class RouteEngine(Protocol):
    """路由决策层。"""

    async def decide(self, request: GatewayRequest) -> RouteDecision: ...


class PluginInvoker(Protocol):
    """插件调用层。"""

    async def invoke(self, request: GatewayRequest, decision: RouteDecision) -> object: ...


class ResponseSerializer(Protocol):
    """响应序列化层。"""

    async def ok(self, request: GatewayRequest, result: object, latency_ms: float) -> GatewayResponse: ...

    async def fail(self, request: GatewayRequest, error: GatewayError, latency_ms: float) -> GatewayResponse: ...
