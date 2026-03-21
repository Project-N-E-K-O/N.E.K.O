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

from bs4 import BeautifulSoup  # type: ignore[import-untyped]
from markdownify import markdownify as markdownify_html  # type: ignore[import-untyped]

from plugin.sdk.plugin import (
    neko_plugin,
    plugin_entry,
    lifecycle,
    Ok,
    Err,
    SdkError,
)
from plugin.sdk.adapter import AdapterGatewayCore, DefaultPolicyEngine, NekoAdapterPlugin
from plugin.sdk.adapter.gateway_models import ExternalRequest
from plugin.plugins.mcp_adapter.normalizer import MCPRequestNormalizer
from plugin.plugins.mcp_adapter.serializer import MCPResponseSerializer
from plugin.plugins.mcp_adapter.router import MCPRouteEngine
from plugin.plugins.mcp_adapter.invoker import MCPPluginInvoker
from utils.aiohttp_proxy_utils import aiohttp_session_kwargs_for_url


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
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
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
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
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
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
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
                
                # 记录 stderr 输出
                stderr_text = line.decode().strip()
                if stderr_text and self.logger:
                    self.logger.debug(f"MCP server '{self.config.name}' stderr: {stderr_text}")
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
        self._connect_task: Optional[asyncio.Task] = None
        self._reconnect_tasks: Dict[str, asyncio.Task] = {}
        self._shutdown = False
        # 重连配置缓存
        self._auto_reconnect = True
        self._reconnect_interval = 5
        self._max_reconnect_attempts = 3
        self._tool_timeout = 60.0
        self._servers_config: Dict[str, Dict[str, object]] = {}
        
        # Gateway Core 组件
        self._route_engine: Optional[MCPRouteEngine] = None
        self._invoker: Optional[MCPPluginInvoker] = None
        self._normalizer: Optional[MCPRequestNormalizer] = None
        self._serializer: Optional[MCPResponseSerializer] = None
        self._policy: Optional[DefaultPolicyEngine] = None
        self._gateway_core: Optional[AdapterGatewayCore] = None
    
    @lifecycle(id="startup")
    async def on_startup(self):
        """插件启动时连接所有配置的 MCP servers"""
        self.ctx.logger.info("MCP Adapter starting...")
        
        # 初始化 Adapter 基类
        await self.adapter_startup()
        
        # 注册静态 UI
        self.register_static_ui("static")
        
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
        
        # 连接所有启用的 servers
        for server_name, server_cfg in servers_config.items():
            if not isinstance(server_cfg, dict):
                continue
            
            if not server_cfg.get("enabled", True):
                self.ctx.logger.info(f"Skipping disabled MCP server: {server_name}")
                continue
            
            await self._connect_server(server_name, server_cfg, connect_timeout)
        
        self.ctx.logger.info(
            f"MCP Adapter started with {len(self._clients)} connected servers"
        )
    
    async def _on_tool_register(
        self,
        tool_id: str,
        display_name: str,
        description: str,
        schema: Optional[Dict[str, object]],
    ) -> bool:
        """Gateway Core 工具注册回调 - 注册为动态 entry。"""
        def _parse_tool_id(value: str) -> tuple[str | None, str | None]:
            # 优先按已连接 server 前缀解析，兼容 server 名含 "_"
            for server_name in sorted(self._clients.keys(), key=len, reverse=True):
                prefix = f"mcp_{server_name}_"
                if value.startswith(prefix):
                    tool_name = value[len(prefix):]
                    if tool_name:
                        return server_name, tool_name
            # 兜底兼容老解析逻辑
            parts = value.split("_", 2)
            if len(parts) >= 3 and parts[1] and parts[2]:
                return parts[1], parts[2]
            return None, None

        parsed_server_name, parsed_tool_name = _parse_tool_id(tool_id)

        # 创建工具处理器
        async def tool_handler(**kwargs: object) -> Dict[str, object]:
            # 从 tool_id 解析 server_name 和 tool_name
            server_name, tool_name = parsed_server_name, parsed_tool_name
            if not server_name or not tool_name:
                return Err(SdkError(f"Invalid tool_id: {tool_id}"))
            
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
                meta={"agent": {"fields": ["summary"]}},
            )
        
        # 注册为动态 entry
        return self.register_dynamic_entry(
            entry_id=tool_id,
            handler=tool_handler,
            name=display_name,
            description=description,
            input_schema=schema,
            kind="action",
            timeout=self._tool_timeout + 5.0,
        )
    
    async def _on_tool_unregister(self, tool_id: str) -> bool:
        """Gateway Core 工具注销回调 - 注销动态 entry。"""
        return self.unregister_dynamic_entry(tool_id)
    
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
        enabled: bool = True,
    ) -> Dict[str, object]:
        server_cfg: Dict[str, object] = {
            "transport": transport,
            "enabled": enabled,
        }
        if command:
            server_cfg["command"] = command
        if args:
            server_cfg["args"] = list(args)
        if url:
            server_cfg["url"] = url
        if env:
            server_cfg["env"] = dict(env)
        return server_cfg

    def _is_same_server_config(self, current: object, incoming: Dict[str, object]) -> bool:
        if not isinstance(current, dict):
            return False
        normalized_current = self._normalize_server_config_payload(
            transport=str(current.get("transport", "")),
            command=str(current["command"]) if isinstance(current.get("command"), str) else None,
            args=list(current["args"]) if isinstance(current.get("args"), list) else None,
            url=str(current["url"]) if isinstance(current.get("url"), str) else None,
            env=dict(current["env"]) if isinstance(current.get("env"), dict) else None,
            enabled=self._coerce_bool(current.get("enabled", True), True),
        )
        return normalized_current == incoming

    def _truncate_llm_text(self, text: str, limit: int = 1200) -> str:
        cleaned = text.strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[:limit] + "..."

    def _string_field(self, value: object) -> str:
        return str(value or "").strip()

    def _describe_mcp_content_item(self, item: Dict[str, object]) -> str:
        item_type = self._string_field(item.get("type")).lower()
        label = (
            self._string_field(item.get("title"))
            or self._string_field(item.get("name"))
            or self._string_field(item.get("description"))
        )
        mime_type = self._string_field(item.get("mimeType"))
        uri = self._string_field(item.get("uri"))

        if item_type == "image":
            parts = ["Image"]
            if label:
                parts.append(label)
            if mime_type:
                parts.append(f"({mime_type})")
            elif uri:
                parts.append(f"({uri})")
            return " ".join(parts)

        if item_type == "audio":
            parts = ["Audio"]
            if label:
                parts.append(label)
            if mime_type:
                parts.append(f"({mime_type})")
            elif uri:
                parts.append(f"({uri})")
            return " ".join(parts)

        if item_type in {"resource", "resource_link"}:
            resource_obj = item.get("resource")
            resource = resource_obj if isinstance(resource_obj, dict) else item
            resource_name = (
                self._string_field(resource.get("name"))
                or self._string_field(resource.get("title"))
                or label
            )
            resource_mime = self._string_field(resource.get("mimeType")) or mime_type
            resource_uri = self._string_field(resource.get("uri")) or uri
            resource_text = self._string_field(resource.get("text"))
            if resource_text:
                prefix_parts = ["Resource"]
                if resource_name:
                    prefix_parts.append(resource_name)
                elif resource_uri:
                    prefix_parts.append(resource_uri)
                prefix = " ".join(prefix_parts)
                if prefix != "Resource":
                    prefix += ": "
                else:
                    prefix = ""
                return prefix + self._truncate_llm_text(resource_text, limit=500)

            parts = ["Resource"]
            if resource_name:
                parts.append(resource_name)
            elif resource_uri:
                parts.append(resource_uri)
            if resource_mime:
                parts.append(f"({resource_mime})")
            return " ".join(parts)

        if item_type:
            parts = [item_type.capitalize()]
            if label:
                parts.append(label)
            elif mime_type:
                parts.append(f"({mime_type})")
            elif uri:
                parts.append(f"({uri})")
            return " ".join(parts)

        return ""

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
                    described = self._describe_mcp_content_item(item)
                    if described:
                        parts.append(described)
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
    
    async def _unregister_mcp_tools(self, server_name: str) -> None:
        """
        使用 Gateway Core 注销 MCP tools。
        
        通过 MCPRouteEngine.unregister_server_tools 方法：
        1. 从路由引擎移除工具索引
        2. 触发回调注销动态 entry
        """
        if self._route_engine:
            await self._route_engine.unregister_server_tools(server_name)
    
    @lifecycle(id="shutdown")
    async def on_shutdown(self):
        """插件关闭时断开所有连接"""
        self.ctx.logger.info("MCP Adapter shutting down...")
        self._shutdown = True
        
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
        try:
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
            
            enabled = self._coerce_bool(server_cfg.get("enabled", True), True)
            
            config = MCPServerConfig(
                name=server_name,
                transport=transport,
                command=command,
                args=[str(a) for a in args],
                url=url,
                env={str(k): str(v) for k, v in env.items()},
                enabled=enabled,
            )
            
            client = MCPClient(config, logger=self.ctx.logger)
            
            # 设置断开回调（用于自动重连）
            client.set_disconnect_callback(self._on_server_disconnect)
            
            if await client.connect(timeout=timeout):
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
                
        except Exception as e:
            self.ctx.logger.exception(f"Failed to connect to MCP server '{server_name}': {e}")
            self._server_states[server_name] = {
                "connected": False,
                "error": str(e),
            }
            return False
    
    @plugin_entry(
        id="list_servers",
        name="List MCP Servers",
        description="列出所有配置的 MCP servers 及其状态",
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
                })
        
        return Ok({"servers": servers, "total": len(servers)})
    
    @plugin_entry(
        id="connect_server",
        name="Connect MCP Server",
        description="连接到指定的 MCP server",
        llm_result_fields=["message"],
        input_schema={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Server name from config"
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
        
        if await self._connect_server(server_name, server_cfg, timeout):
            return Ok({
                "message": f"Connected to server '{server_name}'",
                "tools_count": len(self._clients[server_name].tools),
            })
        else:
            return Err(SdkError(f"Failed to connect to server '{server_name}'"))
    
    @plugin_entry(
        id="disconnect_server",
        name="Disconnect MCP Server",
        description="断开与指定 MCP server 的连接",
        llm_result_fields=["message"],
        input_schema={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Server name"
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
    
    @plugin_entry(
        id="add_server",
        name="Add MCP Server",
        description="添加新的 MCP server 配置",
        llm_result_fields=["message"],
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Server name (unique identifier)"
                },
                "transport": {
                    "type": "string",
                    "enum": ["stdio", "sse", "streamable-http"],
                    "description": "Transport type"
                },
                "command": {
                    "type": "string",
                    "description": "Command to run (for stdio transport)"
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Command arguments"
                },
                "url": {
                    "type": "string",
                    "description": "Server URL (for sse/http transport)"
                },
                "env": {
                    "type": "object",
                    "description": "Environment variables"
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Whether to enable this server"
                },
                "auto_connect": {
                    "type": "boolean",
                    "description": "Whether to connect immediately"
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
        enabled: bool = True,
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
            enabled=enabled,
        )

        existing_cfg = servers_config.get(name)
        if existing_cfg is not None:
            if self._is_same_server_config(existing_cfg, server_cfg):
                self.ctx.logger.info(f"Server '{name}' already exists with identical config")
                if auto_connect and enabled and name not in self._clients:
                    adapter_config = config.get("mcp_adapter", {})
                    timeout_val = self._coerce_timeout(adapter_config.get("connect_timeout", 30), 30.0)
                    if await self._connect_server(name, server_cfg, timeout_val):
                        return Ok({
                            "message": f"Server '{name}' already exists and is now connected",
                            "tools_count": len(self._clients[name].tools),
                            "already_exists": True,
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
            return Err(SdkError(f"Failed to save server config: {exc}"))
        
        # 缓存配置
        self._servers_config = servers_config
        self.ctx.logger.info(f"Server '{name}' added to config")
        
        # 如果需要自动连接
        if auto_connect and enabled:
            adapter_config = config.get("mcp_adapter", {})
            timeout_val = self._coerce_timeout(adapter_config.get("connect_timeout", 30), 30.0)
            
            if await self._connect_server(name, server_cfg, timeout_val):
                return Ok({
                    "message": f"Added and connected to server '{name}'",
                    "tools_count": len(self._clients[name].tools),
                })
            else:
                return Ok({
                    "message": f"Added server '{name}' but connection failed",
                    "connected": False,
                })
        
        return Ok({"message": f"Added server '{name}'"})
    
    @plugin_entry(
        id="remove_servers",
        name="Remove MCP Servers",
        description="批量移除 MCP server 配置",
        llm_result_fields=["message"],
        input_schema={
            "type": "object",
            "properties": {
                "server_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of server names to remove"
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
            return Err(SdkError(f"Failed to save server config: {exc}"))
        self._servers_config = servers_config
        
        return Ok({
            "removed": removed,
            "not_found": not_found,
            "message": f"Removed {len(removed)} server(s)",
        })
    
    @plugin_entry(
        id="call_tool",
        name="Call MCP Tool",
        description="调用指定 MCP server 的 tool",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Server name"
                },
                "tool_name": {
                    "type": "string",
                    "description": "Tool name"
                },
                "arguments": {
                    "type": "object",
                    "description": "Tool arguments"
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
        name="List MCP Tools",
        description="列出所有可用的 MCP tools",
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
        name="Gateway Invoke",
        description="通过 Gateway Core 调用 MCP tool（新架构）",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Tool name to invoke"
                },
                "arguments": {
                    "type": "object",
                    "description": "Tool arguments"
                },
                "target_plugin_id": {
                    "type": "string",
                    "description": "Optional: route to NEKO plugin instead of MCP tool"
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Optional timeout in seconds for downstream call"
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
