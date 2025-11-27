import asyncio
import logging
import time
from typing import Dict, Any, List, Optional
import httpx
from cachetools import TTLCache
from config import MCP_ROUTER_URL
from utils.config_manager import get_config_manager
from utils.logger_config import ThrottledLogger
import uuid

logger = logging.getLogger(__name__)

# 使用统一的速率限制日志记录器
_throttled_logger = ThrottledLogger(logger, interval=10.0)


class McpRouterClient:
    """
    MCP Router HTTP client using MCP protocol.
    
    MCP Router现在使用标准MCP协议通过HTTP传输 (端点: /mcp)
    参考: https://github.com/mcp-router/mcp-router
    """
    def __init__(self, base_url: str = None, api_key: str = None, timeout: float = 10.0):
        # 动态获取配置
        if base_url is None:
            base_url = MCP_ROUTER_URL
        if api_key is None:
            core_config = get_config_manager().get_core_config()
            api_key = core_config.get('MCP_ROUTER_API_KEY', '')
        
        self.base_url = base_url.rstrip('/')
        self.mcp_endpoint = f"{self.base_url}/mcp"  # MCP协议端点
        self.api_key = api_key
        self._initialized = False
        self._request_id = 0
        
        # 设置HTTP客户端
        # MCP Router要求同时接受JSON和SSE流
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream'
        }
        if self.api_key and self.api_key != 'Copy from MCP Router if needed':
            headers['Authorization'] = f'Bearer {self.api_key}'
        
        self.http = httpx.AsyncClient(timeout=timeout, headers=headers)
        
        # Cache tools listing for 10 seconds
        self._tools_cache: TTLCache[str, Any] = TTLCache(maxsize=1, ttl=10)
        
    def _next_request_id(self) -> int:
        """生成下一个请求ID"""
        self._request_id += 1
        return self._request_id
    
    async def _mcp_request(self, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        发送MCP JSON-RPC 2.0请求并处理SSE响应
        """
        import json
        
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": method,
        }
        if params:
            payload["params"] = params
            
        try:
            logger.debug(f"[MCP] Sending {method} request to {self.mcp_endpoint}")
            resp = await self.http.post(self.mcp_endpoint, json=payload)
            resp.raise_for_status()
            
            # 检查内容类型
            content_type = resp.headers.get('content-type', '')
            
            if 'text/event-stream' in content_type:
                # 处理SSE流响应
                # SSE格式: event: message\ndata: {...}\n\n
                logger.debug(f"[MCP] Parsing SSE response")
                response_text = resp.text
                
                # 解析SSE格式
                lines = response_text.split('\n')
                for i, line in enumerate(lines):
                    line = line.strip()
                    if line.startswith('data:'):
                        json_str = line[5:].strip()  # 去掉 "data: " 前缀
                        if not json_str:  # 跳过空data行
                            continue
                        try:
                            result = json.loads(json_str)
                            
                            # 检查JSON-RPC错误
                            if "error" in result:
                                error = result["error"]
                                logger.error(f"[MCP] JSON-RPC error: {error}")
                                return None
                            
                            # 返回result字段
                            if "result" in result:
                                return result["result"]
                            else:
                                logger.debug(f"[MCP] No result field in response: {result}")
                                return result
                        except json.JSONDecodeError as e:
                            logger.debug(f"[MCP] Failed to parse JSON: {json_str[:100]}, error: {e}")
                            continue
                
                logger.warning(f"[MCP] No valid JSON found in SSE response")
                return None
            else:
                # 处理普通JSON响应
                result = resp.json()
                
                # 检查JSON-RPC错误
                if "error" in result:
                    error = result["error"]
                    logger.error(f"[MCP] JSON-RPC error: {error}")
                    return None
                    
                return result.get("result")
                
        except httpx.HTTPStatusError as e:
            logger.error(f"[MCP] HTTP error {e.response.status_code}: {e.response.text}")
            return None
        except Exception as e:
            # 使用统一的速率限制日志记录器
            _throttled_logger.debug(f"mcp_request_{method}", f"[MCP] Request failed for {method}: {e}")
            return None
    
    async def initialize(self) -> bool:
        """初始化MCP连接"""
        if self._initialized:
            return True
            
        result = await self._mcp_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "PROJECT-NEKO-MCP-Client",
                "version": "1.0.0"
            }
        })
        
        if result:
            self._initialized = True
            logger.info(f"[MCP] Initialized successfully: {result.get('serverInfo', {}).get('name', 'Unknown')}")
            return True
        else:
            # Throttled in _mcp_request, no need to log again here
            return False

    async def list_tools(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        获取所有可用工具列表（通过MCP协议）
        返回工具列表，每个工具包含name, description等信息
        
        Args:
            force_refresh: 如果为True，忽略缓存强制刷新
        """
        # 检查缓存（除非强制刷新）
        if not force_refresh and 'tools' in self._tools_cache:
            cached_tools = self._tools_cache['tools']
            logger.debug(f"[MCP] Using cached tools: {len(cached_tools)} tools")
            return cached_tools
        
        # 确保已初始化
        if not self._initialized:
            await self.initialize()
        
        # 发送list_tools请求
        result = await self._mcp_request("tools/list", {})
        
        if result and "tools" in result:
            tools = result["tools"]
            # 只缓存成功的结果
            self._tools_cache['tools'] = tools
            logger.info(f"[MCP] Discovered {len(tools)} tools")
            return tools
        else:
            # Throttled in _mcp_request, no need to log again here
            # ⚠️ 失败时不缓存，下次会重试
            return []
    
    async def list_servers(self) -> List[Dict[str, Any]]:
        """
        兼容旧接口：从工具列表推断"服务器"
        注意：新版MCP Router不再有独立的servers端点
        我们将工具分组作为"服务器"返回
        """
        tools = await self.list_tools()
        
        # 按工具名称前缀分组（简化实现）
        servers = []
        if tools:
            servers.append({
                'identifier': 'mcp-router',
                'name': 'MCP Router',
                'description': f'Aggregated MCP tools ({len(tools)} tools available)',
                'status': 'active',
                'tool_count': len(tools)
            })
        
        return servers

    async def get_server_by_name(self, name_or_id: str) -> Optional[Dict[str, Any]]:
        servers = await self.list_servers()
        for s in servers:
            if s.get('identifier') == name_or_id or s.get('name') == name_or_id:
                return s
        return None

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        调用MCP工具
        """
        # 确保已初始化
        if not self._initialized:
            await self.initialize()
        
        # 发送call_tool请求
        result = await self._mcp_request("tools/call", {
            "name": tool_name,
            "arguments": arguments or {}
        })
        
        if result:
            logger.info(f"[MCP] Tool {tool_name} executed successfully")
            return {
                "success": True,
                "result": result,
                "tool": tool_name
            }
        else:
            logger.error(f"[MCP] Tool {tool_name} execution failed")
            return {
                "success": False,
                "error": "Tool execution failed",
                "tool": tool_name
            }

    async def aclose(self):
        await self.http.aclose()


class McpToolCatalog:
    """
    工具目录：从MCP Router获取可用工具并转换为LLM可用的格式
    """
    def __init__(self, router: McpRouterClient):
        self.router = router

    async def get_capabilities(self, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """
        获取所有可用工具的能力描述
        返回格式: {tool_name: {title, description, schema, ...}}
        
        Args:
            force_refresh: 如果为True，强制刷新工具列表
        """
        tools_list = await self.router.list_tools(force_refresh=force_refresh)
        
        # 转换为能力字典
        capabilities: Dict[str, Dict[str, Any]] = {}
        for tool in tools_list:
            tool_name = tool.get('name', 'unknown')
            capabilities[tool_name] = {
                'title': tool_name,
                'description': tool.get('description', ''),
                'input_schema': tool.get('inputSchema', {}),
                'type': 'mcp_tool'
            }
        
        logger.debug(f"[MCP] Loaded {len(capabilities)} tool capabilities")
        return capabilities


