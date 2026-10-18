"""
Deadlock detection module

Tracks lock ownership and detects potential deadlocks.

Usage::

    from plugin.core.deadlock_detector import DeadlockDetector, tracked_lock
    
    # Use a tracked lock.
    with tracked_lock(my_lock, "my_lock_name"):
        # Critical-section code.
        pass
    
    # Check for potential deadlocks.
    DeadlockDetector.check_all()
"""

from __future__ import annotations

import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from plugin.logging_config import logger


@dataclass
class LockHolderInfo:
    """Lock-owner information."""
    lock_name: str
    thread_id: int
    thread_name: str
    acquire_time: float
    stack_trace: str
    waiting_for: Optional[str] = None  # If the thread is waiting for another lock.


class DeadlockDetector:
    """Deadlock detector.
    
    Track ownership of all locks and detect potential deadlocks.
    
    Features:
        - Record each lock's owner thread and acquisition time.
        - Record threads waiting for locks.
        - Detect long-held locks.
        - Detect potential deadlock cycles.
    """
    
    _lock = threading.Lock()
    _lock_holders: Dict[str, LockHolderInfo] = {}  # lock_name -> holder info
    _waiting_threads: Dict[int, str] = {}  # thread_id -> waiting_for_lock_name
    _lock_order: Dict[int, List[str]] = {}  # thread_id -> [lock_names in acquisition order]
    
    # Configuration.
    LONG_HOLD_THRESHOLD = 5.0  # Holding longer than this counts as prolonged ownership.
    DEADLOCK_CHECK_INTERVAL = 10.0  # Deadlock-check interval.
    
    @classmethod
    def on_lock_acquire(cls, lock_name: str, stack_depth: int = 5) -> None:
        """Record a lock acquisition event."""
        thread = threading.current_thread()
        thread_id = thread.ident or 0
        
        # Get the call stack.
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
            
            # Record lock acquisition order.
            if thread_id not in cls._lock_order:
                cls._lock_order[thread_id] = []
            cls._lock_order[thread_id].append(lock_name)
            
            # Clear waiting state.
            cls._waiting_threads.pop(thread_id, None)
    
    @classmethod
    def on_lock_release(cls, lock_name: str) -> None:
        """Record a lock release event."""
        thread_id = threading.current_thread().ident or 0
        
        with cls._lock:
            cls._lock_holders.pop(lock_name, None)
            
            # Remove from lock order.
            if thread_id in cls._lock_order:
                try:
                    cls._lock_order[thread_id].remove(lock_name)
                except ValueError:
                    pass
                if not cls._lock_order[thread_id]:
                    del cls._lock_order[thread_id]
    
    @classmethod
    def on_lock_wait(cls, lock_name: str) -> None:
        """Record a thread beginning to wait for a lock."""
        thread_id = threading.current_thread().ident or 0
        with cls._lock:
            cls._waiting_threads[thread_id] = lock_name
    
    @classmethod
    def on_lock_wait_end(cls, lock_name: str) -> None:
        """Record a thread finishing its lock wait."""
        thread_id = threading.current_thread().ident or 0
        with cls._lock:
            if cls._waiting_threads.get(thread_id) == lock_name:
                cls._waiting_threads.pop(thread_id, None)
    
    @classmethod
    def check_long_held_locks(cls, threshold: Optional[float] = None) -> List[LockHolderInfo]:
        """Check for long-held locks."""
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
        """Check for potential deadlocks.
        
        Args:
            waiting_for: Name of the lock being awaited.
            timeout: Wait timeout.
        
        Returns:
            Diagnostics if a potential deadlock is detected, otherwise None.
        """
        with cls._lock:
            if waiting_for not in cls._lock_holders:
                return None
            
            holder_info = cls._lock_holders[waiting_for]
            hold_time = time.time() - holder_info.acquire_time
            
            if hold_time < timeout:
                return None
            
            # Build diagnostics.
            diag = (
                f"Potential deadlock detected!\n"
                f"  Waiting for: '{waiting_for}'\n"
                f"  Held by: Thread {holder_info.thread_id} ({holder_info.thread_name})\n"
                f"  Hold time: {hold_time:.1f}s\n"
                f"  Holder stack:\n{holder_info.stack_trace}"
            )
            
            # Check for a deadlock cycle.
            holder_thread_id = holder_info.thread_id
            if holder_thread_id in cls._waiting_threads:
                holder_waiting_for = cls._waiting_threads[holder_thread_id]
                diag += f"\n  Holder is waiting for: '{holder_waiting_for}'"
                
                # Check for cycles.
                current_thread_id = threading.current_thread().ident or 0
                if current_thread_id in cls._lock_order:
                    held_by_current = cls._lock_order[current_thread_id]
                    if holder_waiting_for in held_by_current:
                        diag += f"\n  DEADLOCK CYCLE DETECTED: Current thread holds '{holder_waiting_for}'"
            
            return diag
    
    @classmethod
    def check_all(cls) -> List[str]:
        """Perform a comprehensive deadlock check.
        
        Returns:
            A list of diagnostics.
        """
        diagnostics = []
        
        # 1. Check long-held locks.
        long_held = cls.check_long_held_locks()
        for info in long_held:
            hold_time = time.time() - info.acquire_time
            diagnostics.append(
                f"Long-held lock: '{info.lock_name}' by Thread {info.thread_id} "
                f"({info.thread_name}) for {hold_time:.1f}s"
            )
        
        # 2. Check waiting threads.
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
        """Get a thread's lock acquisition order."""
        if thread_id is None:
            thread_id = threading.current_thread().ident or 0
        with cls._lock:
            return list(cls._lock_order.get(thread_id, []))
    
    @classmethod
    def clear(cls) -> None:
        """Clear all tracking data for tests."""
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
    """Context manager for a tracked lock.
    
    Args:
        lock: Lock to acquire.
        name: Lock name for logging and tracking.
        timeout: Acquisition timeout.
        warn_threshold: Wait-warning threshold.
        track: Whether to track; disable in production for better performance.
    
    Raises:
        TimeoutError: If the lock cannot be acquired before timeout.
    """
    if track:
        DeadlockDetector.on_lock_wait(name)
    
    start_time = time.time()
    acquired = lock.acquire(timeout=timeout)
    wait_time = time.time() - start_time
    
    if track:
        DeadlockDetector.on_lock_wait_end(name)
    
    if not acquired:
        # Check for potential deadlocks.
        diag = DeadlockDetector.check_potential_deadlock(name, timeout)
        if diag:
            logger.error(diag)
        else:
            logger.error(
                f"Failed to acquire lock '{name}' within {timeout}s - possible deadlock"
            )
        raise TimeoutError(f"Failed to acquire lock '{name}' within {timeout}s")
    
    # Warn about prolonged waits.
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
    """Context manager for a tracked read/write lock in read mode."""
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
    """Context manager for a tracked read/write lock in write mode."""
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
