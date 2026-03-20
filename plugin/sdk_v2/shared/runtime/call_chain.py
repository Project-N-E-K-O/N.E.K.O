"""Call-chain helpers for SDK v2 shared runtime."""

from __future__ import annotations

import time
from contextvars import ContextVar
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any

from plugin.sdk_v2.shared.models import Ok, Result
from plugin.sdk_v2.shared.models.exceptions import CallChainErrorLike, CallChainTooDeepError, CircularCallError


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

    _chain_var: ContextVar[tuple[CallInfo, ...]] = ContextVar("sdk_v2_call_chain", default=())
    _call_ids_var: ContextVar[frozenset[str]] = ContextVar("sdk_v2_call_ids", default=frozenset())
    DEFAULT_MAX_DEPTH = 20
    DEFAULT_WARN_DEPTH = 10

    @classmethod
    def _get_chain(cls) -> list[CallInfo]:
        return list(cls._chain_var.get())

    @classmethod
    def _get_call_ids(cls) -> set[str]:
        return set(cls._call_ids_var.get())

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
        cls._chain_var.set(())
        cls._call_ids_var.set(frozenset())

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
            except Exception as error:
                debug = getattr(logger, "debug", None)
                if callable(debug):
                    try:
                        debug(f"Failed to log call-chain warning: {error}")
                    except Exception:
                        pass
        info = CallInfo(
            call_id=call_id,
            start_time=time.time(),
            caller_plugin=caller_plugin,
            caller_entry=caller_entry,
            metadata=dict(metadata or {}),
        )
        chain_token = cls._chain_var.set((*chain, info))
        call_ids_token = cls._call_ids_var.set(frozenset((*call_ids, call_id)))
        try:
            yield info
        finally:
            cls._call_ids_var.reset(call_ids_token)
            cls._chain_var.reset(chain_token)


class AsyncCallChain:
    """Async helper wrapper over the `ContextVar`-backed per-task call-chain."""

    @staticmethod
    def is_available() -> bool:
        return True

    async def get(self) -> Result[list[CallChainFrame], CallChainErrorLike]:
        return await get_call_chain()

    async def depth(self) -> Result[int, CallChainErrorLike]:
        return await get_call_depth()

    async def contains(self, plugin_id: str, event_id: str) -> Result[bool, CallChainErrorLike]:
        return await is_in_call_chain(plugin_id, event_id)

    async def get_current_chain(self) -> Result[list[CallChainFrame], CallChainErrorLike]:
        return await get_call_chain()

    async def get_depth(self) -> Result[int, CallChainErrorLike]:
        return await get_call_depth()

    @asynccontextmanager
    async def track(
        self,
        plugin_id: str,
        event_type: str,
        event_id: str,
        *,
        max_depth: int = CallChain.DEFAULT_MAX_DEPTH,
        warn_depth: int = CallChain.DEFAULT_WARN_DEPTH,
        allow_reentry: bool = False,
        caller_plugin: str | None = None,
        caller_entry: str | None = None,
        metadata: dict[str, Any] | None = None,
        logger: Any = None,
    ):
        call_id = f"{plugin_id}.{event_type}:{event_id}"
        with CallChain.track(
            call_id,
            max_depth=max_depth,
            warn_depth=warn_depth,
            allow_reentry=allow_reentry,
            caller_plugin=caller_plugin,
            caller_entry=caller_entry,
            metadata=metadata,
            logger=logger,
        ):
            yield CallChainFrame(plugin_id=plugin_id, event_type=event_type, event_id=event_id)

    async def format_chain(self) -> Result[str, CallChainErrorLike]:
        return Ok(CallChain.format_chain())

# `call_id` is expected to look like `plugin.event_type:event_id`.
# Missing parts fall back to `plugin_id="unknown"`, `event_type="entry"`,
# and `event_id=<tail-or-original-call_id>` so callers still get a usable frame.
def _split_call_id(call_id: str) -> CallChainFrame:
    plugin_id, _, tail = call_id.partition(".")
    event_type, _, event_id = tail.partition(":")
    if not event_type:
        event_type = "entry"
    if not event_id:
        event_id = tail or call_id
    return CallChainFrame(plugin_id=plugin_id or "unknown", event_type=event_type, event_id=event_id)


async def get_call_chain() -> Result[list[CallChainFrame], CallChainErrorLike]:
    return Ok([_split_call_id(call_id) for call_id in CallChain.get_current_chain()])


async def get_call_depth() -> Result[int, CallChainErrorLike]:
    return Ok(CallChain.get_depth())


async def is_in_call_chain(plugin_id: str, event_id: str) -> Result[bool, CallChainErrorLike]:
    for call_id in CallChain.get_current_chain():
        frame = _split_call_id(call_id)
        if frame.plugin_id == plugin_id and frame.event_id == event_id:
            return Ok(True)
    return Ok(False)


__all__ = [
    "AsyncCallChain",
    "CallChain",
    "CallChainFrame",
    "CallChainTooDeepError",
    "CallInfo",
    "CircularCallError",
    "get_call_chain",
    "get_call_depth",
    "is_in_call_chain",
]
