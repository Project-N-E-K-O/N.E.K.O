"""
调用链追踪模块

提供插件间调用的链路追踪和循环检测功能，防止死锁和无限递归。

使用方式::

    from plugin.sdk.call_chain import CallChain, CircularCallError
    
    # 在插件调用时自动追踪
    async def call_plugin(target_plugin: str, entry: str, args: dict):
        call_id = f"{target_plugin}.{entry}"
        with CallChain.track(call_id):
            return await _do_call(target_plugin, entry, args)
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


class CircularCallError(RuntimeError):
    """循环调用错误
    
    当检测到插件间调用形成循环时抛出。
    
    Attributes:
        chain: 调用链列表
        circular_call: 导致循环的调用
    """
    
    def __init__(self, message: str, chain: List[str], circular_call: str):
        super().__init__(message)
        self.chain = chain
        self.circular_call = circular_call


class CallChainTooDeepError(RuntimeError):
    """调用链过深错误
    
    当调用链深度超过限制时抛出。
    
    Attributes:
        chain: 调用链列表
        max_depth: 最大深度限制
    """
    
    def __init__(self, message: str, chain: List[str], max_depth: int):
        super().__init__(message)
        self.chain = chain
        self.max_depth = max_depth


@dataclass
class CallInfo:
    """调用信息"""
    call_id: str
    start_time: float
    caller_plugin: Optional[str] = None
    caller_entry: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class CallChain:
    """调用链追踪器
    
    使用线程本地存储追踪每个线程的调用链，支持：
    - 循环调用检测
    - 调用深度限制
    - 调用链日志
    
    线程安全：每个线程有独立的调用链。
    
    Example:
        >>> with CallChain.track("plugin_a.entry_1"):
        ...     # 调用 plugin_a.entry_1
        ...     with CallChain.track("plugin_b.entry_2"):
        ...         # 调用 plugin_b.entry_2
        ...         pass
    """
    
    _local = threading.local()
    
    # 默认配置
    DEFAULT_MAX_DEPTH = 20
    DEFAULT_WARN_DEPTH = 10
    
    @classmethod
    def _get_chain(cls) -> List[CallInfo]:
        """获取当前线程的调用链"""
        if not hasattr(cls._local, "chain"):
            cls._local.chain = []
        return cls._local.chain
    
    @classmethod
    def _get_call_ids(cls) -> Set[str]:
        """获取当前线程调用链中的所有 call_id"""
        if not hasattr(cls._local, "call_ids"):
            cls._local.call_ids = set()
        return cls._local.call_ids
    
    @classmethod
    def get_current_chain(cls) -> List[str]:
        """获取当前调用链的 call_id 列表（用于日志）"""
        return [info.call_id for info in cls._get_chain()]
    
    @classmethod
    def get_depth(cls) -> int:
        """获取当前调用深度"""
        return len(cls._get_chain())
    
    @classmethod
    def get_current_call(cls) -> Optional[CallInfo]:
        """获取当前调用信息"""
        chain = cls._get_chain()
        return chain[-1] if chain else None
    
    @classmethod
    def get_root_call(cls) -> Optional[CallInfo]:
        """获取根调用信息"""
        chain = cls._get_chain()
        return chain[0] if chain else None
    
    @classmethod
    def is_in_call(cls, call_id: str) -> bool:
        """检查指定 call_id 是否在当前调用链中"""
        return call_id in cls._get_call_ids()
    
    @classmethod
    @contextmanager
    def track(
        cls,
        call_id: str,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        warn_depth: int = DEFAULT_WARN_DEPTH,
        allow_reentry: bool = False,
        caller_plugin: Optional[str] = None,
        caller_entry: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        logger: Any = None,
    ):
        """追踪一次调用
        
        Args:
            call_id: 调用标识符（通常是 "plugin_id.entry_id"）
            max_depth: 最大调用深度（超过则抛出 CallChainTooDeepError）
            warn_depth: 警告深度（超过则记录警告日志）
            allow_reentry: 是否允许重入（同一 call_id 再次出现）
            caller_plugin: 调用方插件 ID
            caller_entry: 调用方入口 ID
            metadata: 额外元数据
            logger: 日志记录器
        
        Yields:
            CallInfo: 当前调用信息
        
        Raises:
            CircularCallError: 如果检测到循环调用且 allow_reentry=False
            CallChainTooDeepError: 如果调用深度超过 max_depth
        """
        chain = cls._get_chain()
        call_ids = cls._get_call_ids()
        
        # 1. 检查循环调用
        if not allow_reentry and call_id in call_ids:
            chain_str = " -> ".join(cls.get_current_chain())
            raise CircularCallError(
                f"Circular call detected: {chain_str} -> {call_id}",
                chain=cls.get_current_chain(),
                circular_call=call_id,
            )
        
        # 2. 检查调用深度
        current_depth = len(chain)
        if current_depth >= max_depth:
            chain_str = " -> ".join(cls.get_current_chain())
            raise CallChainTooDeepError(
                f"Call chain too deep ({current_depth} >= {max_depth}): {chain_str}",
                chain=cls.get_current_chain(),
                max_depth=max_depth,
            )
        
        # 3. 深度警告
        if current_depth >= warn_depth and logger:
            try:
                chain_str = " -> ".join(cls.get_current_chain())
                logger.warning(
                    f"[CallChain] Deep call chain ({current_depth}): {chain_str} -> {call_id}"
                )
            except Exception:
                pass
        
        # 4. 创建调用信息并入栈
        call_info = CallInfo(
            call_id=call_id,
            start_time=time.time(),
            caller_plugin=caller_plugin,
            caller_entry=caller_entry,
            metadata=metadata or {},
        )
        
        chain.append(call_info)
        call_ids.add(call_id)
        
        try:
            yield call_info
        finally:
            # 5. 出栈
            chain.pop()
            # 只有当调用链中没有其他相同 call_id 时才从 set 中移除
            if not any(info.call_id == call_id for info in chain):
                call_ids.discard(call_id)
    
    @classmethod
    def clear(cls):
        """清空当前线程的调用链（用于测试或错误恢复）"""
        cls._local.chain = []
        cls._local.call_ids = set()
    
    @classmethod
    def format_chain(cls, include_time: bool = False) -> str:
        """格式化当前调用链为字符串"""
        chain = cls._get_chain()
        if not chain:
            return "(empty)"
        
        if include_time:
            now = time.time()
            parts = []
            for info in chain:
                elapsed = now - info.start_time
                parts.append(f"{info.call_id}({elapsed:.2f}s)")
            return " -> ".join(parts)
        else:
            return " -> ".join(info.call_id for info in chain)


class AsyncCallChain:
    """异步调用链追踪器
    
    使用 contextvars 追踪异步调用链，支持跨 await 的调用追踪。
    
    Note:
        在 asyncio 环境中使用此类，在同步环境中使用 CallChain。
    """
    
    try:
        from contextvars import ContextVar
        _chain_var: "ContextVar[List[CallInfo]]" = ContextVar("async_call_chain", default=[])
        _call_ids_var: "ContextVar[Set[str]]" = ContextVar("async_call_ids", default=set())
        _available = True
    except ImportError:
        _available = False
    
    @classmethod
    def is_available(cls) -> bool:
        """检查是否可用（需要 Python 3.7+ 的 contextvars）"""
        return cls._available
    
    @classmethod
    def _get_chain(cls) -> List[CallInfo]:
        """获取当前上下文的调用链"""
        if not cls._available:
            return []
        return list(cls._chain_var.get())
    
    @classmethod
    def _get_call_ids(cls) -> Set[str]:
        """获取当前上下文调用链中的所有 call_id"""
        if not cls._available:
            return set()
        return set(cls._call_ids_var.get())
    
    @classmethod
    def get_current_chain(cls) -> List[str]:
        """获取当前调用链的 call_id 列表"""
        return [info.call_id for info in cls._get_chain()]
    
    @classmethod
    def get_depth(cls) -> int:
        """获取当前调用深度"""
        return len(cls._get_chain())
    
    @classmethod
    @contextmanager
    def track(
        cls,
        call_id: str,
        *,
        max_depth: int = CallChain.DEFAULT_MAX_DEPTH,
        warn_depth: int = CallChain.DEFAULT_WARN_DEPTH,
        allow_reentry: bool = False,
        caller_plugin: Optional[str] = None,
        caller_entry: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        logger: Any = None,
    ):
        """追踪一次异步调用（与 CallChain.track 接口相同）"""
        if not cls._available:
            # 降级到同步版本
            with CallChain.track(
                call_id,
                max_depth=max_depth,
                warn_depth=warn_depth,
                allow_reentry=allow_reentry,
                caller_plugin=caller_plugin,
                caller_entry=caller_entry,
                metadata=metadata,
                logger=logger,
            ) as info:
                yield info
            return
        
        chain = cls._get_chain()
        call_ids = cls._get_call_ids()
        
        # 1. 检查循环调用
        if not allow_reentry and call_id in call_ids:
            chain_str = " -> ".join(cls.get_current_chain())
            raise CircularCallError(
                f"Circular call detected: {chain_str} -> {call_id}",
                chain=cls.get_current_chain(),
                circular_call=call_id,
            )
        
        # 2. 检查调用深度
        current_depth = len(chain)
        if current_depth >= max_depth:
            chain_str = " -> ".join(cls.get_current_chain())
            raise CallChainTooDeepError(
                f"Call chain too deep ({current_depth} >= {max_depth}): {chain_str}",
                chain=cls.get_current_chain(),
                max_depth=max_depth,
            )
        
        # 3. 深度警告
        if current_depth >= warn_depth and logger:
            try:
                chain_str = " -> ".join(cls.get_current_chain())
                logger.warning(
                    f"[AsyncCallChain] Deep call chain ({current_depth}): {chain_str} -> {call_id}"
                )
            except Exception:
                pass
        
        # 4. 创建新的调用链（不可变更新）
        call_info = CallInfo(
            call_id=call_id,
            start_time=time.time(),
            caller_plugin=caller_plugin,
            caller_entry=caller_entry,
            metadata=metadata or {},
        )
        
        new_chain = chain + [call_info]
        new_call_ids = call_ids | {call_id}
        
        # 设置新的上下文变量
        token_chain = cls._chain_var.set(new_chain)
        token_ids = cls._call_ids_var.set(new_call_ids)
        
        try:
            yield call_info
        finally:
            # 恢复上下文变量
            cls._chain_var.reset(token_chain)
            cls._call_ids_var.reset(token_ids)
    
    @classmethod
    def format_chain(cls, include_time: bool = False) -> str:
        """格式化当前调用链为字符串"""
        chain = cls._get_chain()
        if not chain:
            return "(empty)"
        
        if include_time:
            now = time.time()
            parts = []
            for info in chain:
                elapsed = now - info.start_time
                parts.append(f"{info.call_id}({elapsed:.2f}s)")
            return " -> ".join(parts)
        else:
            return " -> ".join(info.call_id for info in chain)


# 便捷函数：自动选择同步或异步版本
def get_call_chain() -> List[str]:
    """获取当前调用链（自动选择同步或异步版本）"""
    # 优先尝试异步版本
    if AsyncCallChain.is_available():
        async_chain = AsyncCallChain.get_current_chain()
        if async_chain:
            return async_chain
    # 降级到同步版本
    return CallChain.get_current_chain()


def get_call_depth() -> int:
    """获取当前调用深度（自动选择同步或异步版本）"""
    if AsyncCallChain.is_available():
        async_depth = AsyncCallChain.get_depth()
        if async_depth > 0:
            return async_depth
    return CallChain.get_depth()


def is_in_call_chain(call_id: str) -> bool:
    """检查指定 call_id 是否在当前调用链中"""
    if AsyncCallChain.is_available():
        if call_id in AsyncCallChain._get_call_ids():
            return True
    return CallChain.is_in_call(call_id)
