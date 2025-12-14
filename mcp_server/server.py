"""
简单的 MCP 服务器实现
用于测试和演示 N.E.K.O 的 MCP 客户端连接
支持连接到其他 MCP 服务器并代理其工具
"""
import json
import logging
import os
import asyncio
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any, List, Optional, Union
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import httpx

logger = logging.getLogger(__name__)

# app 将在后面使用 lifespan 初始化

# MCP 协议版本
MCP_PROTOCOL_VERSION = "2024-11-05"

# 服务器信息
SERVER_INFO = {
    "name": "Simple-MCP-Server",
    "version": "1.0.0"
}

# 本地工具列表（保留几个简单工具）
LOCAL_TOOLS = [
    {
        "name": "echo",
        "description": "回显输入的文本",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "要回显的消息"
                }
            },
            "required": ["message"]
        }
    },
    {
        "name": "add",
        "description": "计算两个数字的和",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {
                    "type": "number",
                    "description": "第一个数字"
                },
                "b": {
                    "type": "number",
                    "description": "第二个数字"
                }
            },
            "required": ["a", "b"]
        }
    },
    {
        "name": "get_time",
        "description": "获取当前时间",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

# 全局工具列表（本地工具 + 远程工具）
TOOLS: List[Dict[str, Any]] = []

# 远程工具映射：工具名 -> 服务器标识符（URL 或 stdio 配置标识）
REMOTE_TOOL_MAPPING: Dict[str, str] = {}

# 服务器配置文件路径
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "servers.json")

# 远程 MCP 服务器配置（将在 load_servers_config 中初始化）
# 支持两种格式：
# - HTTP 服务器: "https://example.com/mcp" 或 {"type": "http", "url": "https://..."}
# - stdio 服务器: {"type": "stdio", "command": "npx", "args": ["bing-cn-mcp"]}
REMOTE_SERVERS: List[Union[str, Dict[str, Any]]] = []


def load_servers_config():
    """从文件加载服务器配置"""
    global REMOTE_SERVERS
    
    # 首先从环境变量加载（优先级最高）
    if os.getenv("MCP_REMOTE_SERVERS"):
        REMOTE_SERVERS = [url.strip() for url in os.getenv("MCP_REMOTE_SERVERS").split(",") if url.strip()]
        logger.info(f"[MCP Server] Loaded {len(REMOTE_SERVERS)} servers from environment variable")
    
    # 然后从配置文件加载（如果环境变量没有设置）
    if not REMOTE_SERVERS and os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                servers = config.get("servers", [])
                # 支持旧格式（字符串列表）和新格式（对象列表）
                REMOTE_SERVERS = []
                for server in servers:
                    if isinstance(server, str):
                        # 旧格式：直接是 URL 字符串
                        REMOTE_SERVERS.append(server)
                    elif isinstance(server, dict):
                        # 新格式：包含 type 的对象
                        REMOTE_SERVERS.append(server)
                    else:
                        logger.warning(f"[MCP Server] Invalid server config format: {server}")
                logger.info(f"[MCP Server] Loaded {len(REMOTE_SERVERS)} servers from config file")
        except Exception as e:
            logger.error(f"[MCP Server] Failed to load config file: {e}")
            REMOTE_SERVERS = []
    elif not REMOTE_SERVERS:
        # 如果文件不存在，创建默认配置
        REMOTE_SERVERS = []
        save_servers_config()


