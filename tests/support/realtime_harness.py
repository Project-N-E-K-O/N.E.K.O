from __future__ import annotations
import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, urlparse
import pytest
_END = object()
_TIMEOUT = object()
import main_logic.asr_client._infra as asr_infra
from main_logic.asr_client._infra import (
    AsrSessionConfig,
    _AsrRequestQueue,
    _AsrWorkerEvent,
    _AsrWorkerRequest,
    _RealtimeAsrSessionImpl,
)
from main_logic.asr_client.workers import gemini, grok, openai, qwen, soniox, step
class _FakeWebSocket:
    def __init__(
        self,
        *,
        initial: list[dict[str, Any]] | None = None,
        on_send: Callable[["_FakeWebSocket", str | bytes], Awaitable[None]]
        | None = None,
    ) -> None:
        self.incoming: asyncio.Queue[str | object] = asyncio.Queue()
        self.sent: list[str | bytes] = []
        self.closed = False
        self.on_send = on_send
        for event in initial or []:
            self.incoming.put_nowait(json.dumps(event))

    async def send(self, payload: str | bytes) -> None:
        if self.closed:
            raise RuntimeError("fake websocket is closed")
        self.sent.append(payload)
        if self.on_send is not None:
            await self.on_send(self, payload)

    async def recv(self) -> str:
        message = await self.incoming.get()
        if message is _END:
            raise RuntimeError("fake websocket closed before ready")
        if message is _TIMEOUT:
            raise asyncio.TimeoutError
        assert isinstance(message, str)
        return message

    def __aiter__(self) -> _FakeWebSocket:
        return self

    async def __anext__(self) -> str:
        message = await self.incoming.get()
        if message is _END:
            raise StopAsyncIteration
        assert isinstance(message, str)
        return message

    async def server_send(self, event: dict[str, Any]) -> None:
        await self.incoming.put(json.dumps(event))

    async def server_end(self) -> None:
        await self.incoming.put(_END)

    async def server_timeout(self) -> None:
        # Makes the next recv() raise asyncio.TimeoutError, simulating a
        # bounded receive wait expiring without tearing the connection down.
        await self.incoming.put(_TIMEOUT)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.incoming.put(_END)
class _FakeConnector:
    def __init__(self, *websockets: _FakeWebSocket) -> None:
        self.websockets = list(websockets)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, url: str, **kwargs: Any) -> _FakeWebSocket:
        self.calls.append((url, kwargs))
        if not self.websockets:
            raise AssertionError("unexpected extra WebSocket connection")
        return self.websockets.pop(0)
async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("condition was not reached")
        await asyncio.sleep(0)
