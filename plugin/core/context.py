"""
插件上下文模块

提供插件运行时上下文，包括状态更新和消息推送功能。
"""
import contextlib
import contextvars
import asyncio
import time
try:
    import tomllib
except ImportError:
    import tomli as tomllib
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
        """
        Create a BusHub bound to the provided PluginContext for lazy access to per-context bus clients.
        
        Parameters:
            ctx (PluginContext): Plugin context instance that the hub will use to lazily create and return memory, messages, events, and lifecycle clients.
        """
        self._ctx = ctx

    @functools.cached_property
    def memory(self) -> "MemoryClient":
        """
        Expose a MemoryClient instance bound to this plugin context.
        
        Returns:
            MemoryClient: A client for interacting with the plugin memory store, constructed with the current context.
        """
        from plugin.sdk.bus.memory import MemoryClient

        return MemoryClient(self._ctx)

    @functools.cached_property
    def messages(self) -> "MessageClient":
        """
        Expose a MessageClient instance bound to this plugin context.
        
        Returns:
            MessageClient: A MessageClient constructed with the current PluginContext.
        """
        from plugin.sdk.bus.messages import MessageClient

        return MessageClient(self._ctx)

    @functools.cached_property
    def events(self) -> "EventClient":
        """
        Expose an EventClient bound to this PluginContext for emitting and subscribing to plugin events.
        
        Returns:
            EventClient: An EventClient instance bound to this PluginContext.
        """
        from plugin.sdk.bus.events import EventClient

        return EventClient(self._ctx)

    @functools.cached_property
    def lifecycle(self) -> "LifecycleClient":
        """
        Expose a LifecycleClient bound to this PluginContext.
        
        Returns:
            LifecycleClient: a client instance for managing and interacting with the plugin lifecycle, bound to the current context.
        """
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
        """
        Provide lazy access to per-context bus clients.
        
        Returns:
            _BusHub: A _BusHub instance bound to this PluginContext that exposes memory, messages, events, and lifecycle clients.
        """
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
        """
        Best-effort destructor that attempts to release per-context resources by calling close().
        
        Suppresses and ignores any exceptions raised during close to avoid raising during object finalization.
        """
        try:
            self.close()
        except Exception:
            pass

    def get_user_context(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> Dict[str, Any]:
        """
        Deprecated placeholder that immediately raises a RuntimeError directing callers to the memory API.
        
        This method is removed and no longer supported; callers must use ctx.bus.memory.get(...) instead.
        
        Raises:
            RuntimeError: Always raised with instructions to use ctx.bus.memory.get(bucket_id=..., limit=..., timeout=...).
        """
        raise RuntimeError(
            "PluginContext.get_user_context() is no longer supported. "
            "Use ctx.bus.memory.get(bucket_id=..., limit=..., timeout=...) instead."
        )

    def _get_sync_call_in_handler_policy(self) -> str:
        """
        Determine the effective sync-call-in-handler policy for this plugin.
        
        Reads the plugin's config file for `plugin.safety.sync_call_in_handler`. Valid values are `"warn"` and `"reject"`. If the config value is invalid, missing, or the file cannot be read, the global `SYNC_CALL_IN_HANDLER_POLICY` is returned. The resolved policy is cached and invalidated when the config file's modification time changes.
        
        Returns:
            policy (str): The effective policy, either `"warn"` or `"reject"`, or `SYNC_CALL_IN_HANDLER_POLICY` when falling back.
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
        """
        Enforces the configured policy for making synchronous calls from within a handler context.
        
        If the current execution is a handler, this will either raise a RuntimeError or emit a warning depending on the configured policy and logging settings.
        
        Parameters:
            method_name (str): The name of the synchronous method being invoked, used in the generated diagnostic message.
        
        Raises:
            RuntimeError: If the current context is a handler and the policy is `"reject"`.
        """
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
        """
        Set the current in-handler context for the duration of a with-block.
        
        Parameters:
            handler_ctx (str): Identifier for the handler context to mark as active while inside the context manager (e.g., a handler name or type).
        """
        token = _IN_HANDLER.set(handler_ctx)
        try:
            yield
        finally:
            _IN_HANDLER.reset(token)

    def update_status(self, status: Dict[str, Any]) -> None:
        """
        Pushes a status update payload for this plugin to the host status queue.
        
        Constructs a payload with keys "type" ("STATUS_UPDATE"), "plugin_id", "data" (the provided status), and an ISO-8601 UTC timestamp, then enqueues it on the context's status_queue for the host to consume.
        
        Parameters:
            status (Dict[str, Any]): Arbitrary status data to include under the payload's "data" key.
        
        Notes:
            - If the status queue is unavailable or a queue operation fails, a warning is logged.
            - Unexpected errors during enqueue are logged as exceptions.
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
        unsafe: bool = False,
        fast_mode: bool = False,
    ) -> None:
        """
        Push a message from the plugin process to the host message queue or ZeroMQ IPC.
        
        The function attempts to deliver a message described by `message_type`, `description`,
        and optional payload fields to the host. If a ZeroMQ IPC client is available the call
        may use that path (synchronous requests or an optional fast batcher); otherwise it
        enqueues a payload to the in-process `message_queue`. If no delivery path is available
        the message is dropped and a warning is logged.
        
        Parameters:
            source (str): Identifier for the message source (plugin-provided).
            message_type (str): One of "text", "url", "binary", "binary_url" describing the payload kind.
            description (str): Human-readable description of the message.
            priority (int): Priority value where larger numbers indicate higher priority.
            content (Optional[str]): Text content or URL (used for "text" or "url" types).
            binary_data (Optional[bytes]): Binary payload for small binary messages ("binary" type).
            binary_url (Optional[str]): URL referencing binary data ("binary_url" type).
            metadata (Optional[Dict[str, Any]]): Additional metadata to include with the message.
            unsafe (bool): If True, indicates the host may skip strict schema validation for this message.
            fast_mode (bool): If True and ZeroMQ IPC is enabled, use the fast, batched push path when available.
        
        Raises:
            RuntimeError: If ZeroMQ IPC is used and the synchronous push fails or the fast batcher is not initialized.
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
                        "unsafe": bool(unsafe),
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
                        "unsafe": bool(unsafe),
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
                "unsafe": bool(unsafe),
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
        """
        Send a request over the plugin inter-communication channel and block until a matching response arrives.
        
        Parameters:
            method_name (str): Logical name of the calling method used in policy checks and logs.
            request_type (str): Protocol-level request type identifier (e.g., "PLUGIN_TO_PLUGIN", "PLUGIN_QUERY").
            request_data (Dict[str, Any]): Payload to include in the request.
            timeout (float): Maximum seconds to wait for a response before timing out.
            wrap_result (bool): If True, return the response wrapped in the library's standard result shape; if False, return the raw response payload.
            send_log_template (Optional[str]): Optional format template used to log successful send events.
            error_log_template (Optional[str]): Optional format template used to log send failures.
            warn_on_orphan_response (bool): If True, emit a warning when a late ("orphan") response is received after timeout.
            orphan_warning_template (Optional[str]): Optional format template for orphan-response warnings.
        
        Returns:
            Any: The response payload (wrapped or raw depending on `wrap_result`).
        
        Raises:
            RuntimeError: If invoked while an asyncio event loop is already running.
            TimeoutError: If no matching response is received before `timeout` seconds.
        """
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
        """
        Send a request to the plugin-communication bus and wait for a matching response.
        
        Sends a request message containing `request_type` and `request_data` to the plugin communication queue, then waits until a response with the same request_id is received or the timeout elapses. Supports recording unrelated responses for later retrieval and optional logging of send/error/orphan events.
        
        Parameters:
            method_name (str): Logical caller name used for logs/tracing (not included in the sent payload).
            request_type (str): Type identifier added to the request payload.
            request_data (Dict[str, Any]): Additional fields to include in the request payload.
            timeout (float): Maximum seconds to wait for a response before raising TimeoutError.
            wrap_result (bool): If True, wrap non-dict results as {"result": <value>}; if False, return the raw result.
            send_log_template (Optional[str]): Optional format string for a debug log when the request is sent; receives `request_id`, `from_plugin`, and keys from `request_data`.
            error_log_template (Optional[str]): Optional format string for an error log when sending fails; receives `error`.
            warn_on_orphan_response (bool): If True and a late (orphan) response is detected after timeout, log an orphan warning.
            orphan_warning_template (Optional[str]): Optional format string for the orphan warning; receives `request_id`, `from_plugin`, and keys from `request_data`.
        
        Returns:
            The response payload's `result` field, possibly wrapped as `{"result": ...}` when `wrap_result` is True.
        
        Raises:
            RuntimeError: If the plugin communication queue is unavailable, sending the request fails, or the received response contains an `error`.
            TimeoutError: If no matching response is received before `timeout` seconds elapse.
        """
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
                    try:
                        if len(pending) > 1024:
                            pending.clear()
                        pending[str(rid)] = msg
                    except Exception:
                        pass

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
        Trigger a custom event on another plugin via the inter-plugin command queue.
        
        Delivers a PLUGIN_TO_PLUGIN request to the target plugin's command loop (queue-based, not HTTP)
        and returns whatever the remote event handler returns.
        
        Parameters:
            target_plugin_id (str): Destination plugin identifier.
            event_type (str): Custom event type name.
            event_id (str): Identifier for this event instance.
            args (Dict[str, Any]): Arguments passed to the event handler.
            timeout (float): Seconds to wait for a response before timing out.
        
        Returns:
            The value returned by the remote plugin's event handler.
        
        Raises:
            RuntimeError: If the inter-plugin communication queue is unavailable.
            TimeoutError: If no response is received before `timeout` seconds.
            Exception: If the remote event handler raises an error.
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
        """
        Query other plugins for information using optional filter criteria.
        
        Parameters:
            filters (Optional[Dict[str, Any]]): Mapping of query filters to narrow results (e.g., {"enabled": True}); empty or None returns all plugins.
            timeout (float): Maximum time in seconds to wait for a response.
        
        Returns:
            result (Dict[str, Any]): Response payload containing plugin information returned by the host.
        
        Raises:
            TimeoutError: If the query does not complete within `timeout` seconds.
        """
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
        """
        Fetches this plugin's configuration from the host.
        
        Parameters:
            timeout (float): Maximum number of seconds to wait for a response.
        
        Returns:
            Dict[str, Any]: The plugin's configuration data.
        
        Raises:
            TimeoutError: If the request does not complete within `timeout` seconds.
        """
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
        """
        Retrieve this plugin's base configuration from the host.
        
        Parameters:
            timeout (float): Maximum number of seconds to wait for a response.
        
        Returns:
            Dict[str, Any]: The plugin's base configuration.
        
        Raises:
            TimeoutError: If the request times out after `timeout` seconds.
        """
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
        """
        Retrieve this plugin's profiles state from the host.
        
        Parameters:
            timeout (float): Maximum time in seconds to wait for a response.
        
        Returns:
            dict: The plugin's profiles state.
        
        Raises:
            TimeoutError: If no response is received within `timeout` seconds.
        """
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
        """
        Retrieve the configuration for a named profile of this plugin.
        
        Parameters:
        	profile_name (str): Non-empty name of the profile to fetch; leading/trailing whitespace will be removed.
        	timeout (float): Maximum time in seconds to wait for the host response.
        
        Returns:
        	Dict[str, Any]: The profile configuration for the specified profile.
        
        Raises:
        	ValueError: If `profile_name` is not a non-empty string.
        	TimeoutError: If the request times out after `timeout` seconds.
        """
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
        """
        Retrieve this plugin's effective configuration.
        
        When `profile_name` is None, returns the active profile overlay (equivalent to get_own_config). When `profile_name` is provided, returns the base configuration overlaid by the named profile.
        
        Parameters:
            profile_name (Optional[str]): Name of the profile to overlay on the base config. Must be a non-empty string when provided.
            timeout (float): Maximum time in seconds to wait for the response.
        
        Returns:
            Dict[str, Any]: The effective configuration for this plugin.
        
        Raises:
            ValueError: If `profile_name` is provided but is not a non-empty string.
            TimeoutError: If the request times out before a response is received.
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
        """
        Fetches the platform's system-wide configuration.
        
        Parameters:
            timeout (float): Maximum time in seconds to wait for a response.
        
        Returns:
            system_config (Dict[str, Any]): Mapping containing the system configuration.
        
        Raises:
            TimeoutError: If no response is received within `timeout` seconds.
        """
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
        """
        Query a LanLan memory backend and return the service response.
        
        Parameters:
            lanlan_name (str): Identifier of the LanLan memory backend to query.
            query (str): Query string to send to the memory backend.
            timeout (float): Maximum time in seconds to wait for a response.
        
        Returns:
            dict: The response payload from the memory service.
        
        Raises:
            TimeoutError: If the memory query does not complete within `timeout` seconds.
        """
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
        """
        Request an update to this plugin's configuration.
        
        Parameters:
            updates (Dict[str, Any]): Mapping of configuration keys to new values to apply.
            timeout (float): Maximum time in seconds to wait for the host to acknowledge the update.
        
        Returns:
            Dict[str, Any]: Result payload returned by the host describing the update outcome.
        
        Raises:
            TypeError: If `updates` is not a dict.
            TimeoutError: If the host does not respond within `timeout` seconds.
        """
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