def save_servers_config():
    """保存服务器配置到文件"""
    try:
        config = {
            "servers": REMOTE_SERVERS,
            "updated_at": datetime.now().isoformat()
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info(f"[MCP Server] Saved {len(REMOTE_SERVERS)} servers to config file")
    except Exception as e:
        logger.error(f"[MCP Server] Failed to save config file: {e}")


def get_server_identifier(server_config: Union[str, Dict[str, Any]]) -> str:
    """获取服务器配置的标识符"""
    if isinstance(server_config, str):
        return server_config
    elif isinstance(server_config, dict):
        server_type = server_config.get("type", "http")
        if server_type == "stdio":
            command = server_config.get("command", "")
            args = server_config.get("args", [])
            return f"stdio:{command}:{':'.join(args)}"
        else:
            return server_config.get("url", "")
    return str(server_config)


# 启动时加载配置（在模块加载时执行）
load_servers_config()


class StdioMcpClient:
    """MCP 客户端，通过 stdio 连接到基于命令行的 MCP 服务器"""
    
    def __init__(self, command: str, args: List[str] = None, timeout: float = 10.0):
        self.command = command
        self.args = args or []
        self.timeout = timeout
        self._initialized = False
        self._request_id = 0
        self._process: Optional[subprocess.Popen] = None
        self._stdin_writer: Optional[asyncio.StreamWriter] = None
        self._stdout_reader: Optional[asyncio.StreamReader] = None
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._read_task: Optional[asyncio.Task] = None
        self._server_info: Optional[Dict[str, Any]] = None
        # 生成唯一标识符
        self.identifier = f"stdio:{command}:{':'.join(self.args)}"
    
    def _next_request_id(self) -> int:
        """生成下一个请求ID"""
        self._request_id += 1
        return self._request_id
    
    async def _start_process(self):
        """启动子进程"""
        if self._process:
            return
        
        logger.info(f"[Stdio MCP Client] Starting process: {self.command} {' '.join(self.args)}")
        try:
            # 启动子进程
            self._process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            
            # 创建读写流
            self._stdin_writer = self._process.stdin
            self._stdout_reader = self._process.stdout
            
            # 启动读取任务
            self._read_task = asyncio.create_task(self._read_responses())
            
            logger.debug(f"[Stdio MCP Client] Process started with PID: {self._process.pid}")
        except Exception as e:
            logger.error(f"[Stdio MCP Client] Failed to start process: {e}")
            raise
    
    async def _read_responses(self):
        """持续读取子进程的输出（JSON-RPC 响应）"""
        try:
            while True:
                if not self._stdout_reader:
                    break
                
                # 读取一行（MCP stdio 协议使用行分隔的 JSON）
                line = await self._stdout_reader.readline()
                if not line:
                    break
                
                line = line.decode('utf-8').strip()
                if not line:
                    continue
                
                try:
                    response = json.loads(line)
                    request_id = response.get("id")
                    
                    if request_id in self._pending_requests:
                        future = self._pending_requests.pop(request_id)
                        if "error" in response:
                            future.set_exception(Exception(f"JSON-RPC error: {response['error']}"))
                        else:
                            future.set_result(response.get("result"))
                    else:
                        logger.warning(f"[Stdio MCP Client] Received response for unknown request ID: {request_id}")
                except json.JSONDecodeError as e:
                    logger.error(f"[Stdio MCP Client] Failed to parse JSON response: {line[:100]}, error: {e}")
                except Exception as e:
                    logger.error(f"[Stdio MCP Client] Error processing response: {e}")
        except Exception:
            logger.exception("[Stdio MCP Client] Error in read loop")
        finally:
            # 进程输出结束时，收敛所有未完成请求
            for _rid, fut in list(self._pending_requests.items()):
                if not fut.done():
                    fut.set_exception(Exception("stdio mcp process closed"))
            self._pending_requests.clear()
    
    async def _mcp_request(self, method: str, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """发送 MCP JSON-RPC 2.0 请求"""
        if not self._process or not self._stdin_writer:
            await self._start_process()
        
        request_id = self._next_request_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        
        logger.debug(f"[Stdio MCP Client] Sending {method} request (ID: {request_id})")
        
        # 创建 Future 等待响应
        future = asyncio.Future()
        self._pending_requests[request_id] = future
        
        try:
            # 发送请求（JSON 行）
            request_line = json.dumps(payload) + "\n"
            self._stdin_writer.write(request_line.encode('utf-8'))
            await self._stdin_writer.drain()
            
            # 等待响应（带超时）
            try:
                result = await asyncio.wait_for(future, timeout=self.timeout)
                logger.debug(f"[Stdio MCP Client] Successfully received response for {method}")
                return result
            except asyncio.TimeoutError:
                self._pending_requests.pop(request_id, None)
                logger.error(f"[Stdio MCP Client] Timeout waiting for response to {method}")
                return None
            except Exception as e:
                self._pending_requests.pop(request_id, None)
                logger.error(f"[Stdio MCP Client] Error in request {method}: {e}")
                return None
        except Exception as e:
            self._pending_requests.pop(request_id, None)
            logger.error(f"[Stdio MCP Client] Failed to send request {method}: {e}")
            return None
    
    async def initialize(self) -> bool:
        """初始化 MCP 连接"""
        if self._initialized:
            logger.debug(f"[Stdio MCP Client] Already initialized")
            return True
        
        logger.info(f"[Stdio MCP Client] Initializing connection to {self.command} {' '.join(self.args)}...")
        
        try:
            await self._start_process()
            
            # 发送 initialize 请求
            result = await self._mcp_request("initialize", {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "Simple-MCP-Server-Client",
                    "version": "1.0.0"
                }
            })
            
            if result:
                self._initialized = True
                self._server_info = result.get("serverInfo", {})
                server_name = self._server_info.get("name", "Unknown")
                server_version = self._server_info.get("version", "Unknown")
                protocol_version = result.get("protocolVersion", "Unknown")
                logger.info(f"[Stdio MCP Client] ✅ Successfully initialized connection")
                logger.info(f"[Stdio MCP Client]    Server: {server_name} v{server_version}")
                logger.info(f"[Stdio MCP Client]    Protocol: {protocol_version}")
                return True
            else:
                logger.error(f"[Stdio MCP Client] ❌ Failed to initialize connection")
                return False
        except Exception as e:
            logger.error(f"[Stdio MCP Client] Unexpected error during initialize: {e}")
            return False
    
    async def list_tools(self) -> List[Dict[str, Any]]:
        """获取工具列表"""
        if not self._initialized:
            await self.initialize()
        
        logger.info(f"[Stdio MCP Client] Requesting tools list...")
        result = await self._mcp_request("tools/list", None)
        if result and "tools" in result:
            tools = result["tools"]
            logger.info(f"[Stdio MCP Client] ✅ Received {len(tools)} tools")
            for tool in tools:
                tool_name = tool.get("name", "unknown")
                tool_desc = tool.get("description", "No description")
                logger.debug(f"[Stdio MCP Client]    Tool: {tool_name} - {tool_desc}")
            return tools
        else:
            logger.warning(f"[Stdio MCP Client] ⚠️  No tools received")
            return []
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """调用工具"""
        if not self._initialized:
            await self.initialize()
        
        logger.info(f"[Stdio MCP Client] Calling tool '{tool_name}' with arguments: {arguments}")
        result = await self._mcp_request("tools/call", {
            "name": tool_name,
            "arguments": arguments or {}
        })
        
        if result:
            logger.info(f"[Stdio MCP Client] ✅ Tool '{tool_name}' executed successfully")
        else:
            logger.error(f"[Stdio MCP Client] ❌ Tool '{tool_name}' execution failed")
        
        return result
    
    async def close(self):
        """关闭连接"""
        logger.info(f"[Stdio MCP Client] Closing connection to {self.command}")
        
        # 取消读取任务
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        
        # 关闭进程
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"[Stdio MCP Client] Process did not terminate, killing...")
                self._process.kill()
                await self._process.wait()
            except Exception as e:
                logger.error(f"[Stdio MCP Client] Error closing process: {e}")
        
        # 关闭流
        if self._stdin_writer:
            self._stdin_writer.close()
            await self._stdin_writer.wait_closed()
        
        self._process = None
        self._stdin_writer = None
        self._stdout_reader = None
        self._initialized = False
        logger.debug(f"[Stdio MCP Client] Connection closed")


