"""
熔断器模块

功能：
- 故障检测和熔断
- 自动恢复（半开状态）
- 统计信息记录
- 多实例支持
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum
import asyncio


class CircuitState(Enum):
    """熔断器状态"""
    CLOSED = "closed"      # 正常状态，请求通过
    OPEN = "open"          # 熔断状态，请求被拒绝
    HALF_OPEN = "half_open"  # 半开状态，尝试恢复


@dataclass
class CircuitBreakerStats:
    """熔断器统计信息"""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_trip_count: int = 0
    last_trip_time: float = 0
    last_reset_time: float = 0


class CircuitBreaker:
    """熔断器"""
    
    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 3,
        reset_timeout: float = 300.0,  # 5分钟
        half_open_max_attempts: int = 1
    ):
        """
        初始化熔断器
        
        Args:
            name: 熔断器名称
            failure_threshold: 失败阈值（连续失败次数）
            reset_timeout: 重置超时时间（秒）
            half_open_max_attempts: 半开状态最大尝试次数
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.half_open_max_attempts = half_open_max_attempts
        
        # 状态
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.half_open_attempts = 0
        self.last_failure_time = 0
        self.last_state_change_time = time.time()
        
        # 统计
        self.stats = CircuitBreakerStats()
        
        # 异步锁
        self._lock = asyncio.Lock()
    
    async def execute(self, func, *args, **kwargs) -> Any:
        """
        执行受保护的操作
        
        Args:
            func: 要执行的函数
            *args, **kwargs: 函数参数
        
        Returns:
            函数执行结果
        
        Raises:
            CircuitBreakerError: 熔断器打开时抛出
            Exception: 函数执行异常
        """
        async with self._lock:
            # 检查是否允许执行
            if not self._allow_request():
                self.stats.total_requests += 1
                raise CircuitBreakerError(
                    f"Circuit breaker '{self.name}' is OPEN",
                    self.state
                )
            
            # 执行操作
            try:
                result = await func(*args, **kwargs)
                self._on_success()
                return result
            
            except Exception as e:
                self._on_failure()
                raise e
    
    def _allow_request(self) -> bool:
        """检查是否允许请求"""
        current_time = time.time()
        
        if self.state == CircuitState.CLOSED:
            return True
        
        elif self.state == CircuitState.OPEN:
            # 检查是否应该进入半开状态
            if current_time - self.last_state_change_time >= self.reset_timeout:
                self._transition_to_half_open()
                return True
            return False
        
        elif self.state == CircuitState.HALF_OPEN:
            # 半开状态限制尝试次数
            if self.half_open_attempts < self.half_open_max_attempts:
                return True
            return False
        
        return False
    
    def _on_success(self) -> None:
        """请求成功处理"""
        self.stats.total_requests += 1
        self.stats.successful_requests += 1
        
        if self.state == CircuitState.HALF_OPEN:
            # 半开状态成功，恢复到闭合状态
            self._transition_to_closed()
        else:
            # 闭合状态成功，重置失败计数
            self.failure_count = 0
    
    def _on_failure(self) -> None:
        """请求失败处理"""
        self.stats.total_requests += 1
        self.stats.failed_requests += 1
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.HALF_OPEN:
            # 半开状态失败，重新打开
            self._transition_to_open()
        
        elif self.state == CircuitState.CLOSED:
            # 闭合状态失败，检查是否达到阈值
            if self.failure_count >= self.failure_threshold:
                self._transition_to_open()
    
    def _transition_to_closed(self) -> None:
        """过渡到闭合状态"""
        old_state = self.state
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.half_open_attempts = 0
        self.last_state_change_time = time.time()
        self.stats.last_reset_time = time.time()
        
        self._log_state_change(old_state, self.state)
    
    def _transition_to_open(self) -> None:
        """过渡到打开状态"""
        old_state = self.state
        self.state = CircuitState.OPEN
        self.last_state_change_time = time.time()
        self.stats.last_trip_time = time.time()
        self.stats.total_trip_count += 1
        
        self._log_state_change(old_state, self.state)
    
    def _transition_to_half_open(self) -> None:
        """过渡到半开状态"""
        old_state = self.state
        self.state = CircuitState.HALF_OPEN
        self.half_open_attempts = 0
        self.last_state_change_time = time.time()
        
        self._log_state_change(old_state, self.state)
    
    def _log_state_change(self, old_state: CircuitState, new_state: CircuitState) -> None:
        """记录状态变化"""
        print(f"[CircuitBreaker {self.name}] {old_state.value} -> {new_state.value}")
    
    def force_open(self) -> None:
        """强制打开熔断器"""
        self._transition_to_open()
    
    def force_close(self) -> None:
        """强制关闭熔断器"""
        self._transition_to_closed()
    
    def force_reset(self) -> None:
        """强制重置熔断器"""
        self._transition_to_closed()
        self.stats = CircuitBreakerStats()
    
    def get_status(self) -> Dict[str, Any]:
        """获取熔断器状态"""
        current_time = time.time()
        
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "half_open_attempts": self.half_open_attempts,
            "half_open_max_attempts": self.half_open_max_attempts,
            "time_since_last_failure": current_time - self.last_failure_time if self.last_failure_time else None,
            "time_since_last_state_change": current_time - self.last_state_change_time,
            "reset_timeout": self.reset_timeout,
            "stats": {
                "total_requests": self.stats.total_requests,
                "successful_requests": self.stats.successful_requests,
                "failed_requests": self.stats.failed_requests,
                "success_rate": (
                    self.stats.successful_requests / self.stats.total_requests * 100
                    if self.stats.total_requests > 0 else 0
                ),
                "total_trip_count": self.stats.total_trip_count,
                "last_trip_time": self.stats.last_trip_time,
                "last_reset_time": self.stats.last_reset_time,
            }
        }
    
    def is_closed(self) -> bool:
        """检查是否闭合"""
        return self.state == CircuitState.CLOSED
    
    def is_open(self) -> bool:
        """检查是否打开"""
        return self.state == CircuitState.OPEN
    
    def is_half_open(self) -> bool:
        """检查是否半开"""
        return self.state == CircuitState.HALF_OPEN


