"""MCP protocol transport adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from plugin.sdk.adapter import Err, Ok, Result, TransportError
from plugin.sdk.adapter.gateway_contracts import LoggerLike
from plugin.sdk.adapter.gateway_models import ExternalRequest, GatewayResponse

if TYPE_CHECKING:
    from plugin.plugins.mcp_adapter import MCPClient


@dataclass(slots=True)
class MCPTransportAdapter:
    """
    MCP protocol transport adapter.
    
    Implements TransportAdapter and encapsulates MCPClient connection management.
    
    MCP is passive (tools are called externally), so recv() pulls requests from an internal queue,
    populated by on_tool_call().
    """

    client: "MCPClient"
    logger: LoggerLike
    protocol_name: str = "mcp"
    _request_queue: asyncio.Queue[ExternalRequest] = field(
        default_factory=lambda: asyncio.Queue(maxsize=1000)
    )
    _running: bool = False

    async def start(self) -> Result[None, TransportError]:
        """Start the transport and connect to the MCP server."""
        if self._running:
            self.logger.warning("MCPTransportAdapter already started")
            return Ok(None)

        self._running = True
        timeout = 30.0
        connected = await self.client.connect(timeout=timeout)
        if not connected:
            self._running = False
            return Err(
                TransportError(
                    f"Failed to connect to MCP server: {self.client.config.name}",
                    op_name="mcp.transport.start",
                )
            )

        self.logger.info(
            "MCPTransportAdapter started for server '{}' with {} tools",
            self.client.config.name,
            len(self.client.tools),
        )
        return Ok(None)

    async def stop(self) -> Result[None, TransportError]:
        """Stop the transport."""
        if not self._running:
            return Ok(None)

        self._running = False
        await self.client.disconnect()
        self.logger.info("MCPTransportAdapter stopped for server '{}'", self.client.config.name)
        return Ok(None)

    async def recv(self) -> Result[ExternalRequest, TransportError]:
        """
        Receive external requests.
        
        MCP is passive; enqueue_request() adds requests to the queue.
        """
        if not self._running:
            return Err(TransportError("MCPTransportAdapter is not running", op_name="mcp.transport.recv"))

        return Ok(await self._request_queue.get())

    async def send(self, response: GatewayResponse) -> Result[None, TransportError]:
        """
        Send a response.
        
        MCP responses return directly to callers; this method primarily logs them.
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
        return Ok(None)

    def enqueue_request(self, envelope: ExternalRequest) -> bool:
        """
        Enqueue a request for external callers.
        
        Returns:
            True on successful enqueue, False when the queue is full.
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
        """Check whether the transport is running."""
        return self._running

    @property
    def queue_size(self) -> int:
        """Current queue size."""
        return self._request_queue.qsize()