class McpClient:
    """MCP 客户端，用于连接到其他 MCP 服务器（HTTP 传输）"""
    
    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: float = 10.0):
        self.base_url = base_url.rstrip('/')
        # 检查 URL 是否已经包含 /mcp 路径，避免重复添加
        # Remote MCP 服务的 URL 通常已经包含完整的路径，如: https://xxx.com/xxx/mcp
        if self.base_url.endswith('/mcp'):
            self.mcp_endpoint = self.base_url
        else:
            self.mcp_endpoint = f"{self.base_url}/mcp"
        self.api_key = api_key
        self._initialized = False
        self._request_id = 0
        self._session_id: Optional[str] = None  # MCP session ID（用于 Remote 服务）
        
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
        }
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        
        self.http = httpx.AsyncClient(
            timeout=timeout,
            headers=headers
        )
    
    def _next_request_id(self) -> int:
        """生成下一个请求ID"""
        self._request_id += 1
        return self._request_id
    
    async def _mcp_notification(self, method: str, params: Dict[str, Any] = None) -> None:
        """发送 MCP JSON-RPC 2.0 通知（不需要响应）
        
        Args:
            method: MCP 方法名
            params: 通知参数，None 表示不包含 params 字段
        """
        payload = {
            "jsonrpc": "2.0",
            "method": method,
        }
        # 通知不包含 id 字段
        if params is not None:
            payload["params"] = params
        
        # 准备请求头（如果需要 session ID，添加到请求头）
        request_headers = {}
        if self._session_id:
            request_headers['mcp-session-id'] = self._session_id
        
        try:
            # 通知不需要等待响应，但某些服务器可能仍会返回响应
            resp = await self.http.post(self.mcp_endpoint, json=payload, headers=request_headers)
            logger.debug(f"[MCP Client] Notification {method} sent, status: {resp.status_code}")
        except Exception as e:
            logger.debug(f"[MCP Client] Notification {method} failed (non-critical): {e}")
    
    async def _mcp_request(self, method: str, params: Dict[str, Any] = None, return_error: bool = False) -> Optional[Dict[str, Any]]:
        """发送 MCP JSON-RPC 2.0 请求
        
        Args:
            method: MCP 方法名
            params: 请求参数，None 表示不包含 params 字段
            return_error: 如果为 True，返回包含错误信息的字典；如果为 False，错误时返回 None
        
        Returns:
            成功时返回 result，失败时根据 return_error 返回 None 或包含错误信息的字典
        """
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": method,
        }
        # 只有当 params 不为 None 时才添加 params 字段
        # 对于不需要参数的方法（如 tools/list），不包含 params 字段
        if params is not None:
            payload["params"] = params
        
        # 添加详细的调试日志（避免泄露敏感数据）
        import json
        logger.debug(f"[MCP Client] Sending {method} request to {self.base_url}")
        # 只记录非敏感字段，避免泄露参数中的敏感信息
        safe_payload = {
            "jsonrpc": payload.get("jsonrpc"),
            "id": payload.get("id"),
            "method": payload.get("method")
        }
        logger.debug(f"[MCP Client] Request payload (redacted): {json.dumps(safe_payload, indent=2)}")
        
        # 准备请求头（如果需要 session ID，添加到请求头）
        request_headers = {}
        if self._session_id:
            request_headers['mcp-session-id'] = self._session_id
            logger.debug(f"[MCP Client] Using session ID: {self._session_id[:20]}...")
        
        try:
            resp = await self.http.post(self.mcp_endpoint, json=payload, headers=request_headers)
            logger.debug(f"[MCP Client] Response status: {resp.status_code} from {self.base_url}")
            
            # 检查响应头中是否有 session ID（Remote MCP 服务可能在响应头中返回）
            session_id_header = resp.headers.get('mcp-session-id') or resp.headers.get('MCP-Session-ID')
            if session_id_header and not self._session_id:
                self._session_id = session_id_header
                logger.debug(f"[MCP Client] Received session ID from {self.base_url}")
            
            resp.raise_for_status()
            
            result = resp.json()
            logger.debug(f"[MCP Client] Response received from {self.base_url}")
            
            if "error" in result:
                error_info = result['error']
                logger.error(f"[MCP Client] JSON-RPC error from {self.base_url}: method={method}, error={error_info}")
                if return_error:
                    return {"error": error_info}
                return None
            
            logger.debug(f"[MCP Client] Successfully received response for {method} from {self.base_url}")
            return result.get("result")
        except httpx.HTTPStatusError as e:
            error_text = e.response.text[:200] if e.response.text else ""
            logger.error(f"[MCP Client] HTTP error {e.response.status_code} from {self.base_url}: {error_text}")
            return None
        except httpx.RequestError as e:
            logger.error(f"[MCP Client] Request error to {self.base_url}: {e}")
            return None
        except Exception as e:
            logger.error(f"[MCP Client] Unexpected error for {self.base_url}: {e}")
            return None
    
    async def initialize(self) -> bool:
        """初始化 MCP 连接"""
        if self._initialized:
            logger.debug(f"[MCP Client] Already initialized to {self.base_url}")
            return True
        
        logger.info(f"[MCP Client] Initializing connection to {self.base_url}...")
        logger.debug(f"[MCP Client] MCP endpoint: {self.mcp_endpoint}")
        
        # initialize 请求不使用 session ID（这是获取 session 的请求）
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "Simple-MCP-Server-Client",
                    "version": "1.0.0"
                }
            }
        }
        
        try:
            # initialize 请求不携带 session ID
            resp = await self.http.post(self.mcp_endpoint, json=payload)
            logger.debug(f"[MCP Client] Response status: {resp.status_code} from {self.base_url}")
            
            # 检查响应头中是否有 session ID（Remote MCP 服务通常在响应头中返回）
            session_id_header = resp.headers.get('mcp-session-id') or resp.headers.get('MCP-Session-ID')
            if session_id_header:
                self._session_id = session_id_header
                logger.debug(f"[MCP Client] Received session ID: {self._session_id[:20]}...")
            
            resp.raise_for_status()
            
            result = resp.json()
            if "error" in result:
                error_info = result['error']
                logger.error(f"[MCP Client] JSON-RPC error from {self.base_url}: error={error_info}")
                return False
            
            init_result = result.get("result")
            if init_result:
                self._initialized = True
                server_info = init_result.get("serverInfo", {})
                server_name = server_info.get("name", "Unknown")
                server_version = server_info.get("version", "Unknown")
                protocol_version = init_result.get("protocolVersion", "Unknown")
                logger.info(f"[MCP Client] ✅ Successfully initialized connection to {self.base_url}")
                logger.info(f"[MCP Client]    Server: {server_name} v{server_version}")
                logger.info(f"[MCP Client]    Protocol: {protocol_version}")
                if self._session_id:
                    logger.info(f"[MCP Client]    Session ID: {self._session_id[:20]}...")
                
                # 发送 initialized 通知（某些 MCP 服务器需要此通知）
                logger.debug(f"[MCP Client] Sending initialized notification...")
                await self._mcp_notification("notifications/initialized", {})
                
                return True
            else:
                logger.error(f"[MCP Client] ❌ Failed to initialize connection to {self.base_url}")
                return False
        except httpx.HTTPStatusError as e:
            error_text = e.response.text[:200] if e.response.text else ""
            logger.error(f"[MCP Client] HTTP error {e.response.status_code} from {self.base_url}: {error_text}")
            return False
        except Exception as e:
            logger.error(f"[MCP Client] Unexpected error during initialize: {e}")
            return False
    
    async def list_tools(self) -> List[Dict[str, Any]]:
        """获取工具列表"""
        if not self._initialized:
            await self.initialize()
        
        logger.info(f"[MCP Client] Requesting tools list from {self.base_url}...")
        # 尝试不同的参数格式以兼容不同的 MCP 服务器实现
        # 某些服务器可能不接受 params 字段，某些可能需要空对象
        
        # 先尝试不包含 params 字段（标准 MCP 协议方式）
        result = await self._mcp_request("tools/list", None, return_error=True)
        
        # 检查是否是参数错误（-32602），如果是则尝试使用空的 params 对象
        if result and "error" in result:
            error_code = result["error"].get("code")
            if error_code == -32602:  # Invalid request parameters
                logger.debug(f"[MCP Client] Invalid parameters error detected, retrying with empty params object...")
                result = await self._mcp_request("tools/list", {}, return_error=True)
        
        # 如果仍有错误，返回空列表
        if result and "error" in result:
            logger.warning(f"[MCP Client] ⚠️  Failed to get tools list: {result['error']}")
            return []
        
        # 检查结果中是否包含 tools
        if result and "tools" in result:
            tools = result["tools"]
            logger.info(f"[MCP Client] ✅ Received {len(tools)} tools from {self.base_url}")
            for tool in tools:
                tool_name = tool.get("name", "unknown")
                tool_desc = tool.get("description", "No description")
                logger.debug(f"[MCP Client]    Tool: {tool_name} - {tool_desc}")
            return tools
        else:
            logger.warning(f"[MCP Client] ⚠️  No tools received from {self.base_url}")
            return []
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """调用工具"""
        if not self._initialized:
            await self.initialize()
        
        logger.info(f"[MCP Client] Calling tool '{tool_name}' on {self.base_url} with arguments: {arguments}")
        result = await self._mcp_request("tools/call", {
            "name": tool_name,
            "arguments": arguments or {}
        })
        
        if result:
            logger.info(f"[MCP Client] ✅ Tool '{tool_name}' executed successfully on {self.base_url}")
        else:
            logger.error(f"[MCP Client] ❌ Tool '{tool_name}' execution failed on {self.base_url}")
        
        return result
    
    async def close(self):
        """关闭连接"""
        logger.info(f"[MCP Client] Closing connection to {self.base_url}")
        await self.http.aclose()
        logger.debug(f"[MCP Client] Connection to {self.base_url} closed")
    
    @property
    def identifier(self) -> str:
        """返回客户端标识符（用于映射）"""
        return self.base_url


