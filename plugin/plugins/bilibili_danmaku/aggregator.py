"""
聚合缓冲器模块

功能：
- 缓冲Ⅱ类事件（不紧急但重要）
- 多种触发策略（时间/数量/优先级）
- 事件批次管理
- 与摘要生成器对接
"""

from __future__ import annotations

import time
import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Set
from enum import Enum
from collections import deque, defaultdict
import heapq


class TriggerStrategy(Enum):
    """触发策略"""
    TIME_BASED = "time_based"      # 时间触发
    COUNT_BASED = "count_based"    # 数量触发
    PRIORITY_BASED = "priority_based"  # 优先级触发
    HYBRID = "hybrid"              # 混合触发


@dataclass(order=True)
class AggregatedEvent:
    """聚合事件（支持优先级队列）"""
    priority: int = field(compare=True)  # 用于堆排序的字段
    event_id: str = field(compare=False)
    type: str = field(compare=False)
    uid: int = field(compare=False)
    name: str = field(compare=False)
    content: Optional[str] = field(default=None, compare=False)
    gift_name: Optional[str] = field(default=None, compare=False)
    gift_num: int = field(default=1, compare=False)
    battery: int = field(default=0, compare=False)
    price: int = field(default=0, compare=False)
    user_level: int = field(default=0, compare=False)
    medal_level: int = field(default=0, compare=False)
    medal_name: str = field(default="", compare=False)
    guard_level: int = field(default=0, compare=False)
    is_vip: bool = field(default=False, compare=False)
    quadrant: str = field(default="II", compare=False)
    timestamp: float = field(default_factory=time.time, compare=False)
    
    @property
    def rmb_value(self) -> float:
        """计算人民币价值"""
        if self.type == "superchat":
            return float(self.price)
        elif self.type in ["gift", "guard"]:
            return self.battery / 10.0
        return 0.0
    
    @property
    def is_high_value(self) -> bool:
        """判断是否为高价值事件"""
        if self.type == "superchat":
            return self.price >= 30
        elif self.type == "gift":
            return self.battery >= 500
        elif self.type == "guard":
            return True
        return False


