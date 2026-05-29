from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403


class AgentConsultMixin:
    def _is_explicit_cat_consultation_reply(
        self,
        inbound: dict[str, Any],
    ) -> dict[str, Any] | None:
        metadata = inbound.get("metadata") if isinstance(inbound.get("metadata"), dict) else {}
        reply_to = str(metadata.get("reply_to_message_id") or "").strip()
        if reply_to:
            return self._pending_cat_consultation_message(message_id=reply_to)
        sender_role = str(metadata.get("sender_role") or "").strip().lower()
        if bool(metadata.get("consultation_reply")) and sender_role in {
            "cat",
            "catgirl",
            "character",
        }:
            return self._pending_cat_consultation_message()
        return None

    def _with_cat_opinions_for_strategy(
        self,
        shared: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        cat_opinions = self._cat_opinion_snapshot(shared)
        if not cat_opinions:
            return context
        local_shared = dict(shared)
        local_shared["cat_opinions"] = cat_opinions
        rendered = render_cat_opinions_for_strategy(local_shared)
        if not rendered:
            return context
        enriched = dict(context)
        enriched["cat_opinion_context"] = rendered
        enriched["cat_opinions"] = json_copy(cat_opinions)
        return enriched

    def _cat_opinion_snapshot(self, shared: dict[str, Any]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        shared_queue = shared.get("cat_opinions")
        sources = []
        if isinstance(shared_queue, list):
            sources.append([dict(item) for item in shared_queue if isinstance(item, dict)])
        sources.append(self._cat_opinions)
        for source in sources:
            for entry in source:
                if not isinstance(entry, dict):
                    continue
                item = dict(entry)
                key = (
                    str(item.get("opinion") or ""),
                    str(item.get("scene_id") or ""),
                    str(item.get("reason") or ""),
                    str(item.get("ts") or ""),
                )
                if not key[0] or key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        return json_copy(merged[-MAX_CAT_OPINIONS:])

    def _apply_pending_cat_consultation_reply(
        self,
        shared: dict[str, Any],
        *,
        message: str,
        pending: dict[str, Any],
    ) -> dict[str, Any] | None:
        text = str(message or "").strip()
        if not text:
            return None
        metadata = pending.get("metadata") if isinstance(pending.get("metadata"), dict) else {}
        scene_id = str(metadata.get("scene_id") or "")
        reason = str(metadata.get("consultation_reason") or "")
        record = self.receive_cat_opinion(
            shared,
            text,
            scene_id=scene_id,
            reason=reason,
        )
        if record is None:
            return None
        self._mark_message(
            pending,
            status="acked",
            acked=True,
            metadata={"cat_opinion_recorded": True},
        )
        self._recent_pushes = self._recent_push_records()
        status = self._compute_status(shared)
        self._last_status = status
        return {
            "action": "send_message",
            "result": "已记录猫娘意见，将作为后续选择和推进策略的参考。",
            "status": status,
            "degraded": False,
            "diagnostic": "",
            "input_source": self._current_input_source(shared),
            "cat_opinion": record,
        }

    def _current_activity_label(self) -> str:
        if self._planning_task is not None:
            return "planning"
        if self._actuation is not None:
            kind = str(self._actuation.get("kind") or "unknown")
            state = str(self._actuation.get("state") or "running")
            return f"{kind}:{state}"
        if self._pending_strategy is not None:
            return "retry_pending"
        return "idle"

    def _character_mode_state(self) -> tuple[str, str]:
        """Return ``(character_mode, character_fixed_name)`` from plugin state.

        Always falls back to ``("off", "")`` when the plugin has not exposed
        a state (e.g. in unit tests using a stub plugin).
        """
        plugin = getattr(self, "_plugin", None)
        if plugin is None:
            return "off", ""
        state = getattr(plugin, "_state", None)
        if state is None:
            return "off", ""
        mode = str(getattr(state, "character_mode", "off") or "off")
        name = str(getattr(state, "character_fixed_name", "") or "")
        return mode, name

    def _resolve_character_profile(self, name: str) -> dict[str, Any] | None:
        if not name:
            return None
        plugin = getattr(self, "_plugin", None)
        state = getattr(plugin, "_state", None) if plugin else None
        profiles = getattr(state, "character_profiles", None) if state else None
        if isinstance(profiles, dict):
            profile = profiles.get(name)
            if isinstance(profile, dict):
                return profile
        # Prefer the same runtime-aware resolver used by the character-profile
        # UI, so Agent restarts do not fall back to bound_game_id-only loading.
        context_loader = getattr(plugin, "_load_character_profiles_for_current_context", None)
        if context_loader is not None:
            try:
                load = context_loader()
            except Exception:  # noqa: BLE001
                self._logger.warning(
                    "cosplay character profile context load failed",
                    exc_info=True,
                )
                load = None
            profiles = (load or {}).get("profiles") if isinstance(load, dict) else None
            if isinstance(profiles, dict):
                profile = profiles.get(name)
                if isinstance(profile, dict):
                    return profile
        # Lazy-activate when bound game is known but profiles not loaded yet.
        activator = getattr(plugin, "_activate_character_profiles", None)
        bound_game_id = (
            str(getattr(state, "bound_game_id", "") or "") if state else ""
        )
        if activator and bound_game_id:
            try:
                load = activator(bound_game_id)
            except Exception:  # noqa: BLE001
                self._logger.warning(
                    "cosplay character profile activation failed",
                    exc_info=True,
                )
                return None
            profiles = (load or {}).get("profiles") if isinstance(load, dict) else None
            if isinstance(profiles, dict):
                profile = profiles.get(name)
                if isinstance(profile, dict):
                    return profile
        return None

    def _build_consult_inputs(
        self,
        shared: dict[str, Any],
        *,
        snapshot: dict[str, Any],
        scene_changed: bool,
    ) -> ConsultInputs:
        mode, name = self._character_mode_state()
        profile_known = bool(self._resolve_character_profile(name)) if name else False
        history_lines = shared.get("history_lines") or []
        seen_total = len(history_lines) if isinstance(history_lines, list) else 0
        delta = max(0, seen_total - self._last_consult_seen_line_count)
        choices_raw = list(snapshot.get("choices") or [])
        visible_choices = tuple(
            str(item.get("text") or "").strip()
            for item in choices_raw
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        )
        return ConsultInputs(
            character_mode=mode,
            character_fixed_name=name,
            scene_id=str(snapshot.get("scene_id") or ""),
            visible_choices=visible_choices,
            scene_changed=bool(scene_changed),
            lines_since_last_consult=delta,
            now=time.monotonic(),
            last_consult_ts=float(self._last_cat_consult_ts or 0.0),
            profile_known=profile_known,
        )

    async def _maybe_consult_cat(
        self,
        shared: dict[str, Any],
        *,
        snapshot: dict[str, Any],
        scene_changed: bool,
    ) -> None:
        if not self._should_push_scene(shared):
            return
        inputs = self._build_consult_inputs(
            shared, snapshot=snapshot, scene_changed=scene_changed
        )
        decision = decide_consultation(inputs)
        if not decision.should_consult:
            return
        profile = self._resolve_character_profile(decision.character_name)
        if not profile:
            return
        voice = summarize_character_voice(profile)
        scene_summary = self._latest_scene_summary_text(snapshot)
        recent_lines = self._latest_recent_line_texts(shared, limit=5)
        prompt = build_consult_prompt(
            reason=decision.reason,
            character_name=decision.character_name,
            character_voice_summary=voice,
            scene_summary=scene_summary,
            visible_choices=inputs.visible_choices,
            recent_lines=recent_lines,
        )
        pending_key = ":".join(
            [
                str(snapshot.get("scene_id") or ""),
                str(snapshot.get("route_id") or ""),
                str(decision.character_name or ""),
                str(decision.reason or ""),
            ]
        )
        if pending_key in self._pending_consults:
            return
        self._pending_consults.add(pending_key)
        # Fire-and-forget — the cat reply arrives via the existing inbound
        # channel; the strategy builder consumes shared['cat_opinions'] later.
        seen_after_consult = (
            self._last_consult_seen_line_count + inputs.lines_since_last_consult
        )
        session_id = str(shared.get("active_session_id") or "")

        async def _deliver_consult() -> bool:
            if session_id and session_id != self._observed_session_id:
                return False
            return await self._push_agent_message(
                shared,
                kind="cat_consultation",
                content=prompt,
                scene_id=str(snapshot.get("scene_id") or ""),
                route_id=str(snapshot.get("route_id") or ""),
                priority=5,
                metadata={
                    "consultation": True,
                    "consultation_reason": decision.reason,
                    "consultation_character": decision.character_name,
                },
            )

        def _advance_consult_state(task: asyncio.Task[bool]) -> None:
            self._consultation_tasks.discard(task)
            self._pending_consults.discard(pending_key)
            try:
                delivered = bool(task.result())
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("cat consultation delivery task failed: {}", exc)
                return
            if not delivered:
                return
            if session_id and session_id != self._observed_session_id:
                return
            self._last_cat_consult_ts = inputs.now
            self._last_consult_seen_line_count = max(
                self._last_consult_seen_line_count,
                seen_after_consult,
            )

        task = asyncio.create_task(_deliver_consult())
        self._consultation_tasks.add(task)
        task.add_done_callback(_advance_consult_state)

    def receive_cat_opinion(
        self,
        shared: dict[str, Any],
        opinion: str,
        *,
        scene_id: str = "",
        reason: str = "",
    ) -> dict[str, Any] | None:
        """Public entry point so the plugin's inbound handler can route a cat
        reply into ``shared['cat_opinions']``. Idempotent on empty input."""
        opinion_state = {"cat_opinions": self._cat_opinion_snapshot(shared)}
        record = inject_cat_opinion(
            opinion_state, opinion=opinion, scene_id=scene_id, reason=reason
        )
        merged_opinions = json_copy(opinion_state.get("cat_opinions") or [])
        self._cat_opinions = merged_opinions
        shared["cat_opinions"] = merged_opinions
        return record.to_dict() if record is not None else None
