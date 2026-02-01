"""
插件基类模块

提供插件开发的基础类和接口。
"""
from dataclasses import dataclass, field
from pathlib import Path
import asyncio
import inspect
from typing import TYPE_CHECKING, Optional, Dict, Any, List, Union, Callable
from functools import wraps
from .events import EventHandler, EventMeta, EVENT_META_ATTR
from .hooks import HookMeta, HookHandler, HOOK_META_ATTR
from .config import PluginConfig
from .plugins import Plugins
from .version import SDK_VERSION
from .state import StatePersistence
from .store import PluginStore
from .database import PluginDatabase
from plugin.settings import (
    NEKO_PLUGIN_META_ATTR, 
    NEKO_PLUGIN_TAG,
    PLUGIN_LOG_LEVEL,
    PLUGIN_LOG_MAX_BYTES,
    PLUGIN_LOG_BACKUP_COUNT,
    PLUGIN_LOG_MAX_FILES,
)

if TYPE_CHECKING:
    from plugin.core.context import PluginContext
    from .router import PluginRouter


@dataclass
class PluginMeta:
    """插件元数据（SDK 内部使用）"""
    id: str
    name: str
    version: str = "0.1.0"
    sdk_version: str = SDK_VERSION
    sdk_recommended: Optional[str] = None
    sdk_supported: Optional[str] = None
    sdk_untested: Optional[str] = None
    sdk_conflicts: List[str] = field(default_factory=list)
    description: str = ""


