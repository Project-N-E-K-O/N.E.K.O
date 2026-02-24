"""
MCP Router client — DISABLED.

MCP Router 功能已完全移除。此模块仅保留类骨架以避免导入报错，
所有方法均为空操作 (no-op)，不会发出任何 HTTP 请求。
"""
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class McpRouterClient:
    """Stub — 不发起任何网络请求。"""

    def __init__(self, base_url: str = None, api_key: str = None, timeout: float = 10.0):
        self._initialized = False

    async def initialize(self) -> bool:
        return False

    async def list_tools(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        return []

    async def list_servers(self) -> List[Dict[str, Any]]:
        return []

    async def get_server_by_name(self, name_or_id: str) -> Optional[Dict[str, Any]]:
        return None

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any] = None) -> Dict[str, Any]:
        return {"success": False, "error": "MCP disabled", "tool": tool_name}

    async def aclose(self):
        pass


class McpToolCatalog:
    """Stub — 始终返回空能力集。"""

    def __init__(self, router: McpRouterClient):
        self.router = router

    async def get_capabilities(self, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        return {}
