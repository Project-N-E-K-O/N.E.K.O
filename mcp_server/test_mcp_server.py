"""
测试 MCP 服务器
一个独立的、可用的 MCP 服务器，用于测试 server.py 的 Router 功能
可以独立运行，提供测试工具供 Router 连接
"""
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

logger = logging.getLogger(__name__)

app = FastAPI(title="Test MCP Server", version="1.0.0")

# MCP 协议版本
MCP_PROTOCOL_VERSION = "2024-11-05"

# 服务器信息
SERVER_INFO = {
    "name": "Test-MCP-Server",
    "version": "1.0.0"
}

# 测试工具列表
TOOLS = [
    {
        "name": "test_multiply",
        "description": "计算两个数字的乘积（测试工具）",
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
        "name": "test_greet",
        "description": "生成问候语（测试工具）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要问候的名字"
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "test_get_date",
        "description": "获取当前日期（测试工具）",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "test_reverse",
        "description": "反转字符串（测试工具）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要反转的文本"
                }
            },
            "required": ["text"]
        }
    }
]


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
    
    logger.info(f"[Test MCP Server] Initialize request from {client_info.get('name', 'Unknown')} (version {client_info.get('version', 'Unknown')})")
    
    return {
        "protocolVersion": protocol_version,
        "capabilities": {
            "tools": {}
        },
        "serverInfo": SERVER_INFO
    }


async def handle_tools_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """处理 tools/list 请求"""
    logger.info(f"[Test MCP Server] Tools list request")
    return {
        "tools": TOOLS
    }


async def handle_tools_call(params: Dict[str, Any]) -> Dict[str, Any]:
    """处理 tools/call 请求"""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})
    
    logger.info(f"[Test MCP Server] Tool call: {tool_name} with args: {arguments}")
    
    if not tool_name:
        raise ValueError("Tool name is required")
    
    # 查找工具
    tool = next((t for t in TOOLS if t["name"] == tool_name), None)
    if not tool:
        raise ValueError(f"Tool '{tool_name}' not found")
    
    # 执行工具
    if tool_name == "test_multiply":
        a = arguments.get("a", 0)
        b = arguments.get("b", 0)
        result = a * b
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"{a} × {b} = {result}"
                }
            ]
        }
    
    elif tool_name == "test_greet":
        name = arguments.get("name", "World")
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Hello, {name}! This is a test tool from Test-MCP-Server."
                }
            ]
        }
    
    elif tool_name == "test_get_date":
        current_date = datetime.now().strftime("%Y-%m-%d")
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Current date: {current_date}"
                }
            ]
        }
    
    elif tool_name == "test_reverse":
        text = arguments.get("text", "")
        reversed_text = text[::-1]
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Reversed: {reversed_text}"
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
        
        logger.debug(f"[Test MCP Server] Received method: {method}, id: {request_id}")
        
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
        logger.exception(f"[Test MCP Server] Unexpected error: {e}")
        return JSONResponse(
            status_code=500,
            content=create_jsonrpc_error(
                body.get("id") if 'body' in locals() else None,
                -32603,
                "Internal error",
                str(e)
            )
        )


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "ok",
        "server": SERVER_INFO,
        "tools_count": len(TOOLS)
    }


