from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class RpcRequest:
    v: int
    op: str
    req_id: str
    args: Dict[str, Any]
    from_plugin: Optional[str] = None


def ok_response(req_id: str, result: Any) -> Dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "req_id": req_id, "ok": True, "result": result, "error": None}


def err_response(req_id: str, error: str) -> Dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "req_id": req_id, "ok": False, "result": None, "error": error}