class AggregationBuffer:
    """聚合缓冲器"""
    
    def __init__(
        self,
        trigger_callback: Callable[[List[AggregatedEvent]], Any],
        config: Optional[Dict[str, Any]] = None
    ):
        """
        初始化聚合缓冲器
        
        Args:
            trigger_callback: 触发回调函数，接收事件批次
            config: 配置参数
        """
        self.trigger_callback = trigger_callback
        self.config = config or {}
        
        # 缓冲队列（优先级队列）
        self.priority_queue: List[AggregatedEvent] = []
        
        # 事件ID集合（用于去重）
        self.event_ids: Set[str] = set()
        
        # 用户事件计数（用于用户级限制）
        self.user_event_counts: Dict[int, int] = defaultdict(int)
        
        # 触发策略配置
        self.trigger_config = {
            "strategy": TriggerStrategy.HYBRID,
            "time_based": {
                "max_wait_sec": 30,      # 最长等待时间
                "min_wait_sec": 10,      # 最短等待时间
            },
            "count_based": {
                "max_events": 20,        # 最大事件数
                "min_events": 5,         # 最小事件数
            },
            "priority_based": {
                "high_priority_threshold": 80,  # 高优先级阈值
                "immediate_push": True,         # 是否立即推送高优先级
            },
            "user_limits": {
                "max_events_per_user": 5,       # 每个用户最多事件数
            }
        }
        
        # 更新配置
        if config:
            self.trigger_config.update(config.get("aggregation", {}))
        
        # 状态变量
        self.last_trigger_time = time.time()
        self.total_events_processed = 0
        self.total_batches_triggered = 0
        
        # 定时器任务
        self._timer_task: Optional[asyncio.Task] = None
        self._running = False
        
        # 异步锁
        self._lock = asyncio.Lock()
        
        # 启动定时检查
        self._start_timer()
    
    def _start_timer(self) -> None:
        """启动定时检查任务"""
        async def timer_loop():
            self._running = True
            while self._running:
                await asyncio.sleep(1)  # 每秒检查一次
                await self._check_triggers()
        
        self._timer_task = asyncio.create_task(timer_loop())
    
    async def add_event(
        self,
        event: AggregatedEvent,
        profile_store: Optional[Any] = None
    ) -> bool:
        """
        添加事件到缓冲器
        
        Returns:
            bool: 是否成功添加
        """
        async with self._lock:
            # 检查去重
            if event.event_id in self.event_ids:
                return False
            
            # 检查用户限制
            user_count = self.user_event_counts.get(event.uid, 0)
            max_per_user = self.trigger_config["user_limits"]["max_events_per_user"]
            
            if user_count >= max_per_user:
                # 用户事件数超限，替换最低优先级事件
                await self._replace_lowest_priority_event(event)
                return True
            
            # 添加到优先级队列
            heapq.heappush(self.priority_queue, event)
            self.event_ids.add(event.event_id)
            self.user_event_counts[event.uid] = user_count + 1
            self.total_events_processed += 1
            
            # 检查是否立即触发（高优先级事件）
            if (self.trigger_config["priority_based"]["immediate_push"] and 
                event.priority >= self.trigger_config["priority_based"]["high_priority_threshold"]):
                await self._trigger_now(reason="high_priority")
                return True
            
            # 检查数量触发
            if self._should_trigger_by_count():
                await self._trigger_now(reason="count_reached")
                return True
            
            return True
    
    async def _replace_lowest_priority_event(self, new_event: AggregatedEvent) -> None:
        """替换最低优先级事件"""
        if not self.priority_queue:
            return
        
        # 找到最低优先级事件（堆顶是最小优先级）
        lowest_event = self.priority_queue[0]
        
        if new_event.priority > lowest_event.priority:
            # 移除最低优先级事件
            removed_event = heapq.heappop(self.priority_queue)
            self.event_ids.remove(removed_event.event_id)
            self.user_event_counts[removed_event.uid] -= 1
            
            # 添加新事件
            heapq.heappush(self.priority_queue, new_event)
            self.event_ids.add(new_event.event_id)
            self.user_event_counts[new_event.uid] += 1
    
    def _should_trigger_by_count(self) -> bool:
        """检查是否应该按数量触发"""
        count_config = self.trigger_config["count_based"]
        current_count = len(self.priority_queue)
        
        return current_count >= count_config["max_events"]
    
    def _should_trigger_by_time(self) -> bool:
        """检查是否应该按时间触发"""
        time_config = self.trigger_config["time_based"]
        time_since_last_trigger = time.time() - self.last_trigger_time
        
        # 检查是否超过最大等待时间
        if time_since_last_trigger >= time_config["max_wait_sec"]:
            return True
        
        # 检查是否超过最小等待时间且有足够事件
        count_config = self.trigger_config["count_based"]
        if (time_since_last_trigger >= time_config["min_wait_sec"] and 
            len(self.priority_queue) >= count_config["min_events"]):
            return True
        
        return False
    
    def _should_trigger_by_priority(self) -> bool:
        """检查是否应该按优先级触发"""
        if not self.priority_queue:
            return False
        
        # 检查是否有高优先级事件
        priority_config = self.trigger_config["priority_based"]
        
        # 查看队列中最高优先级事件（需要遍历，因为堆是最小堆）
        max_priority = max(event.priority for event in self.priority_queue)
        
        return max_priority >= priority_config["high_priority_threshold"]
    
    async def check_trigger(self, force: bool = False) -> bool:
        """
        检查并触发聚合（外部调用接口）
        
        Args:
            force: 是否强制触发（忽略条件检查）
            
        Returns:
            bool: 是否触发了推送
        """
        async with self._lock:
            if not self.priority_queue:
                return False
            
            if force:
                await self._trigger_now(reason="force")
                return True
            
            strategy = self.trigger_config["strategy"]
            should_trigger = False
            
            if strategy == TriggerStrategy.TIME_BASED:
                should_trigger = self._should_trigger_by_time()
            elif strategy == TriggerStrategy.COUNT_BASED:
                should_trigger = self._should_trigger_by_count()
            elif strategy == TriggerStrategy.PRIORITY_BASED:
                should_trigger = self._should_trigger_by_priority()
            elif strategy == TriggerStrategy.HYBRID:
                should_trigger = (self._should_trigger_by_time() or 
                                  self._should_trigger_by_count() or 
                                  self._should_trigger_by_priority())
            
            if should_trigger:
                await self._trigger_now(reason=f"check_trigger_{strategy.value}")
                return True
            
            return False

    async def _check_triggers(self) -> None:
        """检查所有触发条件（内部定时器使用）"""
        async with self._lock:
            if not self.priority_queue:
                return
            
            strategy = self.trigger_config["strategy"]
            
            if strategy == TriggerStrategy.TIME_BASED:
                if self._should_trigger_by_time():
                    await self._trigger_now(reason="time_based")
            
            elif strategy == TriggerStrategy.COUNT_BASED:
                if self._should_trigger_by_count():
                    await self._trigger_now(reason="count_based")
            
            elif strategy == TriggerStrategy.PRIORITY_BASED:
                if self._should_trigger_by_priority():
                    await self._trigger_now(reason="priority_based")
            
            elif strategy == TriggerStrategy.HYBRID:
                # 混合策略：任一条件满足即触发
                if (self._should_trigger_by_time() or 
                    self._should_trigger_by_count() or 
                    self._should_trigger_by_priority()):
                    await self._trigger_now(reason="hybrid")
    
    async def _trigger_now(self, reason: str = "") -> None:
        """立即触发摘要生成"""
        if not self.priority_queue:
            return
        
        # 获取事件批次（按优先级从高到低）
        batch_size = min(
            len(self.priority_queue),
            self.trigger_config["count_based"]["max_events"]
        )
        
        batch: List[AggregatedEvent] = []
        for _ in range(batch_size):
            # 堆是最小堆，需要反转优先级获取最高优先级
            # 这里使用堆排序获取最高优先级事件
            event = heapq.heappop(self.priority_queue)
            batch.append(event)
            self.event_ids.remove(event.event_id)
            self.user_event_counts[event.uid] -= 1
        
        # 按优先级降序排序
        batch.sort(key=lambda x: x.priority, reverse=True)
        
        # 更新状态
        self.last_trigger_time = time.time()
        self.total_batches_triggered += 1
        
        # 调用回调函数
        if self.trigger_callback and batch:
            try:
                await self.trigger_callback(batch)
            except Exception as e:
                print(f"触发回调失败: {e}")
    
    async def force_trigger(self) -> List[AggregatedEvent]:
        """强制触发并返回当前批次"""
        async with self._lock:
            batch_size = len(self.priority_queue)
            batch: List[AggregatedEvent] = []
            
            for _ in range(batch_size):
                event = heapq.heappop(self.priority_queue)
                batch.append(event)
                self.event_ids.remove(event.event_id)
                self.user_event_counts[event.uid] -= 1
            
            batch.sort(key=lambda x: x.priority, reverse=True)
            self.last_trigger_time = time.time()
            
            return batch
    
    async def clear_buffer(self) -> None:
        """清空缓冲器"""
        async with self._lock:
            self.priority_queue.clear()
            self.event_ids.clear()
            self.user_event_counts.clear()
    
    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        async with self._lock:
            return {
                "queue_size": len(self.priority_queue),
                "unique_users": len(self.user_event_counts),
                "total_events_processed": self.total_events_processed,
                "total_batches_triggered": self.total_batches_triggered,
                "time_since_last_trigger": time.time() - self.last_trigger_time,
                "avg_batch_size": (
                    self.total_events_processed / self.total_batches_triggered
                    if self.total_batches_triggered > 0 else 0
                )
            }
    
    async def shutdown(self) -> None:
        """关闭缓冲器"""
        self._running = False
        
        if self._timer_task:
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass
        
        # 触发剩余事件
        remaining_batch = await self.force_trigger()
        if remaining_batch and self.trigger_callback:
            try:
                await self.trigger_callback(remaining_batch)
            except Exception as e:
                print(f"关闭时触发回调失败: {e}")


def create_event_id(event_type: str, uid: int, timestamp: float, content: Optional[str] = None) -> str:
    """创建唯一事件ID"""
    import hashlib
    
    content_hash = ""
    if content:
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()[:8]
    
    return f"{event_type}_{uid}_{int(timestamp)}_{content_hash}"


# 全局聚合缓冲器实例
_global_aggregation_buffer: Optional[AggregationBuffer] = None

def get_global_aggregation_buffer(
    trigger_callback: Optional[Callable[[List[AggregatedEvent]], Any]] = None,
    config: Optional[Dict[str, Any]] = None
) -> AggregationBuffer:
    """获取全局聚合缓冲器实例"""
    global _global_aggregation_buffer
    
    if _global_aggregation_buffer is None:
        if trigger_callback is None:
            # 默认回调：打印日志
            async def default_callback(batch: List[AggregatedEvent]):
                print(f"[Aggregator] 触发批次: {len(batch)} 个事件")
            
            trigger_callback = default_callback
        
        _global_aggregation_buffer = AggregationBuffer(trigger_callback, config)
    
    return _global_aggregation_buffer