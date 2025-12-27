"""
插件上下文模块

提供插件运行时上下文，包括状态更新和消息推送功能。
"""
import contextlib
import contextvars
import inspect
import time
import tomllib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty
from typing import TYPE_CHECKING, Any, Dict, Optional

from fastapi import FastAPI

from plugin.api.exceptions import PluginEntryNotFoundError, PluginError
from plugin.core.state import state
from plugin.settings import EVENT_META_ATTR

if TYPE_CHECKING:
    from plugin.sdk.bus.events import EventClient
    from plugin.sdk.bus.lifecycle import LifecycleClient
    from plugin.sdk.bus.memory import MemoryClient
    from plugin.sdk.bus.messages import MessageClient


_IN_HANDLER: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("plugin_in_handler", default=None)


class _BusHub:
    def __init__(self, ctx: "PluginContext"):
        self._ctx = ctx
        self._memory: Optional["MemoryClient"] = None
        self._messages: Optional["MessageClient"] = None
        self._events: Optional["EventClient"] = None
        self._lifecycle: Optional["LifecycleClient"] = None

    @property
    def memory(self) -> "MemoryClient":
        if self._memory is None:
            from plugin.sdk.bus.memory import MemoryClient

            self._memory = MemoryClient(self._ctx)
        return self._memory

    @property
    def messages(self) -> "MessageClient":
        if self._messages is None:
            from plugin.sdk.bus.messages import MessageClient

            self._messages = MessageClient(self._ctx)
        return self._messages

    @property
    def events(self) -> "EventClient":
        if self._events is None:
            from plugin.sdk.bus.events import EventClient

            self._events = EventClient(self._ctx)
        return self._events

    @property
    def lifecycle(self) -> "LifecycleClient":
        if self._lifecycle is None:
            from plugin.sdk.bus.lifecycle import LifecycleClient

            self._lifecycle = LifecycleClient(self._ctx)
        return self._lifecycle