# 全局 MCP 客户端字典（支持 HTTP 和 stdio 客户端）
_mcp_clients: Dict[str, Union[McpClient, StdioMcpClient]] = {}

# 全局状态锁，保护并发访问
_state_lock = asyncio.Lock()


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    logger.info("=" * 60)
    logger.info("[MCP Server] 🚀 Server startup event triggered")
    logger.info("=" * 60)
    await connect_to_remote_servers()
    logger.info("[MCP Server] ✅ Server startup completed")
    
    yield
    
    # 关闭时
    logger.info("=" * 60)
    logger.info("[MCP Server] 🛑 Server shutdown event triggered")
    logger.info(f"[MCP Server] Closing {len(_mcp_clients)} remote connection(s)...")
    for server_url, client in _mcp_clients.items():
        await client.close()
    _mcp_clients.clear()
    logger.info("[MCP Server] ✅ All connections closed")
    logger.info("=" * 60)


# 初始化 FastAPI 应用，使用 lifespan 事件处理器（必须在路由定义之前）
app = FastAPI(title="Simple MCP Server", version="1.0.0", lifespan=lifespan)


def create_jsonrpc_response(request_id: Any, result: Any = None, error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """创建 JSON-RPC 2.0 响应"""
    response = {
        "jsonrpc": "2.0",
        "id": request_id
    }
    if error:
        response["error"] = error
    else:
        response["result"] = result
    return response


def create_jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    """创建 JSON-RPC 错误响应"""
    error = {
        "code": code,
        "message": message
    }
    if data is not None:
        error["data"] = data
    return create_jsonrpc_response(request_id, error=error)


async def handle_initialize(params: Dict[str, Any]) -> Dict[str, Any]:
    """处理 initialize 请求"""
    protocol_version = params.get("protocolVersion", MCP_PROTOCOL_VERSION)
    client_info = params.get("clientInfo", {})
    
    logger.info(f"[MCP Server] Initialize request from {client_info.get('name', 'Unknown')} (version {client_info.get('version', 'Unknown')})")
    
    return {
        "protocolVersion": protocol_version,
        "capabilities": {
            "tools": {}
        },
        "serverInfo": SERVER_INFO
    }


async def handle_tools_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """处理 tools/list 请求"""
    logger.info(f"[MCP Server] Tools list request")
    # 返回合并后的工具列表（本地 + 远程）
    return {
        "tools": TOOLS
    }


async def handle_tools_call(params: Dict[str, Any]) -> Dict[str, Any]:
    """处理 tools/call 请求"""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})
    
    logger.info(f"[MCP Server] 📞 Tool call request: {tool_name}")
    logger.debug(f"[MCP Server]    Arguments: {arguments}")
    
    if not tool_name:
        logger.error("[MCP Server] ❌ Tool name is required")
        raise ValueError("Tool name is required")
    
    # 检查是否是远程工具
    if tool_name in REMOTE_TOOL_MAPPING:
        server_identifier = REMOTE_TOOL_MAPPING[tool_name]
        logger.info(f"[MCP Server] 🔄 Routing to remote server: {server_identifier}")
        client = _mcp_clients.get(server_identifier)
        
        if client:
            result = await client.call_tool(tool_name, arguments)
            if result:
                logger.info(f"[MCP Server] ✅ Remote tool '{tool_name}' executed successfully")
                return result
            else:
                logger.error(f"[MCP Server] ❌ Remote tool '{tool_name}' execution failed")
                raise ValueError(f"Failed to call remote tool '{tool_name}' from {server_identifier}")
        else:
            logger.error(f"[MCP Server] ❌ No client available for remote server {server_identifier}")
            raise ValueError(f"No client available for remote server {server_identifier}")
    
    # 查找本地工具
    tool = next((t for t in LOCAL_TOOLS if t["name"] == tool_name), None)
    if not tool:
        raise ValueError(f"Tool '{tool_name}' not found")
    
    # 执行本地工具
    if tool_name == "echo":
        message = arguments.get("message", "")
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Echo: {message}"
                }
            ]
        }
    
    elif tool_name == "add":
        a = arguments.get("a", 0)
        b = arguments.get("b", 0)
        result = a + b
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"{a} + {b} = {result}"
                }
            ]
        }
    
    elif tool_name == "get_time":
        current_time = datetime.now().isoformat()
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Current time: {current_time}"
                }
            ]
        }
    
    else:
        raise ValueError(f"Tool '{tool_name}' is not implemented")


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    """
    MCP 协议端点
    处理 JSON-RPC 2.0 请求
    """
    try:
        # 解析请求
        body = await request.json()
        
        # 验证 JSON-RPC 格式
        if body.get("jsonrpc") != "2.0":
            return JSONResponse(
                status_code=400,
                content=create_jsonrpc_error(
                    body.get("id"),
                    -32600,
                    "Invalid Request",
                    "jsonrpc must be '2.0'"
                )
            )
        
        method = body.get("method")
        params = body.get("params", {})
        request_id = body.get("id")
        
        if not method:
            return JSONResponse(
                status_code=400,
                content=create_jsonrpc_error(
                    request_id,
                    -32600,
                    "Invalid Request",
                    "method is required"
                )
            )
        
        logger.debug(f"[MCP Server] Received method: {method}, id: {request_id}")
        
        # 路由到对应的处理方法
        if method == "initialize":
            result = await handle_initialize(params)
        elif method == "tools/list":
            result = await handle_tools_list(params)
        elif method == "tools/call":
            try:
                result = await handle_tools_call(params)
            except ValueError as e:
                return JSONResponse(
                    status_code=200,
                    content=create_jsonrpc_error(
                        request_id,
                        -32602,
                        "Invalid params",
                        str(e)
                    )
                )
        else:
            return JSONResponse(
                status_code=200,
                content=create_jsonrpc_error(
                    request_id,
                    -32601,
                    "Method not found",
                    f"Method '{method}' is not supported"
                )
            )
        
        # 返回成功响应
        response = create_jsonrpc_response(request_id, result)
        return JSONResponse(content=response)
        
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=400,
            content=create_jsonrpc_error(
                None,
                -32700,
                "Parse error",
                "Invalid JSON"
            )
        )
    except Exception:
        logger.exception("[MCP Server] Unexpected error")
        return JSONResponse(
            status_code=500,
            content=create_jsonrpc_error(
                body.get("id") if 'body' in locals() else None,
                -32603,
                "Internal error",
                str(e)
            )
        )


