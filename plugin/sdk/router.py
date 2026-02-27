"""
PluginRouter - 插件路由器模块

提供类似 FastAPI Router 的模块化入口点组织方式。
允许开发者将插件功能拆分到多个独立的 Router 中，提升代码可读性和可维护性。

基本用法::

    from plugin.sdk import PluginRouter, NekoPluginBase, neko_plugin, plugin_entry, ok
    
    # 定义一个独立的 Router
    class DebugRouter(PluginRouter):
        @plugin_entry(id="config_debug")
        async def config_debug(self, **_):
            cfg = await self.config.dump()
            return ok(data={"config": cfg})
    
    # 在主插件中注册 Router
    @neko_plugin
    class MyPlugin(NekoPluginBase):
        def __init__(self, ctx):
            super().__init__(ctx)
            self.include_router(DebugRouter())
            self.include_router(MemoryRouter(), prefix="mem_")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, ClassVar, Dict, List, Optional, TypeVar, Union, Callable

from .events import EventHandler, EventMeta, EVENT_META_ATTR
from .hooks import HookMeta, HookHandler, HOOK_META_ATTR
from .hook_executor import HookExecutorMixin

if TYPE_CHECKING:
    from .base import NekoPluginBase
    from .config import PluginConfig
    from .plugins import Plugins
    from .store import PluginStore
    from .database import PluginDatabase
    from plugin.core.context import PluginContext

# 类型变量
T = TypeVar("T")


class PluginRouterError(RuntimeError):
    """Router 相关错误
    
    常见错误场景:
    - Router 未绑定到插件就访问属性
    - 重复绑定 Router
    - 依赖注入失败
    - 绑定后修改 prefix
    """
    
    @classmethod
    def not_bound(cls, router_name: str, action: str = "access properties") -> "PluginRouterError":
        """创建未绑定错误"""
        return cls(
            f"Router '{router_name}' is not bound to a plugin. "
            f"Cannot {action}. "
            f"Call plugin.include_router(router) first."
        )
    
    @classmethod
    def already_bound(cls, router_name: str, plugin_name: str) -> "PluginRouterError":
        """创建重复绑定错误"""
        return cls(
            f"Router '{router_name}' is already bound to plugin '{plugin_name}'. "
            f"A router can only be bound to one plugin. "
            f"Create a new router instance if you need to use it in another plugin."
        )
    
    @classmethod
    def dependency_missing(cls, router_name: str, dep_name: str) -> "PluginRouterError":
        """创建依赖缺失错误"""
        return cls(
            f"Router '{router_name}' requires dependency '{dep_name}', "
            f"but it's not available in the main plugin. "
            f"Either add '{dep_name}' to the main plugin or provide a default value in the router."
        )
    
    @classmethod
    def prefix_change_after_bound(cls, router_name: str) -> "PluginRouterError":
        """创建绑定后修改 prefix 错误"""
        return cls(
            f"Cannot change prefix of router '{router_name}' after it's bound to a plugin. "
            f"Set the prefix before calling include_router() or pass it as a parameter."
        )


class PluginRouter(HookExecutorMixin):
    """插件路由器基类
    
    允许将插件入口点组织到独立的模块中，类似 FastAPI 的 APIRouter。
    Router 中定义的 @plugin_entry、@lifecycle 等装饰器会被自动收集。
    支持动态加载和卸载。
    
    Attributes:
        prefix: 入口点 ID 前缀，用于命名空间隔离
        tags: 标签列表，用于分类和文档
        name: Router 名称，用于标识和卸载
        
    Properties (绑定后可用):
        ctx: 插件上下文 (PluginContext)
        config: 配置管理器 (PluginConfig)
        plugins: 插件间调用 (Plugins)
        logger: 日志记录器
        file_logger: 文件日志记录器 (如果主插件启用了)
        store: KV 存储 (PluginStore)
        db: 数据库 (PluginDatabase)
    
    Example:
        >>> class MyRouter(PluginRouter):
        ...     @plugin_entry(id="hello")
        ...     def hello(self, name: str = "World", **_):
        ...         self.logger.info(f"Hello {name}")
        ...         return ok(data={"message": f"Hello, {name}!"})
        ...
        >>> # 在主插件中动态加载
        >>> router = MyRouter()
        >>> self.include_router(router)
        >>> # 动态卸载
        >>> self.exclude_router(router)
        >>> # 或通过名称卸载
        >>> self.exclude_router("MyRouter")
    """
    
    def __init__(
        self,
        prefix: str = "",
        tags: Optional[List[str]] = None,
        name: Optional[str] = None,
    ):
        """初始化 Router
        
        Args:
            prefix: 入口点 ID 前缀，会添加到所有入口点 ID 前面
            tags: 标签列表，用于分类
            name: Router 名称，用于标识和卸载（默认使用类名）
        """
        self._prefix: str = prefix
        self._tags: List[str] = tags or []
        self._name: str = name or self.__class__.__name__
        self._plugin: Optional["NekoPluginBase"] = None
        self._bound: bool = False
        self._entry_ids: List[str] = []  # 记录注册的入口点 ID，用于卸载
        # 初始化 Hook 执行器（来自 HookExecutorMixin）
        self.__init_hook_executor__()
    
    @property
    def prefix(self) -> str:
        """获取当前前缀"""
        return self._prefix
    
    @prefix.setter
    def prefix(self, value: str) -> None:
        """设置前缀（只能在绑定前设置）"""
        if self._bound:
            raise PluginRouterError.prefix_change_after_bound(self._name)
        self._prefix = value
    
    @property
    def tags(self) -> List[str]:
        """获取标签列表"""
        return self._tags
    
    @property
    def name(self) -> str:
        """获取 Router 名称"""
        return self._name
    
    @property
    def is_bound(self) -> bool:
        """检查是否已绑定到插件"""
        return self._bound
    
    @property
    def entry_ids(self) -> List[str]:
        """获取已注册的入口点 ID 列表"""
        return self._entry_ids.copy()
    
    def _bind(self, plugin: "NekoPluginBase") -> None:
        """绑定到主插件（内部方法，由 include_router 调用）
        
        Args:
            plugin: 主插件实例
        
        Raises:
            PluginRouterError: 如果 Router 已经绑定到其他插件
        """
        if self._bound:
            plugin_name = self._plugin.__class__.__name__ if self._plugin else "unknown"
            raise PluginRouterError.already_bound(self._name, plugin_name)
        self._plugin = plugin
        self._bound = True
        
        # 注入依赖
        self._inject_dependencies()
    
    def _unbind(self) -> None:
        """解除与主插件的绑定（内部方法，由 exclude_router 调用）"""
        self._plugin = None
        self._bound = False
        self._entry_ids.clear()
    
    def _ensure_bound(self, action: str = "access properties") -> None:
        """确保 Router 已绑定到插件
        
        Args:
            action: 当前尝试执行的操作，用于错误信息
        
        Raises:
            PluginRouterError: 如果 Router 未绑定
        """
        if not self._bound or self._plugin is None:
            raise PluginRouterError.not_bound(self._name, action)
    
    # ========== 代理属性：访问主插件的功能 ==========
    
    @property
    def ctx(self) -> "PluginContext":
        """获取插件上下文"""
        if not self._bound or self._plugin is None:
            return None  # type: ignore[return-value]
        return self._plugin.ctx
    
    @property
    def config(self) -> "PluginConfig":
        """获取配置管理器"""
        if not self._bound or self._plugin is None:
            return None  # type: ignore[return-value]
        return self._plugin.config
    
    @property
    def plugins(self) -> "Plugins":
        """获取插件间调用管理器"""
        if not self._bound or self._plugin is None:
            return None  # type: ignore[return-value]
        return self._plugin.plugins
    
    @property
    def logger(self) -> Any:
        """获取日志记录器
        
        优先返回 file_logger（如果主插件启用了），否则返回 ctx.logger。
        未绑定时返回 None（防止 inspect.getmembers 扫描崩溃）。
        """
        if not self._bound or self._plugin is None:
            return None
        # 优先使用 file_logger
        file_logger = getattr(self._plugin, "file_logger", None)
        if file_logger is not None:
            return file_logger
        return getattr(self._plugin.ctx, "logger", None)
    
    @property
    def file_logger(self) -> Optional[Any]:
        """获取文件日志记录器
        
        Returns:
            文件日志记录器，如果主插件未启用或未绑定则返回 None
        """
        if not self._bound or self._plugin is None:
            return None
        return getattr(self._plugin, "file_logger", None)
    
    @property
    def store(self) -> Optional["PluginStore"]:
        """获取 KV 存储
        
        Returns:
            PluginStore 实例，如果未启用或未绑定则返回 None
        """
        if not self._bound or self._plugin is None:
            return None
        return getattr(self._plugin, "store", None)
    
    @property
    def db(self) -> Optional["PluginDatabase"]:
        """获取数据库
        
        Returns:
            PluginDatabase 实例，如果未启用或未绑定则返回 None
        """
        if not self._bound or self._plugin is None:
            return None
        return getattr(self._plugin, "db", None)
    
    @property
    def plugin_id(self) -> str:
        """获取插件 ID"""
        if not self._bound or self._plugin is None:
            return self._name
        return getattr(self._plugin, "_plugin_id", "unknown")
    
    @property
    def main_plugin(self) -> "NekoPluginBase":
        """获取主插件实例
        
        用于访问主插件的自定义属性和方法。
        
        Example:
            >>> class MyRouter(PluginRouter):
            ...     @plugin_entry(id="test")
            ...     def test(self, **_):
            ...         # 访问主插件的自定义属性
            ...         counter = self.main_plugin.counter
            ...         # 调用主插件的自定义方法
            ...         self.main_plugin.custom_method()
            ...         return ok(data={"counter": counter})
        
        Returns:
            主插件实例 (NekoPluginBase)
        
        Raises:
            PluginRouterError: 如果 Router 未绑定到插件
        """
        if not self._bound or self._plugin is None:
            raise PluginRouterError.not_bound(self._name, "access main_plugin")
        return self._plugin
    
    def get_plugin_attr(self, name: str, default: T = None) -> Union[Any, T]:  # type: ignore[assignment]
        """安全地获取主插件的属性
        
        Args:
            name: 属性名
            default: 默认值（属性不存在时返回）
        
        Returns:
            属性值或默认值
        
        Example:
            >>> counter = self.get_plugin_attr("counter", 0)
            >>> cache = self.get_plugin_attr("_cache")  # 也可以访问私有属性
        """
        self._ensure_bound()
        return getattr(self._plugin, name, default)
    
    def has_plugin_attr(self, name: str) -> bool:
        """检查主插件是否有某个属性
        
        Args:
            name: 属性名
        
        Returns:
            True 如果属性存在
        """
        self._ensure_bound()
        return hasattr(self._plugin, name)
    
    # ========== 依赖注入 ==========
    
    # 子类可以覆盖这个列表，声明需要的依赖
    __requires__: ClassVar[List[str]] = []
    
    def _inject_dependencies(self) -> None:
        """注入依赖（内部方法，由 _bind 调用）
        
        Raises:
            PluginRouterError: 如果必需的依赖不存在
        """
        for dep_name in self.__requires__:
            if self.has_plugin_attr(dep_name):
                setattr(self, dep_name, self.get_plugin_attr(dep_name))
            else:
                # 检查 Router 自身是否有默认值
                if not hasattr(self, dep_name):
                    raise PluginRouterError.dependency_missing(self._name, dep_name)
    
    def get_dependency(self, name: str, default: T = None) -> Union[Any, T]:  # type: ignore[assignment]
        """获取依赖
        
        优先从 Router 自身获取，如果没有则从主插件获取。
        
        Args:
            name: 依赖名称
            default: 默认值
        
        Returns:
            依赖实例或默认值
        
        Example:
            >>> http_client = self.get_dependency("http_client")
            >>> cache = self.get_dependency("cache", default={})
        """
        # 先检查 Router 自身
        if hasattr(self, name) and name not in ("_plugin", "_bound", "_prefix", "_tags", "_name", "_entry_ids"):
            value = getattr(self, name)
            if value is not None:
                return value
        # 再检查主插件
        return self.get_plugin_attr(name, default)
    
    # ========== 便捷方法：代理常用操作 ==========
    
    def push_message(
        self,
        source: str,
        message_type: str,
        description: str = "",
        priority: int = 0,
        content: Optional[str] = None,
        binary_data: Optional[bytes] = None,
        binary_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """推送消息到主进程
        
        Args:
            source: 消息来源标识
            message_type: 消息类型 ("text" | "url" | "binary" | "binary_url")
            description: 消息描述
            priority: 优先级 (0-10)
            content: 文本内容或 URL
            binary_data: 二进制数据
            binary_url: 二进制文件 URL
            metadata: 额外元数据
        """
        self._ensure_bound()
        self.ctx.push_message(
            source=source,
            message_type=message_type,
            description=description,
            priority=priority,
            content=content,
            binary_data=binary_data,
            binary_url=binary_url,
            metadata=metadata,
        )
    
    def report_status(self, status: Dict[str, Any]) -> None:
        """上报插件状态
        
        Args:
            status: 状态字典
        """
        self._ensure_bound()
        plugin = self._plugin
        assert plugin is not None  # for type checker
        if hasattr(plugin, "report_status"):
            plugin.report_status(status)
    
    # ========== HookExecutorMixin 抽象方法实现 ==========
    
    def _get_hook_logger(self) -> Any:
        """获取日志记录器（HookExecutorMixin 抽象方法实现）"""
        return self.logger
    
    def _get_hook_owner_name(self) -> str:
        """获取所属对象名称（HookExecutorMixin 抽象方法实现）"""
        return self.__class__.__name__
    
    # ========== 入口点收集 ==========
    
    def collect_entries(self) -> Dict[str, EventHandler]:
        """收集本 Router 中所有带装饰器的入口点
        
        Returns:
            入口点字典，key 为入口点 ID（已添加前缀），value 为 EventHandler
        """
        entries: Dict[str, EventHandler] = {}
        
        for attr_name in dir(self):
            # 跳过私有属性和特殊属性
            if attr_name.startswith("_"):
                continue
            
            try:
                value = getattr(self, attr_name)
            except Exception:
                continue
            
            if not callable(value):
                continue
            
            # 检查是否有事件元数据
            meta: Optional[EventMeta] = getattr(value, EVENT_META_ATTR, None)
            if meta is None:
                continue
            
            # 添加前缀到入口点 ID
            entry_id = f"{self._prefix}{meta.id}" if self._prefix else meta.id
            
            # 检查重复
            if entry_id in entries:
                if self.logger:
                    self.logger.warning(
                        f"Duplicate entry id '{entry_id}' in router {self.__class__.__name__}"
                    )
            
            # 创建带前缀的新 meta（如果有前缀）
            base_metadata: Dict[str, Any] = dict(meta.metadata) if meta.metadata else {}
            base_metadata["_router"] = self.__class__.__name__
            if self._prefix:
                base_metadata["_original_id"] = meta.id
            
            prefixed_meta = EventMeta(
                event_type=meta.event_type,
                id=entry_id,
                name=meta.name,
                description=meta.description,
                input_schema=meta.input_schema,
                kind=meta.kind,
                auto_start=meta.auto_start,
                metadata=base_metadata,
            )
            
            entries[entry_id] = EventHandler(meta=prefixed_meta, handler=value)
            self._entry_ids.append(entry_id)  # 记录入口点 ID
        
        return entries
    
    # ========== 动态 Entry 管理 ==========
    
    async def add_entry(
        self,
        entry_id: str,
        handler: Callable,
        name: str = "",
        description: str = "",
        input_schema: Optional[Dict[str, Any]] = None,
        kind: str = "action",
        auto_start: bool = False,
    ) -> bool:
        """动态添加一个 entry 到 Router
        
        在运行时向 Router 添加一个新的 entry。如果 Router 已绑定到插件，
        会自动通知主进程更新 entry 列表。
        
        Args:
            entry_id: entry 的唯一标识符（不包含前缀，会自动添加）
            handler: 处理函数（async 或 sync）
            name: 显示名称（默认使用 entry_id）
            description: 描述信息
            input_schema: 输入参数的 JSON Schema
            kind: entry 类型（action/service/hook/custom）
            auto_start: 是否自动启动
        
        Returns:
            True 如果添加成功
        
        Example:
            >>> async def my_handler(self, arg1: str, **_):
            ...     return ok(data={"result": arg1})
            >>> await router.add_entry("my_entry", my_handler, name="My Entry")
        """
        # 添加前缀
        full_entry_id = f"{self._prefix}{entry_id}" if self._prefix else entry_id
        
        # 创建 EventMeta
        meta = EventMeta(
            event_type="plugin_entry",
            id=full_entry_id,
            name=name or entry_id,
            description=description,
            input_schema=input_schema,
            kind=kind,  # type: ignore
            auto_start=auto_start,
            enabled=True,
            dynamic=True,
            metadata={
                "_dynamic": True,
                "_router": self.__class__.__name__,
                "_original_id": entry_id,
                "_registered_at": __import__("time").time(),
            },
        )
        
        # 给 handler 添加元数据属性
        setattr(handler, EVENT_META_ATTR, meta)
        
        # 动态添加到 Router 实例
        attr_name = f"_dynamic_{entry_id}"
        setattr(self, attr_name, handler)
        self._entry_ids.append(full_entry_id)
        
        # 如果已绑定到插件，同步更新插件的 _router_entries
        if self._bound and self._plugin:
            event_handler = EventHandler(meta=meta, handler=handler)
            self._plugin._router_entries[full_entry_id] = event_handler
            
            # 通知主进程
            if hasattr(self._plugin, "_notify_entry_update"):
                await self._plugin._notify_entry_update("register", full_entry_id, meta)
        
        if self.logger:
            self.logger.info(f"Dynamic entry '{full_entry_id}' added to router {self.name}")
        
        return True
    
    async def remove_entry(self, entry_id: str) -> bool:
        """从 Router 移除一个动态 entry
        
        Args:
            entry_id: entry 的唯一标识符（不包含前缀）
        
        Returns:
            True 如果移除成功，False 如果 entry 不存在
        """
        # 添加前缀
        full_entry_id = f"{self._prefix}{entry_id}" if self._prefix else entry_id
        
        # 检查是否存在
        if full_entry_id not in self._entry_ids:
            if self.logger:
                self.logger.warning(f"Entry '{full_entry_id}' not found in router {self.name}")
            return False
        
        # 移除动态属性
        attr_name = f"_dynamic_{entry_id}"
        if hasattr(self, attr_name):
            delattr(self, attr_name)
        
        # 从 entry_ids 中移除
        self._entry_ids.remove(full_entry_id)
        
        # 如果已绑定到插件，同步更新插件的 _router_entries
        if self._bound and self._plugin:
            if full_entry_id in self._plugin._router_entries:
                del self._plugin._router_entries[full_entry_id]
            
            # 通知主进程
            if hasattr(self._plugin, "_notify_entry_update"):
                await self._plugin._notify_entry_update("unregister", full_entry_id, None)
        
        if self.logger:
            self.logger.info(f"Dynamic entry '{full_entry_id}' removed from router {self.name}")
        
        return True
    
    def on_mount(self) -> Union[None, Awaitable[None]]:
        """Router 被挂载时的回调（子类可重写）
        
        在 include_router 成功后调用。
        可用于初始化 Router 特定的资源。
        
        支持同步和异步两种方式::
        
            # 同步
            def on_mount(self):
                self.cache = {}
            
            # 异步
            async def on_mount(self):
                await self.init_database()
        """
        pass
    
    def on_unmount(self) -> Union[None, Awaitable[None]]:
        """Router 被卸载时的回调（子类可重写）
        
        在 exclude_router 成功后调用。
        可用于清理 Router 特定的资源。
        
        支持同步和异步两种方式::
        
            # 同步
            def on_unmount(self):
                self.cache.clear()
            
            # 异步
            async def on_unmount(self):
                await self.close_connections()
        """
        pass
    
    def __repr__(self) -> str:
        bound_status = f"bound to {self._plugin.__class__.__name__}" if self._bound else "unbound"
        return f"<{self.__class__.__name__} prefix='{self._prefix}' {bound_status}>"
