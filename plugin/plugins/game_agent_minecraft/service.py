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
import uuid
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
    # Per-task ID we generate locally and forward to the agent on the
    # outbound ``task`` frame. If the agent echoes it on
    # ``task_finished``, we use it for explicit correlation; if not
    # (sequential agents with no concurrency) we fall back to FIFO
    # ordering on the stale-frame drop counter. See README "已知限制".
    task_id: str = ""
    # Filled in by the WebSocket callback (or by overwrite/timeout
    # paths) right before ``event`` is set.
    result: Dict[str, Any] = field(default_factory=dict)
    # True once the task text was actually sent to the agent server
    # (``client.send_task`` returned True). Drop-counter bumps for
    # abandonment paths (timeout / overwrite / cancel / stop) must
    # gate on this flag — if we never dispatched, the agent will
    # never emit a stale ``task_finished`` for this task and bumping
    # the counter would silently swallow the *next* legitimate
    # frame.
    dispatched: bool = False
    # Set by overwrite/stop when they abandon an *undispatched* task
    # (i.e. its handler is still suspended inside ``send_task``).
    # Tells the handler: "if you successfully dispatch later, bump
    # the stale-drop counter yourself — the agent will receive your
    # task and emit a frame for it that no one is waiting for, and
    # I (the abandoner) couldn't do the bump because dispatched was
    # False at my point in time."
    bump_on_late_dispatch: bool = False


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

        # Latest known body state (inventory dict from mc-agent's
        # ``task_finished`` payload). Cached so the autonomous nudge loop
        # can re-surface it every 5s as a hard grounding signal — without
        # this, the dialog LLM only sees inventory at task_finished moments
        # and tends to hallucinate items it doesn't have between those.
        self._last_inventory: Dict[str, int] = {}
        self._last_inventory_at: float = 0.0

        # Pacing state for the autonomous loop. Three independent rate
        # limiters cover the three distinct nudge purposes:
        #
        # * ``_last_system_prompt_time`` — generic "here's recent state"
        #   nudge, fires only when there's actual cache to surface
        # * ``_last_in_progress_nudge_at`` — when a task has been pending
        #   ≥10s, periodically prompt the dialog LLM to narrate what it's
        #   doing in its own voice (so the user gets ongoing engagement
        #   instead of dead silence during long actions)
        # * ``_last_keep_going_nudge_at`` — after a task finishes, if no
        #   new task is dispatched within ~5s, prompt the dialog LLM to
        #   decide the next concrete action (so the avatar doesn't stand
        #   still indefinitely waiting for {MASTER_NAME} to drive it).
        # ``_last_task_finished_at`` is the anchor for the keep-going
        # branch: time-since-finish must be in [5s, 60s] to fire — too
        # early and the cue cooldown is still active; too late and the
        # user has clearly moved on.
        self._last_system_prompt_time: float = 0.0
        self._last_in_progress_nudge_at: float = 0.0
        self._last_keep_going_nudge_at: float = 0.0
        self._last_task_finished_at: float = 0.0

        # Pacing state for inline log push (separate from autonomous nudge).
        # mc-agent emits a ``log`` frame for each chat-loop turn the in-game
        # agent takes (every chat reply, every action narration). Surfacing
        # those to the dialog LLM only via the 5s nudge loop means a turn
        # can be 5s stale — perceptibly slow in realtime conversation.
        # Inline push forwards each new log line to the dialog LLM
        # immediately, but rate-limited so a chatty agent (e.g. multi-step
        # newAction loops) doesn't spam push_message at packet rate.
        # ``_inline_log_min_interval`` is the minimum spacing between
        # consecutive inline pushes; bursts within that window are batched
        # and delivered as one combined push when the window expires.
        self._inline_log_min_interval: float = 1.5
        self._last_inline_log_time: float = 0.0
        self._inline_log_pending: list[str] = []
        self._inline_log_flush_task: Optional[asyncio.Task] = None

        # Pacing state for inline screenshot push. mc-agent broadcasts at
        # 1Hz (configurable on its side via NEKO_AGENT_SCREENSHOT_INTERVAL_MS).
        # Forwarding every frame to the dialog LLM at that rate burns tokens
        # fast — 60 image parts/min into the realtime session is wasteful
        # when the picture changes little. ``_screenshot_stream_min_interval``
        # caps the push rate; frames that arrive inside the window get
        # collapsed (we keep only the latest pending and deliver it when
        # the window opens, so the dialog LLM always sees the most recent
        # visual rather than a stale one).
        self._screenshot_stream_min_interval: float = 1.0
        self._last_screenshot_push_time: float = 0.0
        self._pending_screenshot: Optional[tuple[bytes, str]] = None
        self._screenshot_flush_task: Optional[asyncio.Task] = None

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
        # Cap at 295s — 5s below the SDK wrapper's 300s ceiling
        # (see ``@llm_tool(timeout=300.0)`` in ``__init__.py``). Without
        # this clamp, an operator setting e.g. ``task_timeout_seconds = 600``
        # would have the SDK wrapper time out at 300s and cancel the
        # handler before the service could return its structured
        # ``{status: "timeout"}`` shape — the LLM would see the cancel
        # error path instead of the clean timeout result.
        self._task_timeout = max(1.0, min(295.0, _f("task_timeout_seconds", 25.0)))
        self._system_prompt_interval = max(1.0, _f("system_prompt_interval_seconds", 5.0))
        self._skip_when_busy = _b("skip_system_prompt_if_busy", True)
        self._stream_screenshots = _b("stream_screenshots_to_llm", True)
        # Floor at 0.2s (i.e. 5 fps cap) — below that we're letting the
        # dialog LLM's context burn at the wire rate of mc-agent and the
        # rate-limit ceases to function as a throttle.
        self._screenshot_stream_min_interval = max(
            0.2, _f("screenshot_stream_min_interval_seconds", 1.0)
        )
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
            on_alert=self._on_alert,
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
            pending = self._pending
            if pending is not None:
                pending.result = {
                    "status": "interrupted",
                    "query": pending.task_text,
                    "reason": "Game agent plugin shutting down.",
                }
                pending.event.set()
                self._pending = None
                # Same logic as overwrite: bump now if dispatched,
                # otherwise arm the late-dispatch flag so the
                # handler bumps when ``send_task`` eventually
                # succeeds.
                if pending.dispatched:
                    self._stale_task_finishes_to_drop += 1
                else:
                    pending.bump_on_late_dispatch = True
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

        # Cancel any pending inline-log flush. We don't try to drain it —
        # the cache is also being cleared right below; sending a final
        # batch on shutdown would surface character chatter into the
        # dialog LLM after the plugin's already going away.
        if self._inline_log_flush_task is not None:
            self._inline_log_flush_task.cancel()
            try:
                await self._inline_log_flush_task
            except (asyncio.CancelledError, Exception):
                pass
            self._inline_log_flush_task = None
        self._inline_log_pending.clear()

        # Same reasoning for the deferred screenshot push — drop the
        # pending frame and tear down the flush task.
        if self._screenshot_flush_task is not None:
            self._screenshot_flush_task.cancel()
            try:
                await self._screenshot_flush_task
            except (asyncio.CancelledError, Exception):
                pass
            self._screenshot_flush_task = None
        self._pending_screenshot = None

        self._log_cache.clear()
        self._screenshot_cache.clear()
        self._task_finished = True
        self._log_info("stopped")

    # ------------------------------------------------------------------
    # @llm_tool handler — the LLM-visible side
    # ------------------------------------------------------------------

    async def execute_minecraft_task(
        self, *, task: str, overwrite: Any = False
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

        # The schema declares ``overwrite: boolean`` but the LLM is
        # not reliably constrained — it may emit a string ``"true"``,
        # an int ``1``, or some other truthy value. Strict ``is True``
        # only accepts the canonical Python bool so the destructive
        # interrupt path doesn't fire by accident on an off-spec arg.
        overwrite_flag = overwrite is True

        async with self._pending_lock:
            if self._pending is not None:
                if not overwrite_flag:
                    # Refuse without disturbing the in-flight task.
                    return {
                        "result": "busy",
                        "currently_executing": self._pending.task_text,
                        "hint": "Set overwrite=true (boolean, not string) to interrupt the current task.",
                    }
                # Wake the old handler with an "interrupted" verdict
                # before claiming the slot for the new task. The agent
                # may still send a delayed ``task_finished`` for the
                # old task, so flag one frame to drop when it arrives.
                self._log_warning(
                    "overwriting task: {} -> {}", self._pending.task_text, task
                )
                old_pending = self._pending
                old_pending.result = {
                    "status": "interrupted",
                    "query": old_pending.task_text,
                    "reason": "Overwritten by a new task.",
                }
                old_pending.event.set()
                self._pending = None
                # Stale-frame debt: bump now if old was dispatched, or
                # arm the late-dispatch flag if it wasn't. Without the
                # late-dispatch path, an old task whose ``send_task``
                # succeeds *after* this overwrite would have its
                # eventual ``task_finished`` frame misattributed to
                # the new (B) task.
                if old_pending.dispatched:
                    self._stale_task_finishes_to_drop += 1
                else:
                    old_pending.bump_on_late_dispatch = True

            # The handler keeps a *local* reference to its own
            # PendingTask; ``self._pending`` may be reassigned (or
            # cleared) by overwrite/shutdown paths before this handler
            # wakes up, but ``my_pending.result`` survives because
            # whoever set the event also wrote into the dataclass we
            # hold here.
            my_pending = PendingTask(
                task_text=task,
                event=asyncio.Event(),
                start_time=time.time(),
                task_id=uuid.uuid4().hex,
            )
            self._pending = my_pending
            self._task_finished = False

        try:
            sent = await self._client.send_task(task, task_id=my_pending.task_id)
        except asyncio.CancelledError:
            # Cancellation can hit during the dispatch await too (the
            # outer SDK timeout fires, plugin shutdown sweeps tasks).
            # The ``event.wait()`` cancellation handler below covers
            # the post-dispatch window, but without this branch a
            # cancel landing in the dispatch window leaves
            # ``self._pending`` dangling and every subsequent call
            # returns "busy" against an event nothing will ever set.
            # Don't bump the drop counter — ``send_task`` was
            # cancelled before completing, so the task was never
            # delivered and won't generate a stale frame.
            async with self._pending_lock:
                if self._pending is my_pending:
                    self._pending = None
                    self._task_finished = True
            raise
        if sent:
            # Mark as dispatched so abandonment paths (overwrite /
            # timeout / cancel / stop) know whether to expect a
            # stale ``task_finished`` frame for this task.
            my_pending.dispatched = True
            # If we were already abandoned during the dispatch
            # window (overwrite/stop ran while ``send_task`` was
            # suspended), the abandoner couldn't bump the drop
            # counter at that moment because ``dispatched`` was
            # False. Now that the agent *has* received the task,
            # we're the only ones with the information that a stale
            # frame is coming for it — bump now to consume it.
            if my_pending.bump_on_late_dispatch:
                async with self._pending_lock:
                    self._stale_task_finishes_to_drop += 1
        # The ``send_task`` await is a suspension point — another
        # coroutine (overwrite / stop / a stale task_finished frame
        # filtered to fall through to ``_pending``) may have already
        # written a verdict into ``my_pending.result`` and set the
        # event during the suspend window. Honor that verdict
        # *before* deciding to return AGENT_DISCONNECTED, otherwise
        # we'd contradict whatever the system already recorded for
        # this slot (e.g. surfacing AGENT_DISCONNECTED to the LLM
        # while the rest of the system thinks the task was
        # interrupted).
        if my_pending.event.is_set():
            async with self._pending_lock:
                if self._pending is my_pending:
                    self._pending = None
            return my_pending.result or {"status": "ok", "query": task}

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
                    if my_pending.dispatched:
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
                    if my_pending.dispatched:
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
    # Inline log push — pacing helpers
    # ------------------------------------------------------------------

    def _schedule_inline_log_push(self, line: str) -> None:
        """Queue a log line for inline delivery to the dialog LLM.

        Two paths:
        * Window open (≥ ``_inline_log_min_interval`` since last push) →
          flush immediately, single-line push.
        * Window closed → append to the pending buffer; if no flush task
          is already scheduled, arm one to fire when the window opens.
          A second log arriving inside the window just appends, riding
          on the already-scheduled flush.

        The buffer + scheduled flush approach keeps the dialog LLM at
        most one window-length stale while collapsing high-frequency
        bursts (newAction loops emit log lines per inner iteration) into
        one combined push.
        """
        self._inline_log_pending.append(line)
        now = time.time()
        elapsed = now - self._last_inline_log_time
        if elapsed >= self._inline_log_min_interval:
            # Window open — flush now, no delay.
            self._flush_inline_log_now()
        else:
            # Inside the rate-limit window — schedule a delayed flush
            # if one isn't already pending. ``_inline_log_flush_task``
            # being non-None means a flush is armed for the end of the
            # current window, so this new line will go out with it.
            if self._inline_log_flush_task is None or self._inline_log_flush_task.done():
                delay = max(0.0, self._inline_log_min_interval - elapsed)
                self._inline_log_flush_task = asyncio.create_task(
                    self._delayed_flush_inline_log(delay),
                    name="game_agent_minecraft.inline_log_flush",
                )

    async def _delayed_flush_inline_log(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._flush_inline_log_now()

    def _flush_inline_log_now(self) -> None:
        if not self._inline_log_pending:
            return
        # Drain buffer atomically; if a new log arrives during the
        # push_message call below it'll re-arm a fresh flush.
        lines = self._inline_log_pending
        self._inline_log_pending = []
        self._last_inline_log_time = time.time()
        text = "\n".join(lines)
        try:
            # ai_behavior="read" — inject into context, don't force
            # immediate reply. The dialog LLM will see fresh narration
            # from the character on its next natural turn.
            self._push_message(
                source="game_agent_minecraft",
                visibility=[],
                ai_behavior="read",
                parts=[{"type": "text", "text": text}],
                priority=4,
            )
        except Exception as exc:
            self._log_error(
                "inline log push failed: {}: {}", type(exc).__name__, exc,
            )

    # ------------------------------------------------------------------
    # Inline screenshot push — pacing helpers (mirror of log path above)
    # ------------------------------------------------------------------

    def _schedule_screenshot_push(self, img_bytes: bytes, mime: str) -> None:
        """Rate-limit screenshot delivery to the dialog LLM.

        Unlike log lines (which batch — old lines still useful), old
        screenshots are obsolete the moment a newer one arrives. So we
        keep only ``_pending_screenshot`` = latest frame; an incoming
        frame inside the rate-limit window replaces it instead of
        queueing alongside.
        """
        now = time.time()
        elapsed = now - self._last_screenshot_push_time
        if elapsed >= self._screenshot_stream_min_interval:
            self._push_screenshot_now(img_bytes, mime)
        else:
            # Window closed — defer. Latest frame wins.
            self._pending_screenshot = (img_bytes, mime)
            if self._screenshot_flush_task is None or self._screenshot_flush_task.done():
                delay = max(0.0, self._screenshot_stream_min_interval - elapsed)
                self._screenshot_flush_task = asyncio.create_task(
                    self._delayed_flush_screenshot(delay),
                    name="game_agent_minecraft.screenshot_flush",
                )

    async def _delayed_flush_screenshot(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        pending = self._pending_screenshot
        self._pending_screenshot = None
        if pending is not None:
            self._push_screenshot_now(*pending)

    def _push_screenshot_now(self, img_bytes: bytes, mime: str) -> None:
        self._last_screenshot_push_time = time.time()
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

    # ------------------------------------------------------------------
    # WebSocket inbound callbacks — invoked from the WS listener task
    # ------------------------------------------------------------------

    async def _on_log(self, text: str) -> None:
        text_strip = text.strip() if isinstance(text, str) else ""
        if not text_strip:
            return
        self._log_cache.append(text_strip)

        # Inline push to the dialog LLM, rate-limited. The 5s autonomous
        # nudge loop is still authoritative for "burst the full state
        # alongside screenshots when nothing else is happening"; this
        # inline path is for "agent just said something, get it in front
        # of the dialog LLM now so it can weave into ongoing conversation
        # without 5s of staleness".
        self._schedule_inline_log_push(text_strip)

        # The original integration sniffed log strings to track agent
        # state because some agent server implementations emit logs
        # before / instead of explicit ``task_finished`` frames. Keep
        # the heuristic so existing agents work, but gate the
        # "True" transitions on ``_pending is None`` — without that
        # gate, an old (timed-out / overwritten / cancelled) task's
        # late "task run ended" log would prematurely flip the busy
        # gate while the *new* in-flight task is still running. When
        # a task IS pending, we defer to the explicit ``task_finished``
        # frame which already does proper stale-frame filtering.
        if "task run ended" in text_strip:
            if self._pending is None:
                self._task_finished = True
                # Some agent implementations emit "task run ended" in
                # lieu of (or alongside) an explicit ``task_finished``
                # frame for the abandoned task. If we're currently
                # carrying stale-frame debt, this log line *is* the
                # signal of that abandoned task's completion — drain
                # one drop so a future legitimate ``task_finished``
                # frame doesn't get swallowed instead.
                if self._stale_task_finishes_to_drop > 0:
                    self._stale_task_finishes_to_drop -= 1
        elif "action selection" in text_strip:
            # Setting False unconditionally is safe — at worst it
            # confirms what's already true (a task is in flight).
            self._task_finished = False
        elif text_strip == "Connection lost and re-established.":
            # Connection bounce wipes the agent's task queue. Two
            # cases to clean up:
            # 1. No task pending → just reset debt (any unpaid drops
            #    from before the bounce will never arrive).
            # 2. A task IS pending → its ``task_finished`` will
            #    never come either; wake the handler with an
            #    "interrupted" verdict so it doesn't sit on
            #    ``event.wait`` until ``task_timeout_seconds``
            #    expires. Clear the slot so a follow-up minecraft_task
            #    isn't refused with "busy".
            async with self._pending_lock:
                pending = self._pending
                if pending is None:
                    self._task_finished = True
                else:
                    pending.result = {
                        "status": "interrupted",
                        "query": pending.task_text,
                        "reason": "Agent connection bounced — task lost.",
                    }
                    self._pending = None
                    pending.event.set()
                    self._task_finished = True
                # Drain debt unconditionally — agent state is gone.
                self._stale_task_finishes_to_drop = 0

    async def _on_screenshot(self, payload: str, encoding: str) -> None:
        """Decode a base64 screenshot, convert JPEG→PNG when needed, and
        either stream it into the realtime LLM session immediately or
        cache it for the next autonomous-prompt burst."""
        # Some agents send screenshots as ``data:`` URIs that already
        # carry the mime in the scheme; pull it out before stripping
        # so we don't mis-tag JPEG bytes as PNG when the explicit
        # ``encoding`` field is empty.
        embedded_mime: Optional[str] = None
        try:
            stripped = payload
            if stripped.startswith("data:"):
                comma = stripped.find(",")
                if comma != -1:
                    header = stripped[5:comma]  # after "data:"
                    # Header looks like "image/jpeg;base64" or just
                    # "image/png". Take the segment up to the first
                    # ``;`` as the mime.
                    semi = header.find(";")
                    candidate = header[:semi] if semi != -1 else header
                    if candidate and "/" in candidate:
                        embedded_mime = candidate.lower()
                    stripped = stripped[comma + 1:]
            img_bytes = base64.b64decode(stripped, validate=False)
        except Exception as exc:
            self._log_error(
                "screenshot base64 decode failed: {}: {}",
                type(exc).__name__, exc,
            )
            return

        # Resolve "is this a JPEG?" by considering both the explicit
        # ``encoding`` field and the mime extracted from a data: URI.
        # The explicit field wins when both are present (more
        # authoritative); the URI scheme is a fallback when the
        # encoding is empty.
        mime = "image/png"
        enc_lower = (encoding or "").lower()
        is_jpeg_explicit = "jpeg" in enc_lower or "jpg" in enc_lower
        is_jpeg_embedded = embedded_mime in ("image/jpeg", "image/jpg")
        if is_jpeg_explicit or (not enc_lower and is_jpeg_embedded):
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

        # Stream into the realtime LLM session, rate-limited. The
        # autonomous nudge loop still bursts the cache at 5s intervals
        # regardless; this path is for "as fresh as the dialog LLM can
        # cope with" between nudges. Bursts collapse to "latest only" —
        # 5 frames within the window become 1 push of the most recent.
        if self._stream_screenshots:
            self._schedule_screenshot_push(img_bytes, mime)

    async def _on_alert(self, data: Dict[str, Any]) -> None:
        """High-severity event from mc-agent (HP damage / death / etc.).

        Pushed to the dialog LLM with ``ai_behavior="respond"`` and
        ``priority=1`` so it can preempt whatever else is queued —
        the user should hear about a death immediately, not 5s later
        on the next autonomous nudge tick.

        Severity is informational on the frame; the priority + behavior
        already encode "act on this now" downstream, but we keep the
        severity string in the text so the LLM can adjust tone (a
        ``warn`` doesn't need the same urgency as ``critical``).
        """
        text = str(data.get("text") or "").strip()
        if not text:
            return
        severity = str(data.get("severity") or "warn").lower()
        prefix = "[character alert | critical]" if severity == "critical" else "[character alert]"
        body = f"{prefix} {text}"
        try:
            self._push_message(
                source="game_agent_minecraft",
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": body}],
                priority=1,
            )
        except Exception as exc:
            self._log_error(
                "alert push failed: {}: {}", type(exc).__name__, exc,
            )

    async def _on_task_finished(self, data: Dict[str, Any]) -> None:
        # Snapshot inventory so the autonomous nudge loop can keep
        # surfacing it as ground truth between task_finished events
        # — without this the dialog LLM only sees inventory at
        # task_finished moments and freely hallucinates items between.
        inv = data.get("inventory")
        if isinstance(inv, dict):
            self._last_inventory = {
                str(k): int(v) for k, v in inv.items()
                if isinstance(v, (int, float)) and int(v) > 0
            }
            self._last_inventory_at = time.time()

        text = str(
            data.get("text") or data.get("data") or data.get("message") or ""
        )
        status = str(data.get("status") or "ok")
        # Optional explicit correlation: agents that opt in echo back
        # the ``task_id`` we sent on the matching ``task`` frame.
        # When present we trust it absolutely and skip the FIFO
        # heuristic entirely (an out-of-order completion is
        # disambiguated by ID, not by arrival order). Agents that
        # don't emit it fall through to the FIFO drop counter as
        # before.
        echoed_task_id = data.get("task_id")
        if not isinstance(echoed_task_id, str) or not echoed_task_id:
            echoed_task_id = None
        self._log_info("task_finished: status={}, text={}", status, text[:80])

        async with self._pending_lock:
            # The agent server's protocol has no task ID, so a delayed
            # ``task_finished`` for an old (timed-out / overwritten /
            # shutdown-cancelled) task is indistinguishable from a
            # fresh one for the current task. We assume the agent
            # emits frames in completion order and drain them FIFO:
            # for every task we abandoned without an ack, swallow one
            # incoming frame.
            #
            # KNOWN LIMITATION (FIFO mode only): this FIFO assumption
            # is exactly that — an assumption. Agents that opt into
            # task_id correlation (the branch above) bypass it
            # entirely. For agents that don't echo task_id, an
            # internal-concurrency case where an *overwritten* task
            # A finishes *after* the replacement task B (frames
            # arrive B-then-A) breaks: this filter swallows B's
            # real completion and later accepts A's stale frame as
            # if it were B's; the current ``minecraft_task`` call
            # hangs until ``task_timeout_seconds`` and a wrong
            # result eventually surfaces. The fix is for the agent
            # to echo task_id on the matching ``task_finished``
            # frame — see README "已知限制" for details. The stale frame's *text* must NOT enter
            # ``_log_cache`` either — otherwise the next system prompt
            # would surface "old task done" while a new task is still
            # running, lying about both branches of the prompt.
            # Explicit correlation path (agent opted into task_id
            # echoing): trust the ID. If it matches the current
            # pending task, accept the frame normally (skip the
            # stale-drop heuristic — even if drop counter is non-zero,
            # this frame is unambiguously for the active task). If it
            # doesn't match, it's stale by definition; drop it without
            # touching the counter (counter is only for FIFO-mode
            # agents).
            if echoed_task_id is not None:
                if self._pending is not None and self._pending.task_id == echoed_task_id:
                    pass  # fall through to acceptance below
                else:
                    self._log_info(
                        "dropped stale task_finished by id mismatch "
                        "(echoed={}, pending={})",
                        echoed_task_id,
                        self._pending.task_id if self._pending else None,
                    )
                    if self._pending is None:
                        self._task_finished = True
                    return
            elif self._stale_task_finishes_to_drop > 0:
                self._stale_task_finishes_to_drop -= 1
                self._log_info(
                    "dropped stale task_finished (FIFO mode, status={}, drops_remaining={})",
                    status, self._stale_task_finishes_to_drop,
                )
                # If no task is currently pending, the agent has
                # genuinely returned to idle (we just dropped what
                # would have been the only signal). Flipping
                # ``_task_finished`` here keeps the autonomous loop's
                # busy gate accurate so it can resume nudging.
                # When a task IS pending, leave the flag alone —
                # flipping it would leak the *old* task's completion
                # state into the *new* (still in-flight) task.
                if self._pending is None:
                    self._task_finished = True
                return
            # From here on the frame is being accepted; it's safe to
            # commit the text + flag updates.
            if text:
                self._log_cache.append(text)
            if self._pending is None:
                # Stray task_finished (e.g. from agent restart) — nothing
                # to wake. Update the flag anyway so the autonomous
                # loop knows the agent is idle and can resume nudging.
                self._task_finished = True
                return
            # Snapshot the pending ref *and* clear the shared slot
            # before calling ``event.set()`` — both still under the
            # lock. If we leave ``self._pending`` pointing at the
            # finished task, a racing ``stop()``/overwrite that
            # acquires the lock between this block exiting and the
            # waiter running could see ``self._pending is not None``
            # and overwrite the result we just wrote (the waiter and
            # ``self._pending`` reference the *same* PendingTask
            # object, so a mutation here corrupts the waiter's read).
            # The waiter holds its own ``my_pending`` local ref, so
            # clearing ``self._pending`` here is safe.
            pending = self._pending
            self._pending = None
            # Carry the agent's free-text message into the tool
            # result so the LLM sees the agent's final commentary
            # (e.g. "Mined 10 oak logs, inventory full" or "Path
            # blocked by water"). Without this, the model only sees
            # status/query and has to infer outcome detail —
            # noticeably worse for narration and error recovery.
            result_payload: Dict[str, Any] = {
                "status": status,
                "query": pending.task_text,
            }
            if text:
                result_payload["text"] = text
            pending.result = result_payload
            pending.event.set()
            self._task_finished = True
            # Anchor for the keep-going nudge branch in
            # ``_system_prompt_loop``. After ~5s without a new task
            # being dispatched, the loop will start poking the dialog
            # LLM to decide a next concrete action — preventing the
            # avatar from going idle indefinitely waiting to be driven.
            self._last_task_finished_at = time.time()

    # ------------------------------------------------------------------
    # Autonomous system-prompt loop
    # ------------------------------------------------------------------

    async def _system_prompt_loop(self) -> None:
        """Periodically nudge the dialog LLM. Three branches, each with
        its own rate limiter:

        * **In-progress nudge** (highest priority when applicable). Fires
          when a task has been pending ≥10s and the last in-progress
          nudge was ≥10s ago. Tells the dialog LLM "your body is still
          doing X — narrate what you're feeling in your own voice (don't
          repeat yourself)". Without this branch, long actions (mining,
          pathfinding) leave the user hearing nothing for 30+ seconds.

        * **Keep-going nudge** (idle, recently finished). Fires when no
          task is pending, the most recent task_finished is 5–60s ago,
          and the last keep-going nudge was ≥15s ago. Tells the dialog
          LLM "your body finished — decide and dispatch the next concrete
          action". Without this branch, the avatar stands still after
          each task waiting for the user to drive it.

        * **General catch-all** — original behavior: every
          ``system_prompt_interval`` seconds (default 5s), if there's
          actual cache to surface (logs / screenshots), fire the standard
          state-update prompt.

        We don't try to detect "user/model is currently speaking" from
        inside the plugin — main_server's proactive_message handler
        already gates timing on its end. The plugin's pacing here is
        only about not flooding main_server with redundant wake-ups,
        not about real-time conversation politeness.
        """
        # Anchor thresholds chosen to balance "keep the avatar engaged"
        # vs "don't spam the dialog LLM's context budget":
        #   in-progress: 10s elapsed + 10s cooldown
        #   keep-going:  5s post-finish + 15s cooldown, max 60s window
        _IN_PROGRESS_AFTER = 10.0
        _IN_PROGRESS_COOLDOWN = 10.0
        _KEEP_GOING_AFTER = 5.0
        _KEEP_GOING_COOLDOWN = 15.0
        _KEEP_GOING_MAX_WINDOW = 60.0

        try:
            while True:
                await asyncio.sleep(0.5)
                now = time.time()

                # ---- Branch 1: in-progress nudge ----
                if self._pending is not None and not self._task_finished:
                    elapsed_pending = now - self._pending.start_time
                    since_last = now - self._last_in_progress_nudge_at
                    if elapsed_pending >= _IN_PROGRESS_AFTER and since_last >= _IN_PROGRESS_COOLDOWN:
                        await self._fire_in_progress_nudge()
                        self._last_in_progress_nudge_at = now
                    # When a task is in flight, do NOT also fire the
                    # general nudge — that would stack two prompts on
                    # the dialog LLM's queue for the same situation.
                    continue

                # ---- Branch 2: keep-going nudge (idle, recent finish) ----
                if (
                    self._task_finished
                    and self._pending is None
                    and self._last_task_finished_at > 0
                ):
                    since_finish = now - self._last_task_finished_at
                    since_last_keep = now - self._last_keep_going_nudge_at
                    if (
                        _KEEP_GOING_AFTER <= since_finish <= _KEEP_GOING_MAX_WINDOW
                        and since_last_keep >= _KEEP_GOING_COOLDOWN
                    ):
                        await self._fire_keep_going_nudge()
                        self._last_keep_going_nudge_at = now
                        continue

                # ---- Branch 3: general catch-all (original behavior) ----
                if now - self._last_system_prompt_time < self._system_prompt_interval:
                    continue
                if self._skip_when_busy and self._pending is not None and not self._task_finished:
                    continue
                if not self._log_cache and not self._screenshot_cache and self._task_finished:
                    continue
                await self._fire_system_prompt()
                self._last_system_prompt_time = time.time()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._log_error(
                "system prompt loop failed: {}: {}", type(exc).__name__, exc,
            )

    async def _fire_in_progress_nudge(self) -> None:
        """Push a "what are you feeling right now?" prompt + latest
        screenshot. Goal: the dialog LLM keeps {MASTER_NAME} engaged with
        live narration during long actions instead of going silent.

        Avoid repetition guidance is in the prompt itself — the dialog
        LLM is told to use a fresh angle each time, not parrot the same
        line it already said.
        """
        # Pull at most one screenshot so push is cheap; keep cache for
        # the general nudge to potentially burst more.
        parts: list[Dict[str, Any]] = []
        if self._screenshot_cache:
            img_bytes, img_mime = self._screenshot_cache[-1]
            parts.append({"type": "image", "data": img_bytes, "mime": img_mime})

        pending_text = self._pending.task_text if self._pending else "(unknown)"
        elapsed = (time.time() - self._pending.start_time) if self._pending else 0.0
        sections = [
            f"你正在做: \"{pending_text[:120]}\"（已经过了 {elapsed:.0f} 秒）。",
        ]
        if self._last_inventory:
            items = sorted(self._last_inventory.items(), key=lambda kv: -kv[1])
            inv_str = "、".join(f"{n}×{c}" for n, c in items[:15])
            sections.append(f"【当前持有 ground truth】{inv_str}")
        sections.append(
            "用第一人称随口讲一句你此刻看到/感觉到啥——换个新角度"
            "（吐槽进度慢、形容画面里的奇怪东西、自言自语、跟 {MASTER_NAME} "
            "瞎扯都行），别复读之前说过的话。"
            "禁止编造尚未发生的结果（比如别说『快搞定了』、『挖到一半了』）。"
        )
        parts.append({"type": "text", "text": "[你正在做事]\n" + "\n".join(sections)})

        try:
            self._push_message(
                source="game_agent_minecraft",
                visibility=[],
                ai_behavior="respond",
                parts=parts,
                priority=2,
            )
        except Exception as exc:
            self._log_error(
                "in-progress nudge push failed: {}: {}", type(exc).__name__, exc,
            )

    async def _fire_keep_going_nudge(self) -> None:
        """Push a "decide the next action" prompt after a task finishes.

        Without this, the conversation drifts after each completion and
        the avatar stands still indefinitely waiting for {MASTER_NAME} to
        explicitly drive it. We give the dialog LLM a clear "you are the
        agent — pick the next concrete action and dispatch it via
        minecraft_task" cue, plus the latest inventory ground truth so
        it can ground its next decision.
        """
        sections: list[str] = []
        if self._last_inventory:
            items = sorted(self._last_inventory.items(), key=lambda kv: -kv[1])
            inv_str = "、".join(f"{n}×{c}" for n, c in items[:20])
            sections.append(f"【当前持有 ground truth】{inv_str}")
        elif self._last_inventory_at > 0:
            sections.append("【当前持有 ground truth】(空)")
        sections.append(
            "你已经停下了，等你挑下一步。基于上面的库存和最近画面，"
            "随手选一个具体可执行的动作（继续挖某种矿、回基地放东西、"
            "做某件 craft 都行），用 minecraft_task 派下去——你在玩游戏，"
            "主动找事做，别站着。如果想停一停跟 {MASTER_NAME} 闲聊"
            "再继续，就用第一人称随口讲讲刚做完了啥、想接着干啥。"
        )
        parts: list[Dict[str, Any]] = [
            {"type": "text", "text": "[你闲下来了]\n" + "\n".join(sections)}
        ]
        try:
            self._push_message(
                source="game_agent_minecraft",
                visibility=[],
                ai_behavior="respond",
                parts=parts,
                priority=2,
            )
        except Exception as exc:
            self._log_error(
                "keep-going nudge push failed: {}: {}", type(exc).__name__, exc,
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
        # Inventory ground-truth FIRST so it's the most prominent line
        # the dialog LLM sees on each nudge — see __init__'s prompt
        # ("尤其【当前持有】行是你的真实库存，绝对不要说出未列出的物品").
        if self._last_inventory:
            items = sorted(self._last_inventory.items(), key=lambda kv: -kv[1])
            inv_str = "、".join(f"{n}×{c}" for n, c in items[:20])
            sections.append(f"【当前持有 ground truth】{inv_str}")
        elif self._last_inventory_at > 0:
            sections.append("【当前持有 ground truth】(空)")
        if self._pending is not None:
            sections.append(f"你正在做: {self._pending.task_text}")
        if log_text:
            sections.append(f"你最近发生的事:\n---\n{log_text}\n---")
        if self._task_finished:
            # Idle — push the dialog LLM to pick the next action
            # rather than just narrate. The avatar framing requires that
            # the LLM-as-character is the one deciding what to do next.
            sections.append(
                "你现在闲着。基于上面看到的内容，挑一个具体动作用 minecraft_task "
                "派下去——你在玩游戏，主动找事做，别站着。如果想顺便跟 "
                "{MASTER_NAME} 唠两句，就用第一人称随口讲讲下一步打算干啥。"
            )
        else:
            sections.append(
                "你还在做上一个动作。可以基于画面/反馈用第一人称随口解说一句"
                "你看到/感觉到的进展。"
            )
        prompt_text = "[当前状态]\n" + "\n".join(sections)

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

    def has_pending_task(self) -> bool:
        """Lock-free read of whether an in-flight task is occupying the
        pending slot. Used by the plugin facade to short-circuit a new
        ``minecraft_task`` call with a 'busy' summary instead of letting
        the detached task drop it on the floor — under fire-and-forget,
        the dialog LLM would otherwise see the standard 'task dispatched'
        ack and assume its new action took, when really it was rejected
        by the pending lock.
        """
        return self._pending is not None

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
