"""
Hook 执行器 Mixin

提供统一的 Hook 收集和执行逻辑，供 NekoPluginBase 和 PluginRouter 共用。
避免代码重复，统一 Hook 执行行为。
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from .hooks import HookMeta, HookHandler, HOOK_META_ATTR

if TYPE_CHECKING:
    from .events import EventMeta


class HookExecutorMixin(ABC):
    """Hook 执行器 Mixin
    
    提供 Hook 收集、查询和执行的统一实现。
    
    子类需要实现:
        - _get_hook_logger(): 返回日志记录器
        - _get_hook_owner_name(): 返回所属对象名称（用于日志）
        - _get_child_hook_sources(): 返回子级 Hook 来源列表（如 Router 列表）
    
    Attributes:
        _hooks: Hook 字典，key 为目标 entry ID，value 为 HookHandler 列表
    """
    
    _hooks: Dict[str, List[HookHandler]]
    
    def __init_hook_executor__(self) -> None:
        """初始化 Hook 执行器状态（子类 __init__ 中调用）"""
        self._hooks = {}
    
    @abstractmethod
    def _get_hook_logger(self) -> Any:
        """获取日志记录器"""
        ...
    
    @abstractmethod
    def _get_hook_owner_name(self) -> str:
        """获取所属对象名称（用于日志）"""
        ...
    
    def _get_child_hook_sources(self) -> "List[Any]":
        """获取子级 Hook 来源列表
        
        默认返回空列表。NekoPluginBase 重写此方法返回 _routers 列表。
        子类返回的对象需要实现 get_hooks_for_entry 方法。
        """
        return []
    
    # ========== Hook 收集 ==========
    
    def collect_hooks(self) -> Dict[str, List[HookHandler]]:
        """收集本对象中所有 @hook 装饰的方法
        
        Returns:
            Hook 字典，key 为目标 entry ID，value 为 HookHandler 列表
        """
        hooks: Dict[str, List[HookHandler]] = {}
        owner_name = self._get_hook_owner_name()
        
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            
            try:
                value = getattr(self, attr_name)
            except Exception:
                continue
            
            if not callable(value):
                continue
            
            # 检查是否有 Hook 元数据
            meta: Optional[HookMeta] = getattr(value, HOOK_META_ATTR, None)
            if meta is None:
                continue
            
            handler = HookHandler(
                meta=meta,
                handler=value,
                router_name=owner_name,
            )
            
            target = meta.target_entry
            if target not in hooks:
                hooks[target] = []
            hooks[target].append(handler)
        
        # 按优先级排序（越大越先执行）
        for target in hooks:
            hooks[target].sort(key=lambda h: h.meta.priority, reverse=True)
        
        self._hooks = hooks
        return hooks
    
    def get_hooks_for_entry(self, entry_id: str) -> List[HookHandler]:
        """获取指定 entry 的所有 Hook（包括自身和子级的 Hook）
        
        Args:
            entry_id: 入口点 ID
        
        Returns:
            HookHandler 列表（已按优先级排序）
        """
        result: List[HookHandler] = []
        
        # 1. 收集自身的 Hook
        if entry_id in self._hooks:
            result.extend(self._hooks[entry_id])
        if "*" in self._hooks:
            result.extend(self._hooks["*"])
        
        # 2. 收集子级的 Hook（如 Router）
        for child in self._get_child_hook_sources():
            result.extend(child.get_hooks_for_entry(entry_id))
        
        # 重新按优先级排序
        result.sort(key=lambda h: h.meta.priority, reverse=True)
        return result
    
    # ========== Hook 执行 ==========
    
    async def execute_before_hooks(
        self,
        entry_id: str,
        params: Dict[str, Any],
    ) -> Tuple[bool, Optional[Dict[str, Any]], Dict[str, Any]]:
        """执行 before 类型的 Hook
        
        Args:
            entry_id: 入口点 ID
            params: 原始参数
        
        Returns:
            (should_continue, early_result, modified_params)
            - should_continue: 是否继续执行原始 entry
            - early_result: 如果不继续，返回的结果
            - modified_params: 修改后的参数
        """
        hooks = self.get_hooks_for_entry(entry_id)
        current_params = dict(params)
        logger = self._get_hook_logger()
        
        for hook_handler in hooks:
            if hook_handler.meta.timing != "before":
                continue
            
            # 检查条件
            if not self._check_hook_condition(hook_handler, entry_id, current_params):
                continue
            
            try:
                result = hook_handler.handler(
                    entry_id=entry_id,
                    params=current_params,
                )
                if inspect.iscoroutine(result):
                    result = await result
                
                if result is None:
                    continue
                elif isinstance(result, dict):
                    # 检查是否是阻止执行的结果（包含 code/message 等）
                    if "code" in result or "message" in result or "data" in result:
                        return False, result, current_params
                    else:
                        # 这是修改后的参数
                        current_params = result
            except Exception as e:
                if logger:
                    logger.warning(
                        f"Hook {hook_handler.router_name}.{hook_handler.handler.__name__} "
                        f"failed for entry {entry_id}: {e}"
                    )
        
        return True, None, current_params
    
    async def execute_after_hooks(
        self,
        entry_id: str,
        params: Dict[str, Any],
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """执行 after 类型的 Hook
        
        Args:
            entry_id: 入口点 ID
            params: 原始参数
            result: entry 执行结果
        
        Returns:
            修改后的结果
        """
        hooks = self.get_hooks_for_entry(entry_id)
        current_result = dict(result)
        logger = self._get_hook_logger()
        
        for hook_handler in hooks:
            if hook_handler.meta.timing != "after":
                continue
            
            # 检查条件
            if not self._check_hook_condition(hook_handler, entry_id, params):
                continue
            
            try:
                hook_result = hook_handler.handler(
                    entry_id=entry_id,
                    params=params,
                    result=current_result,
                )
                if inspect.iscoroutine(hook_result):
                    hook_result = await hook_result
                
                if hook_result is not None and isinstance(hook_result, dict):
                    current_result = hook_result
            except Exception as e:
                if logger:
                    logger.warning(
                        f"Hook {hook_handler.router_name}.{hook_handler.handler.__name__} "
                        f"failed for entry {entry_id}: {e}"
                    )
        
        return current_result
    
    def get_around_hooks(
        self,
        entry_id: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[HookHandler]:
        """获取 around 类型的 Hook（已过滤条件）
        
        Args:
            entry_id: 入口点 ID
            params: 参数（用于条件检查）
        
        Returns:
            HookHandler 列表
        """
        hooks = self.get_hooks_for_entry(entry_id)
        result = []
        for h in hooks:
            if h.meta.timing == "around":
                if params is None or self._check_hook_condition(h, entry_id, params):
                    result.append(h)
        return result
    
    def get_replace_hook(
        self,
        entry_id: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[HookHandler]:
        """获取 replace 类型的 Hook（只返回优先级最高且满足条件的一个）
        
        Args:
            entry_id: 入口点 ID
            params: 参数（用于条件检查）
        
        Returns:
            HookHandler 或 None
        """
        hooks = self.get_hooks_for_entry(entry_id)
        for h in hooks:
            if h.meta.timing == "replace":
                if params is None or self._check_hook_condition(h, entry_id, params):
                    return h
        return None
    
    def _check_hook_condition(
        self,
        hook_handler: HookHandler,
        entry_id: str,
        params: Dict[str, Any],
    ) -> bool:
        """检查 Hook 条件是否满足
        
        Args:
            hook_handler: Hook 处理器
            entry_id: 入口点 ID
            params: 参数
        
        Returns:
            True 如果条件满足或无条件
        """
        if not hook_handler.meta.condition:
            return True
        
        # 从 handler 所属的对象获取条件方法
        owner = getattr(hook_handler.handler, "__self__", None) or self
        condition_method = getattr(owner, hook_handler.meta.condition, None)
        
        if condition_method and callable(condition_method):
            try:
                return bool(condition_method(entry_id, params))
            except Exception:
                return False
        
        return True
    
    # ========== Handler 包装 ==========
    
    def _wrap_handler_with_hooks(
        self,
        entry_id: str,
        original_handler: Callable,
    ) -> Callable:
        """包装 handler，在执行前后执行 Hook
        
        Args:
            entry_id: 入口点 ID
            original_handler: 原始 handler
        
        Returns:
            包装后的 handler（统一为异步）
        """
        from functools import wraps
        
        executor_ref = self
        is_async = asyncio.iscoroutinefunction(original_handler)
        
        @wraps(original_handler)
        async def wrapped(**kwargs):
            logger = executor_ref._get_hook_logger()
            
            # 1. 执行 before hooks
            should_continue, early_result, modified_params = await executor_ref.execute_before_hooks(
                entry_id, kwargs
            )
            if not should_continue:
                return early_result
            
            # 2. 检查是否有 replace hook
            replace_hook = executor_ref.get_replace_hook(entry_id, modified_params)
            if replace_hook:
                result = await executor_ref._execute_replace_hook(
                    replace_hook, entry_id, modified_params, original_handler, is_async, logger
                )
            else:
                # 3. 构建 around hook 链
                around_hooks = executor_ref.get_around_hooks(entry_id, modified_params)
                if around_hooks:
                    result = await executor_ref._execute_around_chain(
                        around_hooks, entry_id, modified_params, original_handler, is_async, logger
                    )
                else:
                    # 无 around hook，直接执行原始 handler
                    result = await executor_ref._call_handler(original_handler, modified_params, is_async)
            
            # 4. 执行 after hooks
            final_result = await executor_ref.execute_after_hooks(
                entry_id, modified_params, result if isinstance(result, dict) else {"data": result}
            )
            return final_result
        
        return wrapped
    
    async def _call_handler(
        self,
        handler: Callable,
        params: Dict[str, Any],
        is_async: bool,
    ) -> Any:
        """调用 handler（统一处理同步/异步）"""
        if is_async:
            return await handler(**params)
        else:
            # 同步 handler 在线程池中执行，避免阻塞事件循环。
            # Capture contextvars (e.g. _CURRENT_RUN_ID) so that the
            # executor thread inherits them — run_in_executor does NOT
            # propagate contextvars by default.
            loop = asyncio.get_running_loop()
            ctx = contextvars.copy_context()
            return await loop.run_in_executor(
                None, lambda: ctx.run(handler, **params)
            )
    
    async def _execute_replace_hook(
        self,
        replace_hook: HookHandler,
        entry_id: str,
        params: Dict[str, Any],
        original_handler: Callable,
        is_async: bool,
        logger: Any,
    ) -> Any:
        """执行 replace hook"""
        try:
            if logger:
                logger.debug(
                    f"[Hook] replace: {replace_hook.router_name}.{replace_hook.handler.__name__} "
                    f"for entry {entry_id}"
                )
            result = replace_hook.handler(
                entry_id=entry_id,
                params=params,
                original_handler=original_handler,
            )
            if inspect.iscoroutine(result):
                result = await result
            return result
        except Exception as e:
            if logger:
                logger.warning(
                    f"[Hook] replace hook {replace_hook.router_name}.{replace_hook.handler.__name__} "
                    f"failed for entry {entry_id}: {e}, falling back to original handler"
                )
            return await self._call_handler(original_handler, params, is_async)
    
    async def _execute_around_chain(
        self,
        around_hooks: List[HookHandler],
        entry_id: str,
        params: Dict[str, Any],
        original_handler: Callable,
        is_async: bool,
        logger: Any,
    ) -> Any:
        """执行 around hook 链"""
        executor_ref = self
        
        async def build_chain(hooks_remaining: List[HookHandler], current_params: Dict[str, Any]) -> Any:
            if not hooks_remaining:
                # 链的末端：执行原始 handler
                return await executor_ref._call_handler(original_handler, current_params, is_async)
            
            current_hook = hooks_remaining[0]
            rest_hooks = hooks_remaining[1:]
            
            # 创建 next_handler 供 around hook 调用
            async def next_handler(p: Optional[Dict[str, Any]] = None) -> Any:
                return await build_chain(rest_hooks, p if p is not None else current_params)
            
            # 执行当前 around hook
            try:
                if logger:
                    logger.debug(
                        f"[Hook] around: {current_hook.router_name}.{current_hook.handler.__name__} "
                        f"for entry {entry_id}"
                    )
                hook_result = current_hook.handler(
                    entry_id=entry_id,
                    params=current_params,
                    next_handler=next_handler,
                )
                if inspect.iscoroutine(hook_result):
                    hook_result = await hook_result
                return hook_result
            except Exception as e:
                if logger:
                    logger.warning(
                        f"[Hook] around hook {current_hook.router_name}.{current_hook.handler.__name__} "
                        f"failed for entry {entry_id}: {e}, skipping to next"
                    )
                return await build_chain(rest_hooks, current_params)
        
        return await build_chain(around_hooks, params)
