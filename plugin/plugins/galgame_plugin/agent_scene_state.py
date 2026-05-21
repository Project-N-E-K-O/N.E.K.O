from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403
from .agent_prompt import _bounded_choice_instruction_text, _context_line_count
from .agent_message_router import AgentMessageRouter
from .agent_scene_tracker import AgentSceneTracker


class AgentSceneStateMixin:
    def _compute_status(self, shared: dict[str, Any]) -> str:
        if self._explicit_standby:
            return AGENT_STATUS_STANDBY
        if not self._is_actionable(shared):
            return AGENT_STATUS_STANDBY
        if self._hard_error:
            return AGENT_STATUS_ERROR
        return AGENT_STATUS_ACTIVE

    @staticmethod
    def _is_actionable(shared: dict[str, Any]) -> bool:
        connection_state = str(shared.get("current_connection_state") or "")
        if connection_state != "active":
            return False
        if not str(shared.get("active_session_id") or ""):
            return False
        if bool(shared.get("stream_reset_pending")):
            return False
        snapshot = shared.get("latest_snapshot")
        return isinstance(snapshot, dict) and bool(snapshot)

    def _should_push_scene(self, shared: dict[str, Any]) -> bool:
        return bool(shared.get("push_notifications")) and mode_allows_agent_push(
            str(shared.get("mode") or "")
        )

    def _should_push_choice(self, shared: dict[str, Any]) -> bool:
        return bool(shared.get("push_notifications")) and mode_allows_choice_push(
            str(shared.get("mode") or "")
        )

    def _update_scene_state(self, shared: dict[str, Any], now: float) -> None:
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        signature = build_snapshot_signature(snapshot)
        scene_id = str(snapshot.get("scene_id") or "")
        route_id = str(snapshot.get("route_id") or "")
        scene_changed = (
            scene_id != str(self._scene_state.get("scene_id") or "")
            or route_id != str(self._scene_state.get("route_id") or "")
        )
        signature_changed = signature != self._scene_state.get("signature")
        next_stage = self._classify_scene_stage(snapshot, now=now, scene_changed=scene_changed)
        if next_stage not in _SCREEN_RECOVERY_STAGES:
            self._screen_recovery_diagnostic = ""

        if scene_changed:
            previous_scene_id = str(self._scene_state.get("scene_id") or "")
            summary_context = build_summarize_context(
                shared,
                scene_id=scene_id,
                config=self._context_config,
            )
            summary_seed = build_local_scene_summary(
                scene_id=scene_id,
                route_id=route_id,
                lines=summary_context["stable_lines"],
                selected_choices=summary_context["recent_choices"],
                snapshot=snapshot,
            )
            self._scene_state = {
                "scene_id": scene_id,
                "route_id": route_id,
                "previous_scene_id": previous_scene_id,
                "signature": signature,
                "stage": next_stage,
                "stage_ticks": 1,
                "same_signature_ticks": 0,
                "last_progress_at": now,
                "last_scene_change_at": now,
                "summary_seed": summary_seed,
            }
            self._advance_retry_budget.clear()
            self._ocr_hold_release_budget.clear()
            return

        if signature_changed:
            self._scene_state["signature"] = signature
            self._scene_state["same_signature_ticks"] = 0
            self._scene_state["last_progress_at"] = now
        else:
            self._scene_state["same_signature_ticks"] = int(
                self._scene_state.get("same_signature_ticks") or 0
            ) + 1

        previous_stage = str(self._scene_state.get("stage") or "")
        if next_stage != previous_stage:
            self._scene_state["stage"] = next_stage
            self._scene_state["stage_ticks"] = 1
            if next_stage == "dialogue" and previous_stage != "dialogue":
                self._clear_ocr_capture_diagnostic()
        else:
            self._scene_state["stage_ticks"] = int(self._scene_state.get("stage_ticks") or 0) + 1

        self._scene_state["scene_id"] = scene_id
        self._scene_state["route_id"] = route_id

    def _preview_scene_state(self, shared: dict[str, Any], *, now: float) -> dict[str, Any]:
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        signature = build_snapshot_signature(snapshot)
        scene_id = str(snapshot.get("scene_id") or "")
        route_id = str(snapshot.get("route_id") or "")
        current = json_copy(self._scene_state)
        scene_changed = (
            scene_id != str(current.get("scene_id") or "")
            or route_id != str(current.get("route_id") or "")
        )
        next_stage = self._classify_scene_stage(snapshot, now=now, scene_changed=scene_changed)
        if scene_changed:
            return {
                "scene_id": scene_id,
                "route_id": route_id,
                "previous_scene_id": str(current.get("scene_id") or ""),
                "signature": signature,
                "stage": next_stage,
                "stage_ticks": 1,
                "same_signature_ticks": 0,
                "last_progress_at": current.get("last_progress_at") or 0.0,
                "last_scene_change_at": current.get("last_scene_change_at") or 0.0,
                "summary_seed": str(current.get("summary_seed") or ""),
            }
        preview = dict(current)
        preview["scene_id"] = scene_id
        preview["route_id"] = route_id
        preview["signature"] = signature
        preview["stage"] = next_stage
        return preview