async def connect_to_remote_servers():
    """连接到远程 MCP 服务器并获取工具"""
    global TOOLS, REMOTE_TOOL_MAPPING
    
    async with _state_lock:
        # 清理旧映射和连接，避免残留脏数据
        REMOTE_TOOL_MAPPING.clear()
        # 关闭所有现有连接
        for client in list(_mcp_clients.values()):
            try:
                await client.close()
            except Exception:
                pass
        _mcp_clients.clear()
        
        # 初始化工具列表为本地工具
        TOOLS = LOCAL_TOOLS.copy()
        logger.info(f"[MCP Server] Initialized with {len(LOCAL_TOOLS)} local tools: {[t['name'] for t in LOCAL_TOOLS]}")
        
        if not REMOTE_SERVERS:
            logger.info("[MCP Server] No remote servers configured, using local tools only")
            return
        
        logger.info("=" * 60)
        logger.info(f"[MCP Server] Starting connection to {len(REMOTE_SERVERS)} remote server(s)...")
        logger.info("=" * 60)
        
        connected_count = 0
        failed_count = 0
        
        for idx, server_config in enumerate(REMOTE_SERVERS, 1):
            server_identifier = get_server_identifier(server_config)
            logger.info(f"[MCP Server] [{idx}/{len(REMOTE_SERVERS)}] Processing server: {server_identifier}")
            
            try:
                # 判断服务器类型
                if isinstance(server_config, str):
                    # HTTP 服务器（旧格式）
                    client = McpClient(server_config)
                elif isinstance(server_config, dict):
                    server_type = server_config.get("type", "http")
                    if server_type == "stdio":
                        # stdio 服务器
                        command = server_config.get("command", "")
                        args = server_config.get("args", [])
                        if not command:
                            logger.error(f"[MCP Server] ❌ stdio server config missing 'command' field")
                            failed_count += 1
                            continue
                        client = StdioMcpClient(command, args)
                    else:
                        # HTTP 服务器（新格式）
                        server_url = server_config.get("url", "")
                        if not server_url:
                            logger.error(f"[MCP Server] ❌ HTTP server config missing 'url' field")
                            failed_count += 1
                            continue
                        api_key = server_config.get("api_key")
                        client = McpClient(server_url, api_key)
                else:
                    logger.error(f"[MCP Server] ❌ Invalid server config format: {server_config}")
                    failed_count += 1
                    continue
                
                # 初始化连接
                if await client.initialize():
                    # 获取工具列表
                    remote_tools = await client.list_tools()
                    
                    if remote_tools:
                        # 保存客户端
                        _mcp_clients[server_identifier] = client
                        connected_count += 1
                        
                        # 添加远程工具到工具列表
                        added_count = 0
                        skipped_count = 0
                        for tool in remote_tools:
                            tool_name = tool.get("name")
                            if tool_name:
                                # 检查是否有名称冲突
                                if any(t["name"] == tool_name for t in TOOLS):
                                    logger.warning(f"[MCP Server] ⚠️  Tool '{tool_name}' already exists, skipping from {server_identifier}")
                                    skipped_count += 1
                                    continue
                                
                                TOOLS.append(tool)
                                REMOTE_TOOL_MAPPING[tool_name] = server_identifier
                                added_count += 1
                                logger.info(f"[MCP Server]    ✅ Added tool: {tool_name}")
                        
                        logger.info(f"[MCP Server] ✅ Successfully connected to {server_identifier}")
                        logger.info(f"[MCP Server]    Added {added_count} tools, skipped {skipped_count} duplicate(s)")
                    else:
                        logger.warning(f"[MCP Server] ⚠️  Connected to {server_identifier} but no tools found")
                        await client.close()
                        failed_count += 1
                else:
                    logger.error(f"[MCP Server] ❌ Failed to initialize connection to {server_identifier}")
                    await client.close()
                    failed_count += 1
                    
            except Exception as e:
                logger.error(f"[MCP Server] ❌ Error connecting to {server_identifier}: {e}")
                logger.exception("[MCP Server] Exception details:")
                failed_count += 1
        
        # 连接摘要
        logger.info("=" * 60)
        logger.info(f"[MCP Server] Connection Summary:")
        logger.info(f"  ✅ Successfully connected: {connected_count}/{len(REMOTE_SERVERS)}")
        logger.info(f"  ❌ Failed connections: {failed_count}/{len(REMOTE_SERVERS)}")
        logger.info(f"  📦 Total tools: {len(TOOLS)} ({len(LOCAL_TOOLS)} local, {len(TOOLS) - len(LOCAL_TOOLS)} remote)")
        logger.info(f"  🔗 Active connections: {len(_mcp_clients)}")
        logger.info("=" * 60)
        
        # 列出所有可用工具
        if TOOLS:
            logger.info(f"[MCP Server] Available tools:")
            for tool in TOOLS:
                tool_name = tool.get("name")
                is_remote = tool_name in REMOTE_TOOL_MAPPING
                source = REMOTE_TOOL_MAPPING.get(tool_name, "local")
                logger.info(f"  - {tool_name} ({'remote' if is_remote else 'local'} from {source})")