class NekoPluginBase:
    """插件都继承这个基类.
    
    Attributes:
        __freezable__: 声明需要持久化保存的属性名列表。
            示例: __freezable__ = ["counter", "cache", "user_prefs"]
        __persist_mode__: 状态持久化模式配置（默认 "off"，需在 toml 中启用）。
            - "auto": 所有 entry 执行后自动保存状态
            - "manual": 只在 freeze 时保存，或在 @plugin_entry(persist=True) 显式启用
            - "off": 完全禁用持久化功能（默认）
            
            优先级：toml [plugin_state].persist_mode > 类属性 > 默认值
    """
    
    # 子类可以覆盖这个列表，声明需要持久化保存的属性
    __freezable__: List[str] = []
    # 子类可以覆盖这个值，控制持久化模式（默认 off，需在 toml 中启用）
    __persist_mode__: str = "off"  # "auto" | "manual" | "off"
    
    def __init__(self, ctx: "PluginContext"):
        self.ctx: "PluginContext" = ctx
        self._plugin_id = getattr(ctx, "plugin_id", "unknown")
        self.config = PluginConfig(ctx)
        self.plugins = Plugins(ctx)
        
        # Router 列表：存储所有通过 include_router 注册的路由器
        self._routers: List["PluginRouter"] = []
        # 动态入口点缓存：存储所有 Router 的入口点
        self._router_entries: Dict[str, EventHandler] = {}
        # 插件级别的 Hook：target -> hooks
        self._hooks: Dict[str, List[HookHandler]] = {}
        
        # 初始化冻结 checkpoint 管理器和持久化存储
        config_path = getattr(ctx, "config_path", None)
        plugin_dir = config_path.parent if config_path else Path.cwd()
        
        # 读取 state_backend 配置（默认 off，需要开发者显式启用）
        state_backend = "off"  # 默认禁用
        try:
            from plugin.settings import PLUGIN_STATE_BACKEND_DEFAULT
            state_backend = PLUGIN_STATE_BACKEND_DEFAULT
            # 尝试从插件配置覆盖
            if hasattr(self, 'config'):
                cfg = self.config.dump_effective_sync(timeout=1.0)
                state_cfg = cfg.get("plugin_state", {})
                if isinstance(state_cfg, dict):
                    cfg_backend = state_cfg.get("backend")
                    if cfg_backend in ("memory", "file", "off"):
                        state_backend = cfg_backend
        except Exception:
            pass
        
        self._state_persistence = StatePersistence(
            plugin_id=self._plugin_id,
            plugin_dir=plugin_dir,
            logger=getattr(ctx, "logger", None),
            backend=state_backend,
        )
        # 向后兼容别名
        self._freeze_checkpoint = self._state_persistence
        
        # 读取 store 配置（默认禁用，需要在 plugin.toml 中显式启用）
        store_enabled = False  # 默认禁用
        try:
            if hasattr(self, 'config'):
                cfg = self.config.dump_effective_sync(timeout=1.0)
                store_cfg = cfg.get("plugin", {}).get("store", {})
                if isinstance(store_cfg, dict):
                    store_enabled = store_cfg.get("enabled", False)
        except Exception:
            pass
        
        self.store = PluginStore(
            plugin_id=self._plugin_id,
            plugin_dir=plugin_dir,
            logger=getattr(ctx, "logger", None),
            enabled=store_enabled,
        )
        
        # 读取 database 配置（默认禁用，需要在 plugin.toml 中显式启用）
        db_enabled = False  # 默认禁用
        db_name = None  # 默认使用 {plugin_id}.db
        try:
            if hasattr(self, 'config'):
                cfg = self.config.dump_effective_sync(timeout=1.0)
                db_cfg = cfg.get("plugin", {}).get("database", {})
                if isinstance(db_cfg, dict):
                    db_enabled = db_cfg.get("enabled", False)
                    db_name = db_cfg.get("name")  # 可选：自定义数据库文件名
        except Exception:
            pass
        
        self.db = PluginDatabase(
            plugin_id=self._plugin_id,
            plugin_dir=plugin_dir,
            logger=getattr(ctx, "logger", None),
            enabled=db_enabled,
            db_name=db_name,
        )

    def get_input_schema(self) -> Dict[str, Any]:
        """默认从类属性 input_schema 取."""
        schema = getattr(self, "input_schema", None)
        return schema or {}

    def include_router(
        self,
        router: "PluginRouter",
        *,
        prefix: str = "",
    ) -> None:
        """注册一个 PluginRouter（支持动态加载）
        
        将 Router 中定义的所有入口点注册到主插件中。
        Router 中的入口点可以访问主插件的 ctx、config、plugins 等功能。
        
        Args:
            router: PluginRouter 实例
            prefix: 可选的前缀，会覆盖 Router 自身的 prefix
        
        Example:
            >>> self.include_router(DebugRouter())
            >>> self.include_router(MemoryRouter(), prefix="mem_")
        """
        logger = getattr(self.ctx, "logger", None)
        
        # 如果提供了 prefix 参数，覆盖 router 的 prefix
        if prefix:
            router.prefix = prefix
        
        # 绑定 router 到当前插件
        router._bind(self)
        
        # 收集并注册入口点
        try:
            router_entries = router.collect_entries()
            for entry_id, handler in router_entries.items():
                if entry_id in self._router_entries:
                    if logger:
                        logger.warning(
                            f"Duplicate entry id '{entry_id}' from router "
                            f"{router.name} in plugin {self._plugin_id}"
                        )
                self._router_entries[entry_id] = handler
            
            # 添加到 router 列表
            self._routers.append(router)
            
            # 调用挂载回调（支持 async）
            try:
                result = router.on_mount()
                if inspect.iscoroutine(result):
                    # 如果是协程，尝试在当前事件循环中运行
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(result)
                    except RuntimeError:
                        # 没有运行中的事件循环，同步运行
                        asyncio.run(result)
            except Exception as e:
                if logger:
                    logger.warning(f"Router {router.name} on_mount failed: {e}")
            
            if logger:
                logger.info(
                    f"Router '{router.name}' mounted with {len(router_entries)} entries: "
                    f"{list(router_entries.keys())}"
                )
        except Exception as e:
            # 回滚绑定
            router._unbind()
            if logger:
                logger.error(f"Failed to mount router {router.name}: {e}")
            raise
    
    def exclude_router(
        self,
        router: Union["PluginRouter", str],
    ) -> bool:
        """卸载一个 PluginRouter（支持动态卸载）
        
        移除 Router 中定义的所有入口点。
        
        Args:
            router: PluginRouter 实例或 Router 名称字符串
        
        Returns:
            True 如果成功卸载，False 如果未找到
        
        Example:
            >>> self.exclude_router(my_router)  # 通过实例卸载
            >>> self.exclude_router("MyRouter")  # 通过名称卸载
        """
        logger = getattr(self.ctx, "logger", None)
        
        # 查找要卸载的 router
        target_router: Optional["PluginRouter"] = None
        if isinstance(router, str):
            # 通过名称查找
            for r in self._routers:
                if r.name == router:
                    target_router = r
                    break
        else:
            # 直接使用实例
            if router in self._routers:
                target_router = router
        
        if target_router is None:
            router_name = router if isinstance(router, str) else router.name
            if logger:
                logger.warning(f"Router '{router_name}' not found, cannot exclude")
            return False
        
        # 移除入口点
        removed_entries = []
        for entry_id in target_router.entry_ids:
            if entry_id in self._router_entries:
                del self._router_entries[entry_id]
                removed_entries.append(entry_id)
        
        # 调用卸载回调（支持 async）
        try:
            result = target_router.on_unmount()
            if inspect.iscoroutine(result):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(result)
                except RuntimeError:
                    asyncio.run(result)
        except Exception as e:
            if logger:
                logger.warning(f"Router {target_router.name} on_unmount failed: {e}")
        
        # 解除绑定
        target_router._unbind()
        
        # 从列表中移除
        self._routers.remove(target_router)
        
        if logger:
            logger.info(
                f"Router '{target_router.name}' unmounted, removed {len(removed_entries)} entries: "
                f"{removed_entries}"
            )
        
        return True
    
    def get_router(self, name: str) -> Optional["PluginRouter"]:
        """通过名称获取已注册的 Router
        
        Args:
            name: Router 名称
        
        Returns:
            PluginRouter 实例，如果未找到则返回 None
        """
        for r in self._routers:
            if r.name == name:
                return r
        return None
    
    def list_routers(self) -> List[str]:
        """获取所有已注册的 Router 名称列表"""
        return [r.name for r in self._routers]
    
    # ========== Hook 支持 ==========
    
    def collect_hooks(self) -> Dict[str, List[HookHandler]]:
        """收集插件自身的所有 @hook 装饰的方法
        
        Returns:
            Hook 字典，key 为目标 entry ID，value 为 HookHandler 列表
        """
        hooks: Dict[str, List[HookHandler]] = {}
        
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
                router_name=self.__class__.__name__,
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
        """获取指定 entry 的所有 Hook（包括插件自身和所有 Router 的 Hook）
        
        Args:
            entry_id: 入口点 ID
        
        Returns:
            HookHandler 列表（已按优先级排序）
        """
        result: List[HookHandler] = []
        
        # 1. 收集插件自身的 Hook
        if entry_id in self._hooks:
            result.extend(self._hooks[entry_id])
        if "*" in self._hooks:
            result.extend(self._hooks["*"])
        
        # 2. 收集所有 Router 的 Hook
        for router in self._routers:
            result.extend(router.get_hooks_for_entry(entry_id))
        
        # 重新按优先级排序
        result.sort(key=lambda h: h.meta.priority, reverse=True)
        return result
    
    async def execute_before_hooks(
        self,
        entry_id: str,
        params: Dict[str, Any],
    ) -> tuple[bool, Optional[Dict[str, Any]], Dict[str, Any]]:
        """执行 before 类型的 Hook
        
        Args:
            entry_id: 入口点 ID
            params: 原始参数
        
        Returns:
            (should_continue, early_result, modified_params)
        """
        hooks = self.get_hooks_for_entry(entry_id)
        current_params = dict(params)
        logger = getattr(self.ctx, "logger", None)
        
        for hook_handler in hooks:
            if hook_handler.meta.timing != "before":
                continue
            
            # 检查条件
            if hook_handler.meta.condition:
                # 从 handler 所属的对象获取条件方法
                owner = getattr(hook_handler.handler, "__self__", None) or self
                condition_method = getattr(owner, hook_handler.meta.condition, None)
                if condition_method and callable(condition_method):
                    if not condition_method(entry_id, current_params):
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
                    if "code" in result or "message" in result or "data" in result:
                        return False, result, current_params
                    else:
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
        logger = getattr(self.ctx, "logger", None)
        
        for hook_handler in hooks:
            if hook_handler.meta.timing != "after":
                continue
            
            if hook_handler.meta.condition:
                owner = getattr(hook_handler.handler, "__self__", None) or self
                condition_method = getattr(owner, hook_handler.meta.condition, None)
                if condition_method and callable(condition_method):
                    if not condition_method(entry_id, params):
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
    
    def get_replace_hook(self, entry_id: str) -> Optional[HookHandler]:
        """获取 replace 类型的 Hook（只返回优先级最高的一个）"""
        hooks = self.get_hooks_for_entry(entry_id)
        for h in hooks:
            if h.meta.timing == "replace":
                return h
        return None
    
    def get_around_hooks(self, entry_id: str) -> List[HookHandler]:
        """获取 around 类型的 Hook（按优先级排序）"""
        hooks = self.get_hooks_for_entry(entry_id)
        return [h for h in hooks if h.meta.timing == "around"]
    
    def _wrap_handler_with_hooks(self, entry_id: str, original_handler: Callable) -> Callable:
        """包装 handler，在执行前后执行 Hook
        
        Args:
            entry_id: 入口点 ID
            original_handler: 原始 handler
        
        Returns:
            包装后的 handler（保持原始 handler 的同步/异步特性）
        """
        plugin_ref = self
        is_async = asyncio.iscoroutinefunction(original_handler)
        
        @wraps(original_handler)
        async def async_wrapped(**kwargs):
            # 1. 执行 before hooks
            should_continue, early_result, modified_params = await plugin_ref.execute_before_hooks(
                entry_id, kwargs
            )
            if not should_continue:
                return early_result
            
            # 2. 检查是否有 replace hook
            replace_hook = plugin_ref.get_replace_hook(entry_id)
            if replace_hook:
                # 使用 replace hook 替代原始 handler
                result = replace_hook.handler(
                    entry_id=entry_id,
                    params=modified_params,
                    original_handler=original_handler,
                )
                if inspect.iscoroutine(result):
                    result = await result
            else:
                # 3. 构建 around hook 链
                around_hooks = plugin_ref.get_around_hooks(entry_id)
                
                if around_hooks:
                    # 构建调用链：around_hook_1 -> around_hook_2 -> ... -> original_handler
                    async def build_chain(hooks_remaining, params):
                        if not hooks_remaining:
                            # 链的末端：执行原始 handler
                            r = original_handler(**params)
                            if inspect.iscoroutine(r):
                                r = await r
                            return r
                        
                        # 取出当前 hook
                        current_hook = hooks_remaining[0]
                        rest_hooks = hooks_remaining[1:]
                        
                        # 创建 next_handler 供 around hook 调用
                        async def next_handler(p=None):
                            return await build_chain(rest_hooks, p if p is not None else params)
                        
                        # 执行当前 around hook
                        hook_result = current_hook.handler(
                            entry_id=entry_id,
                            params=params,
                            next_handler=next_handler,
                        )
                        if inspect.iscoroutine(hook_result):
                            hook_result = await hook_result
                        return hook_result
                    
                    result = await build_chain(around_hooks, modified_params)
                else:
                    # 无 around hook，直接执行原始 handler
                    result = original_handler(**modified_params)
                    if inspect.iscoroutine(result):
                        result = await result
            
            # 4. 执行 after hooks
            final_result = await plugin_ref.execute_after_hooks(
                entry_id, modified_params, result if isinstance(result, dict) else {"data": result}
            )
            return final_result
        
        @wraps(original_handler)
        def sync_wrapped(**kwargs):
            """同步包装器：在独立事件循环中执行 Hook，但在当前线程中执行原始同步 handler"""
            # 1. 执行 before hooks（在新事件循环中）
            should_continue, early_result, modified_params = asyncio.run(
                plugin_ref.execute_before_hooks(entry_id, kwargs)
            )
            if not should_continue:
                return early_result
            
            # 2. 检查是否有 replace hook
            replace_hook = plugin_ref.get_replace_hook(entry_id)
            if replace_hook:
                # 使用 replace hook 替代原始 handler
                result = replace_hook.handler(
                    entry_id=entry_id,
                    params=modified_params,
                    original_handler=original_handler,
                )
                if inspect.iscoroutine(result):
                    result = asyncio.run(result)
            else:
                # 3. 构建 around hook 链
                around_hooks = plugin_ref.get_around_hooks(entry_id)
                
                if around_hooks:
                    # 对于同步 handler，around hook 链需要在事件循环中执行
                    async def build_chain_async(hooks_remaining, params):
                        if not hooks_remaining:
                            # 链的末端：执行原始同步 handler
                            return original_handler(**params)
                        
                        current_hook = hooks_remaining[0]
                        rest_hooks = hooks_remaining[1:]
                        
                        async def next_handler(p=None):
                            return await build_chain_async(rest_hooks, p if p is not None else params)
                        
                        hook_result = current_hook.handler(
                            entry_id=entry_id,
                            params=params,
                            next_handler=next_handler,
                        )
                        if inspect.iscoroutine(hook_result):
                            hook_result = await hook_result
                        return hook_result
                    
                    result = asyncio.run(build_chain_async(around_hooks, modified_params))
                else:
                    # 无 around hook，直接执行原始同步 handler
                    result = original_handler(**modified_params)
            
            # 4. 执行 after hooks（在新事件循环中）
            final_result = asyncio.run(
                plugin_ref.execute_after_hooks(
                    entry_id, modified_params, result if isinstance(result, dict) else {"data": result}
                )
            )
            return final_result
        
        # 根据原始 handler 的类型返回对应的包装器
        # 这样可以保持原始 handler 的同步/异步特性
        if is_async:
            return async_wrapped
        else:
            return sync_wrapped
    
    def collect_entries(self, wrap_with_hooks: bool = True) -> Dict[str, EventHandler]:
        """
        收集所有入口点，包括：
        1. 插件自身的方法（带 @plugin_entry 等装饰器）
        2. 所有注册的 Router 中的入口点
        
        Args:
            wrap_with_hooks: 是否用 Hook 包装 handler（默认 True）
        """
        entries: Dict[str, EventHandler] = {}
        logger = getattr(self.ctx, "logger", None)
        
        # 先收集 hooks（如果还没收集）
        if wrap_with_hooks:
            self.collect_hooks()
            # 同时收集所有 Router 的 hooks
            for router in self._routers:
                router.collect_hooks()
        
        # 检查是否有任何 Hook（包括插件自身和所有 Router）
        has_any_hooks = bool(self._hooks)
        if not has_any_hooks:
            for router in self._routers:
                if router._hooks:
                    has_any_hooks = True
                    break
        
        # 如果没有任何 Hook，跳过包装
        should_wrap = wrap_with_hooks and has_any_hooks
        
        # 1. 收集插件自身的入口点
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            try:
                value = getattr(self, attr_name)
            except Exception:
                continue
            if not callable(value):
                continue
            meta: EventMeta | None = getattr(value, EVENT_META_ATTR, None)
            if meta:
                if meta.id in entries:
                    if logger:
                        logger.warning(f"Duplicate entry id '{meta.id}' in plugin {self._plugin_id}")
                
                # 包装 handler（仅当有 Hook 时）
                handler = value
                if should_wrap:
                    handler = self._wrap_handler_with_hooks(meta.id, value)
                
                entries[meta.id] = EventHandler(meta=meta, handler=handler)
        
        # 2. 合并 Router 的入口点
        for entry_id, event_handler in self._router_entries.items():
            if entry_id in entries:
                if logger:
                    logger.warning(f"Router entry '{entry_id}' conflicts with plugin entry")
            
            # Router 的 handler 也需要包装（仅当有 Hook 时）
            handler = event_handler.handler
            if should_wrap:
                handler = self._wrap_handler_with_hooks(entry_id, event_handler.handler)
            
            entries[entry_id] = EventHandler(meta=event_handler.meta, handler=handler)
        
        return entries
    
    def report_status(self, status: Dict[str, Any]) -> None:
        """
        插件内部调用此方法上报状态。
        通过 ctx.update_status 把状态发回主进程。
        """
        if hasattr(self.ctx, "update_status"):
            # 这里只传原始 status，由 Context 负责打包成队列消息
            self.ctx.update_status(status)
        else:
            logger = getattr(self.ctx, "logger", None)
            if logger:
                logger.warning(
                    f"Plugin {self._plugin_id} tried to report status but ctx.update_status is missing."
                )
    
    def enable_file_logging(
        self,
        log_level: Optional[str] = None,
        max_bytes: Optional[int] = None,
        backup_count: Optional[int] = None,
        max_files: Optional[int] = None,
    ) -> Any:
        """
        启用插件文件日志功能（使用loguru）
        
        为插件创建独立的文件日志，日志文件保存在插件的logs目录下。
        日志会同时输出到文件和控制台（终端）。
        自动管理日志文件数量，支持日志轮转。
        
        Args:
            log_level: 日志级别（字符串："DEBUG", "INFO", "WARNING", "ERROR"），默认使用配置中的PLUGIN_LOG_LEVEL
            max_bytes: 单个日志文件最大大小（字节），默认使用配置中的PLUGIN_LOG_MAX_BYTES
            backup_count: 保留的备份文件数量，默认使用配置中的PLUGIN_LOG_BACKUP_COUNT
            max_files: 最多保留的日志文件总数，默认使用配置中的PLUGIN_LOG_MAX_FILES
            
        Returns:
            配置好的loguru logger实例（已添加文件handler和控制台handler）
            
        使用示例:
            ```python
            class MyPlugin(NekoPluginBase):
                def __init__(self, ctx):
                    super().__init__(ctx)
                    # 启用文件日志（同时输出到文件和控制台）
                    self.file_logger = self.enable_file_logging(log_level="DEBUG")
                    # 使用file_logger记录日志，会同时显示在终端和保存到文件
                    self.file_logger.info("Plugin initialized")
            ```
        
        注意:
            - 日志文件保存在 `{plugin_dir}/logs/` 目录下
            - 日志文件名格式：`{plugin_id}_{YYYYMMDD_HHMMSS}.log`（包含日期和时间）
            - 日志会同时输出到文件和控制台（终端）
            - 当日志文件达到最大大小时会自动轮转
            - 超过最大文件数量限制的旧日志会自动删除
        """
        # 延迟导入，避免循环依赖
        from .logger import enable_plugin_file_logging
        
        # 获取插件目录（config_path的父目录）
        config_path = getattr(self.ctx, "config_path", None)
        plugin_dir = config_path.parent if config_path else Path.cwd()
        
        # 使用配置中的默认值
        log_level = log_level if log_level is not None else PLUGIN_LOG_LEVEL
        max_bytes = max_bytes if max_bytes is not None else PLUGIN_LOG_MAX_BYTES
        backup_count = backup_count if backup_count is not None else PLUGIN_LOG_BACKUP_COUNT
        max_files = max_files if max_files is not None else PLUGIN_LOG_MAX_FILES
        
        # 启用文件日志
        file_logger = enable_plugin_file_logging(
            plugin_id=self._plugin_id,
            plugin_dir=plugin_dir,
            logger=getattr(self.ctx, "logger", None),
            log_level=log_level,
            max_bytes=max_bytes,
            backup_count=backup_count,
            max_files=max_files,
        )
        
        # 将file_logger保存到实例，方便访问
        self.file_logger = file_logger
        
        return file_logger
