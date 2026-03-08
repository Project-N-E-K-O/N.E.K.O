"""Call-chain contracts for SDK v2 shared runtime."""

from __future__ import annotations

from dataclasses import dataclass

from plugin.sdk_v2.shared.models import Result


class CircularCallError(RuntimeError):
    """Call-chain cycle detected."""


class CallChainTooDeepError(RuntimeError):
    """Call-chain max depth exceeded."""


@dataclass(slots=True)
class CallChainFrame:
    plugin_id: str
    event_type: str
    event_id: str


class CallChain:
    """Synchronous snapshot contract object."""


class AsyncCallChain:
    """Async helper contract object."""

    async def get(self) -> Result[list[CallChainFrame], Exception]:
        raise NotImplementedError("sdk_v2 contract-only facade: shared.runtime.call_chain not implemented")

    async def depth(self) -> Result[int, Exception]:
        raise NotImplementedError

    async def contains(self, plugin_id: str, event_id: str) -> Result[bool, Exception]:
        raise NotImplementedError


async def get_call_chain() -> Result[list[CallChainFrame], Exception]:
    raise NotImplementedError("sdk_v2 contract-only facade: shared.runtime.call_chain not implemented")


async def get_call_depth() -> Result[int, Exception]:
    raise NotImplementedError("sdk_v2 contract-only facade: shared.runtime.call_chain not implemented")


async def is_in_call_chain(plugin_id: str, event_id: str) -> Result[bool, Exception]:
    raise NotImplementedError("sdk_v2 contract-only facade: shared.runtime.call_chain not implemented")


__all__ = [
    "CircularCallError",
    "CallChainTooDeepError",
    "CallChainFrame",
    "CallChain",
    "AsyncCallChain",
    "get_call_chain",
    "get_call_depth",
    "is_in_call_chain",
]
