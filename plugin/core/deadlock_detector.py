"""
死锁检测模块

提供锁持有追踪和潜在死锁检测功能。

使用方式::

    from plugin.core.deadlock_detector import DeadlockDetector, tracked_lock
    
    # 使用追踪锁
    with tracked_lock(my_lock, "my_lock_name"):
        # 临界区代码
        pass
    
    # 检查潜在死锁
    DeadlockDetector.check_all()
"""

from __future__ import annotations

import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from loguru import logger


@dataclass
class LockHolderInfo:
    """锁持有者信息"""
    lock_name: str
    thread_id: int
    thread_name: str
    acquire_time: float
    stack_trace: str
    waiting_for: Optional[str] = None  # 如果线程在等待另一个锁


class DeadlockDetector:
    """死锁检测器
    
    追踪所有锁的持有情况，检测潜在死锁。
    
    Features:
        - 记录每个锁的持有者线程和获取时间
        - 记录等待锁的线程
        - 检测长时间持有的锁
        - 检测可能的死锁循环
    """
    
    _lock = threading.Lock()
    _lock_holders: Dict[str, LockHolderInfo] = {}  # lock_name -> holder info
    _waiting_threads: Dict[int, str] = {}  # thread_id -> waiting_for_lock_name
    _lock_order: Dict[int, List[str]] = {}  # thread_id -> [lock_names in acquisition order]
    
    # 配置
    LONG_HOLD_THRESHOLD = 5.0  # 超过此时间视为长时间持有
    DEADLOCK_CHECK_INTERVAL = 10.0  # 死锁检查间隔
    
    @classmethod
    def on_lock_acquire(cls, lock_name: str, stack_depth: int = 5) -> None:
        """记录锁获取事件"""
        thread = threading.current_thread()
        thread_id = thread.ident or 0
        
        # 获取调用栈
        stack_lines = traceback.format_stack()
        stack_trace = ''.join(stack_lines[-stack_depth-2:-2])
        
        info = LockHolderInfo(
            lock_name=lock_name,
            thread_id=thread_id,
            thread_name=thread.name,
            acquire_time=time.time(),
            stack_trace=stack_trace,
        )
        
        with cls._lock:
            cls._lock_holders[lock_name] = info
            
            # 记录锁获取顺序
            if thread_id not in cls._lock_order:
                cls._lock_order[thread_id] = []
            cls._lock_order[thread_id].append(lock_name)
            
            # 清除等待状态
            cls._waiting_threads.pop(thread_id, None)
    
    @classmethod
    def on_lock_release(cls, lock_name: str) -> None:
        """记录锁释放事件"""
        thread_id = threading.current_thread().ident or 0
        
        with cls._lock:
            cls._lock_holders.pop(lock_name, None)
            
            # 从锁顺序中移除
            if thread_id in cls._lock_order:
                try:
                    cls._lock_order[thread_id].remove(lock_name)
                except ValueError:
                    pass
                if not cls._lock_order[thread_id]:
                    del cls._lock_order[thread_id]
    
    @classmethod
    def on_lock_wait(cls, lock_name: str) -> None:
        """记录线程开始等待锁"""
        thread_id = threading.current_thread().ident or 0
        with cls._lock:
            cls._waiting_threads[thread_id] = lock_name
    
    @classmethod
    def on_lock_wait_end(cls, lock_name: str) -> None:
        """记录线程结束等待锁"""
        thread_id = threading.current_thread().ident or 0
        with cls._lock:
            if cls._waiting_threads.get(thread_id) == lock_name:
                cls._waiting_threads.pop(thread_id, None)
    
    @classmethod
    def check_long_held_locks(cls, threshold: Optional[float] = None) -> List[LockHolderInfo]:
        """检查长时间持有的锁"""
        threshold = threshold or cls.LONG_HOLD_THRESHOLD
        now = time.time()
        long_held = []
        
        with cls._lock:
            for lock_name, info in cls._lock_holders.items():
                hold_time = now - info.acquire_time
                if hold_time > threshold:
                    long_held.append(info)
        
        return long_held
    
    @classmethod
    def check_potential_deadlock(cls, waiting_for: str, timeout: float = 5.0) -> Optional[str]:
        """检查是否可能发生死锁
        
        Args:
            waiting_for: 正在等待的锁名称
            timeout: 等待超时时间
        
        Returns:
            如果检测到潜在死锁，返回诊断信息；否则返回 None
        """
        with cls._lock:
            if waiting_for not in cls._lock_holders:
                return None
            
            holder_info = cls._lock_holders[waiting_for]
            hold_time = time.time() - holder_info.acquire_time
            
            if hold_time < timeout:
                return None
            
            # 构建诊断信息
            diag = (
                f"Potential deadlock detected!\n"
                f"  Waiting for: '{waiting_for}'\n"
                f"  Held by: Thread {holder_info.thread_id} ({holder_info.thread_name})\n"
                f"  Hold time: {hold_time:.1f}s\n"
                f"  Holder stack:\n{holder_info.stack_trace}"
            )
            
            # 检查是否形成死锁循环
            holder_thread_id = holder_info.thread_id
            if holder_thread_id in cls._waiting_threads:
                holder_waiting_for = cls._waiting_threads[holder_thread_id]
                diag += f"\n  Holder is waiting for: '{holder_waiting_for}'"
                
                # 检查循环
                current_thread_id = threading.current_thread().ident or 0
                if current_thread_id in cls._lock_order:
                    held_by_current = cls._lock_order[current_thread_id]
                    if holder_waiting_for in held_by_current:
                        diag += f"\n  DEADLOCK CYCLE DETECTED: Current thread holds '{holder_waiting_for}'"
            
            return diag
    
    @classmethod
    def check_all(cls) -> List[str]:
        """执行全面的死锁检查
        
        Returns:
            诊断信息列表
        """
        diagnostics = []
        
        # 1. 检查长时间持有的锁
        long_held = cls.check_long_held_locks()
        for info in long_held:
            hold_time = time.time() - info.acquire_time
            diagnostics.append(
                f"Long-held lock: '{info.lock_name}' by Thread {info.thread_id} "
                f"({info.thread_name}) for {hold_time:.1f}s"
            )
        
        # 2. 检查等待中的线程
        with cls._lock:
            for thread_id, waiting_for in cls._waiting_threads.items():
                if waiting_for in cls._lock_holders:
                    holder = cls._lock_holders[waiting_for]
                    diagnostics.append(
                        f"Thread {thread_id} waiting for '{waiting_for}' "
                        f"held by Thread {holder.thread_id}"
                    )
        
        return diagnostics
    
    @classmethod
    def get_lock_order_for_thread(cls, thread_id: Optional[int] = None) -> List[str]:
        """获取线程的锁获取顺序"""
        if thread_id is None:
            thread_id = threading.current_thread().ident or 0
        with cls._lock:
            return list(cls._lock_order.get(thread_id, []))
    
    @classmethod
    def clear(cls) -> None:
        """清空所有追踪数据（用于测试）"""
        with cls._lock:
            cls._lock_holders.clear()
            cls._waiting_threads.clear()
            cls._lock_order.clear()


