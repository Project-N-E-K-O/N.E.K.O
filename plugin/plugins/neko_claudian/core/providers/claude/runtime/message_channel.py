"""
1:1 ported from claudian/src/providers/claude/runtime/ClaudeMessageChannel.ts

异步消息通道 — 持久查询中 `put(turn)` 入队，`__aiter__` 异步出队。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

from .types import PreparedChatTurn


class MessageChannelClosed(Exception):
    pass


class ClaudeMessageChannel:
    """
    与 Claude Agent SDK 的 MessageChannel 行为对齐。
    - put(turn): 入队下一条 user turn
    - __aiter__(): 异步消费 turn
    - close(): 关闭（后续 put / iter 会抛异常）
    """

    def __init__(self, maxsize: int = 64):
        self._queue: asyncio.Queue[PreparedChatTurn] = asyncio.Queue(maxsize=maxsize)
        self._closed = False
        self._lock = asyncio.Lock()

    async def put(self, turn: PreparedChatTurn, *, timeout: Optional[float] = None):
        """入队一个 turn。"""
        async with self._lock:
            if self._closed:
                raise MessageChannelClosed("MessageChannel 已关闭")
        if timeout is not None:
            await asyncio.wait_for(self._queue.put(turn), timeout=timeout)
        else:
            await self._queue.put(turn)

    def try_put_nowait(self, turn: PreparedChatTurn) -> bool:
        """非阻塞入队。"""
        if self._closed:
            return False
        try:
            self._queue.put_nowait(turn)
            return True
        except asyncio.QueueFull:
            return False

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()

    async def __aiter__(self) -> AsyncIterator[PreparedChatTurn]:
        while True:
            turn = await self._queue.get()
            if turn is None:  # sentinel
                return
            yield turn

    async def close(self):
        async with self._lock:
            if not self._closed:
                self._closed = True
                # 注入 sentinel 唤醒等待中的消费者
                try:
                    self._queue.put_nowait(None)  # type: ignore[arg-type]
                except Exception:
                    pass

    @property
    def closed(self) -> bool:
        return self._closed
