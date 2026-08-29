"""
MCP Adapter Plugin

MCP (Model Context Protocol) Router - 连接 MCP servers 并将其 tools 暴露为 NEKO entries。

功能：
1. 管理多个 MCP server 连接
2. 自动发现 MCP server 的 tools
3. 将 tools 动态注册为 NEKO entries
4. 提供统一的工具调用接口
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
from utils.aiohttp_proxy_utils import aiohttp_session_kwargs_for_url

# 聊天注入（LLM tool）路径经由 POST /runs 产生运行记录，再轮询到终态后取回结果。
# 这些常量约束该转发链路的超时预算：LLM tool 的注册超时 = MCP tool 超时 + 转发余量，
# 并受 main_server /api/tools/register 的 300s 上限约束。
_LLM_TOOL_TIMEOUT_SLACK_S = 15.0
_LLM_TOOL_TIMEOUT_CAP_S = 300.0
_RUN_POLL_INTERVAL_S = 0.5
_RUN_TERMINAL_STATUSES = frozenset(("succeeded", "failed", "canceled", "timeout"))
# 跨插件 LLM tool 名查重缓存 TTL；序号去重的上限（防极端场景无界循环）
_FOREIGN_NAME_CACHE_TTL_S = 5.0
_LLM_TOOL_NAME_MAX_SUFFIX = 100


class _MCPInternalTransport:
    """
    内部直调 transport。

    gateway_invoke 走 handle_envelope 直调，不依赖 recv/send 轮询。
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
    """MCP Server 配置"""
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
    """MCP Tool 信息"""
    name: str
    description: str
    input_schema: Dict[str, object]
    server_name: str