@contextmanager
def tracked_lock(
    lock: threading.Lock,
    name: str,
    timeout: float = 10.0,
    warn_threshold: float = 5.0,
    track: bool = True,
):
    """带追踪的锁上下文管理器
    
    Args:
        lock: 要获取的锁
        name: 锁名称（用于日志和追踪）
        timeout: 获取超时时间
        warn_threshold: 等待警告阈值
        track: 是否启用追踪（生产环境可关闭以提高性能）
    
    Raises:
        TimeoutError: 如果在超时时间内无法获取锁
    """
    if track:
        DeadlockDetector.on_lock_wait(name)
    
    start_time = time.time()
    acquired = lock.acquire(timeout=timeout)
    wait_time = time.time() - start_time
    
    if track:
        DeadlockDetector.on_lock_wait_end(name)
    
    if not acquired:
        # 检查潜在死锁
        diag = DeadlockDetector.check_potential_deadlock(name, timeout)
        if diag:
            logger.error(diag)
        else:
            logger.error(
                f"Failed to acquire lock '{name}' within {timeout}s - possible deadlock"
            )
        raise TimeoutError(f"Failed to acquire lock '{name}' within {timeout}s")
    
    # 警告长时间等待
    if wait_time > warn_threshold:
        logger.warning(
            f"Lock '{name}' acquired after {wait_time:.2f}s wait (threshold: {warn_threshold}s)"
        )
    
    if track:
        DeadlockDetector.on_lock_acquire(name)
    
    try:
        yield
    finally:
        if track:
            DeadlockDetector.on_lock_release(name)
        lock.release()


@contextmanager
def tracked_rwlock_read(
    rwlock: Any,  # RWLock
    name: str,
    timeout: float = 10.0,
    warn_threshold: float = 5.0,
    track: bool = True,
):
    """带追踪的读写锁（读模式）上下文管理器"""
    lock_name = f"{name}:read"
    
    if track:
        DeadlockDetector.on_lock_wait(lock_name)
    
    start_time = time.time()
    acquired = rwlock.acquire_read(timeout=timeout)
    wait_time = time.time() - start_time
    
    if track:
        DeadlockDetector.on_lock_wait_end(lock_name)
    
    if not acquired:
        diag = DeadlockDetector.check_potential_deadlock(lock_name, timeout)
        if diag:
            logger.error(diag)
        raise TimeoutError(f"Failed to acquire read lock '{name}' within {timeout}s")
    
    if wait_time > warn_threshold:
        logger.warning(
            f"Read lock '{name}' acquired after {wait_time:.2f}s wait"
        )
    
    if track:
        DeadlockDetector.on_lock_acquire(lock_name)
    
    try:
        yield
    finally:
        if track:
            DeadlockDetector.on_lock_release(lock_name)
        rwlock.release_read()


@contextmanager
def tracked_rwlock_write(
    rwlock: Any,  # RWLock
    name: str,
    timeout: float = 10.0,
    warn_threshold: float = 5.0,
    track: bool = True,
):
    """带追踪的读写锁（写模式）上下文管理器"""
    lock_name = f"{name}:write"
    
    if track:
        DeadlockDetector.on_lock_wait(lock_name)
    
    start_time = time.time()
    acquired = rwlock.acquire_write(timeout=timeout)
    wait_time = time.time() - start_time
    
    if track:
        DeadlockDetector.on_lock_wait_end(lock_name)
    
    if not acquired:
        diag = DeadlockDetector.check_potential_deadlock(lock_name, timeout)
        if diag:
            logger.error(diag)
        raise TimeoutError(f"Failed to acquire write lock '{name}' within {timeout}s")
    
    if wait_time > warn_threshold:
        logger.warning(
            f"Write lock '{name}' acquired after {wait_time:.2f}s wait"
        )
    
    if track:
        DeadlockDetector.on_lock_acquire(lock_name)
    
    try:
        yield
    finally:
        if track:
            DeadlockDetector.on_lock_release(lock_name)
        rwlock.release_write()
