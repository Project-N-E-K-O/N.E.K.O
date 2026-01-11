from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class MemoryClient:
    ctx: Any

    def query(self, lanlan_name: str, query: str, *, timeout: float = 5.0) -> Dict[str, Any]:
        if not hasattr(self.ctx, "query_memory"):
            raise RuntimeError("ctx.query_memory is not available")
        result = self.ctx.query_memory(lanlan_name=lanlan_name, query=query, timeout=timeout)
        if not isinstance(result, dict):
            return {"result": result}
        return result
