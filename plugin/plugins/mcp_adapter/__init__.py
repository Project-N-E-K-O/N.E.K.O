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
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

from plugin.sdk import (
    neko_plugin,
    plugin_entry,
    lifecycle,
    ok,
    fail,
)
from plugin.sdk.adapter import NekoAdapterPlugin
from plugin.sdk.adapter.gateway_models import ExternalEnvelope
from plugin.plugins.mcp_adapter.normalizer import MCPRequestNormalizer
from plugin.plugins.mcp_adapter.serializer import MCPResponseSerializer
from plugin.plugins.mcp_adapter.router import MCPRouteEngine
from plugin.plugins.mcp_adapter.invoker import MCPPluginInvoker


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
    
    async def connect(self, timeout: float = 30.0) -> bool:
        """连接到 MCP Server"""
        if self.config.transport == "stdio":
            return await self._connect_stdio(timeout)
        elif self.config.transport in ("sse", "streamable-http"):
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
            self._http_session = aiohttp.ClientSession()
            
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
                init_result = await resp.json()
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
                pass  # 通知不需要响应
            
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
                tools_result = await resp.json()
            
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
                self.logger.exception(f"Failed to connect to MCP server '{self.config.name}': {e}")
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
                }),
                timeout=timeout
            )
            
            if result.get("error"):
                return {"error": result["error"]}
            
            return {"result": result.get("result", {})}
            
        except asyncio.TimeoutError:
            return {"error": f"Tool call timed out after {timeout}s"}
        except Exception as e:
            return {"error": str(e)}
    
    async def _send_http_request(self, method: str, params: Dict[str, object]) -> Dict[str, object]:
        """通过 HTTP 发送 JSON-RPC 请求"""
        import aiohttp
        
        # 每次请求都创建新的 session，避免事件循环问题
        async with aiohttp.ClientSession() as session:
            return await self._do_http_request(session, method, params)
    
    async def _do_http_request(
        self,
        session: object,
        method: str,
        params: Dict[str, object],
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
            timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}: {await resp.text()}")
            result = await resp.json()
        
        if "error" in result:
            return {"error": result["error"]}
        
        return result
    
    async def _send_request(self, method: str, params: Dict[str, object]) -> Dict[str, object]:
        """发送 JSON-RPC 请求"""
        # HTTP 传输
        if self.config.transport in ("sse", "streamable-http"):
            return await self._send_http_request(method, params)
        
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
        self._servers_config: Dict[str, Dict[str, object]] = {}
        
        # Gateway Core 组件
        self._route_engine: Optional[MCPRouteEngine] = None
        self._invoker: Optional[MCPPluginInvoker] = None
        self._normalizer: Optional[MCPRequestNormalizer] = None
        self._serializer: Optional[MCPResponseSerializer] = None
    
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
        
        connect_timeout = adapter_config.get("connect_timeout", 30)
        
        # 缓存重连配置
        self._auto_reconnect = adapter_config.get("auto_reconnect", True)
        self._reconnect_interval = adapter_config.get("reconnect_interval", 5)
        self._max_reconnect_attempts = adapter_config.get("max_reconnect_attempts", 3)
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
        # 创建工具处理器
        async def tool_handler(**kwargs: object) -> Dict[str, object]:
            # 从 tool_id 解析 server_name 和 tool_name
            parts = tool_id.split("_", 2)
            if len(parts) < 3:
                return fail("INVALID_TOOL_ID", f"Invalid tool_id: {tool_id}")
            server_name = parts[1]
            tool_name = parts[2]
            
            # 移除 NEKO 注入的参数
            arguments = {k: v for k, v in kwargs.items() if not k.startswith("_")}
            
            # 获取对应的 client
            target_client = self._clients.get(server_name)
            if not target_client:
                return fail("NOT_CONNECTED", f"Server '{server_name}' not connected")
            
            result = await target_client.call_tool(tool_name, arguments)
            if "error" in result:
                return fail("MCP_ERROR", str(result["error"]))
            return ok(data=result.get("result", {}))
        
        # 注册为动态 entry
        return await self.register_dynamic_entry(
            entry_id=tool_id,
            handler=tool_handler,
            name=display_name,
            description=description,
            input_schema=schema,
            kind="action",
        )
    
    async def _on_tool_unregister(self, tool_id: str) -> bool:
        """Gateway Core 工具注销回调 - 注销动态 entry。"""
        return await self.unregister_dynamic_entry(tool_id)
    
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
        
        self.ctx.logger.info("Gateway Core components initialized")
    
    def _call_neko_plugin(
        self,
        plugin_id: str,
        entry_id: str,
        params: dict[str, object],
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
            timeout=30.0,
        )
    
    async def _register_mcp_tools(self, server_name: str, client: MCPClient) -> None:
        """
        使用 Gateway Core 注册 MCP tools。
        
        通过 MCPRouteEngine.register_server_tools 方法：
        1. 更新路由引擎的工具索引
        2. 触发回调注册为动态 entry（出现在前端管理面板）
        """
        if self._route_engine:
            count = await self._route_engine.register_server_tools(server_name, client)
            self.ctx.logger.info(f"Registered {count} tools from MCP server '{server_name}'")
    
    async def _unregister_mcp_tools(self, server_name: str) -> None:
        """
        使用 Gateway Core 注销 MCP tools。
        
        通过 MCPRouteEngine.unregister_server_tools 方法：
        1. 从路由引擎移除工具索引
        2. 触发回调注销动态 entry
        """
        if self._route_engine:
            count = await self._route_engine.unregister_server_tools(server_name)
            if count > 0:
                self.ctx.logger.info(f"Unregistered {count} tools from MCP server '{server_name}'")
    
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
            timeout = adapter_config.get("connect_timeout", 30)
            
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
        
        # 清理重连任务
        self._reconnect_tasks.pop(server_name, None)
    
    async def _connect_server(
        self,
        server_name: str,
        server_cfg: Dict[str, object],
        timeout: float = 30.0
    ) -> bool:
        """连接到单个 MCP server"""
        try:
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
            
            enabled_raw = server_cfg.get("enabled", True)
            enabled = bool(enabled_raw) if enabled_raw is not None else True
            
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
                await self._register_mcp_tools(server_name, client)
                
                # 重建路由索引
                if self._route_engine:
                    self._route_engine.rebuild_tool_index()
                
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
        
        return ok(data={"servers": servers, "total": len(servers)})
    
    @plugin_entry(
        id="connect_server",
        name="Connect MCP Server",
        description="连接到指定的 MCP server",
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
            return fail("ALREADY_CONNECTED", f"Server '{server_name}' is already connected")
        
        # 从配置中获取 server 配置
        config = await self.config.dump()
        servers_config = config.get("mcp_servers", {})
        
        if server_name not in servers_config:
            return fail("NOT_FOUND", f"Server '{server_name}' not found in config")
        
        server_cfg = servers_config[server_name]
        adapter_config = config.get("mcp_adapter", {})
        timeout = adapter_config.get("connect_timeout", 30)
        
        if await self._connect_server(server_name, server_cfg, timeout):
            return ok(data={
                "message": f"Connected to server '{server_name}'",
                "tools_count": len(self._clients[server_name].tools),
            })
        else:
            return fail("CONNECTION_FAILED", f"Failed to connect to server '{server_name}'")
    
    @plugin_entry(
        id="disconnect_server",
        name="Disconnect MCP Server",
        description="断开与指定 MCP server 的连接",
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
            return fail("NOT_CONNECTED", f"Server '{server_name}' is not connected")
        
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
        
        return ok(data={"message": f"Disconnected from server '{server_name}'"})
    
    @plugin_entry(
        id="add_server",
        name="Add MCP Server",
        description="添加新的 MCP server 配置",
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
        
        if name in servers_config:
            return fail("ALREADY_EXISTS", f"Server '{name}' already exists")
        
        # 验证配置
        if transport == "stdio" and not command:
            return fail("INVALID_CONFIG", "Command is required for stdio transport")
        if transport in ("sse", "streamable-http") and not url:
            return fail("INVALID_CONFIG", "URL is required for sse/http transport")
        
        # 构建配置
        server_cfg: Dict[str, object] = {
            "transport": transport,
            "enabled": enabled,
        }
        if command:
            server_cfg["command"] = command
        if args:
            server_cfg["args"] = args
        if url:
            server_cfg["url"] = url
        if env:
            server_cfg["env"] = env
        
        # 保存到配置
        servers_config[name] = server_cfg
        self.ctx.logger.info(f"Saving mcp_servers config: {list(servers_config.keys())}")
        await self.config.set("mcp_servers", servers_config)
        
        # 缓存配置
        self._servers_config = servers_config
        self.ctx.logger.info(f"Server '{name}' added to config")
        
        # 如果需要自动连接
        if auto_connect and enabled:
            adapter_config = config.get("mcp_adapter", {})
            timeout = adapter_config.get("connect_timeout", 30)
            timeout_val = float(timeout) if isinstance(timeout, (int, float)) else 30.0
            
            if await self._connect_server(name, server_cfg, timeout_val):
                return ok(data={
                    "message": f"Added and connected to server '{name}'",
                    "tools_count": len(self._clients[name].tools),
                })
            else:
                return ok(data={
                    "message": f"Added server '{name}' but connection failed",
                    "connected": False,
                })
        
        return ok(data={"message": f"Added server '{name}'"})
    
    @plugin_entry(
        id="remove_servers",
        name="Remove MCP Servers",
        description="批量移除 MCP server 配置",
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
        
        # 保存配置（使用 __replace__ 标记强制替换整个 mcp_servers 配置）
        self.ctx.logger.info(f"Saving updated mcp_servers config: {list(servers_config.keys())}")
        replace_config = dict(servers_config)
        replace_config["__replace__"] = True
        result = await self.config.set("mcp_servers", replace_config)
        self.ctx.logger.info(f"Config update result: {result}")
        self._servers_config = servers_config
        
        return ok(data={
            "removed": removed,
            "not_found": not_found,
            "message": f"Removed {len(removed)} server(s)",
        })
    
    @plugin_entry(
        id="call_tool",
        name="Call MCP Tool",
        description="调用指定 MCP server 的 tool",
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
            return fail("NOT_CONNECTED", f"Server '{server_name}' is not connected")
        
        client = self._clients[server_name]
        
        config = await self.config.dump()
        adapter_config = config.get("mcp_adapter", {})
        timeout = adapter_config.get("tool_timeout", 60)
        
        timeout_val = float(timeout) if isinstance(timeout, (int, float)) else 60.0
        result = await client.call_tool(tool_name, arguments or {}, timeout=timeout_val)
        
        if "error" in result:
            error_msg = str(result["error"]) if result["error"] else "Unknown error"
            return fail("MCP_ERROR", error_msg)
        
        return ok(data=result.get("result", {}))
    
    @plugin_entry(
        id="list_tools",
        name="List MCP Tools",
        description="列出所有可用的 MCP tools",
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
        
        return ok(data={"tools": tools, "total": len(tools)})
    
    @plugin_entry(
        id="gateway_invoke",
        name="Gateway Invoke",
        description="通过 Gateway Core 调用 MCP tool（新架构）",
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
        **_
    ):
        """
        通过 Gateway Core 调用 MCP tool 或 NEKO 插件。
        
        这是新架构的统一入口，使用 Gateway Core 组件处理请求。
        """
        import uuid
        import time
        
        if self._route_engine is None or self._invoker is None:
            return fail("GATEWAY_NOT_INITIALIZED", "Gateway Core components not initialized")
        
        if self._normalizer is None or self._serializer is None:
            return fail("GATEWAY_NOT_INITIALIZED", "Gateway Core components not initialized")
        
        # 构造 ExternalEnvelope
        request_id = str(uuid.uuid4())
        envelope = ExternalEnvelope(
            protocol="mcp",
            connection_id="neko_internal",
            request_id=request_id,
            action="tool_call",
            payload={
                "name": tool_name,
                "arguments": arguments or {},
                "target_plugin_id": target_plugin_id,
            },
            metadata={},
        )
        
        started_at = time.perf_counter()
        
        try:
            # 规范化请求
            request = await self._normalizer.normalize(envelope)
            
            # 路由决策
            decision = await self._route_engine.decide(request)
            
            # 调用目标
            result = await self._invoker.invoke(request, decision)
            
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            
            # 序列化响应
            response = await self._serializer.ok(request, result, latency_ms)
            
            return ok(data={
                "request_id": response.request_id,
                "result": response.data,
                "latency_ms": response.latency_ms,
                "route": {
                    "mode": decision.mode.value,
                    "plugin_id": decision.plugin_id,
                    "entry_id": decision.entry_id,
                    "reason": decision.reason,
                },
            })
            
        except Exception as exc:
            from plugin.sdk.adapter.gateway_models import GatewayErrorException
            
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            
            if isinstance(exc, GatewayErrorException):
                error_code = exc.error.code
                error_msg = exc.error.message
            else:
                error_code = "GATEWAY_ERROR"
                error_msg = str(exc)
            
            self.ctx.logger.warning(
                f"Gateway invoke failed: code={error_code}, msg={error_msg}, latency={latency_ms:.2f}ms"
            )
            
            return fail(error_code, error_msg)
