"""WebSocket client for the local Minecraft agent server.

The agent server speaks a small JSON protocol:

* **client → server** ``{"type": "task", "task": "<goal>"}``
* **server → client** ``{"type": "log",        "text": "..."}``
* **server → client** ``{"type": "screenshot", "image": "<base64>", "encoding": "png"|"jpeg"}``
* **server → client** ``{"type": "task_finished", "status": "ok", "text": "..."}``
* **server → client** ``{"type": "agent_status", ...}``  # informational, ignored by callbacks

Why a long-lived WebSocket instead of polling HTTP: screenshot frames can
arrive at >1Hz, and the handshake cost of HTTP per frame would dominate
latency. The plugin keeps one persistent connection and auto-reconnects
when the agent restarts.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Optional

import websockets
from websockets import exceptions as ws_exceptions

# Callback signatures.  All callbacks are async — the plugin's service
# layer dispatches IPC notifications inside them.
OnLog = Callable[[str], Awaitable[None]]
OnScreenshot = Callable[[str, str], Awaitable[None]]  # (base64_payload, encoding_hint)
OnTaskFinished = Callable[[dict[str, Any]], Awaitable[None]]


class GameAgentClient:
    """Persistent WebSocket bridge to the agent server.

    Lifecycle: ``await start()`` runs forever (auto-reconnect loop) until
    ``await stop()`` is called. Outbound traffic uses ``await
    send_task(text)`` which returns ``False`` if the socket is currently
    not connected (caller decides how to surface that to the LLM).
    """

    def __init__(
        self,
        uri: str,
        *,
        on_log: Optional[OnLog] = None,
        on_screenshot: Optional[OnScreenshot] = None,
        on_task_finished: Optional[OnTaskFinished] = None,
        reconnect_interval: float = 5.0,
        logger: Any = None,
    ):
        self.uri = uri
        self.on_log = on_log
        self.on_screenshot = on_screenshot
        self.on_task_finished = on_task_finished
        self.reconnect_interval = reconnect_interval
        # Plugin SDK injects a per-plugin loguru logger; fall back to a
        # noop when running outside that environment (unit tests).
        self.logger = logger

        self._ws: Optional[Any] = None
        self._running = False
        self.is_connected: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Block until ``stop()`` — reconnects on transient failures.

        Pattern: outer loop owns the reconnect interval; inner ``_listen``
        loops on incoming frames until the socket closes. Any exception
        below the cancellation level is logged and we sleep before
        retrying so we don't busy-loop against a dead server.
        """
        self._running = True
        while self._running:
            try:
                self._ws = await websockets.connect(
                    self.uri,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=3,
                    # Screenshot frames can be hundreds of KB once
                    # base64-encoded; disable the default 1 MiB cap.
                    max_size=None,
                )
                self.is_connected = True
                self._log_info("connected to {}", self.uri)
                await self._listen()
                # ``_listen`` swallows ConnectionClosed internally and
                # returns normally — without an explicit sleep here the
                # outer loop reconnects immediately, which against an
                # agent that's repeatedly dropping the socket becomes a
                # tight loop. Mirror the exception-path reconnect
                # interval for the clean-close path too.
                self.is_connected = False
                if self._running:
                    self._log_info(
                        "listen exited cleanly, reconnecting in {:.1f}s",
                        self.reconnect_interval,
                    )
                    try:
                        await asyncio.sleep(self.reconnect_interval)
                    except asyncio.CancelledError:
                        break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.is_connected = False
                if self._running:
                    self._log_warning(
                        "connection failed ({}: {}), retrying in {:.1f}s",
                        type(exc).__name__, exc, self.reconnect_interval,
                    )
                    try:
                        await asyncio.sleep(self.reconnect_interval)
                    except asyncio.CancelledError:
                        break

    async def stop(self) -> None:
        self._running = False
        self.is_connected = False
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                # Best-effort close on shutdown — if the socket is
                # already torn down (peer hung up, OS reaped fd, ...)
                # there's nothing for us to recover; we're stopping.
                pass
        self._log_info("stopped")

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send_task(self, task: str) -> bool:
        """Push a task command. Returns ``True`` on successful send,
        ``False`` if the socket isn't connected (caller decides whether
        to retry, queue, or surface as a tool error)."""
        ws = self._ws
        if ws is None or not self.is_connected:
            self._log_warning("not connected; dropping task: {}", task[:80])
            return False
        try:
            await ws.send(json.dumps({"type": "task", "task": task}))
            self._log_info("sent task: {}", task[:120])
            return True
        except Exception as exc:
            self._log_error("failed to send task: {}: {}", type(exc).__name__, exc)
            return False

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    async def _listen(self) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            async for raw in ws:
                try:
                    data: dict = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    # Agent server should never emit non-JSON frames;
                    # drop and keep listening rather than tearing down
                    # the connection over malformed input.
                    continue

                msg_type = data.get("type", "")
                try:
                    if msg_type == "log" and self.on_log is not None:
                        text = str(
                            data.get("text")
                            or data.get("data")
                            or data.get("message")
                            or ""
                        )
                        await self.on_log(text)

                    elif msg_type == "screenshot" and self.on_screenshot is not None:
                        img_payload = data.get("image") or data.get("data") or ""
                        encoding = str(data.get("encoding") or "").lower()
                        if isinstance(img_payload, str) and img_payload:
                            await self.on_screenshot(img_payload, encoding)

                    elif msg_type == "task_finished" and self.on_task_finished is not None:
                        await self.on_task_finished(data)

                    elif msg_type == "agent_status":
                        # Informational status — the original integration
                        # logged this at debug. Keep parity.
                        self._log_debug("agent_status: {}", data)

                except Exception as cb_err:
                    # A misbehaving callback shouldn't kill the whole
                    # WebSocket loop — log and continue.
                    self._log_error(
                        "callback error for type={}: {}: {}",
                        msg_type, type(cb_err).__name__, cb_err,
                    )

        except ws_exceptions.ConnectionClosed:
            self.is_connected = False
            self._log_info("connection closed")
        except Exception as exc:
            self.is_connected = False
            self._log_error("listen error: {}: {}", type(exc).__name__, exc)

    # ------------------------------------------------------------------
    # Logging helpers — silently no-op when no logger is supplied.
    # Each helper guards its emit with try/except because the plugin
    # SDK's loguru-based logger can transiently fail (e.g. file rotation
    # mid-write) and we never want a log line to surface as a real
    # error in callers that just wanted to record diagnostics.
    # ------------------------------------------------------------------

    def _log_info(self, msg: str, *args: Any) -> None:
        if self.logger is not None:
            try:
                self.logger.info("[GameAgent] " + msg, *args)
            except Exception:
                pass  # log emission itself failed — see comment above

    def _log_warning(self, msg: str, *args: Any) -> None:
        if self.logger is not None:
            try:
                self.logger.warning("[GameAgent] " + msg, *args)
            except Exception:
                pass  # log emission itself failed — see comment above

    def _log_error(self, msg: str, *args: Any) -> None:
        if self.logger is not None:
            try:
                self.logger.error("[GameAgent] " + msg, *args)
            except Exception:
                pass  # log emission itself failed — see comment above

    def _log_debug(self, msg: str, *args: Any) -> None:
        if self.logger is not None:
            try:
                self.logger.debug("[GameAgent] " + msg, *args)
            except Exception:
                pass  # log emission itself failed — see comment above


__all__ = [
    "GameAgentClient",
    "OnLog",
    "OnScreenshot",
    "OnTaskFinished",
]