@app.get("/health")
async def health_check():
    """健康检查端点"""
    local_count = len(LOCAL_TOOLS)
    remote_count = len(TOOLS) - local_count
    return {
        "status": "ok",
        "server": SERVER_INFO,
        "tools_count": len(TOOLS),
        "local_tools": local_count,
        "remote_tools": remote_count,
        "connected_servers": len(_mcp_clients)
    }


@app.get("/")
async def root():
    """根端点"""
    local_count = len(LOCAL_TOOLS)
    remote_count = len(TOOLS) - local_count
    return {
        "name": SERVER_INFO["name"],
        "version": SERVER_INFO["version"],
        "protocol": "MCP (Model Context Protocol)",
        "endpoint": "/mcp",
        "tools": len(TOOLS),
        "local_tools": local_count,
        "remote_tools": remote_count,
        "connected_servers": len(_mcp_clients)
    }


@app.get("/status")
async def get_status():
    """获取详细状态信息"""
    local_count = len(LOCAL_TOOLS)
    remote_count = len(TOOLS) - local_count
    
    # 分类工具
    local_tools_list = []
    remote_tools_list = []
    
    for tool in TOOLS:
        tool_name = tool.get("name", "unknown")
        tool_info = {
            "name": tool_name,
            "description": tool.get("description", "No description"),
            "inputSchema": tool.get("inputSchema", {})
        }
        
        if tool_name in REMOTE_TOOL_MAPPING:
            tool_info["source"] = REMOTE_TOOL_MAPPING[tool_name]
            remote_tools_list.append(tool_info)
        else:
            tool_info["source"] = "local"
            local_tools_list.append(tool_info)
    
    # 连接的服务器信息
    connected_servers_info = []
    for server_identifier, client in _mcp_clients.items():
        server_info = {
            "identifier": server_identifier,
            "initialized": client._initialized
        }
        # 根据客户端类型添加额外信息
        if isinstance(client, StdioMcpClient):
            server_info["type"] = "stdio"
            server_info["command"] = client.command
            server_info["args"] = client.args
        else:
            server_info["type"] = "http"
            server_info["url"] = client.base_url
        connected_servers_info.append(server_info)
    
    return {
        "server": SERVER_INFO,
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "statistics": {
            "total_tools": len(TOOLS),
            "local_tools": local_count,
            "remote_tools": remote_count,
            "connected_servers": len(_mcp_clients)
        },
        "local_tools": local_tools_list,
        "remote_tools": remote_tools_list,
        "connected_servers": connected_servers_info,
        "configured_remote_servers": REMOTE_SERVERS
    }


# /ui 路由已移除，使用 mount 提供静态文件服务
# 访问 /ui 会自动提供 index.html（如果存在），或访问 /ui/index.html


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Dashboard 重定向到 UI"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/ui/index.html")


@app.get("/api/servers")
async def get_servers():
    """获取配置的服务器列表"""
    servers_info = []
    for server in REMOTE_SERVERS:
        if isinstance(server, str):
            servers_info.append({
                "type": "http",
                "identifier": server,
                "url": server
            })
        elif isinstance(server, dict):
            server_type = server.get("type", "http")
            info = {
                "type": server_type,
                "identifier": get_server_identifier(server)
            }
            if server_type == "stdio":
                info["command"] = server.get("command")
                info["args"] = server.get("args", [])
            else:
                info["url"] = server.get("url")
            servers_info.append(info)
    
    return {
        "servers": servers_info,
        "connected": list(_mcp_clients.keys())
    }


