"""
插件上下文模块

提供插件运行时上下文，包括状态更新和消息推送功能。
"""
import contextlib
import contextvars
import asyncio
import base64
import time
try:
    import tomllib
except ImportError:
    import tomli as tomllib
import uuid
import threading
import functools
import itertools

# 模块级初始化锁，用于 _push_lock 的双检初始化
_PUSH_LOCK_INIT = threading.Lock()
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty
from typing import TYPE_CHECKING, Any, Dict, Optional, cast

try:
    import ormsgpack
except ImportError:  # pragma: no cover
    ormsgpack = None  # type: ignore[assignment]

try:
    import zmq
except ImportError:  # pragma: no cover
    zmq = None

from fastapi import FastAPI

from plugin.core.state import state
from plugin.settings import (
    EXPORT_INLINE_BINARY_MAX_BYTES,
    PLUGIN_LOG_CTX_MESSAGE_PUSH,
    PLUGIN_LOG_CTX_STATUS_UPDATE,
    PLUGIN_LOG_SYNC_CALL_WARNINGS,
    SYNC_CALL_IN_HANDLER_POLICY,
)

if TYPE_CHECKING:
    from plugin.sdk.bus.types import BusHubProtocol
    from plugin.sdk.bus.events import EventClient
    from plugin.sdk.bus.lifecycle import LifecycleClient
    from plugin.sdk.memory import MemoryClient
    from plugin.sdk.bus.messages import MessageClient
    from plugin.sdk.bus.conversations import ConversationClient
    from loguru import Logger as LoguruLogger


_IN_HANDLER: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("plugin_in_handler", default=None)

_CURRENT_RUN_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("plugin_current_run_id", default=None)

_IN_WORKER: contextvars.ContextVar[bool] = contextvars.ContextVar("plugin_in_worker", default=False)


class _BusHub:
    def __init__(self, ctx: "PluginContext"):
        self._ctx = ctx

    @functools.cached_property
    def memory(self) -> "MemoryClient":
        from plugin.sdk.memory import MemoryClient

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

    @functools.cached_property
    def conversations(self) -> "ConversationClient":
        from plugin.sdk.bus.conversations import ConversationClient

        return ConversationClient(self._ctx)