class CircuitBreakerError(Exception):
    """熔断器错误"""
    
    def __init__(self, message: str, state: CircuitState):
        super().__init__(message)
        self.state = state
        self.message = message
    
    def __str__(self) -> str:
        return f"{self.message} (state: {self.state.value})"


# 全局熔断器管理器
class CircuitBreakerManager:
    """熔断器管理器"""
    
    def __init__(self):
        self.breakers: Dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()
    
    async def get_breaker(
        self,
        name: str,
        failure_threshold: int = 3,
        reset_timeout: float = 300.0,
        half_open_max_attempts: int = 1
    ) -> CircuitBreaker:
        """获取或创建熔断器"""
        async with self._lock:
            if name not in self.breakers:
                self.breakers[name] = CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold,
                    reset_timeout=reset_timeout,
                    half_open_max_attempts=half_open_max_attempts
                )
            
            return self.breakers[name]
    
    async def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有熔断器状态"""
        async with self._lock:
            return {
                name: breaker.get_status()
                for name, breaker in self.breakers.items()
            }
    
    async def reset_all(self) -> None:
        """重置所有熔断器"""
        async with self._lock:
            for breaker in self.breakers.values():
                breaker.force_reset()
    
    async def force_close_all(self) -> None:
        """强制关闭所有熔断器"""
        async with self._lock:
            for breaker in self.breakers.values():
                breaker.force_close()


# 全局熔断器管理器实例
_global_breaker_manager: Optional[CircuitBreakerManager] = None

def get_global_breaker_manager() -> CircuitBreakerManager:
    """获取全局熔断器管理器"""
    global _global_breaker_manager
    
    if _global_breaker_manager is None:
        _global_breaker_manager = CircuitBreakerManager()
    
    return _global_breaker_manager