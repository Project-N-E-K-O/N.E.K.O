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
        configured_live_line_limit = max(
            0,
            int(getattr(config, "history_events_limit", 0) or 0),
        ) + max(
            0,
            int(getattr(config, "history_lines_limit", 0) or 0),
        )
        self._scene_tracker = AgentSceneTracker(
            seen_line_limit=max(
                self._SUMMARY_SEEN_LINE_KEYS_LIMIT,
                configured_live_line_limit,
            ),
        )
        self._message_router = AgentMessageRouter(now_factory=self._utc_now_iso)
        self._last_interruption = {}
        self._pending_choice_advice: dict[str, Any] | None = None
        self._summary_tasks: set[asyncio.Task[bool]] = set()
        self._summary_task_meta: dict[asyncio.Task[bool], dict[str, Any]] = {}
        self._scene_capsule_tasks: set[asyncio.Task[bool]] = set()
        self._scene_capsule_task_meta: dict[
            asyncio.Task[bool],
            dict[str, Any],
        ] = {}
        self._consultation_tasks: set[asyncio.Task[bool]] = set()
        self._pending_consults: set[str] = set()
        self._summary_generation = 0
        self._summary_debug: dict[str, Any] = {}
        self._scene_summary_repeat_guard_enabled = bool(
            getattr(config, "scene_summary_repeat_guard_enabled", True)
        )
        self._scene_summary_repeat_reservations: set[str] = set()
        self._scene_summary_repeat_deliveries: dict[str, dict[str, Any]] = {}
        self._scene_summary_latest_scene_content: dict[str, dict[str, Any]] = {}
        self._scene_summary_repeat_data_source = ""
        # Freshness ordering is a correctness boundary and remains active even
        # when the optional content repeat guard is disabled.
        self._scene_summary_schedule_order_counter = 0
        self._scene_summary_latest_observed_order = 0
        self._scene_summary_latest_submitted_order = 0
        self._scene_capsule_generation = 0
        self._scene_capsule_observation_epoch = 0
        self._scene_capsule_input_marker = ""
        self._scene_capsule_event_versions: dict[str, int] = {}
        self._scene_capsule_retired_event_versions: dict[str, int] = {}
        self._scene_capsule_marker_event_state: dict[str, dict[str, Any]] = {}
        self._scene_capsule_line_fallback_aliases: dict[
            str,
            dict[int, str],
        ] = {}
        self._scene_summary_latest_memory_order_by_scene: dict[str, int] = {}
        self._scene_capsule_reservations: set[str] = set()
        # Per logical scene, this stores only committed event/cursor state and
        # the short stable tail needed to reconcile a trusted source handoff.
        self._scene_capsule_delivery_ledger: dict[str, dict[str, Any]] = {}
        self._scene_capsule_source_aliases: dict[str, str] = {}
        self._scene_capsule_fallback_occurrences: dict[str, dict[str, Any]] = {}
        self._scene_timeline_boundaries: dict[str, dict[str, Any]] = {}
        self._scene_summary_suppressed_count = 0
        self._scene_summary_last_success_at = 0.0
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

    def _is_trusted_scene_source_handoff(
        self,
        fields: dict[str, Any],
    ) -> bool:
        fields = dict(fields or {})
        previous_source = str(fields.get("previous_data_source") or "")
        current_source = str(fields.get("current_data_source") or "")
        source_handoff = (
            DATA_SOURCE_OCR_READER in {previous_source, current_source}
            and bool(
                {previous_source, current_source}
                & {DATA_SOURCE_MEMORY_READER, DATA_SOURCE_BRIDGE_SDK}
            )
        )
        same_game = bool(fields.get("previous_game_id")) and (
            str(fields.get("previous_game_id") or "")
            == str(fields.get("current_game_id") or "")
        )

        def _is_native_reader_game_id(source: str, game_id: Any) -> bool:
            normalized_game_id = str(game_id or "").strip().lower()
            if source == DATA_SOURCE_MEMORY_READER:
                return re.fullmatch(r"mem-[0-9a-f]{16}", normalized_game_id) is not None
            if source == DATA_SOURCE_OCR_READER:
                return re.fullmatch(r"ocr-[0-9a-f]{12}", normalized_game_id) is not None
            return False

        native_reader_game_ids = (
            {previous_source, current_source}
            == {DATA_SOURCE_MEMORY_READER, DATA_SOURCE_OCR_READER}
            and _is_native_reader_game_id(
                previous_source,
                fields.get("previous_game_id"),
            )
            and _is_native_reader_game_id(
                current_source,
                fields.get("current_game_id"),
            )
        )

        def _conflicts(previous_key: str, current_key: str) -> bool:
            previous = fields.get(previous_key)
            current = fields.get(current_key)
            return bool(previous and current and previous != current)

        def _identity_conflicts(previous_key: str, current_key: str) -> bool:
            previous = self._normalized_identity_text(fields.get(previous_key))
            current = self._normalized_identity_text(fields.get(current_key))
            return bool(previous and current and previous != current)

        def _identity_matches(previous_key: str, current_key: str) -> bool:
            previous = self._normalized_identity_text(fields.get(previous_key))
            current = self._normalized_identity_text(fields.get(current_key))
            return bool(previous and current and previous == current)

        stable_runtime_identity = any(
            (
                _identity_matches("previous_process_name", "current_process_name"),
                bool(fields.get("previous_pid"))
                and fields.get("previous_pid") == fields.get("current_pid"),
                _identity_matches("previous_window_title", "current_window_title"),
                bool(fields.get("previous_target_hwnd"))
                and fields.get("previous_target_hwnd")
                == fields.get("current_target_hwnd"),
            )
        )

        return bool(
            source_handoff
            and (same_game or stable_runtime_identity)
            and (
                native_reader_game_ids
                or not _conflicts("previous_game_id", "current_game_id")
            )
            and not _identity_conflicts(
                "previous_process_name", "current_process_name"
            )
            and not _conflicts("previous_pid", "current_pid")
            and not _identity_conflicts(
                "previous_window_title", "current_window_title"
            )
            and not _conflicts("previous_target_hwnd", "current_target_hwnd")
        )

    def _reset_scene_summary_repeat_guard(self, *, force: bool = False) -> None:
        fields = dict(getattr(self, "_last_session_transition_fields", {}) or {})
        current_source = str(fields.get("current_data_source") or "")
        trusted_handoff = (
            not force and self._is_trusted_scene_source_handoff(fields)
        )
        self._scene_summary_repeat_reservations.clear()
        self._scene_capsule_reservations.clear()
        if trusted_handoff:
            # Observation currently classifies OCR -> trusted-reader as a real
            # session reset.  Preserve only successfully submitted capsule
            # cursors across that trusted source handoff; all pending work was
            # already cancelled by the caller.
            self._scene_summary_repeat_data_source = current_source
            return
        self._scene_summary_repeat_deliveries.clear()
        self._scene_summary_latest_scene_content.clear()
        self._scene_summary_repeat_data_source = ""
        self._scene_summary_schedule_order_counter = 0
        self._scene_summary_latest_observed_order = 0
        self._scene_summary_latest_submitted_order = 0
        # Task invalidation belongs to the matching cancel method.  Reset only
        # clears committed boundary state so a full cancel + reset advances
        # each generation exactly once.
        self._scene_capsule_input_marker = ""
        self._scene_capsule_event_versions.clear()
        self._scene_capsule_retired_event_versions.clear()
        self._scene_capsule_marker_event_state.clear()
        self._scene_capsule_line_fallback_aliases.clear()
        self._scene_summary_latest_memory_order_by_scene.clear()
        self._scene_capsule_delivery_ledger.clear()
        self._scene_capsule_source_aliases.clear()
        self._scene_capsule_fallback_occurrences.clear()
        self._scene_timeline_boundaries.clear()
        self._scene_summary_suppressed_count = 0
        self._scene_summary_last_success_at = 0.0

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

    def _cancel_scene_capsule_tasks(
        self,
        *,
        reason: str,
        retire: bool,
    ) -> None:
        tasks = list(self._scene_capsule_tasks)
        pending = [task for task in tasks if not task.done()]
        pending_set = set(pending)
        if pending:
            self._scene_capsule_generation += 1

        cancelled_event_keys: set[str] = set()
        cancelled_orders: set[int] = set()
        # A task may already be done while its done callback is still queued.
        # Its reservation and event version remain owned until that callback
        # runs, so retirement must inspect every tracked task, not only pending
        # tasks.  Cancellation itself still applies only to pending tasks.
        for task in tasks:
            meta = dict(self._scene_capsule_task_meta.get(task) or {})
            raw_versions = meta.get("event_versions")
            versions: dict[str, int] = {}
            if isinstance(raw_versions, dict):
                for raw_key, raw_version in raw_versions.items():
                    key = str(raw_key or "")
                    if not key:
                        continue
                    try:
                        versions[key] = int(raw_version or 0)
                    except (TypeError, ValueError):
                        versions[key] = 0

            try:
                fallback_version = int(
                    meta.get("observation_epoch")
                    or meta.get("order")
                    or self._scene_capsule_observation_epoch
                    or 0
                )
            except (TypeError, ValueError):
                fallback_version = int(self._scene_capsule_observation_epoch or 0)
            for raw_key in list(meta.get("event_keys") or []):
                key = str(raw_key or "")
                if key:
                    versions.setdefault(key, fallback_version)
            cancelled_event_keys.update(versions)
            if task in pending_set:
                try:
                    cancelled_order = int(meta.get("order") or 0)
                except (TypeError, ValueError):
                    cancelled_order = 0
                if cancelled_order > 0:
                    cancelled_orders.add(cancelled_order)

            if retire:
                for key, version in versions.items():
                    previous = int(
                        self._scene_capsule_retired_event_versions.get(key) or 0
                    )
                    if key in self._scene_capsule_retired_event_versions:
                        self._scene_capsule_retired_event_versions.pop(key, None)
                    self._scene_capsule_retired_event_versions[key] = max(
                        previous,
                        int(version or 0),
                    )
            if task in pending_set:
                task.cancel()

        if cancelled_orders:
            for outbound in self._outbound_messages:
                metadata = outbound.get("metadata")
                metadata_obj = metadata if isinstance(metadata, dict) else {}
                try:
                    outbound_order = int(metadata_obj.get("capsule_order") or 0)
                except (TypeError, ValueError):
                    outbound_order = 0
                if (
                    str(outbound.get("kind") or "") == "scene_delta"
                    and str(outbound.get("status") or "") == "queued"
                    and outbound_order in cancelled_orders
                ):
                    self._mark_message(
                        outbound,
                        status="superseded",
                        delivered=False,
                        metadata={
                            "cancelled_before_retry": True,
                            "cancellation_reason": str(reason or ""),
                        },
                    )
            self._recent_pushes = self._recent_push_records()

        # Remove only the tasks captured by this cancellation pass.  If a new
        # owner is registered re-entrantly, keep its task metadata intact so
        # both this method and the old task's done callback can see ownership.
        for task in tasks:
            self._scene_capsule_tasks.discard(task)
            self._scene_capsule_task_meta.pop(task, None)

        # Only release reservations that no still-active task owns.  This is
        # important when a cancelled task's done callback races with a newer
        # task that inherited the same logical event.
        active_owner_keys: set[str] = set()
        for task in self._scene_capsule_tasks:
            if task.done():
                continue
            active_meta = self._scene_capsule_task_meta.get(task) or {}
            active_owner_keys.update(
                str(item)
                for item in list(active_meta.get("event_keys") or [])
                if str(item)
            )
        for event_key in cancelled_event_keys:
            if event_key not in active_owner_keys:
                self._scene_capsule_reservations.discard(event_key)
        if not active_owner_keys:
            self._scene_capsule_reservations.clear()

        self._summary_debug["last_capsule_task_cancelled"] = {
            "reason": str(reason or "cancel_scene_capsule_tasks"),
            "pending_count": len(pending),
            "retired": bool(retire),
            "retired_event_count": len(cancelled_event_keys) if retire else 0,
            "capsule_generation": self._scene_capsule_generation,
            "ts": self._utc_now_iso(),
        }

    def _cancel_scene_memory_tasks(self, *, reason: str) -> None:
        tasks = list(self._summary_tasks)
        pending = [task for task in tasks if not task.done()]
        self._summary_generation += 1
        self._scene_summary_repeat_reservations.clear()
        for task in tasks:
            task_meta = self._summary_task_meta.get(task)
            if not isinstance(task_meta, dict):
                continue
            task_meta["restore_schedule_on_failure"] = False
            task_meta["permanent_cancellation_reason"] = str(reason or "")
        for task in pending:
            task.cancel()
        self._summary_tasks.clear()
        self._summary_task_meta.clear()
        self._summary_debug["last_memory_task_cancelled"] = {
            "reason": str(reason or "cancel_scene_memory_tasks"),
            "pending_count": len(pending),
            "memory_generation": self._summary_generation,
            "ts": self._utc_now_iso(),
        }

    def _cancel_summary_tasks(self) -> None:
        summary_pending_count = sum(
            1 for task in self._summary_tasks if not task.done()
        )
        capsule_pending_count = sum(
            1 for task in self._scene_capsule_tasks if not task.done()
        )
        pending_count = summary_pending_count + capsule_pending_count
        self._cancel_scene_capsule_tasks(
            reason="cancel_summary_tasks",
            retire=True,
        )
        self._cancel_scene_memory_tasks(reason="cancel_summary_tasks")
        if pending_count:
            self._summary_debug["last_task_cancelled"] = {
                "reason": "cancel_summary_tasks",
                "pending_count": pending_count,
                "summary_pending_count": summary_pending_count,
                "capsule_pending_count": capsule_pending_count,
                "ts": self._utc_now_iso(),
            }

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
        tasks = [*self._summary_tasks, *self._scene_capsule_tasks]
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
        self._reset_scene_summary_repeat_guard(force=True)
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
