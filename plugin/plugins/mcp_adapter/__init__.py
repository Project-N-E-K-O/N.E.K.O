"""
MCP Adapter Plugin

MCP (Model Context Protocol) Router - connects MCP servers and exposes their tools as NEKO entries.

Features:
1. Manage connections to multiple MCP servers
2. Automatically discover MCP server tools
3. Dynamically register tools as NEKO entries
4. Provide a unified tool invocation interface
"""
import asyncio
import json
import os
import re
import subprocess
import copy
from urllib.parse import urljoin
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable

import httpx
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup  # type: ignore[import-untyped]
from markdownify import markdownify as markdownify_html  # type: ignore[import-untyped]

from plugin.sdk.plugin import (
    neko_plugin,
    plugin_entry,
    lifecycle,
    Ok,
    Err,
    SdkError,
    tr,
    ui,
)
from plugin.sdk.adapter import AdapterGatewayCore, DefaultPolicyEngine, NekoAdapterPlugin
from plugin.sdk.adapter.gateway_models import ExternalRequest
from plugin.plugins.mcp_adapter.normalizer import MCPRequestNormalizer
from plugin.plugins.mcp_adapter.serializer import MCPResponseSerializer
from plugin.plugins.mcp_adapter.router import MCPRouteEngine
from plugin.plugins.mcp_adapter.invoker import MCPPluginInvoker
from plugin.plugins.mcp_adapter.chat_schema import portable_chat_schema
from utils.aiohttp_proxy_utils import aiohttp_session_kwargs_for_url

# The chat-injection (LLM tool) path creates run records through POST /runs, then polls for terminal results.
# These constants constrain the forwarding timeout budget: LLM tool registration timeout = MCP tool timeout + forwarding margin,
# bounded by main_server /api/tools/register's 300s maximum.
_LLM_TOOL_TIMEOUT_SLACK_S = 15.0
_LLM_TOOL_TIMEOUT_CAP_S = 300.0
_RUN_POLL_INTERVAL_S = 0.5
_RUN_TERMINAL_STATUSES = frozenset(("succeeded", "failed", "canceled", "timeout"))
# Cross-plugin LLM tool-name duplicate-check cache TTL; numeric suffix deduplication limit (prevents unbounded loops in extreme cases).
_FOREIGN_NAME_CACHE_TTL_S = 5.0
_LLM_TOOL_NAME_MAX_SUFFIX = 100
# Polling window to confirm remote registration (register_llm_tool only queues registration to the host).
_REMOTE_CONFIRM_TIMEOUT_S = 5.0
_REMOTE_CONFIRM_INTERVAL_S = 0.2


class _MCPInternalTransport:
    """
    Internal direct-call transport.

    gateway_invoke calls handle_envelope directly without recv/send polling.
    """

    protocol_name = "mcp_internal"

    async def start(self):
        return Ok(None)

    async def stop(self):
        return Ok(None)

    async def recv(self):
        return Err(SdkError("mcp_internal transport does not support recv()"))

    async def send(self, response: object):
        return Ok(None)


@dataclass
class MCPServerConfig:
    """MCP Server configuration"""
    name: str
    transport: str  # "stdio" | "sse" | "streamable-http"
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    url: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class MCPTool:
    """MCP Tool information"""
    name: str
    description: str
    input_schema: Dict[str, object]
    server_name: str


@dataclass
class MCPServerConnection:
    """MCP Server connection status"""
    config: MCPServerConfig
    process: Optional[subprocess.Popen] = None
    reader: Optional[asyncio.StreamReader] = None
    writer: Optional[asyncio.StreamWriter] = None
    tools: List[MCPTool] = field(default_factory=list)
    connected: bool = False
    error: Optional[str] = None
    request_id: int = 0


class McpServerView(BaseModel):
    name: str
    transport: str
    connected: bool
    tools_count: int
    error: str | None = None
    tools: list[dict[str, str]] = Field(default_factory=list)
    inject_to_chat: bool = False


class McpPanelState(BaseModel):
    connected_servers: int
    total_servers: int
    total_tools: int
    servers: list[McpServerView]


