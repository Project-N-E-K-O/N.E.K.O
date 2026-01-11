"""
插件上下文模块

提供插件运行时上下文，包括状态更新和消息推送功能。
"""
import contextlib
import contextvars
import asyncio
import inspect
import time
import tomllib
import uuid
import threading
import functools
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty
from typing import TYPE_CHECKING, Any, Dict, Optional

from fastapi import FastAPI

from plugin.api.exceptions import PluginEntryNotFoundError, PluginError
from plugin.core.state import state
from plugin.settings import (
    EVENT_META_ATTR,
    PLUGIN_LOG_CTX_MESSAGE_PUSH,
    PLUGIN_LOG_CTX_STATUS_UPDATE,
    PLUGIN_LOG_SYNC_CALL_WARNINGS,
    SYNC_CALL_IN_HANDLER_POLICY,
)

if TYPE_CHECKING:
    from plugin.sdk.bus.events import EventClient
    from plugin.sdk.bus.lifecycle import LifecycleClient
    from plugin.sdk.bus.memory import MemoryClient
    from plugin.sdk.bus.messages import MessageClient


_IN_HANDLER: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("plugin_in_handler", default=None)


class _BusHub:
    def __init__(self, ctx: "PluginContext"):
        self._ctx = ctx

    @functools.cached_property
    def memory(self) -> "MemoryClient":
        from plugin.sdk.bus.memory import MemoryClient

        return MemoryClient(self._ctx)

    @functools.cached_property
    def messages(self) -> "MessageClient":
        from plugin.sdk.bus.messages import MessageClient

        return MessageClient(self._ctx)

    @functools.cached_property
    def events(self) -> "EventClient":
        from plugin.sdk.bus.events import EventClient

        return EventClient(self._ctx)

    @functools.cached_property
    def lifecycle(self) -> "LifecycleClient":
        from plugin.sdk.bus.lifecycle import LifecycleClient

        return LifecycleClient(self._ctx)


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
    _zmq_ipc_client: Optional[Any] = None
    _cmd_queue: Optional[Any] = None  # 命令队列（用于在等待期间处理命令）
    _res_queue: Optional[Any] = None  # 结果队列（用于在等待期间处理响应）
    _response_queue: Optional[Any] = None
    _response_pending: Optional[Dict[str, Any]] = None
    _entry_map: Optional[Dict[str, Any]] = None  # 入口映射（用于处理命令）
    _instance: Optional[Any] = None  # 插件实例（用于处理命令）
    _push_seq: int = 0
    _push_lock: Optional[Any] = None
    _push_batcher: Optional[Any] = None

    @functools.cached_property
    def bus(self) -> _BusHub:
        return _BusHub(self)

    def close(self) -> None:
        """Release per-context resources such as the ZeroMQ push batcher.

        This is safe to call multiple times.
        """
        batcher = getattr(self, "_push_batcher", None)
        if batcher is not None:
            try:
                # Give the batcher a bounded window to flush and stop.
                batcher.stop(timeout=2.0)
            except Exception:
                # Cleanup should be best-effort and never raise.
                pass
            try:
                self._push_batcher = None
            except Exception:
                pass

    def __del__(self) -> None:  # pragma: no cover - best-effort safety net
        try:
            self.close()
        except Exception:
            pass

    def get_user_context(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> Dict[str, Any]:
        raise RuntimeError(
            "PluginContext.get_user_context() is no longer supported. "
            "Use ctx.bus.memory.get(bucket_id=..., limit=..., timeout=...) instead."
        )

    def _get_sync_call_in_handler_policy(self) -> str:
        """获取同步调用策略，优先使用插件自身配置，其次使用全局配置。

        有效值："warn" / "reject"。任何非法值都会回退到全局策略。
        """
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
                .get("sync_call_in_handler")
            )
            if policy not in ("warn", "reject"):
                policy = SYNC_CALL_IN_HANDLER_POLICY
            setattr(self, "_a1_policy_mtime", st.st_mtime)
            setattr(self, "_a1_policy_value", policy)
            return policy
        except Exception:
            return SYNC_CALL_IN_HANDLER_POLICY

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
        if PLUGIN_LOG_SYNC_CALL_WARNINGS:
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
            if PLUGIN_LOG_CTX_STATUS_UPDATE:
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
        fast_mode: bool = False,
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
        zmq_client = getattr(self, "_zmq_ipc_client", None)
        if zmq_client is None and bool(fast_mode):
            # ZeroMQ IPC 被显式关闭时，queue 是默认路径，这里仅以 debug 级别提示 fast_mode 已被忽略
            try:
                self.logger.debug(
                    "[PluginContext] fast_mode requested but ZeroMQ IPC is disabled; using queue-based push_message",
                )
            except Exception:
                pass

        if zmq_client is not None:
            try:
                from plugin.settings import (
                    PLUGIN_ZMQ_MESSAGE_PUSH_SYNC_TIMEOUT,
                    PLUGIN_ZMQ_MESSAGE_PUSH_ENDPOINT,
                    PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE,
                    PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS,
                )
            except Exception as e:
                # Fallback to safe defaults if settings import fails, but keep a clue in logs.
                try:
                    self.logger.warning(
                        "[PluginContext] Failed to import ZeroMQ push settings ({})",
                        e,
                    )
                except Exception:
                    pass
                PLUGIN_ZMQ_MESSAGE_PUSH_SYNC_TIMEOUT = 3600.0
                PLUGIN_ZMQ_MESSAGE_PUSH_ENDPOINT = "tcp://127.0.0.1:38766"
                PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE = 256
                PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS = 5

            # Canonical initialization of the per-context push lock.
            lock = getattr(self, "_push_lock", None)
            if lock is None:
                new_lock = threading.Lock()
                try:
                    object.__setattr__(self, "_push_lock", new_lock)
                    lock = new_lock
                except (AttributeError, TypeError):
                    # Fallback for non-dataclass or unusual attribute models.
                    self._push_lock = new_lock
                    lock = new_lock

            if bool(fast_mode):
                if getattr(self, "_push_batcher", None) is None:
                    from plugin.zeromq_ipc import ZmqMessagePushBatcher

                    batcher = ZmqMessagePushBatcher(
                        plugin_id=self.plugin_id,
                        endpoint=str(PLUGIN_ZMQ_MESSAGE_PUSH_ENDPOINT),
                        batch_size=int(PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE),
                        flush_interval_ms=int(PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS),
                    )
                    batcher.start()
                    try:
                        object.__setattr__(self, "_push_batcher", batcher)
                    except Exception:
                        self._push_batcher = batcher

                batcher = getattr(self, "_push_batcher", None)
                if batcher is None:
                    raise RuntimeError("push batcher not initialized")

                # IMPORTANT: seq allocation and enqueue must be atomic under the same lock.
                # Otherwise, concurrent threads can enqueue out-of-order relative to seq.
                with lock:
                    self._push_seq = int(getattr(self, "_push_seq", 0)) + 1
                    seq = int(self._push_seq)
                    item = {
                        "seq": seq,
                        "source": source,
                        "message_type": message_type,
                        "description": description,
                        "priority": priority,
                        "content": content,
                        "binary_data": binary_data,
                        "binary_url": binary_url,
                        "metadata": metadata or {},
                    }
                    batcher.enqueue(item)
                return

            timeout_s = float(PLUGIN_ZMQ_MESSAGE_PUSH_SYNC_TIMEOUT)
            if timeout_s <= 0:
                timeout_s = 3600.0

            attempt_timeout = float(timeout_s)
            if attempt_timeout > 1.0:
                attempt_timeout = 1.0
            if attempt_timeout <= 0:
                attempt_timeout = 0.2

            with lock:
                self._push_seq = int(getattr(self, "_push_seq", 0)) + 1
                seq = int(self._push_seq)

                start_ts = time.time()
                deadline = start_ts + timeout_s
                attempt = 0
                last_exc: Optional[BaseException] = None

                while True:
                    now = time.time()
                    if now >= deadline:
                        # Bounded by total elapsed time derived from sync timeout.
                        msg = (
                            f"ZeroMQ MESSAGE_PUSH failed after {attempt} attempts "
                            f"over ~{timeout_s:.2f}s; last_error={last_exc!r}"
                        )
                        raise RuntimeError(msg)

                    attempt += 1
                    req_id = str(uuid.uuid4())
                    req = {
                        "type": "MESSAGE_PUSH",
                        "from_plugin": self.plugin_id,
                        "request_id": req_id,
                        "timeout": timeout_s,
                        "seq": seq,
                        "source": source,
                        "message_type": message_type,
                        "description": description,
                        "priority": priority,
                        "content": content,
                        "binary_data": binary_data,
                        "binary_url": binary_url,
                        "metadata": metadata or {},
                    }
                    try:
                        resp = zmq_client.request(req, timeout=attempt_timeout)
                        last_exc = None
                    except Exception as e:  # noqa: BLE001 - we want to capture and report any IPC failure here
                        resp = None
                        last_exc = e

                    if not isinstance(resp, dict):
                        # Transport-level failure or timeout; apply bounded exponential backoff.
                        try:
                            self.logger.warning(
                                "[PluginContext] ZeroMQ IPC failed for MESSAGE_PUSH; "
                                "retrying attempt {}, last_error={!r}",
                                attempt,
                                last_exc,
                            )
                        except Exception:
                            pass

                        # Exponential backoff with cap to avoid hot looping when router is down.
                        backoff_base = 0.05
                        backoff_cap = 1.0
                        sleep_s = backoff_base * (2 ** (attempt - 1))
                        if sleep_s > backoff_cap:
                            sleep_s = backoff_cap

                        remaining = deadline - time.time()
                        if remaining <= 0:
                            msg = (
                                f"ZeroMQ MESSAGE_PUSH failed after {attempt} attempts "
                                f"over ~{timeout_s:.2f}s; last_error={last_exc!r}"
                            )
                            raise RuntimeError(msg)

                        time.sleep(min(sleep_s, max(0.0, remaining)))
                        continue

                    if resp.get("error"):
                        raise RuntimeError(str(resp.get("error")))
                    return

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
            if PLUGIN_LOG_CTX_MESSAGE_PUSH:
                self.logger.debug(f"Plugin {self.plugin_id} pushed message: {source} - {description}")
        except (AttributeError, RuntimeError) as e:
            # 队列操作错误
            self.logger.warning(f"Queue error pushing message for plugin {self.plugin_id}: {e}")
        except Exception:
            # 其他未知异常
            self.logger.exception(f"Unexpected error pushing message for plugin {self.plugin_id}")

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
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self._send_request_and_wait_async(
                    method_name=method_name,
                    request_type=request_type,
                    request_data=request_data,
                    timeout=timeout,
                    wrap_result=wrap_result,
                    send_log_template=send_log_template,
                    error_log_template=error_log_template,
                    warn_on_orphan_response=warn_on_orphan_response,
                    orphan_warning_template=orphan_warning_template,
                )
            )
        raise RuntimeError(
            f"Sync call '{method_name}' cannot be used inside a running event loop. "
            "Use _send_request_and_wait_async(...) instead."
        )

    async def _send_request_and_wait_async(
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
        plugin_comm_queue = self._plugin_comm_queue
        if plugin_comm_queue is None:
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
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: plugin_comm_queue.put(request, timeout=timeout),
            )
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

        deadline = time.time() + timeout
        response_queue = getattr(self, "_response_queue", None)
        pending = getattr(self, "_response_pending", None)
        if pending is None:
            pending = {}
            try:
                object.__setattr__(self, "_response_pending", pending)
            except Exception:
                self._response_pending = pending

        if isinstance(pending, dict) and request_id in pending:
            response = pending.pop(request_id)
            if isinstance(response, dict) and response.get("error"):
                raise RuntimeError(str(response.get("error")))
            result = response.get("result") if isinstance(response, dict) else None
            if wrap_result:
                return result if isinstance(result, dict) else {"result": result}
            return result

        if response_queue is not None:
            loop = asyncio.get_running_loop()
            while time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                try:
                    msg = await loop.run_in_executor(
                        None,
                        lambda r=remaining: response_queue.get(timeout=min(0.05, r)),
                    )
                except Empty:
                    continue
                except Exception:
                    break
                if not isinstance(msg, dict):
                    continue
                rid = msg.get("request_id")
                if rid == request_id:
                    if msg.get("error"):
                        raise RuntimeError(str(msg.get("error")))
                    result = msg.get("result")
                    if wrap_result:
                        return result if isinstance(result, dict) else {"result": result}
                    return result
                if isinstance(pending, dict) and rid:
                    pending[str(rid)] = msg

        check_interval = 0.01
        while time.time() < deadline:
            response = state.get_plugin_response(request_id)
            if not isinstance(response, dict):
                await asyncio.sleep(check_interval)
                continue

            if response.get("error"):
                raise RuntimeError(str(response.get("error")))

            result = response.get("result")
            if wrap_result:
                return result if isinstance(result, dict) else {"result": result}
            return result

        orphan_response = None
        try:
            orphan_response = state.peek_plugin_response(request_id)
        except Exception:
            orphan_response = None
        if warn_on_orphan_response and orphan_response is not None:
            try:
                state.get_plugin_response(request_id)
            except Exception:
                pass
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

    def get_own_base_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            return self._send_request_and_wait(
                method_name="get_own_base_config",
                request_type="PLUGIN_CONFIG_BASE_GET",
                request_data={"plugin_id": self.plugin_id},
                timeout=timeout,
                wrap_result=True,
                error_log_template=None,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin base config get timed out after {timeout}s") from e

    def get_own_profiles_state(self, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            return self._send_request_and_wait(
                method_name="get_own_profiles_state",
                request_type="PLUGIN_CONFIG_PROFILES_GET",
                request_data={"plugin_id": self.plugin_id},
                timeout=timeout,
                wrap_result=True,
                error_log_template=None,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin profiles state get timed out after {timeout}s") from e

    def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> Dict[str, Any]:
        if not isinstance(profile_name, str) or not profile_name.strip():
            raise ValueError("profile_name must be a non-empty string")
        try:
            return self._send_request_and_wait(
                method_name="get_own_profile_config",
                request_type="PLUGIN_CONFIG_PROFILE_GET",
                request_data={
                    "plugin_id": self.plugin_id,
                    "profile_name": profile_name.strip(),
                },
                timeout=timeout,
                wrap_result=True,
                error_log_template=None,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin profile config get timed out after {timeout}s") from e

    def get_own_effective_config(self, profile_name: Optional[str] = None, timeout: float = 5.0) -> Dict[str, Any]:
        """Get effective config.

        - profile_name is None: returns active profile overlay (same as get_own_config).
        - profile_name is a string: returns base overlaid by that profile name.
        """

        request_data: Dict[str, Any] = {"plugin_id": self.plugin_id}
        if profile_name is not None:
            if not isinstance(profile_name, str) or not profile_name.strip():
                raise ValueError("profile_name must be a non-empty string")
            request_data["profile_name"] = profile_name.strip()

        try:
            return self._send_request_and_wait(
                method_name="get_own_effective_config",
                request_type="PLUGIN_CONFIG_EFFECTIVE_GET",
                request_data=request_data,
                timeout=timeout,
                wrap_result=True,
                error_log_template=None,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin effective config get timed out after {timeout}s") from e

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

