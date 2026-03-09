"""Call-chain helpers for SDK v2 shared runtime."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from plugin.sdk_v2.shared.models import Ok, Result


class CircularCallError(RuntimeError):
    """Call-chain cycle detected."""

    def __init__(self, message: str, chain: list[str] | None = None, circular_call: str | None = None):
        super().__init__(message)
        self.chain = list(chain or [])
        self.circular_call = circular_call


class CallChainTooDeepError(RuntimeError):
    """Call-chain max depth exceeded."""

    def __init__(self, message: str, chain: list[str] | None = None, max_depth: int | None = None):
        super().__init__(message)
        self.chain = list(chain or [])
        self.max_depth = max_depth


@dataclass(slots=True)
class CallChainFrame:
    plugin_id: str
    event_type: str
    event_id: str


@dataclass(slots=True)
class CallInfo:
    call_id: str
    start_time: float
    caller_plugin: str | None = None
    caller_entry: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CallChain:
    """Synchronous call-chain tracker."""

    _local = threading.local()
    DEFAULT_MAX_DEPTH = 20
    DEFAULT_WARN_DEPTH = 10

    @classmethod
    def _get_chain(cls) -> list[CallInfo]:
        chain = getattr(cls._local, "chain", None)
        if chain is None:
            chain = []
            cls._local.chain = chain
        return chain

    @classmethod
    def _get_call_ids(cls) -> set[str]:
        call_ids = getattr(cls._local, "call_ids", None)
        if call_ids is None:
            call_ids = set()
            cls._local.call_ids = call_ids
        return call_ids

    @classmethod
    def get_current_chain(cls) -> list[str]:
        return [info.call_id for info in cls._get_chain()]

    @classmethod
    def get_depth(cls) -> int:
        return len(cls._get_chain())

    @classmethod
    def get_current_call(cls) -> CallInfo | None:
        chain = cls._get_chain()
        return chain[-1] if chain else None

    @classmethod
    def get_root_call(cls) -> CallInfo | None:
        chain = cls._get_chain()
        return chain[0] if chain else None

    @classmethod
    def is_in_call(cls, call_id: str) -> bool:
        return call_id in cls._get_call_ids()

    @classmethod
    def clear(cls) -> None:
        cls._local.chain = []
        cls._local.call_ids = set()

    @classmethod
    def format_chain(cls) -> str:
        return " -> ".join(cls.get_current_chain())

    @classmethod
    @contextmanager
    def track(
        cls,
        call_id: str,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        warn_depth: int = DEFAULT_WARN_DEPTH,
        allow_reentry: bool = False,
        caller_plugin: str | None = None,
        caller_entry: str | None = None,
        metadata: dict[str, Any] | None = None,
        logger: Any = None,
    ):
        chain = cls._get_chain()
        call_ids = cls._get_call_ids()
        if not allow_reentry and call_id in call_ids:
            raise CircularCallError(
                f"Circular call detected: {cls.format_chain()} -> {call_id}",
                chain=cls.get_current_chain(),
                circular_call=call_id,
            )
        current_depth = len(chain)
        if current_depth >= max_depth:
            raise CallChainTooDeepError(
                f"Call chain too deep ({current_depth} >= {max_depth}): {cls.format_chain()}",
                chain=cls.get_current_chain(),
                max_depth=max_depth,
            )
        if current_depth >= warn_depth and logger is not None:
            try:
                logger.warning("Call chain depth warning: %s", cls.format_chain())
            except Exception:
                pass
        info = CallInfo(
            call_id=call_id,
            start_time=time.time(),
            caller_plugin=caller_plugin,
            caller_entry=caller_entry,
            metadata=dict(metadata or {}),
        )
        chain.append(info)
        call_ids.add(call_id)
        try:
            yield info
        finally:
            if chain:
                popped = chain.pop()
                if popped.call_id in call_ids:
                    call_ids.remove(popped.call_id)


class AsyncCallChain:
    """Async helper wrapper over the process-local call-chain."""

    @staticmethod
    def is_available() -> bool:
        return True

    async def get(self) -> Result[list[CallChainFrame], Exception]:
        return await get_call_chain()

    async def depth(self) -> Result[int, Exception]:
        return await get_call_depth()

    async def contains(self, plugin_id: str, event_id: str) -> Result[bool, Exception]:
        return await is_in_call_chain(plugin_id, event_id)

    async def get_current_chain(self) -> Result[list[CallChainFrame], Exception]:
        return await get_call_chain()

    async def get_depth(self) -> Result[int, Exception]:
        return await get_call_depth()

    async def track(self, plugin_id: str, event_type: str, event_id: str) -> Result[CallChainFrame, Exception]:
        frame = CallChainFrame(plugin_id=plugin_id, event_type=event_type, event_id=event_id)
        return Ok(frame)

    async def format_chain(self) -> Result[str, Exception]:
        return Ok(CallChain.format_chain())


def _split_call_id(call_id: str) -> CallChainFrame:
    plugin_id, _, tail = call_id.partition(".")
    event_type, _, event_id = tail.partition(":")
    if not event_type:
        event_type = "entry"
    if not event_id:
        event_id = tail or call_id
    return CallChainFrame(plugin_id=plugin_id or "unknown", event_type=event_type, event_id=event_id)


async def get_call_chain() -> Result[list[CallChainFrame], Exception]:
    return Ok([_split_call_id(call_id) for call_id in CallChain.get_current_chain()])


async def get_call_depth() -> Result[int, Exception]:
    return Ok(CallChain.get_depth())


async def is_in_call_chain(plugin_id: str, event_id: str) -> Result[bool, Exception]:
    prefix = f"{plugin_id}."
    for call_id in CallChain.get_current_chain():
        if call_id.startswith(prefix) and call_id.endswith(event_id):
            return Ok(True)
    return Ok(False)


__all__ = [
    "CircularCallError",
    "CallChainTooDeepError",
    "CallChainFrame",
    "CallInfo",
    "CallChain",
    "AsyncCallChain",
    "get_call_chain",
    "get_call_depth",
    "is_in_call_chain",
]
