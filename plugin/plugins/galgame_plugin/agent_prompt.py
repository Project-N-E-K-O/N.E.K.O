from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403
from .context_tokens import truncate_tokens_heuristic


def _bounded_choice_instruction_text(value: object) -> str:
    text = str(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CHOICE_INSTRUCTION_CONTROL_RE.sub(" ", text)
    return truncate_tokens_heuristic(
        text,
        _CHOICE_INSTRUCTION_TEXT_MAX_TOKENS,
        notice_template="\n...[truncated {omitted} chars]",
    )


def _context_line_count(lines: object) -> int:
    if not isinstance(lines, list):
        return 0
    total = 0
    for item in lines:
        if not isinstance(item, dict):
            total += 1
            continue
        try:
            count = int(item.get("_condensed_count") or 1)
        except (TypeError, ValueError):
            count = 1
        total += max(1, count)
    return total


class AgentPromptMixin:
    def _build_agent_reply_context(self, shared: dict[str, Any], *, prompt: str) -> dict[str, Any]:
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        status = self._compute_status(shared)
        history_lines = list(shared.get("history_lines") or [])
        history_observed_lines = list(shared.get("history_observed_lines") or [])
        scene_id = str(snapshot.get("scene_id") or "")
        route_id = str(snapshot.get("route_id") or "")
        min_limit, max_limit, target_tokens = _context_window_bounds(
            self._context_config,
            min_floor=16,
            max_floor=16,
        )
        tagged_stable = [
            {**dict(item), "_reply_context_source": "stable"}
            for item in history_lines
            if isinstance(item, dict)
            and (
                not scene_id
                or not str(item.get("scene_id") or "")
                or str(item.get("scene_id") or "") == scene_id
            )
        ]
        tagged_observed = [
            {**dict(item), "_reply_context_source": "observed"}
            for item in history_observed_lines
            if isinstance(item, dict)
            and (
                not scene_id
                or not str(item.get("scene_id") or "")
                or str(item.get("scene_id") or "") == scene_id
            )
        ]
        recency_ordered = _recency_ordered_context_lines(tagged_stable, tagged_observed)
        line_limit = _compute_dynamic_line_limit(
            recency_ordered,
            min_limit=min_limit,
            max_limit=max_limit,
            target_tokens=target_tokens,
        )
        history_choices = list(shared.get("history_choices") or [])
        if line_limit > 0:
            merged_recent = recency_ordered[-line_limit:]
            stable_lines = [
                {
                    key: value
                    for key, value in item.items()
                    if key != "_reply_context_source"
                }
                for item in merged_recent
                if item.get("_reply_context_source") == "stable"
            ]
            observed_lines = [
                {
                    key: value
                    for key, value in item.items()
                    if key != "_reply_context_source"
                }
                for item in merged_recent
                if item.get("_reply_context_source") == "observed"
            ]
            recent_lines = [
                {
                    key: value
                    for key, value in item.items()
                    if key != "_reply_context_source" and not str(key).startswith("_condensed_")
                }
                for item in merged_recent
            ]
            recent_line_ids = {
                str(item.get("line_id") or "")
                for item in recent_lines
                if str(item.get("line_id") or "")
            }
            matching_history_choices = [
                (index, dict(item))
                for index, item in enumerate(history_choices)
                if isinstance(item, dict)
                and (
                    not scene_id
                    or not str(item.get("scene_id") or "")
                    or str(item.get("scene_id") or "") == scene_id
                )
            ]
            choices_without_line_id = [
                (index, item)
                for index, item in matching_history_choices
                if not str(item.get("line_id") or "").strip()
            ]
            choices_with_recent_line_id = [
                (index, item)
                for index, item in matching_history_choices
                if str(item.get("line_id") or "").strip()
                and str(item.get("line_id") or "") in recent_line_ids
            ]
            recent_choices = [
                item
                for _index, item in sorted(
                    [*choices_without_line_id, *choices_with_recent_line_id],
                    key=lambda pair: pair[0],
                )
            ][-line_limit:]
        else:
            stable_lines = []
            observed_lines = []
            recent_choices = []
            recent_lines = []
        effective_line = resolve_effective_current_line(shared) or {}
        latest_line = ""
        if effective_line.get("text"):
            speaker = str(effective_line.get("speaker") or "Narration")
            latest_line = (
                f"{speaker}: "
                f"{str(effective_line.get('text') or '')}"
            )
        restored_context_snapshot = _matching_context_snapshot(
            shared,
            scene_id=scene_id,
            route_id=route_id,
        )
        public_context = {
            "current_line": {
                "speaker": str(effective_line.get("speaker") or ""),
                "text": str(effective_line.get("text") or ""),
                "line_id": str(effective_line.get("line_id") or ""),
                "scene_id": str(effective_line.get("scene_id") or scene_id),
                "route_id": str(effective_line.get("route_id") or route_id),
                "source": str(effective_line.get("source") or ""),
                "stability": str(effective_line.get("stability") or ""),
            },
            "latest_line": latest_line,
            "recent_lines": json_copy(recent_lines),
            "stable_lines": json_copy(stable_lines),
            "observed_lines": json_copy(observed_lines),
            "recent_choices": json_copy(recent_choices),
            "scene_summary_seed": _scene_summary_seed_with_restored_context(
                shared,
                scene_id=scene_id,
                route_id=route_id,
                lines=recent_lines,
                selected_choices=recent_choices,
                snapshot=snapshot,
                restored_context_snapshot=restored_context_snapshot,
            ),
            "restored_context_snapshot": json_copy(restored_context_snapshot),
            "diagnostic": self._target_window_focus_diagnostic(shared)
            or self._ocr_capture_diagnostic
            or "",
            "screen_context": self._screen_context_payload(shared),
        }
        context = {
            "prompt": prompt,
            "game_id": str(shared.get("active_game_id") or ""),
            "session_id": str(shared.get("active_session_id") or ""),
            "scene_id": scene_id,
            "route_id": route_id,
            "public_context": public_context,
            "status": status,
            "agent_user_status": self._agent_user_status(shared, status=status),
            "mode": str(shared.get("mode") or ""),
            "input_source": self._current_input_source(shared),
            "push_policy": self._current_push_policy(shared),
            "standby_requested": self._explicit_standby,
        }
        pov_context = self._fixed_character_pov_context(
            shared, applied_to="agent_reply"
        )
        if pov_context:
            context.update(pov_context)
            public_context["fixed_character_pov"] = json_copy(
                pov_context["fixed_character_pov"]
            )
        context.update(self._vision_context_payload(shared, snapshot=snapshot))
        return context

    def _vision_context_payload(
        self,
        shared: dict[str, Any],
        *,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        reason = self._vision_attachment_reason(shared, snapshot=snapshot)
        if not reason:
            return {}
        snapshot_getter = getattr(self._plugin, "latest_ocr_vision_snapshot", None)
        if not callable(snapshot_getter):
            return {}
        try:
            vision_snapshot = snapshot_getter()
        except Exception as exc:
            self._trace_runtime(f"vision snapshot unavailable: {exc}")
            return {}
        if not isinstance(vision_snapshot, dict):
            return {}
        image_base64 = str(vision_snapshot.get("vision_image_base64") or "").strip()
        if not image_base64:
            return {}
        metadata = {
            key: json_copy(value)
            for key, value in vision_snapshot.items()
            if key != "vision_image_base64"
        }
        return {
            "vision_enabled": True,
            "vision_image_base64": image_base64,
            "vision_detail": "low",
            "vision_reason": reason,
            "vision_snapshot": metadata,
        }

    def _fixed_character_anchor_block(
        self,
        name: str,
        profile: dict[str, Any] | None,
        *,
        level: str = "L1",
    ) -> str:
        if not name or not profile:
            return ""
        plugin = getattr(self, "_plugin", None)
        manager_getter = getattr(plugin, "_get_character_profile_manager", None)
        if manager_getter is None:
            return ""
        try:
            manager = manager_getter()
        except Exception:  # noqa: BLE001
            return ""
        state = getattr(plugin, "_state", None)
        runtime_state = (
            getattr(state, "character_runtime_state", {}) or {}
        ).get(name) if state is not None else None
        payload = manager.build_character_push_payload(
            [name],
            {name: profile},
            runtime_states={name: runtime_state} if runtime_state else {},
            level=level,
            fixed_character=name,
        )
        try:
            return self._push_composer.build_character_block(payload)
        except Exception:  # noqa: BLE001
            return ""

    def _fixed_character_pov_context(
        self,
        shared: dict[str, Any],
        *,
        applied_to: str,
    ) -> dict[str, Any]:
        mode, name = self._character_mode_state()
        if mode != "fixed" or not name:
            return {}
        source = dict(shared) if isinstance(shared, dict) else {}
        source["character_mode"] = mode
        source["character_fixed_name"] = name
        profile = self._resolve_character_profile(name)
        if profile:
            source["character_profiles"] = {name: profile}
        plugin = getattr(self, "_plugin", None)
        state = getattr(plugin, "_state", None) if plugin else None
        if state is not None:
            source.setdefault(
                "character_profile_game_id",
                str(getattr(state, "character_profile_game_id", "") or ""),
            )
            source.setdefault(
                "character_profile_match_reason",
                str(getattr(state, "character_profile_match_reason", "") or ""),
            )
            runtime_states = getattr(state, "character_runtime_state", {}) or {}
            if isinstance(runtime_states, dict) and name in runtime_states:
                source["character_runtime_state"] = {name: runtime_states.get(name)}
        result = _build_fixed_character_pov_context(source)
        pov = result.get("fixed_character_pov") if isinstance(result, dict) else None
        if not isinstance(pov, dict):
            return {}
        pov = dict(pov)
        pov["applied_to"] = applied_to
        block = self._fixed_character_anchor_block(name, profile, level="L1")
        if block:
            pov["profile_context"] = block
        return {"fixed_character_pov": pov}

    def _maybe_augment_with_character_anchor(
        self,
        shared: dict[str, Any],
        content: str,
        *,
        kind: str,
    ) -> str:
        """Prepend the catgirl-facing character anchor when fixed mode is on.

        Only scene-context-like pushes get augmented — choice acks and similar
        short replies pass through unchanged so the prepend cost is bounded.
        """
        if not content:
            return content
        if kind not in {
            "scene_context",
            "scene_summary",
            "choice_reason",
            "cat_consultation",
            "proactive_notification",
        }:
            return content
        mode, name = self._character_mode_state()
        if mode != "fixed" or not name:
            return content
        profile = self._resolve_character_profile(name)
        if not profile:
            return content
        character_block = self._fixed_character_anchor_block(name, profile, level="L1")
        if not character_block:
            return content
        return f"{character_block}\n\n{content}"

    def _record_push_history(
        self,
        outbound: dict[str, Any] | None,
        *,
        kind: str,
        scene_id: str,
        content_len: int,
    ) -> None:
        plugin = getattr(self, "_plugin", None)
        history = getattr(plugin, "_push_history", None) if plugin else None
        if history is None:
            return
        self._push_seq_counter += 1
        record = {
            "push_seq": self._push_seq_counter,
            "kind": kind,
            "scene_id": scene_id,
            "content_size": int(content_len),
            "ts": time.time(),
            "delivered": bool(
                outbound and str(outbound.get("status") or "") == "delivered"
            ),
        }
        try:
            history.append(record)
        except Exception:  # noqa: BLE001 — deque may have a maxlen
            self._logger.warning(
                "galgame push_history record append failed", exc_info=True
            )
