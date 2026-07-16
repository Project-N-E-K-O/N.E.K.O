"""GameAgentService — wires the WebSocket client to the LLM session.

Responsibilities split off the plugin facade so it stays testable:

1. Hold the cross-callback state (pending tool call, log/screenshot
   caches, task-finished signal).
2. Translate raw agent-server frames into push_message v2 payloads:
   * screenshots → image parts on the realtime stream
     (``ai_behavior="read"``)
   * task_finished → wakes the pending ``minecraft_task`` handler so it
     can return the result to the LLM
3. Run the system-prompt loop that injects passive game-state awareness and,
   after a confirmed task completion has stayed idle for 45 seconds,
   rate-limited keep-going model turns.

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

from . import prompts
from .client import GameAgentClient

# Strip ANSI colour escapes from agent log lines before relaying to the
# LLM — we don't want VT100 noise in the model's context window.
_ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

# [ANTI-PARROT] mc-agent emits internal action/telemetry logs that contain
# executable command syntax — function calls like ``goToCoordinates(118,64,-115,1)``
# or ``attackEntity(spider)``, ``!command`` prefixes, and "admin movement request"
# lines. Forwarded verbatim to the dialog LLM, these get copied back as new
# minecraft_task dispatches (observed: Neko spamming goToCoordinates/attackEntity
# instead of the user's actual request), a self-reinforcing loop. These lines are
# internal mechanics, not narration the character should see, so we drop them
# before they ever reach the dialog LLM (both inline push and the nudge digest).
_INJECTION_LOG_RE = re.compile(
    r"""(?xi)
      [A-Za-z_][A-Za-z0-9_]*\([^)]*\)   # function-call syntax: goToCoordinates(...), attackEntity(...)
    | (?:^|\s)![A-Za-z_]\w*             # !command prefix
    | admin\s+movement                  # "admin movement request"
    | latest\s+admin                    # "Continuing with latest admin ... request"
    | forced\s+interrupt                # stack-kick / interrupt mechanics
    | kicking\s+the\s+stack
    | pinned\s+\d+\s*min                # "Pinned 15min+ — ..."
    """
)

# English "the action was actually blocked" markers. mc-agent's chat-loop
# sometimes returns status="ok" while the real in-game action was blocked
# (target not found, no path, missing tool, needs more info…), burying the
# failure in the free-text feedback. Both the live completion cue
# (``_format_completion_cue`` in the facade) and the retroactive cue
# (``_push_retroactive_completion_cue`` below) sniff these so a blocked
# action is never narrated as a success (user-reported "她以为找博士成功了"
# bug). Kept in ONE place so the two consumers can't drift apart.
_BLOCKED_TEXT_MARKERS = (
    "obstacle", "obstructed", "not found", "could not", "couldn't",
    "unable", "failed", "no path", "blocked", "missing",
    "cannot", "can't",
    # status=ok 但缺目标 / 要更多信息也算受阻：mc-agent 对「过来」「跟着我」
    # 这类缺玩家名/坐标的指令会回 status=ok + "Target unavailable. Please
    # provide the exact player name or coordinates."
    "unavailable", "please provide", "provide the exact",
    "no target", "target not",
)


def text_signals_blocked(text: str) -> bool:
    """True when free-text feedback indicates the action was actually
    blocked / unsuccessful despite a status that claims otherwise. Shared
    by the live and retroactive completion cues (see ``_BLOCKED_TEXT_MARKERS``)."""
    if not text:
        return False
    low = text.lower()
    return any(m in low for m in _BLOCKED_TEXT_MARKERS)


@dataclass
class PendingTask:
    """State for a single in-flight ``minecraft_task`` invocation.

    The handler creates one when the LLM picks the tool, blocks on
    ``event``, and reads ``result`` after waking.
    """

    task_text: str
    event: asyncio.Event
    start_time: float
    # Per-task ID generated locally and forwarded to the agent on the
    # outbound ``task`` frame. mc-agent echoes it on ``task_finished``;
    # ``_on_task_finished`` uses the echo to look the task up in
    # ``_dispatched_history`` and decide whether it's the current
    # pending slot (normal wake), a known previously-dispatched task
    # (emit retroactive completion cue), or unknown (FIFO fallback).
    task_id: str = ""
    # Filled in by the WebSocket callback (or by overwrite/timeout
    # paths) right before ``event`` is set.
    result: Dict[str, Any] = field(default_factory=dict)
    # True once the task text was actually sent to the agent server
    # (``client.send_task`` returned True). Used by the autonomous
    # nudge loop to distinguish "queued but never sent" from "really
    # running" — only the latter is worth narrating elapsed time on.
    dispatched: bool = False


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
        self._task_timeout: float = 90.0
        self._system_prompt_interval: float = 15.0
        self._skip_when_busy: bool = True
        self._stream_screenshots: bool = True
        self._screenshot_cache_size: int = 3
        # Bound each pushed frame so it fits the message_plane payload cap
        # (default 256KB). mc-agent sends full-res frames; left untouched they
        # blow past the cap and get silently dropped at ingest.
        self._screenshot_max_edge_px: int = 1024
        self._screenshot_jpeg_quality: int = 80
        # Hard budget on the raw JPEG bytes. The wire payload base64-encodes the
        # frame (+~33%) AND carries a raw copy, so packed payload ≈ 2.3x raw +
        # envelope; 100KB raw → ~238KB < the 256KB message_plane cap.
        self._screenshot_max_bytes: int = 100 * 1024

        # WebSocket lifecycle
        self._client: Optional[GameAgentClient] = None
        self._client_task: Optional[asyncio.Task] = None
        self._system_loop_task: Optional[asyncio.Task] = None

        # User-language short code for localizing every push_message cue
        # and tool result this service emits. Set via ``set_lang`` from
        # the plugin facade at startup; until then, EN is the fallback
        # so a misordered init never throws on prompt lookup.
        self._lang: str = prompts.DEFAULT_LANG

        # Cross-callback state
        self._pending: Optional[PendingTask] = None
        self._pending_lock = asyncio.Lock()
        # Bounded history of dispatched task_id → task_text. Used by
        # ``_on_task_finished`` to recognize completion frames for tasks
        # that are no longer the active ``_pending`` (typical case:
        # ``overwrite=True`` interrupt path — the old task really does
        # finish on mc-agent later, and we still want to surface that
        # completion to the dialog LLM as a "your earlier action
        # actually finished" cue instead of silently dropping it).
        # Capped at 32 because mc-agent can only run one task at a time;
        # we only need enough history to cover a handful of in-flight
        # overwrites.
        self._dispatched_history: "collections.OrderedDict[str, str]" = collections.OrderedDict()
        self._dispatched_history_max: int = 32
        # One-way latch: flips True the first time mc-agent echoes a
        # task_id on task_finished. Used by ``_on_task_finished`` to
        # disable the FIFO fallback once we know the agent is modern —
        # an id-less frame from a modern agent is anomalous, not a
        # legacy-protocol completion, and FIFO-routing it onto current
        # pending under overwrite races can resolve task B with task A's
        # stale payload (Codex review on PR #1395). Stays False forever
        # for genuinely legacy agents, preserving FIFO compat.
        self._seen_task_id_echo: bool = False
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

        # Latest known body state (inventory dict from mc-agent). Updated
        # in two ways now:
        #   1. piggy-backed on ``task_finished`` frames (legacy path,
        #      keeps the nudge loop fresh-enough between explicit queries)
        #   2. on-demand via ``request_fresh_inventory`` → mc-agent emits
        #      a dedicated ``inventory`` frame in response
        # The second path is what ``query_inventory`` entry uses so the
        # dialog LLM always gets present-state, not minutes-old cache.
        self._last_inventory: Dict[str, int] = {}
        self._last_inventory_at: float = 0.0
        # [INV-DEDUP] snapshot of the inventory we last SURFACED to the dialog
        # (in a nudge/cue). The bag line is only re-stated when this changes, so
        # she stops robotically reciting the same inventory every nudge. None =
        # never surfaced yet (first mention always allowed).
        self._last_pushed_inventory: Optional[tuple] = None
        # [AGENT-BUSY] mc-agent's self-reported activity, parsed from its
        # bot_status_nl frames. When it's actively executing (a skill/command),
        # the autonomous nudge loop must NOT tell her to dispatch a new task —
        # that interrupts what the agent is already doing. Refreshed per frame;
        # treated as stale (idle) after ``_agent_busy_ttl`` with no update.
        self._agent_busy: bool = False
        self._agent_busy_at: float = 0.0
        self._agent_activity: str = ""
        self._agent_busy_ttl: float = 60.0
        # On-demand inventory refresh plumbing. ``_inventory_waiters`` are
        # asyncio.Futures that resolve when the next ``inventory`` frame
        # lands; multiple concurrent ``query_inventory`` calls can all
        # await the same in-flight response. List, not single future, so
        # we don't drop a second caller's wakeup if it arrives mid-fetch.
        self._inventory_waiters: list[asyncio.Future] = []

        # Pacing state for the autonomous loop. Three independent rate
        # limiters cover the three distinct nudge purposes:
        #
        # * ``_last_system_prompt_time`` — generic "here's recent state"
        #   nudge, fires only when there's actual cache to surface
        # * ``_last_in_progress_nudge_at`` — when a task has been pending
        #   ≥10s, periodically prompt the dialog LLM to narrate what it's
        #   doing in its own voice (so the user gets ongoing engagement
        #   instead of dead silence during long actions)
        # * ``_last_keep_going_nudge_at`` — after a correlated task_finished
        #   frame has stayed idle for 45s, wake the dialog LLM, then keep the
        #   original 45s cooldown while it remains idle. Startup, local send
        #   failures/timeouts, connection bounces and stray frames never arm
        #   this branch.
        # ``_last_task_finished_at`` is the real-completion anchor;
        # ``_last_keep_going_nudge_at`` paces the retained recurring mechanism.
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
        self._inline_log_min_interval: float = 12.0  # [THROTTLE] was 1.5
        self._last_inline_log_time: float = 0.0
        self._inline_log_pending: list[str] = []
        self._inline_log_flush_task: Optional[asyncio.Task] = None
        # [DEDUP] mc-agent repeats identical status lines (e.g. "Nightfall —
        # securing till dawn", "Picking up item!") every few seconds while it's
        # stuck in a loop. Without dedup each one becomes a push and the dialog
        # LLM re-narrates the same thing forever. Suppress a log line that was
        # already seen within this window; refresh its timestamp on every
        # repeat so a persistently-looping line stays muted after being said
        # once, and only resurfaces once it's been quiet for the full window.
        self._log_dedup_window: float = 120.0
        self._recent_log_times: dict[str, float] = {}

        # Pacing state for inline screenshot push. mc-agent broadcasts at
        # 1Hz (configurable on its side via NEKO_AGENT_SCREENSHOT_INTERVAL_MS).
        # Forwarding every frame to the dialog LLM at that rate burns tokens
        # fast — 60 image parts/min into the realtime session is wasteful
        # when the picture changes little. ``_screenshot_stream_min_interval``
        # caps the push rate; frames that arrive inside the window get
        # collapsed (we keep only the latest pending and deliver it when
        # the window opens, so the dialog LLM always sees the most recent
        # visual rather than a stale one).
        self._screenshot_stream_min_interval: float = 6.0  # [THROTTLE] was 1.0
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

    # [ISSUE4c] Anti-thrash floor: minimum seconds a just-claimed task must run
    # before an overwrite=True can interrupt it. Weak dialog LLMs (esp. realtime,
    # which has no per-turn tool-call cap) set overwrite=True on nearly every
    # minecraft_task call, interrupting each freshly-sent task ~1s later and
    # thrashing mc-agent between goals. A genuine {MASTER_NAME} correction of a
    # <2s-old task is implausible; sub-2s overwrites are the runaway signature.
    _OVERWRITE_MIN_SURVIVAL_S = 2.0

    def set_lang(self, lang: str) -> None:
        """Set the locale used for every push_message cue + result
        summary this service emits. Called by the plugin facade after
        resolving the host's user language at startup; if never called,
        EN is used as a safe fallback (per ``prompts.DEFAULT_LANG``).
        """
        self._lang = lang or prompts.DEFAULT_LANG

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
        self._task_timeout = max(1.0, min(295.0, _f("task_timeout_seconds", 90.0)))
        self._system_prompt_interval = max(1.0, _f("system_prompt_interval_seconds", 15.0))
        self._skip_when_busy = _b("skip_system_prompt_if_busy", True)
        self._stream_screenshots = _b("stream_screenshots_to_llm", True)
        # Floor at 0.2s (i.e. 5 fps cap) — below that we're letting the
        # dialog LLM's context burn at the wire rate of mc-agent and the
        # rate-limit ceases to function as a throttle.
        self._screenshot_stream_min_interval = max(
            0.2, _f("screenshot_stream_min_interval_seconds", 6.0)
        )
        size = max(1, _i("screenshot_cache_size", 3))
        self._screenshot_cache_size = size
        # ``deque(maxlen=...)`` is immutable post-construction, so swap.
        if self._screenshot_cache.maxlen != size:
            self._screenshot_cache = collections.deque(
                self._screenshot_cache, maxlen=size
            )
        # Frame size bounds. ``screenshot_max_edge_px=0`` disables resizing
        # (re-encode to JPEG only). Quality is clamped to a sane 1..95.
        self._screenshot_max_edge_px = max(0, _i("screenshot_max_edge_px", 1024))
        self._screenshot_jpeg_quality = max(1, min(95, _i("screenshot_jpeg_quality", 80)))
        # 0 disables the byte budget (resolution/quality caps still apply).
        self._screenshot_max_bytes = max(0, _i("screenshot_max_bytes", 100 * 1024))

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
            on_inventory=self._on_inventory,
            on_bot_status=self._on_bot_status,
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
                    "reason": prompts.t(
                        "INTERRUPTED_REASON_SHUTDOWN", lang=self._lang,
                    ),
                }
                pending.event.set()
                self._pending = None
                # A delayed ``task_finished`` for this task may still
                # arrive after shutdown — ``_on_task_finished`` looks
                # it up in ``_dispatched_history`` and routes it
                # through the retroactive cue path; no FIFO drop
                # counter needed.

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
                # Cancellation we just asked for, or a flush-time error
                # already logged by the task itself — nothing to recover.
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
                # Cancellation we just asked for, or a flush-time error
                # already logged by the task itself — nothing to recover.
                pass
            self._screenshot_flush_task = None
        self._pending_screenshot = None

        self._log_cache.clear()
        self._screenshot_cache.clear()
        # Inventory snapshot belongs to the WS session that just ended.
        # game_agent_reload_config（ws_url 切换）/ 重启场景下，下一个 WS
        # 可能连到完全不同的世界，沿用旧 ``_last_inventory`` 会让
        # query_inventory 把旧世界的库存当新世界 ground truth 上报。
        # 清空 = "回到 inv_at==0 未知"分支，由下一个 task_finished 重建。
        self._last_inventory = {}
        self._last_inventory_at = 0.0
        # New session may be a different world — let its first inventory and
        # activity surface fresh instead of being deduped against the old one.
        self._last_pushed_inventory = None
        self._mark_agent_idle()
        self._agent_busy_at = 0.0
        self._agent_activity = ""
        # ``_dispatched_history`` belongs to the just-ended session too —
        # next session's task_id space is independent, holding onto these
        # would only cause spurious "retroactive completion" cues if a
        # later frame happened to repeat a stale id.
        self._dispatched_history.clear()
        # The next WS session might land on a different mc-agent version
        # (legacy or modern). Reset the latch so we re-learn from the
        # first task_finished frame.
        self._seen_task_id_echo = False
        self._task_finished = True
        # Completion eligibility belongs to the old WS session. Carrying it
        # across a restart could fire keep-going for a task from another world.
        self._last_task_finished_at = 0.0
        self._last_keep_going_nudge_at = 0.0
        self._log_info("stopped")

    # ------------------------------------------------------------------
    # @llm_tool handler — the LLM-visible side
    # ------------------------------------------------------------------

    async def request_fresh_inventory(
        self, *, timeout: float = 2.0
    ) -> Dict[str, Any]:
        """Ask mc-agent for a live inventory snapshot.

        Returns a dict ``{inventory, snapshot_at, source}`` where source
        is ``"live"`` (mc-agent responded within the deadline) or
        ``"cached"`` (timed out / not connected — fell back to whatever
        ``_last_inventory`` we have, possibly empty). The dialog LLM
        cares whether the value is fresh; ``source`` lets the
        ``query_inventory`` summary be honest about that without the LLM
        having to compare timestamps itself.
        """
        if self._client is None or not self._client.is_connected:
            return {
                "inventory": dict(self._last_inventory),
                "snapshot_at": self._last_inventory_at,
                "source": "cached",
            }
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future = loop.create_future()
        async with self._pending_lock:
            self._inventory_waiters.append(waiter)
        try:
            sent = await self._client.request_inventory()
        except Exception as exc:
            self._log_warning(
                "request_inventory call raised: {}: {}",
                type(exc).__name__, exc,
            )
            sent = False
        if not sent:
            async with self._pending_lock:
                if waiter in self._inventory_waiters:
                    self._inventory_waiters.remove(waiter)
            return {
                "inventory": dict(self._last_inventory),
                "snapshot_at": self._last_inventory_at,
                "source": "cached",
            }
        try:
            await asyncio.wait_for(waiter, timeout=timeout)
            return {
                "inventory": dict(self._last_inventory),
                "snapshot_at": self._last_inventory_at,
                "source": "live",
            }
        except asyncio.TimeoutError:
            async with self._pending_lock:
                if waiter in self._inventory_waiters:
                    self._inventory_waiters.remove(waiter)
            return {
                "inventory": dict(self._last_inventory),
                "snapshot_at": self._last_inventory_at,
                "source": "cached",
            }

    async def _on_inventory(self, data: Dict[str, Any]) -> None:
        """Handle the dedicated ``inventory`` frame from mc-agent (response
        to ``request_inventory``, or proactive periodic push). Updates the
        cache and wakes all pending ``request_fresh_inventory`` waiters.
        """
        raw = data.get("inventory") if isinstance(data.get("inventory"), dict) else data.get("items")
        parsed: Dict[str, int] = {}
        if isinstance(raw, dict):
            parsed = {
                str(k): int(v) for k, v in raw.items()
                if isinstance(v, (int, float)) and int(v) > 0
            }
        async with self._pending_lock:
            self._last_inventory = parsed
            self._last_inventory_at = time.time()
            waiters = self._inventory_waiters
            self._inventory_waiters = []
        for w in waiters:
            if not w.done():
                w.set_result(None)

    async def _on_bot_status(self, data: Dict[str, Any]) -> None:
        """Track the mc-agent's activity from a ``bot_status_nl`` frame.

        The agent tags its status text with 🎯[按指令] (executing a dispatched
        command) or 🤖[自主] (doing its own autonomous thing), and carries a
        ``skill``/``kind`` when it's mid-action. We collapse that to a single
        ``_agent_busy`` flag: busy unless it explicitly says it's idle/thinking
        (or reports no skill and no kind). The autonomous nudge loop reads this
        so it won't tell her to dispatch a fresh task over a running one.
        """
        text = str(data.get("text") or "")
        skill = data.get("skill")
        kind = data.get("kind")
        idle_markers = ("空闲", "在想下一步", "空閒", "idle", "thinking", "nothing to do")
        is_idle = ((not skill) and (not kind)) or any(m in text for m in idle_markers)
        # [BUSY-RACE] Re-arm the busy latch only when the frame correlates to real
        # ongoing work: a pending dispatched task, or an explicitly autonomous
        # (自主) frame. The agent tags each status 🎯[按指令] (a DISPATCHED command)
        # or 🤖[自主] (autonomous). With no pending task AND no 自主 tag, a busy
        # frame is either a stale trailing report of the just-finished dispatch
        # (按指令) or an untagged frame we can't attribute — so we do NOT re-arm.
        # Conservative on purpose: a wrong "not busy" costs at most one extra
        # nudge, but a wrong "busy" strands recovery for the full TTL while the
        # agent is idle. (A precise call on untagged frames would need the mc-agent
        # to stamp bot_status_nl with a run-id/seq — a protocol change on that
        # side, out of scope here.)
        if (not is_idle) and self._pending is None:
            autonomous_tag = ("自主" in text) or ("🤖" in text)
            if not autonomous_tag:
                return
        self._agent_busy = not is_idle
        self._agent_busy_at = time.time()
        self._agent_activity = text[:120]

    def _mc_agent_busy(self) -> bool:
        """True when the mc-agent recently reported it's actively executing.
        Fresh within ``_agent_busy_ttl`` — a stale flag (no bot_status_nl for a
        while) is treated as not-busy so the loop can resume nudging."""
        return bool(self._agent_busy) and (time.time() - self._agent_busy_at) < self._agent_busy_ttl

    def _mark_agent_idle(self) -> None:
        """Drop the busy latch on a task-terminal path. Centralized so every
        terminal clears it uniformly; ``_on_bot_status`` then won't re-arm from an
        uncorrelated (no pending, non-autonomous) post-terminal frame."""
        self._agent_busy = False

    def _inventory_section_if_changed(self, top_n: int = 20) -> Optional[str]:
        """Return the localized bag line ONLY when the inventory changed since
        we last surfaced it, so the dialog LLM stops reciting the same bag every
        nudge (the "有点人机" complaint). Returns ``None`` to omit the line."""
        inv = self._last_inventory
        if inv:
            snapshot = tuple(sorted(
                (str(k), int(v)) for k, v in inv.items() if int(v) > 0
            ))
            if snapshot == self._last_pushed_inventory:
                return None
            self._last_pushed_inventory = snapshot
            items = sorted(inv.items(), key=lambda kv: -kv[1])
            inv_str = "、".join(f"{n}×{c}" for n, c in items[:top_n])
            return prompts.t("BAG_LINE", lang=self._lang, items=inv_str)
        # inventory known-empty: mention "empty" once, only on the transition.
        if self._last_inventory_at > 0 and self._last_pushed_inventory != ():
            self._last_pushed_inventory = ()
            return prompts.t("BAG_EMPTY_LINE", lang=self._lang)
        return None

    def current_task_text(self) -> Optional[str]:
        """Return the text of the currently-pending task, or ``None`` if idle.
        Lock-free snapshot read for the facade's "busy" reply — the facade
        needs to tell the dialog LLM what task is in flight so the猫娘 can
        narrate it correctly instead of派 a new conflicting task.
        """
        pending = self._pending
        return pending.task_text if pending is not None else None

    def _remember_dispatched(self, task_id: str, task_text: str) -> None:
        """Stash (task_id → task_text) so a later ``task_finished`` echo
        for this task can be recognized even after it's no longer the
        active ``_pending`` (e.g. overwritten / abandoned). Bounded so
        a long session can't grow this without limit.
        """
        if not task_id:
            return
        self._dispatched_history[task_id] = task_text
        while len(self._dispatched_history) > self._dispatched_history_max:
            self._dispatched_history.popitem(last=False)

    async def try_claim_pending(
        self, task: str, *, overwrite: bool
    ) -> Optional[PendingTask]:
        """Atomic "check + claim" of the pending slot. Returns the claimed
        PendingTask (caller MUST follow up with :meth:`run_claimed_task`),
        or ``None`` if the call should be refused as busy.

        Why this exists separately from the actual send/wait: the facade
        needs a *synchronous* yes/no decision before fire-and-forget
        detaching the run, so the dialog LLM can get an immediate
        "you're still doing X" response when busy without overwrite.
        Doing the check inside the same lock that claims the slot
        eliminates the race where two concurrent callers both saw
        ``has_pending_task() == False`` and both dispatched, silently
        overwriting each other's pending state.
        """
        async with self._pending_lock:
            if self._pending is not None:
                if not overwrite:
                    return None
                # [ISSUE4c] Anti-thrash guard: even with overwrite=True, refuse
                # to interrupt a task that has barely started (< _OVERWRITE_MIN_
                # SURVIVAL_S). This is the structural stop for the dispatch storm
                # — the busy-without-overwrite gate never engaged because the LLM
                # set overwrite=True on every call. Give a freshly-sent task a
                # floor of run time; a real correction arrives seconds later and
                # clears the floor. Rejected → caller returns the busy summary.
                age = time.time() - self._pending.start_time
                if age < self._OVERWRITE_MIN_SURVIVAL_S:
                    self._log_info(
                        "overwrite rejected (anti-thrash): current task age={:.2f}s "
                        "< {:.1f}s — keeping {!r}, refusing {!r}",
                        age, self._OVERWRITE_MIN_SURVIVAL_S,
                        self._pending.task_text[:40], task[:40],
                    )
                    return None
                # overwrite=True: wake the old handler with an
                # "interrupted" verdict before claiming the slot.
                # mc-agent may still emit a delayed ``task_finished``
                # for the old task; that frame is recognized through
                # ``_dispatched_history`` in ``_on_task_finished`` and
                # surfaced as a "your earlier action actually finished"
                # cue rather than misattributed to the new task.
                self._log_warning(
                    "overwriting task: {} -> {}", self._pending.task_text, task
                )
                old_pending = self._pending
                old_pending.result = {
                    "status": "interrupted",
                    "query": old_pending.task_text,
                    "reason": prompts.t(
                        "INTERRUPTED_REASON_OVERWRITTEN", lang=self._lang,
                    ),
                }
                old_pending.event.set()
                self._pending = None

            my_pending = PendingTask(
                task_text=task,
                event=asyncio.Event(),
                start_time=time.time(),
                task_id=uuid.uuid4().hex,
            )
            self._pending = my_pending
            self._task_finished = False
            # A new dispatch supersedes any prior completion's keep-going
            # eligibility. Only this task's own correlated task_finished frame
            # may arm a fresh 45-second timer.
            self._last_task_finished_at = 0.0
            return my_pending

    async def run_claimed_task(self, my_pending: PendingTask) -> Dict[str, Any]:
        """Send the already-claimed task to mc-agent and wait for its
        ``task_finished`` (or timeout / interrupt / disconnect). Caller
        must have obtained ``my_pending`` from :meth:`try_claim_pending`.

        Return shapes mirror the historical ``execute_minecraft_task``
        contract so detached-task done-callbacks and tests don't need to
        change:
            * ``{"status": "ok",          "query": ...}``       — finished
            * ``{"status": "timeout",     "query": ..., "reason": ...}``
            * ``{"status": "interrupted", "query": ..., "reason": ...}``
            * ``{"output": ..., "is_error": True, "error": "..."}``  — error
        """
        if self._client is None:
            async with self._pending_lock:
                if self._pending is my_pending:
                    self._pending = None
                    self._task_finished = True
                    self._mark_agent_idle()  # [BUSY] local terminal — drop stale latch
            return {
                "output": {
                    "error": "plugin is not started yet",
                    "query": my_pending.task_text,
                },
                "is_error": True,
                "error": "NOT_STARTED",
            }

        task = my_pending.task_text

        try:
            sent = await self._client.send_task(task, task_id=my_pending.task_id)
        except asyncio.CancelledError:
            async with self._pending_lock:
                if self._pending is my_pending:
                    self._pending = None
                    self._task_finished = True
            raise
        if sent:
            my_pending.dispatched = True
            self._remember_dispatched(my_pending.task_id, task)
        # ``send_task`` is a suspension point — overwrite / stop may
        # have already written a verdict during the suspend. Honor it
        # before deciding anything else.
        if my_pending.event.is_set():
            async with self._pending_lock:
                if self._pending is my_pending:
                    self._pending = None
            return my_pending.result or {"status": "ok", "query": task}

        if not sent:
            async with self._pending_lock:
                if self._pending is my_pending:
                    self._pending = None
                    self._task_finished = True
                    self._mark_agent_idle()  # [BUSY] local terminal — drop stale latch
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
                    self._task_finished = True
                    self._mark_agent_idle()  # [BUSY] local terminal — drop stale latch
            self._log_info("task timed out: {}", task[:80])
            return {
                "status": "timeout",
                "query": task,
                "reason": f"Not finished within {self._task_timeout:.0f}s.",
            }
        except asyncio.CancelledError:
            async with self._pending_lock:
                if self._pending is my_pending:
                    self._pending = None
                    self._task_finished = True
            raise

        async with self._pending_lock:
            if self._pending is my_pending:
                self._pending = None
        return my_pending.result or {"status": "ok", "query": task}

    async def execute_minecraft_task(
        self, *, task: str, overwrite: Any = False
    ) -> Dict[str, Any]:
        """Thin wrapper: claim + run in one call. Kept for smoke tests and
        unit tests that still drive the service synchronously. The plugin
        facade splits these calls (claim synchronously, run detached) so
        the dialog LLM can get an immediate busy answer.
        """
        if not isinstance(task, str) or not task.strip():
            return {
                "output": {"error": "task must be a non-empty string"},
                "is_error": True,
                "error": "INVALID_TASK",
            }
        overwrite_flag = overwrite is True
        claimed = await self.try_claim_pending(task, overwrite=overwrite_flag)
        if claimed is None:
            return {
                "result": "busy",
                "currently_executing": self.current_task_text() or "",
                "hint": "Set overwrite=true (boolean, not string) to interrupt the current task.",
            }
        return await self.run_claimed_task(claimed)

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
                # [COALESCE] agent narration is superseding — while the dialog
                # LLM is busy, older queued log pushes collapse so it only ever
                # reads the LATEST one, not a backlog it would recite in order.
                coalesce_key="mc_log",
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
            # Cancel any pending delayed flush — it would surface an
            # older frame just after we pushed the fresher one. Tight
            # race window: a previously-scheduled flush whose sleep
            # expires in the same event loop tick as this immediate
            # branch firing would emit its stale ``_pending_screenshot``
            # right after our push, reversing freshness order at the
            # dialog LLM.
            if self._screenshot_flush_task is not None and not self._screenshot_flush_task.done():
                self._screenshot_flush_task.cancel()
            self._screenshot_flush_task = None
            self._pending_screenshot = None
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
                # [COALESCE] older frames are obsolete the moment a newer one
                # exists — collapse queued screenshots so the LLM only sees the
                # freshest view.
                coalesce_key="mc_screenshot",
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
        # [CONTROL-LOG] Agent state transitions (task-ended latch, "action
        # selection" reset, connection-bounce → wake pending) must run on EVERY
        # frame, ahead of the anti-parrot/dedup early-returns below. Otherwise a
        # repeated control log — e.g. a second "Connection lost and re-established."
        # inside the dedup window — would be dropped before it could wake a
        # pending task, stranding the handler until ``task_timeout_seconds``.
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
                self._mark_agent_idle()  # [BUSY] local terminal — drop stale latch
        elif "action selection" in text_strip:
            # Setting False unconditionally is safe — at worst it
            # confirms what's already true (a task is in flight).
            self._task_finished = False
        elif text_strip == "Connection lost and re-established.":
            # Connection bounce wipes the agent's task queue: any
            # pending task's ``task_finished`` will never arrive, so
            # wake the handler with an "interrupted" verdict instead
            # of letting it sit on ``event.wait`` until timeout. Clear
            # the slot so the next ``minecraft_task`` isn't refused as
            # busy. ``_dispatched_history`` is left intact — if mc-agent
            # surprises us with a late frame for a known task after the
            # bounce, the retroactive cue path will still surface it.
            async with self._pending_lock:
                # [BUSY] a bounce wiped the agent's queue — any prior busy
                # bot_status_nl is stale; clear the latch (both branches) so the
                # recovery keep-going nudge this path anchors isn't suppressed for
                # the full _agent_busy_ttl, matching the task_finished/timeout paths.
                self._mark_agent_idle()
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
        # [ANTI-PARROT] drop internal command/telemetry lines entirely (no cache,
        # no push) so the dialog LLM never sees command syntax it would echo back
        # as a task. This is what stops the goToCoordinates/attackEntity dispatch
        # loop that was overriding the user's actual request.
        if _INJECTION_LOG_RE.search(text_strip):
            self._log_debug("[anti-parrot] dropped injection-shaped log: {}", text_strip[:80])
            return
        # [DEDUP] drop a line already seen within the dedup window so the
        # dialog LLM doesn't re-narrate the mc-agent's repeated status spam.
        # Refresh-on-hit keeps a persistently-looping line muted; prune the
        # map when it grows so it can't leak memory over a long session.
        _now = time.time()
        _seen_at = self._recent_log_times.get(text_strip)
        if _seen_at is not None and (_now - _seen_at) < self._log_dedup_window:
            self._recent_log_times[text_strip] = _now
            return
        self._recent_log_times[text_strip] = _now
        if len(self._recent_log_times) > 256:
            _cutoff = _now - self._log_dedup_window
            self._recent_log_times = {
                k: v for k, v in self._recent_log_times.items() if v >= _cutoff
            }
        self._log_cache.append(text_strip)

        # Inline push to the dialog LLM, rate-limited. The 5s autonomous
        # nudge loop is still authoritative for "burst the full state
        # alongside screenshots when nothing else is happening"; this
        # inline path is for "agent just said something, get it in front
        # of the dialog LLM now so it can weave into ongoing conversation
        # without 5s of staleness".
        self._schedule_inline_log_push(text_strip)

    def _normalize_screenshot_bytes(self, img_bytes: bytes, src_mime: str) -> tuple[bytes, str]:
        """Downscale + re-encode to JPEG under a *raw-bytes budget* so the pushed
        frame survives the message_plane payload cap.

        Capping resolution + quality alone is not enough: the wire payload encodes
        the frame as a base64 string (``binary_base64``, +~33%) **and** carries a
        raw ``binary_data`` copy, so the packed payload is ~2.3x the raw JPEG plus
        envelope. A high-detail 1024px/q80 frame can land at 150-250KB raw and
        still trip ``payload_too_big`` after expansion. So we treat
        ``_screenshot_max_bytes`` as a hard budget on the raw JPEG and step
        quality (then edge) down until the encode fits.

        Returns ``(bytes, mime)``. Falls back to the original bytes + their source
        mime if Pillow is missing or decoding fails — downstream tolerates either
        mime, and shipping an oversized original is still better than crashing the
        screenshot path. Pillow is already a transitive project dep (avatar / MMD
        pipelines)."""
        try:
            from PIL import Image
            import io

            budget = int(self._screenshot_max_bytes)
            base_edge = int(self._screenshot_max_edge_px)
            base_quality = int(self._screenshot_jpeg_quality)

            with Image.open(io.BytesIO(img_bytes)) as im:
                im = im.convert("RGB")
                # Edge ladder: start at the configured max, then halve twice as a
                # fallback for frames too dense to fit at full size/quality.
                edges: list[int] = []
                for e in (base_edge, base_edge // 2, base_edge // 4):
                    if e and e > 0 and e not in edges:
                        edges.append(e)
                if not edges:  # max_edge=0 → resizing disabled; encode at native size
                    edges = [max(im.size) or 1]
                # Quality ladder: never go above the configured quality.
                qualities = [q for q in (base_quality, 65, 50, 40, 30) if q <= base_quality]
                if not qualities:
                    qualities = [base_quality]

                smallest: bytes | None = None
                for edge in edges:
                    frame = im.copy()
                    if max(frame.size) > edge:
                        frame.thumbnail((edge, edge))  # aspect-preserving, shrink-only
                    for ql in qualities:
                        buf = io.BytesIO()
                        frame.save(buf, format="JPEG", quality=ql, optimize=True)
                        data = buf.getvalue()
                        if budget <= 0 or len(data) <= budget:
                            return data, "image/jpeg"
                        if smallest is None or len(data) < len(smallest):
                            smallest = data
                # Nothing fit the budget — ship the smallest attempt (best effort).
                # Still far better than the original full-res frame, and the ingest
                # drop diagnostic will surface it if it's somehow still over cap.
                self._log_warning(
                    "screenshot still over budget after downscale (smallest={} > budget={}); shipping smallest",
                    len(smallest) if smallest else -1, budget,
                )
                return (smallest if smallest is not None else img_bytes), "image/jpeg"
        except Exception as exc:
            self._log_warning(
                "screenshot downscale failed, shipping original bytes as-is: {}: {}",
                type(exc).__name__, exc,
            )
            return img_bytes, src_mime

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
        # encoding is empty. ``src_mime`` is only the fallback tag used when
        # Pillow is unavailable and we ship the original bytes untouched.
        enc_lower = (encoding or "").lower()
        is_jpeg_explicit = "jpeg" in enc_lower or "jpg" in enc_lower
        is_jpeg_embedded = embedded_mime in ("image/jpeg", "image/jpg")
        src_mime = "image/jpeg" if (is_jpeg_explicit or (not enc_lower and is_jpeg_embedded)) else "image/png"

        # Downscale + recompress so the pushed frame fits the message_plane
        # payload cap. The previous JPEG→lossless-PNG conversion (done here to
        # please Gemini's realtime input) inflated ~100KB frames into 1.5-4MB,
        # which blew past NEKO_MESSAGE_PLANE_PAYLOAD_MAX_BYTES (default 256KB)
        # and got silently dropped at ingest — so the dialog LLM never saw a
        # fresh frame. A vision model needs neither 4MP nor lossless; bounding
        # the long edge + JPEG keeps every frame comfortably under the cap.
        img_bytes, mime = self._normalize_screenshot_bytes(img_bytes, src_mime)

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

        Forwarded with ``ai_behavior="respond"`` + ``priority=9`` (highest on
        the repo-wide HIGHER=more-important scale) so the
        dialog LLM hears about a death immediately, not 5s later on a
        nudge tick. ``cause`` (when mc-agent could infer one — nearby
        hostile, lava, fall, etc.) is rendered as a hint inside the cue
        so the character can narrate the actual reason instead of
        inventing one (the historical UX problem: 猫娘 saw HP drop and
        made up "被怪物打了" with no evidence).
        """
        text = str(data.get("text") or "").strip()
        if not text:
            return
        severity = str(data.get("severity") or "warn").lower()
        cause_hint = self._format_alert_cause(data.get("cause"))

        sections = [prompts.t(
            "CUE_PREFIX_ALERT", lang=self._lang, severity=severity, text=text,
        )]
        if cause_hint:
            sections.append(prompts.t(
                "ALERT_CAUSE_HINT_PREFIX", lang=self._lang, hint=cause_hint,
            ))
        sections.append(prompts.t("ALERT_FOLLOWUP", lang=self._lang))
        body = "\n".join(sections)
        try:
            self._push_message(
                source="game_agent_minecraft",
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": body}],
                priority=9,
                coalesce_key="mc_alert",
            )
        except Exception as exc:
            self._log_error(
                "alert push failed: {}: {}", type(exc).__name__, exc,
            )

    def _format_alert_cause(self, cause: Any) -> str:
        """Turn mc-agent's structured ``cause`` hint dict into one short
        phrase the dialog LLM can paraphrase, localized to ``self._lang``.
        mc-agent populates the dict best-effort (see agent.js
        ``_inferDamageCause``); we accept any subset and emit empty
        string when nothing useful is inside so callers can skip the line.
        """
        if not isinstance(cause, dict) or not cause:
            return ""
        parts: list[str] = []
        env = cause.get("environment")
        env_key_map = {
            "lava": "CAUSE_ENV_LAVA",
            "fire": "CAUSE_ENV_FIRE",
            "soul_fire": "CAUSE_ENV_SOUL_FIRE",
            "drowning": "CAUSE_ENV_DROWNING",
            "magma_block": "CAUSE_ENV_MAGMA_BLOCK",
            "cactus": "CAUSE_ENV_CACTUS",
            "sweet_berry_bush": "CAUSE_ENV_SWEET_BERRY_BUSH",
        }
        if isinstance(env, str) and env in env_key_map:
            parts.append(prompts.t(env_key_map[env], lang=self._lang))
        elif isinstance(env, str) and env:
            parts.append(prompts.t("CAUSE_ENV_GENERIC", lang=self._lang, env=env))
        if cause.get("fall"):
            parts.append(prompts.t("CAUSE_FALL", lang=self._lang))
        attacker = cause.get("attacker")
        if isinstance(attacker, dict):
            kind = str(attacker.get("kind") or "").strip()
            dist = attacker.get("distance")
            name = str(attacker.get("name") or "").strip()
            if kind == "player" and name:
                # Player attackers carry the username so the dialog LLM
                # can name them. Without the name we'd say "player nearby"
                # which leaks the technical kind word and reads weird.
                if isinstance(dist, (int, float)):
                    parts.append(prompts.t(
                        "CAUSE_ATTACKER_PLAYER_NEAR_DIST",
                        lang=self._lang, name=name, dist=dist,
                    ))
                else:
                    parts.append(prompts.t(
                        "CAUSE_ATTACKER_PLAYER_NEAR",
                        lang=self._lang, name=name,
                    ))
            elif kind:
                if isinstance(dist, (int, float)):
                    parts.append(prompts.t(
                        "CAUSE_ATTACKER_KIND_DIST",
                        lang=self._lang, kind=kind, dist=dist,
                    ))
                else:
                    parts.append(prompts.t(
                        "CAUSE_ATTACKER_KIND", lang=self._lang, kind=kind,
                    ))
        return prompts.t("CAUSE_JOIN_SEP", lang=self._lang).join(parts)

    def _push_retroactive_completion_cue(self, info: Dict[str, Any]) -> None:
        """Tell the dialog LLM the fate of a previously-dispatched task she's
        no longer waiting on (one she explicitly overwrote, or that timed out
        on this side but kept running on mc-agent). Without this she'd keep
        narrating "I'm doing X" or fall silent — both wrong once the task has
        reached a terminal state on mc-agent. The header honestly reflects
        that state: a genuine completion says "actually finished"; a
        non-completion (interrupted/superseded/failed/timeout, or status=ok
        whose text signals it was blocked) says "ended without completing" so
        she never narrates a phantom success.
        """
        task_text = str(
            info.get("task_text")
            or prompts.t("PLACEHOLDER_UNKNOWN", lang=self._lang)
        )
        status = str(info.get("status") or "ok")
        # [NO-SWITCH-CUE] same as the live cue: a late interrupted/superseded
        # frame is a task the LLM already replaced on purpose. Re-surfacing it
        # only nudges another dispatch (the retry storm). Drop it silently.
        if status.strip().lower() in ("interrupted", "superseded"):
            self._log_debug("[no-switch-cue] skipping retroactive cue for status={}", status)
            return
        text = str(info.get("text") or "").strip()
        inv = info.get("inventory")

        # Only assert "actually finished" when the late frame reports a
        # genuine completion. Two ways it can fail to be genuine:
        #   1. an explicit non-completion status — mc-agent now distinguishes
        #      ``interrupted`` / ``superseded`` / ``failed`` / ``timeout``;
        #   2. status=="ok" but the free-text feedback signals the action was
        #      actually blocked (target not found, no path…) — the same
        #      status=ok-but-really-blocked quirk the live completion cue
        #      re-labels (see ``text_signals_blocked``). Without this second
        #      check the retroactive cue would say "其实跑完了（结果 ok）" while
        #      its own feedback line shows "could not find path" — re-telling
        #      猫娘 a success that never happened.
        # ``.strip().lower()`` so a whitespace-padded "ok " still reads as ok.
        is_genuine_ok = (
            status.strip().lower() == "ok" and not text_signals_blocked(text)
        )
        header_key = "RETROACTIVE_HEADER" if is_genuine_ok else "RETROACTIVE_HEADER_ENDED"
        sections = [prompts.t(
            header_key, lang=self._lang,
            task_text=task_text[:100], status=status,
        )]
        if text:
            sections.append(prompts.t(
                "COMPLETION_FEEDBACK_LINE", lang=self._lang, detail=text[:240],
            ))
        if isinstance(inv, dict) and inv:
            items = sorted(((str(k), int(v)) for k, v in inv.items() if int(v) > 0),
                           key=lambda kv: -kv[1])
            if items:
                snippet = "、".join(f"{n}×{c}" for n, c in items[:15])
                sections.append(prompts.t(
                    "RETROACTIVE_INVENTORY_LINE", lang=self._lang, snippet=snippet,
                ))
        sections.append(prompts.t("RETROACTIVE_FOLLOWUP", lang=self._lang))
        sections.append(prompts.t("LATEST_MASTER_TOOL_GUARD", lang=self._lang))
        body = prompts.t("CUE_PREFIX_DONE", lang=self._lang) + "\n" + "\n".join(sections)
        try:
            self._push_message(
                source="game_agent_minecraft",
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": body}],
                priority=7,
                coalesce_key="mc_completion",
            )
        except Exception as exc:
            self._log_error(
                "retroactive completion push failed: {}: {}",
                type(exc).__name__, exc,
            )

    async def _on_task_finished(self, data: Dict[str, Any]) -> None:
        raw_inv = data.get("inventory")
        parsed_inv: Optional[Dict[str, int]] = None
        if isinstance(raw_inv, dict):
            parsed_inv = {
                str(k): int(v) for k, v in raw_inv.items()
                if isinstance(v, (int, float)) and int(v) > 0
            }

        text = str(
            data.get("text") or data.get("data") or data.get("message") or ""
        )
        # [ANTI-PARROT] a completion's free-text can carry the same mc-agent
        # command/telemetry syntax the _on_log filter drops (e.g. "Finished
        # goToCoordinates(118,64,-115,1)"). This ``text`` feeds both _log_cache
        # (→ RECENT_EVENTS_BLOCK) and the completion / retroactive cue, so scrub
        # the command syntax here at its single derivation point before any
        # model-visible sink sees it — otherwise the dialog LLM re-dispatches the
        # raw command. Scrub only the matched command syntax (``sub``, don't blank
        # the whole line): a status="ok" completion whose text ALSO explains a
        # block ("Failed goToCoordinates(...): could not find path") must keep the
        # "could not find path" signal so ``text_signals_blocked`` still fires and
        # the blocked action isn't relabeled a success.
        if text and _INJECTION_LOG_RE.search(text):
            scrubbed = _INJECTION_LOG_RE.sub("", text).strip()
            self._log_debug(
                "[anti-parrot] scrubbed command syntax from completion text: {!r} -> {!r}",
                text[:80], scrubbed[:80],
            )
            text = scrubbed
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

        # Outcome of the in-lock classification, drained outside so
        # ``push_message`` (which may take its own locks downstream)
        # doesn't run under our ``_pending_lock``.
        retroactive: Optional[Dict[str, Any]] = None

        async with self._pending_lock:
            # Four classification buckets:
            #   (a) echoed matches current pending → wake the handler
            #   (b) echoed in dispatched history → retroactive cue
            #       (an earlier task — usually one we overwrote — really
            #       finished on mc-agent, surface that to the dialog LLM
            #       so猫娘 knows the prior action actually completed)
            #   (c) no task_id or unknown id, but ``_pending`` is set →
            #       FIFO fallback: treat as completion of current pending
            #       (covers legacy mc-agent builds that don't echo id)
            #   (d) no pending + unknown id → idle drift; just update
            #       the busy gate so the nudge loop can resume.
            pending = self._pending
            historical_text: Optional[str] = None
            if echoed_task_id is not None:
                if pending is not None and pending.task_id == echoed_task_id:
                    bucket = "current"
                    # Only flip the latch once the id has been proven to
                    # belong to OUR dispatch (current pending or recent
                    # history). A foreign id (leaked from another client
                    # on the same WS endpoint, mc-agent restart crossover,
                    # buffered prior-session frame) lands in "unknown" and
                    # must NOT flip the latch — that would permanently
                    # disable the FIFO fallback for legacy agents that
                    # never echo their own ids. Per Codex review on PR
                    # #1395.
                    self._seen_task_id_echo = True
                elif echoed_task_id in self._dispatched_history:
                    bucket = "retroactive"
                    historical_text = self._dispatched_history.get(echoed_task_id)
                    self._seen_task_id_echo = True
                else:
                    bucket = "unknown"
            elif pending is not None and not self._seen_task_id_echo:
                # Legacy agent that has never echoed task_id → FIFO it
                # onto current pending. We're conservative about flipping
                # into this branch: once any frame has carried task_id
                # (``_seen_task_id_echo`` latched True), an id-less
                # frame is anomalous and routes to ``stray`` instead —
                # otherwise an out-of-order/stale completion from a
                # task we already overwrote could silently resolve the
                # new pending with the old payload.
                bucket = "fifo"
            else:
                bucket = "stray"

            # Only commit the inventory snapshot for frames we accept as
            # belonging to a task we actually dispatched. unknown / stray
            # frames could be from a ghost task_id (mc-agent leftover
            # from a prior session, restart crossover, another client
            # sharing the same WS endpoint) — letting them overwrite
            # `_last_inventory` would poison query_inventory and nudge
            # ground truth with state from an unrelated context.
            # Per Codex review on PR #1395.
            if parsed_inv is not None and bucket in ("current", "fifo", "retroactive"):
                self._last_inventory = parsed_inv
                self._last_inventory_at = time.time()

            if bucket == "current":
                self._pending = None
                if text:
                    self._log_cache.append(text)
                result_payload: Dict[str, Any] = {
                    "status": status,
                    "query": pending.task_text,
                }
                if text:
                    result_payload["text"] = text
                if parsed_inv is not None:
                    result_payload["inventory"] = dict(parsed_inv)
                pending.result = result_payload
                pending.event.set()
                self._task_finished = True
                # This is a correlated terminal frame for the current task, so
                # it is the only trustworthy completion clock. Status affects
                # the completion cue, not whether the task has really ended.
                self._last_task_finished_at = time.time()
                # [BUSY] a dispatched task just reached a terminal frame — the
                # agent is no longer mid-action on it. Clear the busy flag now so
                # the keep-going nudge isn't suppressed for the full
                # ``_agent_busy_ttl`` while the agent sits idle awaiting the next
                # dispatch; a fresh bot_status_nl re-arms it if the agent starts
                # something on its own.
                self._mark_agent_idle()
            elif bucket == "fifo":
                # Legacy agent without task_id echo: pending exists, so
                # treat this as its completion.
                self._pending = None
                if text:
                    self._log_cache.append(text)
                result_payload = {
                    "status": status,
                    "query": pending.task_text,
                }
                if text:
                    result_payload["text"] = text
                if parsed_inv is not None:
                    result_payload["inventory"] = dict(parsed_inv)
                pending.result = result_payload
                pending.event.set()
                self._task_finished = True
                self._last_task_finished_at = time.time()
                # [BUSY] see the "current" branch — clear the busy flag on a
                # completed dispatch so the nudge loop can resume promptly.
                self._mark_agent_idle()
            elif bucket == "retroactive":
                # A previously-dispatched task (now no longer pending)
                # actually finished — emit a cue so the dialog LLM
                # learns the action completed instead of holding a
                # stale "still doing it" belief in its narration.
                retroactive = {
                    "task_text": historical_text or "(unknown)",
                    "status": status,
                    "text": text,
                    "inventory": dict(parsed_inv) if parsed_inv is not None else None,
                }
                # Don't touch ``_task_finished`` — current pending is
                # genuinely still running.
            else:  # "unknown" or "stray"
                # No pending and no known dispatch → drift (e.g. an autonomous
                # task_finished). Mark idle so the nudge loop resumes, and clear
                # the busy latch too: a terminal frame proves the agent reached
                # idle, so the recovery/keep-going nudge shouldn't be suppressed
                # for the full _agent_busy_ttl. A fresh bot_status_nl re-arms it
                # if the agent starts something new.
                if pending is None:
                    self._task_finished = True
                    self._mark_agent_idle()  # [BUSY] terminal frame — drop stale latch

        # Emit the retroactive cue outside the lock.
        if retroactive is not None:
            self._push_retroactive_completion_cue(retroactive)

    # ------------------------------------------------------------------
    # Autonomous system-prompt loop
    # ------------------------------------------------------------------

    def _keep_going_due(
        self, now: float, *, min_delay: float, cooldown: float
    ) -> bool:
        """Return whether confirmed idle completion is due another turn."""
        finished_at = self._last_task_finished_at
        return (
            self._task_finished
            and self._pending is None
            and finished_at > 0
            and now - finished_at >= min_delay
            and now - self._last_keep_going_nudge_at >= cooldown
            # A self-reported autonomous action means the character is not
            # actually idle; wait for its terminal state instead.
            and not self._mc_agent_busy()
        )

    async def _system_prompt_loop(self) -> None:
        """Periodically nudge the dialog LLM. Three branches, each with
        its own rate limiter:

        * **In-progress nudge** (highest priority when applicable). Fires
          when a task has been pending ≥10s and the last in-progress
          nudge was ≥10s ago. Tells the dialog LLM "your body is still
          doing X — narrate what you're feeling in your own voice (don't
          repeat yourself)". Without this branch, long actions (mining,
          pathfinding) leave the user hearing nothing for 30+ seconds.

        * **Keep-going nudge**. After a correlated task completion has remained
          idle for 45 seconds, wakes the dialog LLM every 45 seconds while idle.
          The prompt may continue the conversation but may dispatch only a new
          command from master's most recent message; old commands are never
          recalled or replayed.

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
        _IN_PROGRESS_AFTER = 30.0      # [THROTTLE] was 8.0
        _IN_PROGRESS_COOLDOWN = 30.0   # [THROTTLE] was 8.0
        _KEEP_GOING_AFTER = 45.0
        _KEEP_GOING_COOLDOWN = 45.0

        self._log_debug(
            "system_prompt_loop started (in_progress={}/{}, "
            "keep_going={}/{}, "
            "general_interval={}s)",
            _IN_PROGRESS_AFTER, _IN_PROGRESS_COOLDOWN,
            _KEEP_GOING_AFTER, _KEEP_GOING_COOLDOWN,
            self._system_prompt_interval,
        )
        # [ISSUE4a] Per-iteration try/except (NOT a recursive restart): a single
        # tick raising used to fall through and RETURN, killing self-prompt for
        # the rest of the session. We catch each iteration, log, briefly pause to
        # avoid hot-spin, and CONTINUE the same loop — iterative, so a persistent
        # exception can never grow the call stack into RecursionError. Only
        # CancelledError exits cleanly.
        while True:
            try:
                await asyncio.sleep(0.5)
                now = time.time()

                # ---- Branch 1: in-progress nudge ----
                if self._pending is not None and not self._task_finished:
                    elapsed_pending = now - self._pending.start_time
                    since_last = now - self._last_in_progress_nudge_at
                    if elapsed_pending >= _IN_PROGRESS_AFTER and since_last >= _IN_PROGRESS_COOLDOWN:
                        self._log_debug(
                            "firing in_progress nudge (elapsed={:.1f}s, since_last={:.1f}s)",
                            elapsed_pending, since_last,
                        )
                        await self._fire_in_progress_nudge()
                        self._last_in_progress_nudge_at = now
                    # When a task is in flight, do NOT also fire the
                    # general nudge — that would stack two prompts on
                    # the dialog LLM's queue for the same situation.
                    continue

                # ---- Branch 2: retained keep-going, paced after real finish ----
                if self._keep_going_due(
                    now,
                    min_delay=_KEEP_GOING_AFTER,
                    cooldown=_KEEP_GOING_COOLDOWN,
                ):
                    since_finish = now - self._last_task_finished_at
                    self._log_debug(
                        "firing keep_going nudge "
                        "(since_finish={:.1f}s)",
                        since_finish,
                    )
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
                self._log_debug(
                    "firing general nudge (task_finished={}, pending={})",
                    self._task_finished,
                    self._pending.task_text[:40] if self._pending else None,
                )
                await self._fire_system_prompt()
                self._last_system_prompt_time = time.time()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._log_error(
                    "system prompt loop iteration failed (continuing): {}: {}",
                    type(exc).__name__, exc,
                )
                try:
                    await asyncio.sleep(2.0)
                except asyncio.CancelledError:
                    return

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

        pending_text = (
            self._pending.task_text if self._pending
            else prompts.t("PLACEHOLDER_UNKNOWN", lang=self._lang)
        )
        elapsed = (time.time() - self._pending.start_time) if self._pending else 0.0
        sections = [prompts.t(
            "IN_PROGRESS_HEADER", lang=self._lang,
            pending_text=pending_text[:120], elapsed=f"{elapsed:.0f}",
        )]
        inv_line = self._inventory_section_if_changed(top_n=15)
        if inv_line:
            sections.append(inv_line)
        sections.append(prompts.t("IN_PROGRESS_FOLLOWUP", lang=self._lang))
        body_text = prompts.t("CUE_PREFIX_IN_PROGRESS", lang=self._lang) + "\n" + "\n".join(sections)
        parts.append({"type": "text", "text": body_text})

        try:
            self._push_message(
                source="game_agent_minecraft",
                visibility=[],
                ai_behavior="respond",
                parts=parts,
                priority=4,
                coalesce_key="mc_in_progress",
            )
        except Exception as exc:
            self._log_error(
                "in-progress nudge push failed: {}: {}", type(exc).__name__, exc,
            )

    async def _fire_keep_going_nudge(self) -> None:
        """Wake the dialog LLM after 45s of confirmed post-task idle."""
        sections: list[str] = []
        inv_line = self._inventory_section_if_changed(top_n=20)
        if inv_line:
            sections.append(inv_line)
        sections.append(prompts.t("KEEP_GOING_BODY", lang=self._lang))
        sections.append(prompts.t("LATEST_MASTER_TOOL_GUARD", lang=self._lang))
        body_text = prompts.t("CUE_PREFIX_IDLE", lang=self._lang) + "\n" + "\n".join(sections)
        parts: list[Dict[str, Any]] = [{"type": "text", "text": body_text}]
        try:
            self._push_message(
                source="game_agent_minecraft",
                visibility=[],
                ai_behavior="respond",
                parts=parts,
                priority=3,
                coalesce_key="mc_keep_going",
            )
        except Exception as exc:
            self._log_error(
                "keep-going nudge push failed: {}: {}", type(exc).__name__, exc,
            )

    async def _fire_system_prompt(self) -> None:
        """Build + push the autonomous nudge.

        Body shape mirrors the original integration: a "GAME_SYSTEM" header,
        the recent agent log snippet, and an awareness tail that distinguishes
        idle from running state without instructing the model to dispatch.
        """
        log_text = ""
        if self._log_cache:
            log_text = _ANSI_RE.sub("", "\n".join(self._log_cache))
            self._log_cache.clear()

        sections: list[str] = []
        # Inventory line first — it's the closest thing to ground truth
        # we have, and the dialog LLM should know it before narrating
        # anything that depends on owned items.
        inv_line = self._inventory_section_if_changed(top_n=20)
        if inv_line:
            sections.append(inv_line)
        if self._pending is not None:
            sections.append(prompts.t(
                "CURRENT_TASK_LINE", lang=self._lang,
                task_text=self._pending.task_text,
            ))
        if log_text:
            sections.append(prompts.t(
                "RECENT_EVENTS_BLOCK", lang=self._lang, log_text=log_text,
            ))
        # [BUSY] The mc-agent's own activity counts as busy too: in autonomous
        # play the plugin has no _pending task, so without this it would always
        # look idle and keep telling her to dispatch. Use the BUSY body (narrate
        # / stay quiet) whenever the agent is actively executing.
        if self._task_finished and not self._mc_agent_busy():
            sections.append(prompts.t("SYSTEM_PROMPT_IDLE_BODY", lang=self._lang))
        else:
            sections.append(prompts.t("SYSTEM_PROMPT_BUSY_BODY", lang=self._lang))
        sections.append(prompts.t("LATEST_MASTER_TOOL_GUARD", lang=self._lang))
        prompt_text = prompts.t("CUE_PREFIX_STATE", lang=self._lang) + "\n" + "\n".join(sections)

        # Build the parts list: cached screenshots first (so the LLM
        # has visual context when it reads the prompt), then the
        # GAME_SYSTEM text. Drain the cache after building the parts
        # list so a flake on push_message doesn't re-send the same
        # screenshots forever.
        parts: list[Dict[str, Any]] = []
        screenshots = list(self._screenshot_cache)
        self._screenshot_cache.clear()
        # Bundle ONLY the most recent frame. Each cached frame is already capped
        # at ``_screenshot_max_bytes`` individually, but stacking several into one
        # push blows the message_plane payload cap: every frame is base64'd
        # (~+37%) AND the legacy ``binary_data`` field carries a raw copy, so even
        # two frames exceed 256KB and the whole burst is silently dropped at
        # ingest. The latest frame is the most relevant; older cached frames are
        # dropped rather than risk losing the entire visual+text cue.
        if screenshots:
            img_bytes, img_mime = screenshots[-1]
            # Preserve the per-frame mime — see ``_screenshot_cache`` field
            # comment for why this isn't always image/png.
            parts.append({"type": "image", "data": img_bytes, "mime": img_mime})
        parts.append({"type": "text", "text": prompt_text})

        try:
            # General periodic state burst (inventory + recent log +
            # screenshots). This is passive CONTEXT, not a "speak now" cue:
            # ai_behavior="read" injects it into the model's context without
            # forcing an AI turn, so it can't make her narrate non-stop or
            # compete with real alert/completion cues in the pacing manager.
            # The specific nudges (in_progress / keep_going) remain "respond".
            self._push_message(
                source="game_agent_minecraft",
                visibility=[],
                ai_behavior="read",
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

    def _log_debug(self, msg: str, *args: Any) -> None:
        if self.logger is not None:
            try:
                self.logger.debug("[GameAgent] " + msg, *args)
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


__all__ = ["GameAgentService", "PendingTask", "text_signals_blocked"]
