"""Adapter Gateway Core 第一阶段实现。"""

from __future__ import annotations

import time

from loguru import logger

from plugin.sdk.adapter.gateway_contracts import (
    LoggerLike,
    PluginInvoker,
    PolicyEngine,
    RequestNormalizer,
    ResponseSerializer,
    RouteEngine,
    TransportAdapter,
)
from plugin.sdk.adapter.gateway_models import (
    ExternalEnvelope,
    GatewayAction,
    GatewayError,
    GatewayErrorException,
    GatewayRequest,
)


class AdapterGatewayCore:
    """Gateway Core 编排器。"""

    def __init__(
        self,
        transport: TransportAdapter,
        normalizer: RequestNormalizer,
        policy: PolicyEngine,
        router: RouteEngine,
        invoker: PluginInvoker,
        serializer: ResponseSerializer,
        logger: LoggerLike | None = None,
    ) -> None:
        self._transport = transport
        self._normalizer = normalizer
        self._policy = policy
        self._router = router
        self._invoker = invoker
        self._serializer = serializer
        self._logger = logger if logger is not None else globals()["logger"]
        self._running = False

    async def start(self) -> None:
        """启动传输层。"""

        if self._running:
            self._logger.warning("Gateway core already started")
            return

        self._running = True
        await self._transport.start()
        self._logger.info("Gateway core started for protocol={}.", self._transport.protocol_name)

    async def stop(self) -> None:
        """停止传输层。"""

        if not self._running:
            return

        self._running = False
        await self._transport.stop()
        self._logger.info("Gateway core stopped for protocol={}.", self._transport.protocol_name)

    async def run_once(self) -> None:
        """处理一条请求。"""

        if not self._running:
            raise RuntimeError("Gateway core must be started before run_once")

        envelope = await self._transport.recv()
        response = await self.handle_envelope(envelope)
        await self._transport.send(response)

    async def handle_envelope(self, envelope: ExternalEnvelope):
        """处理外部输入并返回统一响应。"""

        started_at = time.perf_counter()
        request: GatewayRequest | None = None
        try:
            request = await self._normalizer.normalize(envelope)
            await self._policy.authorize(request)
            decision = await self._router.decide(request)
            result = await self._invoker.invoke(request, decision)
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            return await self._serializer.ok(request=request, result=result, latency_ms=latency_ms)
        except GatewayErrorException as exc:
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            fallback_request = request if request is not None else self._fallback_request(envelope)
            self._logger.warning(
                "Gateway handled error: protocol={}, request_id={}, code={}",
                envelope.protocol,
                envelope.request_id,
                exc.error.code,
            )
            return await self._serializer.fail(
                request=fallback_request,
                error=exc.error,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            fallback_request = request if request is not None else self._fallback_request(envelope)
            self._logger.exception(
                "Gateway unexpected error: protocol={}, request_id={}, err={}",
                envelope.protocol,
                envelope.request_id,
                str(exc),
            )
            internal_error = GatewayError(
                code="GATEWAY_INTERNAL_ERROR",
                message="unexpected gateway error",
                details={"protocol": envelope.protocol, "request_id": envelope.request_id},
                retryable=False,
            )
            return await self._serializer.fail(
                request=fallback_request,
                error=internal_error,
                latency_ms=latency_ms,
            )

    @staticmethod
    def _fallback_request(envelope: ExternalEnvelope) -> GatewayRequest:
        """在 normalize 失败时构造兜底请求对象。"""

        return GatewayRequest(
            request_id=envelope.request_id,
            protocol=envelope.protocol,
            action=GatewayAction.EVENT_PUSH,
            source_app=envelope.connection_id,
            trace_id=envelope.request_id,
            params=envelope.payload,
            metadata=envelope.metadata,
        )