@dataclass
class MCPServerConnection:
    """MCP Server 连接状态"""
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
    """MCP Client - 管理与 MCP Server 的通信"""
    
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
        # 重连配置
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
        """连接到 MCP Server"""
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
        """通过 stdio 连接到 MCP Server"""
        try:
            if not self.config.command:
                raise ValueError("Command is required for stdio transport")
            
            # 准备环境变量
            env = os.environ.copy()
            env.update(self.config.env)
            
            # 启动进程
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
            
            # 启动读取任务
            self._read_task = asyncio.create_task(self._read_loop())
            # 启动 stderr 读取任务（避免缓冲区满导致阻塞）
            self._stderr_task = asyncio.create_task(self._read_stderr())
            
            # 发送 initialize 请求
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
            
            # 发送 initialized 通知
            await self._send_notification("notifications/initialized", {})
            
            # 获取 tools 列表
            tools_result = await asyncio.wait_for(
                self._send_request("tools/list", {}),
                timeout=timeout
            )
            
            if tools_result.get("error"):
                raise Exception(f"Failed to list tools: {tools_result['error']}")
            
            # 解析 tools
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
        """通过 HTTP/SSE 连接到 MCP Server"""
        try:
            if not self.config.url:
                raise ValueError("URL is required for HTTP/SSE transport")
            
            import aiohttp
            
            url = self.config.url.rstrip("/")
            if self.logger:
                self.logger.info(f"Connecting to MCP server '{self.config.name}' via HTTP: {url}")
            
            # 创建 HTTP session
            self._http_session = aiohttp.ClientSession(
                **aiohttp_session_kwargs_for_url(url)
            )
            
            # 发送 initialize 请求
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
            
            # MCP Streamable HTTP 需要 Accept 头
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
                # 保存 session ID（如果服务器返回）
                session_id = resp.headers.get("mcp-session-id")
                if session_id:
                    self._http_session_id = session_id
                    headers["mcp-session-id"] = session_id
            
            if "error" in init_result:
                raise ValueError(f"Initialize failed: {init_result['error']}")
            
            # 发送 initialized 通知
            notif_payload = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            }
            async with self._http_session.post(url, json=notif_payload, headers=headers) as resp:
                if resp.status >= 400:
                    raise ValueError(f"HTTP {resp.status}: {await resp.text()}")
            
            # 获取 tools 列表
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
            
            # 解析 tools
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
        """通过 legacy HTTP+SSE 连接到 MCP Server。"""
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
        """设置断开连接时的回调"""
        self._on_disconnect_callback = callback
    
    async def disconnect(self):
        """断开连接"""
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
        
        # 关闭 HTTP session
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
        
        # 取消所有待处理的请求
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(Exception("Connection closed"))
        self._pending_requests.clear()
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, object], timeout: float = 60.0) -> Dict[str, object]:
        """调用 MCP tool"""
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
        """通过 HTTP 发送 JSON-RPC 请求"""
        import aiohttp
        
        # 每次请求都创建新的 session，避免事件循环问题
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
        """执行实际的 HTTP 请求"""
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
        # 添加 session ID（如果有）
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
        """发送 JSON-RPC 请求"""
        # HTTP 传输
        if self.config.transport == "streamable-http":
            return await self._send_http_request(method, params, timeout=timeout)
        if self.config.transport == "sse":
            return await self._send_sse_request(method, params, timeout=timeout)
        
        # stdio 传输
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
        
        # 创建 Future 等待响应
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        
        try:
            # 发送消息
            data = json.dumps(message) + "\n"
            self.writer.write(data.encode())
            await self.writer.drain()
            
            # 等待响应
            return await future
        finally:
            self._pending_requests.pop(request_id, None)
    
    async def _send_notification(self, method: str, params: Dict[str, object]):
        """发送 JSON-RPC 通知（无响应）"""
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
        """读取 stderr 输出（避免缓冲区满导致进程阻塞）"""
        try:
            if not self.process or not self.process.stderr:
                return
            
            while not self._shutdown:
                line = await self.process.stderr.readline()
                if not line:
                    break
                
                # 记录 stderr 输出 — 上游 MCP 进程的 stderr 可能含敏感数据，不写 logger。
                # decode 用 errors="replace" 防止单行非 UTF-8 字节让整个 stderr
                # 协程崩掉、子进程的 stderr buffer 后续被堵死。
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
        """读取响应循环"""
        try:
            while self.reader and not self._shutdown:
                line = await self.reader.readline()
                if not line:
                    # 连接断开
                    if not self._shutdown and self.connected:
                        self.connected = False
                        if self.logger:
                            self.logger.warning(f"MCP server '{self.config.name}' connection lost")
                        # 触发断开回调
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
            # 连接异常断开
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
        """处理收到的消息"""
        request_id = message.get("id")
        
        if request_id is not None:
            # 这是一个响应
            req_id_int = int(request_id) if isinstance(request_id, (int, float, str)) else 0
            future = self._pending_requests.get(req_id_int)
            if future and not future.done():
                if "error" in message:
                    future.set_result({"error": message["error"]})
                else:
                    future.set_result({"result": message.get("result")})
        else:
            # 这是一个通知
            method = message.get("method")
            if self.logger:
                self.logger.debug(f"Received notification: {method}")


