"""
Plugin SDK Protocol interface definitions.

Provides Protocol definitions for type hints and IDE completion.
These Protocols live in the types layer to avoid circular dependencies.
"""
from typing import Protocol, Dict, Any, Optional, Union, Coroutine, runtime_checkable, TYPE_CHECKING
from pathlib import Path

if TYPE_CHECKING:
    from plugin.core.bus.types import BusHubProtocol
    from plugin.sdk.shared.core.types import PushMessageResult


@runtime_checkable
class PluginContextProtocol(Protocol):
    """Plugin-context interface for plugin developers.
    
    This Protocol defines all ctx methods available to plugin developers.
    Using a Protocol rather than a concrete class avoids circular imports while retaining complete type hints.
    
    Attributes:
        plugin_id: Plugin ID.
        config_path: Plugin configuration-file path.
        logger: Logger (plugin.logging_config.PluginLoggerAdapter).
        run_id: Current run ID, if triggered under /runs.
    
    Example:
        >>> from plugin._types import PluginContextProtocol
        >>> 
        >>> class MyPlugin(NekoPluginBase):
        ...     def __init__(self, ctx: PluginContextProtocol):
        ...         super().__init__(ctx)
        ...         # The IDE now completes every ctx method!
    """
    
    # ==================== Basic properties ====================
    plugin_id: str
    config_path: Path
    logger: Any  # plugin.logging_config.PluginLoggerAdapter (stdlib-backed)
    
    @property
    def run_id(self) -> Optional[str]:
        """Current run ID, if triggered under /runs."""
        ...
    
    def require_run_id(self) -> str:
        """Require run_id, raising if absent."""
        ...
    
    # ==================== Configuration management ====================
    async def get_own_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Get plugin configuration including profile overrides.
        
        Args:
            timeout: Timeout in seconds.
        
        Returns:
            Configuration dictionary.
        """
        ...
    
    async def get_own_base_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Get base plugin configuration without profile overrides.
        
        Args:
            timeout: Timeout in seconds.
        
        Returns:
            Base configuration dictionary.
        """
        ...
    
    async def get_own_profiles_state(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Get profiles.toml state: active profile and file mappings.
        
        Args:
            timeout: Timeout in seconds.
        
        Returns:
            Profile-state dictionary.
        """
        ...
    
    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> Dict[str, Any]:
        """Get configuration for a specific profile.
        
        Args:
            profile_name: Profile name.
            timeout: Timeout in seconds.
        
        Returns:
            Profile configuration dictionary.
        """
        ...
    
    async def get_own_effective_config(
        self,
        profile_name: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Get effective configuration.
        
        Args:
            profile_name: Profile name; None uses the active profile.
            timeout: Timeout in seconds.
        
        Returns:
            Effective configuration dictionary.
        """
        ...
    
    async def update_own_config(self, updates: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
        """Update plugin configuration.
        
        Args:
            updates: Configuration dictionary to update.
            timeout: Timeout in seconds.
        
        Returns:
            Updated configuration dictionary.
        """
        ...

    async def replace_own_config(self, config: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
        """Replace this plugin's runtime configuration."""
        ...
    
    async def get_system_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Get system configuration.
        
        Args:
            timeout: Timeout in seconds.
        
        Returns:
            System configuration dictionary.
        """
        ...
    
    # ==================== Inter-plugin communication ====================
    def trigger_plugin_event(
        self,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: Dict[str, Any],
        timeout: float = 10.0
    ) -> Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]:
        """Trigger another plugin's event.
        
        Args:
            target_plugin_id: Target plugin ID
            event_type: Event type.
            event_id: Event ID
            params: Parameter dictionary
            timeout: Timeout in seconds.
        
        Returns:
            Event-handling result.
        """
        ...
    
    def query_plugins(
        self, filters: Optional[Dict[str, Any]] = None, timeout: float = 5.0
    ) -> Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]:
        """Query the plugin list.
        
        Args:
            filters: Filter criteria.
            timeout: Timeout in seconds.
        
        Returns:
            Plugin list.
        """
        ...
    
    # ==================== State updates ====================
    def update_status(self, status: Dict[str, Any]) -> None:
        """Update plugin state.
        
        Args:
            status: State dictionary.
        """
        ...
    
    # ==================== Export functionality ====================
    def export_push_text(
        self,
        *,
        run_id: Optional[str] = None,
        text: str,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]:
        """Push a text export with automatic sync/async selection.
        
        Args:
            run_id: Run ID; None uses the current run_id.
            text: Text content.
            description: Description.
            metadata: Metadata.
            timeout: Timeout in seconds.
        
        Returns:
            Return a coroutine in the event loop; otherwise return a result dictionary
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
        """Push a text export asynchronously."""
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
        """Push a text export synchronously."""
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
        """Push a binary-data export with automatic sync/async selection."""
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
        """Push a binary-data export asynchronously."""
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
        """Push a binary-data export synchronously."""
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
        """Push a binary-URL export with automatic sync/async selection."""
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
        """Push a binary-URL export asynchronously."""
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
        """Push a binary-URL export synchronously."""
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
        """Push a URL export with automatic sync/async selection."""
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
        """Push a URL export asynchronously."""
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
        """Push a URL export synchronously."""
        ...
    
    # ==================== Run progress updates ====================
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
        """Update run state with automatic sync/async selection."""
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
        """Update run state asynchronously."""
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
        """Update run state synchronously."""
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
        """Update run progress with automatic sync/async selection."""
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
        """Update run progress asynchronously."""
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
        """Update run progress synchronously."""
        ...
    
    # ==================== Message pushes ====================
    def push_message(
        self,
        *,
        # Preferred v2 schema: see plugin/sdk/shared/core/push_message_schema.py.
        visibility: Optional[list] = None,
        ai_behavior: Optional[str] = None,
        parts: Optional[list] = None,
        # General:
        source: str = "",
        target_lanlan: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        priority: int = 0,
        coalesce_key: Optional[str] = None,
        # v1 legacy (deprecated, removed in v0.9)：
        message_type: Optional[str] = None,
        description: Optional[str] = None,
        content: Optional[str] = None,
        binary_data: Optional[bytes] = None,
        binary_url: Optional[str] = None,
        mime: Optional[str] = None,
        unsafe: bool = False,
        fast_mode: bool = False,
        delivery: Any = None,
        reply: Optional[bool] = None,
    ) -> "PushMessageResult":
        """Push a message to the main process and return the local transport submission result.

        See push_message_schema for recommended v2 visibility/ai_behavior/parts;
        remaining parameters are deprecated v1, translated automatically by the host with DeprecationWarning.
        """
        ...
    
    # ==================== Memory queries ====================
    def query_memory(self, lanlan_name: str, query: str, timeout: float = 5.0) -> Union[Coroutine[Any, Any, Dict[str, Any]], Dict[str, Any]]:
        """Query memory data with automatic execution-environment detection.
        
        Args:
            lanlan_name: Lanlan name.
            query: Query string.
            timeout: Timeout in seconds.
        
        Returns:
            Return a coroutine inside an event loop, otherwise a result dictionary.
        """
        ...
    
    # ==================== Bus Hub ====================
    @property
    def bus(self) -> "BusHubProtocol":
        """Bus Hub providing memory/messages/events/lifecycle/conversations/frames clients.
        
        Example:
            # Get messages.
            messages = ctx.bus.messages.get(max_count=10)
            
            # Get events.
            events = ctx.bus.events.get(plugin_id="my_plugin")
            
            # Get lifecycle events.
            lifecycle = ctx.bus.lifecycle.get()
            
            # Get memory data.
            memory = ctx.bus.memory.get(bucket_id="default")
            
            # Get conversation context.
            conversations = ctx.bus.conversations.get_by_id(conversation_id)

            # Get recent host-supplied model frames, lossy rather than a log.
            frames = ctx.bus.frames.get(max_count=4)
        """
        ...


__all__ = [
    "PluginContextProtocol",
]
