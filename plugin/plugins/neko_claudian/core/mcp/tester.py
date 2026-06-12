# Ported from claudian/src/core/mcp/McpTester.ts
# Original author: Claudian contributors
# License: MIT

"""
McpTester — Test MCP server connections.

Provides functionality to test MCP server connectivity.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class McpTestResult:
    """Result of an MCP server test."""
    success: bool = False
    server_name: str = ""
    error: Optional[str] = None
    tools: List[Dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "success": self.success,
            "serverName": self.server_name,
            "durationMs": self.duration_ms,
        }
        if self.error:
            out["error"] = self.error
        if self.tools:
            out["tools"] = self.tools
        return out


class McpTester:
    """Test MCP server connections.

    Ported from McpTester.ts
    """

    def __init__(self):
        self._active_processes: Dict[str, subprocess.Popen] = {}

    async def test_server(
        self,
        server_name: str,
        config: Dict[str, Any],
        timeout: float = 10.0,
    ) -> McpTestResult:
        """Test an MCP server connection.

        Args:
            server_name: Name of the server
            config: Server configuration
            timeout: Timeout in seconds

        Returns:
            McpTestResult with success status and tools list
        """
        import time
        start_time = time.time()

        result = McpTestResult(server_name=server_name)

        try:
            server_type = config.get("type", "stdio")

            if server_type == "stdio":
                tools = await self._test_stdio_server(config, timeout)
            elif server_type in ("sse", "http"):
                tools = await self._test_http_server(config, timeout)
            else:
                raise ValueError(f"Unknown server type: {server_type}")

            result.success = True
            result.tools = tools

        except asyncio.TimeoutError:
            result.error = f"Connection timed out after {timeout}s"
        except Exception as e:
            result.error = str(e)

        result.duration_ms = (time.time() - start_time) * 1000
        return result

    async def _test_stdio_server(
        self,
        config: Dict[str, Any],
        timeout: float,
    ) -> List[Dict[str, Any]]:
        """Test a stdio MCP server."""
        command = config.get("command")
        args = config.get("args", [])
        env = config.get("env", {})

        if not command:
            raise ValueError("No command specified for stdio server")

        # Build command
        cmd = [command] + args

        # Start process
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env if env else None,
            )
        except FileNotFoundError:
            raise ValueError(f"Command not found: {command}")

        try:
            # Send initialize request
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "neko-claudian",
                        "version": "1.0.0",
                    },
                },
            }

            request_str = json.dumps(init_request) + "\n"
            process.stdin.write(request_str.encode())
            await process.stdin.drain()

            # Read response with timeout
            try:
                response_line = await asyncio.wait_for(
                    process.stdout.readline(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                raise

            if not response_line:
                raise ValueError("No response from server")

            response = json.loads(response_line.decode())
            if "error" in response:
                raise ValueError(f"Server error: {response['error']}")

            # Send initialized notification
            initialized = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
            process.stdin.write((json.dumps(initialized) + "\n").encode())
            await process.stdin.drain()

            # Request tools list
            tools_request = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
            }
            process.stdin.write((json.dumps(tools_request) + "\n").encode())
            await process.stdin.drain()

            # Read tools response
            try:
                tools_line = await asyncio.wait_for(
                    process.stdout.readline(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                raise

            if not tools_line:
                return []

            tools_response = json.loads(tools_line.decode())
            tools = tools_response.get("result", {}).get("tools", [])

            return tools

        finally:
            # Clean up process
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except Exception:
                process.kill()

    async def _test_http_server(
        self,
        config: Dict[str, Any],
        timeout: float,
    ) -> List[Dict[str, Any]]:
        """Test an HTTP/SSE MCP server."""
        url = config.get("url")
        if not url:
            raise ValueError("No URL specified for HTTP server")

        # For now, just check if the URL is reachable
        # Full implementation would use aiohttp
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status >= 400:
                        raise ValueError(f"Server returned status {resp.status}")
                    return []
        except ImportError:
            logger.warning("aiohttp not installed, cannot test HTTP servers")
            return []

    def stop_all(self) -> None:
        """Stop all active test processes."""
        for process in self._active_processes.values():
            try:
                process.terminate()
            except Exception:
                pass
        self._active_processes.clear()
