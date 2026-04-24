"""
时间窗口弹幕聚合器

功能：
- 按时间窗口缓冲弹幕
- 超过阈值时随机采样（>100条 -> 30条）
- 窗口大小由前端控制
- 定时 flush 触发回调
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Awaitable


@dataclass
class DanmakuEntry:
    """单条弹幕数据"""
    uid: int
    uname: str
    level: int
    text: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class BatchedDanmaku:
    """聚合后的弹幕批次"""
    entries: List[DanmakuEntry]
    total_count: int
    window_start: float
    window_end: float
    sampled: bool  # 是否经过了采样

    @property
    def count(self) -> int:
        return len(self.entries)


class TimeWindowAggregator:
    """
    时间窗口弹幕聚合器
    
    工作方式：
    1. add() 将弹幕加入当前窗口缓冲
    2. 每 window_size 秒触发一次 flush()
    3. flush() 取出当前窗口所有弹幕，超过 max_samples 则随机采样
    4. 通过 callback 通知上层
    """

    def __init__(
        self,
        callback: Callable[[BatchedDanmaku], Awaitable[None]],
        window_size: float = 15.0,
        max_samples: int = 30,
    ):
        """
        Args:
            callback: 聚合完成后调用的回调函数
            window_size: 时间窗口大小（秒），可由前端动态调整
            max_samples: 最大采样数，超过此数则随机采样
        """
        self.callback = callback
        self._window_size = window_size
        self.max_samples = max_samples

        # 当前窗口
        self._buffer: List[DanmakuEntry] = []
        self._window_start: float = 0.0

        # 定时器
        self._timer_task: Optional[asyncio.Task] = None
        self._running = False
        self._lock = asyncio.Lock()

        # 统计
        self.total_danmaku_received = 0
        self.total_batches_flushed = 0

    @property
    def window_size(self) -> float:
        return self._window_size

    @window_size.setter
    def window_size(self, value: float):
        """由前端动态调整窗口大小"""
        value = max(3.0, min(value, 180.0))
        self._window_size = value

    async def start(self):
        """启动定时器"""
        if self._running:
            return
        self._running = True
        self._window_start = time.time()
        self._timer_task = asyncio.create_task(self._tick_loop())

    async def stop(self):
        """停止定时器并刷新剩余缓冲"""
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass
            self._timer_task = None
        # 刷新剩余弹幕
        await self.flush()

    async def add(self, uid: int, uname: str, level: int, text: str):
        """添加一条弹幕到当前窗口"""
        entry = DanmakuEntry(uid=uid, uname=uname, level=level, text=text)
        async with self._lock:
            self._buffer.append(entry)
            self.total_danmaku_received += 1

    async def flush(self) -> Optional[BatchedDanmaku]:
        """刷新当前窗口，返回聚合批次"""
        async with self._lock:
            if not self._buffer:
                return None

            entries = self._buffer
            total = len(entries)
            window_end = time.time()
            window_start = self._window_start

            # 重置窗口
            self._buffer = []
            self._window_start = window_end

        # 采样处理（在锁外执行以避免长时间持有锁）
        sampled = False
        if total > self.max_samples:
            entries = random.sample(entries, self.max_samples)
            sampled = True

        batch = BatchedDanmaku(
            entries=entries,
            total_count=total,
            window_start=window_start,
            window_end=window_end,
            sampled=sampled,
        )

        self.total_batches_flushed += 1

        # 调用回调
        if self.callback:
            try:
                await self.callback(batch)
            except Exception as e:
                print(f"[Aggregator] callback 异常: {e}")

        return batch

    async def force_flush(self) -> Optional[BatchedDanmaku]:
        """强制刷新（外部调用用）"""
        return await self.flush()

    async def _tick_loop(self):
        """定时检查窗口到期"""
        while self._running:
            await asyncio.sleep(1.0)

            # 检查当前窗口是否到期
            async with self._lock:
                elapsed = time.time() - self._window_start
                if elapsed >= self._window_size and self._buffer:
                    need_flush = True
                else:
                    need_flush = False

            if need_flush:
                await self.flush()

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "buffer_size": len(self._buffer),
            "window_size": self._window_size,
            "window_elapsed": time.time() - self._window_start,
            "max_samples": self.max_samples,
            "total_received": self.total_danmaku_received,
            "total_batches": self.total_batches_flushed,
        }