@neko_plugin
class MCPAdapterPlugin(NekoAdapterPlugin):
    """
    MCP Adapter Plugin - 真正的 Adapter 类型插件
    
    使用 Gateway Core 架构：
    - MCPRouteEngine: 路由决策
    - MCPPluginInvoker: 插件调用
    - MCPRequestNormalizer: 请求规范化
    - MCPResponseSerializer: 响应序列化
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
        # 重连配置缓存
        self._auto_reconnect = True
        self._reconnect_interval = 5
        self._max_reconnect_attempts = 3
        self._tool_timeout = 60.0
        self._servers_config: Dict[str, Dict[str, object]] = {}
        # 已注入聊天上下文的 MCP tools：tool_id -> {"llm_name", "server_name", "tool_name"}
        self._chat_tools: Dict[str, Dict[str, str]] = {}
        # 跨插件 LLM tool 名查重缓存：(fetched_at, foreign_names)
        self._foreign_llm_names_cache: Optional[tuple[float, frozenset]] = None
        
        # Gateway Core 组件
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
        """插件启动时连接所有配置的 MCP servers"""
        self.ctx.logger.info("MCP Adapter starting...")
        
        # 初始化 Adapter 基类
        await self.adapter_startup()
        
        # 加载配置
        config = await self.config.dump()
        servers_config = config.get("mcp_servers", {})
        adapter_config = config.get("mcp_adapter", {})
        
        connect_timeout = self._coerce_timeout(adapter_config.get("connect_timeout", 30), 30.0)
        
        # 缓存重连配置
        self._auto_reconnect = self._coerce_bool(adapter_config.get("auto_reconnect", True), True)
        self._reconnect_interval = self._coerce_int(adapter_config.get("reconnect_interval", 5), 5, minimum=0)
        self._max_reconnect_attempts = self._coerce_int(adapter_config.get("max_reconnect_attempts", 3), 3, minimum=0)
        self._tool_timeout = self._coerce_timeout(adapter_config.get("tool_timeout", 60), 60.0)
        self._servers_config = servers_config
        
        # 先初始化 Gateway Core 组件（需要在连接服务器之前，因为 _register_mcp_tools 依赖它）
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
        """Gateway Core 工具注册回调 - 注册为动态 entry，并按配置注入聊天上下文。"""
        # 创建工具处理器
        async def tool_handler(**kwargs: object) -> Dict[str, object]:
            # 移除 NEKO 注入的参数
            arguments = {k: v for k, v in kwargs.items() if not k.startswith("_")}

            # 获取对应的 client
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

        # 注册为动态 entry
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

        # 按该 server 的配置注入聊天上下文（LLM tool）。注入失败只记日志，
        # 不影响动态 entry 本身——原调用路径（POST /runs）依然可用。
        if registered and self._is_chat_injection_enabled(server_name):
            await self._register_chat_tool(
                tool_id=tool_id,
                server_name=server_name,
                tool_name=tool_name,
                description=description,
                schema=schema,
            )

        return registered

    async def _on_tool_unregister(self, tool_id: str) -> bool:
        """Gateway Core 工具注销回调 - 注销动态 entry 与聊天注入。"""
        await self._unregister_chat_tool(tool_id)
        return self.unregister_dynamic_entry(tool_id)

    # ------------------------------------------------------------------
    # 聊天上下文注入（LLM tool）
    # ------------------------------------------------------------------
    #
    # 通过插件 SDK 的 register_llm_tool / unregister_llm_tool（见
    # plugin/sdk/plugin/base.py）把 MCP tool 动态注入/移出聊天上下文：
    # SDK 侧登记为保留 id（__llm_tool__{name}）的动态 entry，并经
    # LLM_TOOL_REGISTER IPC → main_server /api/tools/register 注册为
    # 模型可调用工具。聊天 LLM 调用时走 /api/llm-tools/callback 回到
    # 本插件的 handler，handler 再转发到 POST /runs 复用原调用路径，
    # 使调用进入插件管理 UI 的"运行记录"。

    def _is_chat_injection_enabled(self, server_name: str) -> bool:
        cfg = self._servers_config.get(server_name)
        if not isinstance(cfg, dict):
            return False
        return self._coerce_bool(cfg.get("inject_to_chat", False), False)

    def _chat_tool_timeout(self) -> float:
        """聊天注入工具的总预算（秒）：MCP 超时 + 转发余量，受 main_server
        ``/api/tools/register`` 的 300s 上限约束。注册超时、轮询期限、run 侧
        entry_timeout 全部从这一个预算推导，保证工具超预算时 run 看门狗先于
        聊天端轮询期限取消，不会出现"聊天已报超时、run 后来又 succeeded"。"""
        return min(self._tool_timeout + _LLM_TOOL_TIMEOUT_SLACK_S, _LLM_TOOL_TIMEOUT_CAP_S)

    async def _fetch_foreign_llm_tool_names(self) -> frozenset:
        """查询 main_server 上其它 source 已占用的 LLM tool 名。

        main_server 的 tool registry 以名字为全局键、replace 语义注册，跨插件
        重名会把对方的工具挤掉（模型调用被重定向）。``/api/tools/register``
        现已在服务端拒绝跨 source 覆盖（权威防线），这里的前置查重只是
        尽力而为的快失败优化：避免为注定被拒的名字排队注册，并提前用序号
        避让。查询失败时 fail-open 返回空集，不阻塞注入。带短 TTL 缓存，
        避免同一批 tool 注册重复打接口。
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
        """把 MCP tool_id 映射为合法且唯一的 LLM tool 名。

        main_server 要求 tool 名匹配 ``[A-Za-z0-9_.\\-]{1,64}``；server/tool
        名可能含非 ASCII 字符，统一折叠为 "_"。``taken`` 是跨插件查重得到
        的已占用名（与本插件已注册名一起参与去重），冲突时追加运行期序号。
        序号耗尽仍冲突时返回 None，由调用方跳过注入。
        """
        base = re.sub(r"[^A-Za-z0-9_.\-]+", "_", tool_id).strip("._-")
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

    async def _register_chat_tool(
        self,
        *,
        tool_id: str,
        server_name: str,
        tool_name: str,
        description: str,
        schema: Optional[Dict[str, object]],
    ) -> bool:
        """把单个 MCP tool 注册为聊天上下文可用的 LLM tool。"""
        if tool_id in self._chat_tools:
            return True
        foreign = await self._fetch_foreign_llm_tool_names()
        llm_name = self._alloc_llm_tool_name(tool_id, foreign)
        if llm_name is None:
            self.ctx.logger.warning(
                f"Skip chat injection for MCP tool '{tool_id}': no free LLM tool name"
            )
            return False
        try:
            self.register_llm_tool(
                name=llm_name,
                description=description or f"MCP tool '{tool_name}' from server '{server_name}'.",
                parameters=schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
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
        self._chat_tools[tool_id] = {
            "llm_name": llm_name,
            "server_name": server_name,
            "tool_name": tool_name,
        }
        self.ctx.logger.info(
            f"Injected MCP tool '{tool_id}' into chat context as LLM tool '{llm_name}'"
        )
        return True

    async def _remote_unregister_llm_tool(self, llm_name: str) -> bool:
        """直接同步调 main_server 注销 LLM tool，返回远端是否确认清理。

        SDK 的 ``unregister_llm_tool`` 只保证本地清理；IPC → host → main_server
        的远端注销是异步的，失败仅由 host 记日志，插件无法观察到结果。为了
        让"停用注入"的状态可靠，先同步调远端并确认，再做本地注销。
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
                    json={"name": llm_name, "role": None},
                )
            if resp.status_code >= 400:
                return False
            body = resp.json()
        except (httpx.HTTPError, OSError, ValueError):
            return False
        if not isinstance(body, dict):
            return True
        # 判定对齐 llm_tool_registry.unregister_remote_tool：failed_roles 非空
        # 视为部分失败（部分 role 还挂着），保留状态供重试；removed=False 表示
        # 远端本就没有该工具，视为已清理。
        return not body.get("failed_roles")

    async def _unregister_chat_tool(self, tool_id: str) -> bool:
        info = self._chat_tools.get(tool_id)
        if info is None:
            return False
        llm_name = str(info.get("llm_name") or "")
        if not llm_name:
            self._chat_tools.pop(tool_id, None)
            return False
        # 先同步确认远端移除，再做本地注销；远端失败时保留映射（可在下次
        # 切换开关/断开时重试），避免"面板显示已停用但 main_server 还挂着
        # 死回调工具、且没有映射可用于清理"的僵尸状态。
        remote_removed = await self._remote_unregister_llm_tool(llm_name)
        if not remote_removed:
            self.ctx.logger.warning(
                f"Remote unregister not confirmed for chat tool '{llm_name}' "
                f"(MCP tool '{tool_id}'); keeping mapping for retry"
            )
            return False
        try:
            removed = self.unregister_llm_tool(llm_name)
        except Exception as exc:
            self.ctx.logger.warning(
                f"Failed to remove chat injection for MCP tool '{tool_id}': {exc}"
            )
            return False
        # removed=False 说明 SDK 已不再跟踪该名（重复注销等），同样清理映射
        self._chat_tools.pop(tool_id, None)
        if removed:
            self.ctx.logger.info(
                f"Removed chat injection for MCP tool '{tool_id}' (LLM tool '{llm_name}')"
            )
        return removed

    def _build_chat_tool_handler(self, *, tool_id: str, server_name: str, tool_name: str) -> Callable[..., Any]:
        """构造聊天注入工具的处理器：转发到 POST /runs 以产生运行记录。"""

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
            # _build_mcp_tool_payload 恒返回 dict（至少含 "result"），payload 为
            # None 只可能是 export 读不回——显式报错让模型可重试，而不是静默空结果
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
        """解析 user_plugin_server 的 loopback origin。

        端口启动时可能因冲突改绑，env 变量保存实际端口；解析顺序与
        llm_tool_registry / music_pusher 一致。
        """
        from config import USER_PLUGIN_SERVER_PORT

        raw = os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "").strip()
        try:
            port = int(raw) if raw else int(USER_PLUGIN_SERVER_PORT)
        except ValueError:
            port = int(USER_PLUGIN_SERVER_PORT)
        return f"http://127.0.0.1:{port}"

    async def _execute_chat_tool_via_run(
        self,
        *,
        entry_id: str,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, object],
    ) -> Dict[str, object]:
        """经由 POST /runs 执行聊天注入的 MCP tool 调用。

        与原调用路径（brain task_executor → POST /runs）完全同构，因此本次
        调用会以 plugin_id=mcp_adapter、entry_id=mcp_{server}_{tool} 进入插件
        管理界面的"运行记录"。返回 {"status", "success", "data", "error"}。
        """
        base = self._resolve_plugin_server_origin()
        timeout_s = self._chat_tool_timeout()
        # 创建请求与轮询共享同一个绝对截止时间：创建请求可消耗全部剩余预算
        #（不再被固定 10s 上限挤占轮询窗口），保证处理总时长不超过注册给
        # main_server 的超时，聊天端不会先于我们拿到干净的超时错误。
        deadline = asyncio.get_running_loop().time() + timeout_s
        run_args = dict(arguments) if isinstance(arguments, dict) else {}
        # entry_timeout 覆盖 run 侧守卫超时（默认 RUN_EXECUTION_TIMEOUT）与
        # entry 看门狗：预算 - 10s，先于聊天端轮询期限（预算 - 5s）触发取消，
        # 避免超预算的 MCP 调用在聊天端已报超时后 run 又单独 succeeded。
        run_args["_ctx"] = {"entry_timeout": max(timeout_s - 10.0, 5.0)}
        run_id: Optional[str] = None

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=2.0),
                proxy=None,
                trust_env=False,
            ) as client:
                remaining = deadline - asyncio.get_running_loop().time()
                create_resp = await client.post(
                    f"{base}/runs",
                    json={
                        "plugin_id": self.plugin_id,
                        "entry_id": entry_id,
                        "args": run_args,
                    },
                    timeout=httpx.Timeout(max(remaining, 1.0), connect=2.0),
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
                        # 放弃前尽力取消 run：否则上层重试可能与仍在执行的 run
                        # 叠加，造成有副作用的 MCP tool 重复执行。run 看门狗
                        # （entry_timeout）通常已先取消，这里是创建耗时挤占
                        # 守卫窗口时的兜底。
                        await self._cancel_run_best_effort(base, run_id, tool_name)
                        return {
                            "status": "timeout",
                            "success": False,
                            "data": None,
                            "error": f"Timed out waiting for run of MCP tool '{tool_name}'",
                        }
                    poll_resp = await client.get(
                        f"{base}/runs/{run_id}",
                        timeout=httpx.Timeout(max(min(remaining, 10.0), 1.0), connect=2.0),
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

                remaining = deadline - asyncio.get_running_loop().time()
                export_resp = await client.get(
                    f"{base}/runs/{run_id}/export",
                    params={"limit": 50},
                    timeout=httpx.Timeout(max(min(remaining, 10.0), 1.0), connect=2.0),
                )
                if export_resp.status_code != 200:
                    return {"status": status, "success": True, "data": None, "error": None}
                # run 已成功，export 解析失败只降级为空数据，不反报失败
                try:
                    payload = self._extract_chat_tool_payload(export_resp.json())
                except ValueError:
                    payload = None
                return {"status": status, "success": True, "data": payload, "error": None}
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return {
                "status": "failed",
                "success": False,
                "data": None,
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def _cancel_run_best_effort(self, base: str, run_id: str, tool_name: str) -> None:
        """轮询放弃前尽力取消 run（best-effort，吞掉一切传输错误）。"""
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
        """从 run 的 export 响应中取出 trigger_response 的业务数据。

        export 响应形如 ``{"items": [ExportItem, ...], "next_after": ...}``；
        trigger_response 条目的 json（序列化别名，兼容 json_data 键）是
        host.trigger 返回的 finish 信封（ok()/finish() 形状），其 ``data``
        字段就是 ``_build_mcp_tool_payload`` 的业务 payload。trigger_response
        不在时回退到第一个含 json 的条目。
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
        """初始化 Gateway Core 组件。"""
        # 路由引擎（带回调，用于通知前端动态 entry 变化）
        self._route_engine = MCPRouteEngine(
            mcp_clients=self._clients,
            logger=self.ctx.logger,  # type: ignore[arg-type]
            on_tool_register=self._on_tool_register,
            on_tool_unregister=self._on_tool_unregister,
        )
        self._route_engine.rebuild_tool_index()
        
        # 请求规范化器
        self._normalizer = MCPRequestNormalizer()
        
        # 响应序列化器
        self._serializer = MCPResponseSerializer()
        
        # 插件调用器
        self._invoker = MCPPluginInvoker(
            mcp_clients=self._clients,
            plugin_call_fn=self._call_neko_plugin,
            logger=self.ctx.logger,  # type: ignore[arg-type]
        )

        # 策略引擎
        self._policy = DefaultPolicyEngine()

        # 统一 Gateway Core 编排器（P0 收敛）
        self._gateway_core = AdapterGatewayCore(
            transport=_MCPInternalTransport(),  # gateway_invoke 走 handle_envelope，不依赖 transport 轮询
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
        调用 NEKO 插件 entry。
        
        这是 MCPPluginInvoker 的回调函数。
        返回协程，由调用方 await。
        """
        # 使用 PluginContext 的能力调用其他插件
        # 注意：trigger_plugin_event 会自动检测环境，在事件循环中返回协程
        return self.ctx.trigger_plugin_event(
            target_plugin_id=plugin_id,
            event_type="adapter_call",
            event_id=entry_id,
            params=dict(params),  # 转换为 Dict[str, Any]
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
        使用 Gateway Core 注册 MCP tools。
        
        通过 MCPRouteEngine.register_server_tools 方法：
        1. 更新路由引擎的工具索引
        2. 触发回调注册为动态 entry（出现在前端管理面板）
        """
        if self._route_engine:
            await self._route_engine.register_server_tools(server_name, client)

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
        使用 Gateway Core 注销 MCP tools。
        
        通过 MCPRouteEngine.unregister_server_tools 方法：
        1. 从路由引擎移除工具索引
        2. 触发回调注销动态 entry
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
        """插件关闭时断开所有连接"""
        self.ctx.logger.info("MCP Adapter shutting down...")
        self._shutdown = True

        self._pending_auto_connect.clear()

        for task in self._connect_tasks.values():
            task.cancel()
        self._connect_tasks.clear()

        # 取消所有重连任务
        for task in self._reconnect_tasks.values():
            task.cancel()
        self._reconnect_tasks.clear()
        
        # 断开所有连接
        for server_name, client in list(self._clients.items()):
            try:
                await client.disconnect()
                self.ctx.logger.info(f"Disconnected from MCP server: {server_name}")
            except Exception as e:
                self.ctx.logger.warning(f"Error disconnecting from {server_name}: {e}")
        
        self._clients.clear()
        self._chat_tools.clear()
        self._gateway_core = None
        self._policy = None

        # 清理 Adapter 基类
        await self.adapter_shutdown()
    
    async def _on_server_disconnect(self, server_name: str) -> None:
        """服务器断开连接时的回调（用于自动重连）"""
        if self._shutdown:
            return
        
        self.ctx.logger.warning(f"MCP server '{server_name}' disconnected")
        
        # 更新状态
        self._server_states[server_name] = {
            **self._server_states.get(server_name, {}),
            "connected": False,
            "error": "Connection lost",
        }
        
        # 注销 MCP tools
        await self._unregister_mcp_tools(server_name)
        
        # 从 clients 中移除
        if server_name in self._clients:
            del self._clients[server_name]
        
        # 如果启用了自动重连，启动重连任务
        if self._auto_reconnect and server_name not in self._reconnect_tasks:
            self._reconnect_tasks[server_name] = asyncio.create_task(
                self._reconnect_server(server_name)
            )
    
    async def _reconnect_server(self, server_name: str) -> None:
        """尝试重连服务器"""
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
                
                # 更新状态
                self._server_states[server_name] = {
                    **self._server_states.get(server_name, {}),
                    "reconnect_attempts": attempts,
                }
                
                # 等待重连间隔
                await asyncio.sleep(self._reconnect_interval)
                
                if self._shutdown:
                    break
                
                # 尝试重连
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
        """连接到单个 MCP server"""
        client: MCPClient | None = None
        try:
            if server_name not in self._servers_config:
                self.ctx.logger.info(f"Skip connecting removed MCP server '{server_name}'")
                return False
            timeout = self._coerce_timeout(timeout, 30.0)
            # 提取配置字段并进行类型转换
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
            
            # 设置断开回调（用于自动重连）
            client.set_disconnect_callback(self._on_server_disconnect)
            
            if await client.connect(timeout=timeout):
                if server_name not in self._servers_config:
                    await client.disconnect()
                    self.ctx.logger.info(f"Discarded connection for removed MCP server '{server_name}'")
                    return False
                self._clients[server_name] = client
                client._reconnect_attempts = 0  # 重置重连计数
                
                # 使用 Gateway Core 注册 tools
                try:
                    await self._register_mcp_tools(server_name, client)
                except Exception:
                    self._clients.pop(server_name, None)
                    await client.disconnect()
                    raise
                
                # 更新状态
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
        """列出所有 MCP servers"""
        servers = []
        seen_names = set()
        
        # 已连接的服务器
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
        
        # 有状态但未连接的服务器
        config = await self.config.dump()
        servers_config = config.get("mcp_servers", {})
        
        for server_name, state in self._server_states.items():
            if server_name not in seen_names:
                seen_names.add(server_name)
                # 从配置中获取 transport 信息
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
        
        # 配置中存在但从未尝试连接的服务器
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
        """连接到指定的 MCP server"""
        if server_name in self._clients:
            return Err(SdkError(f"Server '{server_name}' is already connected"))
        
        # 从配置中获取 server 配置
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
        """断开与指定 MCP server 的连接"""
        if server_name not in self._clients:
            return Err(SdkError(f"Server '{server_name}' is not connected"))

        self._cancel_reconnect_task(server_name)
        
        # 注销 MCP tools
        await self._unregister_mcp_tools(server_name)
        
        # 断开连接
        client = self._clients.pop(server_name)
        await client.disconnect()
        
        # 更新状态
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
        """配置单个 MCP server 是否把 tools 注入聊天上下文"""
        config = await self.config.dump()
        servers_config = config.get("mcp_servers", {})
        server_cfg = servers_config.get(server_name)
        if not isinstance(server_cfg, dict):
            return Err(SdkError(f"Server '{server_name}' not found in config"))

        inject_flag = self._coerce_bool(inject_to_chat, False)
        server_cfg["inject_to_chat"] = inject_flag
        try:
            await self._persist_servers_config(servers_config)
        except Exception as exc:
            self.ctx.logger.exception(
                f"Failed to persist chat injection flag for MCP server '{server_name}': {exc}"
            )
            return Err(SdkError(f"Failed to save server config: {exc}"))
        self._servers_config = servers_config

        # 已连接的 server 立即生效；未连接的只在下次连接时生效
        client = self._clients.get(server_name)
        applied = 0
        if client is not None:
            for tool in client.tools:
                tool_id = f"mcp_{server_name}_{tool.name}"
                # 只处理路由索引归属本 server 的 tool_id：server/tool 拼接可能
                # 撞出相同 ID（如 server a_b + tool c 与 server a + tool b_c），
                # 路由引擎按先到先得去重，非归属方若也注册 chat 注入会转发到
                # 别人的 entry、注销时还会拆掉别人的注入。
                if self._route_engine is None or self._route_engine.get_tool_server(tool_id) != server_name:
                    continue
                if inject_flag:
                    if tool_id in self._chat_tools:
                        continue
                    if await self._register_chat_tool(
                        tool_id=tool_id,
                        server_name=server_name,
                        tool_name=tool.name,
                        description=tool.description or f"MCP tool from {server_name}",
                        schema=tool.input_schema,
                    ):
                        applied += 1
                else:
                    if await self._unregister_chat_tool(tool_id):
                        applied += 1

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
        """添加新的 MCP server 配置"""
        # 检查是否已存在
        config = await self.config.dump()
        servers_config = config.get("mcp_servers", {})

        # 验证配置
        if transport == "stdio" and not command:
            return Err(SdkError("Command is required for stdio transport"))
        if transport in ("sse", "streamable-http") and not url:
            return Err(SdkError("URL is required for sse/http transport"))

        # 构建配置
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
        
        # 保存到配置
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
        
        # 缓存配置
        self._servers_config = servers_config
        self.ctx.logger.info(f"Server '{name}' added to config")
        
        # 如果需要自动连接
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
        """批量移除 MCP server 配置"""
        config = await self.config.dump()
        servers_config = config.get("mcp_servers", {})
        
        removed = []
        not_found = []
        
        for name in server_names:
            if name not in servers_config:
                not_found.append(name)
                continue

            self._cancel_connect_task(name)
            self._cancel_reconnect_task(name)
            
            # 如果已连接，先断开
            if name in self._clients:
                await self._unregister_mcp_tools(name)
                client = self._clients.pop(name)
                await client.disconnect()
            
            # 从配置中移除
            del servers_config[name]
            
            # 清理状态
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
        """调用 MCP tool"""
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
        """列出所有 MCP tools"""
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
        通过 Gateway Core 调用 MCP tool 或 NEKO 插件。
        
        这是新架构的统一入口，使用 Gateway Core 组件处理请求。
        """
        import uuid
        if self._gateway_core is None:
            return Err(SdkError("Gateway Core components not initialized"))
        
        # 构造 ExternalRequest
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
