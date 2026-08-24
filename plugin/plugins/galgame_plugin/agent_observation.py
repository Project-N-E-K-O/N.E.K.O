from __future__ import annotations

import hashlib

from .agent_shared import *  # noqa: F401,F403


class AgentObservationMixin:
    async def _observe(
        self,
        shared: dict[str, Any],
        *,
        allow_agent_side_effects: bool = True,
        resume_safe_session_transition: bool = False,
    ) -> bool:
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        session_id = str(shared.get("active_session_id") or "")
        virtual_mouse_runtime_key = self._virtual_mouse_runtime_key(shared)
        selected = latest_selected_choice(shared.get("history_choices", []))
        selected_marker = self._selected_choice_marker(selected)
        now = time.monotonic()
        context_boundary = self._build_context_boundary(
            snapshot,
            selected_marker=selected_marker,
            now=now,
        )
        current_fingerprint = self._session_fingerprint(shared)

        def _trusted_gameplay_evidence_marker() -> str:
            gameplay_event_types = {
                "line_observed",
                "line_changed",
                "choices_shown",
                "choice_selected",
            }
            evidence = {
                "active_game_id": str(shared.get("active_game_id") or ""),
                "active_session_id": session_id,
                "active_data_source": str(shared.get("active_data_source") or ""),
                "snapshot": {
                    field: snapshot.get(field)
                    for field in (
                        "speaker",
                        "text",
                        "line_id",
                        "scene_id",
                        "route_id",
                        "choices",
                    )
                },
                "history_events": [
                    event
                    for event in list(shared.get("history_events") or [])
                    if isinstance(event, dict)
                    and str(event.get("type") or "") in gameplay_event_types
                ],
                "history_lines": list(shared.get("history_lines") or []),
                "history_observed_lines": list(
                    shared.get("history_observed_lines") or []
                ),
                "history_choices": list(shared.get("history_choices") or []),
            }
            canonical = json.dumps(
                evidence,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        previous_game_id = self._normalized_identity_text(
            self._observed_session_fingerprint.get("active_game_id")
        )
        current_game_id = self._normalized_identity_text(
            current_fingerprint.get("active_game_id")
        )
        previous_data_source = str(
            self._observed_session_fingerprint.get("active_data_source") or ""
        )
        current_data_source = str(current_fingerprint.get("active_data_source") or "")
        game_identity_changed = bool(previous_game_id and current_game_id) and (
            previous_game_id != current_game_id
        )
        data_source_changed = bool(previous_data_source and current_data_source) and (
            previous_data_source != current_data_source
        )
        stream_generation_changed = self._normalized_numeric_identity(
            self._observed_session_fingerprint.get("stream_generation")
        ) != self._normalized_numeric_identity(
            current_fingerprint.get("stream_generation")
        )
        if (
            session_id != self._observed_session_id
            or game_identity_changed
            or data_source_changed
            or stream_generation_changed
        ):
            transition_type, transition_reason, transition_fields = self._classify_session_transition(
                self._observed_session_fingerprint,
                current_fingerprint,
            )
            self._last_session_transition_type = transition_type
            self._last_session_transition_reason = transition_reason
            self._last_session_transition_fields = transition_fields
            previous_source = str(transition_fields.get("previous_data_source") or "")
            current_source = str(transition_fields.get("current_data_source") or "")
            source_changed = bool(previous_source and current_source) and (
                previous_source != current_source
            )
            trusted_source_handoff = (
                source_changed
                and self._is_trusted_scene_source_handoff(transition_fields)
            )
            if trusted_source_handoff:
                self._remember_trusted_scene_source_handoff(
                    self._observed_session_fingerprint,
                    current_fingerprint,
                )

            def _identity_matches(previous_key: str, current_key: str) -> bool:
                previous = self._normalized_identity_text(
                    transition_fields.get(previous_key)
                )
                current = self._normalized_identity_text(
                    transition_fields.get(current_key)
                )
                return bool(previous and current and previous == current)

            def _identity_conflicts(previous_key: str, current_key: str) -> bool:
                previous = self._normalized_identity_text(
                    transition_fields.get(previous_key)
                )
                current = self._normalized_identity_text(
                    transition_fields.get(current_key)
                )
                return bool(previous and current and previous != current)

            def _numeric_conflicts(previous_key: str, current_key: str) -> bool:
                previous = self._normalized_numeric_identity(
                    transition_fields.get(previous_key)
                )
                current = self._normalized_numeric_identity(
                    transition_fields.get(current_key)
                )
                return bool(previous and current and previous != current)

            same_game = _identity_matches("previous_game_id", "current_game_id")
            same_process = _identity_matches(
                "previous_process_name",
                "current_process_name",
            )
            same_window = _identity_matches(
                "previous_window_title",
                "current_window_title",
            )
            same_pid = bool(transition_fields.get("previous_pid")) and (
                transition_fields.get("previous_pid")
                == transition_fields.get("current_pid")
            )
            same_hwnd = bool(transition_fields.get("previous_target_hwnd")) and (
                transition_fields.get("previous_target_hwnd")
                == transition_fields.get("current_target_hwnd")
            )
            strong_ocr_identity = bool(
                any((same_game, same_process, same_window, same_pid, same_hwnd))
                and not any(
                    (
                        _identity_conflicts(
                            "previous_game_id",
                            "current_game_id",
                        ),
                        _identity_conflicts(
                            "previous_process_name",
                            "current_process_name",
                        ),
                        _identity_conflicts(
                            "previous_window_title",
                            "current_window_title",
                        ),
                        _numeric_conflicts("previous_pid", "current_pid"),
                        _numeric_conflicts(
                            "previous_target_hwnd",
                            "current_target_hwnd",
                        ),
                    )
                )
            )
            safe_session_transition = bool(
                transition_reason == "initial_observation"
                or trusted_source_handoff
                or (
                    transition_type == "ocr_transient_session_reset"
                    and not source_changed
                    and strong_ocr_identity
                )
            )
            await self._reset_runtime_state(cancel_host_task=True, clear_retry=True)
            self._reset_consult_state()
            self._pending_choice_advice = None
            if trusted_source_handoff:
                self._cancel_scene_capsule_tasks(
                    reason=f"session_transition:{transition_reason}",
                    retire=True,
                )
                # Reader and OCR event sequences belong to independent streams.
                # Preserve delivered content, but never compare the new source's
                # sequence markers with the previous source's high-water marks.
                self._scene_capsule_marker_event_state.clear()
                self._reset_scene_summary_repeat_guard()
                self._session_transition_actuation_blocked = False
                self._summary_debug.pop(
                    "unknown_session_reset_evidence_marker",
                    None,
                )
            elif transition_type == "real_session_reset":
                self._cancel_summary_tasks()
                self._reset_scene_summary_repeat_guard()
                self._scene_tracker.reset(scene_id=str(snapshot.get("scene_id") or ""))
                self._summary_debug.clear()
                self._cat_opinions.clear()
                self._last_delivered_summary_key = ""
                self._last_delivered_summary_seq = 0
                self._last_delivered_summary_scene_id = ""
                self._inbound_messages.clear()
                self._outbound_messages.clear()
                self._failure_memory.clear()
                self._recent_local_inputs.clear()
                self._virtual_mouse_stats.clear()
                self._suggestion_reasons.clear()
                self._clear_hard_error()
                self._session_transition_actuation_blocked = False
            elif transition_type == "unknown_session_reset":
                self._cancel_scene_capsule_tasks(
                    reason=f"session_transition:{transition_reason}",
                    retire=True,
                )
                self._cancel_scene_memory_tasks(
                    reason=f"session_transition:{transition_reason}",
                )
                self._reset_scene_summary_repeat_guard()
                self._scene_tracker.reset(scene_id=str(snapshot.get("scene_id") or ""))
                self._last_delivered_summary_key = ""
                self._last_delivered_summary_seq = 0
                self._last_delivered_summary_scene_id = ""
                self._session_transition_actuation_blocked = True
                self._summary_debug["unknown_session_reset_evidence_marker"] = (
                    _trusted_gameplay_evidence_marker()
                )
                self._summary_debug["last_session_transition"] = {
                    "type": transition_type,
                    "reason": transition_reason,
                    "fields": json_copy(transition_fields),
                }
            else:
                self._cancel_scene_capsule_tasks(
                    reason=f"session_transition:{transition_reason}",
                    retire=True,
                )
                self._session_transition_actuation_blocked = False
                self._summary_debug.pop(
                    "unknown_session_reset_evidence_marker",
                    None,
                )
                self._summary_debug["last_session_transition"] = {
                    "type": transition_type,
                    "reason": transition_reason,
                    "fields": json_copy(transition_fields),
                }
            self._last_interruption = {}
            self._observed_choice_marker = ""
            self._observed_scene_id = str(snapshot.get("scene_id") or "")
            self._observed_route_id = str(snapshot.get("route_id") or "")
            self._observed_session_id = session_id
            self._observed_session_fingerprint = current_fingerprint
            self._remember_context_boundary(context_boundary)
            self._observed_virtual_mouse_runtime_key = virtual_mouse_runtime_key
            if transition_type == "real_session_reset":
                self._clear_ocr_capture_diagnostic()
            self._ocr_last_progress_seq = self._latest_ocr_progress_seq(shared)
            self._next_actuation_at = 0.0
            self._scene_state = self._build_empty_scene_state()
            if not (resume_safe_session_transition and safe_session_transition):
                return False
        if (
            self._session_transition_actuation_blocked
            and self._has_trusted_game_observation(shared)
            and _trusted_gameplay_evidence_marker()
            != str(
                self._summary_debug.get("unknown_session_reset_evidence_marker")
                or ""
            )
        ):
            self._session_transition_actuation_blocked = False
            self._summary_debug.pop(
                "unknown_session_reset_evidence_marker",
                None,
            )
            self._last_session_transition_reason = (
                "trusted_observation_after_unknown_reset"
            )
        self._observed_session_fingerprint = current_fingerprint
        if self._is_untrusted_ocr_capture(shared):
            # Trust is deliberately not part of the semantic input marker.
            # Clearing it creates a newer observation version when the same
            # content becomes trusted again, while the retired task version
            # remains fenced out.
            if self._scene_capsule_input_marker:
                self._scene_capsule_observation_epoch += 1
            self._scene_capsule_input_marker = ""
            self._cancel_scene_capsule_tasks(
                reason="untrusted_ocr_capture",
                retire=True,
            )
            self._summary_debug["last_skip"] = {
                "reason": "untrusted_ocr_capture",
                "session_id": session_id,
                "scene_id": str(snapshot.get("scene_id") or ""),
            }
            return False
        if self._session_transition_actuation_blocked:
            self._summary_debug["last_skip"] = {
                "reason": "awaiting_trusted_gameplay_after_unknown_reset",
                "session_id": session_id,
                "scene_id": str(snapshot.get("scene_id") or ""),
            }
            return False
        if virtual_mouse_runtime_key != self._observed_virtual_mouse_runtime_key:
            if self._observed_virtual_mouse_runtime_key:
                self._virtual_mouse_stats.clear()
            self._observed_virtual_mouse_runtime_key = virtual_mouse_runtime_key

        latest_ocr_progress_seq = self._latest_ocr_progress_seq(shared)
        if latest_ocr_progress_seq > self._ocr_last_progress_seq:
            self._clear_ocr_capture_diagnostic()
            self._ocr_last_progress_seq = latest_ocr_progress_seq

        current_scene_id = str(snapshot.get("scene_id") or "")
        current_route_id = str(snapshot.get("route_id") or "")
        scene_changed = bool(current_scene_id) and (
            current_scene_id != self._observed_scene_id
            or current_route_id != self._observed_route_id
        )
        pending_local_scene_memory: dict[str, Any] | None = None
        if scene_changed:
            if not allow_agent_side_effects:
                # Read-only calls must not archive or schedule, but the newly
                # observed scene still makes any retry for the previous scene
                # permanently stale. Keep the old observed ids so the next
                # normal tick can perform the scene-transition side effects.
                if self._scene_capsule_input_marker:
                    self._scene_capsule_observation_epoch += 1
                self._scene_capsule_input_marker = ""
                self._cancel_scene_capsule_tasks(
                    reason="read_only_scene_changed",
                    retire=True,
                )
                return False
            summary_shared = shared
            timeline_boundary_active = False
            save_context = snapshot.get("save_context")
            save_obj = save_context if isinstance(save_context, dict) else {}
            if str(save_obj.get("kind") or "").strip().lower() in {
                "load",
                "rollback",
            }:
                line_occurrences = self._scene_capsule_line_occurrences(
                    shared,
                    snapshot=snapshot,
                )
                choice_occurrences = self._scene_capsule_choice_occurrences(
                    shared,
                    snapshot=snapshot,
                )
                (
                    filtered_line_occurrences,
                    filtered_choice_occurrences,
                    timeline_boundary_active,
                ) = self._scene_timeline_occurrences_after_save_boundary(
                    shared,
                    snapshot=snapshot,
                    line_occurrences=line_occurrences,
                    choice_occurrences=choice_occurrences,
                )
                if timeline_boundary_active:
                    summary_shared = dict(shared)
                    summary_shared["history_lines"] = [
                        dict(line)
                        for occurrence in filtered_line_occurrences
                        if isinstance(line := occurrence.get("line"), dict)
                    ]
                    # Observed lines do not carry the occurrence identity needed to
                    # prove that they are post-boundary. Drop them conservatively.
                    summary_shared["history_observed_lines"] = []
                    summary_shared["history_choices"] = [
                        {
                            **dict(choice),
                            "action": "selected",
                        }
                        for occurrence in filtered_choice_occurrences
                        if isinstance(choice := occurrence.get("choice"), dict)
                        and str(choice.get("choice_state") or "").strip().lower()
                        == "selected"
                    ]
            context = build_summarize_context(
                summary_shared,
                scene_id=current_scene_id,
                config=self._context_config,
            )
            summary = self._build_local_scene_summary_from_context(
                context,
                scene_id=current_scene_id,
                route_id=current_route_id,
                snapshot=snapshot,
            )
            local_scene_memory = {
                "scene_id": current_scene_id,
                "route_id": current_route_id,
                "summary": summary,
                "ts": str(snapshot.get("ts") or ""),
                "memory_kind": "local",
            }
            if timeline_boundary_active:
                # A periodic summary resets scene memory when it observes the
                # load boundary. Apply the filtered local seed after that reset.
                pending_local_scene_memory = local_scene_memory
            else:
                self._upsert_local_scene_memory(local_scene_memory)
            self._observed_scene_id = current_scene_id
            self._observed_route_id = current_route_id
            self._scene_tracker.reset_summary(scene_id=current_scene_id)
            self._remember_context_boundary(context_boundary)

        if allow_agent_side_effects:
            if not scene_changed:
                self._maybe_schedule_context_boundary_summary(
                    shared,
                    session_id=session_id,
                    snapshot=snapshot,
                    boundary=context_boundary,
                )
            await self._maybe_push_periodic_scene_summary(shared, snapshot=snapshot)
            if pending_local_scene_memory is not None:
                self._upsert_local_scene_memory(pending_local_scene_memory)
            # host-play-mode plan, steps 8 + 10: fire-and-forget consultation.
            # Re-enqueues the consult prompt through _push_agent_message so the
            # cat receives it via the normal channel; replies arrive via the
            # existing inbound queue and update shared['cat_opinions'].
            try:
                await self._maybe_consult_cat(
                    shared,
                    snapshot=snapshot,
                    scene_changed=scene_changed,
                )
            except Exception:  # noqa: BLE001 — consultation must never break observe
                self._logger.warning(
                    "galgame cat consultation failed",
                    exc_info=True,
                )

        if selected is not None:
            if not allow_agent_side_effects:
                return False
            marker = selected_marker
            if marker and marker != self._observed_choice_marker:
                choice_id = str(selected.get("choice_id") or "")
                choice_text = str(selected.get("text") or "")
                self._append_bounded(
                    self._choice_memory,
                    {
                        "choice_id": choice_id,
                        "text": choice_text,
                        "scene_id": str(selected.get("scene_id") or ""),
                        "route_id": str(selected.get("route_id") or ""),
                        "ts": str(selected.get("ts") or ""),
                    },
                    limit=64,
                )
                reason = self._suggestion_reasons.pop(choice_id, "")
                self._suggestion_reasons.clear()
                if self._should_push_choice(shared) and reason:
                    await self._push_agent_message(
                        shared,
                        kind="choice_reason",
                        content=(
                            f"\u5df2\u9009\u62e9\u300c{choice_text}\u300d\u3002"
                            f"\u63a8\u8350\u7406\u7531\uff1a{reason}"
                        ),
                        scene_id=str(selected.get("scene_id") or ""),
                        route_id=str(selected.get("route_id") or ""),
                        priority=8,
                        metadata={"suppress_delivery": reason.startswith("cat_advice:")},
                    )
                self._observed_choice_marker = marker
        return True