@dataclass
class PluginContext:
    """插件运行时上下文"""
    plugin_id: str
    config_path: Path
    logger: "LoguruLogger"
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
    _bus_hub: Optional[Any] = None
    _push_seq: int = 0
    _push_lock: Optional[Any] = None
    _push_batcher: Optional[Any] = None
    _restored_from_freeze: bool = False  # 标记是否从冻结状态恢复

    @property
    def bus(self) -> "BusHubProtocol":
        hub = self._bus_hub
        if hub is None:
            hub = _BusHub(self)
            self._bus_hub = hub
        return cast("BusHubProtocol", hub)

    def close(self) -> None:
        """Release per-context resources such as the ZeroMQ push batcher.

        This is safe to call multiple times.
        """
        batcher = getattr(self, "_push_batcher", None)
        if batcher is not None:
            try:
                # Give the batcher a bounded window to flush and stop.
                batcher.stop(timeout=2.0)
            except Exception as e:
                # Cleanup should be best-effort and never raise.
                try:
                    self.logger.debug(f"Batcher stop failed (best-effort): {e}")
                except Exception:
                    pass
            try:
                self._push_batcher = None
            except Exception:
                pass

        mp_batcher = getattr(self, "_message_plane_push_batcher", None)
        if mp_batcher is not None:
            push_lock = getattr(self, "_push_lock", None)
            acquired_push_lock = False
            if push_lock is not None:
                try:
                    acquired_push_lock = bool(push_lock.acquire(timeout=3.0))
                except TypeError:
                    acquired_push_lock = bool(push_lock.acquire())
                except Exception:
                    acquired_push_lock = False
            try:
                mp_batcher.stop(timeout=2.0)
            except Exception as e:
                try:
                    self.logger.debug(f"Message plane batcher stop failed (best-effort): {e}")
                except Exception:
                    pass
            try:
                self._message_plane_push_batcher = None
            except Exception:
                pass
            finally:
                if acquired_push_lock and push_lock is not None:
                    try:
                        push_lock.release()
                    except Exception:
                        pass

        zmq_client = getattr(self, "_zmq_ipc_client", None)
        if zmq_client is not None:
            try:
                close_fn = getattr(zmq_client, "close", None)
                if callable(close_fn):
                    close_fn()
            except Exception:
                pass
            try:
                self._zmq_ipc_client = None
            except Exception:
                pass

        tls = getattr(self, "_message_plane_ingest_tls", None)
        if tls is not None:
            try:
                sock = getattr(tls, "sock", None)
                if sock is not None:
                    try:
                        sock.close(0)
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                object.__setattr__(self, "_message_plane_ingest_tls", None)
            except Exception:
                try:
                    self._message_plane_ingest_tls = None
                except Exception:
                    pass

    def __del__(self) -> None:  # pragma: no cover - best-effort safety net
        try:
            self.close()
        except Exception:
            pass

    def get_user_context(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> Dict[str, Any]:
        """[DEPRECATED] Use ctx.bus.memory.get(bucket_id=..., limit=..., timeout=...) instead."""
        try:
            self.logger.warning(
                "PluginContext.get_user_context() is deprecated; "
                "use ctx.bus.memory.get(bucket_id=..., limit=..., timeout=...) instead."
            )
        except Exception:
            pass
        from plugin.sdk.bus.memory import MemoryClient as BusMemoryClient
        bus_client = BusMemoryClient(self)
        if self._is_in_event_loop():
            raise RuntimeError(
                "get_user_context() cannot be called from within an event loop; "
                "use await ctx.bus.memory.get_async(...) instead."
            )
        result = bus_client.get_sync(bucket_id=bucket_id, limit=limit, timeout=timeout)
        return {"history": [r.dump() for r in result], "bucket_id": bucket_id}

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
            self._a1_policy_mtime = st.st_mtime
            self._a1_policy_value = policy
            return policy
        except Exception:
            return SYNC_CALL_IN_HANDLER_POLICY

    def _enforce_sync_call_policy(self, method_name: str) -> None:
        handler_ctx = _IN_HANDLER.get()
        if handler_ctx is None:
            return
        # @worker threads run in a separate thread pool — sync IPC calls
        # are safe there (they don't block the command loop).
        if _IN_WORKER.get():
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

    @contextlib.contextmanager
    def _run_scope(self, run_id: Optional[str]):
        token = _CURRENT_RUN_ID.set(run_id if isinstance(run_id, str) and run_id.strip() else None)
        try:
            yield
        finally:
            _CURRENT_RUN_ID.reset(token)

    @property
    def run_id(self) -> Optional[str]:
        return _CURRENT_RUN_ID.get()

    def require_run_id(self) -> str:
        rid = self.run_id
        if not isinstance(rid, str) or not rid.strip():
            raise RuntimeError("run_id is required (this entry may not be triggered via /runs)")
        return rid

    def _is_in_event_loop(self) -> bool:
        """检测当前是否在事件循环中运行。
        
        Returns:
            True 如果当前在事件循环中，False 如果在 worker 线程或无事件循环环境
        """
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def _run_coro_sync(self, coro: Any, *, operation: str) -> Any:
        """Run a coroutine from sync context.

        This is a convenience wrapper (e.g. run_update_sync) and is intentionally
        strict: it refuses to run when an event loop is already running.

        NOTE: ``asyncio.run()`` creates a **new** Context, which does NOT inherit
        the calling thread's contextvars.  To preserve values like
        ``_CURRENT_RUN_ID`` that were propagated into a @worker thread, we
        capture a snapshot of the current context and re-apply it inside the
        new event loop via a thin wrapper coroutine.
        """

        self._enforce_sync_call_policy(operation)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            ctx_snapshot = contextvars.copy_context()

            async def _with_ctx():
                # Re-apply every contextvar from the snapshot into the
                # asyncio.run()-created context so that require_run_id() etc.
                # see the correct values.
                for var in ctx_snapshot:
                    try:
                        var.set(ctx_snapshot[var])
                    except Exception:
                        pass
                return await coro

            return asyncio.run(_with_ctx())
        raise RuntimeError(f"{operation}_sync cannot be used inside a running event loop; use 'await {operation}(...)' instead")

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
        except Exception:
            # 其他未知异常
            self.logger.exception(f"Unexpected error updating status for plugin {self.plugin_id}")

    # ==================== Unified Export Push ====================

    async def _export_push_async(
        self,
        *,
        export_type: str,
        run_id: Optional[str] = None,
        text: Optional[str] = None,
        json_data: Optional[Dict[str, Any]] = None,
        url: Optional[str] = None,
        binary_data: Optional[bytes] = None,
        binary_url: Optional[str] = None,
        mime: Optional[str] = None,
        description: Optional[str] = None,
        label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Unified core implementation for all export types."""
        rid = run_id if isinstance(run_id, str) and run_id.strip() else self.require_run_id()
        request_data: Dict[str, Any] = {
            "run_id": rid,
            "export_type": export_type,
            "description": description,
            "metadata": metadata or {},
        }
        if label is not None:
            request_data["label"] = label

        if export_type == "text":
            request_data["text"] = text
        elif export_type == "json":
            request_data["json"] = json_data
        elif export_type == "url":
            request_data["url"] = url
        elif export_type == "binary_url":
            request_data["binary_url"] = binary_url
            request_data["mime"] = mime
        elif export_type == "binary":
            if not isinstance(binary_data, (bytes, bytearray)):
                raise TypeError("binary_data must be bytes")
            data = bytes(binary_data)
            limit = int(EXPORT_INLINE_BINARY_MAX_BYTES) if EXPORT_INLINE_BINARY_MAX_BYTES is not None else 0
            if limit > 0 and len(data) > limit:
                raise ValueError("binary_data too large")
            request_data["binary_base64"] = base64.b64encode(data).decode("ascii")
            request_data["mime"] = mime
        else:
            raise ValueError(f"unsupported export_type: {export_type}")

        return await self._send_request_and_wait_async(
            method_name=f"export_push_{export_type}",
            request_type="EXPORT_PUSH",
            request_data=request_data,
            timeout=float(timeout),
            wrap_result=True,
        )

    def export_push(
        self,
        *,
        export_type: str,
        run_id: Optional[str] = None,
        text: Optional[str] = None,
        json_data: Optional[Dict[str, Any]] = None,
        url: Optional[str] = None,
        binary_data: Optional[bytes] = None,
        binary_url: Optional[str] = None,
        mime: Optional[str] = None,
        description: Optional[str] = None,
        label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ):
        """Unified export push (smart sync/async proxy)."""
        coro = self._export_push_async(
            export_type=export_type, run_id=run_id, text=text, json_data=json_data,
            url=url, binary_data=binary_data, binary_url=binary_url, mime=mime,
            description=description, label=label, metadata=metadata, timeout=timeout,
        )
        if self._is_in_event_loop():
            return coro
        return self._run_coro_sync(coro, operation=f"export_push_{export_type}")

    async def export_push_async(
        self,
        *,
        export_type: str,
        run_id: Optional[str] = None,
        text: Optional[str] = None,
        json_data: Optional[Dict[str, Any]] = None,
        url: Optional[str] = None,
        binary_data: Optional[bytes] = None,
        binary_url: Optional[str] = None,
        mime: Optional[str] = None,
        description: Optional[str] = None,
        label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Unified export push (explicit async)."""
        return await self._export_push_async(
            export_type=export_type, run_id=run_id, text=text, json_data=json_data,
            url=url, binary_data=binary_data, binary_url=binary_url, mime=mime,
            description=description, label=label, metadata=metadata, timeout=timeout,
        )

    # ==================== Backward-compatible thin wrappers ====================

    async def _export_push_text_async(self, *, run_id: Optional[str] = None, text: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_async(export_type="text", run_id=run_id, text=text, description=description, metadata=metadata, timeout=timeout)

    def export_push_text(self, *, run_id: Optional[str] = None, text: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0):
        """Push text export (smart sync/async proxy)."""
        return self.export_push(export_type="text", run_id=run_id, text=text, description=description, metadata=metadata, timeout=timeout)

    async def export_push_text_async(self, *, run_id: Optional[str] = None, text: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_text_async(run_id=run_id, text=text, description=description, metadata=metadata, timeout=timeout)

    def export_push_text_sync(self, *, run_id: Optional[str] = None, text: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_coro_sync(self._export_push_text_async(run_id=run_id, text=text, description=description, metadata=metadata, timeout=timeout), operation="export_push_text")

    async def _export_push_binary_async(self, *, run_id: Optional[str] = None, binary_data: bytes, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_async(export_type="binary", run_id=run_id, binary_data=binary_data, mime=mime, description=description, metadata=metadata, timeout=timeout)

    def export_push_binary(self, *, run_id: Optional[str] = None, binary_data: bytes, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0):
        """Push binary export (smart sync/async proxy)."""
        return self.export_push(export_type="binary", run_id=run_id, binary_data=binary_data, mime=mime, description=description, metadata=metadata, timeout=timeout)

    async def export_push_binary_async(self, *, run_id: Optional[str] = None, binary_data: bytes, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_binary_async(run_id=run_id, binary_data=binary_data, mime=mime, description=description, metadata=metadata, timeout=timeout)

    def export_push_binary_sync(self, *, run_id: Optional[str] = None, binary_data: bytes, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_coro_sync(self._export_push_binary_async(run_id=run_id, binary_data=binary_data, mime=mime, description=description, metadata=metadata, timeout=timeout), operation="export_push_binary")

    async def _export_push_url_async(self, *, run_id: Optional[str] = None, url: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_async(export_type="url", run_id=run_id, url=url, description=description, metadata=metadata, timeout=timeout)

    def export_push_url(self, *, run_id: Optional[str] = None, url: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0):
        """Push URL export (smart sync/async proxy)."""
        return self.export_push(export_type="url", run_id=run_id, url=url, description=description, metadata=metadata, timeout=timeout)

    async def export_push_url_async(self, *, run_id: Optional[str] = None, url: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_url_async(run_id=run_id, url=url, description=description, metadata=metadata, timeout=timeout)

    def export_push_url_sync(self, *, run_id: Optional[str] = None, url: str, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_coro_sync(self._export_push_url_async(run_id=run_id, url=url, description=description, metadata=metadata, timeout=timeout), operation="export_push_url")

    async def _export_push_binary_url_async(self, *, run_id: Optional[str] = None, binary_url: str, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_async(export_type="binary_url", run_id=run_id, binary_url=binary_url, mime=mime, description=description, metadata=metadata, timeout=timeout)

    def export_push_binary_url(self, *, run_id: Optional[str] = None, binary_url: str, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0):
        """Push binary URL export (smart sync/async proxy)."""
        return self.export_push(export_type="binary_url", run_id=run_id, binary_url=binary_url, mime=mime, description=description, metadata=metadata, timeout=timeout)

    async def export_push_binary_url_async(self, *, run_id: Optional[str] = None, binary_url: str, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return await self._export_push_binary_url_async(run_id=run_id, binary_url=binary_url, mime=mime, description=description, metadata=metadata, timeout=timeout)

    def export_push_binary_url_sync(self, *, run_id: Optional[str] = None, binary_url: str, mime: Optional[str] = None, description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_coro_sync(self._export_push_binary_url_async(run_id=run_id, binary_url=binary_url, mime=mime, description=description, metadata=metadata, timeout=timeout), operation="export_push_binary_url")

    async def _run_update_async(
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
        rid = run_id if isinstance(run_id, str) and run_id.strip() else self.require_run_id()
        data: Dict[str, Any] = {
            "run_id": rid,
        }
        if progress is not None:
            data["progress"] = progress
        if stage is not None:
            data["stage"] = stage
        if message is not None:
            data["message"] = message
        if step is not None:
            data["step"] = step
        if step_total is not None:
            data["step_total"] = step_total
        if eta_seconds is not None:
            data["eta_seconds"] = eta_seconds
        if metrics is not None:
            data["metrics"] = metrics

        return await self._send_request_and_wait_async(
            method_name="run_update",
            request_type="RUN_UPDATE",
            request_data=data,
            timeout=float(timeout),
            wrap_result=True,
        )

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
    ):
        """智能代理：自动检测执行环境，选择同步或异步执行方式。"""
        coro = self._run_update_async(
            run_id=run_id,
            progress=progress,
            stage=stage,
            message=message,
            step=step,
            step_total=step_total,
            eta_seconds=eta_seconds,
            metrics=metrics,
            timeout=timeout,
        )
        if self._is_in_event_loop():
            return coro
        return self._run_coro_sync(coro, operation="run_update")

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
        return await self._run_update_async(
            run_id=run_id,
            progress=progress,
            stage=stage,
            message=message,
            step=step,
            step_total=step_total,
            eta_seconds=eta_seconds,
            metrics=metrics,
            timeout=timeout,
        )

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
        return self._run_coro_sync(
            self._run_update_async(
                run_id=run_id,
                progress=progress,
                stage=stage,
                message=message,
                step=step,
                step_total=step_total,
                eta_seconds=eta_seconds,
                metrics=metrics,
                timeout=timeout,
            ),
            operation="run_update",
        )

    async def _run_progress_async(
        self,
        *,
        run_id: Optional[str] = None,
        progress: float,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        return await self._run_update_async(
            run_id=run_id,
            progress=float(progress),
            stage=stage,
            message=message,
            timeout=float(timeout),
        )

    def run_progress(
        self,
        *,
        run_id: Optional[str] = None,
        progress: float,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        timeout: float = 5.0,
    ):
        """智能代理：自动检测执行环境，选择同步或异步执行方式。"""
        coro = self._run_progress_async(
            run_id=run_id, progress=progress, stage=stage, message=message, timeout=timeout
        )
        if self._is_in_event_loop():
            return coro
        return self._run_coro_sync(coro, operation="run_progress")

    async def run_progress_async(
        self,
        *,
        run_id: Optional[str] = None,
        progress: float = 0.0,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        return await self._run_progress_async(run_id=run_id, progress=progress, stage=stage, message=message, timeout=timeout)

    def run_progress_sync(
        self,
        *,
        run_id: Optional[str] = None,
        progress: float = 0.0,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        return self._run_coro_sync(
            self._run_progress_async(run_id=run_id, progress=progress, stage=stage, message=message, timeout=timeout),
            operation="run_progress",
        )

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
            unsafe: 为 True 时，允许主进程跳过严格 schema 校验（用于高性能场景，默认 False）
        """
        # Prefer writing messages directly to message_plane ingest to isolate high-frequency writes
        # from the control plane and rely on ZMQ backpressure.
        if zmq is not None:
            try:
                from plugin.settings import MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT
                from plugin.settings import (
                    PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE,
                    PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS,
                )

                endpoint = str(MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT)
                if endpoint:
                    if bool(fast_mode):
                        lock = getattr(self, "_push_lock", None)
                        if lock is None:
                            with _PUSH_LOCK_INIT:
                                lock = getattr(self, "_push_lock", None)
                                if lock is None:
                                    new_lock = threading.Lock()
                                    try:
                                        object.__setattr__(self, "_push_lock", new_lock)
                                    except Exception:
                                        self._push_lock = new_lock
                                    lock = new_lock

                        # 双重检查锁定模式：先检查是否需要创建 batcher
                        batcher = getattr(self, "_message_plane_push_batcher", None)
                        need_create_batcher = batcher is None
                        
                        if need_create_batcher:
                            # 在锁外创建 batcher（避免在锁内做 I/O）
                            from plugin.utils.zeromq_ipc import MessagePlaneIngestBatcher
                            from plugin.settings import (
                                MESSAGE_PLANE_PUSH_BATCHER_ENQUEUE_TIMEOUT_SECONDS,
                                MESSAGE_PLANE_PUSH_BATCHER_MAX_QUEUE,
                                MESSAGE_PLANE_PUSH_BATCHER_REJECT_RATIO,
                            )
                            
                            new_batcher = MessagePlaneIngestBatcher(
                                from_plugin=self.plugin_id,
                                endpoint=endpoint,
                                batch_size=int(PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE),
                                flush_interval_ms=int(PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS),
                                max_queue=int(MESSAGE_PLANE_PUSH_BATCHER_MAX_QUEUE),
                                reject_ratio=float(MESSAGE_PLANE_PUSH_BATCHER_REJECT_RATIO),
                                enqueue_timeout_s=float(MESSAGE_PLANE_PUSH_BATCHER_ENQUEUE_TIMEOUT_SECONDS),
                            )
                            # 在锁外启动（可能涉及 ZMQ 连接）
                            new_batcher.start()
                            
                            # 获取锁后再次检查并设置
                            with lock:
                                batcher = getattr(self, "_message_plane_push_batcher", None)
                                if batcher is None:
                                    batcher = new_batcher
                                    try:
                                        object.__setattr__(self, "_message_plane_push_batcher", batcher)
                                    except Exception:
                                        self._message_plane_push_batcher = batcher
                                else:
                                    # 另一个线程已经创建了，关闭我们创建的
                                    try:
                                        new_batcher.stop(timeout=2.0)
                                    except Exception:
                                        pass
                        
                        # 在锁内只做快速的内存操作
                        with lock:
                            # Fast path: use counter instead of UUID, use float timestamp instead of ISO
                            msg_counter = getattr(self, "_msg_counter", None)
                            if msg_counter is None:
                                msg_counter = itertools.count(1)
                                try:
                                    object.__setattr__(self, "_msg_counter", msg_counter)
                                except Exception:
                                    self._msg_counter = msg_counter
                            
                            # Ultra-fast path: minimize allocations
                            payload = {
                                "type": "MESSAGE_PUSH",
                                "message_id": f"{self.plugin_id}:{next(msg_counter)}",
                                "plugin_id": self.plugin_id,
                                "source": source,
                                "description": description,
                                "priority": priority,
                                "message_type": message_type,
                                "content": content,
                                "binary_data": binary_data,
                                "binary_url": binary_url,
                                "metadata": metadata if metadata is not None else {},
                                "unsafe": unsafe,
                                "time": time.time(),
                            }
                            item = {"store": "messages", "topic": "all", "payload": payload}
                            try:
                                batcher.enqueue(item)
                            except Exception:
                                # Backpressure: do not fall back to control-plane (it will amplify overload).
                                try:
                                    last_ts = float(getattr(self, "_mp_backpressure_last_ts", 0.0) or 0.0)
                                except Exception:
                                    last_ts = 0.0
                                try:
                                    cnt = int(getattr(self, "_mp_backpressure_count", 0) or 0) + 1
                                except Exception:
                                    cnt = 1
                                try:
                                    object.__setattr__(self, "_mp_backpressure_count", cnt)
                                except Exception:
                                    try:
                                        self._mp_backpressure_count = cnt
                                    except Exception:
                                        pass
                                now_ts = time.time()
                                if now_ts - last_ts >= 1.0:
                                    try:
                                        object.__setattr__(self, "_mp_backpressure_last_ts", float(now_ts))
                                        object.__setattr__(self, "_mp_backpressure_count", 0)
                                    except Exception:
                                        try:
                                            self._mp_backpressure_last_ts = float(now_ts)
                                            self._mp_backpressure_count = 0
                                        except Exception:
                                            pass
                                    try:
                                        self.logger.warning(
                                            "[PluginContext] message_plane backpressure: rejected push_message.fast (x{})",
                                            int(cnt),
                                        )
                                    except Exception:
                                        pass
                                return
                            if PLUGIN_LOG_CTX_MESSAGE_PUSH:
                                try:
                                    self.logger.debug(
                                        f"Plugin {self.plugin_id} pushed message (message_plane.fast): {source} - {description}"
                                    )
                                except Exception:
                                    pass
                            return

                    tls = getattr(self, "_message_plane_ingest_tls", None)
                    if tls is None:
                        tls = threading.local()
                        try:
                            object.__setattr__(self, "_message_plane_ingest_tls", tls)
                        except Exception:
                            self._message_plane_ingest_tls = tls

                    sock = getattr(tls, "sock", None)
                    if sock is None:
                        ctx = zmq.Context.instance()
                        sock = ctx.socket(zmq.PUSH)
                        try:
                            sock.setsockopt(zmq.LINGER, 0)
                            try:
                                from plugin.settings import MESSAGE_PLANE_INGEST_SNDTIMEO_MS

                                sock.setsockopt(zmq.SNDTIMEO, int(MESSAGE_PLANE_INGEST_SNDTIMEO_MS))
                            except Exception:
                                pass
                        except Exception:
                            pass
                        sock.connect(endpoint)
                        try:
                            tls.sock = sock
                        except Exception:
                            pass

                    payload = {
                        "type": "MESSAGE_PUSH",
                        "message_id": str(uuid.uuid4()),
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
                    msg = {
                        "v": 1,
                        "kind": "delta_batch",
                        "from": str(self.plugin_id),
                        "ts": time.time(),
                        "batch_id": str(uuid.uuid4()),
                        "items": [
                            {
                                "store": "messages",
                                "topic": "all",
                                "payload": payload,
                            }
                        ],
                    }

                    # Blocking send: rely on ZMQ HWM for backpressure.
                    if ormsgpack is None:
                        raise RuntimeError("ormsgpack is required for message_plane push")
                    sock.send(ormsgpack.packb(msg), flags=0)
                    if PLUGIN_LOG_CTX_MESSAGE_PUSH:
                        try:
                            self.logger.debug(f"Plugin {self.plugin_id} pushed message (message_plane): {source} - {description}")
                        except Exception:
                            pass
                    return
            except Exception as e:
                # Catch all ZMQ/Batcher errors to prevent plugin crash
                try:
                    self.logger.warning(
                        "[PluginContext] message_plane error: plugin_id={} error={}",
                        self.plugin_id, e
                    )
                except Exception:
                    pass
                # Do not fall back to control-plane: it can amplify overload
                return

        # message_plane 不可用时，尝试回退到 message_queue（如果可用）
        if self.message_queue is not None:
            try:
                payload = {
                    "type": "MESSAGE_PUSH",
                    "message_id": str(uuid.uuid4()),
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
                    try:
                        self.logger.debug(f"Plugin {self.plugin_id} pushed message (fallback queue): {source} - {description}")
                    except Exception:
                        pass
                return
            except Exception as e:
                try:
                    self.logger.warning(f"[PluginContext] fallback message_queue push failed: {e}")
                except Exception:
                    pass
        
        # 所有方式都不可用时，记录警告而非抛错（避免插件崩溃）
        try:
            self.logger.error(
                "[PluginContext] push_message failed: message_plane unavailable "
                "(zmq={}, endpoint={}). Check MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT config.",
                zmq is not None,
                bool(getattr(self, "_mp_endpoint_cached", None)),
            )
        except Exception:
            pass

    async def push_message_async(
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
        """异步版本的 push_message，使用 asyncio.to_thread 包装同步调用。
        
        Note: 底层 ZMQ socket 是同步的，此方法通过线程池实现非阻塞。
        """
        await asyncio.to_thread(
            self.push_message,
            source=source,
            message_type=message_type,
            description=description,
            priority=priority,
            content=content,
            binary_data=binary_data,
            binary_url=binary_url,
            metadata=metadata,
            unsafe=unsafe,
            fast_mode=fast_mode,
        )

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
        _ = method_name

        def _error_to_message(error: Any) -> str:
            if isinstance(error, str):
                return error
            if isinstance(error, dict):
                code = error.get("code")
                message = error.get("message")
                details = error.get("details")
                parts = []
                if isinstance(code, str) and code:
                    parts.append(f"[{code}]")
                if isinstance(message, str) and message:
                    parts.append(message)
                if details is not None:
                    parts.append(f"details={details}")
                if parts:
                    return " ".join(parts)
            return str(error)

        plugin_comm_queue = self._plugin_comm_queue
        if plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {self.plugin_id}. "
                "This method can only be called from within a plugin process."
            )

        request_id = str(uuid.uuid4())
        payload = dict(request_data or {})
        for _k in ("type", "from_plugin", "request_id", "timeout"):
            payload.pop(_k, None)
        request: Dict[str, Any] = {
            **payload,
            "type": request_type,
            "from_plugin": self.plugin_id,
            "request_id": request_id,
            "timeout": timeout,
        }

        deadline = time.time() + timeout
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: plugin_comm_queue.put(request, timeout=max(0.0, deadline - time.time())),
            )
            if send_log_template:
                try:
                    self.logger.debug(
                        send_log_template.format(
                            request_id=request_id,
                            from_plugin=self.plugin_id,
                            **payload,
                        )
                    )
                except Exception:
                    pass
        except Exception as e:
            if error_log_template:
                try:
                    self.logger.exception(error_log_template.format(error=e))
                except Exception:
                    pass
            raise RuntimeError(f"Failed to send {request_type} request: {e}") from e

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
                raise RuntimeError(_error_to_message(response.get("error")))
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
                        raise RuntimeError(_error_to_message(msg.get("error")))
                    result = msg.get("result")
                    if wrap_result:
                        return result if isinstance(result, dict) else {"result": result}
                    return result
                if isinstance(pending, dict) and rid:
                    if len(pending) > 1024:
                        raise RuntimeError(
                            f"_response_pending overflow (size={len(pending)}, rid={rid}); "
                            "upstream response correlation/backpressure bug must be fixed."
                        )
                    pending[str(rid)] = msg

        check_interval = 0.01
        while time.time() < deadline:
            response = state.get_plugin_response(request_id)
            if not isinstance(response, dict):
                await asyncio.sleep(check_interval)
                continue

            if response.get("error"):
                raise RuntimeError(_error_to_message(response.get("error")))

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
                try:
                    self.logger.warning(
                        orphan_warning_template.format(
                            request_id=request_id,
                            from_plugin=self.plugin_id,
                            **payload,
                        )
                    )
                except Exception:
                    pass
        raise TimeoutError(f"{request_type} timed out after {timeout}s")
    
    def trigger_plugin_event_sync(
        self,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: Dict[str, Any],
        timeout: float = 10.0
    ) -> Dict[str, Any]:
        """同步版本:触发其他插件的自定义事件（插件间通信）
        
        Args:
            target_plugin_id: 目标插件ID
            event_type: 自定义事件类型
            event_id: 事件ID
            params: 参数字典
            timeout: 超时时间（秒）
            
        Returns:
            事件处理器的返回结果
        """
        try:
            return self._send_request_and_wait(
                method_name="trigger_plugin_event",
                request_type="PLUGIN_TO_PLUGIN",
                request_data={
                    "to_plugin": target_plugin_id,
                    "event_type": event_type,
                    "event_id": event_id,
                    "args": params,  # 内部协议仍使用 args
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
    
    async def trigger_plugin_event_async(
        self,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: Dict[str, Any],
        timeout: float = 10.0
    ) -> Dict[str, Any]:
        """异步版本:触发其他插件的自定义事件（插件间通信）"""
        try:
            return await self._send_request_and_wait_async(
                method_name="trigger_plugin_event",
                request_type="PLUGIN_TO_PLUGIN",
                request_data={
                    "to_plugin": target_plugin_id,
                    "event_type": event_type,
                    "event_id": event_id,
                    "args": params,  # 内部协议仍使用 args
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
    
    def trigger_plugin_event(
        self,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: Dict[str, Any],
        timeout: float = 10.0
    ):
        """智能版本:自动检测执行环境,选择同步或异步执行方式
        
        Returns:
            在事件循环中返回协程,否则返回结果字典
        """
        if self._is_in_event_loop():
            return self.trigger_plugin_event_async(
                target_plugin_id=target_plugin_id,
                event_type=event_type,
                event_id=event_id,
                params=params,
                timeout=timeout,
            )
        return self.trigger_plugin_event_sync(
            target_plugin_id=target_plugin_id,
            event_type=event_type,
            event_id=event_id,
            params=params,
            timeout=timeout,
        )

    def query_plugins_sync(self, filters: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        """同步版本:查询插件列表"""
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
    
    async def query_plugins_async(self, filters: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        """异步版本:查询插件列表"""
        try:
            return await self._send_request_and_wait_async(
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
    
    def query_plugins(self, filters: Optional[Dict[str, Any]] = None, timeout: float = 5.0):
        """智能版本:自动检测执行环境,选择同步或异步执行方式
        
        Returns:
            在事件循环中返回协程,否则返回结果字典
        """
        if self._is_in_event_loop():
            return self.query_plugins_async(filters=filters, timeout=timeout)
        return self.query_plugins_sync(filters=filters, timeout=timeout)

    async def get_own_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            return await self._send_request_and_wait_async(
                method_name="get_own_config",
                request_type="PLUGIN_CONFIG_GET",
                request_data={"plugin_id": self.plugin_id},
                timeout=timeout,
                wrap_result=True,
                error_log_template=None,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin config get timed out after {timeout}s") from e

    async def get_own_base_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            return await self._send_request_and_wait_async(
                method_name="get_own_base_config",
                request_type="PLUGIN_CONFIG_BASE_GET",
                request_data={"plugin_id": self.plugin_id},
                timeout=timeout,
                wrap_result=True,
                error_log_template=None,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin base config get timed out after {timeout}s") from e

    async def get_own_profiles_state(self, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            return await self._send_request_and_wait_async(
                method_name="get_own_profiles_state",
                request_type="PLUGIN_CONFIG_PROFILES_GET",
                request_data={"plugin_id": self.plugin_id},
                timeout=timeout,
                wrap_result=True,
                error_log_template=None,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin profiles state get timed out after {timeout}s") from e

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> Dict[str, Any]:
        if not isinstance(profile_name, str) or not profile_name.strip():
            raise ValueError("profile_name must be a non-empty string")
        try:
            return await self._send_request_and_wait_async(
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

    async def get_own_effective_config(
        self,
        profile_name: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Get effective config.

        - profile_name is None: returns active profile overlay (same as get_own_config).
        - profile_name is a string: returns base + that profile overlay.
        """

        request_data: Dict[str, Any] = {
            "plugin_id": self.plugin_id,
        }
        if isinstance(profile_name, str) and profile_name.strip():
            request_data["profile_name"] = profile_name.strip()

        try:
            return await self._send_request_and_wait_async(
                method_name="get_own_effective_config",
                request_type="PLUGIN_CONFIG_EFFECTIVE_GET",
                request_data=request_data,
                timeout=timeout,
                wrap_result=True,
                error_log_template=None,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Plugin effective config get timed out after {timeout}s") from e

    async def get_system_config(self, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            return await self._send_request_and_wait_async(
                method_name="get_system_config",
                request_type="PLUGIN_SYSTEM_CONFIG_GET",
                request_data={},
                timeout=timeout,
                wrap_result=True,
                error_log_template=None,
            )
        except TimeoutError as e:
            raise TimeoutError(f"System config get timed out after {timeout}s") from e

    def query_memory_sync(self, lanlan_name: str, query: str, timeout: float = 5.0) -> Dict[str, Any]:
        """同步版本:查询内存数据"""
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
    
    async def query_memory_async(self, lanlan_name: str, query: str, timeout: float = 5.0) -> Dict[str, Any]:
        """异步版本:查询内存数据"""
        try:
            return await self._send_request_and_wait_async(
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
    
    def query_memory(self, lanlan_name: str, query: str, timeout: float = 5.0):
        """智能版本:自动检测执行环境,选择同步或异步执行方式
        
        Returns:
            在事件循环中返回协程,否则返回结果字典
        """
        if self._is_in_event_loop():
            return self.query_memory_async(lanlan_name=lanlan_name, query=query, timeout=timeout)
        return self.query_memory_sync(lanlan_name=lanlan_name, query=query, timeout=timeout)

    async def update_own_config(self, updates: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
        if not isinstance(updates, dict):
            raise TypeError("updates must be a dict")
        try:
            return await self._send_request_and_wait_async(
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

