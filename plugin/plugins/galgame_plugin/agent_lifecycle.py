from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403
from .agent_message_router import AgentMessageRouter
from .agent_scene_tracker import AgentSceneTracker


class AgentLifecycleMixin:
    def __init__(
        self,
        *,
        plugin,
        logger,
        llm_gateway,
        host_adapter: HostAgentAdapter,
        config: GalgameLLMConfig | None = None,
        local_input_actuator: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
        | None = None,
    ) -> None:
        self._plugin = plugin
        self._logger = logger
        self._llm_gateway = llm_gateway
        self._host_adapter = host_adapter
        self._context_config = config
        self._scene_summary_push_line_interval = max(
            1,
            int(
                getattr(
                    config,
                    "scene_summary_push_line_interval",
                    self._SCENE_SUMMARY_PUSH_LINE_INTERVAL,
                )
                or self._SCENE_SUMMARY_PUSH_LINE_INTERVAL
            ),
        )
        self._scene_push_half_threshold = max(
            1,
            int(
                getattr(
                    config,
                    "scene_push_half_threshold",
                    self._SCENE_PUSH_HALF_THRESHOLD,
                )
                or self._SCENE_PUSH_HALF_THRESHOLD
            ),
        )
        self._scene_push_time_fallback_seconds = max(
            0.0,
            float(
                getattr(
                    config,
                    "scene_push_time_fallback_seconds",
                    self._SCENE_PUSH_TIME_FALLBACK_SECONDS,
                )
                or self._SCENE_PUSH_TIME_FALLBACK_SECONDS
            ),
        )
        self._scene_merge_total_threshold = max(
            1,
            int(
                getattr(
                    config,
                    "scene_merge_total_threshold",
                    self._SCENE_MERGE_TOTAL_THRESHOLD,
                )
                or self._SCENE_MERGE_TOTAL_THRESHOLD
            ),
        )
        self._scene_cross_scene_total_threshold = max(
            1,
            int(
                getattr(
                    config,
                    "scene_cross_scene_total_threshold",
                    self._SCENE_CROSS_SCENE_TOTAL_THRESHOLD,
                )
                or self._SCENE_CROSS_SCENE_TOTAL_THRESHOLD
            ),
        )
        self._local_input_actuator = local_input_actuator or perform_local_input_actuation
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        self._op_lock: asyncio.Lock | None = None
        self._explicit_standby = False
        self._hard_error = ""
        self._hard_error_retryable = False
        self._planning_task: asyncio.Task[dict[str, Any]] | None = None
        self._planning_choice_signature: tuple[tuple[str, str, int], ...] = ()
        self._planning_candidates: list[dict[str, Any]] = []
        self._planning_started_at = 0.0
        self._actuation: dict[str, Any] | None = None
        self._starting_actuation = False
        self._start_generation = 0
        self._pending_strategy: dict[str, Any] | None = None
        self._next_actuation_at = 0.0
        self._last_focus_attempt_at = 0.0
        self._focus_failure_count = 0
        self._ocr_choice_fallback_attempts = 0
        self._scene_tracker = AgentSceneTracker(
            seen_line_limit=self._SUMMARY_SEEN_LINE_KEYS_LIMIT,
        )
        self._message_router = AgentMessageRouter(now_factory=self._utc_now_iso)
        self._last_interruption = {}
        self._pending_choice_advice: dict[str, Any] | None = None
        self._summary_tasks: set[asyncio.Task[bool]] = set()
        self._summary_task_meta: dict[asyncio.Task[bool], dict[str, Any]] = {}
        self._consultation_tasks: set[asyncio.Task[bool]] = set()
        self._pending_consults: set[str] = set()
        self._summary_generation = 0
        self._summary_debug: dict[str, Any] = {}
        self._failure_memory: list[dict[str, Any]] = []
        self._recent_local_inputs: list[dict[str, Any]] = []
        self._virtual_mouse_stats: dict[str, dict[str, Any]] = {}
        self._suggestion_reasons: dict[str, str] = {}
        self._observed_session_id = ""
        self._observed_session_fingerprint: dict[str, Any] = {}
        # host-play-mode plan, steps 8 + 10 + 12 + 13.
        self._last_cat_consult_ts: float = 0.0
        self._lines_seen_for_consult: int = 0
        self._last_consult_seen_line_count: int = 0
        self._cat_opinions: list[dict[str, Any]] = []
        self._push_seq_counter: int = 0
        self._cross_scene_memory_dirty: bool = False
        self._push_composer = PushComposer(logger=self._logger)
        self._last_session_transition_type = ""
        self._last_session_transition_reason = ""
        self._last_session_transition_fields: dict[str, Any] = {}
        self._session_transition_actuation_blocked = False
        self._observed_scene_id = ""
        self._observed_route_id = ""
        self._observed_choice_marker = ""
        self._observed_context_boundary: dict[str, str] = {}
        self._observed_context_boundary_key = ""
        self._observed_virtual_mouse_runtime_key = ""
        self._ocr_no_observed_advance_count = 0
        self._ocr_last_progress_seq = 0
        self._advance_retry_budget: dict[str, int] = {}
        self._ocr_hold_release_budget: dict[str, int] = {}
        self._ocr_capture_diagnostic = ""
        self._ocr_capture_diagnostic_set_at = 0.0
        self._screen_recovery_diagnostic = ""
        self._computer_use_quota_bypass_until = 0.0
        self._local_task_seq = 0
        self._scene_state = self._build_empty_scene_state()
        self._last_status = AGENT_STATUS_STANDBY
        self._last_trace_message = ""
        self._last_push_ts: float = 0.0
        self._pending_merge_scene_ids: list[str] | None = None
        self._pending_merge_primary: str = ""
        self._pending_cross_scene_primary: str = ""
        self._last_delivered_summary_key = ""
        self._last_delivered_summary_seq = 0
        self._last_delivered_summary_scene_id = ""
        self._agent_reply_lock: asyncio.Lock | None = None

    def _reset_consult_state(self) -> None:
        self._last_cat_consult_ts = 0.0
        self._lines_seen_for_consult = 0
        self._last_consult_seen_line_count = 0
        self._pending_consults.clear()

    def _ensure_loop_affinity(self) -> None:
        loop = asyncio.get_running_loop()
        if (
            self._runtime_loop is loop
            and self._op_lock is not None
            and self._agent_reply_lock is not None
        ):
            return
        if self._runtime_loop is not None and self._runtime_loop is not loop:
            self._clear_loop_bound_state()
        self._runtime_loop = loop
        self._op_lock = asyncio.Lock()
        self._agent_reply_lock = asyncio.Lock()

    def _clear_loop_bound_state(self) -> None:
        if self._planning_task is not None:
            self._cancel_foreign_task(self._planning_task)
            self._planning_task = None
        self._planning_candidates = []
        self._planning_choice_signature = ()
        self._planning_started_at = 0.0
        self._starting_actuation = False
        self._start_generation += 1

    @staticmethod
    def _cancel_foreign_task(task: asyncio.Task[Any]) -> None:
        try:
            task_loop = task.get_loop()
        except Exception:
            logging.getLogger(__name__).warning(
                "galgame _cancel_foreign_task: get_loop failed",
                exc_info=True,
            )
            return
        if task.done():
            return
        try:
            if task_loop.is_closed():
                return

            def _cancel_if_pending() -> None:
                if not task.done():
                    task.cancel()

            task_loop.call_soon_threadsafe(_cancel_if_pending)
        except RuntimeError:
            return

    def _cancel_summary_tasks(self) -> None:
        if not self._summary_tasks:
            return
        self._summary_generation += 1
        self._summary_debug["last_task_cancelled"] = {
            "reason": "cancel_summary_tasks",
            "pending_count": len(self._summary_tasks),
            "ts": self._utc_now_iso(),
        }
        for task in list(self._summary_tasks):
            if not task.done():
                task.cancel()
        self._summary_tasks.clear()
        self._summary_task_meta.clear()

    async def _cancel_consultation_tasks(self) -> None:
        if not self._consultation_tasks:
            return
        current = asyncio.current_task()
        tasks = [
            task
            for task in list(self._consultation_tasks)
            if task is not current
        ]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in tasks:
            self._consultation_tasks.discard(task)
        self._pending_consults.clear()

    async def drain_summary_tasks(self, *, timeout: float = 30.0) -> None:
        tasks = list(self._summary_tasks)
        if not tasks:
            return
        bounded_timeout = max(0.1, float(timeout or 30.0))
        done, pending = await asyncio.wait(tasks, timeout=bounded_timeout)
        if pending:
            self._record_summary_task_event(
                "drain_timeout",
                {
                    "reason": "summary_task_drain_timeout",
                    "timeout_seconds": bounded_timeout,
                    "pending_count": len(pending),
                },
            )
            # Timer ticks run in short-lived event loops. Returning while summary
            # tasks are still pending lets the loop shutdown cancel them, so a
            # drain timeout must be diagnostic-only here.
            await asyncio.gather(*pending, return_exceptions=True)
        if done:
            await asyncio.gather(*done, return_exceptions=True)

    async def shutdown(self) -> None:
        self._ensure_loop_affinity()
        await self._reset_runtime_state(cancel_host_task=True, clear_retry=True)
        self._clear_hard_error()
        self._scene_tracker.reset()
        self._summary_debug.clear()
        self._last_delivered_summary_key = ""
        self._last_delivered_summary_seq = 0
        self._last_delivered_summary_scene_id = ""
        self._inbound_messages.clear()
        self._outbound_messages.clear()
        self._last_interruption = {}
        self._pending_choice_advice = None
        self._cancel_summary_tasks()
        await self._cancel_consultation_tasks()
        self._failure_memory.clear()
        self._recent_local_inputs.clear()
        self._virtual_mouse_stats.clear()
        self._suggestion_reasons.clear()
        self._observed_session_id = ""
        self._observed_session_fingerprint = {}
        self._reset_consult_state()
        self._cat_opinions.clear()
        self._last_session_transition_type = ""
        self._last_session_transition_reason = ""
        self._last_session_transition_fields = {}
        self._session_transition_actuation_blocked = False
        self._observed_scene_id = ""
        self._observed_route_id = ""
        self._observed_choice_marker = ""
        self._observed_context_boundary = {}
        self._observed_context_boundary_key = ""
        self._observed_virtual_mouse_runtime_key = ""
        self._ocr_no_observed_advance_count = 0
        self._ocr_last_progress_seq = 0
        self._advance_retry_budget.clear()
        self._ocr_hold_release_budget.clear()
        self._ocr_capture_diagnostic = ""
        self._ocr_capture_diagnostic_set_at = 0.0
        self._screen_recovery_diagnostic = ""
        self._computer_use_quota_bypass_until = 0.0
        self._local_task_seq = 0
        self._next_actuation_at = 0.0
        self._last_focus_attempt_at = 0.0
        self._focus_failure_count = 0
        self._ocr_choice_fallback_attempts = 0
        self._scene_state = self._build_empty_scene_state()
        self._last_status = AGENT_STATUS_STANDBY
        self._last_trace_message = ""
        self._last_push_ts = 0.0
        self._pending_merge_primary = ""
        self._pending_merge_scene_ids = None
        self._pending_cross_scene_primary = ""

    async def _reset_runtime_state(
        self,
        *,
        cancel_host_task: bool,
        clear_retry: bool,
    ) -> None:
        self._start_generation += 1
        if self._planning_task is not None:
            self._planning_task.cancel()
            await asyncio.gather(self._planning_task, return_exceptions=True)
            self._planning_task = None
        await self._cancel_consultation_tasks()
        self._planning_candidates = []
        self._planning_choice_signature = ()
        self._planning_started_at = 0.0

        if self._actuation is not None:
            task_id = str(self._actuation.get("task_id") or "")
            if cancel_host_task and task_id and str(self._actuation.get("state") or "") == "running_host":
                try:
                    await self._host_adapter.cancel_task(task_id)
                except Exception as exc:
                    self._logger.warning("galgame host task cancellation failed: {}", exc)
            self._actuation = None
        self._starting_actuation = False

        if clear_retry:
            self._pending_strategy = None
            self._advance_retry_budget.clear()
            self._ocr_hold_release_budget.clear()

    async def _interrupt_current(self) -> None:
        await self._reset_runtime_state(cancel_host_task=True, clear_retry=True)
        self._next_actuation_at = time.monotonic() + 0.2
