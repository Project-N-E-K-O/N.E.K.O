"""插件进程间通信资源管理器 - ZMQ 版

通过 :class:`~plugin.core.zmq_transport.HostTransport` 与子进程通信。
所有消息复用 2 个 ZMQ 管道 (downlink / uplink), 按 channel tag 分流。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Optional

from loguru import logger

from plugin.utils.time_utils import now_iso
from plugin.settings import (
    PLUGIN_TRIGGER_TIMEOUT,
    PLUGIN_SHUTDOWN_TIMEOUT,
    QUEUE_GET_TIMEOUT,
    MESSAGE_CONSUMER_SLEEP_INTERVAL,
    PLUGIN_LOG_MESSAGE_FORWARD,
    PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS,
)
from plugin._types.exceptions import PluginExecutionError
from plugin.logging_config import format_log_text as _format_log_text
from plugin.core.zmq_transport import (
    HostTransport, CH_RES, CH_STS, CH_MSG, CH_COMM,
)


@dataclass
class PluginCommunicationResourceManager:
    """Host-side communication manager backed by ZMQ transport.

    Reads all uplink messages in a single consumer task and dispatches by
    channel tag (``res`` / ``sts`` / ``msg`` / ``comm``).
    """

    plugin_id: str
    transport: HostTransport
    logger: Any = field(default_factory=lambda: logger.bind(component="communication"))

    # async internals
    _pending_futures: Dict[str, asyncio.Future] = field(default_factory=dict)
    _uplink_consumer_task: Optional[asyncio.Task] = None
    _shutdown_event: Optional[asyncio.Event] = None
    _message_target_queue: Optional[asyncio.Queue] = None
    _background_tasks: set[asyncio.Task] = field(default_factory=set)
    _last_forward_log_key: Optional[tuple] = field(default=None, init=False, repr=False)
    _last_forward_log_time: float = field(default=0.0, init=False, repr=False)
    _last_forward_log_repeat_count: int = field(default=0, init=False, repr=False)

    # ── lifecycle ────────────────────────────────────────────────

    def _ensure_shutdown_event(self) -> None:
        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()

    async def start(self, message_target_queue: Optional[asyncio.Queue] = None) -> None:
        self._message_target_queue = message_target_queue
        if self._uplink_consumer_task is None or self._uplink_consumer_task.done():
            self._uplink_consumer_task = asyncio.create_task(self._consume_uplink())
            self.logger.debug("Started uplink consumer for plugin {}", self.plugin_id)

    async def shutdown(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None:
        self.logger.debug("Shutting down communication for plugin {}", self.plugin_id)
        self._ensure_shutdown_event()
        se = self._shutdown_event
        if se is not None:
            se.set()

        # Let the consumer drain briefly, then cancel.
        graceful = min(0.5, float(timeout)) if timeout is not None else 0.5
        if self._uplink_consumer_task and not self._uplink_consumer_task.done():
            try:
                await asyncio.wait_for(self._uplink_consumer_task, timeout=graceful)
            except asyncio.TimeoutError:
                self._uplink_consumer_task.cancel()
                try:
                    await self._uplink_consumer_task
                except asyncio.CancelledError:
                    pass

        self._cleanup_pending_futures()

        if self._background_tasks:
            for t in list(self._background_tasks):
                t.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        self.logger.debug("Communication for plugin {} shutdown complete", self.plugin_id)

    # ── pending futures ──────────────────────────────────────────

    def get_pending_requests_count(self) -> int:
        return len(self._pending_futures)

    def _cleanup_pending_futures(self) -> None:
        count = len(self._pending_futures)
        for _rid, fut in self._pending_futures.items():
            if not fut.done():
                fut.cancel()
        self._pending_futures.clear()
        if count > 0:
            self.logger.debug("Cleaned up {} pending futures for plugin {}", count, self.plugin_id)

    # ── send commands (downlink) ─────────────────────────────────

    async def _send_command_and_wait(
        self,
        req_id: str,
        msg: dict,
        timeout: float,
        error_context: str,
    ) -> Any:
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_futures[req_id] = future
        try:
            await self.transport.send_command(msg)
        except Exception as e:
            self._pending_futures.pop(req_id, None)
            raise RuntimeError(
                f"Failed to send command to plugin {self.plugin_id} ({error_context}): {e}"
            ) from e

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            if result["success"]:
                return result["data"]
            raise PluginExecutionError(
                self.plugin_id, error_context, result.get("error", "Unknown error"),
            )
        except asyncio.TimeoutError:
            self.logger.error(
                "Plugin {} {} timed out after {}s, req_id={}",
                self.plugin_id, error_context, timeout, req_id,
            )
            async def _cleanup():
                await asyncio.sleep(2.0)
                self._pending_futures.pop(req_id, None)
            ct = asyncio.create_task(_cleanup())
            self._background_tasks.add(ct)
            ct.add_done_callback(self._background_tasks.discard)
            raise TimeoutError(f"{error_context} execution timed out after {timeout}s") from None

    async def trigger(self, entry_id: str, args: dict, timeout: float = PLUGIN_TRIGGER_TIMEOUT) -> Any:
        req_id = str(uuid.uuid4())
        self.logger.debug(
            "[CommManager] TRIGGER plugin_id={}, entry_id={}, req_id={}",
            self.plugin_id, entry_id, req_id,
        )
        msg = {"type": "TRIGGER", "req_id": req_id, "entry_id": entry_id, "args": args}
        return await self._send_command_and_wait(req_id, msg, timeout, f"entry {entry_id}")

    async def trigger_custom_event(
        self,
        event_type: str,
        event_id: str,
        args: dict,
        timeout: float = PLUGIN_TRIGGER_TIMEOUT,
    ) -> Any:
        req_id = str(uuid.uuid4())
        self.logger.info(
            "[CommManager] TRIGGER_CUSTOM plugin_id={}, {}.{}, req_id={}",
            self.plugin_id, event_type, event_id, req_id,
        )
        msg = {
            "type": "TRIGGER_CUSTOM",
            "req_id": req_id,
            "event_type": event_type,
            "event_id": event_id,
            "args": args,
        }
        return await self._send_command_and_wait(
            req_id, msg, timeout, f"custom event {event_type}.{event_id}",
        )

    async def send_freeze_command(self, timeout: float = PLUGIN_TRIGGER_TIMEOUT) -> Dict[str, Any]:
        req_id = str(uuid.uuid4())
        self.logger.info("[CommManager] FREEZE plugin_id={}, req_id={}", self.plugin_id, req_id)
        try:
            result = await self._send_command_and_wait(
                req_id, {"type": "FREEZE", "req_id": req_id}, timeout, "freeze",
            )
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}
        if not isinstance(result, dict):
            return {"success": True, "data": result, "error": None}
        if "success" in result:
            return result
        if "error" in result:
            return {"success": False, "data": result.get("data"), "error": result.get("error")}
        return {"success": True, "data": result, "error": None}

    async def send_cancel_run(self, run_id: str) -> None:
        try:
            await self.transport.send_command({"type": "CANCEL_RUN", "run_id": str(run_id)})
            self.logger.debug("Sent CANCEL_RUN for run_id={} to plugin {}", run_id, self.plugin_id)
        except Exception as e:
            self.logger.warning("Failed to send CANCEL_RUN to plugin {}: {}", self.plugin_id, e)

    async def push_bus_change(
        self, *, sub_id: str, bus: str, op: str, delta: Dict[str, Any] | None = None,
    ) -> None:
        msg = {
            "type": "BUS_CHANGE",
            "sub_id": str(sub_id),
            "bus": str(bus),
            "op": str(op),
            "delta": dict(delta or {}),
        }
        try:
            await self.transport.send_command(msg)
        except Exception as e:
            raise RuntimeError(f"Failed to push BUS_CHANGE to plugin {self.plugin_id}: {e}") from e

    async def send_stop_command(self, timeout: float = 0.5) -> None:
        try:
            await asyncio.wait_for(self.transport.send_command({"type": "STOP"}), timeout=timeout)
            self.logger.debug("Sent STOP to plugin {}", self.plugin_id)
        except asyncio.TimeoutError:
            self.logger.warning("Sending STOP to plugin {} timed out after {}s", self.plugin_id, timeout)
        except Exception as e:
            self.logger.warning("Failed to send STOP to plugin {}: {}", self.plugin_id, e)

    async def send_plugin_response(self, msg: dict) -> None:
        """Forward a plugin-to-plugin response to the child via the downlink."""
        try:
            await self.transport.send_response(msg)
        except Exception as e:
            self.logger.warning("Failed to send plugin response to {}: {}", self.plugin_id, e)

    # ── status drain (sync, called from routes) ──────────────────

    def get_status_messages(self, max_count: int | None = None) -> list[Dict[str, Any]]:
        from plugin.settings import STATUS_MESSAGE_DEFAULT_MAX_COUNT
        if max_count is None:
            max_count = STATUS_MESSAGE_DEFAULT_MAX_COUNT
        if not hasattr(self, "_status_buffer"):
            return []
        msgs: list[Dict[str, Any]] = []
        for _ in range(max_count):
            try:
                msgs.append(self._status_buffer.get_nowait())
            except asyncio.QueueEmpty:
                break
        return msgs

    # ── uplink consumer ──────────────────────────────────────────

    _MESSAGE_ROUTING: ClassVar[Dict[str, str]] = {
        "ENTRY_UPDATE": "_handle_entry_update",
        "STATIC_UI_REGISTER": "_handle_static_ui_register",
    }

    async def _consume_uplink(self) -> None:
        """Single consumer that reads **all** uplink messages and routes them."""
        self._ensure_shutdown_event()
        se = self._shutdown_event
        if se is None:
            return

        poll_ms = int(QUEUE_GET_TIMEOUT * 1000)
        self._status_buffer: asyncio.Queue = asyncio.Queue(maxsize=512)

        while not se.is_set():
            try:
                result = await self.transport.recv(timeout_ms=poll_ms)
                if result is None:
                    continue
                ch, payload = result

                if ch == CH_RES:
                    self._dispatch_result(payload)
                elif ch == CH_STS:
                    try:
                        self._status_buffer.put_nowait(payload)
                    except asyncio.QueueFull:
                        try:
                            self._status_buffer.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        try:
                            self._status_buffer.put_nowait(payload)
                        except asyncio.QueueFull:
                            pass
                elif ch == CH_MSG:
                    await self._route_message(payload)
                elif ch == CH_COMM:
                    await self._route_comm(payload)
                else:
                    self.logger.debug("Unknown uplink channel '{}' from plugin {}", ch, self.plugin_id)

            except asyncio.CancelledError:
                break
            except Exception:
                if not se.is_set():
                    self.logger.exception("Error in uplink consumer for plugin {}", self.plugin_id)
                await asyncio.sleep(MESSAGE_CONSUMER_SLEEP_INTERVAL)

    # ── result dispatch ──────────────────────────────────────────

    def _dispatch_result(self, res: dict) -> None:
        req_id = res.get("req_id")
        if not req_id:
            self.logger.warning("Result without req_id from plugin {}", self.plugin_id)
            return
        fut = self._pending_futures.get(req_id)
        if fut:
            if not fut.done():
                fut.set_result(res)
            self._pending_futures.pop(req_id, None)
        else:
            self.logger.warning(
                "Result for unknown req_id {} from plugin {}. Known: {}",
                req_id, self.plugin_id, list(self._pending_futures.keys())[:5],
            )

    # ── message routing ──────────────────────────────────────────

    async def _route_message(self, msg: dict) -> None:
        handler_name = self._MESSAGE_ROUTING.get(msg.get("type", ""))
        if handler_name:
            await getattr(self, handler_name)(msg)
            return
        se = self._shutdown_event
        if se and se.is_set():
            return
        await self._forward_message(msg)

    async def _forward_message(self, msg: Dict[str, Any]) -> None:
        if not self._message_target_queue:
            return

        if isinstance(msg, dict) and not msg.get("_bus_stored"):
            try:
                from plugin.core.state import state
                msg = dict(msg)
                if not isinstance(msg.get("message_id"), str) or not msg.get("message_id"):
                    msg["message_id"] = str(uuid.uuid4())
                if not isinstance(msg.get("time"), str) or not msg.get("time"):
                    msg["time"] = now_iso()
                msg["_bus_stored"] = True
                state.append_message_record(msg)
            except Exception:
                self.logger.debug("Failed to store message for plugin {}", self.plugin_id, exc_info=True)

        try:
            await asyncio.wait_for(self._message_target_queue.put(msg), timeout=0.05)
        except asyncio.TimeoutError:
            return

        if PLUGIN_LOG_MESSAGE_FORWARD:
            log_content = _format_log_text(msg.get("content", ""))
            window = PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS
            if window and window > 0:
                now_ts = time.monotonic()
                key = (
                    self.plugin_id,
                    msg.get("source", "unknown"),
                    msg.get("priority", 0),
                    msg.get("description", ""),
                    log_content,
                )
                last_key = self._last_forward_log_key
                last_ts = self._last_forward_log_time
                if last_key == key and last_ts > 0.0 and (now_ts - last_ts) <= window:
                    self._last_forward_log_repeat_count += 1
                    return
                if last_key is not None and self._last_forward_log_repeat_count > 0:
                    self.logger.info(
                        "[MESSAGE FORWARD] (suppressed {} duplicates for Plugin: {} | Source: {} | Priority: {} | Description: {})",
                        self._last_forward_log_repeat_count,
                        last_key[0], last_key[1], last_key[2], last_key[3],
                    )
                self._last_forward_log_key = key
                self._last_forward_log_time = now_ts
                self._last_forward_log_repeat_count = 0

            self.logger.info(
                "[MESSAGE FORWARD] Plugin: {} | Source: {} | Priority: {} | Description: {} | Content: {}",
                self.plugin_id,
                msg.get("source", "unknown"),
                msg.get("priority", 0),
                msg.get("description", ""),
                log_content,
            )

    # ── plugin-to-plugin comm routing ────────────────────────────

    async def _route_comm(self, msg: dict) -> None:
        """Forward a plugin-to-plugin request to the central comm queue."""
        try:
            from plugin.core.state import state
            comm_queue = state.plugin_comm_queue
            if comm_queue is not None:
                await comm_queue.put(msg)
        except Exception as e:
            self.logger.warning("Failed to route comm message from plugin {}: {}", self.plugin_id, e)

    # ── ENTRY_UPDATE / STATIC_UI_REGISTER handlers ───────────────

    async def _handle_entry_update(self, msg: Dict[str, Any]) -> None:
        try:
            from plugin.core.state import state
            from plugin._types.events import EventMeta, EventHandler

            action = msg.get("action")
            entry_id = msg.get("entry_id")
            plugin_id = self.plugin_id
            incoming_pid = msg.get("plugin_id")
            if incoming_pid and incoming_pid != self.plugin_id:
                self.logger.warning(
                    "ENTRY_UPDATE plugin_id mismatch: expected={}, got={}",
                    self.plugin_id, incoming_pid,
                )
                return
            meta_dict = msg.get("meta")

            if not entry_id:
                self.logger.warning("ENTRY_UPDATE missing entry_id: {}", msg)
                return

            self.logger.info("Processing ENTRY_UPDATE: action={}, entry_id={}, plugin_id={}", action, entry_id, plugin_id)

            if action == "register":
                if not meta_dict:
                    self.logger.warning("ENTRY_UPDATE register missing meta: {}", msg)
                    return
                event_meta = EventMeta(
                    event_type="plugin_entry",
                    id=meta_dict.get("id", entry_id),
                    name=meta_dict.get("name", entry_id),
                    description=meta_dict.get("description", ""),
                    input_schema=meta_dict.get("input_schema"),
                    kind=meta_dict.get("kind", "action"),
                    auto_start=meta_dict.get("auto_start", False),
                    enabled=meta_dict.get("enabled", True),
                    dynamic=True,
                    metadata={"_dynamic": True, "_registered_via_ipc": True},
                )
                handler = EventHandler(
                    event_type="plugin_entry",
                    id=entry_id,
                    meta=event_meta,
                    handler=None,
                    plugin_id=plugin_id,
                    is_dynamic=True,
                )
                state.register_event_handler(plugin_id, handler)
                self.logger.info("Dynamic entry registered: {} for plugin {}", entry_id, plugin_id)
            elif action == "unregister":
                state.unregister_event_handler(plugin_id, entry_id)
                self.logger.info("Dynamic entry unregistered: {} for plugin {}", entry_id, plugin_id)
            else:
                self.logger.warning("Unknown ENTRY_UPDATE action: {}", action)
        except Exception:
            self.logger.exception("Failed to handle ENTRY_UPDATE")

    async def _handle_static_ui_register(self, msg: Dict[str, Any]) -> None:
        try:
            from plugin.core.state import state
            plugin_id = self.plugin_id
            config = msg.get("config")
            if not config:
                self.logger.warning("STATIC_UI_REGISTER missing config from plugin {}", plugin_id)
                return
            self.logger.info("Processing STATIC_UI_REGISTER: plugin_id={}", plugin_id)
            with state.acquire_plugins_write_lock():
                plugin_meta = state.plugins.get(plugin_id)
                if isinstance(plugin_meta, dict):
                    plugin_meta["static_ui_config"] = config
                    state.plugins[plugin_id] = plugin_meta
                    self.logger.info("Static UI registered for plugin {}: {}", plugin_id, config.get("directory"))
                else:
                    self.logger.warning("Plugin {} not found in state.plugins", plugin_id)
        except Exception:
            self.logger.exception("Failed to handle STATIC_UI_REGISTER")
