"""MCP 协议传输层适配器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from plugin.sdk.adapter.gateway_contracts import LoggerLike
from plugin.sdk.adapter.gateway_models import ExternalEnvelope, GatewayResponse

if TYPE_CHECKING:
    from plugin.plugins.mcp_adapter import MCPClient


@dataclass(slots=True)
class MCPTransportAdapter:
    """
    MCP 协议传输层适配器。
    
    实现 TransportAdapter 协议，封装 MCPClient 的连接管理。
    
    注意：MCP 是被动模式（由外部调用 tool），因此 recv() 从内部队列获取请求，
    请求由 on_tool_call() 方法推入队列。
    """

    client: "MCPClient"
    logger: LoggerLike
    protocol_name: str = "mcp"
    _request_queue: asyncio.Queue[ExternalEnvelope] = field(
        default_factory=lambda: asyncio.Queue(maxsize=1000)
    )
    _running: bool = False

    async def start(self) -> None:
        """启动传输层（连接 MCP Server）。"""
        if self._running:
            self.logger.warning("MCPTransportAdapter already started")
            return

        self._running = True
        timeout = 30.0
        connected = await self.client.connect(timeout=timeout)
        if not connected:
            self._running = False
            raise RuntimeError(f"Failed to connect to MCP server: {self.client.config.name}")

        self.logger.info(
            "MCPTransportAdapter started for server '{}' with {} tools",
            self.client.config.name,
            len(self.client.tools),
        )

    async def stop(self) -> None:
        """停止传输层。"""
        if not self._running:
            return

        self._running = False
        await self.client.disconnect()
        self.logger.info("MCPTransportAdapter stopped for server '{}'", self.client.config.name)

    async def recv(self) -> ExternalEnvelope:
        """
        接收外部请求。
        
        MCP 是被动模式，请求由 enqueue_request() 推入队列。
        """
        if not self._running:
            raise RuntimeError("MCPTransportAdapter is not running")

        return await self._request_queue.get()

    async def send(self, response: GatewayResponse) -> None:
        """
        发送响应。
        
        MCP 模式下响应直接返回给调用方，这里主要用于日志记录。
        """
        if response.success:
            self.logger.debug(
                "MCP response sent: request_id={}, latency_ms={:.2f}",
                response.request_id,
                response.latency_ms or 0.0,
            )
        else:
            error_code = response.error.code if response.error else "UNKNOWN"
            self.logger.warning(
                "MCP error response: request_id={}, code={}, latency_ms={:.2f}",
                response.request_id,
                error_code,
                response.latency_ms or 0.0,
            )

    def enqueue_request(self, envelope: ExternalEnvelope) -> bool:
        """
        将请求推入队列（供外部调用）。
        
        Returns:
            True 如果成功入队，False 如果队列已满
        """
        try:
            self._request_queue.put_nowait(envelope)
            return True
        except asyncio.QueueFull:
            self.logger.error(
                "MCP request queue full, dropping request: {}",
                envelope.request_id,
            )
            return False

    @property
    def is_running(self) -> bool:
        """检查传输层是否运行中。"""
        return self._running

    @property
    def queue_size(self) -> int:
        """当前队列大小。"""
        return self._request_queue.qsize()
