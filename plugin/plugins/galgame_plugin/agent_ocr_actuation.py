from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403


class AgentOcrActuationMixin:
    @staticmethod
    def _latest_ocr_progress_seq(shared: dict[str, Any]) -> int:
        latest = 0
        history_events = shared.get("history_events")
        if isinstance(history_events, list):
            for event in history_events:
                if not isinstance(event, dict):
                    continue
                if str(event.get("type") or "") in {
                    "line_observed",
                    "line_changed",
                    "choices_shown",
                    "screen_classified",
                }:
                    latest = max(latest, int(event.get("seq") or 0))
        return latest

    def _clear_ocr_capture_diagnostic(self) -> None:
        self._ocr_no_observed_advance_count = 0
        self._ocr_capture_diagnostic = ""
        self._ocr_capture_diagnostic_set_at = 0.0

    def _set_ocr_capture_diagnostic(self, diagnostic: str, *, now: float | None = None) -> None:
        value = str(diagnostic or "")
        if not value:
            self._clear_ocr_capture_diagnostic()
            return
        if value != self._ocr_capture_diagnostic or self._ocr_capture_diagnostic_set_at <= 0:
            self._ocr_capture_diagnostic_set_at = float(now if now is not None else time.monotonic())
        self._ocr_capture_diagnostic = value

    def _ocr_unobserved_advance_hold_duration_seconds(self) -> float:
        cfg = getattr(self._plugin, "_cfg", None)
        try:
            value = float(getattr(cfg, "ocr_reader_unobserved_advance_hold_duration_seconds", 0.0))
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, value)

    def _record_ocr_no_observed_timeout(
        self,
        *,
        actuation: dict[str, Any],
        shared: dict[str, Any],
    ) -> str:
        if not self._actuation_input_source_is_ocr(actuation):
            return ""
        if str(actuation.get("kind") or "") not in {"advance", "probe"}:
            return ""
        local_result = actuation.get("local_fallback_result")
        if not isinstance(local_result, dict) or not bool(local_result.get("success")):
            return ""
        if bool((sanitize_snapshot_state(shared.get("latest_snapshot", {}))).get("choices")):
            return ""
        runtime = shared.get("ocr_reader_runtime")
        if isinstance(runtime, dict):
            detail = str(runtime.get("detail") or "")
            if detail in {"backend_unavailable", "self_ui_guard_blocked"}:
                return ""
        self._ocr_no_observed_advance_count += 1
        cfg = getattr(self._plugin, "_cfg", None)
        threshold = getattr(cfg, "ocr_reader_max_unobserved_advances_before_hold", 3)
        if self._ocr_no_observed_advance_count < threshold:
            return ""
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        context_state = (
            str((runtime or {}).get("ocr_context_state") or "")
            if isinstance(runtime, dict)
            else ""
        )
        if context_state in {"observed", "stable"} or snapshot.get("text") or snapshot.get("line_id"):
            self._set_ocr_capture_diagnostic(
                "input_advance_unconfirmed: 本地点击已发送，但 OCR 仍停在同一句台词；"
                "可能是游戏窗口没有接收输入、被其他窗口遮挡/抢焦点、点击点未命中对白区，"
                "或当前画面不是可推进对白。已暂停盲目推进，请切回/置顶游戏窗口后再继续。"
            )
        else:
            self._set_ocr_capture_diagnostic(
                "ocr_context_unavailable: 连续本地推进后没有 OCR observed，"
                "请检查截图区、目标窗口或当前画面是否为普通对白"
            )
        return self._ocr_capture_diagnostic

    def _hold_reason_from_diagnostic(self) -> str:
        diagnostic = str(self._ocr_capture_diagnostic or "")
        if diagnostic.startswith("input_advance_unconfirmed"):
            return "input_advance_unconfirmed"
        return "ocr_context_unavailable"

    def _should_hold_for_ocr_capture_diagnostic(self, shared: dict[str, Any]) -> bool:
        if self._current_input_source(shared) != DATA_SOURCE_OCR_READER:
            return False
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        if bool(snapshot.get("is_menu_open")) or list(snapshot.get("choices", [])):
            return False
        if self._ocr_capture_diagnostic:
            if self._should_release_input_advance_hold(shared):
                return False
            return True
        runtime = shared.get("ocr_reader_runtime")
        context_state = str((runtime or {}).get("ocr_context_state") or "") if isinstance(runtime, dict) else ""
        if context_state in {"poll_not_running", "capture_failed", "diagnostic_required", "stale_capture_backend"}:
            self._set_ocr_capture_diagnostic(
                f"ocr_context_unavailable: OCR context_state={context_state}，"
                "暂停普通推进并等待截图/OCR 恢复"
            )
            return True
        if snapshot.get("text") or snapshot.get("line_id"):
            return False
        runtime_requires_diagnostic = bool(
            isinstance(runtime, dict)
            and (
                runtime.get("ocr_capture_diagnostic_required")
                or str(runtime.get("detail") or "") == "ocr_capture_diagnostic_required"
            )
        )
        return bool(self._ocr_capture_diagnostic or runtime_requires_diagnostic)

    def _should_release_input_advance_hold(self, shared: dict[str, Any]) -> bool:
        if not str(self._ocr_capture_diagnostic or "").startswith("input_advance_unconfirmed"):
            return False
        hold_duration = self._ocr_unobserved_advance_hold_duration_seconds()
        if hold_duration <= 0:
            return False
        set_at = float(self._ocr_capture_diagnostic_set_at or 0.0)
        if set_at <= 0:
            return False
        age = time.monotonic() - set_at
        if age < hold_duration:
            return False
        if not self._consume_ocr_hold_release_budget(shared):
            self._trace_runtime(
                "input_advance_unconfirmed hold duration elapsed but hold release budget is exhausted"
            )
            return False
        self._trace_runtime(
            "input_advance_unconfirmed hold duration elapsed; releasing OCR hold for bounded retry"
        )
        self._clear_ocr_capture_diagnostic()
        return True

    def _ocr_hold_release_budget_key(self, shared: dict[str, Any]) -> str:
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        return "|".join(
            [
                str(shared.get("active_session_id") or ""),
                str(snapshot.get("scene_id") or ""),
                str(snapshot.get("line_id") or ""),
                repr(build_snapshot_signature(snapshot)),
            ]
        )

    def _consume_ocr_hold_release_budget(self, shared: dict[str, Any]) -> bool:
        key = self._ocr_hold_release_budget_key(shared)
        used = int(self._ocr_hold_release_budget.get(key) or 0)
        if used >= 1:
            return False
        self._ocr_hold_release_budget[key] = used + 1
        if len(self._ocr_hold_release_budget) > 32:
            for stale_key in list(self._ocr_hold_release_budget)[:-32]:
                self._ocr_hold_release_budget.pop(stale_key, None)
        return True

    def _should_pause_for_target_window_focus(self, shared: dict[str, Any]) -> bool:
        if self._current_input_source(shared) != DATA_SOURCE_OCR_READER:
            return False
        runtime = shared.get("ocr_reader_runtime")
        if not isinstance(runtime, dict):
            return False
        if "input_target_foreground" not in runtime and "target_is_foreground" not in runtime:
            return False
        if not str(runtime.get("process_name") or runtime.get("effective_process_name") or ""):
            return False
        if str(runtime.get("status") or "") not in {"starting", "active"}:
            return False
        return not bool(
            runtime.get("input_target_foreground", runtime.get("target_is_foreground"))
        )

    def _should_pause_for_minigame_screen(self, shared: dict[str, Any]) -> bool:
        if self._current_input_source(shared) != DATA_SOURCE_OCR_READER:
            return False
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        if str(snapshot.get("screen_type") or "") != OCR_CAPTURE_PROFILE_STAGE_MINIGAME:
            return False
        try:
            confidence = float(snapshot.get("screen_confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return confidence >= 0.45

    def _should_pause_for_screen_recovery(self, shared: dict[str, Any]) -> bool:
        if self._current_input_source(shared) != DATA_SOURCE_OCR_READER:
            return False
        if not self._screen_recovery_diagnostic:
            return False
        return self._current_screen_recovery_stage() != ""

    def _current_screen_recovery_stage(self) -> str:
        stage = str(self._scene_state.get("stage") or "")
        return stage if stage in _SCREEN_RECOVERY_STAGES else ""

    @staticmethod
    def _is_screen_escape_strategy(
        *,
        kind: str,
        strategy_family: str,
        strategy_id: str,
    ) -> bool:
        return (
            kind == "recover"
            and strategy_family in _SCREEN_RECOVERY_STAGES
            and strategy_id in _SCREEN_ESCAPE_STRATEGY_IDS
        )

    def _pause_screen_recovery_after_input_unavailable(
        self,
        shared: dict[str, Any],
        *,
        kind: str,
        strategy_family: str,
        strategy_id: str,
        reason: str,
        now: float,
        local_fallback_reason: str = "",
    ) -> bool:
        if self._current_input_source(shared) != DATA_SOURCE_OCR_READER:
            return False
        if not self._is_screen_escape_strategy(
            kind=kind,
            strategy_family=strategy_family,
            strategy_id=strategy_id,
        ):
            return False
        detail = str(reason or "computer_use unavailable").strip()
        if local_fallback_reason:
            detail = f"{detail}; local_input={local_fallback_reason}"
        self._screen_recovery_diagnostic = detail
        self._record_failure(
            kind=kind,
            strategy_id=strategy_id,
            reason=detail,
            scene_id=str((shared.get("latest_snapshot") or {}).get("scene_id") or ""),
        )
        self._clear_hard_error()
        self._actuation = None
        self._pending_strategy = None
        self._next_actuation_at = now + 1.0
        self._trace_runtime(
            "screen recovery paused: "
            f"stage={self._current_screen_recovery_stage() or strategy_family} "
            f"strategy_id={strategy_id} reason={detail}"
        )
        return True

    def _convert_screen_recovery_hard_error_if_applicable(
        self,
        shared: dict[str, Any],
        *,
        now: float,
    ) -> None:
        if not self._hard_error:
            return
        if self._current_input_source(shared) != DATA_SOURCE_OCR_READER:
            return
        if not self._current_screen_recovery_stage():
            return
        message = str(self._hard_error or "")
        lowered = message.lower()
        if "computer_use" not in lowered and "local input" not in lowered:
            return
        self._screen_recovery_diagnostic = message
        self._clear_hard_error()
        self._actuation = None
        self._pending_strategy = None
        self._next_actuation_at = now + 1.0
        self._trace_runtime(
            "screen recovery converted stale hard_error to pause: "
            f"stage={self._current_screen_recovery_stage()} reason={message}"
        )

    def _target_window_focus_diagnostic(self, shared: dict[str, Any]) -> str:
        if not self._should_pause_for_target_window_focus(shared):
            return ""
        runtime = shared.get("ocr_reader_runtime") if isinstance(shared.get("ocr_reader_runtime"), dict) else {}
        process_name = str(
            runtime.get("process_name")
            or runtime.get("effective_process_name")
            or "目标游戏"
        )
        title = str(runtime.get("window_title") or runtime.get("effective_window_title") or "")
        target = f"{process_name} / {title}" if title else process_name
        return (
            f"target_window_not_foreground: 已暂停 Agent 自动推进；当前目标窗口不是前台窗口（{target}）。"
            "为避免抢焦点或后台误输入，请切回/置顶游戏窗口后继续。"
        )

    def _ocr_advance_observation_window(self, shared: dict[str, Any]) -> float:
        return float(
            self._OCR_ADVANCE_OBSERVATION_WINDOWS.get(
                self._effective_advance_speed(shared),
                self._OCR_ADVANCE_OBSERVATION_WINDOWS[ADVANCE_SPEED_MEDIUM],
            )
        )

    def _ocr_advance_retry_timeout(self, shared: dict[str, Any]) -> float:
        return float(
            self._OCR_ADVANCE_RETRY_TIMEOUTS.get(
                self._effective_advance_speed(shared),
                self._OCR_ADVANCE_RETRY_TIMEOUTS[ADVANCE_SPEED_MEDIUM],
            )
        )

    def _should_prefer_local_input_for_ocr(
        self,
        shared: dict[str, Any],
        *,
        kind: str,
        strategy_family: str = "",
        strategy_id: str = "",
    ) -> bool:
        if self._current_input_source(shared) != DATA_SOURCE_OCR_READER:
            return False
        if kind == "recover":
            if strategy_family not in {
                "save_load",
                "config_screen",
                "gallery_screen",
                "game_over_screen",
            }:
                return False
            if strategy_id not in {
                "save_load_escape",
                "config_escape",
                "gallery_escape",
                "game_over_escape",
            }:
                return False
        elif kind not in {"advance", "probe", "choose"}:
            return False
        runtime = shared.get("ocr_reader_runtime")
        return isinstance(runtime, dict) and int(runtime.get("pid") or 0) > 0

    @staticmethod
    def _should_block_dialogue_advance_for_visible_choices(
        shared: dict[str, Any],
        *,
        kind: str,
    ) -> bool:
        if kind != "advance":
            return False
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        return bool(snapshot.get("is_menu_open")) or bool(list(snapshot.get("choices", [])))

    @staticmethod
    def _virtual_mouse_candidate_ids() -> tuple[str, ...]:
        return tuple(
            str(candidate.get("target_id") or "")
            for candidate in VIRTUAL_MOUSE_DIALOGUE_CANDIDATES
            if str(candidate.get("target_id") or "")
        )

    @staticmethod
    def _coerce_stat_time(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _virtual_mouse_runtime_key(self, shared: dict[str, Any]) -> str:
        runtime = shared.get("ocr_reader_runtime")
        if not isinstance(runtime, dict):
            runtime = shared.get("memory_reader_runtime")
        if not isinstance(runtime, dict):
            return ""
        pid = int(runtime.get("pid") or 0)
        process_name = str(
            runtime.get("effective_process_name") or runtime.get("process_name") or ""
        ).strip()
        window_title = str(
            runtime.get("effective_window_title") or runtime.get("window_title") or ""
        ).strip()
        if pid <= 0 and not process_name and not window_title:
            return ""
        return f"{pid}:{process_name}:{window_title}"

    def _virtual_mouse_stat(self, target_id: str) -> dict[str, Any]:
        stat = self._virtual_mouse_stats.get(target_id)
        if not isinstance(stat, dict):
            stat = {
                "success": 0,
                "failure": 0,
                "consecutive_failures": 0,
                "last_success_at": None,
                "last_failure_at": None,
            }
            self._virtual_mouse_stats[target_id] = stat
        return stat

    def _virtual_mouse_score(self, target_id: str, *, now: float) -> int:
        stat = self._virtual_mouse_stats.get(target_id) or {}
        success = int(stat.get("success") or 0)
        failure = int(stat.get("failure") or 0)
        consecutive_failures = int(stat.get("consecutive_failures") or 0)
        last_success_at = self._coerce_stat_time(stat.get("last_success_at"))
        last_failure_at = self._coerce_stat_time(stat.get("last_failure_at"))
        recent_success_bonus = 0
        if (
            last_success_at > 0
            and now - last_success_at <= self._VIRTUAL_MOUSE_RECENT_SUCCESS_SECONDS
            and last_success_at >= last_failure_at
        ):
            recent_success_bonus = 2
        return success * 3 - failure * 2 - consecutive_failures * 3 + recent_success_bonus

    def _select_virtual_mouse_dialogue_candidate(
        self,
        *,
        now: float,
        mutate: bool,
    ) -> dict[str, Any] | None:
        candidates = [
            (index, target_id)
            for index, target_id in enumerate(self._virtual_mouse_candidate_ids())
            if target_id
        ]
        if not candidates:
            return None

        excluded = {
            target_id
            for _, target_id in candidates
            if int(
                (self._virtual_mouse_stats.get(target_id) or {}).get("consecutive_failures")
                or 0
            )
            >= self._VIRTUAL_MOUSE_SKIP_AFTER_CONSECUTIVE_FAILURES
        }
        available = [(index, target_id) for index, target_id in candidates if target_id not in excluded]
        all_excluded_reset = False
        if not available:
            all_excluded_reset = True
            if mutate:
                for _, target_id in candidates:
                    if target_id in self._virtual_mouse_stats:
                        self._virtual_mouse_stats[target_id]["consecutive_failures"] = 0
                excluded = set()
            available = candidates

        scored = [
            {
                "target_id": target_id,
                "candidate_index": index,
                "score": self._virtual_mouse_score(target_id, now=now),
                "temporarily_excluded_target_ids": sorted(excluded),
                "all_candidates_temporarily_excluded_reset": all_excluded_reset,
            }
            for index, target_id in available
        ]
        scored.sort(key=lambda item: (-int(item["score"]), int(item["candidate_index"])))
        return scored[0]

    def _virtual_mouse_stats_debug(self, *, now: float) -> dict[str, dict[str, Any]]:
        stats: dict[str, dict[str, Any]] = {}
        for target_id in self._virtual_mouse_candidate_ids():
            stat = self._virtual_mouse_stats.get(target_id) or {}
            stats[target_id] = {
                "success": int(stat.get("success") or 0),
                "failure": int(stat.get("failure") or 0),
                "consecutive_failures": int(stat.get("consecutive_failures") or 0),
                "last_success_at": stat.get("last_success_at"),
                "last_failure_at": stat.get("last_failure_at"),
                "score": self._virtual_mouse_score(target_id, now=now),
            }
        return stats

    def _virtual_mouse_result_for_learning(
        self,
        actuation: dict[str, Any],
    ) -> dict[str, Any] | None:
        if str(actuation.get("kind") or "") != "advance":
            return None
        if str(actuation.get("strategy_id") or "") != "advance_click":
            return None
        if str(actuation.get("strategy_family") or "") != "dialogue":
            return None
        if not self._actuation_input_source_is_ocr(actuation):
            return None
        result = actuation.get("local_fallback_result")
        if not isinstance(result, dict):
            return None
        if not bool(result.get("success")):
            return None
        if str(result.get("method") or "") != "virtual_mouse_dialogue_click":
            return None
        virtual_mouse = result.get("virtual_mouse")
        if not isinstance(virtual_mouse, dict):
            return None
        if bool(virtual_mouse.get("blocked")):
            return None
        if virtual_mouse.get("success") is False:
            return None
        safety_policy = virtual_mouse.get("safety_policy")
        if not isinstance(safety_policy, dict):
            safety_policy = result.get("safety_policy")
        if isinstance(safety_policy, dict) and bool(safety_policy.get("blocked")):
            return None
        target_id = str(
            virtual_mouse.get("target_id") or actuation.get("virtual_mouse_target_id") or ""
        )
        if target_id not in self._virtual_mouse_candidate_ids():
            return None
        try:
            candidate_index = int(
                virtual_mouse.get("candidate_index")
                if virtual_mouse.get("candidate_index") is not None
                else actuation.get("virtual_mouse_candidate_index")
            )
        except (TypeError, ValueError):
            candidate_index = -1
        return {"target_id": target_id, "candidate_index": candidate_index}

    def _record_virtual_mouse_outcome(
        self,
        actuation: dict[str, Any],
        *,
        success: bool,
        now: float,
    ) -> bool:
        target = self._virtual_mouse_result_for_learning(actuation)
        if target is None:
            return False
        stat = self._virtual_mouse_stat(str(target["target_id"]))
        if success:
            stat["success"] = int(stat.get("success") or 0) + 1
            stat["consecutive_failures"] = 0
            stat["last_success_at"] = now
        else:
            stat["failure"] = int(stat.get("failure") or 0) + 1
            stat["consecutive_failures"] = int(stat.get("consecutive_failures") or 0) + 1
            stat["last_failure_at"] = now
        return True

    def _detect_bridge_progress(
        self,
        shared: dict[str, Any],
        *,
        actuation: dict[str, Any],
    ) -> str | None:
        baseline_session_id = str(actuation.get("baseline_session_id") or "")
        current_session_id = str(shared.get("active_session_id") or "")
        session_changed = bool(
            current_session_id and baseline_session_id and current_session_id != baseline_session_id
        )

        current_snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        current_last_seq = int(shared.get("last_seq") or 0)
        baseline_last_seq = int(actuation.get("baseline_last_seq") or 0)

        if (
            not session_changed
            and build_snapshot_signature(current_snapshot) != actuation.get("baseline_signature")
            and current_last_seq >= baseline_last_seq
        ):
            return "snapshot_signature"

        baseline_snapshot_ts = str(actuation.get("baseline_snapshot_ts") or "")
        current_snapshot_ts = str(current_snapshot.get("ts") or "")
        if (
            not session_changed
            and current_last_seq > baseline_last_seq
            and current_snapshot_ts != baseline_snapshot_ts
        ):
            return "snapshot_ts"

        input_source = str(
            shared.get("active_data_source")
            or actuation.get("input_source")
            or DATA_SOURCE_BRIDGE_SDK
        )
        baseline_line_id = str(actuation.get("baseline_line_id") or "")
        baseline_scene_id = str(actuation.get("baseline_scene_id") or "")
        history_events = shared.get("history_events")
        if not isinstance(history_events, list):
            return None

        for event in reversed(history_events):
            if not isinstance(event, dict):
                continue
            seq = int(event.get("seq") or 0)
            if seq <= baseline_last_seq and not session_changed:
                break
            event_type = str(event.get("type") or "")
            if session_changed:
                if event_type == "save_loaded":
                    return "session_changed:save_loaded"
                if event_type == "choice_selected":
                    return "session_changed:choice_selected"
                continue
            if event_type in self._BRIDGE_PROGRESS_EVENT_TYPES:
                return f"history:{event_type}"
            if input_source != DATA_SOURCE_OCR_READER or event_type != "heartbeat":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            heartbeat_state_ts = str(payload.get("state_ts") or "")
            if heartbeat_state_ts and heartbeat_state_ts != baseline_snapshot_ts:
                return "history:heartbeat_state_ts"
            heartbeat_line_id = str(payload.get("line_id") or "")
            if heartbeat_line_id and heartbeat_line_id != baseline_line_id:
                return "history:heartbeat_line_id"
            heartbeat_scene_id = str(payload.get("scene_id") or "")
            if heartbeat_scene_id and heartbeat_scene_id != baseline_scene_id:
                return "history:heartbeat_scene_id"
        return None

    def _bridge_wait_timeout(self, shared: dict[str, Any], *, actuation: dict[str, Any]) -> float:
        input_source = str(
            shared.get("active_data_source")
            or actuation.get("input_source")
            or DATA_SOURCE_BRIDGE_SDK
        )
        if input_source == DATA_SOURCE_OCR_READER:
            kind = str(actuation.get("kind") or "")
            if kind in {"advance", "probe"}:
                if kind == "advance":
                    return self._ocr_advance_retry_timeout(shared)
                return self._OCR_ADVANCE_BRIDGE_WAIT_TIMEOUT
            if self._has_recent_ocr_bridge_activity(shared, actuation=actuation):
                return self._OCR_BRIDGE_WAIT_TIMEOUT + self._OCR_BRIDGE_ACTIVITY_GRACE_SECONDS
            return self._OCR_BRIDGE_WAIT_TIMEOUT
        return self._DEFAULT_BRIDGE_WAIT_TIMEOUT

    def _has_recent_ocr_bridge_activity(
        self,
        shared: dict[str, Any],
        *,
        actuation: dict[str, Any],
    ) -> bool:
        baseline_last_seq = int(actuation.get("baseline_last_seq") or 0)
        if int(shared.get("last_seq") or 0) > baseline_last_seq:
            return True
        history_events = shared.get("history_events")
        if not isinstance(history_events, list):
            return False
        for event in reversed(history_events):
            if not isinstance(event, dict):
                continue
            seq = int(event.get("seq") or 0)
            if seq <= baseline_last_seq:
                break
            if str(event.get("type") or "") in {
                "heartbeat",
                "line_observed",
                "line_changed",
                "choices_shown",
                "scene_changed",
                "screen_classified",
            }:
                return True
        return False

    def _has_confirmed_ocr_choice_menu(
        self,
        shared: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> bool:
        if self._current_input_source(shared) != DATA_SOURCE_OCR_READER:
            return True
        choices = list(snapshot.get("choices", []))
        screen_type = str(snapshot.get("screen_type") or "").strip()
        if screen_type == OCR_CAPTURE_PROFILE_STAGE_MENU:
            return True
        if not bool(snapshot.get("is_menu_open")) or not choices:
            return False
        history_events = shared.get("history_events")
        if not isinstance(history_events, list):
            return len(choices) >= 2
        current_choice_signature = build_choice_signature(choices)
        current_line_id = str(snapshot.get("line_id") or "")
        current_scene_id = str(snapshot.get("scene_id") or "")
        for event in reversed(history_events):
            if not isinstance(event, dict):
                continue
            if str(event.get("type") or "") != "choices_shown":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if build_choice_signature(list(payload.get("choices") or [])) != current_choice_signature:
                continue
            event_line_id = str(payload.get("line_id") or "")
            if current_line_id and event_line_id and event_line_id != current_line_id:
                continue
            event_scene_id = str(payload.get("scene_id") or "")
            if current_scene_id and event_scene_id and event_scene_id != current_scene_id:
                continue
            return True
        return len(choices) >= 2
