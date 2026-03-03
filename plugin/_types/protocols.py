"""
插件SDK Protocol接口定义模块

提供Protocol接口定义,用于类型提示和IDE智能补全。
这些Protocol定义在types/层，避免循环依赖。
"""
from typing import Protocol, Dict, Any, Optional, Union, Coroutine, runtime_checkable, TYPE_CHECKING
from pathlib import Path

if TYPE_CHECKING:
    from plugin.sdk.bus.types import BusHubProtocol


@runtime_checkable
class PluginContextProtocol(Protocol):
    """插件上下文接口定义 (面向插件开发者)
    
    这是一个Protocol类型,定义了插件开发者可以使用的所有ctx方法。
    使用Protocol而不是具体类可以避免循环导入,同时提供完整的类型提示。
    
    Attributes:
        plugin_id: 插件ID
        config_path: 插件配置文件路径
        logger: 日志记录器(loguru.Logger)
        run_id: 当前运行ID(如果在/runs触发的上下文中)
    
    Example:
        >>> from plugin._types import PluginContextProtocol
        >>> 
        >>> class MyPlugin(NekoPluginBase):
        ...     def __init__(self, ctx: PluginContextProtocol):
        ...         super().__init__(ctx)
        ...         # IDE现在可以提示所有ctx方法!
    """
    
    # ==================== 基础属性 ====================
    plugin_id: str
    config_path: Path
    logger: Any  # loguru.Logger
    
    @property
    def run_id(self) -> Optional[str]:
        """当前运行ID(如果在/runs触发的上下文中)"""
        ...
    
    def require_run_id(self) -> str:
        """要求必须有run_id,否则抛出异常"""
        ...
    
    # ==================== 配置管理 ====================
    async def get_own_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        """获取插件配置(包含profile覆盖)
        
        Args:
            timeout: 超时时间(秒)
        
        Returns:
            配置字典
        """
        ...
    
    async def get_own_base_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        """获取插件基础配置(不含profile覆盖)
        
        Args:
            timeout: 超时时间(秒)
        
        Returns:
            基础配置字典
        """
        ...
    
    async def get_own_profiles_state(self, timeout: float = 5.0) -> Dict[str, Any]:
        """获取profiles.toml状态(激活的profile和文件映射)
        
        Args:
            timeout: 超时时间(秒)
        
        Returns:
            profiles状态字典
        """
        ...
    
    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> Dict[str, Any]:
        """获取指定profile的配置
        
        Args:
            profile_name: profile名称
            timeout: 超时时间(秒)
        
        Returns:
            profile配置字典
        """
        ...
    
    async def get_own_effective_config(
        self,
        profile_name: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """获取有效配置
        
        Args:
            profile_name: profile名称(None表示使用当前激活的profile)
            timeout: 超时时间(秒)
        
        Returns:
            有效配置字典
        """
        ...
    
    async def update_own_config(self, updates: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
        """更新插件配置
        
        Args:
            updates: 要更新的配置字典
            timeout: 超时时间(秒)
        
        Returns:
            更新后的配置字典
        """
        ...
    
    async def get_system_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        """获取系统配置
        
        Args:
            timeout: 超时时间(秒)
        
        Returns:
            系统配置字典
        """
        ...
    
    # ==================== 插件间通信 ====================
    def trigger_plugin_event(
        self,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: Dict[str, Any],
        timeout: float = 10.0
    ) -> Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]:
        """触发其他插件的事件
        
        Args:
            target_plugin_id: 目标插件ID
            event_type: 事件类型
            event_id: 事件ID
            params: 参数字典
            timeout: 超时时间(秒)
        
        Returns:
            事件处理结果
        """
        ...
    
    def query_plugins(
        self, filters: Optional[Dict[str, Any]] = None, timeout: float = 5.0
    ) -> Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]:
        """查询插件列表
        
        Args:
            filters: 过滤条件
            timeout: 超时时间(秒)
        
        Returns:
            插件列表
        """
        ...
    
    # ==================== 状态更新 ====================
    def update_status(self, status: Dict[str, Any]) -> None:
        """更新插件状态
        
        Args:
            status: 状态字典
        """
        ...
    
    # ==================== Export功能 ====================
    def export_push_text(
        self,
        *,
        run_id: Optional[str] = None,
        text: str,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]:
        """推送文本导出(智能同步/异步)
        
        Args:
            run_id: 运行ID(None表示使用当前run_id)
            text: 文本内容
            description: 描述
            metadata: 元数据
            timeout: 超时时间(秒)
        
        Returns:
            在事件循环中返回协程,否则返回结果字典
        """
        ...
    
    async def export_push_text_async(
        self,
        *,
        run_id: Optional[str] = None,
        text: str,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """推送文本导出(异步)"""
        ...
    
    def export_push_text_sync(
        self,
        *,
        run_id: Optional[str] = None,
        text: str,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """推送文本导出(同步)"""
        ...
    
    def export_push_binary(
        self,
        *,
        run_id: Optional[str] = None,
        binary_data: bytes,
        mime: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]:
        """推送二进制数据导出(智能同步/异步)"""
        ...
    
    async def export_push_binary_async(
        self,
        *,
        run_id: Optional[str] = None,
        binary_data: bytes,
        mime: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """推送二进制数据导出(异步)"""
        ...
    
    def export_push_binary_sync(
        self,
        *,
        run_id: Optional[str] = None,
        binary_data: bytes,
        mime: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """推送二进制数据导出(同步)"""
        ...
    
    def export_push_binary_url(
        self,
        *,
        run_id: Optional[str] = None,
        binary_url: str,
        mime: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]:
        """推送二进制URL导出(智能同步/异步)"""
        ...
    
    async def export_push_binary_url_async(
        self,
        *,
        run_id: Optional[str] = None,
        binary_url: str,
        mime: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """推送二进制URL导出(异步)"""
        ...
    
    def export_push_binary_url_sync(
        self,
        *,
        run_id: Optional[str] = None,
        binary_url: str,
        mime: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """推送二进制URL导出(同步)"""
        ...
    
    def export_push_url(
        self,
        *,
        run_id: Optional[str] = None,
        url: str,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]:
        """推送URL导出(智能同步/异步)"""
        ...
    
    async def export_push_url_async(
        self,
        *,
        run_id: Optional[str] = None,
        url: str,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """推送URL导出(异步)"""
        ...
    
    def export_push_url_sync(
        self,
        *,
        run_id: Optional[str] = None,
        url: str,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """推送URL导出(同步)"""
        ...
    
    # ==================== Run进度更新 ====================
    def run_update(
        self,
        *,
        run_id: Optional[str] = None,
        progress: Optional[float] = None,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        step: Optional[int] = None,
        step_total: Optional[int] = None,
        eta_seconds: Optional[float] = None,
        metrics: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]:
        """更新运行状态(智能同步/异步)"""
        ...
    
    async def run_update_async(
        self,
        *,
        run_id: Optional[str] = None,
        progress: Optional[float] = None,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        step: Optional[int] = None,
        step_total: Optional[int] = None,
        eta_seconds: Optional[float] = None,
        metrics: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """更新运行状态(异步)"""
        ...
    
    def run_update_sync(
        self,
        *,
        run_id: Optional[str] = None,
        progress: Optional[float] = None,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        step: Optional[int] = None,
        step_total: Optional[int] = None,
        eta_seconds: Optional[float] = None,
        metrics: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """更新运行状态(同步)"""
        ...
    
    def run_progress(
        self,
        *,
        run_id: Optional[str] = None,
        progress: float = 0.0,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]:
        """更新运行进度(智能同步/异步)"""
        ...
    
    async def run_progress_async(
        self,
        *,
        run_id: Optional[str] = None,
        progress: float = 0.0,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """更新运行进度(异步)"""
        ...
    
    def run_progress_sync(
        self,
        *,
        run_id: Optional[str] = None,
        progress: float = 0.0,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """更新运行进度(同步)"""
        ...
    
    # ==================== 消息推送 ====================
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
        unsafe: bool = False,
        fast_mode: bool = False,
    ) -> None:
        """推送消息到主进程
        
        Args:
            source: 消息来源
            message_type: 消息类型("text", "url", "binary", "binary_url")
            description: 描述
            priority: 优先级(数字越大优先级越高)
            content: 文本内容或URL
            binary_data: 二进制数据
            binary_url: 二进制文件URL
            metadata: 元数据
            unsafe: 是否跳过严格schema校验
            fast_mode: 是否使用快速模式(批量推送)
        """
        ...
    
    # ==================== 内存查询 ====================
    def query_memory(self, lanlan_name: str, query: str, timeout: float = 5.0) -> Union[Coroutine[Any, Any, Dict[str, Any]], Dict[str, Any]]:
        """查询内存数据（智能版本：自动检测执行环境）
        
        Args:
            lanlan_name: lanlan名称
            query: 查询字符串
            timeout: 超时时间(秒)
        
        Returns:
            在事件循环中返回协程，否则返回结果字典
        """
        ...
    
    # ==================== Bus Hub ====================
    @property
    def bus(self) -> "BusHubProtocol":
        """总线Hub，提供 memory/messages/events/lifecycle/conversations 客户端
        
        Example:
            # 获取消息
            messages = ctx.bus.messages.get(max_count=10)
            
            # 获取事件
            events = ctx.bus.events.get(plugin_id="my_plugin")
            
            # 获取生命周期事件
            lifecycle = ctx.bus.lifecycle.get()
            
            # 获取内存数据
            memory = ctx.bus.memory.get(bucket_id="default")
            
            # 获取对话上下文
            conversations = ctx.bus.conversations.get_by_id(conversation_id)
        """
        ...


__all__ = [
    "PluginContextProtocol",
]
