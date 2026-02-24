"""
Processor module — DISABLED.

MCP Router 功能已移除。此模块仅保留类骨架以避免导入报错。
"""
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class Processor:
    """Stub — MCP 已移除，process() 始终返回 can_execute=False。"""

    def __init__(self):
        pass

    async def process(self, query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"can_execute": False, "reason": "MCP disabled"}
