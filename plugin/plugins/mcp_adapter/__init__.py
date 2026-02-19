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
from typing import Any, Dict, List, Optional, Callable
from functools import partial

from plugin.sdk import (
    NekoPluginBase,
    PluginRouter,
    neko_plugin,
    plugin_entry,
    lifecycle,
    ok,
    fail,
)
from plugin.sdk.events import EventMeta, EVENT_META_ATTR


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
    input_schema: Dict[str, Any]
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
            for tool in tools_result.get("result", {}).get("tools", []):
                self.tools.append(MCPTool(
                    name=tool.get("name", ""),
                    description=tool.get("description", ""),
                    input_schema=tool.get("inputSchema", {}),
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
            except Exception:
                pass
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
        
        # 取消所有待处理的请求
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(Exception("Connection closed"))
        self._pending_requests.clear()
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any], timeout: float = 60.0) -> Dict[str, Any]:
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
    
    async def _send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """发送 JSON-RPC 请求"""
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
    
    async def _send_notification(self, method: str, params: Dict[str, Any]):
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
    
    async def _handle_message(self, message: Dict[str, Any]):
        """处理收到的消息"""
        request_id = message.get("id")
        
        if request_id is not None:
            # 这是一个响应
            future = self._pending_requests.get(request_id)
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


class MCPToolRouter(PluginRouter):
    """MCP Tool Router - 动态管理 MCP tools 作为 entries"""
    
    def __init__(self, server_name: str, client: MCPClient):
        super().__init__(prefix=f"mcp_{server_name}_")
        self._server_name = server_name
        self._client = client
        self._tool_handlers: Dict[str, Callable] = {}
    
    async def register_tools(self):
        """注册所有 tools 为 entries"""
        for tool in self._client.tools:
            await self._register_tool(tool)
    
    async def _register_tool(self, tool: MCPTool):
        """注册单个 tool 为 entry"""
        # 创建 handler，使用默认参数捕获 tool.name 避免闭包问题
        async def tool_handler(self_router, _tool_name=tool.name, **kwargs):
            # 移除 NEKO 注入的参数
            arguments = {k: v for k, v in kwargs.items() if not k.startswith("_")}
            result = await self_router._client.call_tool(_tool_name, arguments)
            if "error" in result:
                return fail("MCP_ERROR", result["error"])
            return ok(data=result.get("result", {}))
        
        # 绑定 self
        bound_handler = partial(tool_handler, self)
        
        # 添加 entry
        await self.add_entry(
            entry_id=tool.name,
            handler=bound_handler,
            name=f"[MCP] {tool.name}",
            description=tool.description or f"MCP tool from {self._server_name}",
            input_schema=tool.input_schema,
            kind="action",
        )
        
        self._tool_handlers[tool.name] = bound_handler


@neko_plugin
class MCPAdapterPlugin(NekoPluginBase):
    """MCP Adapter Plugin - MCP Router 功能"""
    
    __freezable__ = ["_server_states"]
    
    def __init__(self, ctx):
        super().__init__(ctx)
        self._clients: Dict[str, MCPClient] = {}
        self._mcp_routers: Dict[str, MCPToolRouter] = {}
        self._server_states: Dict[str, Dict[str, Any]] = {}
        self._connect_task: Optional[asyncio.Task] = None
        self._reconnect_tasks: Dict[str, asyncio.Task] = {}
        self._shutdown = False
        # 重连配置缓存
        self._auto_reconnect = True
        self._reconnect_interval = 5
        self._max_reconnect_attempts = 3
        self._servers_config: Dict[str, Dict[str, Any]] = {}
    
    @lifecycle(id="startup")
    async def on_startup(self):
        """插件启动时连接所有配置的 MCP servers"""
        self.ctx.logger.info("MCP Adapter starting...")
        
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
        self._mcp_routers.clear()
    
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
        
        # 卸载 Router
        if server_name in self._mcp_routers:
            try:
                self.exclude_router(self._mcp_routers[server_name])
            except Exception as e:
                self.ctx.logger.debug(f"Error excluding router for {server_name}: {e}")
            del self._mcp_routers[server_name]
        
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
        server_cfg: Dict[str, Any],
        timeout: float = 30.0
    ) -> bool:
        """连接到单个 MCP server"""
        try:
            config = MCPServerConfig(
                name=server_name,
                transport=server_cfg.get("transport", "stdio"),
                command=server_cfg.get("command"),
                args=server_cfg.get("args", []),
                url=server_cfg.get("url"),
                env=server_cfg.get("env", {}),
                enabled=server_cfg.get("enabled", True),
            )
            
            client = MCPClient(config, logger=self.ctx.logger)
            
            # 设置断开回调（用于自动重连）
            client.set_disconnect_callback(self._on_server_disconnect)
            
            if await client.connect(timeout=timeout):
                self._clients[server_name] = client
                client._reconnect_attempts = 0  # 重置重连计数
                
                # 创建 Router 并注册 tools
                router = MCPToolRouter(server_name, client)
                self.include_router(router)
                await router.register_tools()
                self._mcp_routers[server_name] = router
                
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
        
        for server_name, client in self._clients.items():
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
        
        # 添加未连接的 servers
        for server_name, state in self._server_states.items():
            if server_name not in self._clients:
                servers.append({
                    "name": server_name,
                    "connected": False,
                    "error": state.get("error"),
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
        
        # 卸载 Router
        if server_name in self._mcp_routers:
            self.exclude_router(self._mcp_routers[server_name])
            del self._mcp_routers[server_name]
        
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
        arguments: Optional[Dict[str, Any]] = None,
        **_
    ):
        """调用 MCP tool"""
        if server_name not in self._clients:
            return fail("NOT_CONNECTED", f"Server '{server_name}' is not connected")
        
        client = self._clients[server_name]
        
        config = await self.config.dump()
        adapter_config = config.get("mcp_adapter", {})
        timeout = adapter_config.get("tool_timeout", 60)
        
        result = await client.call_tool(tool_name, arguments or {}, timeout=timeout)
        
        if "error" in result:
            return fail("MCP_ERROR", result["error"])
        
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
