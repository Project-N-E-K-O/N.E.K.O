"""
Worker 执行器模块

提供线程池管理和任务队列，用于执行插件的同步代码。
"""

import asyncio
import contextvars
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future, wait as futures_wait
from typing import Any, Callable, Dict, Optional
from dataclasses import dataclass

from plugin.core.context import _IN_WORKER


@dataclass
class WorkerTask:
    """Worker 任务"""
    task_id: str
    handler: Callable
    args: tuple
    kwargs: dict
    timeout: float
    result_future: Future
    executor_future: Optional[Future] = None


class WorkerExecutor:
    """Worker 执行器：管理线程池和任务队列"""
    
    def __init__(self, max_workers: int = 4, queue_size: int = 100):
        """
        初始化 Worker 执行器
        
        Args:
            max_workers: 线程池最大线程数
            queue_size: 任务队列最大大小（当前未使用，预留）
        """
        self.max_workers = max_workers
        self.queue_size = queue_size
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="PluginWorker"
        )
        self._active_tasks: Dict[str, WorkerTask] = {}
        self._lock = threading.Lock()
        self._shutdown = False
    
    def submit(
        self,
        task_id: str,
        handler: Callable,
        args: tuple,
        kwargs: dict,
        timeout: float = 30.0
    ) -> Future:
        """
        提交任务到 worker 线程池
        
        Args:
            task_id: 任务 ID
            handler: 处理函数
            args: 位置参数
            kwargs: 关键字参数
            timeout: 超时时间（秒）
        
        Returns:
            Future 对象，用于获取结果
        
        Raises:
            RuntimeError: 如果执行器已关闭
        """
        if self._shutdown:
            raise RuntimeError("WorkerExecutor is shutdown")
        
        # 创建 Future 用于返回结果
        result_future = Future()
        
        # 创建任务
        task = WorkerTask(
            task_id=task_id,
            handler=handler,
            args=args,
            kwargs=kwargs,
            timeout=timeout,
            result_future=result_future
        )
        
        # 记录活跃任务
        with self._lock:
            if task_id in self._active_tasks:
                raise ValueError(f"Duplicate active task_id: {task_id}")
            self._active_tasks[task_id] = task
        
        # Capture caller's contextvars (including _CURRENT_RUN_ID) so that
        # the worker thread inherits them transparently.
        ctx_snapshot = contextvars.copy_context()

        # 提交到线程池
        def _worker():
            try:
                # Run the ENTIRE handler + asyncio.run chain inside the
                # captured context.  This is critical because when
                # hook_executor wraps a sync handler into an async
                # ``wrapped`` function, ctx_snapshot.run(handler) only sets
                # contextvars during the synchronous call that *creates* the
                # coroutine.  The coroutine itself runs later in
                # asyncio.run() which would otherwise have a fresh context.
                def _run_in_ctx():
                    # Mark this thread as a @worker thread so that
                    # _enforce_sync_call_policy skips false-positive
                    # deadlock warnings (worker threads don't block the
                    # command loop).
                    _IN_WORKER.set(True)
                    result = handler(*args, **kwargs)
                    if asyncio.iscoroutine(result):
                        result = asyncio.run(result)
                    return result

                result = ctx_snapshot.run(_run_in_ctx)
                result_future.set_result(result)
            except Exception as e:
                result_future.set_exception(e)
            finally:
                # 清理活跃任务
                with self._lock:
                    self._active_tasks.pop(task_id, None)

        try:
            exec_future = self._executor.submit(_worker)
            task.executor_future = exec_future
        except Exception as e:
            with self._lock:
                self._active_tasks.pop(task_id, None)
            try:
                if not result_future.done():
                    result_future.set_exception(e)
            except Exception:
                pass
            raise
        return result_future
    
    def wait_for_result(self, future: Future, timeout: float) -> Any:
        """
        等待任务结果（带超时）
        
        Args:
            future: Future 对象
            timeout: 超时时间（秒）
        
        Returns:
            任务结果
        
        Raises:
            TimeoutError: 如果超时
            Exception: 如果任务执行失败
        """
        try:
            return future.result(timeout=timeout)
        except TimeoutError as err:
            raise TimeoutError(f"Worker task timed out after {timeout}s") from err
        except Exception:
            raise
    
    def get_active_tasks_count(self) -> int:
        """获取活跃任务数量"""
        with self._lock:
            return len(self._active_tasks)
    
    def shutdown(self, wait: bool = True, timeout: float = 5.0):
        """
        关闭 worker 执行器
        
        Args:
            wait: 是否等待任务完成
            timeout: 等待超时时间（秒）
        """
        self._shutdown = True
        if not wait:
            self._executor.shutdown(wait=False, cancel_futures=True)
            with self._lock:
                for task in self._active_tasks.values():
                    if task.executor_future is not None and task.executor_future.cancelled():
                        if not task.result_future.done():
                            task.result_future.cancel()
            return

        # For wait=True, implement timeout by waiting on tracked task futures.
        self._executor.shutdown(wait=False, cancel_futures=False)
        with self._lock:
            active_futures = [t.result_future for t in self._active_tasks.values()]
        if not active_futures:
            return
        try:
            futures_wait(active_futures, timeout=timeout)
        except Exception:
            pass