class MCPClient:
    """MCP Client - manages communication with MCP Server"""
    
    def __init__(self, config: MCPServerConfig, logger=None):
        self.config = config
        self.logger = logger
        self.process: Optional[asyncio.subprocess.Process] = None
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected = False
        self.tools: List[MCPTool] = []
        self._request_id = 0
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._read_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._shutdown = False
        # Reconnection configuration.
        self._reconnect_attempts = 0
        self._on_disconnect_callback: Optional[Callable] = None
        self._content_error_pattern = re.compile(r"<error>\s*(.*?)\s*</error>", re.IGNORECASE | re.DOTALL)
        self._simplification_error_pattern = re.compile(
            r"(failed to be simplified from html|cannot be simplified to markdown)",
            re.IGNORECASE,
        )

    def _extract_tool_error_message(self, payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None

        content = payload.get("content")
        text_parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())

        if payload.get("isError") is True:
            if text_parts:
                return "\n".join(text_parts)

            structured_error = payload.get("error")
            if isinstance(structured_error, dict):
                message = structured_error.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
            if isinstance(structured_error, str) and structured_error.strip():
                return structured_error.strip()

            return "MCP tool returned isError=true"

        for text in text_parts:
            match = self._content_error_pattern.search(text)
            if match is not None:
                extracted = match.group(1).strip()
                if extracted:
                    return extracted

        return None

    def _get_tool_schema(self, tool_name: str) -> Dict[str, object] | None:
        for tool in self.tools:
            if tool.name == tool_name:
                return tool.input_schema
        return None

    def _tool_supports_raw_mode(self, tool_name: str) -> bool:
        schema = self._get_tool_schema(tool_name)
        if not isinstance(schema, dict):
            return False
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return False
        raw_obj = properties.get("raw")
        if not isinstance(raw_obj, dict):
            return False
        return raw_obj.get("type") == "boolean"

    def _should_retry_with_raw_mode(
        self,
        tool_name: str,
        arguments: Dict[str, object],
        payload: object,
    ) -> bool:
        if arguments.get("raw") is True:
            return False
        if not self._tool_supports_raw_mode(tool_name):
            return False
        error_message = self._extract_tool_error_message(payload)
        if not isinstance(error_message, str):
            return False
        return self._simplification_error_pattern.search(error_message) is not None

    def _extract_embedded_html(self, payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        content = payload.get("content")
        if not isinstance(content, list):
            return None
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str):
                continue
            html_start = text.find("<!DOCTYPE")
            if html_start < 0:
                html_start = text.find("<html")
            if html_start < 0:
                html_start = text.find("<body")
            if html_start < 0:
                html_start = text.find("<main")
            if html_start < 0:
                generic_match = re.search(r"<([a-zA-Z][a-zA-Z0-9:_-]*)(\s|>)", text)
                if generic_match is not None:
                    html_start = generic_match.start()
            if html_start >= 0:
                return text[html_start:].strip()
        return None

    def _normalize_html_payload(self, payload: object, *, source_url: str | None = None) -> Dict[str, object] | None:
        html = self._extract_embedded_html(payload)
        if not isinstance(html, str) or not html.strip():
            return None

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
            tag.decompose()

        target = soup.body or soup
        markdown = markdownify_html(str(target), heading_style="ATX").strip()
        if not markdown:
            return None

        text = markdown if not source_url else f"Contents of {source_url}:\n{markdown}"
        return {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }

    async def _read_http_response_payload(
        self,
        response: object,
        *,
        expected_id: int | None = None,
    ) -> Dict[str, object]:
        headers = getattr(response, "headers", {}) or {}
        content_type_obj = headers.get("Content-Type") or headers.get("content-type") or ""
        content_type = str(content_type_obj).lower()

        if "text/event-stream" not in content_type:
            payload = await response.json()
            if not isinstance(payload, dict):
                raise ValueError(f"Expected JSON object response, got {type(payload).__name__}")
            return payload

        content = getattr(response, "content", None)
        if content is None:
            raise ValueError("SSE response missing content stream")

        data_lines: list[str] = []
        matched_payload: Dict[str, object] | None = None

        async def _flush_event() -> Dict[str, object] | None:
            nonlocal data_lines
            if not data_lines:
                return None
            raw = "\n".join(data_lines).strip()
            data_lines = []
            if not raw:
                return None
            payload_obj = json.loads(raw)
            if not isinstance(payload_obj, dict):
                return None
            if expected_id is None:
                return payload_obj
            payload_id = payload_obj.get("id")
            if payload_id == expected_id:
                return payload_obj
            return None

        while True:
            line = await content.readline()
            if not line:
                payload = await _flush_event()
                if payload is not None:
                    matched_payload = payload
                break

            decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if decoded == "":
                payload = await _flush_event()
                if payload is not None:
                    matched_payload = payload
                    break
                continue
            if decoded.startswith(":"):
                continue
            if decoded.startswith("data:"):
                data_lines.append(decoded[5:].lstrip())

        if matched_payload is None:
            raise ValueError("No matching JSON-RPC payload found in SSE response")
        return matched_payload
    
    async def connect(self, timeout: float = 30.0) -> bool:
        """Connect to MCP Server"""
        if self.config.transport == "stdio":
            return await self._connect_stdio(timeout)
        elif self.config.transport == "sse":
            return await self._connect_sse(timeout)
        elif self.config.transport == "streamable-http":
            return await self._connect_http(timeout)
        else:
            if self.logger:
                self.logger.warning(f"Unsupported transport: {self.config.transport}")
            return False
    
    async def _connect_stdio(self, timeout: float) -> bool:
        """Connect to MCP Server over stdio"""
        try:
            if not self.config.command:
                raise ValueError("Command is required for stdio transport")
            
            # Prepare environment variables.
            env = os.environ.copy()
            env.update(self.config.env)
            
            # Start the process.
            cmd = [self.config.command] + self.config.args
            if self.logger:
                self.logger.info(f"Starting MCP server '{self.config.name}': {' '.join(cmd)}")
            
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            
            self.reader = self.process.stdout
            self.writer = self.process.stdin
            
            # Start the reader task.
            self._read_task = asyncio.create_task(self._read_loop())
            # Start stderr reading to avoid blocking when its buffer fills.
            self._stderr_task = asyncio.create_task(self._read_stderr())
            
            # Send the initialize request.
            result = await asyncio.wait_for(
                self._send_request("initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "neko-mcp-adapter",
                        "version": "0.1.0"
                    }
                }),
                timeout=timeout
            )
            
            if result.get("error"):
                raise Exception(f"Initialize failed: {result['error']}")
            
            # Send the initialized notification.
            await self._send_notification("notifications/initialized", {})
            
            # Fetch the tools list.
            tools_result = await asyncio.wait_for(
                self._send_request("tools/list", {}),
                timeout=timeout
            )
            
            if tools_result.get("error"):
                raise Exception(f"Failed to list tools: {tools_result['error']}")
            
            # Parse tools.
            self.tools = []
            result_obj = tools_result.get("result")
            tools_list: list[object] = []
            if isinstance(result_obj, dict):
                tools_raw = result_obj.get("tools")
                if isinstance(tools_raw, list):
                    tools_list = tools_raw
            for tool in tools_list:
                if not isinstance(tool, dict):
                    continue
                self.tools.append(MCPTool(
                    name=str(tool.get("name", "")),
                    description=str(tool.get("description", "")),
                    input_schema=dict(tool.get("inputSchema", {})) if isinstance(tool.get("inputSchema"), dict) else {},
                    server_name=self.config.name,
                ))
            
            self.connected = True
            if self.logger:
                self.logger.info(
                    f"Connected to MCP server '{self.config.name}' with {len(self.tools)} tools"
                )
            
            return True
            
        except asyncio.TimeoutError:
            if self.logger:
                self.logger.error(f"Timeout connecting to MCP server '{self.config.name}'")
            await self.disconnect()
            return False
        except Exception as e:
            if self.logger:
                self.logger.exception(f"Failed to connect to MCP server '{self.config.name}': {e}")
            await self.disconnect()
            return False
    
    async def _connect_http(self, timeout: float) -> bool:
        """Connect to MCP Server over HTTP/SSE"""
        try:
            if not self.config.url:
                raise ValueError("URL is required for HTTP/SSE transport")
            
            import aiohttp
            
            url = self.config.url.rstrip("/")
            if self.logger:
                self.logger.info(f"Connecting to MCP server '{self.config.name}' via HTTP: {url}")
            
            # Create the HTTP session.
            self._http_session = aiohttp.ClientSession(
                **aiohttp_session_kwargs_for_url(url)
            )
            
            # Send the initialize request.
            init_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "neko-mcp-adapter",
                        "version": "0.1.0"
                    }
                }
            }
            
            # MCP Streamable HTTP requires the Accept header.
            headers = {
                **self.config.headers,
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            }
            
            async with self._http_session.post(
                url,
                json=init_payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    raise ValueError(f"HTTP {resp.status}: {await resp.text()}")
                init_result = await self._read_http_response_payload(resp, expected_id=1)
                # Save the session ID if returned by the server.
                session_id = resp.headers.get("mcp-session-id")
                if session_id:
                    self._http_session_id = session_id
                    headers["mcp-session-id"] = session_id
            
            if "error" in init_result:
                raise ValueError(f"Initialize failed: {init_result['error']}")
            
            # Send the initialized notification.
            notif_payload = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            }
            async with self._http_session.post(url, json=notif_payload, headers=headers) as resp:
                if resp.status >= 400:
                    raise ValueError(f"HTTP {resp.status}: {await resp.text()}")
            
            # Fetch the tools list.
            tools_payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            }
            async with self._http_session.post(
                url,
                json=tools_payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    raise ValueError(f"HTTP {resp.status}: {await resp.text()}")
                tools_result = await self._read_http_response_payload(resp, expected_id=2)

            if "error" in tools_result:
                raise ValueError(f"Failed to list tools: {tools_result['error']}")
            
            # Parse tools.
            self.tools = []
            result_obj = tools_result.get("result")
            tools_list: list[object] = []
            if isinstance(result_obj, dict):
                tools_raw = result_obj.get("tools")
                if isinstance(tools_raw, list):
                    tools_list = tools_raw
            for tool in tools_list:
                if not isinstance(tool, dict):
                    continue
                self.tools.append(MCPTool(
                    name=str(tool.get("name", "")),
                    description=str(tool.get("description", "")),
                    input_schema=dict(tool.get("inputSchema", {})) if isinstance(tool.get("inputSchema"), dict) else {},
                    server_name=self.config.name,
                ))
            
            self.connected = True
            if self.logger:
                self.logger.info(
                    f"Connected to MCP server '{self.config.name}' via HTTP with {len(self.tools)} tools"
                )
            
            return True
            
        except asyncio.TimeoutError:
            if self.logger:
                self.logger.error(f"Timeout connecting to MCP server '{self.config.name}'")
            await self.disconnect()
            return False
        except Exception as e:
            if self.logger:
                self.logger.exception(f"Failed to connect to MCP server '{self.config.name}' via HTTP: {e}")
            await self.disconnect()
            return False

    async def _connect_sse(self, timeout: float) -> bool:
        """Connect to MCP Server over legacy HTTP+SSE."""
        try:
            if not self.config.url:
                raise ValueError("URL is required for SSE transport")

            import aiohttp

            url = self.config.url.rstrip("/")
            if self.logger:
                self.logger.info(f"Connecting to MCP server '{self.config.name}' via SSE: {url}")

            self._http_session = aiohttp.ClientSession(
                **aiohttp_session_kwargs_for_url(url)
            )
            headers = {
                **self.config.headers,
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
            }

            response = await self._http_session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(connect=timeout),
            )
            if response.status != 200:
                body = await response.text()
                response.release()
                raise ValueError(f"HTTP {response.status}: {body}")

            self._sse_response = response
            endpoint_future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._read_task = asyncio.create_task(self._read_sse_loop(endpoint_future=endpoint_future))

            endpoint_url = await asyncio.wait_for(endpoint_future, timeout=timeout)
            if not isinstance(endpoint_url, str) or not endpoint_url.strip():
                raise ValueError("SSE endpoint event did not provide a valid message endpoint")
            self._sse_message_url = urljoin(f"{url}/", endpoint_url.strip())

            result = await asyncio.wait_for(
                self._send_request("initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "neko-mcp-adapter",
                        "version": "0.1.0"
                    }
                }, timeout=timeout),
                timeout=timeout,
            )

            if result.get("error"):
                raise ValueError(f"Initialize failed: {result['error']}")

            await self._send_notification("notifications/initialized", {})

            tools_result = await asyncio.wait_for(
                self._send_request("tools/list", {}, timeout=timeout),
                timeout=timeout,
            )
            if tools_result.get("error"):
                raise ValueError(f"Failed to list tools: {tools_result['error']}")

            self.tools = []
            result_obj = tools_result.get("result")
            tools_list: list[object] = []
            if isinstance(result_obj, dict):
                tools_raw = result_obj.get("tools")
                if isinstance(tools_raw, list):
                    tools_list = tools_raw
            for tool in tools_list:
                if not isinstance(tool, dict):
                    continue
                self.tools.append(MCPTool(
                    name=str(tool.get("name", "")),
                    description=str(tool.get("description", "")),
                    input_schema=dict(tool.get("inputSchema", {})) if isinstance(tool.get("inputSchema"), dict) else {},
                    server_name=self.config.name,
                ))

            self.connected = True
            if self.logger:
                self.logger.info(
                    f"Connected to MCP server '{self.config.name}' via SSE with {len(self.tools)} tools"
                )
            return True
        except asyncio.TimeoutError:
            if self.logger:
                self.logger.error(f"Timeout connecting to MCP server '{self.config.name}' via SSE")
            await self.disconnect()
            return False
        except Exception as e:
            if self.logger:
                self.logger.exception(f"Failed to connect to MCP server '{self.config.name}' via SSE: {e}")
            await self.disconnect()
            return False
    
    def set_disconnect_callback(self, callback: Callable) -> None:
        """Set the disconnect callback"""
        self._on_disconnect_callback = callback
    
    async def disconnect(self):
        """Disconnect"""
        self._shutdown = True
        self.connected = False

        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None
        
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
        
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception as e:
                if self.logger:
                    self.logger.debug(f"Error closing writer: {e}")
            self.writer = None
        
        if self.process:
            try:
                self.process.terminate()
            except ProcessLookupError:
                self.process = None
                self.reader = None
                self.tools = []
            try:
                if self.process is not None:
                    await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    if self.process is not None:
                        self.process.kill()
                except ProcessLookupError:
                    pass
            self.process = None
        
        self.reader = None
        self.tools = []
        
        # Close the HTTP session.
        sse_response = getattr(self, "_sse_response", None)
        if sse_response is not None:
            try:
                sse_response.close()
            except Exception:
                pass
            self._sse_response = None
        self._sse_message_url = None
        self._http_session_id = None
        if hasattr(self, '_http_session') and self._http_session:
            await self._http_session.close()
            self._http_session = None
        
        # Cancel all pending requests.
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(Exception("Connection closed"))
        self._pending_requests.clear()
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, object], timeout: float = 60.0) -> Dict[str, object]:
        """Invoke an MCP tool"""
        if not self.connected:
            return {"error": "Not connected"}
        
        try:
            result = await asyncio.wait_for(
                self._send_request("tools/call", {
                    "name": tool_name,
                    "arguments": arguments,
                }, timeout=timeout),
                timeout=timeout
            )
            
            if result.get("error"):
                return {"error": result["error"]}

            payload = result.get("result", {})
            tool_error = self._extract_tool_error_message(payload)
            if tool_error is not None:
                if self._should_retry_with_raw_mode(tool_name, arguments, payload):
                    retry_arguments = dict(arguments)
                    retry_arguments["raw"] = True
                    retry_result = await asyncio.wait_for(
                        self._send_request("tools/call", {
                            "name": tool_name,
                            "arguments": retry_arguments,
                        }, timeout=timeout),
                        timeout=timeout
                    )
                    if retry_result.get("error"):
                        return {"error": retry_result["error"]}
                    retry_payload = retry_result.get("result", {})
                    source_url = arguments.get("url") if isinstance(arguments.get("url"), str) else None
                    normalized_retry_payload = self._normalize_html_payload(retry_payload, source_url=source_url)
                    if normalized_retry_payload is not None:
                        return {"result": normalized_retry_payload}
                    retry_error = self._extract_tool_error_message(retry_payload)
                    if retry_error is None:
                        return {"result": retry_payload}
                    return {"error": retry_error, "result": retry_payload}
                return {"error": tool_error, "result": payload}

            return {"result": payload}
            
        except asyncio.TimeoutError:
            return {"error": f"Tool call timed out after {timeout}s"}
        except Exception as e:
            return {"error": str(e)}
    
    async def _send_http_request(
        self,
        method: str,
        params: Dict[str, object],
        *,
        timeout: float = 60.0,
    ) -> Dict[str, object]:
        """Send a JSON-RPC request over HTTP"""
        import aiohttp
        
        # Create a fresh session per request to avoid event-loop issues.
        async with aiohttp.ClientSession(
            **aiohttp_session_kwargs_for_url(self.config.url or "")
        ) as session:
            return await self._do_http_request(session, method, params, timeout=timeout)

    async def _send_sse_request(
        self,
        method: str,
        params: Dict[str, object],
        *,
        timeout: float = 60.0,
    ) -> Dict[str, object]:
        import aiohttp

        session = getattr(self, "_http_session", None)
        message_url = getattr(self, "_sse_message_url", None)
        if session is None or not message_url:
            raise Exception("SSE transport is not initialized")

        self._request_id += 1
        request_id = self._request_id
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        try:
            async with session.post(
                message_url,
                json=payload,
                headers={**self.config.headers, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status >= 400:
                    raise Exception(f"HTTP {resp.status}: {await resp.text()}")
                content_type = str(resp.headers.get("Content-Type", "")).lower()
                if "application/json" in content_type or "text/event-stream" in content_type:
                    payload_obj = await self._read_http_response_payload(resp, expected_id=request_id)
                    return payload_obj
                if resp.status == 200:
                    try:
                        payload_obj = await self._read_http_response_payload(resp, expected_id=request_id)
                        return payload_obj
                    except Exception:
                        pass
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_requests.pop(request_id, None)
    
    async def _do_http_request(
        self,
        session: object,
        method: str,
        params: Dict[str, object],
        *,
        timeout: float = 60.0,
    ) -> Dict[str, object]:
        """Perform the actual HTTP request"""
        import aiohttp
        
        self._request_id += 1
        request_id = self._request_id
        
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        
        url = self.config.url
        if not url:
            raise Exception("URL not configured")
        
        headers = {
            **self.config.headers,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        # Add the session ID if present.
        if hasattr(self, '_http_session_id') and self._http_session_id:
            headers["mcp-session-id"] = self._http_session_id
        
        async with session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}: {await resp.text()}")
            result = await self._read_http_response_payload(resp, expected_id=request_id)
        
        if "error" in result:
            return {"error": result["error"]}
        
        return result
    
    async def _send_request(
        self,
        method: str,
        params: Dict[str, object],
        *,
        timeout: float = 60.0,
    ) -> Dict[str, object]:
        """Send a JSON-RPC request"""
        # HTTP transport.
        if self.config.transport == "streamable-http":
            return await self._send_http_request(method, params, timeout=timeout)
        if self.config.transport == "sse":
            return await self._send_sse_request(method, params, timeout=timeout)
        
        # stdio transport.
        if not self.writer:
            raise Exception("Not connected")
        
        self._request_id += 1
        request_id = self._request_id
        
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        
        # Create a Future to await the response.
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        
        try:
            # Send the message.
            data = json.dumps(message) + "\n"
            self.writer.write(data.encode())
            await self.writer.drain()
            
            # Await the response.
            return await future
        finally:
            self._pending_requests.pop(request_id, None)
    
    async def _send_notification(self, method: str, params: Dict[str, object]):
        """Send a JSON-RPC notification (no response)"""
        if self.config.transport == "sse":
            import aiohttp

            session = getattr(self, "_http_session", None)
            message_url = getattr(self, "_sse_message_url", None)
            if session is None or not message_url:
                raise Exception("SSE transport is not initialized")
            message = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
            async with session.post(
                message_url,
                json=message,
                headers={**self.config.headers, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                timeout=aiohttp.ClientTimeout(total=30.0),
            ) as resp:
                if resp.status >= 400:
                    raise Exception(f"HTTP {resp.status}: {await resp.text()}")
            return

        if not self.writer:
            raise Exception("Not connected")
        
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        
        data = json.dumps(message) + "\n"
        self.writer.write(data.encode())
        await self.writer.drain()
    
    async def _read_stderr(self):
        """Read stderr output to prevent a full buffer from blocking the process"""
        try:
            if not self.process or not self.process.stderr:
                return
            
            while not self._shutdown:
                line = await self.process.stderr.readline()
                if not line:
                    break
                
                # Record stderr output: upstream MCP stderr may contain sensitive data, so do not use logger.
                # Decode with errors="replace" so one non-UTF-8 line cannot crash the entire stderr
                # coroutine and subsequently block the subprocess's stderr buffer.
                stderr_text = line.decode("utf-8", errors="replace").strip()
                if stderr_text and self.logger:
                    self.logger.debug(f"MCP server '{self.config.name}' stderr (len={len(stderr_text)})")
                    print(f"[MCP] '{self.config.name}' stderr: {stderr_text}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self.logger:
                self.logger.debug(f"Error reading stderr: {e}")
    
    async def _read_loop(self):
        """Response-reading loop"""
        try:
            while self.reader and not self._shutdown:
                line = await self.reader.readline()
                if not line:
                    # Connection closed.
                    if not self._shutdown and self.connected:
                        self.connected = False
                        if self.logger:
                            self.logger.warning(f"MCP server '{self.config.name}' connection lost")
                        # Trigger the disconnect callback.
                        if self._on_disconnect_callback:
                            asyncio.create_task(self._on_disconnect_callback(self.config.name))
                    break
                
                try:
                    message = json.loads(line.decode())
                    await self._handle_message(message)
                except json.JSONDecodeError:
                    if self.logger:
                        self.logger.warning(f"Invalid JSON from MCP server: {line}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self.logger:
                self.logger.exception(f"Error in read loop: {e}")
            # Connection closed unexpectedly.
            if not self._shutdown and self.connected:
                self.connected = False
                if self._on_disconnect_callback:
                    asyncio.create_task(self._on_disconnect_callback(self.config.name))

    async def _read_sse_loop(self, *, endpoint_future: asyncio.Future | None = None) -> None:
        response = getattr(self, "_sse_response", None)
        if response is None:
            if endpoint_future is not None and not endpoint_future.done():
                endpoint_future.set_exception(RuntimeError("SSE response not initialized"))
            return

        event_name = "message"
        data_lines: list[str] = []
        try:
            while not self._shutdown:
                line = await response.content.readline()
                if not line:
                    if endpoint_future is not None and not endpoint_future.done():
                        endpoint_future.set_exception(RuntimeError("SSE stream closed before endpoint event"))
                    if not self._shutdown and self.connected and self._on_disconnect_callback:
                        self.connected = False
                        asyncio.create_task(self._on_disconnect_callback(self.config.name))
                    break

                decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if decoded == "":
                    raw_data = "\n".join(data_lines).strip()
                    if event_name == "endpoint" and endpoint_future is not None and not endpoint_future.done():
                        endpoint_future.set_result(raw_data)
                    elif event_name == "message" and raw_data:
                        try:
                            message = json.loads(raw_data)
                            await self._handle_message(message)
                        except Exception as e:
                            if self.logger:
                                self.logger.warning(f"Invalid SSE message from MCP server '{self.config.name}': {e}")
                    event_name = "message"
                    data_lines = []
                    continue
                if decoded.startswith(":"):
                    continue
                if decoded.startswith("event:"):
                    event_name = decoded[6:].strip() or "message"
                    continue
                if decoded.startswith("data:"):
                    data_lines.append(decoded[5:].lstrip())
        except asyncio.CancelledError:
            if endpoint_future is not None and not endpoint_future.done():
                endpoint_future.cancel()
        except Exception as e:
            if endpoint_future is not None and not endpoint_future.done():
                endpoint_future.set_exception(e)
            if self.logger:
                self.logger.exception(f"Error in SSE read loop: {e}")
            if not self._shutdown and self.connected and self._on_disconnect_callback:
                self.connected = False
                asyncio.create_task(self._on_disconnect_callback(self.config.name))
    
    async def _handle_message(self, message: Dict[str, object]):
        """Handle an incoming message"""
        request_id = message.get("id")
        
        if request_id is not None:
            # This is a response.
            req_id_int = int(request_id) if isinstance(request_id, (int, float, str)) else 0
            future = self._pending_requests.get(req_id_int)
            if future and not future.done():
                if "error" in message:
                    future.set_result({"error": message["error"]})
                else:
                    future.set_result({"result": message.get("result")})
        else:
            # This is a notification.
            method = message.get("method")
            if self.logger:
                self.logger.debug(f"Received notification: {method}")


@neko_plugin
class MCPAdapterPlugin(NekoAdapterPlugin):
    """
    MCP Adapter Plugin - a genuine Adapter-type plugin
    
    Uses the Gateway Core architecture:
    - MCPRouteEngine: routing decisions
    - MCPPluginInvoker: plugin invocation
    - MCPRequestNormalizer: request normalization
    - MCPResponseSerializer: response serialization
    """
    
    __freezable__ = ["_server_states"]
    _CONFIG_DELETE_MARKER = "__DELETE__"
    
    def __init__(self, ctx):
        super().__init__(ctx)
        self._clients: Dict[str, MCPClient] = {}
        self._server_states: Dict[str, Dict[str, object]] = {}
        self._connect_tasks: Dict[str, asyncio.Task] = {}
        self._pending_auto_connect: Dict[str, tuple[Dict[str, object], float]] = {}
        self._reconnect_tasks: Dict[str, asyncio.Task] = {}
        self._shutdown = False
        # Cached reconnection configuration.
        self._auto_reconnect = True
        self._reconnect_interval = 5
        self._max_reconnect_attempts = 3
        self._tool_timeout = 60.0
        self._servers_config: Dict[str, Dict[str, object]] = {}
        # MCP tools injected into chat context: tool_id -> {"llm_name", "server_name", "tool_name"}
        self._chat_tools: Dict[str, Dict[str, str]] = {}
        # Locally registered chat injections awaiting remote confirmation (same structure); move to _chat_tools after confirmation.
        self._pending_chat_tools: Dict[str, Dict[str, str]] = {}
        # Serialize chat-injection registration/removal and set_chat_injection's persistence + application,
        # preventing concurrent toggles from interleaving (mapping overwrites or cleanup during registration).
        self._chat_tools_lock = asyncio.Lock()
        self._servers_config_lock = asyncio.Lock()
        # Cross-plugin LLM tool-name duplicate-check cache: (fetched_at, foreign_names).
        self._foreign_llm_names_cache: Optional[tuple[float, frozenset]] = None
        
        # Gateway Core components.
        self._route_engine: Optional[MCPRouteEngine] = None
        self._invoker: Optional[MCPPluginInvoker] = None
        self._normalizer: Optional[MCPRequestNormalizer] = None
        self._serializer: Optional[MCPResponseSerializer] = None
        self._policy: Optional[DefaultPolicyEngine] = None
        self._gateway_core: Optional[AdapterGatewayCore] = None

    @ui.context(id="dashboard", title="MCP Adapter 管理面板")
    async def get_dashboard_ui_context(self) -> McpPanelState:
        self._schedule_pending_auto_connects()
        servers: list[McpServerView] = []
        seen: set[str] = set()
        for name, client in self._clients.items():
            seen.add(name)
            servers.append(McpServerView(
                name=name,
                transport=client.config.transport,
                connected=bool(client.connected),
                tools_count=len(client.tools),
                error=None,
                inject_to_chat=self._is_chat_injection_enabled(name),
                tools=[
                    {
                        "name": tool.name,
                        "description": tool.description,
                    }
                    for tool in client.tools
                ],
            ))
        for name, cfg in self._servers_config.items():
            if name in seen or not isinstance(cfg, dict):
                continue
            state = self._server_states.get(name, {})
            servers.append(McpServerView(
                name=name,
                transport=str(cfg.get("transport", "unknown")),
                connected=bool(state.get("connected", False)),
                tools_count=int(state.get("tools_count", 0) or 0),
                error=str(state.get("error")) if state.get("error") else None,
                inject_to_chat=self._is_chat_injection_enabled(name),
                tools=[
                    {"name": str(tool_name), "description": ""}
                    for tool_name in state.get("tools", [])
                    if isinstance(tool_name, str)
                ] if isinstance(state.get("tools"), list) else [],
            ))
        return McpPanelState(
            connected_servers=sum(1 for item in servers if item.connected),
            total_servers=len(servers),
            total_tools=sum(item.tools_count for item in servers),
            servers=servers,
        )
    
    @lifecycle(id="startup")
    async def on_startup(self):
        """Connect all configured MCP servers on plugin startup"""
        self.ctx.logger.info("MCP Adapter starting...")
        
        # Initialize the Adapter base class.
        await self.adapter_startup()
        
        # Load configuration.
        config = await self.config.dump()
        servers_config = config.get("mcp_servers", {})
        adapter_config = config.get("mcp_adapter", {})
        
        connect_timeout = self._coerce_timeout(adapter_config.get("connect_timeout", 30), 30.0)
        
        # Cache reconnection configuration.
        self._auto_reconnect = self._coerce_bool(adapter_config.get("auto_reconnect", True), True)
        self._reconnect_interval = self._coerce_int(adapter_config.get("reconnect_interval", 5), 5, minimum=0)
        self._max_reconnect_attempts = self._coerce_int(adapter_config.get("max_reconnect_attempts", 3), 3, minimum=0)
        self._tool_timeout = self._coerce_timeout(adapter_config.get("tool_timeout", 60), 60.0)
        self._servers_config = servers_config
        
        # Initialize Gateway Core components first, before server connections, because _register_mcp_tools depends on them.
        self._init_gateway_core()
        
        # Schedule enabled servers in the background. Startup must stay responsive
        # so hosted UI context/actions can be served immediately.
        for server_name, server_cfg in servers_config.items():
            if not isinstance(server_cfg, dict):
                continue
            
            if not server_cfg.get("enabled", True):
                self.ctx.logger.info(f"Skipping disabled MCP server: {server_name}")
                continue
            
            self._pending_auto_connect[server_name] = (server_cfg, connect_timeout)
            self._server_states[server_name] = {
                **self._server_states.get(server_name, {}),
                "connected": False,
                "error": "Auto-connect pending",
            }
        
        self.ctx.logger.info(
            f"MCP Adapter started with {len(self._clients)} connected servers; pending {len(self._pending_auto_connect)} auto-connect task(s)"
        )

    async def _on_command_loop_start(self) -> None:
        """Schedule startup auto-connect tasks once the long-lived command loop is active."""
        self._schedule_pending_auto_connects()
    
    async def _on_tool_register(
        self,
        tool_id: str,
        display_name: str,
        description: str,
        schema: Optional[Dict[str, object]],
        server_name: str,
        tool_name: str,
    ) -> bool:
        """Gateway Core tool registration callback - register a dynamic entry and inject into chat context as configured."""
        # Create the tool handler.
        async def tool_handler(**kwargs: object) -> Dict[str, object]:
            # Remove parameters injected by NEKO.
            arguments = {k: v for k, v in kwargs.items() if not k.startswith("_")}

            # Get the corresponding client.
            target_client = self._clients.get(server_name)
            if not target_client:
                return Err(SdkError(f"Server '{server_name}' not connected"))

            result = await target_client.call_tool(tool_name, arguments, timeout=self._tool_timeout)
            if "error" in result:
                return Err(SdkError(str(result["error"])))
            payload = self._build_mcp_tool_payload(
                result=result.get("result", {}),
                server_name=server_name,
                tool_name=tool_name,
            )
            return await self.finish(
                data=payload,
                reply=True,
                message=str(payload.get("summary") or ""),
            )

        # Register as a dynamic entry.
        registered = self.register_dynamic_entry(
            entry_id=tool_id,
            handler=tool_handler,
            name=display_name,
            description=description,
            input_schema=schema,
            kind="action",
            timeout=self._tool_timeout + 5.0,
            llm_result_fields=["summary"],
        )

        # Inject into chat context (LLM tool) per server configuration: register only locally
        # as pending; _register_mcp_tools confirms remotely after registering the entire batch.
        # Injection failures only log and do not affect the dynamic entry itself: the original POST /runs
        # invocation path remains available.
        if registered and self._is_chat_injection_enabled(server_name):
            async with self._chat_tools_lock:
                await self._register_chat_tool_local_locked(
                    tool_id=tool_id,
                    server_name=server_name,
                    tool_name=tool_name,
                    description=description,
                    schema=schema,
                )

        return registered

    async def _on_tool_unregister(self, tool_id: str) -> bool:
        """Gateway Core tool unregistration callback - remove the dynamic entry and chat injection."""
        async with self._chat_tools_lock:
            await self._unregister_chat_tool_locked(tool_id)
        return self.unregister_dynamic_entry(tool_id)

    # ------------------------------------------------------------------
    # Chat-context injection (LLM tool).
    # ------------------------------------------------------------------
    #
    # Use the plugin SDK's register_llm_tool / unregister_llm_tool (see
    # plugin/sdk/plugin/base.py) to dynamically inject/remove MCP tools in chat context:
    # the SDK registers a dynamic entry with reserved ID __llm_tool__{name}, then
    # LLM_TOOL_REGISTER IPC -> main_server /api/tools/register registers it as
    # a model-callable tool. Chat LLM calls return through /api/llm-tools/callback to
    # this plugin's handler, which forwards to POST /runs to reuse the original invocation path,
    # placing calls in the plugin management UI's run history.

    def _is_chat_injection_enabled(self, server_name: str) -> bool:
        cfg = self._servers_config.get(server_name)
        if not isinstance(cfg, dict):
            return False
        return self._coerce_bool(cfg.get("inject_to_chat", False), False)

    def _chat_tool_timeout(self) -> float:
        """Total chat-injected tool budget (seconds): MCP timeout + forwarding margin, bounded by main_server's
        ``/api/tools/register`` 300s maximum. Registration timeout, polling deadline, and run-side
        entry_timeout all derive from this budget, ensuring the run watchdog cancels before the
        chat polling deadline, rather than chat timing out before the run later succeeds."""
        return min(self._tool_timeout + _LLM_TOOL_TIMEOUT_SLACK_S, _LLM_TOOL_TIMEOUT_CAP_S)

    async def _fetch_foreign_llm_tool_names(self) -> frozenset:
        """Query LLM tool names occupied by other sources on main_server.

        main_server's tool registry uses names as global keys with replacement semantics; cross-plugin
        duplicates would evict others' tools and redirect model calls. ``/api/tools/register``
        now rejects cross-source overwrites on the server (the authoritative defense); this preliminary check
        is merely best-effort fail-fast optimization, avoiding queued registrations for doomed names and selecting numeric
        suffixes early. Query failure returns an empty set (fail-open) without blocking injection. A short TTL cache
        avoids repeated interface calls within one tool-registration batch.
        """
        from config import MAIN_SERVER_PORT

        loop = asyncio.get_running_loop()
        now = loop.time()
        cached = self._foreign_llm_names_cache
        if cached is not None and now - cached[0] < _FOREIGN_NAME_CACHE_TTL_S:
            return cached[1]

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=2.0),
                proxy=None,
                trust_env=False,
            ) as client:
                resp = await client.get(f"http://127.0.0.1:{int(MAIN_SERVER_PORT)}/api/tools")
            body = resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, OSError, ValueError):
            body = None

        foreign: set[str] = set()
        tools_by_role = body.get("tools_by_role") if isinstance(body, dict) else None
        if isinstance(tools_by_role, dict):
            own_source = f"plugin:{self.plugin_id}"
            for role_tools in tools_by_role.values():
                if not isinstance(role_tools, list):
                    continue
                for tool in role_tools:
                    if not isinstance(tool, dict):
                        continue
                    name = tool.get("name")
                    if not isinstance(name, str) or not name:
                        continue
                    if str(tool.get("source") or "") != own_source:
                        foreign.add(name)

        frozen = frozenset(foreign)
        self._foreign_llm_names_cache = (now, frozen)
        return frozen

    def _alloc_llm_tool_name(self, tool_id: str, taken: frozenset = frozenset()) -> Optional[str]:
        """Map an MCP tool_id to a valid, unique LLM tool name.

        Chat providers require names matching ``[A-Za-z0-9_\\-]{1,64}``; server/tool
        names may contain non-ASCII characters, all folded to "_". ``taken`` contains names occupied
        across plugins (deduplicated with this plugin's registered names); append runtime numeric suffixes on conflict.
        Return None if suffixes are exhausted and conflicts remain, letting the caller skip injection.
        """
        base = re.sub(r"[^A-Za-z0-9_\-]+", "_", tool_id).strip("_-")
        if not base:
            base = "mcp_tool"
        base = base[:56]
        candidate = base
        suffix = 2
        while candidate in self._llm_tools or candidate in taken:
            if suffix > _LLM_TOOL_NAME_MAX_SUFFIX:
                return None
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    async def _confirm_pending_chat_tools_locked(self, server_name: str) -> int:
        """Confirm a server's pending chat injections have taken effect on main_server in one batch.

        ``register_llm_tool`` merely queues registration to the host asynchronously; only the host logs failures.
        main_server unavailability and cross-source name rejection both occur on this path,
        so local pending registration requires confirmation. The batch shares one deadline, preventing tool
        count from multiplying connection stalls (at most one window). Confirmed entries move to
        ``_chat_tools``; unconfirmed local registrations roll back. Match both (name,
        source=plugin:{self}); a name belonging to another source can never
        match. Requires ``_chat_tools_lock``.
        """
        pending_ids = [
            tid for tid, info in self._pending_chat_tools.items()
            if info.get("server_name") == server_name
        ]
        if not pending_ids:
            return 0
        from config import MAIN_SERVER_PORT

        own_source = f"plugin:{self.plugin_id}"
        names_by_id = {tid: str(self._pending_chat_tools[tid].get("llm_name") or "") for tid in pending_ids}
        pending_names = {name for name in names_by_id.values() if name}
        confirmed_names: set[str] = set()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _REMOTE_CONFIRM_TIMEOUT_S
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(2.0, connect=1.0),
                proxy=None,
                trust_env=False,
            ) as client:
                while pending_names and loop.time() < deadline:
                    try:
                        resp = await client.get(f"http://127.0.0.1:{int(MAIN_SERVER_PORT)}/api/tools")
                        if resp.status_code == 200:
                            body = resp.json()
                            tools_by_role = body.get("tools_by_role") if isinstance(body, dict) else None
                            if isinstance(tools_by_role, dict):
                                for role_tools in tools_by_role.values():
                                    if not isinstance(role_tools, list):
                                        continue
                                    for tool in role_tools:
                                        if not isinstance(tool, dict):
                                            continue
                                        name = tool.get("name")
                                        if (
                                            name in pending_names
                                            and str(tool.get("source") or "") == own_source
                                        ):
                                            confirmed_names.add(str(name))
                            pending_names -= confirmed_names
                    except (httpx.HTTPError, OSError, ValueError):
                        pass
                    if pending_names and loop.time() < deadline:
                        await asyncio.sleep(min(_REMOTE_CONFIRM_INTERVAL_S, max(deadline - loop.time(), 0.0)))
        except (httpx.HTTPError, OSError, ValueError):
            pass

        def _rollback(llm_name: str) -> None:
            try:
                self.unregister_llm_tool(llm_name)
            except Exception:
                pass

        if not self._is_chat_injection_enabled(server_name):
            # Configuration flipped during confirmation (e.g. concurrent disable persisted false): roll everything back.
            for tid in pending_ids:
                info = self._pending_chat_tools.pop(tid, None)
                if info:
                    _rollback(str(info.get("llm_name") or ""))
            self.ctx.logger.info(
                f"Chat injection disabled while confirming tools of server '{server_name}'; rolled back"
            )
            return 0

        confirmed_count = 0
        for tid in pending_ids:
            info = self._pending_chat_tools.pop(tid, None)
            if info is None:
                continue
            llm_name = str(info.get("llm_name") or "")
            if llm_name in confirmed_names:
                self._chat_tools[tid] = info
                confirmed_count += 1
            else:
                _rollback(llm_name)
        if confirmed_count < len(pending_ids):
            self.ctx.logger.warning(
                f"Remote registration confirmed for {confirmed_count}/{len(pending_ids)} "
                f"chat tool(s) of server '{server_name}'; unconfirmed ones rolled back"
            )
        return confirmed_count

    async def _register_chat_tool_local_locked(
        self,
        *,
        tool_id: str,
        server_name: str,
        tool_name: str,
        description: str,
        schema: Optional[Dict[str, object]],
    ) -> bool:
        """Locally register one MCP tool for chat injection (pending batch confirmation).

        Requires ``_chat_tools_lock``. Remote confirmation is batched by
        ``_confirm_pending_chat_tools_locked``. Registration paths (connection callbacks,
        enabling the toggle) must not await each tool's confirmation here, which would multiply stalls by tool count.
        """
        if tool_id in self._chat_tools or tool_id in self._pending_chat_tools:
            return True
        foreign = await self._fetch_foreign_llm_tool_names()
        llm_name = self._alloc_llm_tool_name(tool_id, foreign)
        if llm_name is None:
            self.ctx.logger.warning(
                f"Skip chat injection for MCP tool '{tool_id}': no free LLM tool name"
            )
            return False
        try:
            parameters = portable_chat_schema(
                schema if isinstance(schema, dict) else {"type": "object", "properties": {}}
            )
            self.register_llm_tool(
                name=llm_name,
                description=description or f"MCP tool '{tool_name}' from server '{server_name}'.",
                parameters=parameters,
                handler=self._build_chat_tool_handler(
                    tool_id=tool_id,
                    server_name=server_name,
                    tool_name=tool_name,
                ),
                timeout=self._chat_tool_timeout(),
            )
        except Exception as exc:
            self.ctx.logger.warning(
                f"Failed to inject MCP tool '{tool_id}' into chat context: {exc}"
            )
            return False
        self._pending_chat_tools[tool_id] = {
            "llm_name": llm_name,
            "server_name": server_name,
            "tool_name": tool_name,
        }
        return True

    async def _remote_unregister_llm_tool(self, llm_name: str) -> str:
        """Synchronously call main_server directly to unregister an LLM tool.

        Return "removed" (removed or already absent), "refused" (name owned by another
        source, so the server refused deletion and our registration never took effect), or "failed" (remote
        temporarily unreachable). SDK ``unregister_llm_tool`` guarantees only local cleanup; remote
        removal is asynchronous, failures are logged only by the host, and the plugin cannot observe results. Include
        ``expected_source`` for server-side ownership verification, never accidentally deleting a same-named
        tool owned by another plugin across sources.
        """
        from config import MAIN_SERVER_PORT

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=2.0),
                proxy=None,
                trust_env=False,
            ) as client:
                resp = await client.post(
                    f"http://127.0.0.1:{int(MAIN_SERVER_PORT)}/api/tools/unregister",
                    json={
                        "name": llm_name,
                        "role": None,
                        "expected_source": f"plugin:{self.plugin_id}",
                    },
                )
            if resp.status_code >= 400:
                return "failed"
            body = resp.json()
        except (httpx.HTTPError, OSError, ValueError):
            return "failed"
        if not isinstance(body, dict):
            return "removed"
        # Evaluate failed_roles before refused_roles: partial failure (some roles still carrying
        # our tools) needs mapping retention for retry more than a name entirely owned by someone else.
        if body.get("failed_roles"):
            return "failed"
        if body.get("refused_roles"):
            return "refused"
        return "removed"

    async def _unregister_chat_tool_locked(self, tool_id: str) -> str:
        """Remove one chat injection. Return "removed" / "absent" / "refused" / "failed".

        Requires ``_chat_tools_lock``. Pending unconfirmed mappings use SDK unregistration (the host
        removes remotely asynchronously with source verification); confirmed mappings synchronously confirm
        remote removal first. "refused" means another source owns the name on main_server: invalidate local
        mappings and registration without touching others' tools. "failed" means the remote is temporarily
        unreachable; retain mappings for retry.
        """
        pending = self._pending_chat_tools.get(tool_id)
        info = pending if pending is not None else self._chat_tools.get(tool_id)
        if info is None:
            return "absent"
        llm_name = str(info.get("llm_name") or "")
        if not llm_name:
            self._chat_tools.pop(tool_id, None)
            self._pending_chat_tools.pop(tool_id, None)
            return "absent"
        remote = await self._remote_unregister_llm_tool(llm_name)
        if remote == "failed":
            # Retain mappings until remote removal is confirmed, allowing retry on the next toggle/disconnect;
            # avoid a zombie state where the panel shows disabled but main_server retains dead callback tools.
            self.ctx.logger.warning(
                f"Remote unregister not confirmed for chat tool '{llm_name}' "
                f"(MCP tool '{tool_id}'); keeping mapping for retry"
            )
            return "failed"
        self._chat_tools.pop(tool_id, None)
        self._pending_chat_tools.pop(tool_id, None)
        try:
            self.unregister_llm_tool(llm_name)
        except Exception as exc:
            self.ctx.logger.warning(
                f"Local cleanup after remote unregister failed for MCP tool '{tool_id}': {exc}"
            )
        if remote == "refused":
            # Name ownership flipped to another source (e.g. claimed after main_server restarted):
            # SDK unregistration above removes only our local registration and stops on remote refusal,
            # never touching others' tools. Invalidate the mapping; next enable registers under a new name.
            self.ctx.logger.info(
                f"Chat tool '{llm_name}' is owned by another source on main_server; "
                f"dropping local registration for MCP tool '{tool_id}'"
            )
            return "refused"
        self.ctx.logger.info(
            f"Removed chat injection for MCP tool '{tool_id}' (LLM tool '{llm_name}')"
        )
        return "removed"

    def _build_chat_tool_handler(self, *, tool_id: str, server_name: str, tool_name: str) -> Callable[..., Any]:
        """Build a chat-injected tool handler forwarding to POST /runs to produce run records."""

        async def handler(**kwargs: object) -> Dict[str, object]:
            arguments = {k: v for k, v in kwargs.items() if not k.startswith("_")}
            outcome = await self._execute_chat_tool_via_run(
                entry_id=tool_id,
                server_name=server_name,
                tool_name=tool_name,
                arguments=arguments,
            )
            if not outcome.get("success"):
                error_msg = str(
                    outcome.get("error")
                    or f"MCP tool '{tool_name}' failed (run status: {outcome.get('status')})"
                )
                return {
                    "output": {"server_name": server_name, "tool_name": tool_name, "error": error_msg},
                    "is_error": True,
                    "error": error_msg,
                }
            payload = outcome.get("data")
            # _build_mcp_tool_payload always returns dict (at least "result"); a None payload
            # means export could not be read. Report an explicit error for model retry rather than a silent empty result.
            if payload is None:
                error_msg = "Tool call succeeded but the run result could not be read back"
                return {
                    "output": {"server_name": server_name, "tool_name": tool_name, "error": error_msg},
                    "is_error": True,
                    "error": error_msg,
                }
            summary = ""
            if isinstance(payload, dict):
                summary = str(payload.get("summary") or "")
            if not summary:
                summary = self._summarize_mcp_result(payload)
            return {
                "server_name": server_name,
                "tool_name": tool_name,
                "summary": summary,
            }

        return handler

    def _resolve_plugin_server_origin(self) -> str:
        """Resolve user_plugin_server's loopback origin.

        Startup may rebind a conflicting port; the environment variable stores the actual port. Resolution order
        matches llm_tool_registry / music_pusher.
        """
        from config import USER_PLUGIN_SERVER_PORT

        raw = os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "").strip()
        try:
            port = int(raw) if raw else int(USER_PLUGIN_SERVER_PORT)
        except ValueError:
            port = int(USER_PLUGIN_SERVER_PORT)
        return f"http://127.0.0.1:{port}"

    @staticmethod
    def _phase_timeout_within(deadline: float, *, cap: float = 10.0) -> httpx.Timeout:
        """Build httpx phase timeouts from the remaining budget.

        httpx.Timeout connect/read/write/pool values limit individual phases, not total request
        duration. Any phase exceeding the remaining budget would cross the deadline and delay
        ``_cancel_run_best_effort``. Cap every phase at the remaining budget; the caller's
        ``asyncio.timeout_at(deadline)`` enforces the hard limit for the complete request.
        """
        remaining = deadline - asyncio.get_running_loop().time()
        return httpx.Timeout(
            max(min(remaining, cap), 1.0),
            connect=max(min(remaining, 2.0), 0.1),
        )

    async def _execute_chat_tool_via_run(
        self,
        *,
        entry_id: str,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, object],
    ) -> Dict[str, object]:
        """Execute a chat-injected MCP tool call through POST /runs.

        This mirrors the original invocation path (brain task_executor -> POST /runs), so
        the call appears with plugin_id=mcp_adapter, entry_id=mcp_{server}_{tool} in the plugin
        management UI's run history. Return {"status", "success", "data", "error"}.
        """
        base = self._resolve_plugin_server_origin()
        timeout_s = self._chat_tool_timeout()
        # Creation and polling share one absolute deadline: creation may consume the entire remaining budget
        # (no fixed 10s ceiling squeezing the polling window), keeping total processing within the timeout
        # registered with main_server, so chat does not time out before our clean timeout error.
        deadline = asyncio.get_running_loop().time() + timeout_s
        run_args = dict(arguments) if isinstance(arguments, dict) else {}
        # Preserve the configured MCP execution window with forwarding slack.
        run_args["_ctx"] = {"entry_timeout": min(self._tool_timeout, max(timeout_s - 1.0, 0.1))}
        run_id: Optional[str] = None

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=2.0),
                proxy=None,
                trust_env=False,
            ) as client:
                try:
                    # Hard deadline: even with httpx phase timeouts capped at remaining budget, a server's
                    # slow trickle can cross the deadline. Ensure no request delays the timeout
                    # decision and cancellation here.
                    async with asyncio.timeout_at(deadline):
                        create_resp = await client.post(
                            f"{base}/runs",
                            json={
                                "plugin_id": self.plugin_id,
                                "entry_id": entry_id,
                                "args": run_args,
                            },
                            timeout=self._phase_timeout_within(deadline, cap=timeout_s),
                        )
                        if create_resp.status_code >= 400:
                            return {
                                "status": "failed",
                                "success": False,
                                "data": None,
                                "error": f"POST /runs returned HTTP {create_resp.status_code}",
                            }
                        create_body = create_resp.json()
                        candidate_id = create_body.get("run_id") if isinstance(create_body, dict) else None
                        if not isinstance(candidate_id, str) or not candidate_id:
                            return {
                                "status": "failed",
                                "success": False,
                                "data": None,
                                "error": "POST /runs response missing run_id",
                            }
                        run_id = candidate_id

                        run_record: Dict[str, object] = {}
                        while True:
                            remaining = deadline - asyncio.get_running_loop().time()
                            if remaining <= 0:
                                # Best-effort cancel the run before giving up; otherwise an upstream retry may overlap
                                # the still-running run and duplicate execution of MCP tools with side effects.
                                # The run watchdog (entry_timeout) usually already canceled it; this is a fallback
                                # when creation time consumes the guard window.
                                await self._cancel_run_best_effort(base, run_id, tool_name)
                                return {
                                    "status": "timeout",
                                    "success": False,
                                    "data": None,
                                    "error": f"Timed out waiting for run of MCP tool '{tool_name}'",
                                }
                            poll_resp = await client.get(
                                f"{base}/runs/{run_id}",
                                timeout=self._phase_timeout_within(deadline),
                            )
                            if poll_resp.status_code in (404, 410):
                                return {
                                    "status": "failed",
                                    "success": False,
                                    "data": None,
                                    "error": f"Run {run_id} not found (HTTP {poll_resp.status_code})",
                                }
                            if poll_resp.status_code == 200:
                                run_record = poll_resp.json()
                                if run_record.get("status") in _RUN_TERMINAL_STATUSES:
                                    break
                            await asyncio.sleep(min(_RUN_POLL_INTERVAL_S, max(deadline - asyncio.get_running_loop().time(), 0.0)))

                        status = str(run_record.get("status") or "failed")
                        if status != "succeeded":
                            error = run_record.get("error")
                            if isinstance(error, dict):
                                error_msg = str(error.get("message") or error.get("code") or status)
                            elif isinstance(error, str) and error:
                                error_msg = error
                            else:
                                error_msg = f"Run {status}"
                            return {"status": status, "success": False, "data": None, "error": error_msg}

                        export_resp = await client.get(
                            f"{base}/runs/{run_id}/export",
                            params={"limit": 50},
                            timeout=self._phase_timeout_within(deadline),
                        )
                        if export_resp.status_code != 200:
                            return {"status": status, "success": True, "data": None, "error": None}
                        # The run succeeded; export parse failure only degrades to empty data and must not report failure.
                        try:
                            payload = self._extract_chat_tool_payload(export_resp.json())
                        except ValueError:
                            payload = None
                        return {"status": status, "success": True, "data": payload, "error": None}
                except TimeoutError:
                    # Hard deadline triggered (a request crossed it): with a run_id, attempt cancellation
                    # after the deadline to prevent continued execution after we give up.
                    await self._cancel_run_best_effort(base, run_id, tool_name)
                    return {
                        "status": "timeout",
                        "success": False,
                        "data": None,
                        "error": f"Timed out waiting for run of MCP tool '{tool_name}'",
                    }
        except (httpx.HTTPError, OSError, ValueError) as exc:
            await self._cancel_run_best_effort(base, run_id, tool_name)
            return {
                "status": "failed",
                "success": False,
                "data": None,
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def _cancel_run_best_effort(self, base: str, run_id: Optional[str], tool_name: str) -> None:
        """Best-effort cancel the run before polling gives up, swallowing all transport errors."""
        if not run_id:
            return
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=2.0),
                proxy=None,
                trust_env=False,
            ) as client:
                await client.post(
                    f"{base}/runs/{run_id}/cancel",
                    json={"reason": f"chat tool '{tool_name}' polling deadline exceeded"},
                )
        except (httpx.HTTPError, OSError, ValueError):
            pass

    @staticmethod
    def _extract_chat_tool_payload(export_json: object) -> object:
        """Extract trigger_response business data from the run export response.

        The export response is ``{"items": [ExportItem, ...], "next_after": ...}``;
        a trigger_response item's json (serialization alias, also accepting json_data) is
        the finish envelope returned by host.trigger (ok()/finish() shape). Its ``data``
        field is the business payload for ``_build_mcp_tool_payload``. If trigger_response
        is absent, fall back to the first item containing json.
        """
        if not isinstance(export_json, dict):
            return None
        items = export_json.get("items")
        if not isinstance(items, list):
            return None
        fallback: object = None
        for item in items:
            if not isinstance(item, dict):
                continue
            raw = item.get("json")
            if raw is None:
                raw = item.get("json_data")
            if not isinstance(raw, dict):
                continue
            metadata = item.get("metadata")
            is_trigger_response = item.get("label") == "trigger_response" or (
                isinstance(metadata, dict) and metadata.get("kind") == "trigger_response"
            )
            if is_trigger_response:
                return raw.get("data")
            if fallback is None:
                fallback = raw.get("data")
        return fallback
    
    def _init_gateway_core(self) -> None:
        """Initialize Gateway Core components."""
        # Routing engine with a callback notifying the frontend of dynamic-entry changes.
        self._route_engine = MCPRouteEngine(
            mcp_clients=self._clients,
            logger=self.ctx.logger,  # type: ignore[arg-type]
            on_tool_register=self._on_tool_register,
            on_tool_unregister=self._on_tool_unregister,
        )
        self._route_engine.rebuild_tool_index()
        
        # Request normalizer.
        self._normalizer = MCPRequestNormalizer()
        
        # Response serializer.
        self._serializer = MCPResponseSerializer()
        
        # Plugin invoker.
        self._invoker = MCPPluginInvoker(
            mcp_clients=self._clients,
            plugin_call_fn=self._call_neko_plugin,
            logger=self.ctx.logger,  # type: ignore[arg-type]
        )

        # Policy engine.
        self._policy = DefaultPolicyEngine()

        # Unified Gateway Core orchestrator (P0 convergence).
        self._gateway_core = AdapterGatewayCore(
            transport=_MCPInternalTransport(),  # gateway_invoke uses handle_envelope without relying on transport polling.
            normalizer=self._normalizer,
            policy=self._policy,
            router=self._route_engine,
            invoker=self._invoker,
            serializer=self._serializer,
        )
        
        self.ctx.logger.info("Gateway Core components initialized")
    
    def _call_neko_plugin(
        self,
        plugin_id: str,
        entry_id: str,
        params: dict[str, object],
        timeout_s: float = 30.0,
    ) -> object:
        """
        Invoke a NEKO plugin entry.
        
        Callback for MCPPluginInvoker.
        Return a coroutine for the caller to await.
        """
        # Use PluginContext capabilities to call other plugins.
        # Note: trigger_plugin_event automatically detects the environment and returns a coroutine inside an event loop.
        return self.ctx.trigger_plugin_event(
            target_plugin_id=plugin_id,
            event_type="adapter_call",
            event_id=entry_id,
            params=dict(params),  # Convert to Dict[str, Any].
            timeout=float(timeout_s),
        )

    async def _persist_servers_config(self, servers_config: Dict[str, object]) -> None:
        """Persist the full MCP server map through the runtime-supported config API."""
        current = self._servers_config if isinstance(self._servers_config, dict) else {}
        remove_names = [name for name in current.keys() if name not in servers_config]

        updates: Dict[str, object] = {"mcp_servers": {}}
        mcp_updates = updates["mcp_servers"]
        if not isinstance(mcp_updates, dict):  # pragma: no cover - defensive guard
            raise TypeError("mcp_servers update payload must be a dict")

        for name in remove_names:
            mcp_updates[name] = self._CONFIG_DELETE_MARKER
        for name, server_cfg in servers_config.items():
            mcp_updates[name] = copy.deepcopy(server_cfg)

        await self.ctx.update_own_config(updates)

    def _coerce_timeout(self, value: object, default: float) -> float:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            timeout = float(value)
        elif isinstance(value, str):
            try:
                timeout = float(value.strip())
            except ValueError:
                return default
        else:
            return default
        return timeout if timeout > 0 else default

    def _coerce_bool(self, value: object, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return default

    def _coerce_int(self, value: object, default: int, *, minimum: int | None = None) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            result = value
        elif isinstance(value, float):
            result = int(value)
        elif isinstance(value, str):
            try:
                result = int(value.strip())
            except ValueError:
                return default
        else:
            return default
        if minimum is not None and result < minimum:
            return minimum
        return result

    def _normalize_server_config_payload(
        self,
        *,
        transport: str,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        url: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        enabled: bool = True,
        inject_to_chat: bool = False,
    ) -> Dict[str, object]:
        server_cfg: Dict[str, object] = {
            "transport": transport,
            "enabled": enabled,
            "inject_to_chat": inject_to_chat,
        }
        if command:
            server_cfg["command"] = command
        if args:
            server_cfg["args"] = list(args)
        if url:
            server_cfg["url"] = url
        if env:
            server_cfg["env"] = dict(env)
        if headers:
            server_cfg["headers"] = dict(headers)
        return server_cfg

    def _is_same_server_config(self, current: object, incoming: Dict[str, object]) -> bool:
        if not isinstance(current, dict):
            return False
        current_headers = current.get("headers")
        normalized_current = self._normalize_server_config_payload(
            transport=str(current.get("transport", "")),
            command=str(current["command"]) if isinstance(current.get("command"), str) else None,
            args=list(current["args"]) if isinstance(current.get("args"), list) else None,
            url=str(current["url"]) if isinstance(current.get("url"), str) else None,
            env=dict(current["env"]) if isinstance(current.get("env"), dict) else None,
            headers=dict(current_headers) if isinstance(current_headers, dict) else None,
            enabled=self._coerce_bool(current.get("enabled", True), True),
            inject_to_chat=self._coerce_bool(current.get("inject_to_chat", False), False),
        )
        return normalized_current == incoming

    def _truncate_llm_text(self, text: str, limit: int | None = None) -> str:
        # `limit` is in tiktoken tokens (o200k_base). Default 1000 ≈ 1400 CJK
        # chars or ~4000 English chars under the current encoding. Sync because
        # callers are sync; truncate_to_tokens handles tiktoken-unavailable
        # fallback. Reserves token room for the trailing "..." so the result
        # fits limit.
        from utils.tokenize import count_tokens, truncate_to_tokens
        if limit is None:
            from config import MCP_TOOL_RESULT_MAX_TOKENS
            limit = MCP_TOOL_RESULT_MAX_TOKENS
        cleaned = text.strip()
        if count_tokens(cleaned) <= limit:
            return cleaned
        suffix = "..."
        suffix_tokens = count_tokens(suffix)
        if limit <= suffix_tokens:
            return truncate_to_tokens(cleaned, limit)
        return truncate_to_tokens(cleaned, limit - suffix_tokens) + suffix

    def _summarize_mcp_result(self, result: object) -> str:
        if result is None:
            return ""

        if isinstance(result, str):
            return self._truncate_llm_text(result)

        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("type") or "").strip().lower()
                    if item_type == "text":
                        text = str(item.get("text") or "").strip()
                        if text:
                            parts.append(text)
                            continue
                    if item_type:
                        marker = str(item.get("mimeType") or item.get("uri") or item_type).strip()
                        parts.append(f"[{marker}]")
                if parts:
                    return self._truncate_llm_text("\n".join(parts))

            structured = result.get("structuredContent")
            if isinstance(structured, (dict, list, tuple, str)):
                structured_summary = self._summarize_mcp_result(structured)
                if structured_summary:
                    return structured_summary

            for key in ("summary", "message", "text", "content"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return self._truncate_llm_text(value)

            try:
                return self._truncate_llm_text(json.dumps(result, ensure_ascii=False, indent=2))
            except Exception:
                return self._truncate_llm_text(str(result))

        if isinstance(result, (list, tuple)):
            parts = [self._summarize_mcp_result(item) for item in result]
            normalized = [part for part in parts if part]
            if normalized:
                return self._truncate_llm_text("\n".join(normalized))
            try:
                return self._truncate_llm_text(json.dumps(list(result), ensure_ascii=False, indent=2))
            except Exception:
                return self._truncate_llm_text(str(result))

        return self._truncate_llm_text(str(result))

    def _build_mcp_tool_payload(
        self,
        *,
        result: object,
        server_name: str | None = None,
        tool_name: str | None = None,
        request_id: str | None = None,
        latency_ms: float | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"result": result}
        summary = self._summarize_mcp_result(result)
        if summary:
            payload["summary"] = summary
        if server_name:
            payload["server_name"] = server_name
        if tool_name:
            payload["tool_name"] = tool_name
        if request_id:
            payload["request_id"] = request_id
        if latency_ms is not None:
            payload["latency_ms"] = latency_ms
        return payload
    
    async def _register_mcp_tools(self, server_name: str, client: MCPClient) -> None:
        """
        Register MCP tools through Gateway Core.

        Via MCPRouteEngine.register_server_tools:
        1. Update the routing engine's tool index
        2. Trigger callbacks to register dynamic entries (visible in the frontend management panel); injection-enabled
           servers register local LLM tools as pending in the callback
        3. Batch-confirm remote pending registrations with one shared deadline, preventing tool count from multiplying
           connection stalls; roll back unconfirmed registrations
        """
        if self._route_engine:
            await self._route_engine.register_server_tools(server_name, client)
        async with self._chat_tools_lock:
            await self._confirm_pending_chat_tools_locked(server_name)

    def _cancel_reconnect_task(self, server_name: str) -> bool:
        task = self._reconnect_tasks.pop(server_name, None)
        if task is None:
            return False
        task.cancel()
        return True

    def _cancel_connect_task(self, server_name: str) -> bool:
        task = self._connect_tasks.pop(server_name, None)
        cancelled = False
        if task is not None and not task.done():
            task.cancel()
            cancelled = True
        self._pending_auto_connect.pop(server_name, None)
        return cancelled
    
    async def _unregister_mcp_tools(self, server_name: str) -> None:
        """
        Unregister MCP tools through Gateway Core.
        
        Via MCPRouteEngine.unregister_server_tools:
        1. Remove the routing engine's tool index
        2. Trigger callbacks to unregister dynamic entries
        """
        if self._route_engine:
            await self._route_engine.unregister_server_tools(server_name)

    def _schedule_pending_auto_connects(self) -> None:
        for server_name, (server_cfg, timeout) in list(self._pending_auto_connect.items()):
            if self._schedule_connect_server(server_name, server_cfg, timeout):
                self._pending_auto_connect.pop(server_name, None)

    def _schedule_connect_server(self, server_name: str, server_cfg: Dict[str, object], timeout: float) -> bool:
        if self._shutdown:
            return False
        if server_name in self._clients:
            return False
        existing = self._connect_tasks.get(server_name)
        if existing is not None and not existing.done():
            return False

        self._server_states[server_name] = {
            **self._server_states.get(server_name, {}),
            "connected": False,
            "error": "Connecting...",
        }

        async def _runner() -> None:
            try:
                await self._connect_server(server_name, server_cfg, timeout)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.ctx.logger.exception(f"Background connect failed for MCP server '{server_name}': {exc}")
                self._server_states[server_name] = {
                    **self._server_states.get(server_name, {}),
                    "connected": False,
                    "error": str(exc),
                }
            finally:
                current = self._connect_tasks.get(server_name)
                if current is asyncio.current_task():
                    self._connect_tasks.pop(server_name, None)

        self._connect_tasks[server_name] = asyncio.create_task(_runner())
        return True
    
    @lifecycle(id="shutdown")
    async def on_shutdown(self):
        """Disconnect all connections on plugin shutdown"""
        self.ctx.logger.info("MCP Adapter shutting down...")
        self._shutdown = True

        self._pending_auto_connect.clear()

        for task in self._connect_tasks.values():
            task.cancel()
        self._connect_tasks.clear()

        # Cancel all reconnection tasks.
        for task in self._reconnect_tasks.values():
            task.cancel()
        self._reconnect_tasks.clear()
        
        # Disconnect all connections.
        for server_name, client in list(self._clients.items()):
            try:
                await client.disconnect()
                self.ctx.logger.info(f"Disconnected from MCP server: {server_name}")
            except Exception as e:
                self.ctx.logger.warning(f"Error disconnecting from {server_name}: {e}")
        
        self._clients.clear()
        self._chat_tools.clear()
        self._pending_chat_tools.clear()
        self._gateway_core = None
        self._policy = None

        # Clean up the Adapter base class.
        await self.adapter_shutdown()
    
    async def _on_server_disconnect(self, server_name: str) -> None:
        """Server disconnect callback for automatic reconnection"""
        if self._shutdown:
            return
        
        self.ctx.logger.warning(f"MCP server '{server_name}' disconnected")
        
        # Update status.
        self._server_states[server_name] = {
            **self._server_states.get(server_name, {}),
            "connected": False,
            "error": "Connection lost",
        }
        
        # Unregister MCP tools.
        await self._unregister_mcp_tools(server_name)
        
        # Remove from clients.
        if server_name in self._clients:
            del self._clients[server_name]
        
        # Start a reconnection task if automatic reconnection is enabled.
        if self._auto_reconnect and server_name not in self._reconnect_tasks:
            self._reconnect_tasks[server_name] = asyncio.create_task(
                self._reconnect_server(server_name)
            )
    
    async def _reconnect_server(self, server_name: str) -> None:
        """Attempt to reconnect a server"""
        try:
            server_cfg = self._servers_config.get(server_name)
            if not server_cfg:
                self.ctx.logger.warning(f"No config found for server '{server_name}', cannot reconnect")
                return
            
            attempts = 0
            while not self._shutdown and attempts < self._max_reconnect_attempts:
                attempts += 1
                self.ctx.logger.info(
                    f"Attempting to reconnect to MCP server '{server_name}' "
                    f"(attempt {attempts}/{self._max_reconnect_attempts})"
                )
                
                # Update status.
                self._server_states[server_name] = {
                    **self._server_states.get(server_name, {}),
                    "reconnect_attempts": attempts,
                }
                
                # Wait for the reconnection interval.
                await asyncio.sleep(self._reconnect_interval)
                
                if self._shutdown:
                    break
                
                # Attempt reconnection.
                config = await self.config.dump()
                adapter_config = config.get("mcp_adapter", {})
                timeout = self._coerce_timeout(adapter_config.get("connect_timeout", 30), 30.0)
                
                if await self._connect_server(server_name, server_cfg, timeout):
                    self.ctx.logger.info(f"Successfully reconnected to MCP server '{server_name}'")
                    break
            else:
                if not self._shutdown:
                    self.ctx.logger.error(
                        f"Failed to reconnect to MCP server '{server_name}' "
                        f"after {self._max_reconnect_attempts} attempts"
                    )
                    self._server_states[server_name] = {
                        **self._server_states.get(server_name, {}),
                        "connected": False,
                        "error": f"Reconnection failed after {self._max_reconnect_attempts} attempts",
                    }
        finally:
            self._reconnect_tasks.pop(server_name, None)
    
    async def _connect_server(
        self,
        server_name: str,
        server_cfg: Dict[str, object],
        timeout: float = 30.0
    ) -> bool:
        """Connect to one MCP server"""
        client: MCPClient | None = None
        try:
            if server_name not in self._servers_config:
                self.ctx.logger.info(f"Skip connecting removed MCP server '{server_name}'")
                return False
            timeout = self._coerce_timeout(timeout, 30.0)
            # Extract configuration fields and convert their types.
            transport_raw = server_cfg.get("transport", "stdio")
            transport = str(transport_raw) if transport_raw else "stdio"
            
            command_raw = server_cfg.get("command")
            command = str(command_raw) if command_raw else None
            
            args_raw = server_cfg.get("args", [])
            args = list(args_raw) if isinstance(args_raw, (list, tuple)) else []
            
            url_raw = server_cfg.get("url")
            url = str(url_raw) if url_raw else None
            
            env_raw = server_cfg.get("env", {})
            env = dict(env_raw) if isinstance(env_raw, dict) else {}
            
            headers_raw = server_cfg.get("headers", {})
            headers = dict(headers_raw) if isinstance(headers_raw, dict) else {}
            
            enabled = self._coerce_bool(server_cfg.get("enabled", True), True)
            
            config = MCPServerConfig(
                name=server_name,
                transport=transport,
                command=command,
                args=[str(a) for a in args],
                url=url,
                env={str(k): str(v) for k, v in env.items()},
                headers={str(k): str(v) for k, v in headers.items()},
                enabled=enabled,
            )
            
            client = MCPClient(config, logger=self.ctx.logger)
            
            # Set the disconnect callback for automatic reconnection.
            client.set_disconnect_callback(self._on_server_disconnect)
            
            if await client.connect(timeout=timeout):
                if server_name not in self._servers_config:
                    await client.disconnect()
                    self.ctx.logger.info(f"Discarded connection for removed MCP server '{server_name}'")
                    return False
                self._clients[server_name] = client
                client._reconnect_attempts = 0  # Reset the reconnection counter.
                
                # Register tools through Gateway Core.
                try:
                    await self._register_mcp_tools(server_name, client)
                except Exception:
                    self._clients.pop(server_name, None)
                    await client.disconnect()
                    raise
                
                # Update status.
                self._server_states[server_name] = {
                    "connected": True,
                    "tools_count": len(client.tools),
                    "tools": [t.name for t in client.tools],
                    "reconnect_attempts": 0,
                }
                
                self.ctx.logger.info(
                    f"Connected to MCP server '{server_name}' with {len(client.tools)} tools"
                )
                return True
            else:
                self._server_states[server_name] = {
                    "connected": False,
                    "error": "Connection failed",
                }
                return False
        except asyncio.CancelledError:
            if client is not None and self._clients.get(server_name) is not client:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            raise
        except Exception as e:
            self.ctx.logger.exception(f"Failed to connect to MCP server '{server_name}': {e}")
            self._server_states[server_name] = {
                "connected": False,
                "error": str(e),
            }
            return False
    
    @plugin_entry(
        id="list_servers",
        name=tr("entries.listServers.name", default="List MCP Servers"),
        description=tr("entries.listServers.description", default="List all configured MCP servers and their status."),
        llm_result_fields=["total"],
    )
    async def list_servers(self, **_):
        """List all MCP servers"""
        servers = []
        seen_names = set()
        
        # Connected servers.
        for server_name, client in self._clients.items():
            seen_names.add(server_name)
            servers.append({
                "name": server_name,
                "connected": client.connected,
                "transport": client.config.transport,
                "tools_count": len(client.tools),
                "inject_to_chat": self._is_chat_injection_enabled(server_name),
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description,
                    }
                    for t in client.tools
                ],
            })
        
        # Servers with status but no connection.
        config = await self.config.dump()
        servers_config = config.get("mcp_servers", {})
        
        for server_name, state in self._server_states.items():
            if server_name not in seen_names:
                seen_names.add(server_name)
                # Get transport information from configuration.
                transport = "unknown"
                if server_name in servers_config:
                    cfg = servers_config[server_name]
                    if isinstance(cfg, dict):
                        transport = str(cfg.get("transport", "stdio"))
                servers.append({
                    "name": server_name,
                    "connected": False,
                    "transport": transport,
                    "error": state.get("error"),
                    "inject_to_chat": self._is_chat_injection_enabled(server_name),
                })
        
        # Configured servers with no connection attempt yet.
        self.ctx.logger.debug(f"list_servers: config has {len(servers_config)} servers: {list(servers_config.keys())}")
        for server_name, server_cfg in servers_config.items():
            if server_name not in seen_names:
                transport = "unknown"
                if isinstance(server_cfg, dict):
                    transport = str(server_cfg.get("transport", "stdio"))
                servers.append({
                    "name": server_name,
                    "connected": False,
                    "transport": transport,
                    "configured": True,
                    "inject_to_chat": self._is_chat_injection_enabled(server_name),
                })
        
        return Ok({"servers": servers, "total": len(servers)})
    
    @ui.action(label=tr("actions.connect.label", default="Connect"), tone="primary", group="server", order=10, refresh_context=True)
    @plugin_entry(
        id="connect_server",
        name=tr("entries.connect.name", default="Connect MCP Server"),
        description=tr("entries.connect.description", default="Connect to a configured MCP server."),
        llm_result_fields=["message"],
        input_schema={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": tr("entries.connect.fields.server_name.description", default="Server name from config")
                }
            },
            "required": ["server_name"]
        }
    )
    async def connect_server(self, server_name: str, **_):
        """Connect to the specified MCP server"""
        if server_name in self._clients:
            return Err(SdkError(f"Server '{server_name}' is already connected"))
        
        # Get server configuration.
        config = await self.config.dump()
        servers_config = config.get("mcp_servers", {})
        
        if server_name not in servers_config:
            return Err(SdkError(f"Server '{server_name}' not found in config"))
        
        server_cfg = servers_config[server_name]
        adapter_config = config.get("mcp_adapter", {})
        timeout = self._coerce_timeout(adapter_config.get("connect_timeout", 30), 30.0)
        scheduled = self._schedule_connect_server(server_name, server_cfg, timeout)
        return Ok({
            "message": f"Connection {'scheduled' if scheduled else 'already pending'} for server '{server_name}'",
            "connecting": scheduled or server_name in self._connect_tasks,
        })
    
    @ui.action(label=tr("actions.disconnect.label", default="Disconnect"), tone="warning", group="server", order=20, refresh_context=True)
    @plugin_entry(
        id="disconnect_server",
        name=tr("entries.disconnect.name", default="Disconnect MCP Server"),
        description=tr("entries.disconnect.description", default="Disconnect from a configured MCP server."),
        llm_result_fields=["message"],
        input_schema={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": tr("entries.common.fields.server_name.description", default="Server name")
                }
            },
            "required": ["server_name"]
        }
    )
    async def disconnect_server(self, server_name: str, **_):
        """Disconnect from the specified MCP server"""
        if server_name not in self._clients:
            return Err(SdkError(f"Server '{server_name}' is not connected"))

        self._cancel_reconnect_task(server_name)
        
        # Unregister MCP tools.
        await self._unregister_mcp_tools(server_name)
        
        # Disconnect.
        client = self._clients.pop(server_name)
        await client.disconnect()
        
        # Update status.
        self._server_states[server_name] = {
            "connected": False,
            "disconnected_manually": True,
        }

        return Ok({"message": f"Disconnected from server '{server_name}'"})

    @ui.action(label=tr("actions.setChatInjection.label", default="Toggle Chat Injection"), tone="primary", group="server", order=15, refresh_context=True)
    @plugin_entry(
        id="set_chat_injection",
        name=tr("entries.setChatInjection.name", default="Set MCP Chat Injection"),
        description=tr("entries.setChatInjection.description", default="Enable or disable injecting an MCP server's tools into the chat context."),
        llm_result_fields=["message"],
        input_schema={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": tr("entries.common.fields.server_name.description", default="Server name")
                },
                "inject_to_chat": {
                    "type": "boolean",
                    "description": tr("entries.setChatInjection.fields.inject.description", default="Whether to inject this server's tools into the chat context")
                }
            },
            "required": ["server_name", "inject_to_chat"]
        }
    )
    async def set_chat_injection(self, server_name: str, inject_to_chat: bool, **_):
        """Configure whether one MCP server injects tools into chat context"""
        async with self._servers_config_lock:
            inject_flag = self._coerce_bool(inject_to_chat, False)
            # Hold the lock throughout persistence + application: concurrent enable/disable interleaving could
            # overwrite registration mappings or let disable remove results while enable is registering.
            # config.dump also stays under the lock. Rollback dumps fresh configuration before persisting, preventing
            # stale dump deletion semantics from accidentally removing concurrent add_server changes.
            async with self._chat_tools_lock:
                config = await self.config.dump()
                servers_config = config.get("mcp_servers", {})
                server_cfg = servers_config.get(server_name)
                if not isinstance(server_cfg, dict):
                    return Err(SdkError(f"Server '{server_name}' not found in config"))

                previous_flag = self._is_chat_injection_enabled(server_name)
                server_cfg["inject_to_chat"] = inject_flag
                try:
                    await self._persist_servers_config(servers_config)
                except Exception as exc:
                    self.ctx.logger.exception(
                        f"Failed to persist chat injection flag for MCP server '{server_name}': {exc}"
                    )
                    return Err(SdkError(f"Failed to save server config: {exc}"))
                self._servers_config = servers_config

                client = self._clients.get(server_name)
                applied = 0
                failed_tool_ids: List[str] = []
                expected_tool_ids: set[str] = set()
                if inject_flag and client is not None:
                    for tool in client.tools:
                        tool_id = f"mcp_{server_name}_{tool.name}"
                        if self._route_engine is None or self._route_engine.get_tool_server(tool_id) != server_name:
                            continue
                        expected_tool_ids.add(tool_id)
                        if tool_id in self._chat_tools or tool_id in self._pending_chat_tools:
                            continue
                        await self._register_chat_tool_local_locked(
                            tool_id=tool_id, server_name=server_name, tool_name=tool.name,
                            description=tool.description or f"MCP tool from {server_name}", schema=tool.input_schema,
                        )
                elif not inject_flag:
                    mapped_ids = [
                        tid for mapping in (self._chat_tools, self._pending_chat_tools)
                        for tid, info in mapping.items() if info.get("server_name") == server_name
                    ]
                    for tool_id in dict.fromkeys(mapped_ids):
                        outcome = await self._unregister_chat_tool_locked(tool_id)
                        if outcome == "removed":
                            applied += 1
                        elif outcome == "failed":
                            failed_tool_ids.append(tool_id)

                if inject_flag:
                    expected_tool_ids.update(
                        tid for tid, info in self._pending_chat_tools.items()
                        if info.get("server_name") == server_name
                    )
                    applied = await self._confirm_pending_chat_tools_locked(server_name)
                    unconfirmed = expected_tool_ids - self._chat_tools.keys()
                    if unconfirmed:
                        # A failed enable must surface an error. When enabling
                        # from disabled, remove the partial registration before
                        # reverting the switch; preserve retry state on failure.
                        rollback_flag = previous_flag
                        if not previous_flag:
                            mapped_ids = [
                                tid for mapping in (self._chat_tools, self._pending_chat_tools)
                                for tid, info in mapping.items()
                                if info.get("server_name") == server_name
                            ]
                            for tool_id in dict.fromkeys(mapped_ids):
                                if await self._unregister_chat_tool_locked(tool_id) == "failed":
                                    rollback_flag = True
                        server_cfg["inject_to_chat"] = rollback_flag
                        try:
                            await self._persist_servers_config(servers_config)
                        except Exception as exc:
                            self.ctx.logger.exception(f"Failed to roll back chat injection: {exc}")
                            return Err(SdkError("Chat injection confirmation and config rollback failed; retry later"))
                        return Err(SdkError(
                            f"Failed to enable chat injection for server '{server_name}': "
                            f"{len(unconfirmed)} tool(s) not confirmed; retry later"
                        ))

                if not inject_flag and failed_tool_ids:
                    # Disable cleanup incomplete (e.g. remote temporarily unreachable): roll back the flag so the panel truthfully
                    # shows still enabled, retaining failed tools' mappings for retry. Rollback uses a fresh dump,
                    # preventing stale deletion semantics from erasing concurrent unlocked add/remove changes.
                    if previous_flag != inject_flag:
                        fresh_config = await self.config.dump()
                        fresh_servers = fresh_config.get("mcp_servers", {})
                        if isinstance(fresh_servers.get(server_name), dict):
                            fresh_servers[server_name]["inject_to_chat"] = previous_flag
                            try:
                                await self._persist_servers_config(fresh_servers)
                            except Exception:
                                self.ctx.logger.exception(
                                    f"Failed to roll back chat injection flag for MCP server '{server_name}'"
                                )
                        self._servers_config.get(server_name, {})["inject_to_chat"] = previous_flag
                    self.ctx.logger.warning(
                        f"Chat injection cleanup incomplete for server '{server_name}': "
                        f"{len(failed_tool_ids)} tool(s) still active"
                    )
                    return Err(SdkError(
                        f"Failed to disable chat injection for server '{server_name}': "
                        f"{len(failed_tool_ids)} tool(s) still active (remote unregister not confirmed); retry later"
                    ))

                state_text = "enabled" if inject_flag else "disabled"
                return Ok({
                    "message": f"Chat injection {state_text} for server '{server_name}' ({applied} tool(s) updated)",
                    "server_name": server_name,
                    "inject_to_chat": inject_flag,
                    "connected": client is not None,
                    "applied": applied,
                })
    
    @ui.action(label=tr("actions.addServer.label", default="Add Server"), tone="success", group="server", order=5, refresh_context=True)
    @plugin_entry(
        id="add_server",
        name=tr("entries.addServer.name", default="Add MCP Server"),
        description=tr("entries.addServer.description", default="Add a new MCP server configuration."),
        llm_result_fields=["message"],
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": tr("entries.addServer.fields.name.description", default="Server name, used as a unique identifier")
                },
                "transport": {
                    "type": "string",
                    "enum": ["stdio", "sse", "streamable-http"],
                    "description": tr("entries.addServer.fields.transport.description", default="Transport type")
                },
                "command": {
                    "type": "string",
                    "description": tr("entries.addServer.fields.command.description", default="Command to run for stdio transport")
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": tr("entries.addServer.fields.args.description", default="Command arguments")
                },
                "url": {
                    "type": "string",
                    "description": tr("entries.addServer.fields.url.description", default="Server URL for SSE or HTTP transport")
                },
                "env": {
                    "type": "object",
                    "description": tr("entries.addServer.fields.env.description", default="Environment variables")
                },
                "headers": {
                    "type": "object",
                    "description": tr("entries.addServer.fields.headers.description", default="Custom HTTP headers for SSE/HTTP transport")
                },
                "enabled": {
                    "type": "boolean",
                    "description": tr("entries.addServer.fields.enabled.description", default="Whether to enable this server")
                },
                "inject_to_chat": {
                    "type": "boolean",
                    "description": tr("entries.setChatInjection.fields.inject.description", default="Whether to inject this server's tools into the chat context")
                },
                "auto_connect": {
                    "type": "boolean",
                    "description": tr("entries.addServer.fields.autoConnect.description", default="Whether to connect immediately")
                }
            },
            "required": ["name", "transport"]
        }
    )
    async def add_server(
        self,
        name: str,
        transport: str,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        url: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        enabled: bool = True,
        inject_to_chat: bool = False,
        auto_connect: bool = True,
        **_
    ):
        """Add new MCP server configuration"""
        async with self._servers_config_lock:
            # Check whether it already exists.
            config = await self.config.dump()
            servers_config = config.get("mcp_servers", {})

            # Validate configuration.
            if transport == "stdio" and not command:
                return Err(SdkError("Command is required for stdio transport"))
            if transport in ("sse", "streamable-http") and not url:
                return Err(SdkError("URL is required for sse/http transport"))

            # Build configuration.
            server_cfg = self._normalize_server_config_payload(
                transport=transport,
                command=command,
                args=args,
                url=url,
                env=env,
                headers=headers,
                enabled=enabled,
                inject_to_chat=self._coerce_bool(inject_to_chat, False),
            )

            existing_cfg = servers_config.get(name)
            if existing_cfg is not None:
                if self._is_same_server_config(existing_cfg, server_cfg):
                    self.ctx.logger.info(f"Server '{name}' already exists with identical config")
                    if auto_connect and enabled and name not in self._clients:
                        adapter_config = config.get("mcp_adapter", {})
                        timeout_val = self._coerce_timeout(adapter_config.get("connect_timeout", 30), 30.0)
                        scheduled = self._schedule_connect_server(name, server_cfg, timeout_val)
                        return Ok({
                            "message": f"Server '{name}' already exists; connection {'scheduled' if scheduled else 'already pending'}",
                            "already_exists": True,
                            "connecting": scheduled or name in self._connect_tasks,
                        })
                    return Ok({
                        "message": f"Server '{name}' already exists",
                        "already_exists": True,
                        "connected": name in self._clients,
                    })
                return Err(SdkError(f"Server '{name}' already exists with different config"))
        
            # Save to configuration.
            servers_config[name] = server_cfg
            self.ctx.logger.info(f"Saving mcp_servers config: {list(servers_config.keys())}")
            try:
                await self._persist_servers_config(servers_config)
            except Exception as exc:
                self.ctx.logger.exception(
                    f"Failed to persist MCP server config while adding server '{name}' "
                    f"(transport={transport}): {exc}"
                )
                return Err(SdkError(f"Failed to save server config: {exc}"))
        
            # Cache configuration.
            self._servers_config = servers_config
            self.ctx.logger.info(f"Server '{name}' added to config")
        
            # If automatic connection is required.
            if auto_connect and enabled:
                adapter_config = config.get("mcp_adapter", {})
                timeout_val = self._coerce_timeout(adapter_config.get("connect_timeout", 30), 30.0)
                self._schedule_connect_server(name, server_cfg, timeout_val)
                return Ok({
                    "message": f"Added server '{name}' and scheduled connection",
                    "connected": False,
                    "connecting": True,
                })
        
            return Ok({"message": f"Added server '{name}'"})
    
    @ui.action(label=tr("actions.removeServers.label", default="Remove Server"), tone="danger", group="server", order=30, confirm=tr("actions.removeServers.confirm", default="Remove these MCP Servers?"), refresh_context=True)
    @plugin_entry(
        id="remove_servers",
        name=tr("entries.removeServers.name", default="Remove MCP Servers"),
        description=tr("entries.removeServers.description", default="Remove one or more MCP server configurations."),
        llm_result_fields=["message"],
        input_schema={
            "type": "object",
            "properties": {
                "server_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": tr("entries.removeServers.fields.server_names.description", default="List of server names to remove")
                }
            },
            "required": ["server_names"]
        }
    )
    async def remove_servers(self, server_names: List[str], **_):
        """Remove MCP server configurations in a batch"""
        async with self._servers_config_lock:
            config = await self.config.dump()
            servers_config = config.get("mcp_servers", {})

            removed = []
            not_found = []

            async with self._chat_tools_lock:
                mapped_ids = [
                    tid for mapping in (self._chat_tools, self._pending_chat_tools)
                    for tid, info in mapping.items()
                    if info.get("server_name") in server_names
                ]
                for tool_id in dict.fromkeys(mapped_ids):
                    if await self._unregister_chat_tool_locked(tool_id) == "failed":
                        return Err(SdkError("Failed to remove servers: chat tool cleanup incomplete; retry later"))

            for name in server_names:
                if name not in servers_config:
                    not_found.append(name)
                    continue

                self._cancel_connect_task(name)
                self._cancel_reconnect_task(name)
                if name in self._clients:
                    await self._unregister_mcp_tools(name)
                    client = self._clients.pop(name)
                    await client.disconnect()

                # Remove from configuration.
                del servers_config[name]
            
                # Clean up status.
                if name in self._server_states:
                    del self._server_states[name]
            
                removed.append(name)
        
            self.ctx.logger.info(f"Saving updated mcp_servers config: {list(servers_config.keys())}")
            try:
                await self._persist_servers_config(dict(servers_config))
            except Exception as exc:
                self.ctx.logger.exception(
                    "Failed to persist MCP server config while removing servers "
                    f"(requested={len(server_names)}, removed={len(removed)}): {exc}"
                )
                return Err(SdkError(f"Failed to save server config: {exc}"))
            self._servers_config = servers_config
        
            return Ok({
                "removed": removed,
                "not_found": not_found,
                "message": f"Removed {len(removed)} server(s)",
            })
    
    @plugin_entry(
        id="call_tool",
        name=tr("entries.callTool.name", default="Call MCP Tool"),
        description=tr("entries.callTool.description", default="Call a tool exposed by a configured MCP server."),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": tr("entries.common.fields.server_name.description", default="Server name")
                },
                "tool_name": {
                    "type": "string",
                    "description": tr("entries.common.fields.tool_name.description", default="Tool name")
                },
                "arguments": {
                    "type": "object",
                    "description": tr("entries.common.fields.arguments.description", default="Tool arguments")
                }
            },
            "required": ["server_name", "tool_name"]
        }
    )
    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Optional[Dict[str, object]] = None,
        **_
    ):
        """Invoke an MCP tool"""
        if server_name not in self._clients:
            return Err(SdkError(f"Server '{server_name}' is not connected"))
        
        client = self._clients[server_name]
        
        config = await self.config.dump()
        adapter_config = config.get("mcp_adapter", {})
        timeout_val = self._coerce_timeout(adapter_config.get("tool_timeout", 60), 60.0)
        result = await client.call_tool(tool_name, arguments or {}, timeout=timeout_val)
        
        if "error" in result:
            error_msg = str(result["error"]) if result["error"] else "Unknown error"
            return Err(SdkError(error_msg))

        return Ok(
            self._build_mcp_tool_payload(
                result=result.get("result", {}),
                server_name=server_name,
                tool_name=tool_name,
            )
        )
    
    @plugin_entry(
        id="list_tools",
        name=tr("entries.listTools.name", default="List MCP Tools"),
        description=tr("entries.listTools.description", default="List all available MCP tools."),
        llm_result_fields=["total"],
    )
    async def list_tools(self, server_name: Optional[str] = None, **_):
        """List all MCP tools"""
        tools = []
        
        for name, client in self._clients.items():
            if server_name and name != server_name:
                continue
            
            for tool in client.tools:
                tools.append({
                    "server": name,
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                    "entry_id": f"mcp_{name}_{tool.name}",
                })
        
        return Ok({"tools": tools, "total": len(tools)})
    
    @plugin_entry(
        id="gateway_invoke",
        name=tr("entries.gatewayInvoke.name", default="Gateway Invoke"),
        description=tr("entries.gatewayInvoke.description", default="Call an MCP tool through Gateway Core."),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": tr("entries.common.fields.tool_name.description", default="Tool name")
                },
                "arguments": {
                    "type": "object",
                    "description": tr("entries.common.fields.arguments.description", default="Tool arguments")
                },
                "target_plugin_id": {
                    "type": "string",
                    "description": tr("entries.gatewayInvoke.fields.targetPluginId.description", default="Optional target N.E.K.O plugin ID")
                },
                "timeout_s": {
                    "type": "number",
                    "description": tr("entries.gatewayInvoke.fields.timeout.description", default="Optional timeout in seconds for the downstream call")
                }
            },
            "required": ["tool_name"]
        }
    )
    async def gateway_invoke(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, object]] = None,
        target_plugin_id: Optional[str] = None,
        timeout_s: Optional[float] = None,
        **_
    ):
        """
        Invoke an MCP tool or NEKO plugin through Gateway Core.
        
        Unified entry point for the new architecture, using Gateway Core components to process requests.
        """
        import uuid
        if self._gateway_core is None:
            return Err(SdkError("Gateway Core components not initialized"))
        
        # Construct ExternalRequest.
        request_id = str(uuid.uuid4())
        try:
            payload: dict[str, object] = {
                "name": tool_name,
                "arguments": arguments or {},
                "target_plugin_id": target_plugin_id,
            }
            if timeout_s is not None:
                # bool is a subclass of int in Python; reject it explicitly to avoid
                # True/False being silently coerced to 1.0/0.0.
                if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
                    raise TypeError(f"timeout_s must be a number, got {type(timeout_s).__name__}")
                if timeout_s <= 0:
                    raise ValueError(f"timeout_s must be positive, got {timeout_s}")
                payload["timeout_s"] = float(timeout_s)
            envelope = ExternalRequest(
                protocol="mcp",
                connection_id="neko_internal",
                request_id=request_id,
                action="tool_call",
                payload=payload,
                metadata={},
            )
            response_result = await self._gateway_core.handle_request(envelope)
        except Exception as exc:
            self.ctx.logger.exception(f"Gateway invoke raised unexpected exception: {exc}")
            return Err(SdkError(str(exc)))

        if isinstance(response_result, Err):
            self.ctx.logger.warning(f"Gateway invoke failed before response build: {response_result.error}")
            return Err(SdkError(str(response_result.error)))

        response = response_result.value

        if response.success:
            return Ok(
                self._build_mcp_tool_payload(
                    result=response.data,
                    tool_name=tool_name,
                    request_id=response.request_id,
                    latency_ms=response.latency_ms,
                )
            )

        error_code = "GATEWAY_ERROR"
        error_msg = "gateway invocation failed"
        if response.error is not None:
            error_code = response.error.code
            error_msg = response.error.message
        self.ctx.logger.warning(
            "Gateway invoke failed: code={}, msg={}, request_id={}, latency_ms={}",
            error_code,
            error_msg,
            response.request_id,
            response.latency_ms,
        )
        return Err(SdkError(error_msg))
