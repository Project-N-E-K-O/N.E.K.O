"""
共享线程池执行器

提供全局共享的线程池,避免重复创建。
"""
import os
from concurrent.futures import ThreadPoolExecutor

_api_executor = ThreadPoolExecutor(
    max_workers=max(16, (os.cpu_count() or 1) * 4),
    thread_name_prefix="api-worker"
)

__all__ = ['_api_executor']
