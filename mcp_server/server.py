"""
简单的 MCP 服务器实现
用于测试和演示 N.E.K.O 的 MCP 客户端连接
支持连接到其他 MCP 服务器并代理其工具
"""
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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

# 远程 MCP 服务器配置
# 可以通过环境变量 MCP_REMOTE_SERVERS 配置，格式：url1,url2,url3
REMOTE_SERVERS: List[str] = []
if os.getenv("MCP_REMOTE_SERVERS"):
    REMOTE_SERVERS = [url.strip() for url in os.getenv("MCP_REMOTE_SERVERS").split(",") if url.strip()]

# 远程工具映射：工具名 -> 服务器URL
REMOTE_TOOL_MAPPING: Dict[str, str] = {}


class McpClient:
    """MCP 客户端，用于连接到其他 MCP 服务器"""
    
    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: float = 10.0):
        self.base_url = base_url.rstrip('/')
        self.mcp_endpoint = f"{self.base_url}/mcp"
        self.api_key = api_key
        self._initialized = False
        self._request_id = 0
        
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
    
    async def _mcp_request(self, method: str, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """发送 MCP JSON-RPC 2.0 请求"""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": method,
        }
        if params:
            payload["params"] = params
        
        logger.debug(f"[MCP Client] Sending {method} request to {self.base_url}")
        
        try:
            resp = await self.http.post(self.mcp_endpoint, json=payload)
            logger.debug(f"[MCP Client] Response status: {resp.status_code} from {self.base_url}")
            resp.raise_for_status()
            
            result = resp.json()
            if "error" in result:
                error_info = result['error']
                logger.error(f"[MCP Client] JSON-RPC error from {self.base_url}: method={method}, error={error_info}")
                return None
            
            logger.debug(f"[MCP Client] Successfully received response for {method} from {self.base_url}")
            return result.get("result")
        except httpx.HTTPStatusError as e:
            logger.error(f"[MCP Client] HTTP error {e.response.status_code} from {self.base_url}: {e.response.text[:200]}")
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
            server_info = result.get("serverInfo", {})
            server_name = server_info.get("name", "Unknown")
            server_version = server_info.get("version", "Unknown")
            protocol_version = result.get("protocolVersion", "Unknown")
            logger.info(f"[MCP Client] ✅ Successfully initialized connection to {self.base_url}")
            logger.info(f"[MCP Client]    Server: {server_name} v{server_version}")
            logger.info(f"[MCP Client]    Protocol: {protocol_version}")
            return True
        else:
            logger.error(f"[MCP Client] ❌ Failed to initialize connection to {self.base_url}")
            return False
    
    async def list_tools(self) -> List[Dict[str, Any]]:
        """获取工具列表"""
        if not self._initialized:
            await self.initialize()
        
        logger.info(f"[MCP Client] Requesting tools list from {self.base_url}...")
        result = await self._mcp_request("tools/list", {})
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


# 全局 MCP 客户端字典
_mcp_clients: Dict[str, McpClient] = {}


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
        remote_url = REMOTE_TOOL_MAPPING[tool_name]
        logger.info(f"[MCP Server] 🔄 Routing to remote server: {remote_url}")
        client = _mcp_clients.get(remote_url)
        
        if client:
            result = await client.call_tool(tool_name, arguments)
            if result:
                logger.info(f"[MCP Server] ✅ Remote tool '{tool_name}' executed successfully")
                return result
            else:
                logger.error(f"[MCP Server] ❌ Remote tool '{tool_name}' execution failed")
                raise ValueError(f"Failed to call remote tool '{tool_name}' from {remote_url}")
        else:
            logger.error(f"[MCP Server] ❌ No client available for remote server {remote_url}")
            raise ValueError(f"No client available for remote server {remote_url}")
    
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
    except Exception as e:
        logger.exception(f"[MCP Server] Unexpected error: {e}")
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
    
    for idx, server_url in enumerate(REMOTE_SERVERS, 1):
        logger.info(f"[MCP Server] [{idx}/{len(REMOTE_SERVERS)}] Processing server: {server_url}")
        try:
            client = McpClient(server_url)
            
            # 初始化连接
            if await client.initialize():
                # 获取工具列表
                remote_tools = await client.list_tools()
                
                if remote_tools:
                    # 保存客户端
                    _mcp_clients[server_url] = client
                    connected_count += 1
                    
                    # 添加远程工具到工具列表
                    added_count = 0
                    skipped_count = 0
                    for tool in remote_tools:
                        tool_name = tool.get("name")
                        if tool_name:
                            # 检查是否有名称冲突
                            if any(t["name"] == tool_name for t in TOOLS):
                                logger.warning(f"[MCP Server] ⚠️  Tool '{tool_name}' already exists, skipping from {server_url}")
                                skipped_count += 1
                                continue
                            
                            TOOLS.append(tool)
                            REMOTE_TOOL_MAPPING[tool_name] = server_url
                            added_count += 1
                            logger.info(f"[MCP Server]    ✅ Added tool: {tool_name}")
                    
                    logger.info(f"[MCP Server] ✅ Successfully connected to {server_url}")
                    logger.info(f"[MCP Server]    Added {added_count} tools, skipped {skipped_count} duplicate(s)")
                else:
                    logger.warning(f"[MCP Server] ⚠️  Connected to {server_url} but no tools found")
                    await client.close()
                    failed_count += 1
            else:
                logger.error(f"[MCP Server] ❌ Failed to initialize connection to {server_url}")
                await client.close()
                failed_count += 1
                
        except Exception as e:
            logger.error(f"[MCP Server] ❌ Error connecting to {server_url}: {e}")
            logger.exception(f"[MCP Server] Exception details:")
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
    
    # 如果提供了远程服务器参数（作为第二个参数）
    if len(sys.argv) > 2:
        REMOTE_SERVERS.extend([url.strip() for url in sys.argv[2].split(",") if url.strip()])
    
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
        logger.info(f"[MCP Server] Remote servers configured: {', '.join(REMOTE_SERVERS)}")
    
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