@app.get("/")
async def root():
    """根端点"""
    return {
        "name": SERVER_INFO["name"],
        "version": SERVER_INFO["version"],
        "protocol": "MCP (Model Context Protocol)",
        "endpoint": "/mcp",
        "tools": len(TOOLS),
        "tools_list": [t["name"] for t in TOOLS]
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


def find_available_port(host: str, start_port: int, max_attempts: int = 10) -> int:
    """查找可用端口"""
    for i in range(max_attempts):
        port = start_port + i
        if check_port_available(host, port):
            return port
    raise RuntimeError(f"无法在 {host} 上找到可用端口（尝试了 {start_port} 到 {start_port + max_attempts - 1}）")


async def test_router_connection(router_url: str = "http://localhost:3282", test_server_url: str = None):
    """测试 Router (server.py) 是否成功连接了测试服务器"""
    import httpx
    import asyncio
    
    if not test_server_url:
        return
    
    logger.info("=" * 60)
    logger.info("[Test MCP Server] 🔍 Testing Router connection...")
    logger.info("=" * 60)
    
    # 等待一下，让 Router 有时间连接
    await asyncio.sleep(2)
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # 1. 检查 Router 健康状态
            logger.info(f"[Test MCP Server] Checking Router health: {router_url}/health")
            resp = await client.get(f"{router_url}/health")
            if resp.status_code == 200:
                health_data = resp.json()
                logger.info(f"[Test MCP Server] ✅ Router is healthy")
                logger.info(f"    Connected servers: {health_data.get('connected_servers', 0)}")
                logger.info(f"    Remote tools: {health_data.get('remote_tools', 0)}")
            else:
                logger.warning(f"[Test MCP Server] ⚠️  Router health check failed: {resp.status_code}")
                return
            
            # 2. 检查 Router 是否发现了测试服务器的工具
            logger.info(f"[Test MCP Server] Checking if Router discovered our tools...")
            mcp_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {}
            }
            resp = await client.post(f"{router_url}/mcp", json=mcp_payload)
            if resp.status_code == 200:
                result = resp.json()
                if "result" in result:
                    tools = result["result"].get("tools", [])
                    test_tools_found = [t for t in tools if t.get("name", "").startswith("test_")]
                    
                    logger.info(f"[Test MCP Server] Router has {len(tools)} total tools")
                    logger.info(f"[Test MCP Server] Found {len(test_tools_found)} test tools from this server:")
                    for tool in test_tools_found:
                        logger.info(f"    ✅ {tool.get('name')}: {tool.get('description', 'No description')}")
                    
                    if len(test_tools_found) == len(TOOLS):
                        logger.info("=" * 60)
                        logger.info("[Test MCP Server] ✅ SUCCESS: Router successfully connected and discovered all tools!")
                        logger.info("=" * 60)
                    elif len(test_tools_found) > 0:
                        logger.warning("=" * 60)
                        logger.warning(f"[Test MCP Server] ⚠️  PARTIAL: Router found {len(test_tools_found)}/{len(TOOLS)} tools")
                        logger.warning("=" * 60)
                    else:
                        logger.error("=" * 60)
                        logger.error("[Test MCP Server] ❌ FAILED: Router did not discover any test tools")
                        logger.error(f"[Test MCP Server] Make sure server.py is started with: python server.py localhost {test_server_url}")
                        logger.error("=" * 60)
                else:
                    logger.error(f"[Test MCP Server] ❌ Failed to get tools list: {result.get('error', 'Unknown error')}")
            else:
                logger.error(f"[Test MCP Server] ❌ Failed to connect to Router: {resp.status_code}")
        
        except Exception as e:
            logger.error(f"[Test MCP Server] ❌ Error testing Router connection: {e}")


if __name__ == "__main__":
    import sys
    import asyncio
    
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 检查是否启用自动测试（先检查，避免被当作端口号）
    test_router = "--test-router" in sys.argv or "-t" in sys.argv
    
    # 过滤掉测试标志参数
    args = [arg for arg in sys.argv[1:] if arg not in ["--test-router", "-t"]]
    
    # 默认端口 3283（用于测试）
    default_port = 3283
    port = int(args[0]) if len(args) > 0 and args[0].isdigit() else default_port
    host = args[1] if len(args) > 1 else "127.0.0.1"
    
    router_url = "http://localhost:3282"  # 默认 Router 地址
    
    # 检查端口是否可用
    if not check_port_available(host, port):
        logger.warning(f"[Test MCP Server] 端口 {port} 已被占用，尝试查找可用端口...")
        try:
            new_port = find_available_port(host, port)
            logger.info(f"[Test MCP Server] 找到可用端口: {new_port}")
            port = new_port
        except RuntimeError as e:
            logger.error(f"[Test MCP Server] {e}")
            logger.error(f"[Test MCP Server] 请手动指定其他端口: python test_mcp_server.py <port>")
            sys.exit(1)
    
    test_server_url = f"http://{host}:{port}"
    
    logger.info("=" * 60)
    logger.info(f"[Test MCP Server] Starting server on {host}:{port}")
    logger.info(f"[Test MCP Server] MCP endpoint: http://{host}:{port}/mcp")
    logger.info(f"[Test MCP Server] Available tools: {', '.join([t['name'] for t in TOOLS])}")
    logger.info("=" * 60)
    logger.info(f"[Test MCP Server] To connect from server.py, use:")
    logger.info(f"    python server.py localhost {test_server_url}")
    logger.info("=" * 60)
    
    if test_router:
        logger.info("[Test MCP Server] Auto-test mode enabled: will test Router connection after startup")
    
    # 启动服务器
    import threading
    
    def run_server():
        try:
            uvicorn.run(app, host=host, port=port, log_level="info")
        except OSError as e:
            if "Address already in use" in str(e) or "address is already in use" in str(e).lower():
                logger.error(f"[Test MCP Server] 端口 {port} 已被占用！")
                logger.error(f"[Test MCP Server] 解决方案：")
                logger.error(f"  1. 使用其他端口: python test_mcp_server.py <其他端口>")
                logger.error(f"  2. 关闭占用端口的程序")
                logger.error(f"  3. 等待端口释放后重试")
                sys.exit(1)
            else:
                raise
    
    # 如果启用了自动测试，在后台运行测试
    if test_router:
        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()
        
        # 等待服务器启动
        import time
        time.sleep(1)
        
        # 运行测试
        try:
            asyncio.run(test_router_connection(router_url, test_server_url))
        except KeyboardInterrupt:
            logger.info("\n[Test MCP Server] Test interrupted by user")
        except Exception as e:
            logger.error(f"[Test MCP Server] Test error: {e}")
        
        # 保持服务器运行
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\n[Test MCP Server] Shutting down...")
    else:
        # 正常模式：直接运行服务器
        run_server()
