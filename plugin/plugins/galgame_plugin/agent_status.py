from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403


class AgentStatusMixin:
    async def apply_mode_change(self, shared: dict[str, Any]) -> dict[str, Any]:
        self._ensure_loop_affinity()
        await self._observe(shared)
        if not self._should_actuate(shared):
            await self._reset_runtime_state(cancel_host_task=True, clear_retry=True)
            self._clear_hard_error()
            self._next_actuation_at = time.monotonic() + 1.0
        status = self._compute_status(shared)
        self._last_status = status
        return self._build_status_payload(shared, status=status, interrupted=False)

    async def query_status(self, shared: SharedStatePayload) -> dict[str, Any]:
        self._ensure_loop_affinity()
        interrupted = await self._interrupt_for_status_query()
        await self._observe(shared, allow_agent_side_effects=False)
        now = time.monotonic()
        self._update_scene_state(shared, now)
        self._clear_actuation_error_if_read_only(shared)
        self._convert_screen_recovery_hard_error_if_applicable(shared, now=now)
        self._recover_retryable_error_if_ready(now)
        status = self._compute_status(shared)
        self._last_status = status
        return {
            "action": "query_status",
            **self._build_status_payload(
                shared,
                status=status,
                interrupted=interrupted,
            ),
        }

    async def peek_status(self, shared: SharedStatePayload) -> dict[str, Any]:
        self._ensure_loop_affinity()
        now = time.monotonic()
        scene_state = self._preview_scene_state(shared, now=now)
        status = self._compute_status(shared)
        return self._build_status_payload(
            shared,
            status=status,
            interrupted=False,
            scene_state=scene_state,
            extra_summary_debug=self._peek_summary_debug(shared),
        )

    def _agent_user_status(self, shared: dict[str, Any], *, status: str) -> str:
        if self._hard_error or status == AGENT_STATUS_ERROR:
            return "error"
        if self._explicit_standby:
            return "paused_by_user"
        if self._should_pause_for_target_window_focus(shared):
            return "paused_window_not_foreground"
        if (
            self._should_pause_for_minigame_screen(shared)
            or self._should_pause_for_screen_recovery(shared)
        ):
            return "screen_safety_pause"
        if self._should_hold_for_ocr_capture_diagnostic(shared):
            return "ocr_unavailable"
        if not self._is_actionable(shared):
            return "read_only"
        if not self._should_actuate(shared):
            return "read_only"
        if self._actuation is not None:
            return "acting"
        if self._planning_task is not None:
            return "waiting_choice"
        if str(self._scene_state.get("stage") or "") == "choice_menu":
            return "waiting_choice"
        return "running"

    def _ocr_reader_trigger_mode(self, shared: dict[str, Any]) -> str:
        cfg = getattr(self._plugin, "_cfg", None)
        cfg_mode = str(getattr(cfg, "ocr_reader_trigger_mode", "") or "").strip().lower()
        if cfg_mode:
            return cfg_mode
        shared_mode = str(shared.get("ocr_reader_trigger_mode") or "").strip().lower()
        return shared_mode or OCR_TRIGGER_MODE_INTERVAL

    def _ocr_window_not_foreground_pause_message(
        self,
        shared: dict[str, Any],
        *,
        target_note: str,
    ) -> str:
        base = "已暂停：游戏窗口不在前台。切回游戏窗口后自动继续。"
        if target_note:
            base += target_note
        trigger_mode = self._ocr_reader_trigger_mode(shared)
        if trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE:
            return (
                f"{base}当前为按推进后识别模式，后台期间不会持续 OCR；"
                "切回后会尝试重新采集。"
            )
        if trigger_mode == OCR_TRIGGER_MODE_INTERVAL:
            return (
                f"{base}当前为定时 OCR，会尝试在后台读取；"
                "实际效果取决于窗口可见性、非最小化状态和捕获后端。"
            )
        return f"{base}OCR 后台读取状态取决于触发模式和捕获后端。"

    def _agent_pause_info(self, shared: dict[str, Any], *, status: str) -> dict[str, Any]:
        user_status = self._agent_user_status(shared, status=status)
        mode = str(shared.get("mode") or "")
        target = self._target_window_label(shared)
        if user_status == "paused_by_user":
            return {
                "agent_pause_kind": "user",
                "agent_pause_message": "Agent 已手动待机。点击“恢复活跃”后才会继续自动操作。",
                "agent_can_resume_by_button": True,
                "agent_can_resume_by_focus": False,
            }
        if user_status == "paused_window_not_foreground":
            target_note = f"当前目标：{target}。" if target else ""
            return {
                "agent_pause_kind": "window_not_foreground",
                "agent_pause_message": self._ocr_window_not_foreground_pause_message(
                    shared,
                    target_note=target_note,
                ),
                "agent_can_resume_by_button": False,
                "agent_can_resume_by_focus": True,
            }
        if user_status == "screen_safety_pause":
            recovery_diagnostic = str(self._screen_recovery_diagnostic or "")
            if recovery_diagnostic:
                return {
                    "agent_pause_kind": "screen_safety",
                    "agent_pause_message": (
                        "Automatic screen recovery is paused because local input or "
                        f"computer_use is unavailable: {recovery_diagnostic}"
                    ),
                    "agent_can_resume_by_button": False,
                    "agent_can_resume_by_focus": False,
                }
            return {
                "agent_pause_kind": "screen_safety",
                "agent_pause_message": "已暂停自动推进：当前像小游戏或非 VN 操作画面，避免盲目输入。",
                "agent_can_resume_by_button": False,
                "agent_can_resume_by_focus": False,
            }
        if user_status == "ocr_unavailable":
            diagnostic = self._ocr_capture_diagnostic or "OCR 截图、窗口目标或后端不可用。"
            return {
                "agent_pause_kind": "ocr_unavailable",
                "agent_pause_message": f"已暂停自动推进：{diagnostic}",
                "agent_can_resume_by_button": False,
                "agent_can_resume_by_focus": False,
            }
        if user_status == "read_only":
            if mode == "choice_advisor" and not self._is_actionable(shared):
                return {
                    "agent_pause_kind": "read_only",
                    "agent_pause_message": (
                        "自动推进已开启，正在等待游戏会话、OCR 台词或目标窗口进入可操作状态。"
                    ),
                    "agent_can_resume_by_button": False,
                    "agent_can_resume_by_focus": False,
                }
            mode_label = "伴读/静默模式" if mode in {"silent", "companion"} else "只读模式"
            return {
                "agent_pause_kind": "read_only",
                "agent_pause_message": f"当前为{mode_label}，不会自动点击。需要自动推进时请切到自动推进模式。",
                "agent_can_resume_by_button": False,
                "agent_can_resume_by_focus": False,
            }
        return {
            "agent_pause_kind": "none",
            "agent_pause_message": "",
            "agent_can_resume_by_button": False,
            "agent_can_resume_by_focus": False,
        }

    @staticmethod
    def _target_window_label(shared: dict[str, Any]) -> str:
        runtime = shared.get("ocr_reader_runtime")
        if not isinstance(runtime, dict):
            return ""
        process_name = str(
            runtime.get("process_name")
            or runtime.get("effective_process_name")
            or ""
        ).strip()
        title = str(
            runtime.get("window_title")
            or runtime.get("effective_window_title")
            or ""
        ).strip()
        pid = int(runtime.get("pid") or 0)
        parts = []
        if process_name:
            parts.append(process_name)
        if title:
            parts.append(title)
        if pid:
            parts.append(f"pid {pid}")
        return " / ".join(parts)

    def _build_status_payload(
        self,
        shared: dict[str, Any],
        *,
        status: str,
        interrupted: bool,
        scene_state: dict[str, Any] | None = None,
        extra_summary_debug: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        recent_pushes = json_copy(self._recent_push_records()[-20:])
        last_outbound_message = (
            json_copy(self._outbound_messages[-1]) if self._outbound_messages else None
        )
        status_scene_state = scene_state if isinstance(scene_state, dict) else self._scene_state
        debug_now = time.monotonic()
        pause_info = self._agent_pause_info(shared, status=status)
        pending_choice_advice = json_copy(self._pending_choice_advice or {})
        pending_choice_requested_at = float(pending_choice_advice.get("requested_at") or 0.0)
        pending_choice_age = (
            max(0.0, debug_now - pending_choice_requested_at)
            if pending_choice_requested_at > 0
            else 0.0
        )
        scene_summary_lines_until_push = max(
            0,
            self._scene_summary_push_line_interval - int(self._summary_lines_since_push or 0),
        )
        existing_task_debug = (
            self._summary_debug.get("task")
            if isinstance(self._summary_debug.get("task"), dict)
            else {}
        )
        task_status_debug = {
            **dict(existing_task_debug or {}),
            **self._summary_task_status_debug(),
        }
        summary_debug = {
            **self._summary_debug,
            "last_processed_event_seq": self._scene_tracker.summary_last_processed_event_seq,
            "scene_states": self._scene_tracker.summary_scene_statuses(
                current_scene_id=str(snapshot.get("scene_id") or "")
            ),
            "task": task_status_debug,
            "pending_summary_task_count": len(self._summary_tasks),
            "pending_summary_tasks": task_status_debug["pending"],
            "last_delivered_summary_key": self._last_delivered_summary_key,
            "last_delivered_summary_seq": self._last_delivered_summary_seq,
            "last_delivered_summary_scene_id": self._last_delivered_summary_scene_id,
            "thresholds": {
                "line_interval": self._scene_summary_push_line_interval,
                "half_threshold": self._scene_push_half_threshold,
                "time_fallback_seconds": self._scene_push_time_fallback_seconds,
                "merge_total_threshold": self._scene_merge_total_threshold,
                "cross_scene_total_threshold": self._scene_cross_scene_total_threshold,
            },
        }
        if extra_summary_debug:
            summary_debug.update(json_copy(extra_summary_debug))
        return {
            "result": self._build_status_result(
                shared,
                status=status,
                interrupted=interrupted,
                scene_state=status_scene_state,
            ),
            "status": status,
            "agent_user_status": self._agent_user_status(shared, status=status),
            **pause_info,
            "activity": self._current_activity_label(),
            "reason": self._current_status_reason(shared),
            "error": self._hard_error,
            "session_id": str(shared.get("active_session_id") or ""),
            "scene_id": str(snapshot.get("scene_id") or ""),
            "route_id": str(snapshot.get("route_id") or ""),
            "line_id": str(snapshot.get("line_id") or ""),
            "scene_stage": str(status_scene_state.get("stage") or "unknown"),
            "input_source": self._current_input_source(shared),
            "advance_speed": self._configured_advance_speed(shared),
            "effective_advance_speed": self._effective_advance_speed(shared),
            "mode": str(shared.get("mode") or ""),
            "push_notifications": bool(shared.get("push_notifications")),
            "push_policy": self._current_push_policy(shared),
            "actionable": self._is_actionable(shared),
            "standby_requested": self._explicit_standby,
            "interrupted": interrupted,
            "inbound_queue_size": len(self._inbound_messages),
            "outbound_queue_size": len(self._outbound_messages),
            "last_interruption": json_copy(self._last_interruption),
            "last_outbound_message": last_outbound_message,
            "pending_choice_advice": pending_choice_advice,
            "pending_choice_advice_age_seconds": pending_choice_age,
            "choice_advice_wait_timeout_seconds": self._CHOICE_ADVICE_WAIT_TIMEOUT_SECONDS,
            "scene_summary_line_interval": self._scene_summary_push_line_interval,
            "scene_summary_lines_since_push": self._summary_lines_since_push,
            "scene_summary_lines_until_push": scene_summary_lines_until_push,
            "memory_counts": {
                "scene_memory": len(self._scene_memory),
                "choice_memory": len(self._choice_memory),
                "failure_memory": len(self._failure_memory),
                "recent_pushes": len(self._message_router.push_delivery_history),
                "inbound_messages": len(self._inbound_messages),
                "outbound_messages": len(self._outbound_messages),
                "recent_local_inputs": len(self._recent_local_inputs),
            },
            "recent_pushes": recent_pushes,
            "last_push": json_copy(recent_pushes[-1]) if recent_pushes else None,
            "last_session_transition_type": self._last_session_transition_type,
            "last_session_transition_reason": self._last_session_transition_reason,
            "last_session_transition_fields": json_copy(self._last_session_transition_fields),
            "session_transition_actuation_blocked": self._session_transition_actuation_blocked,
            "debug": {
                "last_trace": self._last_trace_message,
                "runtime_loop_id": id(self._runtime_loop) if self._runtime_loop is not None else 0,
                "current_loop_id": id(asyncio.get_running_loop()),
                "planning_active": self._planning_task is not None,
                "actuation": json_copy(self._actuation) if self._actuation is not None else None,
                "pending_strategy": json_copy(self._pending_strategy)
                if self._pending_strategy is not None
                else None,
                "scene_state": json_copy(status_scene_state),
                "summary": json_copy(summary_debug),
                "recent_local_inputs": json_copy(self._recent_local_inputs[-10:]),
                "advance_observation_window_seconds": self._ocr_advance_observation_window(shared),
                "advance_retry_timeout_seconds": self._ocr_advance_retry_timeout(shared),
                "ocr_no_observed_advance_count": self._ocr_no_observed_advance_count,
                "ocr_capture_diagnostic_required": bool(
                    self._ocr_capture_diagnostic
                    or self._should_hold_for_ocr_capture_diagnostic(shared)
                ),
                "ocr_capture_diagnostic": self._ocr_capture_diagnostic,
                "screen_recovery_diagnostic": self._screen_recovery_diagnostic,
                "ocr_context_state": str(
                    (shared.get("ocr_reader_runtime") or {}).get("ocr_context_state") or ""
                )
                if isinstance(shared.get("ocr_reader_runtime"), dict)
                else "",
                "target_window_not_foreground": self._should_pause_for_target_window_focus(shared),
                "target_window_diagnostic": self._target_window_focus_diagnostic(shared),
                "virtual_mouse_stats": self._virtual_mouse_stats_debug(now=debug_now),
                "virtual_mouse_preferred_target": self._select_virtual_mouse_dialogue_candidate(
                    now=debug_now,
                    mutate=False,
                ),
            },
        }

    def _build_status_result(
        self,
        shared: dict[str, Any],
        *,
        status: str,
        interrupted: bool,
        scene_state: dict[str, Any] | None = None,
    ) -> str:
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        status_scene_state = scene_state if isinstance(scene_state, dict) else self._scene_state
        parts = [
            f"status={status}",
            f"session={str(shared.get('active_session_id') or '') or 'none'}",
            f"scene={str(snapshot.get('scene_id') or '') or 'none'}",
            f"route={str(snapshot.get('route_id') or '') or 'none'}",
            f"line={str(snapshot.get('line_id') or '') or 'none'}",
            f"stage={str(status_scene_state.get('stage') or 'unknown')}",
            f"activity={self._current_activity_label()}",
            f"user_status={self._agent_user_status(shared, status=status)}",
            f"input_source={self._current_input_source(shared)}",
            f"push_policy={self._current_push_policy(shared)}",
            f"reason={self._current_status_reason(shared)}",
        ]
        if interrupted:
            parts.append("interrupted=yes")
        if self._hard_error:
            parts.append(f"error={self._hard_error}")
        return " ".join(parts)

    @staticmethod
    def _current_input_source(shared: dict[str, Any]) -> str:
        return str(shared.get("active_data_source") or DATA_SOURCE_BRIDGE_SDK)

    @staticmethod
    def _normalized_identity_text(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    def _session_fingerprint(self, shared: dict[str, Any]) -> dict[str, Any]:
        meta = shared.get("active_session_meta")
        meta_obj = meta if isinstance(meta, dict) else {}
        metadata = meta_obj.get("metadata")
        metadata_obj = metadata if isinstance(metadata, dict) else {}
        runtime = shared.get("ocr_reader_runtime")
        runtime_obj = runtime if isinstance(runtime, dict) else {}
        locked_target = runtime_obj.get("locked_target")
        locked_target_obj = locked_target if isinstance(locked_target, dict) else {}
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        return {
            "active_session_id": str(shared.get("active_session_id") or ""),
            "active_game_id": str(shared.get("active_game_id") or ""),
            "active_data_source": self._current_input_source(shared),
            "meta_data_source": str(meta_obj.get("data_source") or ""),
            "meta_game_id": str(meta_obj.get("game_id") or ""),
            "meta_session_id": str(meta_obj.get("session_id") or ""),
            "process_name": str(
                metadata_obj.get("game_process_name")
                or runtime_obj.get("effective_process_name")
                or runtime_obj.get("process_name")
                or ""
            ),
            "pid": int(
                metadata_obj.get("game_pid")
                or runtime_obj.get("pid")
                or locked_target_obj.get("pid")
                or 0
            ),
            "window_title": str(
                metadata_obj.get("window_title")
                or runtime_obj.get("effective_window_title")
                or runtime_obj.get("window_title")
                or locked_target_obj.get("title")
                or ""
            ),
            "target_hwnd": int(runtime_obj.get("target_hwnd") or locked_target_obj.get("hwnd") or 0),
            "target_window_visible": bool(runtime_obj.get("target_window_visible")),
            "target_window_minimized": bool(runtime_obj.get("target_window_minimized")),
            "ocr_detail": str(runtime_obj.get("detail") or ""),
            "ocr_context_state": str(runtime_obj.get("ocr_context_state") or ""),
            "scene_id": str(snapshot.get("scene_id") or ""),
            "snapshot_ts": str(snapshot.get("ts") or ""),
        }

    def _trusted_history_token(self, shared: dict[str, Any]) -> str:
        return self._trusted_history_token_from_fingerprint(self._session_fingerprint(shared))

    def _trusted_history_token_from_fingerprint(self, fp: dict[str, Any]) -> str:
        data_source = str(fp.get("active_data_source") or "")
        if data_source == DATA_SOURCE_OCR_READER:
            parts = [
                data_source,
                str(fp.get("active_game_id") or ""),
                self._normalized_identity_text(fp.get("process_name")),
                self._normalized_identity_text(fp.get("window_title")),
                str(int(fp.get("target_hwnd") or 0)),
            ]
        else:
            parts = [
                data_source,
                str(fp.get("active_game_id") or ""),
                str(fp.get("active_session_id") or ""),
            ]
        return "|".join(parts)

    def _classify_session_transition(
        self,
        previous: dict[str, Any],
        current: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        if not previous:
            return "same_session", "initial_observation", {}
        fields = {
            "previous_session_id": str(previous.get("active_session_id") or ""),
            "current_session_id": str(current.get("active_session_id") or ""),
            "previous_game_id": str(previous.get("active_game_id") or ""),
            "current_game_id": str(current.get("active_game_id") or ""),
            "previous_data_source": str(previous.get("active_data_source") or ""),
            "current_data_source": str(current.get("active_data_source") or ""),
            "previous_process_name": str(previous.get("process_name") or ""),
            "current_process_name": str(current.get("process_name") or ""),
            "previous_pid": int(previous.get("pid") or 0),
            "current_pid": int(current.get("pid") or 0),
            "previous_window_title": str(previous.get("window_title") or ""),
            "current_window_title": str(current.get("window_title") or ""),
            "previous_target_hwnd": int(previous.get("target_hwnd") or 0),
            "current_target_hwnd": int(current.get("target_hwnd") or 0),
            "ocr_detail": str(current.get("ocr_detail") or ""),
            "ocr_context_state": str(current.get("ocr_context_state") or ""),
        }
        if (
            fields["previous_session_id"] == fields["current_session_id"]
            and fields["previous_game_id"] == fields["current_game_id"]
            and fields["previous_data_source"] == fields["current_data_source"]
        ):
            return "same_session", "session_identity_unchanged", fields

        previous_source = fields["previous_data_source"]
        current_source = fields["current_data_source"]
        if DATA_SOURCE_OCR_READER not in {previous_source, current_source}:
            return "real_session_reset", "non_ocr_session_or_source_changed", fields
        if current_source in {DATA_SOURCE_BRIDGE_SDK, DATA_SOURCE_MEMORY_READER}:
            return "real_session_reset", "trusted_reader_replaced_ocr_session", fields

        previous_process = self._normalized_identity_text(fields["previous_process_name"])
        current_process = self._normalized_identity_text(fields["current_process_name"])
        previous_title = self._normalized_identity_text(fields["previous_window_title"])
        current_title = self._normalized_identity_text(fields["current_window_title"])
        process_changed = bool(previous_process and current_process and previous_process != current_process)
        pid_changed = bool(fields["previous_pid"] and fields["current_pid"] and fields["previous_pid"] != fields["current_pid"])
        title_changed = bool(previous_title and current_title and previous_title != current_title)
        hwnd_changed = bool(
            fields["previous_target_hwnd"]
            and fields["current_target_hwnd"]
            and fields["previous_target_hwnd"] != fields["current_target_hwnd"]
        )
        game_changed = bool(fields["previous_game_id"] and fields["current_game_id"] and fields["previous_game_id"] != fields["current_game_id"])
        if game_changed and (process_changed or pid_changed or (title_changed and hwnd_changed)):
            return "real_session_reset", "ocr_game_and_window_identity_changed", fields
        if process_changed or pid_changed:
            return "real_session_reset", "ocr_process_identity_changed", fields

        transient_detail = fields["ocr_detail"] in {
            "capture_failed",
            "screen_classified",
            "self_ui_guard_blocked",
            "ocr_capture_diagnostic_required",
            "attached_no_text_yet",
        }
        stable_window = bool(
            (previous_process and previous_process == current_process)
            or (previous_title and previous_title == current_title)
            or (
                fields["previous_target_hwnd"]
                and fields["previous_target_hwnd"] == fields["current_target_hwnd"]
            )
        )
        if stable_window or transient_detail or bool(current.get("target_window_visible")):
            return "ocr_transient_session_reset", "ocr_session_changed_without_real_identity_change", fields
        return "unknown_session_reset", "insufficient_evidence_for_real_reset", fields

    def _has_trusted_game_observation(self, shared: dict[str, Any]) -> bool:
        if self._is_untrusted_ocr_capture(shared):
            return False
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        if str(snapshot.get("text") or "").strip():
            return True
        return any(
            isinstance(line, dict) and str(line.get("text") or "").strip()
            for line in list(shared.get("history_lines") or [])[-3:]
        )

    @staticmethod
    def _is_untrusted_ocr_capture(shared: dict[str, Any]) -> bool:
        runtime = shared.get("ocr_reader_runtime")
        runtime_obj = runtime if isinstance(runtime, dict) else {}
        snapshot = shared.get("latest_snapshot")
        snapshot_obj = snapshot if isinstance(snapshot, dict) else {}
        return (
            shared.get("ocr_capture_content_trusted") is False
            or runtime_obj.get("ocr_capture_content_trusted") is False
            or snapshot_obj.get("ocr_capture_content_trusted") is False
        )

    def _current_status_reason(self, shared: dict[str, Any]) -> str:
        if self._hard_error:
            return "hard_error"
        if self._explicit_standby:
            return "explicit_standby"
        if not self._is_actionable(shared):
            return "bridge_inactive"
        if self._session_transition_actuation_blocked:
            return "unknown_session_reset"
        if not self._should_actuate(shared):
            return "mode_read_only"
        if self._planning_task is not None:
            return "planning_choice"
        if self._actuation is not None:
            return (
                f"actuating_{str(self._actuation.get('kind') or 'unknown')}_"
                f"{str(self._actuation.get('state') or 'running')}"
            )
        if self._should_pause_for_target_window_focus(shared):
            return "target_window_not_foreground"
        if self._pending_strategy is not None:
            return "retry_pending"
        if self._should_pause_for_minigame_screen(shared):
            return "minigame_screen_pause"
        if self._should_pause_for_screen_recovery(shared):
            return "screen_recovery_pause"
        if self._should_hold_for_ocr_capture_diagnostic(shared):
            return self._hold_reason_from_diagnostic()
        return "background_loop_ready"

    def _current_push_policy(self, shared: dict[str, Any]) -> str:
        if not bool(shared.get("push_notifications")):
            return "disabled"
        mode = str(shared.get("mode") or "")
        if mode_allows_choice_push(mode):
            return "selective_scene_and_choice"
        if mode_allows_agent_push(mode):
            return "selective_scene_only"
        return "disabled"
