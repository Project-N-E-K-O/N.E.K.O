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


class GiftAggregator:
    """
    礼物时间窗口聚合器

    功能：
    - 按时间窗口缓冲礼物/SC 事件
    - 同一窗口内按 (user_name, gift_name) 聚合，合并数量和总价值
    - 推送后进入冷却期，冷却期间礼物继续累积
    - 冷却结束后推送下一批次（如果有）
    - 提供 has_pending 属性供弹幕队列查询优先级
    """

    def __init__(
        self,
        callback: Callable[[list[dict]], Awaitable[None]],
        window_size: float = 5.0,
        cooldown: float = 7.0,
    ):
        self.callback = callback
        self._window_size = window_size
        self._cooldown = cooldown

        # 当前窗口缓冲
        self._buffer: list[dict] = []
        self._window_start: float = 0.0

        # 已聚合待推送的批次队列
        self._pending_queue: list[list[dict]] = []

        # 推送状态
        self._last_push_time: float = 0.0
        self._pushing = False

        # 定时器
        self._timer_task: Optional[asyncio.Task] = None
        self._running = False
        self._lock = asyncio.Lock()

        # 统计
        self.total_gifts_received = 0
        self.total_batches_pushed = 0

    @property
    def window_size(self) -> float:
        return self._window_size

    @window_size.setter
    def window_size(self, value: float):
        self._window_size = max(1.0, min(value, 60.0))

    @property
    def cooldown(self) -> float:
        return self._cooldown

    @cooldown.setter
    def cooldown(self, value: float):
        self._cooldown = max(5.0, min(value, 120.0))

    @property
    def has_pending(self) -> bool:
        """是否有待推送的批次（供弹幕队列查询）"""
        return bool(self._pending_queue) or bool(self._buffer)

    @property
    def is_cooling(self) -> bool:
        """是否在冷却期"""
        return time.time() - self._last_push_time < self._cooldown

    async def start(self):
        if self._running:
            return
        self._running = True
        self._window_start = time.time()
        self._timer_task = asyncio.create_task(self._tick_loop())

    async def stop(self):
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass
            self._timer_task = None
        # 刷新剩余缓冲
        async with self._lock:
            if self._buffer:
                aggregated = self._aggregate(self._buffer)
                self._pending_queue.append(aggregated)
                self._buffer = []
        await self._flush_pending()

    async def add(self, gift_info: dict):
        """添加一个礼物/SC 事件到当前窗口"""
        async with self._lock:
            self._buffer.append(gift_info)
            self.total_gifts_received += 1

    def _aggregate(self, gifts: list[dict]) -> list[dict]:
        """按 (user_name, gift_name) 聚合礼物，合并数量和总价值"""
        merged: dict[tuple[str, str], dict] = {}
        for g in gifts:
            key = (g.get("user_name", ""), g.get("gift_name", ""))
            if key not in merged:
                merged[key] = {
                    "user_name": g.get("user_name", "未知用户"),
                    "gift_name": g.get("gift_name", "未知礼物"),
                    "total_num": 0,
                    "total_coin": 0,
                    "is_sc": g.get("is_sc", False),
                    "sc_message": g.get("sc_message", ""),
                }
            m = merged[key]
            m["total_num"] += g.get("total_num", g.get("num", 1))
            m["total_coin"] += g.get("total_coin", 0)
            if g.get("is_sc"):
                m["is_sc"] = True
                msg = g.get("sc_message", "")
                if msg and msg not in m["sc_message"]:
                    m["sc_message"] = f"{m['sc_message']}\n{msg}" if m["sc_message"] else msg
        return list(merged.values())

    async def _tick_loop(self):
        while self._running:
            await asyncio.sleep(1.0)
            await self._check_and_flush()

    async def _check_and_flush(self):
        now = time.time()
        flush_window = False
        flush_pending = False

        async with self._lock:
            # 窗口到期：聚合当前缓冲，加入待推送队列
            if self._buffer and (now - self._window_start) >= self._window_size:
                aggregated = self._aggregate(self._buffer)
                self._pending_queue.append(aggregated)
                self._buffer = []
                self._window_start = now

            # 冷却结束 + 有待推送批次：触发推送
            if self._pending_queue and not self._pushing:
                if (now - self._last_push_time) >= self._cooldown:
                    flush_pending = True

        if flush_pending:
            await self._flush_pending()

    async def _flush_pending(self):
        if not self._pending_queue:
            return

        # 取出所有待推送批次，合并为一个大批次
        all_gifts: list[dict] = []
        while self._pending_queue:
            all_gifts.extend(self._pending_queue.pop(0))

        if not all_gifts:
            return

        # 再次聚合（跨批次合并同一用户同一礼物）
        final = self._aggregate(all_gifts)

        self._pushing = True
        try:
            if self.callback:
                await self.callback(final)
            self._last_push_time = time.time()
            self.total_batches_pushed += 1
        except Exception as e:
            print(f"[GiftAggregator] callback 异常: {e}")
        finally:
            self._pushing = False

    def get_stats(self) -> dict:
        return {
            "buffer_size": len(self._buffer),
            "pending_batches": len(self._pending_queue),
            "window_size": self._window_size,
            "cooldown": self._cooldown,
            "window_elapsed": time.time() - self._window_start,
            "is_cooling": self.is_cooling,
            "total_received": self.total_gifts_received,
            "total_pushed": self.total_batches_pushed,
        }