@dataclass
class PluginContext:
    """插件运行时上下文"""
    plugin_id: str
    config_path: Path
    logger: Any  # loguru.Logger
    status_queue: Any
    message_queue: Any = None  # 消息推送队列
    app: Optional[FastAPI] = None
    _plugin_comm_queue: Optional[Any] = None  # 插件间通信队列（主进程提供）
    _cmd_queue: Optional[Any] = None  # 命令队列（用于在等待期间处理命令）
    _res_queue: Optional[Any] = None  # 结果队列（用于在等待期间处理响应）
    _entry_map: Optional[Dict[str, Any]] = None  # 入口映射（用于处理命令）
    _instance: Optional[Any] = None  # 插件实例（用于处理命令）

    @property
    def bus(self) -> _BusHub:
        hub = getattr(self, "_bus_hub", None)
        if hub is None:
            hub = _BusHub(self)
            object.__setattr__(self, "_bus_hub", hub)
        return hub

    def get_user_context(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> Dict[str, Any]:
        raise RuntimeError(
            "PluginContext.get_user_context() is no longer supported. "
            "Use ctx.bus.memory.get(bucket_id=..., limit=..., timeout=...) instead."
        )

    def _get_sync_call_in_handler_policy(self) -> str:
        try:
            st = self.config_path.stat()
            cache_mtime = getattr(self, "_a1_policy_mtime", None)
            cache_value = getattr(self, "_a1_policy_value", None)
            if cache_mtime == st.st_mtime and isinstance(cache_value, str):
                return cache_value

            with self.config_path.open("rb") as f:
                conf = tomllib.load(f)
            policy = (
                conf.get("plugin", {})
                .get("safety", {})
                .get("sync_call_in_handler", "warn")
            )
            if policy not in ("warn", "reject"):
                policy = "warn"
            setattr(self, "_a1_policy_mtime", st.st_mtime)
            setattr(self, "_a1_policy_value", policy)
            return policy
        except Exception:
            return "warn"

    def _enforce_sync_call_policy(self, method_name: str) -> None:
        handler_ctx = _IN_HANDLER.get()
        if handler_ctx is None:
            return
        policy = self._get_sync_call_in_handler_policy()
        msg = (
            f"Sync call '{method_name}' invoked inside handler ({handler_ctx}). "
            "This may block the command loop and cause deadlocks/timeouts."
        )
        if policy == "reject":
            raise RuntimeError(msg)
        self.logger.warning(msg)

    @contextlib.contextmanager
    def _handler_scope(self, handler_ctx: str):
        token = _IN_HANDLER.set(handler_ctx)
        try:
            yield
        finally:
            _IN_HANDLER.reset(token)

    def update_status(self, status: Dict[str, Any]) -> None:
        """
        子进程 / 插件内部调用：把原始 status 丢到主进程的队列里，由主进程统一整理。
        """
        try:
            payload = {
                "type": "STATUS_UPDATE",
                "plugin_id": self.plugin_id,
                "data": status,
                "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            self.status_queue.put_nowait(payload)
            # 这条日志爱要不要
            self.logger.info(f"Plugin {self.plugin_id} status updated: {payload}")
        except (AttributeError, RuntimeError) as e:
            # 队列操作错误
            self.logger.warning(f"Queue error updating status for plugin {self.plugin_id}: {e}")
        except Exception as e:
            # 其他未知异常
            self.logger.exception(f"Unexpected error updating status for plugin {self.plugin_id}: {e}")

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
        """
        子进程 / 插件内部调用：推送消息到主进程的消息队列。
        
        Args:
            source: 插件自己标明的来源
            message_type: 消息类型，可选值: "text", "url", "binary", "binary_url"
            description: 插件自己标明的描述
            priority: 插件自己设定的优先级，数字越大优先级越高
            content: 文本内容或URL（当message_type为text或url时）
            binary_data: 二进制数据（当message_type为binary时，仅用于小文件）
            binary_url: 二进制文件的URL（当message_type为binary_url时）
            metadata: 额外的元数据
        """
        if self.message_queue is None:
            self.logger.warning(f"Plugin {self.plugin_id} message_queue is not available, message dropped")
            return
        
        try:
            payload = {
                "type": "MESSAGE_PUSH",
                "plugin_id": self.plugin_id,
                "source": source,
                "description": description,
                "priority": priority,
                "message_type": message_type,
                "content": content,
                "binary_data": binary_data,
                "binary_url": binary_url,
                "metadata": metadata or {},
                "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            self.message_queue.put_nowait(payload)
            self.logger.debug(f"Plugin {self.plugin_id} pushed message: {source} - {description}")
        except (AttributeError, RuntimeError) as e:
            # 队列操作错误
            self.logger.warning(f"Queue error pushing message for plugin {self.plugin_id}: {e}")
        except Exception as e:
            # 其他未知异常
            self.logger.exception(f"Unexpected error pushing message for plugin {self.plugin_id}: {e}")

    def _send_request_and_wait(
        self,
        *,
        method_name: str,
        request_type: str,
        request_data: Dict[str, Any],
        timeout: float,
        wrap_result: bool = True,
        send_log_template: Optional[str] = None,
        error_log_template: Optional[str] = None,
        warn_on_orphan_response: bool = False,
        orphan_warning_template: Optional[str] = None,
    ) -> Any:
        self._enforce_sync_call_policy(method_name)
        if self._plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {self.plugin_id}. "
                "This method can only be called from within a plugin process."
            )

        request_id = str(uuid.uuid4())
        request: Dict[str, Any] = {
            "type": request_type,
            "from_plugin": self.plugin_id,
            "request_id": request_id,
            "timeout": timeout,
            **(request_data or {}),
        }

        try:
            self._plugin_comm_queue.put(request, timeout=timeout)
            if send_log_template:
                self.logger.debug(
                    send_log_template.format(
                        request_id=request_id,
                        from_plugin=self.plugin_id,
                        **(request_data or {}),
                    )
                )
        except Exception as e:
            if error_log_template:
                self.logger.error(error_log_template.format(error=e))
            raise RuntimeError(f"Failed to send {request_type} request: {e}") from e

        start_time = time.time()
        check_interval = 0.01
        while time.time() - start_time < timeout:
            response = state.get_plugin_response(request_id)
            if response is None:
                time.sleep(check_interval)
                continue
            if not isinstance(response, dict):
                time.sleep(check_interval)
                continue

            if response.get("error"):
                raise RuntimeError(str(response.get("error")))

            result = response.get("result")
            if wrap_result:
                return result if isinstance(result, dict) else {"result": result}
            return result

        orphan_response = None
        try:
            orphan_response = state.get_plugin_response(request_id)
        except Exception:
            orphan_response = None
        if warn_on_orphan_response and orphan_response is not None:
            if orphan_warning_template:
                self.logger.warning(
                    orphan_warning_template.format(
                        request_id=request_id,
                        from_plugin=self.plugin_id,
                        **(request_data or {}),
                    )
                )
        raise TimeoutError(f"{request_type} timed out after {timeout}s")
    
    def trigger_plugin_event(
        self,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        args: Dict[str, Any],
        timeout: float = 10.0  # 增加超时时间以应对命令循环可能的延迟
    ) -> Dict[str, Any]:
        """
        触发其他插件的自定义事件（插件间通信）
        
        这是插件间功能复用的机制，使用 Queue 而不是 HTTP。
        处理流程和 plugin_entry 一样，在单线程的命令循环中执行。
        
        Args:
            target_plugin_id: 目标插件ID
            event_type: 自定义事件类型
            event_id: 事件ID
            args: 参数字典
            timeout: 超时时间（秒）
            
        Returns:
            事件处理器的返回结果
            
        Raises:
            RuntimeError: 如果通信队列不可用
            TimeoutError: 如果超时
            Exception: 如果事件执行失败
        """
        try:
            return self._send_request_and_wait(
                method_name="trigger_plugin_event",
                request_type="PLUGIN_TO_PLUGIN",
                request_data={
                    "to_plugin": target_plugin_id,
                    "event_type": event_type,
                    "event_id": event_id,
                    "args": args,
                },
                timeout=timeout,
                wrap_result=False,
                send_log_template=(
                    "[PluginContext] Sent plugin communication request: {from_plugin} -> {to_plugin}, "
                    "event={event_type}.{event_id}, req_id={request_id}"
                ),
                error_log_template="Failed to send plugin communication request: {error}",
                warn_on_orphan_response=True,
                orphan_warning_template=(
                    "[PluginContext] Timeout reached, but response was found (likely delayed). "
                    "Cleaned up orphan response for req_id={request_id}"
                ),
            )
        except TimeoutError as e:
            raise TimeoutError(
                f"Plugin {target_plugin_id} event {event_type}.{event_id} timed out after {timeout}s"
            ) from e

    def query_plugins(self, filters: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            return self._send_request_and_wait(
                method_name="query_plugins",
                request_type="PLUGIN_QUERY",
                request_data={"filters": filters or {}},
                timeout=timeout,
                wrap_result=True,
                send_log_template="[PluginContext] Sent plugin query request: from={from_plugin}, req_id={request_id}",
                error_log_template="Failed to send plugin query request: {error}",
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin query timed out after {timeout}s") from e

    def get_own_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            return self._send_request_and_wait(
                method_name="get_own_config",
                request_type="PLUGIN_CONFIG_GET",
                request_data={"plugin_id": self.plugin_id},
                timeout=timeout,
                wrap_result=True,
                error_log_template=None,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin config get timed out after {timeout}s") from e

    def get_system_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            return self._send_request_and_wait(
                method_name="get_system_config",
                request_type="PLUGIN_SYSTEM_CONFIG_GET",
                request_data={},
                timeout=timeout,
                wrap_result=True,
                error_log_template=None,
            )
        except TimeoutError as e:
            raise TimeoutError(f"System config get timed out after {timeout}s") from e

    def query_memory(self, lanlan_name: str, query: str, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            return self._send_request_and_wait(
                method_name="query_memory",
                request_type="MEMORY_QUERY",
                request_data={
                    "lanlan_name": lanlan_name,
                    "query": query,
                },
                timeout=timeout,
                wrap_result=True,
                error_log_template=None,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Memory query timed out after {timeout}s") from e

    def update_own_config(self, updates: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
        if not isinstance(updates, dict):
            raise TypeError("updates must be a dict")
        try:
            return self._send_request_and_wait(
                method_name="update_own_config",
                request_type="PLUGIN_CONFIG_UPDATE",
                request_data={
                    "plugin_id": self.plugin_id,
                    "updates": updates,
                },
                timeout=timeout,
                wrap_result=True,
                error_log_template=None,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin config update timed out after {timeout}s") from e

