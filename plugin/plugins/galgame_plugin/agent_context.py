from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403
from .agent_prompt import _bounded_choice_instruction_text, _context_line_count
from .agent_message_router import AgentMessageRouter
from .agent_scene_tracker import AgentSceneTracker


class AgentContextMixin:
    @staticmethod
    def _screen_context_payload(shared: dict[str, Any]) -> dict[str, Any]:
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        elements = list(snapshot.get("screen_ui_elements") or [])
        if not elements:
            elements = list(shared.get("screen_ui_elements") or [])
        bounded_elements = []
        for index, item in enumerate(elements[:10]):
            element = dict(item or {})
            bounded = {
                "index": index + 1,
                "text": str(element.get("text") or ""),
                "role": str(element.get("role") or ""),
                "text_source": str(element.get("text_source") or ""),
            }
            for key in (
                "bounds",
                "normalized_bounds",
                "bounds_coordinate_space",
                "source_size",
                "capture_rect",
                "window_rect",
            ):
                value = element.get(key)
                if value:
                    bounded[key] = json_copy(value)
            bounded_elements.append(bounded)
        try:
            screen_confidence = float(
                snapshot.get("screen_confidence") or shared.get("screen_confidence") or 0.0
            )
        except (TypeError, ValueError):
            screen_confidence = 0.0
        return {
            "screen_type": str(snapshot.get("screen_type") or shared.get("screen_type") or ""),
            "screen_confidence": screen_confidence,
            "screen_debug": json_copy(snapshot.get("screen_debug") or shared.get("screen_debug") or {}),
            "ui_elements": bounded_elements,
        }

    def _with_strategy_memory_context(
        self,
        shared: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        enriched = self._with_cat_opinions_for_strategy(shared, context)
        pov_context = self._fixed_character_pov_context(
            shared, applied_to="suggest_choice"
        )
        if pov_context:
            enriched = {**dict(enriched), **pov_context}
        memory = self._cross_scene_memory_snapshot(shared)
        rendered = _render_cross_scene_memory_for_push(memory, max_chars=360)
        if not rendered:
            return enriched
        enriched = dict(enriched)
        enriched["cross_scene_memory"] = json_copy(memory)
        enriched["cross_scene_memory_context"] = rendered
        return enriched

    @staticmethod
    def _context_boundary_key(boundary: dict[str, str]) -> str:
        if not boundary:
            return ""
        return json.dumps(boundary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _remember_context_boundary(self, boundary: dict[str, str]) -> None:
        self._observed_context_boundary = dict(boundary)
        self._observed_context_boundary_key = self._context_boundary_key(boundary)

    def _build_context_boundary(
        self,
        snapshot: dict[str, Any],
        *,
        selected_marker: str,
        now: float,
    ) -> dict[str, str]:
        save_context = snapshot.get("save_context") if isinstance(snapshot.get("save_context"), dict) else {}
        save_kind = str(save_context.get("kind") or "")
        save_slot = str(save_context.get("slot_id") or "")
        save_name = str(save_context.get("display_name") or "")
        screen_type = str(snapshot.get("screen_type") or "").strip()
        try:
            screen_confidence = float(snapshot.get("screen_confidence") or 0.0)
        except (TypeError, ValueError):
            screen_confidence = 0.0
        if screen_type == OCR_CAPTURE_PROFILE_STAGE_DEFAULT or screen_confidence < 0.45:
            screen_type_key = ""
        else:
            screen_type_key = screen_type
        stage = self._classify_scene_stage(snapshot, now=now, scene_changed=False)
        if (
            stage == "scene_transition"
            and screen_type_key != OCR_CAPTURE_PROFILE_STAGE_TRANSITION
            and save_kind not in {"load", "rollback"}
        ):
            stage = "unknown"
        return {
            "scene_id": str(snapshot.get("scene_id") or ""),
            "route_id": str(snapshot.get("route_id") or ""),
            "stage": stage,
            "screen_type": screen_type_key,
            "save_kind": save_kind,
            "save_marker": f"{save_kind}:{save_slot}:{save_name}",
            "choice_marker": selected_marker,
        }

    @staticmethod
    def _context_boundary_trigger(
        previous: dict[str, str],
        current: dict[str, str],
    ) -> str:
        if not previous:
            return ""
        if current.get("scene_id") != previous.get("scene_id") or current.get("route_id") != previous.get("route_id"):
            return "scene_changed"
        if current.get("choice_marker") and current.get("choice_marker") != previous.get("choice_marker"):
            return "choice_selected"
        if (
            current.get("save_marker") != previous.get("save_marker")
            and (current.get("save_kind") in {"load", "rollback"} or previous.get("save_kind") in {"load", "rollback"})
        ):
            return "save_context_changed"
        if current.get("stage") != previous.get("stage"):
            return "screen_stage_changed"
        if current.get("screen_type") != previous.get("screen_type"):
            return "screen_type_changed"
        if current.get("save_marker") != previous.get("save_marker"):
            return "save_context_changed"
        return "context_boundary_changed"

    def _maybe_schedule_context_boundary_summary(
        self,
        shared: dict[str, Any],
        *,
        session_id: str,
        snapshot: dict[str, Any],
        boundary: dict[str, str],
    ) -> None:
        scene_id = str(boundary.get("scene_id") or "")
        if not scene_id:
            self._remember_context_boundary(boundary)
            return
        previous = dict(self._observed_context_boundary)
        boundary_key = self._context_boundary_key(boundary)
        if not self._observed_context_boundary_key:
            self._remember_context_boundary(boundary)
            return
        if boundary_key == self._observed_context_boundary_key:
            return
        trigger = self._context_boundary_trigger(previous, boundary)
        self._remember_context_boundary(boundary)
        if not trigger or trigger == "scene_changed" or not self._should_push_scene(shared):
            return
        route_id = str(boundary.get("route_id") or snapshot.get("route_id") or "")
        context = build_summarize_context(
            shared,
            scene_id=scene_id,
            config=self._context_config,
        )
        self._schedule_scene_summary_task(
            shared=shared,
            session_id=session_id,
            scene_id=scene_id,
            route_id=route_id,
            snapshot=snapshot,
            context=context,
            trigger=trigger,
            metadata={
                "context_type": "galgame_scene_context",
                "trigger": trigger,
                "context_boundary": json_copy(boundary),
            },
            update_scene_memory=False,
        )

    async def query_context(self, shared: dict[str, Any], *, context_query: str) -> dict[str, Any]:
        self._ensure_loop_affinity()
        message: dict[str, Any]
        reply_context: dict[str, Any]
        status_snapshot: str
        input_source_snapshot: str
        await self._observe(shared)
        message = self._enqueue_inbound_message(
            kind="query_context",
            content=context_query,
            priority=8,
        )
        self._mark_message(message, status="processing")
        try:
            await self._interrupt_for_inbound_message(message)
            self._recover_retryable_error_if_ready(time.monotonic())
            reply_context = self._build_agent_reply_context(shared, prompt=context_query)
            status_snapshot = self._compute_status(shared)
            input_source_snapshot = self._current_input_source(shared)
        except Exception as exc:
            self._mark_message(
                message,
                status="failed",
                metadata={"error": str(exc)},
            )
            raise
        try:
            async with self._agent_reply_lock:
                payload = await self._llm_gateway.agent_reply(reply_context)
        except Exception as exc:
            self._mark_message(
                message,
                status="failed",
                metadata={"error": str(exc)},
            )
            raise
        self._last_status = status_snapshot
        self._mark_message(message, status="completed", delivered=True)
        return {
            "action": "query_context",
            "result": str(payload.get("reply") or ""),
            "status": status_snapshot,
            "degraded": bool(payload.get("degraded")),
            "diagnostic": str(payload.get("diagnostic") or ""),
            "input_source": input_source_snapshot,
            "message": json_copy(message),
        }
