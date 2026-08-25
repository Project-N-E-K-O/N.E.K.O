from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403


class AgentThinkingMixin:
    @property
    def _scene_memory(self) -> list[dict[str, Any]]:
        return self._scene_tracker.scene_memory

    @property
    def _choice_memory(self) -> list[dict[str, Any]]:
        return self._scene_tracker.choice_memory

    def _remember_suggestion_reason(self, choice_id: str, reason: str, *, limit: int = 32) -> None:
        if not choice_id or not reason:
            return
        self._suggestion_reasons.pop(choice_id, None)
        self._suggestion_reasons[choice_id] = reason
        while len(self._suggestion_reasons) > limit:
            oldest_key = next(iter(self._suggestion_reasons))
            self._suggestion_reasons.pop(oldest_key, None)

    def _vision_attachment_reason(
        self,
        shared: dict[str, Any],
        *,
        snapshot: dict[str, Any],
    ) -> str:
        if self._current_input_source(shared) != DATA_SOURCE_OCR_READER:
            return ""
        screen_type = str(snapshot.get("screen_type") or "").strip()
        try:
            screen_confidence = float(snapshot.get("screen_confidence") or 0.0)
        except (TypeError, ValueError):
            screen_confidence = 0.0
        has_dialogue_text = bool(snapshot.get("text") or snapshot.get("line_id"))
        if has_dialogue_text and screen_type in {
            "",
            OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        }:
            return ""
        runtime = shared.get("ocr_reader_runtime")
        runtime_obj = runtime if isinstance(runtime, dict) else {}
        detail = str(runtime_obj.get("detail") or "")
        context_state = str(runtime_obj.get("ocr_context_state") or "")
        if self._ocr_capture_diagnostic or detail == "ocr_capture_diagnostic_required":
            return "ocr_diagnostic"
        if context_state in {"diagnostic_required", "capture_failed", "stale_capture_backend"}:
            return f"ocr_context_{context_state}"
        recent_recover_failures = sum(
            1
            for item in self._failure_memory[-5:]
            if isinstance(item, dict) and str(item.get("kind") or "") == "recover"
        )
        if recent_recover_failures >= 2:
            return "repeated_recover_failures"
        if not screen_type or screen_type == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            return "unknown_screen"
        if screen_confidence < 0.55 and screen_type in {
            OCR_CAPTURE_PROFILE_STAGE_TITLE,
            OCR_CAPTURE_PROFILE_STAGE_MENU,
            OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
            OCR_CAPTURE_PROFILE_STAGE_CONFIG,
            OCR_CAPTURE_PROFILE_STAGE_GALLERY,
            OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
            OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
        }:
            return "low_confidence_screen"
        return ""