@app.post("/api/servers")
async def add_server(request: Request):
    """添加新的 MCP 服务器（支持 HTTP 和 stdio）"""
    try:
        data = await request.json()
        server_type = data.get("type", "http")
        
        if server_type == "stdio":
            # stdio 服务器
            command = data.get("command", "").strip()
            args = data.get("args", [])
            
            if not command:
                return JSONResponse(
                    status_code=400,
                    content={"error": "command is required for stdio servers"}
                )
            
            if not isinstance(args, list):
                return JSONResponse(
                    status_code=400,
                    content={"error": "args must be a list"}
                )
            
            server_config = {
                "type": "stdio",
                "command": command,
                "args": args
            }
            server_identifier = get_server_identifier(server_config)
            
            # 检查是否已存在
            for existing in REMOTE_SERVERS:
                if get_server_identifier(existing) == server_identifier:
                    return JSONResponse(
                        status_code=400,
                        content={"error": "Server already exists"}
                    )
            
            # 添加到配置
            REMOTE_SERVERS.append(server_config)
            save_servers_config()
            
            logger.info(f"[MCP Server] Added new stdio server via API: {command} {' '.join(args)}")
            
            return {
                "success": True,
                "message": f"Stdio server {command} added successfully",
                "servers": REMOTE_SERVERS
            }
        else:
            # HTTP 服务器
            server_url = data.get("url", "").strip()
            
            if not server_url:
                return JSONResponse(
                    status_code=400,
                    content={"error": "URL is required for HTTP servers"}
                )
            
            # 验证 URL 格式
            if not server_url.startswith(("http://", "https://")):
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid URL format. Must start with http:// or https://"}
                )
            
            # 检查是否已存在
            for existing in REMOTE_SERVERS:
                if get_server_identifier(existing) == server_url:
                    return JSONResponse(
                        status_code=400,
                        content={"error": "Server already exists"}
                    )
            
            # 添加到配置（支持新格式，包含 api_key）
            server_config = {
                "type": "http",
                "url": server_url
            }
            if data.get("api_key"):
                server_config["api_key"] = data.get("api_key")
            
            REMOTE_SERVERS.append(server_config)
            save_servers_config()
            
            logger.info(f"[MCP Server] Added new HTTP server via API: {server_url}")
            
            return {
                "success": True,
                "message": f"Server {server_url} added successfully",
                "servers": REMOTE_SERVERS
            }
    except Exception as e:
        logger.error(f"[MCP Server] Error adding server: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.post("/api/servers/import")
async def import_remote_config(request: Request):
    """导入 Remote MCP 服务配置（JSON 格式）"""
    try:
        data = await request.json()
        config_json = data.get("config", "").strip()
        
        if not config_json:
            return JSONResponse(
                status_code=400,
                content={"error": "Config JSON is required"}
            )
        
        # 解析 JSON 配置
        try:
            config = json.loads(config_json)
        except json.JSONDecodeError as e:
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid JSON format: {str(e)}"}
            )
        
        # 解析 mcpServers 配置
        mcp_servers = config.get("mcpServers", {})
        if not mcp_servers:
            return JSONResponse(
                status_code=400,
                content={"error": "No mcpServers found in config"}
            )
        
        added_servers = []
        skipped_servers = []
        errors = []
        
        for server_name, server_config in mcp_servers.items():
            try:
                server_type = server_config.get("type", "")
                
                # 支持多种 HTTP 传输类型：streamable_http, sse, http
                if server_type in ("streamable_http", "sse", "http") or "url" in server_config:
                    # HTTP 服务器（包括 SSE 类型）
                    server_url = server_config.get("url", "").strip()
                    
                    if not server_url:
                        errors.append(f"{server_name}: URL is missing")
                        continue
                    
                    # 验证 URL 格式
                    if not server_url.startswith(("http://", "https://")):
                        errors.append(f"{server_name}: Invalid URL format")
                        continue
                    
                    # 构建配置对象
                    imported_config = {
                        "type": "http",
                        "url": server_url
                    }
                    if server_config.get("api_key"):
                        imported_config["api_key"] = server_config.get("api_key")
                    
                    server_identifier = get_server_identifier(imported_config)
                    
                    # 检查是否已存在
                    exists = False
                    for existing in REMOTE_SERVERS:
                        if get_server_identifier(existing) == server_identifier:
                            exists = True
                            break
                    
                    if exists:
                        skipped_servers.append({
                            "name": server_name,
                            "identifier": server_identifier,
                            "reason": "Already exists"
                        })
                        continue
                    
                    # 添加到配置
                    REMOTE_SERVERS.append(imported_config)
                    added_servers.append({
                        "name": server_name,
                        "identifier": server_identifier,
                        "type": "http"
                    })
                    
                    logger.info(f"[MCP Server] Imported HTTP server '{server_name}': {server_url}")
                    
                elif server_type == "stdio" or "command" in server_config:
                    # stdio 服务器
                    command = server_config.get("command", "").strip()
                    args = server_config.get("args", [])
                    
                    if not command:
                        errors.append(f"{server_name}: command is missing")
                        continue
                    
                    # 确保 args 是列表格式
                    if args is None:
                        args = []
                    elif not isinstance(args, list):
                        # 如果 args 不是列表，尝试转换
                        if isinstance(args, str):
                            args = [args]
                        else:
                            errors.append(f"{server_name}: args must be a list")
                            continue
                    
                    imported_config = {
                        "type": "stdio",
                        "command": command,
                        "args": args
                    }
                    
                    server_identifier = get_server_identifier(imported_config)
                    
                    # 检查是否已存在
                    exists = False
                    for existing in REMOTE_SERVERS:
                        if get_server_identifier(existing) == server_identifier:
                            exists = True
                            break
                    
                    if exists:
                        skipped_servers.append({
                            "name": server_name,
                            "identifier": server_identifier,
                            "reason": "Already exists"
                        })
                        continue
                    
                    # 添加到配置
                    REMOTE_SERVERS.append(imported_config)
                    added_servers.append({
                        "name": server_name,
                        "identifier": server_identifier,
                        "type": "stdio"
                    })
                    
                    logger.info(f"[MCP Server] Imported stdio server '{server_name}': {command} {' '.join(args)}")
                else:
                    errors.append(f"{server_name}: Unknown server type or missing required fields")
                
            except Exception as e:
                errors.append(f"{server_name}: {str(e)}")
        
        # 保存配置
        if added_servers:
            save_servers_config()
        
        return {
            "success": True,
            "message": f"Imported {len(added_servers)} server(s)",
            "added": added_servers,
            "skipped": skipped_servers,
            "errors": errors,
            "total_servers": len(REMOTE_SERVERS)
        }
        
    except Exception as e:
        logger.error(f"[MCP Server] Error importing config: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.delete("/api/servers")
async def delete_server(request: Request):
    """删除 MCP 服务器"""
    try:
        data = await request.json()
        server_identifier = data.get("identifier", "").strip()
        
        if not server_identifier:
            return JSONResponse(
                status_code=400,
                content={"error": "identifier is required"}
            )
        
        # 查找并移除服务器配置
        server_to_remove = None
        for server in REMOTE_SERVERS:
            if get_server_identifier(server) == server_identifier:
                server_to_remove = server
                break
        
        if not server_to_remove:
            return JSONResponse(
                status_code=404,
                content={"error": "Server not found"}
            )
        
        # 从配置中移除
        REMOTE_SERVERS.remove(server_to_remove)
        save_servers_config()
        
        # 如果已连接，关闭连接
        if server_identifier in _mcp_clients:
            await _mcp_clients[server_identifier].close()
            del _mcp_clients[server_identifier]
            
            # 移除该服务器的工具
            tools_to_remove = [name for name, ident in REMOTE_TOOL_MAPPING.items() if ident == server_identifier]
            for tool_name in tools_to_remove:
                del REMOTE_TOOL_MAPPING[tool_name]
                TOOLS[:] = [t for t in TOOLS if t.get("name") != tool_name]
        
        logger.info(f"[MCP Server] Removed server via API: {server_identifier}")
        
        return {
            "success": True,
            "message": f"Server {server_identifier} removed successfully",
            "servers": REMOTE_SERVERS
        }
    except Exception as e:
        logger.error(f"[MCP Server] Error removing server: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.post("/api/reconnect")
async def reconnect_servers():
    """重新连接所有配置的服务器"""
    try:
        logger.info("[MCP Server] Reconnecting to all servers via API...")
        
        # 关闭现有连接
        for server_url, client in list(_mcp_clients.items()):
            await client.close()
        _mcp_clients.clear()
        REMOTE_TOOL_MAPPING.clear()
        
        # 重新连接
        await connect_to_remote_servers()
        
        return {
            "success": True,
            "message": "Reconnected to all servers",
            "connected_servers": len(_mcp_clients),
            "total_tools": len(TOOLS)
        }
    except Exception as e:
        logger.error(f"[MCP Server] Error reconnecting: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


# 挂载静态文件（UI文件）- 必须在所有路由定义之后
# 注意：访问 /ui 会提供目录内容，访问 /ui/index.html 获取主页面
ui_dir = os.path.join(os.path.dirname(__file__), "ui")
if os.path.exists(ui_dir):
    app.mount("/ui", StaticFiles(directory=ui_dir), name="ui")


def check_port_available(host: str, port: int) -> bool:
    """检查端口是否可用"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex((host, port))
            return result != 0  # 0 表示端口被占用
    except Exception:
        return True  # 如果检查失败，假设端口可用


if __name__ == "__main__":
    import sys
    
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 固定使用端口 3282（必须）
    REQUIRED_PORT = 3282
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    
    # 如果提供了远程服务器参数（作为第二个参数）- 命令行参数会追加到配置文件中
    # 注意：现在主要通过 Web 界面配置，命令行参数仅用于初始配置
    if len(sys.argv) > 2:
        new_servers = [url.strip() for url in sys.argv[2].split(",") if url.strip()]
        for server_url in new_servers:
            if server_url not in REMOTE_SERVERS:
                REMOTE_SERVERS.append(server_url)
        if new_servers:
            save_servers_config()
            logger.info(f"[MCP Server] Added {len(new_servers)} server(s) from command line arguments")
    
    logger.info(f"[MCP Server] Web UI available at: http://{host}:{REQUIRED_PORT}/ui")
    logger.info(f"[MCP Server] You can manage servers via Web UI instead of command line arguments")
    
    # 检查端口 3282 是否可用
    if not check_port_available(host, REQUIRED_PORT):
        logger.error(f"[MCP Server] 错误：端口 {REQUIRED_PORT} 已被占用！")
        logger.error(f"[MCP Server] server.py 必须使用端口 {REQUIRED_PORT}，无法更改。")
        logger.error(f"[MCP Server] 解决方案：")
        logger.error(f"  1. 关闭占用端口 {REQUIRED_PORT} 的程序")
        logger.error(f"  2. Windows: netstat -ano | findstr :{REQUIRED_PORT}")
        logger.error(f"  3. Linux/Mac: lsof -i :{REQUIRED_PORT}")
        logger.error(f"  4. 等待端口释放后重试")
        sys.exit(1)
    
    logger.info(f"[MCP Server] Starting server on {host}:{REQUIRED_PORT}")
    logger.info(f"[MCP Server] MCP endpoint: http://{host}:{REQUIRED_PORT}/mcp")
    logger.info(f"[MCP Server] Local tools: {', '.join([t['name'] for t in LOCAL_TOOLS])}")
    if REMOTE_SERVERS:
        # 将服务器配置转换为字符串表示
        server_strs = []
        for server in REMOTE_SERVERS:
            if isinstance(server, str):
                server_strs.append(server)
            elif isinstance(server, dict):
                server_type = server.get("type", "http")
                if server_type == "stdio":
                    command = server.get("command", "")
                    args = server.get("args", [])
                    server_strs.append(f"stdio:{command} {' '.join(args)}")
                else:
                    server_strs.append(server.get("url", str(server)))
            else:
                server_strs.append(str(server))
        logger.info(f"[MCP Server] Remote servers configured: {', '.join(server_strs)}")
    
    # 运行服务器（启动事件会自动连接远程服务器）
    try:
        uvicorn.run(app, host=host, port=REQUIRED_PORT)
    except OSError as e:
        if "Address already in use" in str(e) or "address is already in use" in str(e).lower():
            logger.error(f"[MCP Server] 错误：端口 {REQUIRED_PORT} 已被占用！")
            logger.error(f"[MCP Server] server.py 必须使用端口 {REQUIRED_PORT}，无法更改。")
            logger.error(f"[MCP Server] 解决方案：")
            logger.error(f"  1. 关闭占用端口 {REQUIRED_PORT} 的程序")
            logger.error(f"  2. Windows: netstat -ano | findstr :{REQUIRED_PORT}")
            logger.error(f"  3. Linux/Mac: lsof -i :{REQUIRED_PORT}")
            logger.error(f"  4. 等待端口释放后重试")
            sys.exit(1)
        else:
            raise

