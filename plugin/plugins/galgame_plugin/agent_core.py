from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403
from .agent_prompt import _bounded_choice_instruction_text, _context_line_count
from .agent_message_router import AgentMessageRouter
from .agent_scene_tracker import AgentSceneTracker
from .agent_lifecycle import AgentLifecycleMixin
from .agent_actuation import AgentActuationMixin
from .agent_ocr_actuation import AgentOcrActuationMixin
from .agent_choice_planning import AgentChoicePlanningMixin
from .agent_strategy import AgentStrategyMixin
from .agent_scene_state import AgentSceneStateMixin
from .agent_sync import AgentSyncMixin
from .agent_summary import AgentSummaryMixin
from .agent_scene_context import AgentSceneContextMixin
from .agent_context import AgentContextMixin
from .agent_prompt import AgentPromptMixin
from .agent_status import AgentStatusMixin
from .agent_thinking import AgentThinkingMixin
from .agent_consult import AgentConsultMixin
from .agent_observation import AgentObservationMixin
from .agent_diagnostics import AgentDiagnosticsMixin


class GameLLMAgent(
    AgentLifecycleMixin,
    AgentActuationMixin,
    AgentOcrActuationMixin,
    AgentChoicePlanningMixin,
    AgentStrategyMixin,
    AgentSceneStateMixin,
    AgentSyncMixin,
    AgentSummaryMixin,
    AgentSceneContextMixin,
    AgentContextMixin,
    AgentPromptMixin,
    AgentStatusMixin,
    AgentThinkingMixin,
    AgentConsultMixin,
    AgentObservationMixin,
    AgentDiagnosticsMixin,
):
    _BRIDGE_PROGRESS_EVENT_TYPES = frozenset(
        {
            "session_started",
            "line_observed",
            "line_changed",
            "choices_shown",
            "choice_selected",
            "scene_changed",
            "screen_classified",
            "save_loaded",
        }
    )

    _DEFAULT_BRIDGE_WAIT_TIMEOUT = 5.0

    _OCR_BRIDGE_WAIT_TIMEOUT = 12.0

    _OCR_ADVANCE_BRIDGE_WAIT_TIMEOUT = 3.0

    _OCR_ADVANCE_OBSERVATION_WINDOWS = {
        ADVANCE_SPEED_SLOW: 3.2,
        ADVANCE_SPEED_MEDIUM: 2.4,
        ADVANCE_SPEED_FAST: 0.8,
    }

    _OCR_ADVANCE_RETRY_TIMEOUTS = {
        ADVANCE_SPEED_SLOW: 5.0,
        ADVANCE_SPEED_MEDIUM: 3.5,
        ADVANCE_SPEED_FAST: 2.0,
    }

    _OCR_ADVANCE_RETRY_BUDGET = 1

    _OCR_BRIDGE_ACTIVITY_GRACE_SECONDS = 4.0

    _CHOICE_PLANNING_TIMEOUT_SECONDS = 8.0

    _FOCUS_RETRY_COOLDOWN_SECONDS = 3.0

    _FOCUS_RETRY_BASE_SECONDS = 0.5

    _FOCUS_RETRY_MAX_SECONDS = 5.0

    _FOCUS_FAILURE_PUSH_THRESHOLD = 3

    _SCENE_SUMMARY_PUSH_LINE_INTERVAL = 8

    _SCENE_PUSH_HALF_THRESHOLD: int = 4

    _SCENE_PUSH_TIME_FALLBACK_SECONDS: float = 120.0

    _SCENE_MERGE_TOTAL_THRESHOLD: int = 12

    _SCENE_CROSS_SCENE_TOTAL_THRESHOLD: int = 6

    _CHOICE_ADVICE_WAIT_TIMEOUT_SECONDS = 20.0

    _OBSERVE_SUMMARY_TIMEOUT_SECONDS = 2.0

    _SUMMARY_SEEN_LINE_KEYS_LIMIT = 512

    _KEY_POINT_LABELS = {
        "plot": "剧情推进",
        "emotion": "人物情绪",
        "decision": "玩家选择",
        "reveal": "新揭示",
        "objective": "当前目标",
    }

    _DIALOGUE_ADVANCE_VARIANTS = (
        {
            "id": "advance_enter",
            "instruction": (
                "Focus the visual novel window. If a dialogue line is visible and no menu choices "
                "are open, press Enter exactly once. Stop immediately after the single input."
            ),
        },
        {
            "id": "advance_click",
            "instruction": (
                "Focus the visual novel window. If a dialogue line or continue prompt is visible "
                "and no menu choices are open, click the usual continue area exactly once, then stop."
            ),
        },
        {
            "id": "advance_space",
            "instruction": (
                "Focus the visual novel window. If a dialogue line is waiting to advance and no "
                "menu choices are open, press Space exactly once. If Space is clearly not appropriate, "
                "click the continue area once instead, then stop."
            ),
        },
    )

    _OCR_DIALOGUE_ADVANCE_VARIANT_ORDER = (
        "advance_click",
        "advance_click",
        "advance_enter",
    )

    _VIRTUAL_MOUSE_RECENT_SUCCESS_SECONDS = 30.0

    _VIRTUAL_MOUSE_SKIP_AFTER_CONSECUTIVE_FAILURES = 2

    _UNKNOWN_NO_TEXT_ADVANCE_VARIANTS = (
        {
            "id": "probe_space",
            "instruction": (
                "Focus the visual novel window. If no branch choices are visible and the game appears "
                "to be waiting on a hidden dialogue, splash, title prompt, or other normal advance "
                "state, press Space exactly once. Do not open menus or select branch choices. Stop "
                "immediately after the single input."
            ),
        },
        {
            "id": "probe_enter",
            "instruction": (
                "Focus the visual novel window. If no branch choices are visible and the game appears "
                "to be waiting on a hidden dialogue, splash, title prompt, or other normal advance "
                "state, press Enter exactly once. Do not open menus or select branch choices. Stop "
                "immediately after the single input."
            ),
        },
    )

    _RECOVER_UI_VARIANTS = (
        {
            "id": "recover_focus",
            "instruction": (
                "Bring the visual novel window to the foreground. If a backlog, history, auto, skip, "
                "or system overlay is open above the game, dismiss that overlay exactly once and stop. "
                "Do not select branch choices."
            ),
        },
        {
            "id": "recover_overlay",
            "instruction": (
                "Focus the visual novel window. If the game appears blocked by a transient overlay or "
                "menu, close that overlay once using the most normal dismiss action, then stop without "
                "advancing dialogue or selecting choices."
            ),
        },
    )

    async def tick(self, shared: SharedStatePayload) -> None:
        self._ensure_loop_affinity()
        await self._observe(shared)
        now = time.monotonic()
        self._update_scene_state(shared, now)
        self._clear_actuation_error_if_read_only(shared)
        self._convert_screen_recovery_hard_error_if_applicable(shared, now=now)
        self._recover_retryable_error_if_ready(now)
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        visible_choices = list(snapshot.get("choices", []))
        status = self._compute_status(shared)

        if status == AGENT_STATUS_ACTIVE and not self._should_actuate(shared):
            if (
                self._actuation is not None
                or self._starting_actuation
                or self._planning_task is not None
                or self._pending_strategy is not None
            ):
                await self._reset_runtime_state(cancel_host_task=True, clear_retry=True)
            self._trace_runtime(
                "tick read-only: "
                f"mode={str(shared.get('mode') or '') or 'unknown'} "
                f"stage={self._scene_state['stage']} choices={len(visible_choices)}"
            )
            self._next_actuation_at = now + 1.0
            self._last_status = self._compute_status(shared)
            return

        if self._actuation is not None:
            await self._progress_actuation(shared, now)
            self._last_status = self._compute_status(shared)
            return

        if self._starting_actuation:
            self._trace_runtime(
                "tick skipped: actuation start already in progress "
                f"stage={self._scene_state['stage']} choices={len(visible_choices)}"
            )
            self._last_status = status
            return

        if self._planning_task is not None:
            await self._progress_planning(shared, now)
            self._last_status = self._compute_status(shared)
            return

        if status != AGENT_STATUS_ACTIVE:
            self._trace_runtime(
                "tick skipped: "
                f"status={status} stage={self._scene_state['stage']} "
                f"choices={len(visible_choices)} reason={self._current_status_reason(shared)}"
            )
            self._last_status = status
            return

        if self._should_pause_for_target_window_focus(shared):
            retry_delay = min(
                self._FOCUS_RETRY_BASE_SECONDS * (2 ** self._focus_failure_count),
                self._FOCUS_RETRY_MAX_SECONDS,
            )
            if now - self._last_focus_attempt_at >= retry_delay:
                self._last_focus_attempt_at = now
                focus_result = try_focus_target_window(shared)
                if focus_result.get("success"):
                    self._focus_failure_count = 0
                    self._next_actuation_at = now
                    self._trace_runtime(
                        "tick focus restored: target window brought to foreground "
                        f"stage={self._scene_state['stage']} choices={len(visible_choices)}"
                    )
                else:
                    self._focus_failure_count += 1
                    focus_diagnostic = str(focus_result.get("focus_diagnostic") or focus_result.get("reason") or "")
                    self._trace_runtime(
                        "tick focus attempt failed: "
                        f"stage={self._scene_state['stage']} choices={len(visible_choices)} "
                        f"detail={focus_diagnostic} consecutive={self._focus_failure_count}"
                    )
                    await self._maybe_push_focus_lost_notification(shared)
                    self._next_actuation_at = now + 1.0
                    self._last_status = status
                    return
            else:
                self._trace_runtime(
                    "tick paused: target window is not foreground "
                    f"stage={self._scene_state['stage']} choices={len(visible_choices)} "
                    f"retry_in={max(0.0, retry_delay - (now - self._last_focus_attempt_at)):.1f}s"
                )
                self._next_actuation_at = now + 1.0
                self._last_status = status
                return
        else:
            self._focus_failure_count = 0

        if self._should_pause_for_minigame_screen(shared):
            self._pending_strategy = None
            self._trace_runtime(
                "tick paused: minigame screen detected "
                f"stage={self._scene_state['stage']} choices={len(visible_choices)}"
            )
            self._next_actuation_at = now + 1.0
            self._last_status = status
            return

        if self._should_pause_for_screen_recovery(shared):
            self._pending_strategy = None
            self._trace_runtime(
                "tick paused: screen recovery input unavailable "
                f"stage={self._scene_state['stage']} choices={len(visible_choices)} "
                f"reason={self._screen_recovery_diagnostic}"
            )
            self._next_actuation_at = now + 1.0
            self._last_status = status
            return

        if now < self._next_actuation_at:
            self._trace_runtime(
                "tick delayed: "
                f"stage={self._scene_state['stage']} choices={len(visible_choices)} "
                f"retry_in={max(0.0, self._next_actuation_at - now):.2f}s"
            )
            self._last_status = status
            return

        strategy = self._take_pending_strategy()
        if strategy is not None:
            self._trace_runtime(
                "tick resuming pending strategy: "
                f"kind={str(strategy.get('kind') or '')} "
                f"strategy_id={str(strategy.get('strategy_id') or '')}"
            )
            await self._start_actuation_from_strategy(shared, strategy=strategy, now=now)
            self._last_status = self._compute_status(shared)
            return

        if self._scene_state["stage"] != "choice_menu":
            self._ocr_choice_fallback_attempts = 0

        if self._scene_state["stage"] == "choice_menu":
            if not visible_choices:
                if not self._has_confirmed_ocr_choice_menu(shared, snapshot):
                    self._trace_runtime(
                        "tick holding choice planning: waiting for confirmed OCR menu event "
                        "(no bridge choices)"
                    )
                    self._last_status = status
                    return
                strategy = self._build_choice_strategy(
                    shared,
                    candidate_choices=[],
                    candidate_index=0,
                    instruction_variant=self._ocr_choice_fallback_attempts,
                )
                if strategy is not None:
                    self._ocr_choice_fallback_attempts += 1
                    self._trace_runtime(
                        "tick starting OCR-only choice navigation: "
                        f"stage={self._scene_state['stage']} "
                        f"attempt={self._ocr_choice_fallback_attempts}"
                    )
                    await self._start_actuation_from_strategy(shared, strategy=strategy, now=now)
                self._last_status = self._compute_status(shared)
                return
            if not self._has_confirmed_ocr_choice_menu(shared, snapshot):
                self._trace_runtime(
                    "tick holding choice planning: waiting for confirmed OCR menu event"
                )
                self._last_status = status
                return
            choice_signature = build_choice_signature(visible_choices)
            if self._pending_choice_advice is not None:
                pending_signature = tuple(self._pending_choice_advice.get("choice_signature") or ())
                if pending_signature == choice_signature:
                    waited = now - float(self._pending_choice_advice.get("requested_at") or now)
                    if waited >= self._CHOICE_ADVICE_WAIT_TIMEOUT_SECONDS:
                        self._pending_choice_advice = None
                        self._planning_choice_signature = choice_signature
                        await self._run_choice_planning_inline(
                            shared,
                            context=build_suggest_context(
                                shared,
                                config=self._context_config,
                            ),
                            now=now,
                        )
                        self._last_status = self._compute_status(shared)
                        return
                    self._trace_runtime(
                        "tick waiting for cat choice advice: "
                        f"choices={len(visible_choices)} waited={waited:.1f}s"
                    )
                    self._next_actuation_at = now + 1.0
                    self._last_status = status
                    return
                self._pending_choice_advice = None

            await self._request_choice_advice(shared, visible_choices, snapshot=snapshot, now=now)
            self._last_status = self._compute_status(shared)
            return

        if self._should_hold_for_ocr_capture_diagnostic(shared):
            runtime = shared.get("ocr_reader_runtime") if isinstance(shared.get("ocr_reader_runtime"), dict) else {}
            self._ocr_capture_diagnostic = self._ocr_capture_diagnostic or (
                "ocr_context_unavailable: OCR 连续未读到有效对白，"
                "请检查截图区、目标窗口或当前画面是否为普通对白"
            )
            self._trace_runtime(
                "tick holding for OCR capture diagnostic: "
                f"detail={str(runtime.get('detail') or '')} "
                f"no_text_polls={int(runtime.get('consecutive_no_text_polls') or 0)}"
            )
            self._next_actuation_at = now + 1.0
            self._last_status = status
            return

        strategy = self._build_scene_strategy(shared, now=now)
        if strategy is not None:
            self._trace_runtime(
                "tick starting scene strategy: "
                f"kind={str(strategy.get('kind') or '')} "
                f"strategy_id={str(strategy.get('strategy_id') or '')} "
                f"stage={self._scene_state['stage']}"
            )
            await self._start_actuation_from_strategy(shared, strategy=strategy, now=now)
        else:
            self._trace_runtime(
                "tick idle: "
                f"stage={self._scene_state['stage']} choices={len(visible_choices)} "
                f"reason={self._current_status_reason(shared)}"
            )
        self._last_status = self._compute_status(shared)

    async def _interrupt_for_status_query(self) -> bool:
        if self._planning_task is None:
            return False
        self._trace_runtime("query_status interrupted in-flight choice planning")
        self._planning_task.cancel()
        await asyncio.gather(self._planning_task, return_exceptions=True)
        self._planning_task = None
        self._planning_candidates = []
        self._planning_choice_signature = ()
        self._planning_started_at = 0.0
        # Status queries should preempt LLM planning, but they should not tear down
        # an already running host actuation or a retry that is about to resume.
        self._next_actuation_at = time.monotonic() + 0.2
        return True

    async def set_standby(self, shared: SharedStatePayload, *, standby: bool) -> dict[str, Any]:
        self._ensure_loop_affinity()
        await self._observe(shared)
        message = self._enqueue_inbound_message(
            kind="set_standby",
            content="standby=true" if standby else "standby=false",
            priority=9,
            metadata={"standby": bool(standby)},
        )
        self._mark_message(message, status="processing")
        await self._interrupt_for_inbound_message(message)
        self._explicit_standby = bool(standby)
        status = self._compute_status(shared)
        self._last_status = status
        self._mark_message(message, status="completed", delivered=True)
        return {
            "action": "set_standby",
            "result": "agent entered standby" if standby else "agent resumed",
            "status": status,
            "message": json_copy(message),
        }

    async def _progress_planning(self, shared: dict[str, Any], now: float) -> None:
        task = self._planning_task
        if task is None:
            return
        if not task.done():
            if now - self._planning_started_at < self._CHOICE_PLANNING_TIMEOUT_SECONDS:
                return
            self._trace_runtime("choice planning timed out; using visible choice fallback")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._planning_task = None
            current_choices = list((shared.get("latest_snapshot") or {}).get("choices") or [])
            if build_choice_signature(current_choices) != self._planning_choice_signature:
                self._trace_runtime("choice planning timeout fallback dropped: visible choices changed")
                self._next_actuation_at = now + 0.2
                return
            await self._start_choice_fallback_actuation(
                shared,
                current_choices=current_choices,
                now=now,
                diagnostic="timeout: choice planning exceeded fallback window",
            )
            return

        self._planning_task = None
        try:
            suggestion = task.result()
        except asyncio.CancelledError:
            self._trace_runtime("choice planning cancelled before result")
            self._next_actuation_at = now + 0.2
            return
        except Exception as exc:
            self._logger.warning("galgame choice planning failed: {}", exc)
            suggestion = {"degraded": True, "choices": [], "diagnostic": str(exc)}

        current_choices = list((shared.get("latest_snapshot") or {}).get("choices") or [])
        if build_choice_signature(current_choices) != self._planning_choice_signature:
            self._trace_runtime("choice planning dropped: visible choices changed before result")
            self._next_actuation_at = now + 0.2
            return

        candidates = await self._build_choice_candidates(current_choices, suggestion)
        self._planning_candidates = json_copy(candidates)
        self._trace_runtime(
            "choice planning finished: "
            f"degraded={bool(suggestion.get('degraded'))} "
            f"diagnostic={str(suggestion.get('diagnostic') or '') or 'none'} "
            f"candidates={len(candidates)}"
        )
        if not candidates:
            self._next_actuation_at = now + 0.2
            return

        await self._start_actuation_from_strategy(
            shared,
            strategy=self._build_choice_strategy(
                shared,
                candidate_choices=candidates,
                candidate_index=0,
                instruction_variant=0,
            ),
            now=now,
        )
