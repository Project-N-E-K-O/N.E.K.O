from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403
from .agent_prompt import _bounded_choice_instruction_text, _context_line_count
from .agent_message_router import AgentMessageRouter
from .agent_scene_tracker import AgentSceneTracker


class AgentThinkingMixin:
    @property
    def _scene_memory(self) -> list[dict[str, Any]]:
        return self._scene_tracker.scene_memory

    @property
    def _choice_memory(self) -> list[dict[str, Any]]:
        return self._scene_tracker.choice_memory

    def _cross_scene_memory_snapshot(self, shared: dict[str, Any]) -> dict[str, Any]:
        plugin = getattr(self, "_plugin", None)
        state = getattr(plugin, "_state", None) if plugin else None
        state_memory = getattr(state, "cross_scene_memory", None) if state else None
        if isinstance(state_memory, dict):
            memory = _cross_scene_sanitize(state_memory)
            if _render_cross_scene_memory_for_push(memory):
                return memory
        shared_memory = shared.get("cross_scene_memory") if isinstance(shared, dict) else None
        return _cross_scene_sanitize(shared_memory if isinstance(shared_memory, dict) else None)

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

    def _maybe_augment_with_cross_scene_memory(
        self,
        shared: dict[str, Any],
        content: str,
        *,
        kind: str,
    ) -> str:
        if not content:
            return content
        if kind not in {"scene_context", "scene_summary", "choice_reason"}:
            return content
        memory = self._cross_scene_memory_snapshot(shared)
        rendered = _render_cross_scene_memory_for_push(memory, max_chars=360)
        if not rendered:
            return content
        return f"Cross-scene memory (reference only):\n{rendered}\n\n{content}"

    def _maybe_update_cross_scene_memory(
        self,
        shared: dict[str, Any],
        *,
        scene_id: str,
        route_id: str,
    ) -> None:
        """Heuristic merge of the latest scene summary into ``cross_scene_memory``.

        Today the merge is deterministic (no LLM): the most recent scene's
        summary becomes ``last_key_event`` for every fixed-mode character known
        to the plugin, and a plot thread is appended/refreshed for the scene.
        The hook is already wired so the eventual ``summary``-tier extractor
        from cross_scene_memory.update_cross_scene_memory only needs to be
        swapped in.
        """
        plugin = getattr(self, "_plugin", None)
        state = getattr(plugin, "_state", None) if plugin else None
        if state is None:
            return
        scene_memory = list(self._scene_memory or [])
        if not scene_memory:
            return
        recent = [
            dict(entry)
            for entry in scene_memory[-3:]
            if isinstance(entry, dict)
        ]
        if not recent:
            return
        latest_summary = str(recent[-1].get("summary") or "").strip()
        if not latest_summary:
            return
        existing = _cross_scene_sanitize(
            getattr(state, "cross_scene_memory", None) or _cross_scene_empty_memory()
        )
        characters_state = (
            getattr(state, "character_runtime_state", {}) or {}
        )
        merged_characters = dict(existing.get("characters", {}))
        for name, runtime in (characters_state or {}).items():
            current_entry = merged_characters.get(name, {})
            merged_characters[name] = {
                "arc": str(
                    (runtime or {}).get("arc_stage")
                    or current_entry.get("arc")
                    or ""
                ),
                "last_key_event": latest_summary[:120],
                "current_emotion": str(
                    (runtime or {}).get("current_emotion")
                    or current_entry.get("current_emotion")
                    or ""
                ),
                "confidence": float(current_entry.get("confidence") or 0.5),
            }
        existing_threads = list(existing.get("plot_threads") or [])
        route_label = route_id or "unknown"
        scene_label = scene_id or "unknown"
        thread_label = f"route::{route_label}::scene::{scene_label}"
        thread_entry = next(
            (t for t in existing_threads if str(t.get("thread") or "") == thread_label),
            None,
        )
        if thread_entry is None:
            existing_threads.append(
                {
                    "thread": thread_label,
                    "status": latest_summary[:160],
                    "key_scenes": [scene_id] if scene_id else [],
                    "updated_at_seq": self._push_seq_counter,
                    "confidence": 0.4,
                }
            )
        else:
            thread_entry["status"] = latest_summary[:160]
            scenes = list(thread_entry.get("key_scenes") or [])
            if scene_id and scene_id not in scenes:
                scenes.append(scene_id)
                thread_entry["key_scenes"] = scenes
            thread_entry["updated_at_seq"] = self._push_seq_counter
            thread_entry["confidence"] = max(
                0.4, float(thread_entry.get("confidence") or 0.4)
            )
        existing_threads = existing_threads[-16:]  # bound the structure
        updated = {
            "characters": merged_characters,
            "plot_threads": existing_threads,
            "last_updated_seq": self._push_seq_counter,
            "low_confidence_streak": int(existing.get("low_confidence_streak") or 0),
        }
        state.cross_scene_memory = updated
        plugin._cached_snapshot = None  # type: ignore[attr-defined]
        persist = getattr(plugin, "_persist", None)
        if persist is not None:
            try:
                persist.persist_config_override(
                    STORE_CROSS_SCENE_MEMORY,
                    json_copy(updated),
                )
            except Exception:  # noqa: BLE001
                self.logger.warning(
                    "failed to persist galgame cross_scene_memory",
                    exc_info=True,
                )
        self._cross_scene_memory_dirty = True
