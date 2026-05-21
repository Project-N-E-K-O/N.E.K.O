from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403
from .agent_prompt import _bounded_choice_instruction_text, _context_line_count
from .agent_message_router import AgentMessageRouter
from .agent_scene_tracker import AgentSceneTracker


class AgentObservationMixin:
    async def _observe(
        self,
        shared: dict[str, Any],
        *,
        allow_agent_side_effects: bool = True,
    ) -> None:
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
        if session_id != self._observed_session_id:
            transition_type, transition_reason, transition_fields = self._classify_session_transition(
                self._observed_session_fingerprint,
                current_fingerprint,
            )
            self._last_session_transition_type = transition_type
            self._last_session_transition_reason = transition_reason
            self._last_session_transition_fields = transition_fields
            await self._reset_runtime_state(cancel_host_task=True, clear_retry=True)
            self._reset_consult_state()
            self._pending_choice_advice = None
            if transition_type == "real_session_reset":
                self._cancel_summary_tasks()
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
                self._session_transition_actuation_blocked = True
                self._summary_debug["last_session_transition"] = {
                    "type": transition_type,
                    "reason": transition_reason,
                    "fields": json_copy(transition_fields),
                }
            else:
                self._session_transition_actuation_blocked = False
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
            return
        if self._session_transition_actuation_blocked and self._has_trusted_game_observation(shared):
            self._session_transition_actuation_blocked = False
            self._last_session_transition_reason = "trusted_observation_after_unknown_reset"
        self._observed_session_fingerprint = current_fingerprint
        if self._is_untrusted_ocr_capture(shared):
            self._summary_debug["last_skip"] = {
                "reason": "untrusted_ocr_capture",
                "session_id": session_id,
                "scene_id": str(snapshot.get("scene_id") or ""),
            }
            return
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
        if scene_changed:
            if not allow_agent_side_effects:
                return
            context = build_summarize_context(
                shared,
                scene_id=current_scene_id,
                config=self._context_config,
            )
            summary = self._build_local_scene_summary_from_context(
                context,
                scene_id=current_scene_id,
                route_id=current_route_id,
                snapshot=snapshot,
            )
            self._append_bounded(
                self._scene_memory,
                {
                    "scene_id": current_scene_id,
                    "route_id": current_route_id,
                    "summary": summary,
                    "ts": str(snapshot.get("ts") or ""),
                },
                limit=32,
            )
            if self._observed_scene_id and self._should_push_scene(shared):
                self._schedule_scene_summary_task(
                    shared=shared,
                    session_id=session_id,
                    scene_id=current_scene_id,
                    route_id=current_route_id,
                    snapshot=snapshot,
                    context=context,
                    trigger="scene_changed",
                    metadata={
                        "context_type": "galgame_scene_context",
                        "trigger": "scene_changed",
                    },
                    update_scene_memory=True,
                )
            self._observed_scene_id = current_scene_id
            self._observed_route_id = current_route_id
            self._scene_tracker.reset_summary(scene_id=current_scene_id)
            self._remember_context_boundary(context_boundary)
            # host-play-mode plan, step 13: refresh cross-scene memory on every
            # confirmed scene change. Heuristic merge today; LLM-driven update
            # routed through this same hook once a summary-tier extraction op
            # lands in LLMGateway.
            try:
                self._maybe_update_cross_scene_memory(
                    shared,
                    scene_id=current_scene_id,
                    route_id=current_route_id,
                )
            except Exception:  # noqa: BLE001 — cross-scene merge must never break observe
                self._logger.warning(
                    "galgame cross_scene_memory update failed",
                    exc_info=True,
                )

        if allow_agent_side_effects:
            if not scene_changed:
                self._maybe_schedule_context_boundary_summary(
                    shared,
                    session_id=session_id,
                    snapshot=snapshot,
                    boundary=context_boundary,
                )
            await self._maybe_push_periodic_scene_summary(shared, snapshot=snapshot)
            # host-play-mode plan, steps 8 + 10: fire-and-forget consultation.
            # Re-enqueues the consult prompt through _push_agent_message so the
            # cat receives it via the normal channel; replies arrive via the
            # existing inbound queue and update shared['cat_opinions'].
            try:
                await self._maybe_consult_cat(
                    shared,
                    snapshot=snapshot,
                    scene_changed=bool(scene_changed),
                )
            except Exception:  # noqa: BLE001 — consultation must never break observe
                self._logger.warning(
                    "galgame cat consultation failed",
                    exc_info=True,
                )

        if selected is not None:
            if not allow_agent_side_effects:
                return
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
