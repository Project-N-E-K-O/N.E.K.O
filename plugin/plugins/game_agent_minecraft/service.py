"""GameAgentService — wires the WebSocket client to the LLM session.

Responsibilities split off the plugin facade so it stays testable:

1. Hold the cross-callback state (pending tool call, log/screenshot
   caches, task-finished signal).
2. Translate raw agent-server frames into push_message v2 payloads:
   * screenshots → image parts on the realtime stream
     (``ai_behavior="read"``)
   * task_finished → wakes the pending ``minecraft_task`` handler so it
     can return the result to the LLM
3. Run the autonomous "system prompt" loop that periodically nudges the
   LLM with the latest game state when there's nothing else for it to
   talk about.

The original integration in ``main_logic/core.py`` (commit ``bca0c5f3``,
later abandoned) baked all of this directly into the realtime client
class. This module keeps every game-agent concern inside the plugin so
adding/removing the feature is a pure plugin install / uninstall.

Async semantics
---------------
``minecraft_task`` is fundamentally async on the agent side: the LLM
calls the tool, we send the task to the agent server, and the result
arrives later as a separate ``task_finished`` frame. The SDK's
``@llm_tool`` contract is that the handler returns a value when done.
We bridge the two by having the handler block on an :class:`asyncio.Event`
that the WebSocket callback sets when the result comes in. ``timeout``
on the decorator caps the wait so a wedged agent server doesn't pin the
LLM forever.
"""
from __future__ import annotations

import asyncio
import base64
import collections
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from .client import GameAgentClient

# Strip ANSI colour escapes from agent log lines before relaying to the
# LLM — we don't want VT100 noise in the model's context window.
_ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


@dataclass
class PendingTask:
    """State for a single in-flight ``minecraft_task`` invocation.

    The handler creates one when the LLM picks the tool, blocks on
    ``event``, and reads ``result`` after waking.
    """

    task_text: str
    event: asyncio.Event
    start_time: float
    # Filled in by the WebSocket callback (or by overwrite/timeout
    # paths) right before ``event`` is set.
    result: Dict[str, Any] = field(default_factory=dict)


