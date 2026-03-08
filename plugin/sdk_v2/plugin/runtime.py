"""sdk_v2.plugin.runtime

SDD contract-only runtime surface.

This module defines complete API contracts for plugin runtime primitives:
- method signatures
- input/output structures
- boundary conditions
- error semantics (`Result` + `ok/fail` envelope compatibility)

No runtime behavior is implemented in this phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, MutableMapping, Optional, Protocol, TypedDict

from plugin.sdk_v2.shared.models import Err, Ok, Result, ResultError, bind_result, capture, is_err, is_ok, map_err_result, map_result, match_result, must, raise_for_err, unwrap, unwrap_or
from plugin.sdk_v2.shared.models.errors import ErrorCode
from plugin.sdk_v2.shared.models.responses import fail, is_envelope, ok
from plugin.sdk_v2.shared.models.version import SDK_VERSION

EVENT_META_ATTR = "__neko_event_meta__"
HOOK_META_ATTR = "__neko_hook_meta__"
EXTENDED_TYPES: Dict[str, Any] = {}


class ErrorDetail(TypedDict, total=False):
    code: str
    message: str
    details: Any
    retriable: bool


class OkEnvelope(TypedDict, total=False):
    success: Literal[True]
    code: int
    data: Any
    message: str
    error: None
    time: str
    trace_id: Optional[str]
    meta: Dict[str, Any]


class ErrEnvelope(TypedDict, total=False):
    success: Literal[False]
    code: int
    data: None
    message: str
    error: ErrorDetail
    time: str
    trace_id: Optional[str]
    meta: Dict[str, Any]


Envelope = OkEnvelope | ErrEnvelope


class PluginConfigError(RuntimeError):
    """Config API contract violation / remote error."""


class PluginCallError(RuntimeError):
    """Cross-plugin call contract violation / timeout / transport error."""


class PluginRouterError(RuntimeError):
    """Router API contract violation / duplicate entry conflict."""


class CircularCallError(RuntimeError):
    """Call-chain cycle detected."""


class CallChainTooDeepError(RuntimeError):
    """Call-chain max depth exceeded."""


@dataclass(slots=True)
class EventMeta:
    """Event metadata contract.

    Boundary constraints:
    - `event_type` non-empty.
    - `id` non-empty and unique in plugin scope.
    - `input_schema` JSON-schema-like object when present.
    """

    event_type: str
    id: str
    name: str = ""
    description: str = ""
    input_schema: Dict[str, Any] | None = None


@dataclass(slots=True)
class EventHandler:
    meta: EventMeta
    handler: Any


@dataclass(slots=True)
class HookMeta:
    target: str = "*"
    timing: str = "before"
    priority: int = 0
    condition: str | None = None


HookHandler = Any
HookTiming = str


class HookExecutorMixin:
    """Hook pipeline executor contract."""

    def __init_hook_executor__(self) -> None:
        raise NotImplementedError("sdk_v2 contract-only facade: runtime not implemented")


class PluginContextProtocol(Protocol):
    """Minimal context protocol for contract typing."""

    plugin_id: str


class PluginConfig:
    """Plugin config contract (async-only)."""

    def __init__(self, _ctx: PluginContextProtocol):
        raise NotImplementedError("sdk_v2 contract-only facade: runtime not implemented")

    async def dump(self, *, timeout: float = 5.0) -> Result[Mapping[str, Any], Exception]:
        """Return effective config snapshot.

        Boundary constraints:
        - timeout must be > 0
        - on timeout/network error: Err(Exception)
        """
        raise NotImplementedError

    async def get(self, path: str, default: Any = None, *, timeout: float = 5.0) -> Result[Any, Exception]:
        """Read scalar/object by dotted path."""
        raise NotImplementedError

    async def require(self, path: str, *, timeout: float = 5.0) -> Result[Any, Exception]:
        """Read path and fail when missing."""
        raise NotImplementedError

    async def set(self, path: str, value: Any, *, timeout: float = 5.0) -> Result[None, Exception]:
        """Write path with value."""
        raise NotImplementedError

    async def update(self, patch: Mapping[str, Any], *, timeout: float = 5.0) -> Result[Mapping[str, Any], Exception]:
        """Merge patch into config and return updated snapshot."""
        raise NotImplementedError

    async def get_section(self, path: str, *, timeout: float = 5.0) -> Result[Mapping[str, Any], Exception]:
        """Read section as mapping."""
        raise NotImplementedError


class Plugins:
    """Cross-plugin call contract (async-only)."""

    def __init__(self, _ctx: PluginContextProtocol):
        raise NotImplementedError("sdk_v2 contract-only facade: runtime not implemented")

    async def call_entry(self, entry_ref: str, args: Optional[Mapping[str, Any]] = None, *, timeout: float = 10.0) -> Result[Envelope | Any, Exception]:
        """Call `<plugin_id>:<entry_id>`.

        Boundary constraints:
        - `entry_ref` must contain ':'
        - timeout must be > 0
        """
        raise NotImplementedError

    async def call_event(self, event_ref: str, args: Optional[Mapping[str, Any]] = None, *, timeout: float = 10.0) -> Result[Envelope | Any, Exception]:
        """Call `<plugin_id>:<event_type>:<event_id>` custom event."""
        raise NotImplementedError

    async def list(self, *, timeout: float = 5.0) -> Result[List[Mapping[str, Any]], Exception]:
        """List discoverable plugins."""
        raise NotImplementedError

    async def require(self, plugin_id: str, *, timeout: float = 5.0) -> Result[Mapping[str, Any], Exception]:
        """Ensure plugin exists and return descriptor."""
        raise NotImplementedError


class PluginRouter:
    """Dynamic entry router contract."""

    def __init__(self, *, prefix: str = "", name: str | None = None):
        raise NotImplementedError("sdk_v2 contract-only facade: runtime not implemented")

    async def add_entry(self, entry_id: str, handler: Any, *, name: str | None = None, description: str = "", input_schema: Optional[Mapping[str, Any]] = None, replace: bool = False) -> Result[bool, Exception]:
        """Add dynamic entry.

        Boundary constraints:
        - `entry_id` must be non-empty.
        - duplicate id without `replace=True` -> Err(PluginRouterError).
        """
        raise NotImplementedError

    async def remove_entry(self, entry_id: str) -> Result[bool, Exception]:
        """Remove dynamic entry by id."""
        raise NotImplementedError

    async def list_entries(self) -> Result[List[EventMeta], Exception]:
        """Return currently mounted dynamic entries."""
        raise NotImplementedError


class CallChain:
    """Synchronous call-chain snapshot contract."""


class AsyncCallChain:
    """Async call-chain helper contract."""


def get_call_chain() -> List[str]:
    """Return current call chain stack."""
    raise NotImplementedError("sdk_v2 contract-only facade: runtime not implemented")


def get_call_depth() -> int:
    """Return current call depth."""
    raise NotImplementedError("sdk_v2 contract-only facade: runtime not implemented")


def is_in_call_chain(_plugin_id: str, _entry_id: str) -> bool:
    """Check if `(plugin_id, entry_id)` exists in chain."""
    raise NotImplementedError("sdk_v2 contract-only facade: runtime not implemented")


class SystemInfo:
    """System information contract."""

    def __init__(self, _ctx: PluginContextProtocol):
        raise NotImplementedError("sdk_v2 contract-only facade: runtime not implemented")

    async def get_system_config(self, *, timeout: float = 5.0) -> Result[Mapping[str, Any], Exception]:
        raise NotImplementedError

    def get_python_env(self) -> Mapping[str, Any]:
        raise NotImplementedError


class MemoryClient:
    """Memory query contract."""

    def __init__(self, _ctx: PluginContextProtocol):
        raise NotImplementedError("sdk_v2 contract-only facade: runtime not implemented")

    async def query(self, bucket_id: str, query: str, *, timeout: float = 5.0) -> Result[Any, Exception]:
        raise NotImplementedError

    async def get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[List[Mapping[str, Any]], Exception]:
        raise NotImplementedError


class PluginStore:
    """KV store contract (async-only)."""

    def __init__(self, *args: Any, **kwargs: Any):
        raise NotImplementedError("sdk_v2 contract-only facade: runtime not implemented")

    async def get(self, key: str, default: Any = None) -> Result[Any, Exception]:
        raise NotImplementedError

    async def set(self, key: str, value: Any) -> Result[None, Exception]:
        raise NotImplementedError

    async def delete(self, key: str) -> Result[bool, Exception]:
        raise NotImplementedError


class PluginKVStore:
    """Database-backed KV contract (async-only)."""

    def __init__(self, *args: Any, **kwargs: Any):
        raise NotImplementedError("sdk_v2 contract-only facade: runtime not implemented")


class PluginDatabase:
    """Database contract (async-only)."""

    def __init__(self, *args: Any, **kwargs: Any):
        raise NotImplementedError("sdk_v2 contract-only facade: runtime not implemented")


class PluginStatePersistence:
    """State persistence contract (async-only)."""

    def __init__(self, *args: Any, **kwargs: Any):
        raise NotImplementedError("sdk_v2 contract-only facade: runtime not implemented")


__all__ = [
    "SDK_VERSION",
    "ErrorCode",
    "ok",
    "fail",
    "is_envelope",
    "Envelope",
    "OkEnvelope",
    "ErrEnvelope",
    "ErrorDetail",
    "PluginConfig",
    "PluginConfigError",
    "Plugins",
    "PluginCallError",
    "PluginRouter",
    "PluginRouterError",
    "EventMeta",
    "EventHandler",
    "EVENT_META_ATTR",
    "HookMeta",
    "HookHandler",
    "HookTiming",
    "HOOK_META_ATTR",
    "HookExecutorMixin",
    "CallChain",
    "AsyncCallChain",
    "CircularCallError",
    "CallChainTooDeepError",
    "get_call_chain",
    "get_call_depth",
    "is_in_call_chain",
    "SystemInfo",
    "MemoryClient",
    "PluginStore",
    "PluginDatabase",
    "PluginKVStore",
    "PluginStatePersistence",
    "EXTENDED_TYPES",
    "PluginContextProtocol",
    "Ok",
    "Err",
    "Result",
    "ResultError",
    "is_ok",
    "is_err",
    "map_result",
    "map_err_result",
    "bind_result",
    "match_result",
    "unwrap",
    "unwrap_or",
    "raise_for_err",
    "must",
    "capture",
]
