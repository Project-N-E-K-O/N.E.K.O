from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403
from .agent_prompt import _bounded_choice_instruction_text, _context_line_count
from .agent_message_router import AgentMessageRouter
from .agent_scene_tracker import AgentSceneTracker


class AgentActuationMixin:
    def _should_actuate(self, shared: dict[str, Any]) -> bool:
        if self._session_transition_actuation_blocked:
            return False
        return mode_allows_agent_actuation(str(shared.get("mode") or ""))

    async def _start_choice_fallback_actuation(
        self,
        shared: dict[str, Any],
        *,
        current_choices: list[dict[str, Any]],
        now: float,
        diagnostic: str,
    ) -> None:
        candidates = await self._build_choice_candidates(
            current_choices,
            {"degraded": True, "choices": [], "diagnostic": diagnostic},
        )
        self._planning_candidates = json_copy(candidates)
        self._trace_runtime(
            "choice planning fallback: "
            f"diagnostic={diagnostic or 'none'} candidates={len(candidates)}"
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

    async def _start_actuation_from_strategy(
        self,
        shared: dict[str, Any],
        *,
        strategy: dict[str, Any],
        now: float,
    ) -> None:
        if self._actuation is not None or self._starting_actuation:
            self._trace_runtime(
                "actuation start skipped: another start is already active "
                f"kind={str(strategy.get('kind') or '')} "
                f"strategy_id={str(strategy.get('strategy_id') or '')}"
            )
            return
        self._start_generation += 1
        start_generation = self._start_generation
        self._starting_actuation = True
        try:
            try:
                virtual_mouse_candidate_index = int(
                    strategy.get("virtual_mouse_candidate_index")
                    if strategy.get("virtual_mouse_candidate_index") is not None
                    else -1
                )
            except (TypeError, ValueError):
                virtual_mouse_candidate_index = -1
            await self._start_actuation(
                shared,
                start_generation=start_generation,
                kind=str(strategy.get("kind") or ""),
                instruction=str(strategy.get("instruction") or ""),
                suggestion_reason=str(strategy.get("suggestion_reason") or ""),
                now=now,
                choice_id=str(strategy.get("choice_id") or ""),
                strategy_family=str(strategy.get("strategy_family") or ""),
                strategy_id=str(strategy.get("strategy_id") or ""),
                instruction_variant=int(strategy.get("instruction_variant") or 0),
                candidate_choices=list(strategy.get("candidate_choices") or []),
                candidate_index=int(strategy.get("candidate_index") or 0),
                retry_reason=str(strategy.get("retry_reason") or ""),
                virtual_mouse_target_id=str(strategy.get("virtual_mouse_target_id") or ""),
                virtual_mouse_candidate_index=virtual_mouse_candidate_index,
            )
        finally:
            if start_generation == self._start_generation:
                self._starting_actuation = False

    def _actuation_start_is_current(self, start_generation: int) -> bool:
        if start_generation == self._start_generation:
            return True
        self._trace_runtime("actuation start discarded: stale generation")
        return False

    def _notify_ocr_after_advance_capture(
        self,
        shared: dict[str, Any],
        *,
        kind: str,
        strategy_id: str,
    ) -> None:
        if self._current_input_source(shared) != DATA_SOURCE_OCR_READER:
            return
        if kind not in {"advance", "probe"}:
            return
        should_request = getattr(self._plugin, "should_request_ocr_after_advance_capture", None)
        if callable(should_request):
            try:
                if not bool(should_request()):
                    return
            except Exception as exc:
                self._trace_runtime(f"check OCR after-advance capture eligibility failed: {exc}")
        requester = getattr(self._plugin, "request_ocr_after_advance_capture", None)
        if not callable(requester):
            return
        try:
            requester(reason=f"{kind}:{strategy_id or 'none'}")
        except Exception as exc:
            self._trace_runtime(f"notify OCR after-advance capture failed: {exc}")

    async def _start_actuation(
        self,
        shared: dict[str, Any],
        *,
        start_generation: int,
        kind: str,
        instruction: str,
        suggestion_reason: str,
        now: float,
        choice_id: str = "",
        strategy_family: str = "",
        strategy_id: str = "",
        instruction_variant: int = 0,
        candidate_choices: list[dict[str, Any]] | None = None,
        candidate_index: int = 0,
        retry_reason: str = "",
        virtual_mouse_target_id: str = "",
        virtual_mouse_candidate_index: int = -1,
    ) -> None:
        if self._should_block_dialogue_advance_for_visible_choices(shared, kind=kind):
            self._trace_runtime("actuation blocked: visible choices are present during advance")
            self._next_actuation_at = now + 0.2
            return

        local_fallback_reason = ""
        if self._should_prefer_local_input_for_ocr(
            shared,
            kind=kind,
            strategy_family=strategy_family,
            strategy_id=strategy_id,
        ):
            snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
            task_id = self._next_local_task_id()
            actuation = self._build_actuation_state(
                shared,
                snapshot=snapshot,
                kind=kind,
                task_id=task_id,
                state="local_fallback",
                now=now,
                choice_id=choice_id,
                strategy_family=strategy_family,
                strategy_id=strategy_id,
                instruction_variant=instruction_variant,
                candidate_choices=candidate_choices,
                candidate_index=candidate_index,
                retry_reason=retry_reason,
                virtual_mouse_target_id=virtual_mouse_target_id,
                virtual_mouse_candidate_index=virtual_mouse_candidate_index,
            )
            if choice_id and suggestion_reason:
                self._remember_suggestion_reason(choice_id, suggestion_reason)
            fallback = await self._run_local_input_fallback(shared, actuation=actuation)
            if not self._actuation_start_is_current(start_generation):
                return
            if bool(fallback.get("success")):
                self._clear_hard_error()
                self._screen_recovery_diagnostic = ""
                self._trace_runtime(
                    "actuation local input preferred for OCR: "
                    f"kind={kind} strategy_id={strategy_id or 'none'} task_id={task_id}"
                )
                actuation["local_fallback_result"] = json_copy(fallback)
                actuation["state"] = "awaiting_bridge"
                actuation["bridge_wait_started_at"] = now
                actuation["bridge_wait_timeout"] = self._bridge_wait_timeout(
                    shared, actuation=actuation
                )
                self._actuation = actuation
                self._notify_ocr_after_advance_capture(
                    shared,
                    kind=kind,
                    strategy_id=strategy_id,
                )
                return
            local_fallback_reason = str(fallback.get("reason") or fallback)
            self._trace_runtime(
                "actuation preferred local input failed, falling back to computer_use: "
                f"kind={kind} strategy_id={strategy_id or 'none'} "
                f"reason={fallback.get('reason') or fallback}"
            )

        if self._should_bypass_computer_use_for_quota(now=now, kind=kind):
            snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
            task_id = self._next_local_task_id()
            actuation = self._build_actuation_state(
                shared,
                snapshot=snapshot,
                kind=kind,
                task_id=task_id,
                state="local_fallback",
                now=now,
                choice_id=choice_id,
                strategy_family=strategy_family,
                strategy_id=strategy_id,
                instruction_variant=instruction_variant,
                candidate_choices=candidate_choices,
                candidate_index=candidate_index,
                retry_reason=retry_reason,
                virtual_mouse_target_id=virtual_mouse_target_id,
                virtual_mouse_candidate_index=virtual_mouse_candidate_index,
            )
            if choice_id and suggestion_reason:
                self._remember_suggestion_reason(choice_id, suggestion_reason)
            fallback = await self._run_local_input_fallback(shared, actuation=actuation)
            if not self._actuation_start_is_current(start_generation):
                return
            if bool(fallback.get("success")):
                self._clear_hard_error()
                self._screen_recovery_diagnostic = ""
                self._trace_runtime(
                    "actuation local fallback started under quota bypass: "
                    f"kind={kind} strategy_id={strategy_id or 'none'} task_id={task_id}"
                )
                actuation["local_fallback_result"] = json_copy(fallback)
                actuation["state"] = "awaiting_bridge"
                actuation["bridge_wait_started_at"] = now
                actuation["bridge_wait_timeout"] = self._bridge_wait_timeout(
                    shared, actuation=actuation
                )
                self._actuation = actuation
                self._notify_ocr_after_advance_capture(
                    shared,
                    kind=kind,
                    strategy_id=strategy_id,
                )
                return
            local_fallback_reason = str(fallback.get("reason") or fallback)
            self._trace_runtime(
                "actuation quota bypass local fallback failed: "
                f"kind={kind} strategy_id={strategy_id or 'none'} "
                f"reason={fallback.get('reason') or fallback}"
            )

        try:
            availability = await self._host_adapter.get_computer_use_availability()
        except HostAgentError as exc:
            if not self._actuation_start_is_current(start_generation):
                return
            self._trace_runtime(f"actuation blocked by availability error: {exc}")
            if self._pause_screen_recovery_after_input_unavailable(
                shared,
                kind=kind,
                strategy_family=strategy_family,
                strategy_id=strategy_id,
                reason=str(exc),
                now=now,
                local_fallback_reason=local_fallback_reason,
            ):
                return
            self._set_hard_error(str(exc), retryable=True)
            self._next_actuation_at = now + 1.0
            return
        if not self._actuation_start_is_current(start_generation):
            return
        if not bool(availability.get("ready")):
            reasons = availability.get("reasons")
            detail = reasons[0] if isinstance(reasons, list) and reasons else "computer_use unavailable"
            self._trace_runtime(f"actuation blocked: computer_use not ready ({detail})")
            if self._pause_screen_recovery_after_input_unavailable(
                shared,
                kind=kind,
                strategy_family=strategy_family,
                strategy_id=strategy_id,
                reason=str(detail),
                now=now,
                local_fallback_reason=local_fallback_reason,
            ):
                return
            self._set_hard_error(str(detail), retryable=True)
            self._next_actuation_at = now + 1.0
            return

        try:
            started = await self._host_adapter.run_computer_use_instruction(instruction)
        except HostAgentError as exc:
            if not self._actuation_start_is_current(start_generation):
                return
            self._trace_runtime(f"actuation start failed: {exc}")
            if self._pause_screen_recovery_after_input_unavailable(
                shared,
                kind=kind,
                strategy_family=strategy_family,
                strategy_id=strategy_id,
                reason=str(exc),
                now=now,
                local_fallback_reason=local_fallback_reason,
            ):
                return
            self._set_hard_error(str(exc), retryable=True)
            self._next_actuation_at = now + 1.0
            return

        if not self._actuation_start_is_current(start_generation):
            return
        task_id = str(started.get("task_id") or "")
        if not task_id:
            self._trace_runtime(f"actuation start failed: invalid task response {started}")
            self._set_hard_error(f"invalid task response: {started}", retryable=False)
            self._next_actuation_at = now + 1.0
            return

        if choice_id and suggestion_reason:
            self._remember_suggestion_reason(choice_id, suggestion_reason)

        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        self._clear_hard_error()
        self._screen_recovery_diagnostic = ""
        self._trace_runtime(
            "actuation started: "
            f"kind={kind} strategy_id={strategy_id or 'none'} task_id={task_id}"
        )
        self._actuation = self._build_actuation_state(
            shared,
            snapshot=snapshot,
            kind=kind,
            task_id=task_id,
            state="running_host",
            now=now,
            choice_id=choice_id,
            strategy_family=strategy_family,
            strategy_id=strategy_id,
            instruction_variant=instruction_variant,
            candidate_choices=candidate_choices,
            candidate_index=candidate_index,
            retry_reason=retry_reason,
            virtual_mouse_target_id=virtual_mouse_target_id,
            virtual_mouse_candidate_index=virtual_mouse_candidate_index,
        )
        self._notify_ocr_after_advance_capture(
            shared,
            kind=kind,
            strategy_id=strategy_id,
        )

    def _build_actuation_state(
        self,
        shared: dict[str, Any],
        *,
        snapshot: dict[str, Any],
        kind: str,
        task_id: str,
        state: str,
        now: float,
        choice_id: str = "",
        strategy_family: str = "",
        strategy_id: str = "",
        instruction_variant: int = 0,
        candidate_choices: list[dict[str, Any]] | None = None,
        candidate_index: int = 0,
        retry_reason: str = "",
        virtual_mouse_target_id: str = "",
        virtual_mouse_candidate_index: int = -1,
    ) -> dict[str, Any]:
        input_source = self._current_input_source(shared)
        return {
            "kind": kind,
            "task_id": task_id,
            "state": state,
            "strategy_family": strategy_family,
            "strategy_id": strategy_id,
            "instruction_variant": instruction_variant,
            "input_source": input_source,
            "started_at": now,
            "bridge_wait_started_at": 0.0,
            "bridge_wait_timeout": (
                self._OCR_BRIDGE_WAIT_TIMEOUT
                if input_source == DATA_SOURCE_OCR_READER
                else self._DEFAULT_BRIDGE_WAIT_TIMEOUT
            ),
            "baseline_last_seq": int(shared.get("last_seq") or 0),
            "baseline_signature": build_snapshot_signature(snapshot),
            "baseline_snapshot_ts": str(snapshot.get("ts") or ""),
            "baseline_stage": str(self._scene_state.get("stage") or ""),
            "baseline_scene_id": str(snapshot.get("scene_id") or ""),
            "baseline_line_id": str(snapshot.get("line_id") or ""),
            "baseline_session_id": str(shared.get("active_session_id") or ""),
            "baseline_choice_signature": build_choice_signature(
                list(snapshot.get("choices", []))
            ),
            "choice_id": choice_id,
            "candidate_choices": json_copy(candidate_choices or []),
            "candidate_index": candidate_index,
            "retry_reason": retry_reason,
            "virtual_mouse_target_id": virtual_mouse_target_id,
            "virtual_mouse_candidate_index": virtual_mouse_candidate_index,
        }

    def _next_local_task_id(self) -> str:
        self._local_task_seq += 1
        return f"local-{self._local_task_seq}"

    def _should_bypass_computer_use_for_quota(self, *, now: float, kind: str) -> bool:
        if kind not in {"advance", "probe", "recover", "choose"}:
            return False
        return now < self._computer_use_quota_bypass_until

    @staticmethod
    def _actuation_input_source_is_ocr(actuation: dict[str, Any]) -> bool:
        return str(actuation.get("input_source") or "") == DATA_SOURCE_OCR_READER

    def _configured_advance_speed(self, shared: dict[str, Any]) -> str:
        speed = str(shared.get("advance_speed") or ADVANCE_SPEED_MEDIUM).strip().lower()
        if speed in {ADVANCE_SPEED_SLOW, ADVANCE_SPEED_MEDIUM, ADVANCE_SPEED_FAST}:
            return speed
        return ADVANCE_SPEED_MEDIUM

    def _effective_advance_speed(self, shared: dict[str, Any]) -> str:
        speed = self._configured_advance_speed(shared)
        if self._current_input_source(shared) != DATA_SOURCE_OCR_READER:
            return speed
        if speed == ADVANCE_SPEED_FAST:
            return speed
        recent_advance_inputs = [
            item
            for item in self._recent_local_inputs
            if str(item.get("kind") or "") == "advance"
            and str(item.get("strategy_id") or "") == "advance_click"
        ][-4:]
        if len(recent_advance_inputs) < 3:
            return speed
        history_events = shared.get("history_events")
        if not isinstance(history_events, list):
            return ADVANCE_SPEED_SLOW if speed == ADVANCE_SPEED_MEDIUM else speed
        recent_observations = [
            event
            for event in history_events[-12:]
            if isinstance(event, dict)
            and str(event.get("type") or "") in {
                "line_observed",
                "line_changed",
                "choices_shown",
                "screen_classified",
            }
        ]
        if recent_observations:
            return speed
        return ADVANCE_SPEED_SLOW if speed == ADVANCE_SPEED_MEDIUM else speed

    def _post_progress_delay(self, shared: dict[str, Any], *, actuation: dict[str, Any]) -> float:
        if not self._actuation_input_source_is_ocr(actuation):
            return 0.2
        if str(actuation.get("kind") or "") != "advance":
            return 0.2
        if str(actuation.get("strategy_id") or "") != "advance_click":
            return 0.2
        if str(actuation.get("strategy_family") or "") != "dialogue":
            return 0.2
        return self._ocr_advance_observation_window(shared)

    async def _progress_actuation(self, shared: dict[str, Any], now: float) -> None:
        actuation = self._actuation
        if actuation is None:
            return

        if str(actuation.get("state") or "") == "running_host":
            task_id = str(actuation.get("task_id") or "")
            if not task_id:
                reason = "invalid actuation state: empty task_id"
                self._trace_runtime("actuation host poll aborted: empty task_id")
                self._record_failure(
                    kind=str(actuation.get("kind") or ""),
                    strategy_id=str(actuation.get("strategy_id") or ""),
                    reason=reason,
                    scene_id=str((shared.get("latest_snapshot") or {}).get("scene_id") or ""),
                )
                self._actuation = None
                self._pending_strategy = None
                self._set_hard_error(reason, retryable=False)
                self._next_actuation_at = now + 1.0
                return
            try:
                task = await self._host_adapter.get_task(task_id)
            except HostAgentError as exc:
                self._handle_recoverable_host_poll_failure(
                    shared,
                    actuation=actuation,
                    reason=str(exc),
                    now=now,
                )
                return

            status = str(task.get("status") or "")
            if status in {"queued", "running"}:
                return
            if status == "completed":
                self._trace_runtime(
                    "actuation host completed, awaiting bridge update: "
                    f"task_id={str(actuation.get('task_id') or '')}"
                )
                actuation["state"] = "awaiting_bridge"
                actuation["bridge_wait_started_at"] = now
                actuation["bridge_wait_timeout"] = self._bridge_wait_timeout(
                    shared, actuation=actuation
                )
                return

            reason = str(task.get("error") or f"actuation task ended with status={status}")
            if self._should_try_local_input_fallback(task, actuation=actuation, reason=reason):
                self._computer_use_quota_bypass_until = now + 300.0
                fallback = await self._run_local_input_fallback(shared, actuation=actuation)
                if bool(fallback.get("success")):
                    self._trace_runtime(
                        "actuation local fallback completed, awaiting bridge update: "
                        f"task_id={str(actuation.get('task_id') or '')} "
                        f"kind={str(actuation.get('kind') or '')} "
                        f"strategy_id={str(actuation.get('strategy_id') or '')}"
                    )
                    actuation["local_fallback_result"] = json_copy(fallback)
                    actuation["state"] = "awaiting_bridge"
                    actuation["bridge_wait_started_at"] = now
                    actuation["bridge_wait_timeout"] = self._bridge_wait_timeout(
                        shared, actuation=actuation
                    )
                    return
                reason = f"{reason}; local fallback failed: {fallback.get('reason') or fallback}"
            self._trace_runtime(
                "actuation host ended unsuccessfully: "
                f"task_id={str(actuation.get('task_id') or '')} "
                f"status={status} reason={reason}"
            )
            retry = self._build_retry_strategy(shared, actuation=actuation, failure_reason=reason)
            self._record_failure(
                kind=str(actuation.get("kind") or ""),
                strategy_id=str(actuation.get("strategy_id") or ""),
                reason=reason,
                scene_id=str((shared.get("latest_snapshot") or {}).get("scene_id") or ""),
            )
            self._actuation = None
            if retry is not None:
                self._clear_hard_error()
                self._pending_strategy = retry
                self._next_actuation_at = now + 0.2
                return
            self._set_hard_error(reason, retryable=False)
            self._next_actuation_at = now + 1.0
            return

        progress_reason = self._detect_bridge_progress(shared, actuation=actuation)
        if progress_reason is not None:
            self._trace_runtime(
                "actuation observed bridge progress: "
                f"task_id={str(actuation.get('task_id') or '')} via={progress_reason}"
            )
            self._record_virtual_mouse_outcome(actuation, success=True, now=now)
            self._clear_hard_error()
            self._screen_recovery_diagnostic = ""
            self._clear_ocr_capture_diagnostic()
            self._actuation = None
            self._pending_strategy = None
            self._next_actuation_at = now + self._post_progress_delay(shared, actuation=actuation)
            return

        wait_timeout = self._bridge_wait_timeout(shared, actuation=actuation)
        actuation["bridge_wait_timeout"] = wait_timeout
        if now - float(actuation.get("bridge_wait_started_at") or now) > wait_timeout:
            reason = "bridge state did not change after actuation"
            self._trace_runtime(
                "actuation timed out waiting for bridge update: "
                f"task_id={str(actuation.get('task_id') or '')} "
                f"timeout={wait_timeout:.1f}s input_source={self._current_input_source(shared)}"
            )
            self._record_virtual_mouse_outcome(actuation, success=False, now=now)
            ocr_diagnostic = self._record_ocr_no_observed_timeout(
                actuation=actuation,
                shared=shared,
            )
            if ocr_diagnostic and str(ocr_diagnostic).startswith("ocr_context_unavailable"):
                retry = self._build_retry_strategy(
                    shared, actuation=actuation, failure_reason=reason
                )
            elif ocr_diagnostic:
                retry = None
            else:
                retry = self._build_retry_strategy(
                    shared, actuation=actuation, failure_reason=reason
                )
            self._record_failure(
                kind=str(actuation.get("kind") or ""),
                strategy_id=str(actuation.get("strategy_id") or ""),
                reason=ocr_diagnostic or reason,
                scene_id=str((shared.get("latest_snapshot") or {}).get("scene_id") or ""),
            )
            self._actuation = None
            if retry is not None:
                self._clear_hard_error()
                self._pending_strategy = retry
                self._next_actuation_at = now + 0.2
                return
            if ocr_diagnostic:
                self._clear_hard_error()
                self._next_actuation_at = now + 1.0
                return
            self._set_hard_error(reason, retryable=False)
            self._next_actuation_at = now + 1.0

    @staticmethod
    def _task_failure_text(task: dict[str, Any], *, reason: str) -> str:
        parts = [str(reason or ""), str(task.get("status") or ""), str(task.get("error") or "")]
        result = task.get("result")
        if result:
            try:
                parts.append(json.dumps(result, ensure_ascii=False, sort_keys=True))
            except TypeError:
                parts.append(str(result))
        return "\n".join(parts)

    def _should_try_local_input_fallback(
        self,
        task: dict[str, Any],
        *,
        actuation: dict[str, Any],
        reason: str,
    ) -> bool:
        kind = str(actuation.get("kind") or "")
        if kind not in {"advance", "probe", "recover", "choose"}:
            return False
        text = self._task_failure_text(task, reason=reason).lower()
        return "agent_quota_exceeded" in text or "quota" in text

    async def _run_local_input_fallback(
        self,
        shared: dict[str, Any],
        *,
        actuation: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(
                self._local_input_actuator,
                json_copy(shared),
                json_copy(actuation),
            )
            self._remember_local_input_result(result, actuation=actuation)
            return result
        except Exception as exc:
            self._logger.warning("galgame local input fallback failed: {}", exc)
            result = {"success": False, "reason": str(exc)}
            self._remember_local_input_result(result, actuation=actuation)
            return result

    def _remember_local_input_result(
        self,
        result: dict[str, Any],
        *,
        actuation: dict[str, Any],
        limit: int = 10,
    ) -> None:
        record = {
            "ts": str(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            "task_id": str(actuation.get("task_id") or ""),
            "kind": str(actuation.get("kind") or ""),
            "strategy_id": str(actuation.get("strategy_id") or ""),
            "instruction_variant": int(actuation.get("instruction_variant") or 0),
            "virtual_mouse_target_id": str(actuation.get("virtual_mouse_target_id") or ""),
            "virtual_mouse_candidate_index": int(
                actuation.get("virtual_mouse_candidate_index")
                if actuation.get("virtual_mouse_candidate_index") not in (None, "")
                else -1
            ),
            "success": bool(result.get("success")),
            "reason": str(result.get("reason") or ""),
            "method": str(result.get("method") or ""),
            "pid": int(result.get("pid") or 0),
            "hwnd": int(result.get("hwnd") or 0),
        }
        if isinstance(result.get("virtual_mouse"), dict):
            record["virtual_mouse"] = json_copy(result["virtual_mouse"])
        if isinstance(result.get("safety_policy"), dict):
            record["safety_policy"] = json_copy(result["safety_policy"])
        self._append_bounded(self._recent_local_inputs, record, limit=limit)

    def _clear_actuation_error_if_read_only(self, shared: dict[str, Any]) -> None:
        if self._hard_error and not self._should_actuate(shared):
            self._clear_hard_error()
