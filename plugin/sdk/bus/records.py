"""
Bus 记录和过滤器类型模块

从 types.py 拆分出来，包含基础数据类型。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Generic, List, Optional, TypeVar, TYPE_CHECKING

if TYPE_CHECKING:
    from plugin.sdk.bus.types import BusList

TRecord = TypeVar("TRecord", bound="BusRecord")


def parse_iso_timestamp(value: Any) -> Optional[float]:
    """解析 ISO 格式时间戳"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


@dataclass(frozen=True)
class BusFilter:
    """Bus 过滤器"""
    kind: Optional[str] = None
    type: Optional[str] = None
    plugin_id: Optional[str] = None
    source: Optional[str] = None
    kind_re: Optional[str] = None
    type_re: Optional[str] = None
    plugin_id_re: Optional[str] = None
    source_re: Optional[str] = None
    content_re: Optional[str] = None
    priority_min: Optional[int] = None
    since_ts: Optional[float] = None
    until_ts: Optional[float] = None


@dataclass(frozen=True, slots=True)
class BusRecord:
    """Bus 记录基类"""
    kind: str
    type: str
    timestamp: Optional[float]
    plugin_id: Optional[str] = None
    source: Optional[str] = None
    priority: int = 0
    content: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def dump(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "type": self.type,
            "timestamp": self.timestamp,
            "plugin_id": self.plugin_id,
            "source": self.source,
            "priority": self.priority,
            "content": self.content,
            "metadata": dict(self.metadata or {}),
            "raw": dict(self.raw or {}),
        }


class BusFilterError(ValueError):
    """Bus 过滤器错误"""
    pass


class NonReplayableTraceError(RuntimeError):
    """不可重放的追踪错误"""
    pass


@dataclass(frozen=True)
class BusFilterResult(Generic[TRecord]):
    """Bus 过滤结果"""
    ok: bool
    value: Optional["BusList[TRecord]"] = None
    error: Optional[Exception] = None


@dataclass(frozen=True)
class BusOp:
    """Bus 操作"""
    name: str
    params: Dict[str, Any]
    at: float


@dataclass(frozen=True)
class TraceNode:
    """追踪节点基类"""
    op: str
    params: Dict[str, Any]
    at: float

    def dump(self) -> Dict[str, Any]:
        return {
            "op": self.op,
            "params": dict(self.params) if isinstance(self.params, dict) else {},
            "at": self.at,
        }

    def explain(self) -> str:
        if self.params:
            return f"{self.op}({self.params})"
        return f"{self.op}()"


@dataclass(frozen=True)
class GetNode(TraceNode):
    """获取节点"""
    def dump(self) -> Dict[str, Any]:
        base = super().dump()
        base["kind"] = "get"
        return base


@dataclass(frozen=True)
class UnaryNode(TraceNode):
    """一元操作节点"""
    child: TraceNode

    def dump(self) -> Dict[str, Any]:
        base = super().dump()
        base["kind"] = "unary"
        base["child"] = self.child.dump()
        return base

    def explain(self) -> str:
        return self.child.explain() + " -> " + super().explain()


@dataclass(frozen=True)
class BinaryNode(TraceNode):
    """二元操作节点"""
    left: TraceNode
    right: TraceNode

    def dump(self) -> Dict[str, Any]:
        base = super().dump()
        base["kind"] = "binary"
        base["left"] = self.left.dump()
        base["right"] = self.right.dump()
        return base

    def explain(self) -> str:
        return f"({self.left.explain()}) {self.op} ({self.right.explain()})"


def _collect_get_nodes_fast(node: "TraceNode") -> List["GetNode"]:
    """Module-level function to collect GetNodes from a plan tree (iterative, faster)."""
    result: List["GetNode"] = []
    stack: List["TraceNode"] = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, GetNode):
            result.append(n)
        elif isinstance(n, UnaryNode):
            stack.append(n.child)
        elif isinstance(n, BinaryNode):
            stack.append(n.left)
            stack.append(n.right)
    return result


def _serialize_plan_fast(node: "TraceNode") -> Optional[Dict[str, Any]]:
    """Module-level function to serialize a plan tree to dict (iterative for common cases)."""
    try:
        if isinstance(node, GetNode):
            return {"kind": "get", "op": "get", "params": dict(node.params or {})}
        if isinstance(node, UnaryNode):
            child = _serialize_plan_fast(node.child)
            if child is None:
                return None
            return {
                "kind": "unary",
                "op": str(node.op),
                "params": dict(node.params or {}),
                "child": child,
            }
        if isinstance(node, BinaryNode):
            left = _serialize_plan_fast(node.left)
            right = _serialize_plan_fast(node.right)
            if left is None or right is None:
                return None
            return {
                "kind": "binary",
                "op": str(node.op),
                "params": dict(node.params or {}),
                "left": left,
                "right": right,
            }
    except Exception:
        return None
    return None
