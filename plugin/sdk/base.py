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
from .hook_executor import HookExecutorMixin
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


class NekoPluginBase(HookExecutorMixin):
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
        # 初始化 Hook 执行器（来自 HookExecutorMixin）
        self.__init_hook_executor__()
        # 静态 UI 配置（实例属性，避免类属性共享问题）
        self._static_ui_config: Optional[Dict[str, Any]] = None
        
        # 初始化冻结 checkpoint 管理器和持久化存储
        config_path = getattr(ctx, "config_path", None)
        plugin_dir = config_path.parent if config_path else Path.cwd()
        
        # 一次性读取有效配置（避免多次同步 IPC 调用拖慢启动）
        _effective_cfg: Dict[str, Any] = {}
        try:
            if hasattr(self, 'config'):
                _effective_cfg = self.config.dump_effective_sync(timeout=1.0)
                if not isinstance(_effective_cfg, dict):
                    _effective_cfg = {}
        except Exception:
            pass
        
        # 读取 state_backend 配置（默认 off，需要开发者显式启用）
        state_backend = "off"
        try:
            from plugin.settings import PLUGIN_STATE_BACKEND_DEFAULT
            state_backend = PLUGIN_STATE_BACKEND_DEFAULT
            state_cfg = _effective_cfg.get("plugin_state", {})
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
        # 向后兼容别名（已弃用，将在 v2.0 移除，请使用 _state_persistence）
        self._freeze_checkpoint = self._state_persistence
        
        # 读取 store 配置（默认禁用，需要在 plugin.toml 中显式启用）
        store_enabled = False
        try:
            store_cfg = _effective_cfg.get("plugin", {}).get("store", {})
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
        db_enabled = False
        db_name = None
        try:
            db_cfg = _effective_cfg.get("plugin", {}).get("database", {})
            if isinstance(db_cfg, dict):
                db_enabled = db_cfg.get("enabled", False)
                db_name = db_cfg.get("name")
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
    
    # ========== 静态 UI 注册 ==========
    
    def register_static_ui(
        self,
        directory: str = "static",
        *,
        index_file: str = "index.html",
        cache_control: str = "public, max-age=3600",
    ) -> bool:
        """显式注册静态 UI 文件目录
        
        插件必须调用此方法才能启用静态 UI 功能。
        
        Args:
            directory: 静态文件目录（相对于插件目录），默认 "static"
            index_file: 入口文件名，默认 "index.html"
            cache_control: 缓存控制头，默认 "public, max-age=3600"
        
        Returns:
            True 如果注册成功
        
        Example:
            >>> # 在 on_startup 中调用
            >>> @lifecycle(id="startup")
            >>> async def on_startup(self):
            >>>     self.register_static_ui("static")
        """
        logger = getattr(self.ctx, "logger", None)
        config_path = getattr(self.ctx, "config_path", None)
        
        if not config_path:
            if logger:
                logger.warning("Cannot register static UI: config_path not available")
            return False
        
        plugin_dir = Path(config_path).parent
        static_dir = plugin_dir / directory
        
        if not static_dir.exists():
            if logger:
                logger.warning(f"Static UI directory not found: {static_dir}")
            return False
        
        index_path = static_dir / index_file
        if not index_path.exists():
            if logger:
                logger.warning(f"Static UI index file not found: {index_path}")
            return False
        
        # 保存配置
        self._static_ui_config = {
            "enabled": True,
            "directory": str(static_dir),
            "index_file": index_file,
            "cache_control": cache_control,
            "plugin_id": self._plugin_id,
        }
        
        # 通知主进程注册静态 UI（通过消息队列）
        try:
            message_queue = getattr(self.ctx, "message_queue", None)
            if message_queue is not None:
                message_queue.put_nowait({
                    "type": "STATIC_UI_REGISTER",
                    "plugin_id": self._plugin_id,
                    "config": self._static_ui_config,
                })
        except Exception as e:
            if logger:
                logger.debug(f"Failed to notify main process about static UI: {e}")
        
        if logger:
            logger.info(f"Static UI registered: {static_dir}")
        
        return True
    
    def get_static_ui_config(self) -> Optional[Dict[str, Any]]:
        """获取静态 UI 配置"""
        return self._static_ui_config
    
    # ========== 动态 Entry 管理 ==========
    
    async def register_dynamic_entry(
        self,
        entry_id: str,
        handler: Callable,
        name: str = "",
        description: str = "",
        input_schema: Optional[Dict[str, Any]] = None,
        kind: str = "action",
        auto_start: bool = False,
    ) -> bool:
        """动态注册一个 entry
        
        在运行时创建并注册一个新的 entry，会通知主进程更新 entry 列表。
        
        Args:
            entry_id: entry 的唯一标识符
            handler: 处理函数（async 或 sync）
            name: 显示名称（默认使用 entry_id）
            description: 描述信息
            input_schema: 输入参数的 JSON Schema
            kind: entry 类型（action/service/hook/custom）
            auto_start: 是否自动启动
        
        Returns:
            True 如果注册成功
        
        Example:
            >>> async def my_handler(self, arg1: str, **_):
            ...     return ok(data={"result": arg1})
            >>> await self.register_dynamic_entry("my_entry", my_handler, name="My Entry")
        """
        logger = getattr(self.ctx, "logger", None)
        
        # 检查是否已存在
        if entry_id in self._router_entries:
            if logger:
                logger.warning(f"Dynamic entry '{entry_id}' already exists, will replace")
        
        # 创建 EventMeta
        meta = EventMeta(
            event_type="plugin_entry",
            id=entry_id,
            name=name or entry_id,
            description=description,
            input_schema=input_schema,
            kind=kind,  # type: ignore
            auto_start=auto_start,
            enabled=True,
            dynamic=True,
            metadata={"_dynamic": True, "_registered_at": __import__("time").time()},
        )
        
        # 给 handler 添加元数据属性
        setattr(handler, EVENT_META_ATTR, meta)
        
        # 注册到本地
        event_handler = EventHandler(meta=meta, handler=handler)
        self._router_entries[entry_id] = event_handler
        
        # 更新 ctx._entry_map（如果存在）
        if hasattr(self.ctx, "_entry_map") and self.ctx._entry_map is not None:
            self.ctx._entry_map[entry_id] = handler
        
        # 通知主进程
        await self._notify_entry_update("register", entry_id, meta)
        
        if logger:
            logger.info(f"Dynamic entry '{entry_id}' registered successfully")
        
        return True
    
    async def unregister_dynamic_entry(self, entry_id: str) -> bool:
        """注销一个动态 entry
        
        移除之前通过 register_dynamic_entry 注册的 entry。
        
        Args:
            entry_id: entry 的唯一标识符
        
        Returns:
            True 如果注销成功，False 如果 entry 不存在
        """
        logger = getattr(self.ctx, "logger", None)
        
        if entry_id not in self._router_entries:
            if logger:
                logger.warning(f"Dynamic entry '{entry_id}' not found")
            return False
        
        # 检查是否是动态 entry
        event_handler = self._router_entries[entry_id]
        if not getattr(event_handler.meta, "dynamic", False):
            if logger:
                logger.warning(f"Entry '{entry_id}' is not a dynamic entry, cannot unregister")
            return False
        
        # 从本地移除
        del self._router_entries[entry_id]
        
        # 从 ctx._entry_map 中移除（如果存在）
        if hasattr(self.ctx, "_entry_map") and self.ctx._entry_map is not None:
            if entry_id in self.ctx._entry_map:
                del self.ctx._entry_map[entry_id]
        
        # 通知主进程
        await self._notify_entry_update("unregister", entry_id, None)
        
        if logger:
            logger.info(f"Dynamic entry '{entry_id}' unregistered successfully")
        
        return True
    
    async def enable_entry(self, entry_id: str) -> bool:
        """启用一个 entry
        
        Args:
            entry_id: entry 的唯一标识符
        
        Returns:
            True 如果启用成功
        """
        logger = getattr(self.ctx, "logger", None)
        
        # 查找 entry（先在 router entries 中查找，再在自身方法中查找）
        event_handler = self._router_entries.get(entry_id)
        if event_handler:
            # 更新 enabled 状态
            object.__setattr__(event_handler.meta, "enabled", True)
            await self._notify_entry_update("enable", entry_id, event_handler.meta)
            if logger:
                logger.info(f"Entry '{entry_id}' enabled")
            return True
        
        # 在自身方法中查找
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            try:
                value = getattr(self, attr_name)
            except Exception:
                continue
            if not callable(value):
                continue
            meta = getattr(value, EVENT_META_ATTR, None)
            if meta and meta.id == entry_id:
                object.__setattr__(meta, "enabled", True)
                await self._notify_entry_update("enable", entry_id, meta)
                if logger:
                    logger.info(f"Entry '{entry_id}' enabled")
                return True
        
        if logger:
            logger.warning(f"Entry '{entry_id}' not found")
        return False
    
    async def disable_entry(self, entry_id: str) -> bool:
        """禁用一个 entry
        
        禁用后，entry 仍然存在但不会被调用。
        
        Args:
            entry_id: entry 的唯一标识符
        
        Returns:
            True 如果禁用成功
        """
        logger = getattr(self.ctx, "logger", None)
        
        # 查找 entry
        event_handler = self._router_entries.get(entry_id)
        if event_handler:
            object.__setattr__(event_handler.meta, "enabled", False)
            await self._notify_entry_update("disable", entry_id, event_handler.meta)
            if logger:
                logger.info(f"Entry '{entry_id}' disabled")
            return True
        
        # 在自身方法中查找
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            try:
                value = getattr(self, attr_name)
            except Exception:
                continue
            if not callable(value):
                continue
            meta = getattr(value, EVENT_META_ATTR, None)
            if meta and meta.id == entry_id:
                object.__setattr__(meta, "enabled", False)
                await self._notify_entry_update("disable", entry_id, meta)
                if logger:
                    logger.info(f"Entry '{entry_id}' disabled")
                return True
        
        if logger:
            logger.warning(f"Entry '{entry_id}' not found")
        return False
    
    def is_entry_enabled(self, entry_id: str) -> Optional[bool]:
        """检查 entry 是否启用
        
        Args:
            entry_id: entry 的唯一标识符
        
        Returns:
            True/False 表示启用/禁用状态，None 表示 entry 不存在
        """
        # 查找 entry
        event_handler = self._router_entries.get(entry_id)
        if event_handler:
            return getattr(event_handler.meta, "enabled", True)
        
        # 在自身方法中查找
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            try:
                value = getattr(self, attr_name)
            except Exception:
                continue
            if not callable(value):
                continue
            meta = getattr(value, EVENT_META_ATTR, None)
            if meta and meta.id == entry_id:
                return getattr(meta, "enabled", True)
        
        return None
    
    def list_entries(self, include_disabled: bool = False) -> List[Dict[str, Any]]:
        """列出所有 entry
        
        Args:
            include_disabled: 是否包含禁用的 entry
        
        Returns:
            entry 信息列表
        """
        entries = []
        
        # 收集所有 entry
        all_entries = self.collect_entries(wrap_with_hooks=False)
        
        for entry_id, event_handler in all_entries.items():
            enabled = getattr(event_handler.meta, "enabled", True)
            if not include_disabled and not enabled:
                continue
            
            entries.append({
                "id": entry_id,
                "name": event_handler.meta.name,
                "description": event_handler.meta.description,
                "kind": event_handler.meta.kind,
                "enabled": enabled,
                "dynamic": getattr(event_handler.meta, "dynamic", False),
                "auto_start": event_handler.meta.auto_start,
            })
        
        return entries
    
    async def _notify_entry_update(
        self,
        action: str,
        entry_id: str,
        meta: Optional[EventMeta],
    ) -> None:
        """通知主进程 entry 状态变化
        
        Args:
            action: 操作类型（register/unregister/enable/disable）
            entry_id: entry ID
            meta: entry 元数据（unregister 时为 None）
        """
        try:
            # 构建消息
            msg = {
                "type": "ENTRY_UPDATE",
                "action": action,
                "entry_id": entry_id,
                "plugin_id": self._plugin_id,
            }
            
            if meta:
                msg["meta"] = {
                    "id": meta.id,
                    "name": meta.name,
                    "description": meta.description,
                    "kind": meta.kind,
                    "enabled": getattr(meta, "enabled", True),
                    "dynamic": getattr(meta, "dynamic", False),
                    "auto_start": meta.auto_start,
                    "input_schema": meta.input_schema,
                }
            
            # 通过消息队列发送到主进程
            message_queue = getattr(self.ctx, "message_queue", None)
            if message_queue is not None:
                message_queue.put_nowait(msg)
        except Exception as e:
            logger = getattr(self.ctx, "logger", None)
            if logger:
                logger.warning(f"Failed to notify entry update: {e}")
    
    # ========== HookExecutorMixin 抽象方法实现 ==========
    
    def _get_hook_logger(self) -> Any:
        """获取日志记录器（HookExecutorMixin 抽象方法实现）"""
        return getattr(self.ctx, "logger", None)
    
    def _get_hook_owner_name(self) -> str:
        """获取所属对象名称（HookExecutorMixin 抽象方法实现）"""
        return self.__class__.__name__
    
    def _get_child_hook_sources(self) -> List["PluginRouter"]:
        """获取子级 Hook 来源列表（HookExecutorMixin 抽象方法实现）
        
        返回所有注册的 Router，它们的 Hook 会被合并到插件的 Hook 执行链中。
        """
        return self._routers
    
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