class GameAgentService:
    """Per-plugin instance state + WebSocket lifecycle + autonomous loop.

    The plugin facade injects two callables we don't construct ourselves:

    * ``push_message_fn`` — bound to ``ctx.push_message`` so we can
      forward image/text payloads upward without taking a hard dep on
      the SDK base class.
    * ``logger`` — the per-plugin loguru logger, already prefixed with
      the plugin id.

    Both are passed in at ``__init__`` time so unit tests can swap in
    fakes.
    """

    def __init__(
        self,
        *,
        logger: Any,
        push_message_fn: Callable[..., Any],
    ) -> None:
        self.logger = logger
        self._push_message = push_message_fn

        # Configuration (filled in by ``configure``).
        self._ws_url: str = "ws://localhost:48909"
        self._reconnect_interval: float = 5.0
        self._task_timeout: float = 25.0
        self._system_prompt_interval: float = 5.0
        self._skip_when_busy: bool = True
        self._stream_screenshots: bool = True
        self._screenshot_cache_size: int = 3

        # WebSocket lifecycle
        self._client: Optional[GameAgentClient] = None
        self._client_task: Optional[asyncio.Task] = None
        self._system_loop_task: Optional[asyncio.Task] = None

        # Cross-callback state
        self._pending: Optional[PendingTask] = None
        self._pending_lock = asyncio.Lock()
        # Counter of tasks we abandoned without an acknowledged
        # ``task_finished`` (timeout / overwrite / shutdown). Used to
        # filter out stale frames from the agent: the protocol carries
        # no task id, so without this a delayed completion for an old
        # task would be (incorrectly) attributed to whatever task is
        # currently pending. Drained FIFO in ``_on_task_finished``.
        self._stale_task_finishes_to_drop: int = 0
        # Bounded ring buffer of agent log lines. Without a cap this
        # would grow without bound when the autonomous loop is gated
        # off (e.g. ``skip_system_prompt_if_busy=True`` and a long
        # task is in flight); the agent emits log lines continuously
        # and we'd never drain. ``deque(maxlen=...)`` drops oldest on
        # overflow which is fine — the LLM only needs recent context.
        self._log_cache: collections.deque[str] = collections.deque(maxlen=200)
        # Bounded ring buffer of (image_bytes, mime). We carry the mime
        # alongside the bytes because the JPEG→PNG conversion in
        # ``_on_screenshot`` can fall through to "ship as-is" on Pillow
        # failure; replaying that frame in the autonomous loop with
        # ``image/png`` would mis-tag JPEG bytes and may confuse the
        # downstream image part handler. Discarding old frames is fine
        # — the autonomous loop only ever sends "the latest few".
        self._screenshot_cache: collections.deque[tuple[bytes, str]] = collections.deque(
            maxlen=3
        )
        self._task_finished: bool = True

        # Pacing state for the autonomous loop
        self._last_system_prompt_time: float = 0.0

    # ------------------------------------------------------------------
    # Configuration / lifecycle
    # ------------------------------------------------------------------

    # Keys whose changes only take effect on the next ``start()`` —
    # the ``GameAgentClient`` constructor copies them once. Tracking
    # them lets ``reload_config_live`` decide whether a stop+start
    # cycle is needed to make the new value real.
    _TRANSPORT_KEYS = ("_ws_url", "_reconnect_interval")

    def configure(self, cfg: Dict[str, Any]) -> None:
        """Read the ``[game_agent]`` section of ``plugin.toml`` (passed
        in by the plugin facade) and update local config. Defensive
        about missing/wrong types so a partial / hand-edited config
        doesn't crash startup.

        Note: when called after ``start()``, transport-affecting keys
        (``ws_url``, ``reconnect_interval_seconds``) update internal
        state but do **not** swap the live ``GameAgentClient`` —
        that's a transport-level identity change that needs a stop+
        start cycle. Use :meth:`reload_config_live` if you want the
        new transport values to take effect immediately.
        """
        def _f(key: str, default: float) -> float:
            v = cfg.get(key, default)
            try:
                return float(v)
            except (TypeError, ValueError):
                return float(default)

        def _i(key: str, default: int) -> int:
            v = cfg.get(key, default)
            try:
                return int(v)
            except (TypeError, ValueError):
                return int(default)

        def _b(key: str, default: bool) -> bool:
            v = cfg.get(key, default)
            return bool(v) if isinstance(v, (bool, int)) else default

        url = cfg.get("ws_url")
        if isinstance(url, str) and url:
            self._ws_url = url
        self._reconnect_interval = max(0.5, _f("reconnect_interval_seconds", 5.0))
        self._task_timeout = max(1.0, _f("task_timeout_seconds", 25.0))
        self._system_prompt_interval = max(1.0, _f("system_prompt_interval_seconds", 5.0))
        self._skip_when_busy = _b("skip_system_prompt_if_busy", True)
        self._stream_screenshots = _b("stream_screenshots_to_llm", True)
        size = max(1, _i("screenshot_cache_size", 3))
        self._screenshot_cache_size = size
        # ``deque(maxlen=...)`` is immutable post-construction, so swap.
        if self._screenshot_cache.maxlen != size:
            self._screenshot_cache = collections.deque(
                self._screenshot_cache, maxlen=size
            )

    async def reload_config_live(self, cfg: Dict[str, Any]) -> bool:
        """Apply a config update at runtime.

        Pure-data keys (timeouts, intervals, screenshot toggles) update
        in place — they're read on every loop tick / handler call.

        Transport-affecting keys (``ws_url``,
        ``reconnect_interval_seconds``) are baked into the live
        :class:`GameAgentClient` instance at construction time, so a
        change there requires a stop+start cycle to take effect. We
        capture the old values, run ``configure``, then compare; if
        they shifted and we're already running, restart the WS
        client so the new endpoint is used.

        Returns ``True`` if a transport restart actually happened.
        """
        was_running = self._client is not None
        old_ws_url = self._ws_url
        old_reconnect = self._reconnect_interval

        self.configure(cfg)

        transport_changed = (
            self._ws_url != old_ws_url
            or self._reconnect_interval != old_reconnect
        )
        if was_running and transport_changed:
            self._log_info(
                "config reload triggered transport restart "
                "(ws_url={} -> {}, reconnect={} -> {})",
                old_ws_url, self._ws_url,
                old_reconnect, self._reconnect_interval,
            )
            await self.stop()
            await self.start()
            return True
        return False

    async def start(self) -> None:
        """Spin up the WebSocket client + autonomous loop. Idempotent —
        a second call after start() noops if already running."""
        if self._client is not None:
            return

        self._client = GameAgentClient(
            uri=self._ws_url,
            on_log=self._on_log,
            on_screenshot=self._on_screenshot,
            on_task_finished=self._on_task_finished,
            reconnect_interval=self._reconnect_interval,
            logger=self.logger,
        )
        self._client_task = asyncio.create_task(
            self._client.start(), name="game_agent_minecraft.ws_client"
        )
        self._system_loop_task = asyncio.create_task(
            self._system_prompt_loop(),
            name="game_agent_minecraft.system_loop",
        )
        self._log_info("started, ws_url={}", self._ws_url)

    async def stop(self) -> None:
        """Tear down WS client + loop, and resolve any pending tool call
        with a "shutdown" status so the @llm_tool handler doesn't hang
        until its timeout expires."""
        # Drain pending handler first so it returns before we kill the
        # transport that would feed its event.
        async with self._pending_lock:
            if self._pending is not None:
                self._pending.result = {
                    "status": "interrupted",
                    "query": self._pending.task_text,
                    "reason": "Game agent plugin shutting down.",
                }
                self._pending.event.set()
                self._pending = None
                # We abandoned this task without an ack — bump the
                # drop counter so a delayed ``task_finished`` (which
                # might still hit us if ``stop()`` is part of a
                # ``reload_config_live`` cycle and the same agent
                # buffers and redelivers the frame after reconnect)
                # doesn't bind to whatever new task the LLM picks
                # next.
                self._stale_task_finishes_to_drop += 1
            # Note: do NOT zero the counter — preserve any existing
            # debt from prior timeouts/overwrites that may still have
            # frames in flight from before this stop().

        if self._system_loop_task is not None:
            self._system_loop_task.cancel()
            try:
                await self._system_loop_task
            except (asyncio.CancelledError, Exception):
                # We just cancelled it — CancelledError is the
                # expected shape; any other Exception means the loop
                # raised on its way out (already logged inside the
                # loop), nothing more to do here on shutdown.
                pass
            self._system_loop_task = None

        if self._client is not None:
            await self._client.stop()
            self._client = None

        if self._client_task is not None:
            self._client_task.cancel()
            try:
                await self._client_task
            except (asyncio.CancelledError, Exception):
                # Same as above — cancellation we asked for, or a
                # transport failure already surfaced via the WS
                # client's own error logging.
                pass
            self._client_task = None

        self._log_cache.clear()
        self._screenshot_cache.clear()
        self._task_finished = True
        self._log_info("stopped")

    # ------------------------------------------------------------------
    # @llm_tool handler — the LLM-visible side
    # ------------------------------------------------------------------

    async def execute_minecraft_task(
        self, *, task: str, overwrite: bool = False
    ) -> Dict[str, Any]:
        """Implementation of the ``minecraft_task`` LLM tool.

        Returns a dict the SDK callback route forwards to the model.
        Two shapes of "non-error completion":

        * ``{"status": "ok",         "query": ...}``      — agent finished
        * ``{"status": "timeout",    "query": ..., "reason": ...}`` — capped
        * ``{"status": "interrupted","query": ..., "reason": ...}`` — overwritten / shutdown
        * ``{"result": "busy",       "currently_executing": ..., "hint": ...}`` — refused

        Real failures (agent disconnected, etc.) come back as
        ``{"output": ..., "is_error": True, "error": "..."}`` so the LLM
        can adapt rather than hallucinate success.
        """
        if not isinstance(task, str) or not task.strip():
            return {
                "output": {"error": "task must be a non-empty string"},
                "is_error": True,
                "error": "INVALID_TASK",
            }
        if self._client is None:
            return {
                "output": {"error": "plugin is not started yet"},
                "is_error": True,
                "error": "NOT_STARTED",
            }

        async with self._pending_lock:
            if self._pending is not None:
                if not overwrite:
                    # Refuse without disturbing the in-flight task.
                    return {
                        "result": "busy",
                        "currently_executing": self._pending.task_text,
                        "hint": "Set overwrite=True to interrupt the current task.",
                    }
                # Wake the old handler with an "interrupted" verdict
                # before claiming the slot for the new task. The agent
                # may still send a delayed ``task_finished`` for the
                # old task, so flag one frame to drop when it arrives.
                self._log_warning(
                    "overwriting task: {} -> {}", self._pending.task_text, task
                )
                self._pending.result = {
                    "status": "interrupted",
                    "query": self._pending.task_text,
                    "reason": "Overwritten by a new task.",
                }
                self._pending.event.set()
                self._pending = None
                self._stale_task_finishes_to_drop += 1

            # The handler keeps a *local* reference to its own
            # PendingTask; ``self._pending`` may be reassigned (or
            # cleared) by overwrite/shutdown paths before this handler
            # wakes up, but ``my_pending.result`` survives because
            # whoever set the event also wrote into the dataclass we
            # hold here.
            my_pending = PendingTask(
                task_text=task, event=asyncio.Event(), start_time=time.time(),
            )
            self._pending = my_pending
            self._task_finished = False

        sent = await self._client.send_task(task)
        if not sent:
            # Roll back the pending slot — the agent never accepted the
            # task, so we shouldn't keep reporting "busy" to subsequent
            # calls. Also reset ``_task_finished`` (we flipped it to
            # ``False`` above optimistically); without resetting, the
            # autonomous loop's ``skip_system_prompt_if_busy`` gate
            # would behave as if a task were still running, and the
            # "正在进行的操作" branch of the system prompt would lie.
            async with self._pending_lock:
                if self._pending is my_pending:
                    self._pending = None
                    self._task_finished = True
            return {
                "output": {
                    "error": "agent server is not connected",
                    "query": task,
                },
                "is_error": True,
                "error": "AGENT_DISCONNECTED",
            }

        try:
            await asyncio.wait_for(my_pending.event.wait(), timeout=self._task_timeout)
        except asyncio.TimeoutError:
            async with self._pending_lock:
                if self._pending is my_pending:
                    self._pending = None
                    self._stale_task_finishes_to_drop += 1
            self._log_info("task timed out: {}", task[:80])
            return {
                "status": "timeout",
                "query": task,
                "reason": f"Not finished within {self._task_timeout:.0f}s.",
            }
        except asyncio.CancelledError:
            # The outer SDK ``@llm_tool(timeout=...)`` wrapper or a
            # plugin shutdown may cancel this task while we're still
            # waiting. Without this branch ``self._pending`` would
            # stick around forever, making subsequent calls return
            # "busy" against an event nothing will ever set. Clean
            # the slot first, then re-raise so the cancellation
            # propagates as the SDK expects.
            async with self._pending_lock:
                if self._pending is my_pending:
                    self._pending = None
                    self._stale_task_finishes_to_drop += 1
            raise

        # Read the verdict the callback (or overwrite/shutdown path)
        # wrote into ``my_pending.result`` before setting the event.
        # Note: we read from the *local* PendingTask, not
        # ``self._pending``, because the latter may already have been
        # reassigned to a different task by an overwrite call that woke
        # us up.
        async with self._pending_lock:
            if self._pending is my_pending:
                self._pending = None

        return my_pending.result or {"status": "ok", "query": task}

    # ------------------------------------------------------------------
    # WebSocket inbound callbacks — invoked from the WS listener task
    # ------------------------------------------------------------------

    async def _on_log(self, text: str) -> None:
        text_strip = text.strip() if isinstance(text, str) else ""
        if not text_strip:
            return
        self._log_cache.append(text_strip)

        # The original integration sniffed log strings to track agent
        # state because the protocol didn't have explicit "task running"
        # frames. Keep the same heuristics so existing agent servers
        # work without protocol changes.
        if "task run ended" in text_strip:
            self._task_finished = True
        elif "action selection" in text_strip:
            self._task_finished = False
        elif text_strip == "Connection lost and re-established.":
            self._task_finished = True

    async def _on_screenshot(self, payload: str, encoding: str) -> None:
        """Decode a base64 screenshot, convert JPEG→PNG when needed, and
        either stream it into the realtime LLM session immediately or
        cache it for the next autonomous-prompt burst."""
        try:
            stripped = payload
            if stripped.startswith("data:"):
                comma = stripped.find(",")
                if comma != -1:
                    stripped = stripped[comma + 1:]
            img_bytes = base64.b64decode(stripped, validate=False)
        except Exception as exc:
            self._log_error(
                "screenshot base64 decode failed: {}: {}",
                type(exc).__name__, exc,
            )
            return

        mime = "image/png"
        enc_lower = (encoding or "").lower()
        if "jpeg" in enc_lower or "jpg" in enc_lower:
            # Gemini's realtime media input prefers PNG; convert here so
            # downstream code can be format-agnostic. Pillow is already
            # a transitive project dep (used by avatar/MMD pipelines).
            try:
                from PIL import Image
                import io

                with Image.open(io.BytesIO(img_bytes)) as im:
                    buf = io.BytesIO()
                    im.save(buf, format="PNG")
                    img_bytes = buf.getvalue()
            except Exception as exc:
                # Fall through with the original JPEG bytes — main_server
                # won't choke if the mime type matches.
                self._log_warning(
                    "JPEG→PNG convert failed, sending as-is: {}: {}",
                    type(exc).__name__, exc,
                )
                mime = "image/jpeg"

        self._screenshot_cache.append((img_bytes, mime))

        # Stream immediately into the realtime LLM session. push_message
        # v2 with ``ai_behavior="read"`` translates downstream into
        # ``session.stream_image`` (see ``main_server.py`` proactive
        # branch + the v2 changelog for the wire flow).
        if self._stream_screenshots:
            try:
                self._push_message(
                    source="game_agent_minecraft",
                    visibility=[],
                    ai_behavior="read",
                    parts=[{"type": "image", "data": img_bytes, "mime": mime}],
                    priority=3,
                )
            except Exception as exc:
                self._log_error(
                    "push_message screenshot failed: {}: {}",
                    type(exc).__name__, exc,
                )

    async def _on_task_finished(self, data: Dict[str, Any]) -> None:
        text = str(
            data.get("text") or data.get("data") or data.get("message") or ""
        )
        status = str(data.get("status") or "ok")
        self._log_info("task_finished: status={}, text={}", status, text[:80])
        if text:
            self._log_cache.append(text)

        async with self._pending_lock:
            # The agent server's protocol has no task ID, so a delayed
            # ``task_finished`` for an old (timed-out / overwritten /
            # shutdown-cancelled) task is indistinguishable from a
            # fresh one for the current task. We assume the agent
            # emits frames in completion order and drain them FIFO:
            # for every task we abandoned without an ack, swallow one
            # incoming frame.
            if self._stale_task_finishes_to_drop > 0:
                self._stale_task_finishes_to_drop -= 1
                self._log_info(
                    "dropped stale task_finished (status={}, drops_remaining={})",
                    status, self._stale_task_finishes_to_drop,
                )
                # Don't touch ``_task_finished`` here — flipping it
                # would leak the *old* task's completion state into
                # the *new* (still in-flight) task, breaking the
                # autonomous-loop's busy gate and the system prompt's
                # "正在进行中 vs 已完成" branch.
                return
            if self._pending is None:
                # Stray task_finished (e.g. from agent restart) — nothing
                # to wake. Update the flag anyway so the autonomous
                # loop knows the agent is idle and can resume nudging.
                self._task_finished = True
                return
            self._pending.result = {
                "status": status,
                "query": self._pending.task_text,
            }
            self._pending.event.set()
            # Now safe to flip — this frame is being applied to the
            # current pending task.
            self._task_finished = True
            # Don't None-out here; ``execute_minecraft_task`` does that
            # after it reads ``result`` so we don't race the read.

    # ------------------------------------------------------------------
    # Autonomous system-prompt loop
    # ------------------------------------------------------------------

    async def _system_prompt_loop(self) -> None:
        """Periodically nudge the LLM with the latest game state.

        We don't try to detect "user/model is currently speaking" from
        inside the plugin — main_server's proactive_message handler
        already gates timing on its end. The plugin's pacing here is
        only about not flooding main_server with redundant wake-ups,
        not about real-time conversation politeness.
        """
        try:
            while True:
                # 0.5s tick keeps us responsive to ``stop()`` without
                # busy-looping.
                await asyncio.sleep(0.5)

                now = time.time()
                if now - self._last_system_prompt_time < self._system_prompt_interval:
                    continue
                if self._skip_when_busy and self._pending is not None and not self._task_finished:
                    # Avoid stacking prompts on top of an in-flight tool
                    # call — the LLM is already committed to the result.
                    continue
                if not self._log_cache and not self._screenshot_cache and self._task_finished:
                    # Nothing happened recently; don't poke the LLM.
                    continue

                await self._fire_system_prompt()
                self._last_system_prompt_time = time.time()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._log_error(
                "system prompt loop failed: {}: {}", type(exc).__name__, exc,
            )

    async def _fire_system_prompt(self) -> None:
        """Build + push the autonomous nudge.

        Body shape mirrors the original integration so prompt-engineering
        carries over: a "GAME_SYSTEM" header, the recent agent log
        snippet, and either a "task done — pick the next one" or a
        "task running — comment if you like" tail.
        """
        log_text = ""
        if self._log_cache:
            log_text = _ANSI_RE.sub("", "\n".join(self._log_cache))
            self._log_cache.clear()

        sections: list[str] = []
        if self._pending is not None:
            sections.append(f"正在进行的操作: {self._pending.task_text}")
        if log_text:
            sections.append(f"先前操作的详细日志:\n---\n{log_text}\n---")
        if self._task_finished:
            sections.append(
                "你先前的操作已经完成了。请使用minecraft_task，但不要说出来。"
            )
        else:
            sections.append("你先前的操作仍在进行中，你可以选择性解说。")
        prompt_text = "GAME_SYSTEM | " + "\n".join(sections)

        # Build the parts list: cached screenshots first (so the LLM
        # has visual context when it reads the prompt), then the
        # GAME_SYSTEM text. Drain the cache after building the parts
        # list so a flake on push_message doesn't re-send the same
        # screenshots forever.
        parts: list[Dict[str, Any]] = []
        screenshots = list(self._screenshot_cache)
        self._screenshot_cache.clear()
        for img_bytes, img_mime in screenshots:
            # Preserve the per-frame mime — see ``_screenshot_cache``
            # field comment for why this isn't always image/png.
            parts.append({"type": "image", "data": img_bytes, "mime": img_mime})
        parts.append({"type": "text", "text": prompt_text})

        try:
            self._push_message(
                source="game_agent_minecraft",
                visibility=[],
                ai_behavior="respond",
                parts=parts,
                priority=4,
            )
        except Exception as exc:
            self._log_error(
                "system prompt push_message failed: {}: {}",
                type(exc).__name__, exc,
            )

    # ------------------------------------------------------------------
    # Diagnostics — surfaced via the plugin's status entries
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        connected = bool(self._client and self._client.is_connected)
        return {
            "ws_url": self._ws_url,
            "connected": connected,
            "task_finished": self._task_finished,
            "pending_task": self._pending.task_text if self._pending else None,
            "log_cache_size": len(self._log_cache),
            "screenshot_cache_size": len(self._screenshot_cache),
        }

    # ------------------------------------------------------------------
    # Logging helpers — silently no-op when no logger is supplied.
    # Each helper guards its emit because the SDK's loguru-based logger
    # can transiently fail (file rotation mid-write, etc.); we never
    # want a diagnostic log line to surface as a real error.
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


__all__ = ["GameAgentService", "PendingTask"]
