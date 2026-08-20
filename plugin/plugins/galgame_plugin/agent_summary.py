from __future__ import annotations

import hashlib

from .agent_shared import *  # noqa: F401,F403
from .agent_prompt import _context_line_count


class AgentSummaryMixin:
    def _remember_scene_capsule_source_alias(
        self,
        source_identity: str,
        boundary_key: str,
    ) -> None:
        normalized_source_identity = str(source_identity or "").strip()
        normalized_boundary_key = str(boundary_key or "").strip()
        if not normalized_source_identity or not normalized_boundary_key:
            return
        aliases = self._scene_capsule_source_aliases
        aliases.pop(normalized_source_identity, None)
        aliases[normalized_source_identity] = normalized_boundary_key
        while len(aliases) > self._SCENE_CAPSULE_SOURCE_ALIAS_LIMIT:
            aliases.pop(next(iter(aliases)), None)

    @property
    def _summary_seen_line_keys(self) -> set[str]:
        return self._scene_tracker.summary_seen_line_keys

    @_summary_seen_line_keys.setter
    def _summary_seen_line_keys(self, value: set[str]) -> None:
        self._scene_tracker.summary_seen_line_keys = value
        self._scene_tracker._summary_seen_line_key_order = list(value or set())
        scene_id = self._scene_tracker.summary_scene_id
        if scene_id:
            state = self._scene_tracker.state_for_scene(
                scene_id,
                route_id=self._scene_tracker.summary_route_id,
            )
            state["seen_line_keys"] = set(value or set())
            state["seen_line_key_order"] = list(value or set())

    @property
    def _summary_lines_since_push(self) -> int:
        return self._scene_tracker.summary_lines_since_push

    @_summary_lines_since_push.setter
    def _summary_lines_since_push(self, value: int) -> None:
        normalized = int(value)
        self._scene_tracker.summary_lines_since_push = normalized
        scene_id = self._scene_tracker.summary_scene_id
        if scene_id:
            state = self._scene_tracker.state_for_scene(
                scene_id,
                route_id=self._scene_tracker.summary_route_id,
            )
            state["lines_since_push"] = normalized

    @property
    def _summary_scene_id(self) -> str:
        return self._scene_tracker.summary_scene_id

    @_summary_scene_id.setter
    def _summary_scene_id(self, value: str) -> None:
        self._scene_tracker.sync_current_scene_summary_mirror(
            str(value or ""),
            route_id=self._scene_tracker.summary_route_id,
        )

    @staticmethod
    def _summary_delivery_key(
        *,
        scene_id: str,
        route_id: str = "",
        scheduled_seq: int = 0,
        last_line_seq: int = 0,
        stable_line_count: int = 0,
        last_line_occurrence_key: str = "",
    ) -> str:
        normalized_scene_id = str(scene_id or "").strip()
        if not normalized_scene_id:
            return ""
        normalized_route_id = str(route_id or "").strip()
        route_component = ""
        if normalized_route_id:
            route_digest = hashlib.sha256(
                normalized_route_id.encode("utf-8")
            ).hexdigest()[:16]
            route_component = f":route:{route_digest}"
        scope = f"{normalized_scene_id}{route_component}"
        normalized_seq = int(scheduled_seq or 0)
        normalized_occurrence_key = str(last_line_occurrence_key or "").strip()
        if normalized_seq > 0:
            if normalized_occurrence_key:
                occurrence_digest = hashlib.sha256(
                    normalized_occurrence_key.encode("utf-8")
                ).hexdigest()[:16]
                return f"{scope}:{normalized_seq}:occ:{occurrence_digest}"
            return f"{scope}:{normalized_seq}"
        if normalized_occurrence_key:
            occurrence_digest = hashlib.sha256(
                normalized_occurrence_key.encode("utf-8")
            ).hexdigest()[:16]
            return f"{scope}:occ:{occurrence_digest}:{int(stable_line_count or 0)}"
        return (
            f"{scope}:{int(last_line_seq or 0)}:"
            f"{int(stable_line_count or 0)}"
        )

    @staticmethod
    def _normalize_scene_summary_fingerprint_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip()).casefold()

    def _scene_summary_content_fingerprint(
        self,
        *,
        shared: dict[str, Any],
        snapshot: dict[str, Any] | None = None,
        context: dict[str, Any],
        route_id: str,
    ) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        stable_lines: list[dict[str, str]] = []
        for item in list(context.get("stable_lines") or []):
            if isinstance(item, dict):
                stable_lines.append(
                    {
                        "line_id": self._normalize_scene_summary_fingerprint_text(
                            item.get("line_id")
                        ),
                        "speaker": self._normalize_scene_summary_fingerprint_text(
                            item.get("speaker")
                        ),
                        "text": self._normalize_scene_summary_fingerprint_text(
                            item.get("text")
                        ),
                    }
                )
            else:
                stable_lines.append(
                    {
                        "line_id": "",
                        "speaker": "",
                        "text": self._normalize_scene_summary_fingerprint_text(item),
                    }
                )
        choices: list[dict[str, str]] = []
        raw_choices = [
            *(
                ("selected", item)
                for item in list(context.get("recent_choices") or [])
            ),
            *(
                ("visible", item)
                for item in list((snapshot or {}).get("choices") or [])
            ),
        ]
        for choice_state, item in raw_choices:
            if isinstance(item, dict):
                choices.append(
                    {
                        "choice_state": choice_state,
                        "choice_id": self._normalize_scene_summary_fingerprint_text(
                            item.get("choice_id") or item.get("option_id")
                        ),
                        "text": self._normalize_scene_summary_fingerprint_text(
                            item.get("text") or item.get("label")
                        ),
                    }
                )
            else:
                choices.append(
                    {
                        "choice_state": choice_state,
                        "choice_id": "",
                        "text": self._normalize_scene_summary_fingerprint_text(item),
                    }
                )
        payload = {
            "data_source": self._normalize_scene_summary_fingerprint_text(
                self._current_input_source(shared)
            ),
            "route_id": self._normalize_scene_summary_fingerprint_text(route_id),
            "stable_lines": stable_lines,
            "choices": choices,
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        stable_line_keys = tuple(
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for item in stable_lines
        )
        choice_keys = tuple(
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for item in choices
        )
        return (
            hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            stable_line_keys,
            choice_keys,
        )

    def _scene_summary_delta_content(
        self,
        *,
        context: dict[str, Any],
        snapshot: dict[str, Any] | None = None,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        tuple[str, ...],
        tuple[str, ...],
    ]:
        stable_lines: list[dict[str, Any]] = []
        stable_line_keys: list[str] = []
        seen_line_keys: set[str] = set()
        for item in list(context.get("stable_lines") or []):
            record = dict(item) if isinstance(item, dict) else {"text": str(item or "")}
            normalized_text = self._normalize_scene_summary_fingerprint_text(
                record.get("text")
            )
            if not normalized_text:
                continue
            # Reader-specific line ids and speaker recognition may change during a
            # source handoff. The normalized dialogue text is the stable semantic
            # identity used only for delivered-content delta tracking.
            semantic_key = normalized_text
            if semantic_key in seen_line_keys:
                continue
            seen_line_keys.add(semantic_key)
            stable_line_keys.append(semantic_key)
            stable_lines.append(record)

        choices: list[dict[str, Any]] = []
        choice_keys: list[str] = []
        seen_choice_keys: set[str] = set()
        raw_choices = [
            *(
                ("selected", item)
                for item in list(context.get("recent_choices") or [])
            ),
            *(
                ("visible", item)
                for item in list((snapshot or {}).get("choices") or [])
            ),
        ]
        for choice_state, item in raw_choices:
            record = dict(item) if isinstance(item, dict) else {"text": str(item or "")}
            record.setdefault("choice_state", choice_state)
            normalized_text = self._normalize_scene_summary_fingerprint_text(
                record.get("text") or record.get("label")
            )
            identity = normalized_text or self._normalize_scene_summary_fingerprint_text(
                record.get("choice_id") or record.get("option_id")
            )
            semantic_key = f"{choice_state}:{identity}" if identity else ""
            if not semantic_key or semantic_key in seen_choice_keys:
                continue
            seen_choice_keys.add(semantic_key)
            choice_keys.append(semantic_key)
            choices.append(record)
        return stable_lines, choices, tuple(stable_line_keys), tuple(choice_keys)

    @staticmethod
    def _scene_summary_coalesce_key(
        *,
        trusted_history_token: str,
        session_id: str,
    ) -> str:
        boundary = str(trusted_history_token or session_id or "").strip()
        if not boundary:
            return ""
        digest = hashlib.sha256(boundary.encode("utf-8")).hexdigest()[:16]
        return f"galgame:scene_summary:{digest}"

    def _scene_capsule_boundary_key(
        self,
        shared: dict[str, Any],
        *,
        session_id: str,
    ) -> str:
        return self._scene_capsule_boundary_key_from_fingerprint(
            self._session_fingerprint(shared),
            fallback_identity=(
                self._trusted_history_token(shared) or str(session_id or "").strip()
            ),
        )

    def _scene_capsule_boundary_key_from_fingerprint(
        self,
        fingerprint: dict[str, Any],
        *,
        fallback_identity: str = "",
    ) -> str:
        source_identity = self._scene_capsule_source_identity_from_fingerprint(
            fingerprint
        )
        source_aliases = getattr(self, "_scene_capsule_source_aliases", None)
        if source_identity and isinstance(source_aliases, dict):
            aliased_boundary = str(source_aliases.get(source_identity) or "")
            if aliased_boundary:
                return aliased_boundary
        game_id = self._normalize_scene_summary_fingerprint_text(
            fingerprint.get("active_game_id")
        )
        if game_id:
            identity = f"game:{game_id}"
        else:
            process_name = self._normalize_scene_summary_fingerprint_text(
                fingerprint.get("process_name")
            )
            window_title = self._normalize_scene_summary_fingerprint_text(
                fingerprint.get("window_title")
            )
            pid = int(fingerprint.get("pid") or 0)
            hwnd = int(fingerprint.get("target_hwnd") or 0)
            if process_name:
                identity = f"process:{process_name}"
            elif window_title:
                identity = f"window:{window_title}"
            elif pid:
                identity = f"pid:{pid}"
            elif hwnd:
                identity = f"hwnd:{hwnd}"
            else:
                identity = str(fallback_identity or "").strip()
        if not identity:
            return ""
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _scene_capsule_source_identity_from_fingerprint(
        fingerprint: dict[str, Any],
    ) -> str:
        data_source = str(fingerprint.get("active_data_source") or "").strip()
        game_id = str(fingerprint.get("active_game_id") or "").strip().casefold()
        session_id = str(fingerprint.get("active_session_id") or "").strip()
        if not data_source or not session_id:
            return ""
        return (
            f"{data_source}|{game_id}|{session_id}"
            if game_id
            else f"{data_source}|{session_id}"
        )

    def _remember_trusted_scene_source_handoff(
        self,
        previous_fingerprint: dict[str, Any],
        current_fingerprint: dict[str, Any],
    ) -> None:
        previous_source_identity = (
            self._scene_capsule_source_identity_from_fingerprint(
                previous_fingerprint
            )
        )
        current_source_identity = self._scene_capsule_source_identity_from_fingerprint(
            current_fingerprint
        )
        if not previous_source_identity or not current_source_identity:
            return
        previous_boundary = self._scene_capsule_boundary_key_from_fingerprint(
            previous_fingerprint,
            fallback_identity=self._trusted_history_token_from_fingerprint(
                previous_fingerprint
            ),
        )
        if previous_boundary:
            self._remember_scene_capsule_source_alias(
                current_source_identity,
                previous_boundary,
            )

    @staticmethod
    def _scene_capsule_semantic_digest(payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]

    def _build_scene_capsule_input_marker(
        self,
        shared: dict[str, Any],
        *,
        snapshot: dict[str, Any],
        boundary_key: str,
        scene_id: str,
        route_id: str,
    ) -> str:
        marker_events: list[dict[str, Any]] = []
        relevant_types = {
            "line_observed",
            "line_changed",
            "choices_shown",
            "choice_selected",
        }
        for event in list(shared.get("history_events") or [])[-256:]:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")
            if event_type not in relevant_types:
                continue
            payload = event.get("payload")
            payload_obj = payload if isinstance(payload, dict) else {}
            try:
                seq = int(event.get("seq") or 0)
            except (TypeError, ValueError):
                seq = 0
            stability = str(payload_obj.get("stability") or "").strip().lower()
            if event_type in {"line_observed", "line_changed"}:
                semantic_payload: dict[str, Any] = {
                    "speaker": str(payload_obj.get("speaker") or ""),
                    "text": str(payload_obj.get("text") or ""),
                    "line_id": str(
                        payload_obj.get("line_id") or event.get("line_id") or ""
                    ),
                    "scene_id": str(
                        payload_obj.get("scene_id") or event.get("scene_id") or scene_id
                    ),
                    "route_id": str(
                        payload_obj.get("route_id") or event.get("route_id") or route_id
                    ),
                }
            else:
                raw_choices = payload_obj.get("choices")
                if not isinstance(raw_choices, list):
                    selected = payload_obj.get("choice")
                    raw_choices = [
                        selected if isinstance(selected, dict) else payload_obj
                    ]
                semantic_choices: list[dict[str, Any]] = []
                for item in raw_choices:
                    if not isinstance(item, dict):
                        continue
                    semantic_choices.append(
                        {
                            "choice_id": str(
                                item.get("choice_id") or item.get("option_id") or ""
                            ),
                            "text": str(item.get("text") or item.get("label") or ""),
                            "scene_id": str(
                                item.get("scene_id")
                                or payload_obj.get("scene_id")
                                or scene_id
                            ),
                            "route_id": str(
                                item.get("route_id")
                                or payload_obj.get("route_id")
                                or route_id
                            ),
                        }
                    )
                semantic_payload = {"choices": semantic_choices}
            marker_events.append(
                {
                    "type": event_type,
                    "seq": seq,
                    "stability": stability,
                    "semantic": self._scene_capsule_semantic_digest(semantic_payload),
                }
            )

        fallback_ids = self._scene_capsule_fallback_occurrence_ids(
            source_key=f"marker:{boundary_key}",
            signatures=[
                json.dumps(item, sort_keys=True, separators=(",", ":"))
                for item in marker_events
            ],
        )
        latest_by_type: dict[str, dict[str, Any]] = {}
        for item, fallback_id in zip(marker_events, fallback_ids, strict=False):
            marker_item = dict(item)
            if int(marker_item.get("seq") or 0) <= 0:
                marker_item["seq"] = f"occurrence:{fallback_id}"
            latest_by_type[str(marker_item.get("type") or "")] = marker_item
        marker_event_state = getattr(self, "_scene_capsule_marker_event_state", None)
        if not isinstance(marker_event_state, dict):
            marker_event_state = {}
            self._scene_capsule_marker_event_state = marker_event_state
        try:
            stream_last_seq = max(0, int(shared.get("last_seq") or 0))
        except (TypeError, ValueError):
            stream_last_seq = 0
        previous_stream_high_water = max(
            (
                int(item.get("seq") or 0)
                for item in marker_event_state.values()
                if isinstance(item, dict) and isinstance(item.get("seq"), int)
            ),
            default=0,
        )
        if stream_last_seq > 0 and previous_stream_high_water > stream_last_seq:
            # Bridge streams may restart at seq=1 without changing session_id.
            # Their old per-type high-water marks must not hide the first new
            # tentative observation, which retires any stale capsule retry.
            marker_event_state.clear()
        for event_type, marker_item in latest_by_type.items():
            previous_item = marker_event_state.get(event_type)
            previous_seq = (
                int(previous_item.get("seq") or 0)
                if isinstance(previous_item, dict)
                and isinstance(previous_item.get("seq"), int)
                else 0
            )
            current_seq = (
                int(marker_item.get("seq") or 0)
                if isinstance(marker_item.get("seq"), int)
                else 0
            )
            if previous_seq > 0 and current_seq < previous_seq:
                continue
            marker_event_state[event_type] = marker_item

        save_context = snapshot.get("save_context")
        save_obj = save_context if isinstance(save_context, dict) else {}
        save_kind = str(save_obj.get("kind") or "").strip().lower()
        save_marker: dict[str, Any] = {}
        if save_kind in {"load", "rollback"}:
            save_marker = {
                "kind": save_kind,
                "identity": self._scene_capsule_semantic_digest(
                    {
                        "slot_id": str(save_obj.get("slot_id") or ""),
                        "save_id": str(save_obj.get("save_id") or ""),
                        "checkpoint_id": str(save_obj.get("checkpoint_id") or ""),
                    }
                ),
            }
        return self._scene_capsule_semantic_digest(
            {
                "boundary": boundary_key,
                "scene_id": scene_id,
                "route_id": route_id,
                "save_context": save_marker,
                "events": [
                    marker_event_state[key] for key in sorted(marker_event_state)
                ],
            }
        )

    @staticmethod
    def _scene_capsule_coalesce_key(boundary_key: str) -> str:
        return f"galgame:scene_delta:{boundary_key}" if boundary_key else ""

    @staticmethod
    def _scene_capsule_handoff_overlap_end(
        previous_tail: list[str],
        current_texts: list[str],
    ) -> int:
        for candidate in range(min(len(previous_tail), len(current_texts)), 0, -1):
            if previous_tail[-candidate:] == current_texts[:candidate]:
                return candidate
        if len(previous_tail) < 2:
            return 0
        full_tail_matches = [
            start + len(previous_tail)
            for start in range(len(current_texts) - len(previous_tail) + 1)
            if current_texts[start : start + len(previous_tail)] == previous_tail
        ]
        return full_tail_matches[0] if len(full_tail_matches) == 1 else 0

    @staticmethod
    def _scene_capsule_event_key(*parts: Any) -> str:
        raw = "|".join(str(part or "") for part in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _scene_route_scope_key(cls, *, scene_id: str, route_id: str) -> str:
        return cls._scene_capsule_event_key(
            "scene_route_scope",
            str(scene_id or ""),
            str(route_id or ""),
        )

    @staticmethod
    def _scene_capsule_event_identity_ids(
        history_events: list[Any],
        *,
        field_name: str,
    ) -> tuple[list[str], int]:
        """Resolve one event-time identity without borrowing from its future."""
        identity_ids: list[str] = []
        active_identity_id = ""
        last_explicit_identity_index = -1
        for event_index, event in enumerate(history_events):
            if not isinstance(event, dict):
                identity_ids.append(active_identity_id)
                continue
            payload = event.get("payload")
            payload_obj = payload if isinstance(payload, dict) else {}
            explicit_identity_id = str(
                payload_obj.get(field_name) or event.get(field_name) or ""
            )
            if explicit_identity_id:
                active_identity_id = explicit_identity_id
                last_explicit_identity_index = event_index
            identity_ids.append(active_identity_id)
        return identity_ids, last_explicit_identity_index

    @classmethod
    def _scene_capsule_event_route_ids(
        cls,
        history_events: list[Any],
    ) -> tuple[list[str], int]:
        return cls._scene_capsule_event_identity_ids(
            history_events,
            field_name="route_id",
        )

    @classmethod
    def _scene_capsule_event_scene_ids(
        cls,
        history_events: list[Any],
    ) -> tuple[list[str], int]:
        return cls._scene_capsule_event_identity_ids(
            history_events,
            field_name="scene_id",
        )

    @staticmethod
    def _scene_capsule_choice_handoff_signature(
        *,
        event_type: str,
        route_id: str,
        choices: list[dict[str, Any]],
    ) -> str:
        return json.dumps(
            {
                "type": str(event_type or ""),
                # Reader and OCR scene identifiers are source-local during a
                # trusted handoff.  Route remains an exact semantic boundary.
                "route_id": str(route_id or ""),
                "choices": [
                    {
                        # Reader-generated IDs are source-local (mem:/ocr:).
                        # Trusted handoff matching is semantic and positional.
                        "text": str(
                            item.get("text") or item.get("label") or ""
                        ).strip(),
                        "index": index,
                    }
                    for index, item in enumerate(choices)
                    if isinstance(item, dict)
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _scene_capsule_fallback_occurrence_ids(
        self,
        *,
        source_key: str,
        signatures: list[str],
    ) -> list[int]:
        state = self._scene_capsule_fallback_occurrences.pop(
            source_key,
            None,
        )
        had_previous_state = isinstance(state, dict)
        if not had_previous_state:
            state = {"signatures": [], "occurrence_ids": [], "next_id": 1}
        self._scene_capsule_fallback_occurrences[source_key] = state
        while len(self._scene_capsule_fallback_occurrences) > 32:
            oldest_source_key = next(iter(self._scene_capsule_fallback_occurrences))
            self._scene_capsule_fallback_occurrences.pop(oldest_source_key, None)
            self._scene_capsule_line_fallback_aliases.pop(oldest_source_key, None)
        previous_signatures = [
            str(item) for item in list(state.get("signatures") or [])
        ]
        previous_ids = [int(item) for item in list(state.get("occurrence_ids") or [])]
        previous_next_id = max(1, int(state.get("next_id") or 1))
        state["previous_observation_had_state"] = had_previous_state
        state["previous_observation_high_water"] = max(
            [*previous_ids, previous_next_id - 1],
            default=0,
        )
        overlap_count = 0
        for candidate in range(
            min(len(previous_signatures), len(signatures)),
            0,
            -1,
        ):
            if previous_signatures[-candidate:] == signatures[:candidate]:
                overlap_count = candidate
                break
        occurrence_ids = previous_ids[-overlap_count:] if overlap_count else []
        next_id = previous_next_id
        for _ in signatures[overlap_count:]:
            occurrence_ids.append(next_id)
            next_id += 1
        state["signatures"] = list(signatures)
        state["occurrence_ids"] = list(occurrence_ids)
        state["next_id"] = next_id
        return occurrence_ids

    def _scene_capsule_sequence_less_event_ids(
        self,
        *,
        source_key: str,
        event_indices: list[int],
        signatures: list[str],
    ) -> dict[int, int]:
        return dict(
            zip(
                event_indices,
                self._scene_capsule_fallback_occurrence_ids(
                    source_key=source_key,
                    signatures=signatures,
                ),
                strict=False,
            )
        )

    @staticmethod
    def _scene_capsule_occurrence_order(
        occurrence: dict[str, Any],
        *,
        fallback_index: int = 0,
    ) -> tuple[int, str, int, int, int, int]:
        record = occurrence.get("line") or occurrence.get("choice") or {}
        if not isinstance(record, dict):
            record = {}
        ts = str(occurrence.get("ts") or record.get("ts") or "")
        try:
            event_index = int(occurrence.get("history_event_index"))
        except (TypeError, ValueError):
            event_index = -1
        try:
            seq = max(0, int(occurrence.get("seq") or 0))
        except (TypeError, ValueError):
            seq = 0
        try:
            fallback_occurrence_id = max(
                0,
                int(occurrence.get("fallback_occurrence_id") or 0),
            )
        except (TypeError, ValueError):
            fallback_occurrence_id = 0
        return (
            int(bool(ts)),
            ts,
            event_index,
            seq,
            fallback_occurrence_id,
            fallback_index,
        )

    def _scene_capsule_line_occurrences(
        self,
        shared: dict[str, Any],
        *,
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        data_source = self._current_input_source(shared)
        session_id = str(shared.get("active_session_id") or "")
        event_occurrences: list[dict[str, Any]] = []
        history_events = list(shared.get("history_events") or [])
        event_scene_ids, last_explicit_scene_index = (
            self._scene_capsule_event_scene_ids(history_events)
        )
        event_route_ids, last_explicit_route_index = (
            self._scene_capsule_event_route_ids(history_events)
        )
        valid_line_event_candidates: list[
            tuple[int, dict[str, Any], dict[str, Any], str]
        ] = []
        for event_index, event in enumerate(history_events):
            if (
                not isinstance(event, dict)
                or str(event.get("type") or "") != "line_changed"
            ):
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            stability = str(payload.get("stability") or "").strip().lower()
            if stability and stability != "stable":
                continue
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            valid_line_event_candidates.append((event_index, event, payload, text))

        sequence_less_line_event_indices: list[int] = []
        sequence_less_line_event_signatures: list[str] = []
        for event_index, event, payload, text in valid_line_event_candidates:
            try:
                candidate_seq = int(event.get("seq") or 0)
            except (TypeError, ValueError):
                candidate_seq = 0
            if candidate_seq > 0:
                continue
            sequence_less_line_event_indices.append(event_index)
            sequence_less_line_event_signatures.append(
                json.dumps(
                    {
                        "session_id": str(event.get("session_id") or session_id),
                        "ts": str(event.get("ts") or payload.get("ts") or ""),
                        "line_id": str(
                            payload.get("line_id") or event.get("line_id") or ""
                        ),
                        "speaker": str(payload.get("speaker") or ""),
                        "text": text,
                        "scene_id": str(
                            payload.get("scene_id") or event.get("scene_id") or ""
                        ),
                        "route_id": str(
                            payload.get("route_id") or event.get("route_id") or ""
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        sequence_less_line_event_ids = self._scene_capsule_sequence_less_event_ids(
            source_key=f"{data_source}|{session_id}|history_event:line_changed",
            event_indices=sequence_less_line_event_indices,
            signatures=sequence_less_line_event_signatures,
        )
        for event_index, event, payload, text in valid_line_event_candidates:
            line = {
                "line_id": str(payload.get("line_id") or event.get("line_id") or ""),
                "speaker": str(payload.get("speaker") or ""),
                "text": text,
                "scene_id": str(
                    payload.get("scene_id")
                    or event.get("scene_id")
                    or event_scene_ids[event_index]
                    or (
                        snapshot.get("scene_id")
                        if last_explicit_scene_index < event_index
                        else ""
                    )
                    or ""
                ),
                "route_id": str(
                    payload.get("route_id")
                    or event.get("route_id")
                    or event_route_ids[event_index]
                    or (
                        snapshot.get("route_id")
                        if last_explicit_route_index < event_index
                        else ""
                    )
                    or ""
                ),
                "ts": str(event.get("ts") or payload.get("ts") or ""),
                "stability": "stable",
            }
            signature = json.dumps(
                {
                    "line_id": line["line_id"],
                    "speaker": line["speaker"],
                    "text": line["text"],
                    "scene_id": line["scene_id"],
                    "route_id": line["route_id"],
                    "ts": line["ts"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            try:
                seq = int(event.get("seq") or 0)
            except (TypeError, ValueError):
                seq = 0
            event_session = str(event.get("session_id") or session_id)
            semantic_version = hashlib.sha256(
                signature.encode("utf-8")
            ).hexdigest()[:16]
            event_key = self._scene_capsule_event_key(
                data_source,
                event_session,
                "line_changed",
                (
                    seq
                    if seq > 0
                    else f"occurrence:{sequence_less_line_event_ids[event_index]}"
                ),
                semantic_version,
            )
            event_occurrences.append(
                {
                    "event_key": event_key,
                    "seq": seq,
                    "history_event_index": event_index,
                    "line": line,
                    "signature": signature,
                }
            )

        event_routes_by_line_signature: dict[str, list[str]] = {}
        event_scopes_by_line_identity: dict[str, list[tuple[str, str]]] = {}
        for occurrence in event_occurrences:
            event_line = occurrence.get("line") or {}
            if not isinstance(event_line, dict):
                continue
            route_agnostic_signature = json.dumps(
                {
                    "line_id": str(event_line.get("line_id") or ""),
                    "speaker": str(event_line.get("speaker") or ""),
                    "text": str(event_line.get("text") or ""),
                    "scene_id": str(event_line.get("scene_id") or ""),
                    "ts": str(event_line.get("ts") or ""),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            event_routes_by_line_signature.setdefault(
                route_agnostic_signature,
                [],
            ).append(str(event_line.get("route_id") or ""))
            scope_agnostic_identity = json.dumps(
                {
                    "line_id": str(event_line.get("line_id") or ""),
                    "speaker": str(event_line.get("speaker") or ""),
                    "text": str(event_line.get("text") or ""),
                    "ts": str(event_line.get("ts") or ""),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            event_scopes_by_line_identity.setdefault(
                scope_agnostic_identity,
                [],
            ).append(
                (
                    str(event_line.get("scene_id") or ""),
                    str(event_line.get("route_id") or ""),
                )
            )

        fallback_occurrences: list[dict[str, Any]] = []
        history_records: list[tuple[dict[str, Any], str, str, bool]] = []
        consumed_event_routes: dict[str, int] = {}
        consumed_event_scopes: dict[str, int] = {}
        for item in list(shared.get("history_lines") or []):
            if not isinstance(item, dict):
                continue
            stability = str(item.get("stability") or "").strip().lower()
            if stability and stability != "stable":
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            line = dict(item)
            line["text"] = text
            scene_was_missing = not str(line.get("scene_id") or "").strip()
            route_was_missing = not str(line.get("route_id") or "").strip()
            scope_was_incomplete = scene_was_missing or route_was_missing
            matched_event_scope: tuple[str, str] | None = None
            if not str(line.get("scene_id") or "").strip():
                scope_agnostic_identity = json.dumps(
                    {
                        "line_id": str(line.get("line_id") or ""),
                        "speaker": str(line.get("speaker") or ""),
                        "text": text,
                        "ts": str(line.get("ts") or ""),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                consumed_scope_count = consumed_event_scopes.get(
                    scope_agnostic_identity,
                    0,
                )
                matching_scopes = event_scopes_by_line_identity.get(
                    scope_agnostic_identity
                ) or []
                if consumed_scope_count < len(matching_scopes):
                    matched_event_scope = matching_scopes[consumed_scope_count]
                    consumed_event_scopes[scope_agnostic_identity] = (
                        consumed_scope_count + 1
                    )
                line["scene_id"] = str(
                    (matched_event_scope or ("", ""))[0]
                    or snapshot.get("scene_id")
                    or ""
                )
            else:
                line["scene_id"] = str(line.get("scene_id") or "")
            if not str(line.get("route_id") or "").strip():
                if matched_event_scope is not None:
                    line["route_id"] = matched_event_scope[1]
                else:
                    route_agnostic_signature = json.dumps(
                        {
                            "line_id": str(line.get("line_id") or ""),
                            "speaker": str(line.get("speaker") or ""),
                            "text": text,
                            "scene_id": str(line.get("scene_id") or ""),
                            "ts": str(line.get("ts") or ""),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    consumed = consumed_event_routes.get(
                        route_agnostic_signature,
                        0,
                    )
                    matching_routes = event_routes_by_line_signature.get(
                        route_agnostic_signature
                    ) or []
                    if consumed < len(matching_routes):
                        line["route_id"] = matching_routes[consumed]
                        consumed_event_routes[route_agnostic_signature] = consumed + 1
                    else:
                        line["route_id"] = ""
            else:
                line["route_id"] = str(line.get("route_id") or "")
            line.setdefault("stability", "stable")
            signature = json.dumps(
                {
                    "line_id": str(line.get("line_id") or ""),
                    "speaker": str(line.get("speaker") or ""),
                    "text": text,
                    "scene_id": str(line.get("scene_id") or ""),
                    "route_id": str(line.get("route_id") or ""),
                    "ts": str(line.get("ts") or ""),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            fallback_identity = json.dumps(
                {
                    "line_id": str(line.get("line_id") or ""),
                    "speaker": str(line.get("speaker") or ""),
                    "text": text,
                    "ts": str(line.get("ts") or ""),
                    **(
                        {}
                        if scene_was_missing
                        else {"scene_id": str(line.get("scene_id") or "")}
                    ),
                    **(
                        {}
                        if route_was_missing
                        else {"route_id": str(line.get("route_id") or "")}
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            history_records.append(
                (line, signature, fallback_identity, scope_was_incomplete)
            )
        fallback_source_key = f"{data_source}|{session_id}|history_line"
        fallback_ids = self._scene_capsule_fallback_occurrence_ids(
            source_key=fallback_source_key,
            signatures=[
                fallback_identity
                for _line, _signature, fallback_identity, _missing in history_records
            ],
        )
        event_keys_by_signature: dict[str, list[str]] = {}
        for occurrence in event_occurrences:
            event_keys_by_signature.setdefault(
                str(occurrence.get("signature") or ""),
                [],
            ).append(str(occurrence.get("event_key") or ""))
        consumed_event_signatures: dict[str, int] = {}
        fallback_aliases = getattr(self, "_scene_capsule_line_fallback_aliases", None)
        if not isinstance(fallback_aliases, dict):
            fallback_aliases = {}
            self._scene_capsule_line_fallback_aliases = fallback_aliases
        source_aliases = fallback_aliases.setdefault(fallback_source_key, {})
        fallback_state = self._scene_capsule_fallback_occurrences.get(
            fallback_source_key
        )
        source_scopes: dict[int, dict[str, str]] = {}
        if isinstance(fallback_state, dict):
            raw_scopes = fallback_state.setdefault("scopes", {})
            if isinstance(raw_scopes, dict):
                source_scopes = raw_scopes
        for (
            line,
            signature,
            _fallback_identity,
            scope_was_incomplete,
        ), occurrence_id in zip(history_records, fallback_ids, strict=False):
            consumed = consumed_event_signatures.get(signature, 0)
            matching_event_keys = event_keys_by_signature.get(signature) or []
            if consumed < len(matching_event_keys):
                source_aliases[occurrence_id] = matching_event_keys[consumed]
                if scope_was_incomplete:
                    source_scopes[occurrence_id] = {
                        "scene_id": str(line.get("scene_id") or ""),
                        "route_id": str(line.get("route_id") or ""),
                    }
                consumed_event_signatures[signature] = consumed + 1
                continue
            if scope_was_incomplete:
                saved_scope = source_scopes.get(occurrence_id)
                if not isinstance(saved_scope, dict):
                    saved_scope = {
                        "scene_id": str(line.get("scene_id") or ""),
                        "route_id": str(line.get("route_id") or ""),
                    }
                    source_scopes[occurrence_id] = saved_scope
                line["scene_id"] = str(saved_scope.get("scene_id") or "")
                line["route_id"] = str(saved_scope.get("route_id") or "")
            aliased_event_key = str(source_aliases.get(occurrence_id) or "")
            fallback_occurrences.append(
                {
                    "event_key": (
                        aliased_event_key
                        or self._scene_capsule_event_key(
                            data_source,
                            session_id,
                            "history_line",
                            str(line.get("scene_id") or ""),
                            str(line.get("route_id") or ""),
                            occurrence_id,
                        )
                    ),
                    "seq": 0,
                    "fallback_source_key": fallback_source_key,
                    "fallback_occurrence_id": occurrence_id,
                    "line": line,
                    "signature": signature,
                }
            )
        active_occurrence_ids = set(fallback_ids)
        for occurrence_id in list(source_aliases):
            if occurrence_id not in active_occurrence_ids:
                source_aliases.pop(occurrence_id, None)
        for occurrence_id in list(source_scopes):
            if occurrence_id not in active_occurrence_ids:
                source_scopes.pop(occurrence_id, None)
        return [*event_occurrences, *fallback_occurrences]

    def _scene_capsule_choice_occurrences(
        self,
        shared: dict[str, Any],
        *,
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        data_source = self._current_input_source(shared)
        session_id = str(shared.get("active_session_id") or "")
        scene_id = str(snapshot.get("scene_id") or "")
        occurrences: list[dict[str, Any]] = []
        visible_event_menu_signatures: set[str] = set()
        history_events = list(shared.get("history_events") or [])
        event_scene_ids, last_explicit_scene_index = (
            self._scene_capsule_event_scene_ids(history_events)
        )
        event_route_ids, last_explicit_route_index = (
            self._scene_capsule_event_route_ids(history_events)
        )
        valid_choice_event_candidates: list[
            tuple[
                int,
                dict[str, Any],
                str,
                dict[str, Any],
                list[tuple[int, dict[str, Any], Any]],
            ]
        ] = []
        for event_index, event in enumerate(history_events):
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")
            if event_type not in {"choices_shown", "choice_selected"}:
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            raw_choices = payload.get("choices") if event_type == "choices_shown" else None
            if event_type == "choice_selected":
                selected = payload.get("choice")
                selected_choice = dict(selected) if isinstance(selected, dict) else {}
                if not str(
                    selected_choice.get("text") or selected_choice.get("label") or ""
                ).strip():
                    selected_choice["text"] = str(payload.get("choice_text") or "")
                payload_choice_index = payload.get("choice_index")
                if (
                    selected_choice.get("index") is None
                    and payload_choice_index is not None
                ):
                    selected_choice["index"] = payload_choice_index
                if not str(selected_choice.get("choice_id") or ""):
                    selected_choice["choice_id"] = str(payload.get("choice_id") or "")
                raw_choices = [selected_choice]
            elif not isinstance(raw_choices, list):
                selected = payload.get("choice")
                raw_choices = [selected if isinstance(selected, dict) else payload]
            valid_choices: list[tuple[int, dict[str, Any], Any]] = []
            for choice_index, item in enumerate(raw_choices):
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or item.get("label") or "").strip()
                if not text:
                    continue
                choice = dict(item)
                choice["text"] = text
                raw_index = choice.get("index")
                normalized_index = choice_index if raw_index is None else raw_index
                if raw_index is None:
                    choice.pop("index", None)
                valid_choices.append((choice_index, choice, normalized_index))
            if not valid_choices:
                continue
            valid_choice_event_candidates.append(
                (event_index, event, event_type, payload, valid_choices)
            )

        sequence_less_choice_event_indices: list[int] = []
        sequence_less_choice_event_signatures: list[str] = []
        for (
            event_index,
            event,
            event_type,
            payload,
            valid_choices,
        ) in valid_choice_event_candidates:
            try:
                candidate_seq = int(event.get("seq") or 0)
            except (TypeError, ValueError):
                candidate_seq = 0
            if candidate_seq > 0:
                continue
            sequence_less_choice_event_indices.append(event_index)
            sequence_less_choice_event_signatures.append(
                json.dumps(
                    {
                        "session_id": str(event.get("session_id") or session_id),
                        "type": event_type,
                        "ts": str(event.get("ts") or payload.get("ts") or ""),
                        "scene_id": str(
                            payload.get("scene_id") or event.get("scene_id") or ""
                        ),
                        "route_id": str(
                            payload.get("route_id") or event.get("route_id") or ""
                        ),
                        "choices": [
                            {
                                "choice_id": str(
                                    choice.get("choice_id")
                                    or choice.get("option_id")
                                    or ""
                                ),
                                "text": str(choice.get("text") or ""),
                                "index": normalized_index,
                            }
                            for _choice_index, choice, normalized_index in valid_choices
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        sequence_less_choice_event_ids = self._scene_capsule_sequence_less_event_ids(
            source_key=f"{data_source}|{session_id}|history_event:choice",
            event_indices=sequence_less_choice_event_indices,
            signatures=sequence_less_choice_event_signatures,
        )
        for (
            event_index,
            event,
            event_type,
            payload,
            valid_choices,
        ) in valid_choice_event_candidates:
            try:
                seq = int(event.get("seq") or 0)
            except (TypeError, ValueError):
                seq = 0
            event_session_id = str(event.get("session_id") or session_id)
            event_occurrence = (
                seq
                if seq > 0
                else f"occurrence:{sequence_less_choice_event_ids[event_index]}"
            )
            event_route_id = str(
                payload.get("route_id")
                or event.get("route_id")
                or event_route_ids[event_index]
                or (
                    snapshot.get("route_id")
                    if last_explicit_route_index < event_index
                    else ""
                )
                or ""
            )
            event_scene_id = str(
                payload.get("scene_id")
                or event.get("scene_id")
                or event_scene_ids[event_index]
                or (
                    snapshot.get("scene_id")
                    if last_explicit_scene_index < event_index
                    else ""
                )
                or ""
            )
            if event_type == "choices_shown":
                visible_event_menu_signatures.add(
                    json.dumps(
                        {
                            "scene_id": event_scene_id,
                            "route_id": event_route_id,
                            "choices": [
                                str(choice.get("text") or "").strip()
                                for _choice_index, choice, _normalized_index in valid_choices
                            ],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            event_group_signature = json.dumps(
                {
                    "type": event_type,
                    "scene_id": event_scene_id,
                    "route_id": event_route_id,
                    "choices": [
                        {
                            "choice_id": str(
                                choice.get("choice_id") or choice.get("option_id") or ""
                            ),
                            "text": str(choice.get("text") or "").strip(),
                            "index": normalized_index,
                        }
                        for _choice_index, choice, normalized_index in valid_choices
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            event_semantic_version = hashlib.sha256(
                event_group_signature.encode("utf-8")
            ).hexdigest()[:16]
            event_group_key = self._scene_capsule_event_key(
                data_source,
                event_session_id,
                event_type,
                event_occurrence,
                "choice_event_group",
                event_semantic_version,
            )
            handoff_group_signature = self._scene_capsule_choice_handoff_signature(
                event_type=event_type,
                route_id=event_route_id,
                choices=[choice for _index, choice, _normalized in valid_choices],
            )
            for choice_index, choice, normalized_index in valid_choices:
                text = str(choice.get("text") or "")
                choice["choice_state"] = (
                    "visible" if event_type == "choices_shown" else "selected"
                )
                choice["scene_id"] = str(
                    choice.get("scene_id") or event_scene_id
                )
                choice["route_id"] = str(
                    choice.get("route_id") or event_route_id
                )
                semantic_version = hashlib.sha256(
                    json.dumps(
                        {
                            "type": event_type,
                            "choice_id": str(
                                choice.get("choice_id") or choice.get("option_id") or ""
                            ),
                            "text": text,
                            "scene_id": str(choice.get("scene_id") or ""),
                            "route_id": str(choice.get("route_id") or ""),
                            "index": normalized_index,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()[:16]
                occurrences.append(
                    {
                        "event_key": self._scene_capsule_event_key(
                            data_source,
                            event_session_id,
                            event_type,
                            event_occurrence,
                            choice_index,
                            semantic_version,
                        ),
                        "event_group_key": event_group_key,
                        "handoff_group_signature": handoff_group_signature,
                        "seq": seq,
                        "history_event_index": event_index,
                        "ts": str(event.get("ts") or payload.get("ts") or ""),
                        "fallback_signature": json.dumps(
                            {
                                "choice_id": str(
                                    choice.get("choice_id")
                                    or choice.get("option_id")
                                    or ""
                                ),
                                "text": text,
                                "state": choice["choice_state"],
                                "ts": str(
                                    choice.get("ts")
                                    or event.get("ts")
                                    or payload.get("ts")
                                    or ""
                                ),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        "fallback_scope": {
                            "scene_id": str(choice.get("scene_id") or ""),
                            "route_id": str(choice.get("route_id") or ""),
                        },
                        "choice": choice,
                    }
                )

        selected_history_choices = [
            item
            for item in list(shared.get("history_choices") or [])
            if not isinstance(item, dict)
            or str(item.get("action") or "selected").strip().lower() == "selected"
        ]
        fallback_choice_sources: list[tuple[str, list[Any]]] = [
            ("selected", selected_history_choices)
        ]
        fallback_choice_sources.append(("visible", list(snapshot.get("choices") or [])))
        for choice_state, items in fallback_choice_sources:
            fallback_choices: list[tuple[dict[str, Any], str, str]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or item.get("label") or "").strip()
                if not text:
                    continue
                choice = dict(item)
                choice["text"] = text
                choice["choice_state"] = choice_state
                choice_scene_id = str(choice.get("scene_id") or scene_id)
                choice_route_id = str(
                    choice.get("route_id")
                    or (
                        snapshot.get("route_id")
                        if choice_state == "visible"
                        else ""
                    )
                    or ""
                )
                choice["scene_id"] = choice_scene_id
                choice["route_id"] = choice_route_id
                occurrence_ts = (
                    str(choice.get("ts") or "")
                    if choice_state == "selected"
                    else ""
                )
                semantic_payload = {
                    "choice_id": str(
                        choice.get("choice_id") or choice.get("option_id") or ""
                    ),
                    "text": text,
                    "state": choice_state,
                    "ts": occurrence_ts,
                }
                if choice_state == "visible":
                    semantic_payload["scene_id"] = choice_scene_id
                    semantic_payload["route_id"] = choice_route_id
                semantic = json.dumps(
                    semantic_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                fallback_choices.append((choice, choice_scene_id, semantic))
            visible_menu_signature = ""
            if choice_state == "visible" and fallback_choices:
                visible_menu_signature = json.dumps(
                    {
                        "scene_id": str(snapshot.get("scene_id") or ""),
                        "route_id": str(snapshot.get("route_id") or ""),
                        "choices": [
                            str(choice.get("text") or "").strip()
                            for choice, _scene, _semantic in fallback_choices
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            fallback_source_key = (
                f"{data_source}|{session_id}|history_choice:{choice_state}"
            )
            fallback_ids = self._scene_capsule_fallback_occurrence_ids(
                source_key=fallback_source_key,
                signatures=[semantic for _choice, _scene, semantic in fallback_choices],
            )
            if visible_menu_signature in visible_event_menu_signatures:
                fallback_state = self._scene_capsule_fallback_occurrences.get(
                    fallback_source_key
                )
                event_occurrence_floor = (
                    int(fallback_state.get("visible_event_occurrence_floor") or 0)
                    if isinstance(fallback_state, dict)
                    else 0
                )
                current_occurrence_floor = max(fallback_ids, default=0)
                if event_occurrence_floor <= 0 and current_occurrence_floor > 0:
                    assert isinstance(fallback_state, dict)
                    fallback_state["visible_event_occurrence_floor"] = (
                        current_occurrence_floor
                    )
                    event_occurrence_floor = current_occurrence_floor
                if current_occurrence_floor <= event_occurrence_floor:
                    continue
            source_aliases: dict[int, str] = {}
            source_scopes: dict[int, dict[str, str]] = {}
            event_occurrences_by_signature: dict[
                str,
                list[dict[str, Any]],
            ] = {}
            consumed_event_signatures: dict[str, int] = {}
            if choice_state == "selected":
                fallback_aliases = self._scene_capsule_line_fallback_aliases
                source_aliases = fallback_aliases.setdefault(
                    fallback_source_key,
                    {},
                )
                fallback_state = self._scene_capsule_fallback_occurrences.get(
                    fallback_source_key
                )
                if isinstance(fallback_state, dict):
                    raw_scopes = fallback_state.setdefault("scopes", {})
                    if isinstance(raw_scopes, dict):
                        source_scopes = raw_scopes
                for occurrence in occurrences:
                    signature = str(occurrence.get("fallback_signature") or "")
                    if signature:
                        event_occurrences_by_signature.setdefault(
                            signature,
                            [],
                        ).append(
                            occurrence
                        )
            visible_group_signature = ""
            if choice_state == "visible" and fallback_choices:
                visible_group_signature = (
                    self._scene_capsule_choice_handoff_signature(
                        event_type="history_choice:visible",
                        route_id=str(snapshot.get("route_id") or ""),
                        choices=[
                            choice for choice, _scene, _semantic in fallback_choices
                        ],
                    )
                )
            for (choice, choice_scene_id, semantic), occurrence_id in zip(
                fallback_choices,
                fallback_ids,
                strict=False,
            ):
                matching_events = event_occurrences_by_signature.get(semantic) or []
                consumed = consumed_event_signatures.get(semantic, 0)
                if consumed < len(matching_events):
                    matched_event = matching_events[consumed]
                    source_aliases[occurrence_id] = str(
                        matched_event.get("event_key") or ""
                    )
                    matched_scope = matched_event.get("fallback_scope")
                    if isinstance(matched_scope, dict):
                        source_scopes[occurrence_id] = {
                            "scene_id": str(matched_scope.get("scene_id") or ""),
                            "route_id": str(matched_scope.get("route_id") or ""),
                        }
                    consumed_event_signatures[semantic] = consumed + 1
                    continue
                if choice_state == "selected":
                    saved_scope = source_scopes.get(occurrence_id)
                    if not isinstance(saved_scope, dict):
                        saved_scope = {
                            "scene_id": choice_scene_id or scene_id,
                            "route_id": (
                                str(choice.get("route_id") or "")
                                or str(snapshot.get("route_id") or "")
                            ),
                        }
                        source_scopes[occurrence_id] = saved_scope
                    choice_scene_id = str(saved_scope.get("scene_id") or "")
                    choice["scene_id"] = choice_scene_id
                    choice["route_id"] = str(saved_scope.get("route_id") or "")
                group_signature = visible_group_signature or (
                    self._scene_capsule_choice_handoff_signature(
                        event_type=f"history_choice:{choice_state}",
                        route_id=str(choice.get("route_id") or ""),
                        choices=[choice],
                    )
                )
                group_occurrence = (
                    group_signature
                    if choice_state == "visible"
                    else f"{group_signature}|occurrence:{occurrence_id}"
                )
                group_key = self._scene_capsule_event_key(
                    data_source,
                    session_id,
                    f"history_choice:{choice_state}:group",
                    group_occurrence,
                )
                occurrences.append(
                    {
                        "event_key": (
                            str(source_aliases.get(occurrence_id) or "")
                            or self._scene_capsule_event_key(
                                data_source,
                                session_id,
                                f"history_choice:{choice_state}",
                                choice_scene_id,
                                str(choice.get("route_id") or ""),
                                occurrence_id,
                            )
                        ),
                        "event_group_key": group_key,
                        "handoff_group_signature": group_signature,
                        "seq": 0,
                        "fallback_source_key": fallback_source_key,
                        "fallback_occurrence_id": occurrence_id,
                        "snapshot_fallback": choice_state == "visible",
                        "ts": str(
                            choice.get("ts")
                            or (snapshot.get("ts") if choice_state == "visible" else "")
                            or ""
                        ),
                        "choice": choice,
                    }
                )
            if choice_state == "selected":
                active_occurrence_ids = set(fallback_ids)
                for occurrence_id in list(source_aliases):
                    if occurrence_id not in active_occurrence_ids:
                        source_aliases.pop(occurrence_id, None)
                for occurrence_id in list(source_scopes):
                    if occurrence_id not in active_occurrence_ids:
                        source_scopes.pop(occurrence_id, None)
        return occurrences

    def _scene_timeline_occurrences_after_save_boundary(
        self,
        shared: dict[str, Any],
        *,
        snapshot: dict[str, Any],
        line_occurrences: list[dict[str, Any]],
        choice_occurrences: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        save_context = snapshot.get("save_context")
        save_obj = save_context if isinstance(save_context, dict) else {}
        save_kind = str(save_obj.get("kind") or "").strip().lower()
        if save_kind not in {"load", "rollback"}:
            return line_occurrences, choice_occurrences, False

        boundary_index: int | None = None
        boundary_event: dict[str, Any] = {}
        for event_index, event in reversed(
            list(enumerate(list(shared.get("history_events") or [])))
        ):
            if (
                not isinstance(event, dict)
                or str(event.get("type") or "") != "save_loaded"
            ):
                continue
            payload = event.get("payload")
            payload_obj = payload if isinstance(payload, dict) else {}
            event_save_context = payload_obj.get("save_context")
            event_save_obj = (
                event_save_context if isinstance(event_save_context, dict) else {}
            )
            event_kind = str(
                event_save_obj.get("kind") or payload_obj.get("reason") or ""
            ).strip().lower()
            if event_kind and event_kind != save_kind:
                continue
            boundary_index = event_index
            boundary_event = event
            break
        data_source = self._current_input_source(shared)
        session_id = str(shared.get("active_session_id") or "")
        boundary_scope_key = self._scene_capsule_boundary_key(
            shared,
            session_id=session_id,
        ) or f"{data_source}|{session_id}"
        save_identity = self._scene_capsule_semantic_digest(save_obj)
        save_boundary_obj = snapshot.get("save_boundary")
        save_boundary = (
            save_boundary_obj if isinstance(save_boundary_obj, dict) else {}
        )
        persisted_boundary_marker = ""
        if str(save_boundary.get("kind") or "").strip().lower() == save_kind:
            persisted_occurrence = {
                "seq": int(save_boundary.get("seq") or 0),
                "ts": str(save_boundary.get("ts") or ""),
            }
            if persisted_occurrence["seq"] or persisted_occurrence["ts"]:
                persisted_boundary_marker = self._scene_capsule_semantic_digest(
                    {"kind": save_kind, "occurrence": persisted_occurrence}
                )
        fallback_source_keys = {
            f"{data_source}|{session_id}|history_line",
            f"{data_source}|{session_id}|history_choice:selected",
        }
        boundary_state = self._scene_timeline_boundaries.pop(
            boundary_scope_key,
            None,
        )
        if boundary_index is not None:
            boundary_occurrence = {
                "seq": int(boundary_event.get("seq") or 0),
                "ts": str(boundary_event.get("ts") or ""),
            }
            if not boundary_occurrence["seq"] and not boundary_occurrence["ts"]:
                boundary_occurrence["index"] = boundary_index
            boundary_marker = self._scene_capsule_semantic_digest(
                {"kind": save_kind, "occurrence": boundary_occurrence}
            )
            if (
                not isinstance(boundary_state, dict)
                or str(boundary_state.get("marker") or "") != boundary_marker
            ):
                fallback_floors: dict[str, int] = {}
                for source_key in fallback_source_keys:
                    fallback_state = self._scene_capsule_fallback_occurrences.get(
                        source_key
                    )
                    if not isinstance(fallback_state, dict):
                        fallback_floors[source_key] = 0
                        continue
                    current_high_water = max(
                        [
                            int(item)
                            for item in list(
                                fallback_state.get("occurrence_ids") or []
                            )
                        ],
                        default=0,
                    )
                    # A prior observation provides a provable pre-boundary floor.
                    # On the first-ever observation, ordering inside the tick is
                    # ambiguous, so retain the conservative current high-water.
                    previous_high_water = int(
                        fallback_state.get("previous_observation_high_water") or 0
                    )
                    fallback_floors[source_key] = (
                        previous_high_water
                        if bool(
                            fallback_state.get("previous_observation_had_state")
                        )
                        and previous_high_water > 0
                        else current_high_water
                    )
                pre_boundary_event_keys = {
                    str(item.get("event_key") or "")
                    for item in [*line_occurrences, *choice_occurrences]
                    if (
                        str(item.get("event_key") or "")
                        and isinstance(item.get("history_event_index"), int)
                        and int(item.get("history_event_index") or 0)
                        <= boundary_index
                    )
                }
                boundary_state = {
                    "marker": boundary_marker,
                    "kind": save_kind,
                    "save_identity": save_identity,
                    "seq": int(boundary_event.get("seq") or 0),
                    "ts": str(boundary_event.get("ts") or ""),
                    "fallback_floors": fallback_floors,
                    "pre_boundary_event_keys": pre_boundary_event_keys,
                }
        elif (
            not isinstance(boundary_state, dict)
            or str(boundary_state.get("kind") or "") != save_kind
            or str(boundary_state.get("save_identity") or "") != save_identity
            or (
                bool(persisted_boundary_marker)
                and str(boundary_state.get("marker") or "")
                != persisted_boundary_marker
            )
        ):
            # The load event may already have left the bounded event window
            # before the agent's first observation.  Ordering is unknowable in
            # that case, so fence every currently retained occurrence and let
            # only subsequently allocated identities cross the boundary.
            boundary_state = {
                "marker": persisted_boundary_marker or f"late:{save_identity}",
                "kind": save_kind,
                "save_identity": save_identity,
                "seq": 0,
                "ts": "",
                "fallback_floors": {},
                "pre_boundary_event_keys": {
                    str(item.get("event_key") or "")
                    for item in [*line_occurrences, *choice_occurrences]
                    if str(item.get("event_key") or "")
                },
            }

        fallback_floors = dict(boundary_state.get("fallback_floors") or {})
        for source_key in fallback_source_keys:
            if source_key in fallback_floors:
                continue
            fallback_state = self._scene_capsule_fallback_occurrences.get(source_key)
            if not isinstance(fallback_state, dict):
                fallback_floors[source_key] = 0
                continue
            fallback_floors[source_key] = max(
                [
                    *[
                        int(item)
                        for item in list(
                            fallback_state.get("occurrence_ids") or []
                        )
                    ],
                    int(fallback_state.get("next_id") or 1) - 1,
                ],
                default=0,
            )
        boundary_state["fallback_floors"] = fallback_floors
        self._scene_timeline_boundaries[boundary_scope_key] = boundary_state
        while len(self._scene_timeline_boundaries) > 32:
            oldest_scope_key = next(iter(self._scene_timeline_boundaries))
            self._scene_timeline_boundaries.pop(oldest_scope_key, None)
        pre_boundary_event_keys = set(
            str(item)
            for item in list(boundary_state.get("pre_boundary_event_keys") or [])
            if str(item)
        )

        def _is_after_boundary(occurrence: dict[str, Any]) -> bool:
            try:
                event_index = int(occurrence.get("history_event_index"))
            except (TypeError, ValueError):
                source_key = str(occurrence.get("fallback_source_key") or "")
                if source_key not in fallback_source_keys:
                    return False
                try:
                    occurrence_id = int(occurrence.get("fallback_occurrence_id"))
                except (TypeError, ValueError):
                    return False
                return occurrence_id > int(fallback_floors.get(source_key) or 0)
            if boundary_index is not None:
                return event_index > boundary_index
            return str(occurrence.get("event_key") or "") not in pre_boundary_event_keys

        return (
            [item for item in line_occurrences if _is_after_boundary(item)],
            [
                item
                for item in choice_occurrences
                if bool(item.get("snapshot_fallback")) or _is_after_boundary(item)
            ],
            True,
        )

    def _note_scene_summary_suppressed(
        self,
        *,
        reason: str,
        trigger: str,
        fingerprint: str,
        stable_line_delta_count: int,
        choice_count: int,
    ) -> None:
        now = time.monotonic()
        since_last_delivery = (
            max(0.0, now - self._scene_summary_last_success_at)
            if self._scene_summary_last_success_at > 0
            else 0.0
        )
        self._scene_summary_suppressed_count += 1
        event = {
            "reason": reason,
            "trigger": trigger,
            "fingerprint": fingerprint[:8],
            "stable_line_delta_count": stable_line_delta_count,
            "choice_count": choice_count,
            "seconds_since_last_delivery": round(since_last_delivery, 3),
            "ts": self._utc_now_iso(),
        }
        self._summary_debug["scene_summary_suppressed_count"] = (
            self._scene_summary_suppressed_count
        )
        self._summary_debug["last_suppress_reason"] = reason
        self._summary_debug["last_suppressed"] = event
        self._logger.info(
            "galgame scene_summary suppressed: reason=%s trigger=%s fingerprint=%s "
            "stable_line_delta=%d choice_delta=%d since_last_delivery=%.3f",
            reason,
            trigger,
            fingerprint[:8],
            stable_line_delta_count,
            choice_count,
            since_last_delivery,
        )

    def _commit_scene_summary_repeat_delivery(
        self,
        *,
        fingerprint: str,
        reservation_key: str,
        scene_id: str,
        trigger: str,
        schedule_order: int,
        stable_line_keys: tuple[str, ...],
        choice_keys: tuple[str, ...],
    ) -> None:
        delivered_at = time.monotonic()
        self._scene_summary_repeat_deliveries[reservation_key] = {
            "delivered_at": delivered_at,
            "scene_id": scene_id,
            "trigger": trigger,
        }
        previous_content = self._scene_summary_latest_scene_content.get(scene_id) or {}
        delivered_line_keys = tuple(
            dict.fromkeys(
                [
                    *list(previous_content.get("stable_line_keys") or ()),
                    *stable_line_keys,
                ]
            )
        )
        delivered_choice_keys = tuple(
            dict.fromkeys(
                [
                    *list(previous_content.get("choice_keys") or ()),
                    *choice_keys,
                ]
            )
        )
        self._scene_summary_latest_scene_content[scene_id] = {
            "stable_line_keys": delivered_line_keys,
            "choice_keys": delivered_choice_keys,
            "delivered_schedule_order": max(
                int(previous_content.get("delivered_schedule_order") or 0),
                int(schedule_order or 0),
            ),
        }
        self._scene_summary_last_success_at = delivered_at
        self._summary_debug["last_repeat_guard_delivery"] = {
            "fingerprint": fingerprint[:8],
            "trigger": trigger,
            "stable_line_count": len(stable_line_keys),
            "choice_count": len(choice_keys),
            "schedule_order": int(schedule_order or 0),
            "ts": self._utc_now_iso(),
        }

    def _summary_task_status_debug(self) -> dict[str, Any]:
        pending: list[dict[str, Any]] = []
        for task in list(self._summary_tasks):
            meta = dict(self._summary_task_meta.get(task) or {})
            meta["done"] = bool(task.done())
            meta["cancelled"] = bool(task.cancelled())
            pending.append(meta)
        return {
            "pending_count": len(self._summary_tasks),
            "pending": json_copy(pending),
            "last_delivered_summary_key": self._last_delivered_summary_key,
            "last_delivered_summary_seq": self._last_delivered_summary_seq,
            "last_delivered_summary_scene_id": self._last_delivered_summary_scene_id,
        }

    def _record_summary_task_event(self, name: str, payload: dict[str, Any]) -> None:
        event = {
            **dict(payload or {}),
            "ts": self._utc_now_iso(),
            "pending_count": len(self._summary_tasks),
        }
        self._summary_debug[f"last_task_{name}"] = event
        task_debug = self._summary_debug.get("task")
        if not isinstance(task_debug, dict):
            task_debug = {}
        task_debug.update(self._summary_task_status_debug())
        task_debug[f"last_{name}"] = event
        self._summary_debug["task"] = task_debug

    def _restore_failed_summary_schedule(
        self,
        *,
        scene_id: str,
        route_id: str = "",
        scheduled_seq: int,
        scheduled_owner_token: int,
        scheduled_line_count: int,
        reason: str = "",
        delivery_key: str = "",
        merged_schedule_restore: list[dict[str, Any]] | None = None,
    ) -> None:
        merged_schedule_restore = list(merged_schedule_restore or [])
        restored_merged: list[dict[str, Any]] = []
        if scheduled_line_count <= 0 and not merged_schedule_restore:
            return
        if scheduled_line_count > 0:
            self._scene_tracker.restore_scene_summary_schedule(
                scene_id,
                route_id=route_id,
                seq=scheduled_seq,
                lines_since_push=scheduled_line_count,
                owner_token=scheduled_owner_token,
            )
        for item in merged_schedule_restore:
            merged_scene_id = str(item.get("scene_id") or "")
            merged_route_id = str(item.get("route_id") or "")
            merged_line_count = int(item.get("lines_since_push") or 0)
            if not merged_scene_id or merged_line_count <= 0:
                continue
            merged_seq = int(item.get("scheduled_seq") or 0)
            self._scene_tracker.restore_scene_summary_schedule(
                merged_scene_id,
                route_id=merged_route_id,
                seq=merged_seq,
                lines_since_push=merged_line_count,
                owner_token=int(item.get("scheduled_owner_token") or 0),
            )
            restored_item = {
                "scene_id": merged_scene_id,
                "scheduled_seq": merged_seq,
                "scheduled_line_count": merged_line_count,
            }
            if merged_route_id:
                restored_item["route_id"] = merged_route_id
            restored_merged.append(restored_item)
        self._record_summary_task_event(
            "restored_schedule",
            {
                "reason": reason,
                "scene_id": scene_id,
                "route_id": route_id,
                "scheduled_seq": scheduled_seq,
                "scheduled_owner_token": scheduled_owner_token,
                "scheduled_line_count": scheduled_line_count,
                "summary_delivery_key": delivery_key,
                "merged_scenes": json_copy(restored_merged),
            },
        )

    def _track_summary_task(
        self,
        task: asyncio.Task[bool],
        *,
        scene_id: str = "",
        route_id: str = "",
        scheduled_seq: int = 0,
        scheduled_owner_token: int = 0,
        scheduled_line_count: int = 0,
        merged_schedule_restore: list[dict[str, Any]] | None = None,
        repeat_reservation_key: str = "",
        meta: dict[str, Any] | None = None,
    ) -> None:
        self._summary_tasks.add(task)
        task_meta = dict(meta or {})
        self._summary_task_meta[task] = task_meta
        self._record_summary_task_event("scheduled", task_meta)

        def _finalize_without_restore() -> None:
            self._finalize_summary_schedules(
                scene_id=scene_id,
                route_id=route_id,
                scheduled_seq=scheduled_seq,
                scheduled_owner_token=scheduled_owner_token,
                merged_schedule_restore=merged_schedule_restore,
                delivered=False,
            )

        def _finish(done: asyncio.Task[bool]) -> None:
            self._summary_tasks.discard(done)
            done_meta = self._summary_task_meta.pop(done, None) or task_meta
            restore_schedule_on_failure = bool(
                done_meta.get("restore_schedule_on_failure", True)
            )
            if repeat_reservation_key:
                self._scene_summary_repeat_reservations.discard(
                    repeat_reservation_key
                )
            delivery_key = str(done_meta.get("summary_delivery_key") or "")
            if done.cancelled():
                if restore_schedule_on_failure:
                    self._restore_failed_summary_schedule(
                        scene_id=scene_id,
                        route_id=route_id,
                        scheduled_seq=scheduled_seq,
                        scheduled_owner_token=scheduled_owner_token,
                        scheduled_line_count=scheduled_line_count,
                        reason="task_cancelled",
                        delivery_key=delivery_key,
                        merged_schedule_restore=merged_schedule_restore,
                    )
                else:
                    _finalize_without_restore()
                self._record_summary_task_event("cancelled", done_meta)
                return
            try:
                delivered = bool(done.result())
            except Exception as exc:
                if restore_schedule_on_failure:
                    self._restore_failed_summary_schedule(
                        scene_id=scene_id,
                        route_id=route_id,
                        scheduled_seq=scheduled_seq,
                        scheduled_owner_token=scheduled_owner_token,
                        scheduled_line_count=scheduled_line_count,
                        reason="task_exception",
                        delivery_key=delivery_key,
                        merged_schedule_restore=merged_schedule_restore,
                    )
                else:
                    _finalize_without_restore()
                self._record_summary_task_event(
                    "exception",
                    {**done_meta, "error": str(exc)},
                )
                self._logger.warning("galgame scene summary task failed: {}", exc)
                return
            if not delivered:
                if restore_schedule_on_failure:
                    self._restore_failed_summary_schedule(
                        scene_id=scene_id,
                        route_id=route_id,
                        scheduled_seq=scheduled_seq,
                        scheduled_owner_token=scheduled_owner_token,
                        scheduled_line_count=scheduled_line_count,
                        reason="task_returned_false",
                        delivery_key=delivery_key,
                        merged_schedule_restore=merged_schedule_restore,
                    )
                else:
                    _finalize_without_restore()
                self._record_summary_task_event("returned_false", done_meta)
                return
            self._finalize_summary_schedules(
                scene_id=scene_id,
                route_id=route_id,
                scheduled_seq=scheduled_seq,
                scheduled_owner_token=scheduled_owner_token,
                merged_schedule_restore=merged_schedule_restore,
                delivered=True,
            )
            self._record_summary_task_event("finished", {**done_meta, "delivered": True})

        task.add_done_callback(_finish)

    def _finalize_summary_schedules(
        self,
        *,
        scene_id: str,
        route_id: str,
        scheduled_seq: int,
        scheduled_owner_token: int,
        merged_schedule_restore: list[dict[str, Any]] | None,
        delivered: bool,
    ) -> None:
        finalize = (
            self._scene_tracker.mark_scene_summary_delivered
            if delivered
            else self._scene_tracker.discard_scene_summary_schedule
        )
        finalize(
            scene_id,
            route_id=route_id,
            seq=scheduled_seq,
            owner_token=scheduled_owner_token,
        )
        for item in list(merged_schedule_restore or []):
            merged_scene_id = str(item.get("scene_id") or "")
            if not merged_scene_id:
                continue
            finalize(
                merged_scene_id,
                route_id=str(item.get("route_id") or ""),
                seq=int(item.get("scheduled_seq") or 0),
                owner_token=int(item.get("scheduled_owner_token") or 0),
            )

    def _track_scene_capsule_task(
        self,
        task: asyncio.Task[bool],
        *,
        order: int,
        event_keys: tuple[str, ...],
        meta: dict[str, Any],
    ) -> None:
        self._scene_capsule_tasks.add(task)
        self._scene_capsule_task_meta[task] = dict(meta)

        def _finish(done: asyncio.Task[bool]) -> None:
            self._scene_capsule_tasks.discard(done)
            self._scene_capsule_task_meta.pop(done, None)
            for event_key in event_keys:
                still_owned = any(
                    event_key
                    in set(
                        str(item)
                        for item in list(
                            (self._scene_capsule_task_meta.get(other) or {}).get(
                                "event_keys"
                            )
                            or []
                        )
                    )
                    for other in self._scene_capsule_tasks
                    if not other.done()
                )
                if not still_owned:
                    self._scene_capsule_reservations.discard(event_key)
            if done.cancelled():
                return
            try:
                done.result()
            except Exception:
                self._logger.warning(
                    "galgame scene capsule task failed: order=%d error_type=%s",
                    order,
                    type(done.exception()).__name__ if done.exception() else "unknown",
                )

        task.add_done_callback(_finish)

    def _scene_capsule_is_fresh(
        self,
        *,
        lifecycle_generation: int,
        capsule_generation: int,
        order: int,
        observation_epoch: int,
        scene_id: str,
        route_id: str,
    ) -> bool:
        if lifecycle_generation != self._start_generation:
            return False
        if capsule_generation != self._scene_capsule_generation:
            return False
        if order != self._scene_summary_latest_observed_order:
            return False
        if observation_epoch != self._scene_capsule_observation_epoch:
            return False
        if scene_id != self._observed_scene_id:
            return False
        if route_id != self._observed_route_id:
            return False
        return True

    async def _run_scene_capsule_task(
        self,
        *,
        lifecycle_generation: int,
        capsule_generation: int,
        order: int,
        observation_epoch: int,
        shared: dict[str, Any],
        session_id: str,
        scene_id: str,
        route_id: str,
        boundary_key: str,
        data_source: str,
        source_identity: str,
        content: str,
        event_keys: tuple[str, ...],
        stable_tail: tuple[str, ...],
        choice_group_tail: tuple[str, ...],
        target_line_count: int,
        target_choice_count: int,
    ) -> bool:
        def freshness_check() -> bool:
            return self._scene_capsule_is_fresh(
                lifecycle_generation=lifecycle_generation,
                capsule_generation=capsule_generation,
                order=order,
                observation_epoch=observation_epoch,
                scene_id=scene_id,
                route_id=route_id,
            )

        if not freshness_check():
            return False
        submitted = await self._push_agent_message(
            shared,
            kind="scene_delta",
            content=content,
            scene_id=scene_id,
            route_id=route_id,
            metadata={
                "context_type": "galgame_scene_delta",
                "trigger": "stable_content_delta",
                "capsule_order": order,
                "new_stable_line_count": target_line_count,
                "new_choice_count": target_choice_count,
            },
            coalesce_key=self._scene_capsule_coalesce_key(boundary_key),
            freshness_check=freshness_check,
        )
        if not submitted:
            return False
        stale_after_submission = not freshness_check()
        ledger = self._scene_capsule_delivery_ledger.setdefault(
            boundary_key,
            {
                "committed_event_keys": [],
                "stable_tail": [],
                "choice_group_tail": [],
                "source_identity": "",
                "data_source": "",
                "scene_id": "",
                "route_id": "",
                "memory_handoff_scene_aliases": {},
            },
        )
        committed = list(ledger.get("committed_event_keys") or [])
        committed.extend(event_keys)
        # The scheduler prunes this ledger against the complete live history on
        # every observation.  Do not impose a smaller fixed cap here: one choice
        # history event can legitimately contribute several occurrence keys.
        ledger["committed_event_keys"] = list(dict.fromkeys(committed))
        ledger["stable_tail"] = list(stable_tail[-4:])
        ledger["choice_group_tail"] = list(choice_group_tail[-4:])
        ledger["source_identity"] = source_identity
        ledger["data_source"] = data_source
        ledger["scene_id"] = scene_id
        ledger["route_id"] = route_id
        ledger["last_submitted_order"] = order
        self._scene_summary_latest_submitted_order = max(
            self._scene_summary_latest_submitted_order,
            order,
        )
        self._summary_debug["last_capsule_submitted"] = {
            "scene_id": scene_id,
            "route_id": route_id,
            "order": order,
            "new_stable_line_count": target_line_count,
            "new_choice_count": target_choice_count,
            "stale_after_submission": stale_after_submission,
            "ts": self._utc_now_iso(),
        }
        return True

    def _maybe_schedule_scene_capsule(
        self,
        shared: dict[str, Any],
        *,
        snapshot: dict[str, Any],
        line_occurrences: list[dict[str, Any]],
        all_choice_occurrences: list[dict[str, Any]],
        allow_delivery: bool = True,
    ) -> None:
        session_id = str(shared.get("active_session_id") or "")
        scene_id = str(snapshot.get("scene_id") or "")
        if not session_id or not scene_id:
            return
        route_id = str(snapshot.get("route_id") or "")
        boundary_key = self._scene_capsule_boundary_key(
            shared,
            session_id=session_id,
        )
        if not boundary_key:
            return
        data_source = self._current_input_source(shared)
        source_identity = self._scene_capsule_source_identity_from_fingerprint(
            self._session_fingerprint(shared)
        )
        if (
            not str(self._scene_capsule_input_marker or "")
            and self._scene_capsule_observation_epoch > 0
        ):
            marker_event_state = getattr(
                self,
                "_scene_capsule_marker_event_state",
                None,
            )
            if isinstance(marker_event_state, dict):
                marker_event_state.clear()
        input_marker = self._build_scene_capsule_input_marker(
            shared,
            snapshot=snapshot,
            boundary_key=boundary_key,
            scene_id=scene_id,
            route_id=route_id,
        )
        previous_input_marker = str(self._scene_capsule_input_marker or "")
        event_version_state = getattr(self, "_scene_capsule_event_versions", None)
        if not isinstance(event_version_state, dict):
            event_version_state = {}
            self._scene_capsule_event_versions = event_version_state
        save_context = snapshot.get("save_context")
        save_obj = save_context if isinstance(save_context, dict) else {}
        save_kind = str(save_obj.get("kind") or "").strip().lower()
        save_boundary_marker = ""
        save_boundary_semantic_marker = ""
        if save_kind in {"load", "rollback"}:
            save_boundary_payload = {
                "kind": save_kind,
                "slot_id": str(save_obj.get("slot_id") or ""),
                "save_id": str(save_obj.get("save_id") or ""),
                "checkpoint_id": str(save_obj.get("checkpoint_id") or ""),
            }
            save_boundary_semantic_marker = self._scene_capsule_semantic_digest(
                save_boundary_payload
            )
            save_occurrence: dict[str, Any] = {}
            for event_index, event in reversed(
                list(enumerate(list(shared.get("history_events") or [])))
            ):
                if (
                    not isinstance(event, dict)
                    or str(event.get("type") or "") != "save_loaded"
                ):
                    continue
                payload = event.get("payload")
                payload_obj = payload if isinstance(payload, dict) else {}
                event_save_context = payload_obj.get("save_context")
                event_save_obj = (
                    event_save_context
                    if isinstance(event_save_context, dict)
                    else {}
                )
                event_kind = str(
                    event_save_obj.get("kind") or payload_obj.get("reason") or ""
                ).strip().lower()
                if event_kind and event_kind != save_kind:
                    continue
                save_occurrence = {
                    "seq": int(event.get("seq") or 0),
                    "ts": str(event.get("ts") or payload_obj.get("ts") or ""),
                }
                if not save_occurrence["seq"] and not save_occurrence["ts"]:
                    save_occurrence["index"] = event_index
                break
            if not save_occurrence:
                persisted_boundary_obj = snapshot.get("save_boundary")
                persisted_boundary = (
                    persisted_boundary_obj
                    if isinstance(persisted_boundary_obj, dict)
                    else {}
                )
                if (
                    str(persisted_boundary.get("kind") or "").strip().lower()
                    == save_kind
                ):
                    persisted_occurrence = {
                        "seq": int(persisted_boundary.get("seq") or 0),
                        "ts": str(persisted_boundary.get("ts") or ""),
                    }
                    if persisted_occurrence["seq"] or persisted_occurrence["ts"]:
                        save_occurrence = persisted_occurrence
            previous_semantic_marker = str(
                getattr(self, "_scene_capsule_save_boundary_semantic_marker", "") or ""
            )
            if (
                not save_occurrence
                and save_boundary_semantic_marker == previous_semantic_marker
            ):
                save_boundary_marker = str(
                    getattr(self, "_scene_capsule_save_boundary_marker", "") or ""
                )
            else:
                save_boundary_marker = self._scene_capsule_semantic_digest(
                    {**save_boundary_payload, "occurrence": save_occurrence}
                )
        previous_save_boundary_marker = str(
            getattr(self, "_scene_capsule_save_boundary_marker", "") or ""
        )
        save_boundary_changed = bool(
            save_boundary_marker
            and save_boundary_marker != previous_save_boundary_marker
        )
        if not previous_input_marker:
            if self._scene_capsule_observation_epoch > 0:
                event_version_state.clear()
            self._scene_capsule_observation_epoch += 1
            self._scene_capsule_input_marker = input_marker
        elif (
            input_marker != previous_input_marker
            or save_boundary_changed
        ):
            self._scene_capsule_observation_epoch += 1
            self._scene_capsule_input_marker = input_marker
            self._cancel_scene_capsule_tasks(
                reason="scene_capsule_input_changed",
                retire=True,
            )
        if save_boundary_changed:
            event_version_state.clear()
            self._cancel_scene_memory_tasks(
                reason="scene_memory_timeline_boundary",
            )
            self._scene_tracker.reset(scene_id=scene_id)
            self._scene_tracker.sync_current_scene_summary_mirror(
                scene_id,
                route_id=route_id,
            )
            self._scene_summary_repeat_deliveries.clear()
            self._scene_summary_latest_scene_content.clear()
            self._scene_summary_latest_memory_order_by_scene.clear()
            self._scene_summary_schedule_order_counter = 0
            self._scene_summary_latest_observed_order = 0
            self._scene_summary_latest_submitted_order = 0
            self._pending_merge_primary = ""
            self._pending_merge_scene_ids = None
            self._pending_cross_scene_primary = ""
            self._last_delivered_summary_key = ""
            self._last_delivered_summary_seq = 0
            self._last_delivered_summary_scene_id = ""
            self._last_push_ts = 0.0
            self._scene_summary_last_success_at = 0.0
            boundary_ledger = self._scene_capsule_delivery_ledger.get(boundary_key)
            if isinstance(boundary_ledger, dict):
                # A load/rollback starts a new timeline inside the same logical
                # game boundary.  Old handoff evidence must not suppress a
                # legitimate replay if the reader source changes immediately.
                boundary_ledger["stable_tail"] = []
                boundary_ledger["observed_tail"] = []
                boundary_ledger["choice_group_tail"] = []
                boundary_ledger["memory_handoff_overlap_event_keys"] = []
                boundary_ledger["memory_handoff_scene_aliases"] = {}
        self._scene_capsule_save_boundary_marker = save_boundary_marker
        self._scene_capsule_save_boundary_semantic_marker = save_boundary_semantic_marker
        observation_epoch = self._scene_capsule_observation_epoch
        self._remember_scene_capsule_source_alias(source_identity, boundary_key)
        ledger = self._scene_capsule_delivery_ledger.setdefault(
            boundary_key,
            {
                "committed_event_keys": [],
                "stable_tail": [],
                "choice_group_tail": [],
                "source_identity": "",
                "data_source": "",
                "scene_id": "",
                "route_id": "",
            },
        )
        current_lines = [
            item
            for item in line_occurrences
            if str((item.get("line") or {}).get("scene_id") or "") == scene_id
            and str((item.get("line") or {}).get("route_id") or "") == route_id
        ]
        choice_occurrences = [
            item
            for item in all_choice_occurrences
            if str((item.get("choice") or {}).get("scene_id") or scene_id)
            == scene_id
            and str((item.get("choice") or {}).get("route_id") or "") == route_id
        ]
        boundary_live_event_keys = tuple(
            dict.fromkeys(
                str(item.get("event_key") or "")
                for item in [*line_occurrences, *all_choice_occurrences]
                if str(item.get("event_key") or "")
            )
        )
        boundary_live_event_key_set = set(boundary_live_event_keys)
        # Event versions must cover every occurrence still present in the
        # bounded live history.  A fixed cap can evict a live key before its
        # cancellation version is compared, allowing stale content to revive
        # under a newer observation epoch.  Prune only after history drops it.
        for versioned_event_key in list(event_version_state):
            if versioned_event_key not in boundary_live_event_key_set:
                event_version_state.pop(versioned_event_key, None)
        # Retirement must cover every occurrence still present in the bounded
        # history window, even when one large choice batch exceeds the old
        # fixed-size ledger cap. Prune only keys that actually left history so
        # tentative input cannot resurrect a cancelled capsule.
        for retired_event_key in list(self._scene_capsule_retired_event_versions):
            if retired_event_key not in boundary_live_event_key_set:
                self._scene_capsule_retired_event_versions.pop(
                    retired_event_key,
                    None,
                )
        live_event_keys = tuple(
            dict.fromkeys(
                str(item.get("event_key") or "")
                for item in [*current_lines, *choice_occurrences]
                if str(item.get("event_key") or "")
            )
        )
        live_event_key_set = set(live_event_keys)
        # A scene/route transition is an occurrence boundary for capsule
        # delivery.  Retire retained records outside the current scope so a
        # later revisit cannot revive dialogue that was already skipped.
        for event_key in boundary_live_event_key_set - live_event_key_set:
            event_version = int(
                event_version_state.setdefault(event_key, observation_epoch)
            )
            previous_retired_version = int(
                self._scene_capsule_retired_event_versions.get(event_key) or 0
            )
            self._scene_capsule_retired_event_versions[event_key] = max(
                previous_retired_version,
                event_version,
            )
        ledger["committed_event_keys"] = [
            str(item)
            for item in list(ledger.get("committed_event_keys") or [])
            if str(item) in boundary_live_event_key_set
        ]
        committed = set(ledger.get("committed_event_keys") or [])

        choice_groups: list[dict[str, Any]] = []
        choice_group_indexes: dict[str, int] = {}
        for item in choice_occurrences:
            group_key = str(
                item.get("event_group_key") or item.get("event_key") or ""
            )
            signature = str(item.get("handoff_group_signature") or "")
            event_key = str(item.get("event_key") or "")
            if not group_key or not signature or not event_key:
                continue
            group_index = choice_group_indexes.get(group_key)
            if group_index is None:
                choice_group_indexes[group_key] = len(choice_groups)
                choice_groups.append(
                    {
                        "signature": signature,
                        "event_keys": [event_key],
                    }
                )
                continue
            choice_groups[group_index]["event_keys"].append(event_key)
        current_choice_group_signatures = [
            str(group.get("signature") or "")
            for group in choice_groups
            if str(group.get("signature") or "")
        ]

        previous_source_identity = str(ledger.get("source_identity") or "")
        current_texts = [
            self._normalize_scene_summary_fingerprint_text(
                (item.get("line") or {}).get("text")
            )
            for item in current_lines
        ]
        previous_observed_source = str(
            ledger.get("observed_source_identity") or ""
        )
        if current_texts:
            if previous_observed_source and previous_observed_source != source_identity:
                ledger["memory_handoff_overlap_event_keys"] = []
                if str(ledger.get("observed_route_id") or "") == route_id:
                    previous_observed_scene_id = str(
                        ledger.get("observed_scene_id") or ""
                    )
                    scene_aliases = dict(
                        ledger.get("memory_handoff_scene_aliases") or {}
                    )
                    current_scope_key = self._scene_tracker.summary_scope_key(
                        scene_id,
                        route_id,
                    )
                    observed_overlap_end = self._scene_capsule_handoff_overlap_end(
                        [
                            str(item)
                            for item in list(ledger.get("observed_tail") or [])
                        ],
                        current_texts,
                    )
                    if observed_overlap_end:
                        if (
                            previous_observed_scene_id
                            and previous_observed_scene_id != scene_id
                        ):
                            scene_aliases.pop(current_scope_key, None)
                            scene_aliases[current_scope_key] = previous_observed_scene_id
                            while len(scene_aliases) > 8:
                                scene_aliases.pop(next(iter(scene_aliases)), None)
                        ledger["memory_handoff_overlap_event_keys"] = [
                            str(item.get("event_key") or "")
                            for item in current_lines[:observed_overlap_end]
                            if str(item.get("event_key") or "")
                        ]
                    else:
                        scene_aliases.pop(current_scope_key, None)
                    ledger["memory_handoff_scene_aliases"] = scene_aliases
            ledger["observed_source_identity"] = source_identity
            ledger["observed_tail"] = list(current_texts[-4:])
            ledger["observed_scene_id"] = scene_id
            ledger["observed_route_id"] = route_id
        if (
            self._scene_summary_repeat_guard_enabled
            and previous_source_identity
            and previous_source_identity != source_identity
            and (current_texts or current_choice_group_signatures)
        ):
            if str(ledger.get("route_id") or "") == route_id:
                previous_tail = [
                    str(item) for item in list(ledger.get("stable_tail") or [])
                ]
                overlap_end = self._scene_capsule_handoff_overlap_end(
                    previous_tail,
                    current_texts,
                )
                if overlap_end:
                    committed.update(
                        str(item.get("event_key") or "")
                        for item in current_lines[:overlap_end]
                        if str(item.get("event_key") or "")
                    )
                choice_overlap_end = self._scene_capsule_handoff_overlap_end(
                    [
                        str(item)
                        for item in list(ledger.get("choice_group_tail") or [])
                    ],
                    current_choice_group_signatures,
                )
                for group in choice_groups[:choice_overlap_end]:
                    committed.update(
                        str(item)
                        for item in list(group.get("event_keys") or [])
                        if str(item)
                    )
            ledger["committed_event_keys"] = [
                event_key
                for event_key in boundary_live_event_keys
                if event_key in committed
            ]
            ledger["source_identity"] = source_identity
            ledger["data_source"] = data_source
            ledger["scene_id"] = scene_id
            ledger["route_id"] = route_id
        if not allow_delivery:
            # Delivery-disabled observations still feed the cumulative memory
            # path below, but they must never become a capsule backlog when the
            # gate reopens.  Retire only the delivery versions: marking these
            # keys committed would also hide them from memory accounting.
            for event_key in live_event_keys:
                event_version = int(
                    event_version_state.setdefault(event_key, observation_epoch)
                )
                previous_retired_version = int(
                    self._scene_capsule_retired_event_versions.get(event_key) or 0
                )
                self._scene_capsule_retired_event_versions[event_key] = max(
                    previous_retired_version,
                    event_version,
                )
            return
        candidates: list[tuple[int, int, str, int, str, dict[str, Any]]] = []
        for index, item in enumerate(current_lines):
            event_key = str(item.get("event_key") or "")
            if not event_key or event_key in self._scene_capsule_reservations:
                continue
            event_version = int(
                event_version_state.setdefault(event_key, observation_epoch)
            )
            if int(
                self._scene_capsule_retired_event_versions.get(event_key) or 0
            ) >= event_version:
                continue
            if self._scene_summary_repeat_guard_enabled and event_key in committed:
                continue
            seq = int(item.get("seq") or 0)
            candidates.append(
                (
                    int(seq > 0),
                    seq,
                    str((item.get("line") or {}).get("ts") or ""),
                    index,
                    "line",
                    item,
                )
            )
        line_offset = len(current_lines)
        for index, item in enumerate(choice_occurrences):
            event_key = str(item.get("event_key") or "")
            if not event_key or event_key in self._scene_capsule_reservations:
                continue
            event_version = int(
                event_version_state.setdefault(event_key, observation_epoch)
            )
            if int(
                self._scene_capsule_retired_event_versions.get(event_key) or 0
            ) >= event_version:
                continue
            if self._scene_summary_repeat_guard_enabled and event_key in committed:
                continue
            seq = int(item.get("seq") or 0)
            candidates.append(
                (
                    int(seq > 0),
                    seq,
                    str(item.get("ts") or ""),
                    line_offset + index,
                    "choice",
                    item,
                )
            )
        if not candidates:
            return

        if all(item[1] > 0 for item in candidates):
            candidates.sort(key=lambda item: (item[1], item[2], item[3]))
        elif all(
            isinstance(item[5].get("history_event_index"), int)
            for item in candidates
        ):
            candidates.sort(
                key=lambda item: (
                    int(item[5]["history_event_index"]),
                    item[3],
                )
            )
        else:
            candidates.sort(
                key=lambda item: (
                    int(bool(item[5].get("snapshot_fallback"))),
                    *self._scene_capsule_occurrence_order(
                        item[5],
                        fallback_index=item[3],
                    ),
                )
            )
        _has_seq, _seq, _ts, _index, target_kind, target = candidates[-1]
        # Only the newest candidate is rendered.  Earlier candidates observed in
        # the same tick are deliberately consumed as superseded so they cannot
        # drain into later cat replies as stale dialogue.
        candidate_event_keys = tuple(
            dict.fromkeys(
                str(item[5].get("event_key") or "")
                for item in candidates
                if str(item[5].get("event_key") or "")
            )
        )
        if target_kind == "line":
            target_line = dict(target.get("line") or {})
            target_position = next(
                (
                    index
                    for index, item in enumerate(current_lines)
                    if item.get("event_key") == target.get("event_key")
                ),
                len(current_lines) - 1,
            )
            continuity_lines = [
                dict(item.get("line") or {})
                for item in current_lines[max(0, target_position - 2):target_position]
            ]
            new_stable_lines = [target_line]
            new_choices: list[dict[str, Any]] = []
        else:
            continuity_lines = [
                dict(item.get("line") or {}) for item in current_lines[-2:]
            ]
            new_stable_lines = []
            target_group_key = str(target.get("event_group_key") or "")
            new_choices = [
                dict(item.get("choice") or {})
                for item in choice_occurrences
                if target_group_key
                and str(item.get("event_group_key") or "") == target_group_key
            ] or [dict(target.get("choice") or {})]
        choice_limit = max(
            1,
            int(
                getattr(
                    self._context_config,
                    "history_choices_limit",
                    self._SCENE_DELTA_CHOICE_LIMIT,
                )
                or self._SCENE_DELTA_CHOICE_LIMIT
            ),
        )
        content = self._format_scene_delta_for_cat(
            new_stable_lines=new_stable_lines,
            new_choices=new_choices,
            continuity_lines=continuity_lines,
            choice_limit=choice_limit,
        )
        if not content:
            return

        self._scene_summary_schedule_order_counter += 1
        order = self._scene_summary_schedule_order_counter
        self._scene_summary_latest_observed_order = order
        superseded_event_keys: list[str] = []
        superseded_event_versions: dict[str, int] = {}
        for pending in list(self._scene_capsule_tasks):
            pending_meta = self._scene_capsule_task_meta.get(pending) or {}
            if int(pending_meta.get("order") or 0) < order and not pending.done():
                superseded_event_keys.extend(
                    str(item)
                    for item in list(pending_meta.get("event_keys") or [])
                    if str(item)
                )
                superseded_event_versions.update(
                    {
                        str(key): int(value or 0)
                        for key, value in dict(
                            pending_meta.get("event_versions") or {}
                        ).items()
                        if str(key)
                    }
                )
                pending.cancel()
        consumed_event_keys = tuple(
            dict.fromkeys([*superseded_event_keys, *candidate_event_keys])
        )
        consumed_event_versions = {
            event_key: int(
                superseded_event_versions.get(event_key)
                or event_version_state.setdefault(event_key, observation_epoch)
            )
            for event_key in consumed_event_keys
        }
        for event_key in consumed_event_keys:
            self._scene_capsule_reservations.add(event_key)
        normalized_tail = tuple(
            self._normalize_scene_summary_fingerprint_text(
                (item.get("line") or {}).get("text")
            )
            for item in current_lines[-4:]
            if self._normalize_scene_summary_fingerprint_text(
                (item.get("line") or {}).get("text")
            )
        )
        choice_group_tail = tuple(current_choice_group_signatures[-4:])
        task = asyncio.create_task(
            self._run_scene_capsule_task(
                lifecycle_generation=self._start_generation,
                capsule_generation=self._scene_capsule_generation,
                order=order,
                observation_epoch=observation_epoch,
                # scene_delta does not consume the cumulative bridge payload.
                # Keeping it out of the task also avoids copying reader-private
                # binary transport state through the JSON-only copy helper.
                shared={},
                session_id=session_id,
                scene_id=scene_id,
                route_id=route_id,
                boundary_key=boundary_key,
                data_source=data_source,
                source_identity=source_identity,
                content=content,
                event_keys=consumed_event_keys,
                stable_tail=normalized_tail,
                choice_group_tail=choice_group_tail,
                target_line_count=len(new_stable_lines),
                target_choice_count=min(
                    len(new_choices),
                    choice_limit,
                ),
            )
        )
        self._track_scene_capsule_task(
            task,
            order=order,
            event_keys=consumed_event_keys,
            meta={
                "order": order,
                "scene_id": scene_id,
                "route_id": route_id,
                "observation_epoch": observation_epoch,
                "event_count": len(consumed_event_keys),
                "event_keys": list(consumed_event_keys),
                "event_versions": consumed_event_versions,
            },
        )

    def _build_local_scene_summary_from_context(
        self,
        context: dict[str, Any],
        *,
        scene_id: str,
        route_id: str,
        snapshot: dict[str, Any],
    ) -> str:
        return self._build_scene_context_fallback(
            scene_id=scene_id,
            route_id=route_id or str(context.get("route_id") or ""),
            lines=list(context.get("stable_lines") or []),
            selected_choices=list(context.get("recent_choices") or []),
            snapshot=snapshot,
        )

    def _replace_scene_memory_summary(
        self,
        *,
        scene_id: str,
        route_id: str,
        summary: str,
    ) -> None:
        if not scene_id or not summary:
            return
        for index in range(len(self._scene_memory) - 1, -1, -1):
            item = self._scene_memory[index]
            if not isinstance(item, dict):
                continue
            if str(item.get("scene_id") or "") != scene_id:
                continue
            if str(item.get("route_id") or "") != route_id:
                continue
            updated = {
                **item,
                "summary": summary,
                "ts": self._utc_now_iso(),
                "memory_kind": "archive",
            }
            self._scene_memory.pop(index)
            self._append_bounded(self._scene_memory, updated, limit=32)
            return
        self._append_bounded(
            self._scene_memory,
            {
                "scene_id": scene_id,
                "route_id": route_id,
                "summary": summary,
                "ts": self._utc_now_iso(),
                "memory_kind": "archive",
            },
            limit=32,
        )

    def _upsert_local_scene_memory(self, memory: dict[str, Any]) -> None:
        scene_id = str(memory.get("scene_id") or "")
        route_id = str(memory.get("route_id") or "")
        summary = str(memory.get("summary") or "").strip()
        if not scene_id or not summary:
            return
        for index in range(len(self._scene_memory) - 1, -1, -1):
            item = self._scene_memory[index]
            if not isinstance(item, dict):
                continue
            if str(item.get("scene_id") or "") != scene_id:
                continue
            if str(item.get("route_id") or "") != route_id:
                continue
            # A cumulative archive is authoritative.  Only refresh the
            # lightweight scene-transition seed from an earlier visit.
            if str(item.get("memory_kind") or "") != "local":
                return
            self._scene_memory.pop(index)
            break
        self._append_bounded(
            self._scene_memory,
            {**dict(memory), "memory_kind": "local"},
            limit=32,
        )

    def _schedule_scene_summary_task(
        self,
        *,
        shared: dict[str, Any],
        session_id: str,
        scene_id: str,
        route_id: str,
        snapshot: dict[str, Any],
        context: dict[str, Any],
        trigger: str,
        metadata: dict[str, Any],
        scheduled_owner_token: int = 0,
        scheduled_line_count: int = 0,
        merged_schedule_restore: list[dict[str, Any]] | None = None,
    ) -> None:
        if trigger != "line_count":
            self._summary_debug["last_memory_skip"] = {
                "reason": "non_line_count_trigger",
                "trigger": trigger,
                "scene_id": scene_id,
                "ts": self._utc_now_iso(),
            }
            return
        if not session_id or not scene_id:
            return
        try:
            shared_payload = json_copy(shared)
            snapshot_payload = json_copy(snapshot)
            context_payload = json_copy(context)
            metadata_payload = json_copy(metadata)
        except Exception as exc:
            self._logger.warning(
                "galgame json_copy failed in scene context update: {}",
                exc,
            )
            shared_payload = dict(shared)
            snapshot_payload = dict(snapshot)
            context_payload = dict(context)
            metadata_payload = dict(metadata)
        current_data_source = self._current_input_source(shared_payload)
        scheduled_seq = int(metadata_payload.get("scheduled_from_event_seq") or 0)
        stable_line_count = _context_line_count(context_payload.get("stable_lines"))
        self._scene_summary_schedule_order_counter += 1
        memory_order = self._scene_summary_schedule_order_counter
        memory_scope_key = self._scene_route_scope_key(
            scene_id=scene_id,
            route_id=route_id,
        )
        memory_scene_alias_ids = [
            str(item)
            for item in list(metadata_payload.get("memory_scene_alias_ids") or [])
            if str(item) and str(item) != scene_id
        ]
        memory_predecessor_scope_keys = list(
            dict.fromkeys(
                [
                    memory_scope_key,
                    *[
                        self._scene_route_scope_key(
                            scene_id=alias_scene_id,
                            route_id=route_id,
                        )
                        for alias_scene_id in memory_scene_alias_ids
                    ],
                ]
            )
        )
        metadata_payload["_memory_schedule_order"] = memory_order
        metadata_payload["_memory_scope_key"] = memory_scope_key
        metadata_payload["_memory_scene_alias_ids"] = memory_scene_alias_ids
        metadata_payload["_memory_predecessor_scope_keys"] = (
            memory_predecessor_scope_keys
        )
        metadata_payload["_memory_boundary_key"] = self._scene_capsule_boundary_key(
            shared_payload,
            session_id=session_id,
        )
        last_line_seq = int(metadata_payload.get("last_line_seq") or scheduled_seq or 0)
        delivery_key = str(metadata_payload.get("summary_delivery_key") or "")
        if not delivery_key:
            delivery_key = self._summary_delivery_key(
                scene_id=scene_id,
                route_id=route_id,
                scheduled_seq=scheduled_seq,
                last_line_seq=last_line_seq,
                stable_line_count=stable_line_count,
                last_line_occurrence_key=str(
                    metadata_payload.get("last_line_occurrence_key") or ""
                ),
            )
            metadata_payload["summary_delivery_key"] = delivery_key
        metadata_payload.setdefault("stable_line_count", stable_line_count)
        task = asyncio.create_task(
            self._run_scene_summary_task(
                summary_lock=self._op_lock,
                generation=self._summary_generation,
                session_id=session_id,
                data_source_at_schedule=current_data_source,
                trusted_history_token=self._trusted_history_token(shared),
                scene_id=scene_id,
                route_id=route_id,
                shared=shared_payload,
                snapshot=snapshot_payload,
                context=context_payload,
                trigger=trigger,
                metadata=metadata_payload,
                # Every accepted line-count GameLLM task is a cumulative memory
                # archive. It never drives the cat reply pipeline.
                update_scene_memory=True,
            )
        )
        self._track_summary_task(
            task,
            scene_id=scene_id,
            route_id=route_id,
            scheduled_seq=scheduled_seq,
            scheduled_owner_token=scheduled_owner_token,
            scheduled_line_count=scheduled_line_count,
            merged_schedule_restore=merged_schedule_restore,
            meta={
                "scene_id": scene_id,
                "route_id": route_id,
                "scheduled_seq": scheduled_seq,
                "scheduled_owner_token": scheduled_owner_token,
                "scheduled_line_count": scheduled_line_count,
                "merged_schedule_restore": json_copy(merged_schedule_restore or []),
                "stable_line_count": stable_line_count,
                "summary_delivery_key": delivery_key,
                "session_id_at_schedule": session_id,
                "data_source_at_schedule": current_data_source,
                "trusted_history_token": self._trusted_history_token(shared),
                "memory_order": memory_order,
                "memory_scope_key": memory_scope_key,
                "memory_predecessor_scope_keys": memory_predecessor_scope_keys,
            },
        )

    async def _run_scene_memory_task(
        self,
        *,
        summary_lock: asyncio.Lock | None,
        generation: int,
        session_id: str,
        data_source_at_schedule: str,
        trusted_history_token: str,
        scene_id: str,
        route_id: str,
        snapshot: dict[str, Any],
        context: dict[str, Any],
        trigger: str,
        metadata: dict[str, Any],
        update_scene_memory: bool,
    ) -> bool:
        # Source/session identifiers are intentionally not completion fences.
        # A trusted reader/OCR handoff may finish a memory-only summary for the
        # same logical game boundary.  Real/unknown resets advance generation;
        # scene+route ordering below prevents cross-route overwrites.
        del data_source_at_schedule, trusted_history_token
        scheduled_seq = int(metadata.get("scheduled_from_event_seq") or 0)
        delivery_key = str(metadata.get("summary_delivery_key") or "")
        memory_order = int(metadata.get("_memory_schedule_order") or 0)
        memory_boundary_key = str(metadata.get("_memory_boundary_key") or "")
        memory_scope_key = str(metadata.get("_memory_scope_key") or "")
        memory_scene_alias_ids = [
            str(item)
            for item in list(metadata.get("_memory_scene_alias_ids") or [])
            if str(item) and str(item) != scene_id
        ]
        memory_predecessor_scope_keys = {
            str(item)
            for item in list(metadata.get("_memory_predecessor_scope_keys") or [])
            if str(item)
        }
        memory_predecessor_scope_keys.add(memory_scope_key)
        expected_memory_scope_key = self._scene_route_scope_key(
            scene_id=scene_id,
            route_id=route_id,
        )
        self._record_summary_task_event(
            "started",
            {
                "scene_id": scene_id,
                "trigger": trigger,
                "scheduled_seq": scheduled_seq,
                "summary_delivery_key": delivery_key,
                "generation": generation,
                "memory_order": memory_order,
            },
        )
        current_task = asyncio.current_task()
        predecessors: list[tuple[int, asyncio.Task[bool]]] = []
        for pending_task in self._summary_tasks:
            if pending_task is current_task or pending_task.done():
                continue
            pending_meta = self._summary_task_meta.get(pending_task) or {}
            if (
                str(pending_meta.get("memory_scope_key") or "")
                not in memory_predecessor_scope_keys
            ):
                continue
            pending_order = int(pending_meta.get("memory_order") or 0)
            if 0 < pending_order < memory_order:
                predecessors.append((pending_order, pending_task))
        if predecessors:
            _, predecessor = max(predecessors, key=lambda item: item[0])
            try:
                await asyncio.shield(predecessor)
            except asyncio.CancelledError:
                if current_task is None or current_task.cancelling():
                    raise
                # A cancelled predecessor surfaces through shield as
                # CancelledError without cancelling this successor task.
                # Continue from the latest committed archive in that case.
            except Exception:
                # The predecessor's tracker restores its schedule.  A newer
                # archive may still proceed from the last committed memory.
                pass
        if generation != self._summary_generation:
            return False
        memory_scene_id_set = {scene_id, *memory_scene_alias_ids}
        previous_scene_candidates = [
            (str(item.get("ts") or ""), index, item)
            for index, item in enumerate(self._scene_memory)
            if isinstance(item, dict)
            and str(item.get("scene_id") or "") in memory_scene_id_set
            and str(item.get("route_id") or "") == route_id
            and str(item.get("summary") or "").strip()
        ]
        previous_scene_summary = (
            str(
                max(
                    previous_scene_candidates,
                    key=lambda candidate: (candidate[0], candidate[1]),
                )[2].get("summary")
                or ""
            ).strip()
            if previous_scene_candidates
            else ""
        )
        if previous_scene_summary:
            context = {
                **context,
                "previous_scene_summary": previous_scene_summary,
            }
        _formatted_summary, summary_meta = await self._summarize_scene_context_for_cat(
            context,
            scene_id=scene_id,
            route_id=route_id,
            snapshot=snapshot,
        )
        summary_text = str(summary_meta.get("scene_summary") or "").strip()
        if not summary_text or summary_lock is None:
            return False
        async with summary_lock:
            if generation != self._summary_generation:
                return False
            current_fingerprint = dict(self._observed_session_fingerprint or {})
            if not current_fingerprint and session_id == self._observed_session_id:
                # Some internal callers schedule an already-observed scene
                # directly without first storing a full runtime fingerprint.
                # Exact session continuity is sufficient only for that empty-
                # fingerprint case; real/unknown resets still invalidate the
                # memory generation or replace the observed session.
                current_boundary_key = memory_boundary_key
            else:
                current_boundary_key = self._scene_capsule_boundary_key_from_fingerprint(
                    current_fingerprint,
                    fallback_identity=self._trusted_history_token_from_fingerprint(
                        current_fingerprint
                    ),
                )
            if (
                not memory_boundary_key
                or memory_boundary_key != current_boundary_key
                or not memory_scope_key
                or memory_scope_key != expected_memory_scope_key
            ):
                return False
            latest_memory_order = int(
                self._scene_summary_latest_memory_order_by_scene.get(
                    memory_scope_key
                )
                or 0
            )
            if memory_order < latest_memory_order:
                self._summary_debug["last_drop"] = {
                    "reason": "stale_memory_order",
                    "scene_id": scene_id,
                    "memory_order": memory_order,
                    "latest_memory_order": latest_memory_order,
                    "summary_delivery_key": delivery_key,
                }
                return True
            if delivery_key and delivery_key == self._last_delivered_summary_key:
                return True

            # Claim the order before either memory sink is called.  A partial
            # sink failure must not permit an older result to overwrite it.
            self._scene_summary_latest_memory_order_by_scene[memory_scope_key] = (
                memory_order
            )
            if update_scene_memory:
                self._replace_scene_memory_summary(
                    scene_id=scene_id,
                    route_id=route_id,
                    summary=summary_text,
                )
            story_recorded = True
            story_recorder = getattr(
                self._plugin,
                "_record_story_progress_from_scene_summary",
                None,
            )
            if callable(story_recorder):
                try:
                    story_recorder(
                        scene_id=scene_id,
                        route_id=route_id,
                        summary=summary_text,
                        push_seq=scheduled_seq,
                    )
                except Exception:
                    self._logger.warning(
                        "galgame story_so_far update failed",
                        exc_info=True,
                    )
                    story_recorded = False
            self._last_delivered_summary_key = delivery_key
            self._last_delivered_summary_seq = scheduled_seq
            self._last_delivered_summary_scene_id = scene_id
            self._last_push_ts = time.monotonic()
            self._record_summary_task_event(
                "memory_finished",
                {
                    "scene_id": scene_id,
                    "trigger": trigger,
                    "scheduled_seq": scheduled_seq,
                    "summary_delivery_key": delivery_key,
                    "memory_order": memory_order,
                    "story_recorded": story_recorded,
                },
            )
            return True

    async def _run_scene_summary_task(
        self,
        *,
        summary_lock: asyncio.Lock | None,
        generation: int,
        session_id: str,
        data_source_at_schedule: str,
        trusted_history_token: str,
        scene_id: str,
        route_id: str,
        shared: dict[str, Any],
        snapshot: dict[str, Any],
        context: dict[str, Any],
        trigger: str,
        metadata: dict[str, Any],
        update_scene_memory: bool,
    ) -> bool:
        return await self._run_scene_memory_task(
            summary_lock=summary_lock,
            generation=generation,
            session_id=session_id,
            data_source_at_schedule=data_source_at_schedule,
            trusted_history_token=trusted_history_token,
            scene_id=scene_id,
            route_id=route_id,
            snapshot=snapshot,
            context=context,
            trigger=trigger,
            metadata=metadata,
            update_scene_memory=update_scene_memory,
        )

    def _line_summary_key(self, line: dict[str, Any]) -> str:
        text = str(line.get("text") or "").strip()
        speaker = str(line.get("speaker") or "").strip()
        scene_id = str(line.get("scene_id") or "").strip()
        if text:
            return f"{scene_id}:{speaker}:{text}"
        return str(line.get("line_id") or "").strip()

    async def _maybe_push_periodic_scene_summary(
        self,
        shared: dict[str, Any],
        *,
        snapshot: dict[str, Any],
    ) -> None:
        allow_capsule_delivery = self._should_push_scene(shared)
        if not allow_capsule_delivery:
            self._summary_debug["gate_blocked"] = {
                "gate": "should_push_scene",
                "push_notifications": bool(shared.get("push_notifications")),
                "mode": str(shared.get("mode") or ""),
            }
            self._logger.info("galgame scene_summary gate: push_notifications=%s mode=%s",
                             bool(shared.get("push_notifications")),
                             str(shared.get("mode") or ""))
            self._cancel_scene_capsule_tasks(
                reason="scene_push_disabled",
                retire=True,
            )
        session_id = str(shared.get("active_session_id") or "")
        if not session_id:
            self._summary_debug["gate_blocked"] = {"gate": "missing_session_id"}
            return
        current_scene_id = str(snapshot.get("scene_id") or "")
        current_route_id = str(snapshot.get("route_id") or "")
        if (
            current_scene_id != self._summary_scene_id
            or current_route_id != self._scene_tracker.summary_route_id
        ):
            self._scene_tracker.sync_current_scene_summary_mirror(
                current_scene_id,
                route_id=current_route_id,
            )

        current_data_source = self._current_input_source(shared)
        if (
            self._scene_summary_repeat_data_source
            and current_data_source != self._scene_summary_repeat_data_source
        ):
            self._cancel_scene_capsule_tasks(
                reason="scene_summary_data_source_changed",
                retire=True,
            )
        self._scene_summary_repeat_data_source = current_data_source

        line_occurrences = self._scene_capsule_line_occurrences(
            shared,
            snapshot=snapshot,
        )
        choice_occurrences = self._scene_capsule_choice_occurrences(
            shared,
            snapshot=snapshot,
        )
        (
            line_occurrences,
            choice_occurrences,
            timeline_boundary_active,
        ) = self._scene_timeline_occurrences_after_save_boundary(
            shared,
            snapshot=snapshot,
            line_occurrences=line_occurrences,
            choice_occurrences=choice_occurrences,
        )
        self._maybe_schedule_scene_capsule(
            shared,
            snapshot=snapshot,
            line_occurrences=line_occurrences,
            all_choice_occurrences=choice_occurrences,
            allow_delivery=allow_capsule_delivery,
        )

        max_processed_seq = self._scene_tracker.summary_last_processed_event_seq
        boundary_key = self._scene_capsule_boundary_key(
            shared,
            session_id=session_id,
        )
        capsule_ledger = self._scene_capsule_delivery_ledger.get(boundary_key) or {}
        capsule_committed_keys = set(
            str(item)
            for item in [
                *list(capsule_ledger.get("committed_event_keys") or []),
                *list(
                    capsule_ledger.get("memory_handoff_overlap_event_keys") or []
                ),
            ]
            if str(item)
        )
        changed_scene_scope_keys: set[str] = set()
        now_ts = time.monotonic()
        for occurrence in line_occurrences:
            line = occurrence.get("line")
            if not isinstance(line, dict):
                continue
            scene_id = str(line.get("scene_id") or "").strip()
            if not scene_id:
                continue
            line_route_id = str(line.get("route_id") or "")
            key = str(occurrence.get("event_key") or "")
            if not key:
                continue
            seq = int(occurrence.get("seq") or 0)
            max_processed_seq = max(max_processed_seq, seq)
            if key in capsule_committed_keys:
                continue
            if self._scene_tracker.remember_scene_line(
                scene_id,
                key,
                route_id=line_route_id,
                seq=seq,
                ts=str(line.get("ts") or ""),
                now_monotonic=now_ts,
                occurrence=occurrence,
            ):
                changed_scene_scope_keys.add(
                    self._scene_tracker.summary_scope_key(
                        scene_id,
                        line_route_id,
                    )
                )
        for occurrence in choice_occurrences:
            choice = occurrence.get("choice")
            if not isinstance(choice, dict):
                continue
            if self._normalized_choice_state(choice) != "selected":
                continue
            choice_scene_id = str(choice.get("scene_id") or "").strip()
            choice_route_id = str(choice.get("route_id") or "")
            choice_key = str(occurrence.get("event_key") or "")
            if not choice_scene_id or not choice_key:
                continue
            self._scene_tracker.remember_scene_choice(
                choice_scene_id,
                choice_key,
                route_id=choice_route_id,
                occurrence=occurrence,
            )
        self._scene_tracker.summary_last_processed_event_seq = max_processed_seq

        ready_scene_scope_keys = set(changed_scene_scope_keys)
        for scope_key, state in self._scene_tracker.summary_scene_states.items():
            if (
                int(state.get("lines_since_push") or 0)
                >= self._scene_summary_push_line_interval
            ):
                ready_scene_scope_keys.add(scope_key)

        # D: 时间回退
        time_fallback_scope_keys: set[str] = set()
        for scope_key, st in self._scene_tracker.summary_scene_states.items():
            if not isinstance(st, dict):
                continue
            lsp = int(st.get("lines_since_push") or 0)
            pending_since = float(st.get("pending_since_monotonic") or 0.0)
            if (
                lsp >= self._scene_push_half_threshold
                and pending_since > 0.0
                and now_ts - pending_since > self._scene_push_time_fallback_seconds
            ):
                ready_scene_scope_keys.add(scope_key)
                time_fallback_scope_keys.add(scope_key)

        # C: 多场景累计回退。每个 scene/route 必须独立归档；把多个
        # scene 合并进 primary context 会将事实写入错误的单场景 memory。
        merge_fallback_scope_keys: set[str] = set()
        if not ready_scene_scope_keys:
            total_lines = sum(
                int(s.get("lines_since_push") or 0)
                for s in self._scene_tracker.summary_scene_states.values()
                if isinstance(s, dict)
            )
            if total_lines >= self._scene_merge_total_threshold:
                sorted_scenes = sorted(
                    (
                        (sid, s)
                        for sid, s in self._scene_tracker.summary_scene_states.items()
                        if isinstance(s, dict)
                        and int(s.get("lines_since_push") or 0) > 0
                    ),
                    key=lambda kv: str(kv[1].get("last_line_ts") or ""),
                    reverse=True,
                )
                if sorted_scenes:
                    self._pending_merge_primary = ""
                    self._pending_merge_scene_ids = None
                    merge_fallback_scope_keys.update(
                        scope_key for scope_key, _state in sorted_scenes
                    )
                    ready_scene_scope_keys.update(merge_fallback_scope_keys)

        # E: 跨 scene 累计回退。和 merge fallback 一样，每个 scope
        # 独立归档，避免总阈值达成后只清空 primary 而遗留其他短 scope。
        cross_scene_fallback_scope_keys: set[str] = set()
        if not ready_scene_scope_keys:
            total_lines = sum(
                int(s.get("lines_since_push") or 0)
                for s in self._scene_tracker.summary_scene_states.values()
                if isinstance(s, dict)
            )
            if total_lines >= self._scene_cross_scene_total_threshold:
                sorted_scenes = sorted(
                    (
                        (sid, s)
                        for sid, s in self._scene_tracker.summary_scene_states.items()
                        if isinstance(s, dict)
                        and int(s.get("lines_since_push") or 0) > 0
                    ),
                    key=lambda kv: str(kv[1].get("last_line_ts") or ""),
                    reverse=True,
                )
                if sorted_scenes:
                    self._pending_cross_scene_primary = ""
                    cross_scene_fallback_scope_keys.update(
                        scope_key for scope_key, _state in sorted_scenes
                    )
                    ready_scene_scope_keys.update(cross_scene_fallback_scope_keys)

        if not ready_scene_scope_keys:
            total_lines = sum(
                int(s.get("lines_since_push") or 0)
                for s in self._scene_tracker.summary_scene_states.values()
                if isinstance(s, dict)
            )
            self._summary_debug["gate_blocked"] = {
                "gate": "no_ready_scenes",
                "total_lines_across_scenes": total_lines,
                "scene_count": len(self._scene_tracker.summary_scene_states),
            }
            self._logger.info(
                "galgame scene_summary gate: no ready scenes (total_lines=%d scenes=%d)",
                total_lines,
                len(self._scene_tracker.summary_scene_states),
            )

        archivable_line_occurrences_by_key: dict[str, dict[str, Any]] = {}
        for summary_state in self._scene_tracker.summary_scene_states.values():
            pending_line_occurrences = summary_state.get("pending_line_occurrences")
            if not isinstance(pending_line_occurrences, dict):
                continue
            for pending_key, pending_occurrence in pending_line_occurrences.items():
                if isinstance(pending_occurrence, dict) and str(pending_key):
                    archivable_line_occurrences_by_key[str(pending_key)] = (
                        pending_occurrence
                    )
        for occurrence in line_occurrences:
            event_key = str(occurrence.get("event_key") or "")
            if event_key:
                archivable_line_occurrences_by_key[event_key] = occurrence
        archivable_line_occurrences = list(
            archivable_line_occurrences_by_key.values()
        )
        archivable_choice_occurrences_by_key: dict[str, dict[str, Any]] = {}
        for summary_state in self._scene_tracker.summary_scene_states.values():
            pending_choice_occurrences = summary_state.get(
                "pending_choice_occurrences"
            )
            if not isinstance(pending_choice_occurrences, dict):
                continue
            for pending_key, pending_occurrence in pending_choice_occurrences.items():
                if isinstance(pending_occurrence, dict) and str(pending_key):
                    archivable_choice_occurrences_by_key[str(pending_key)] = (
                        pending_occurrence
                    )
        for occurrence in choice_occurrences:
            event_key = str(occurrence.get("event_key") or "")
            if event_key:
                archivable_choice_occurrences_by_key[event_key] = occurrence
        archivable_choice_occurrences = list(
            archivable_choice_occurrences_by_key.values()
        )

        scheduled: list[dict[str, Any]] = []
        for scene_scope_key in sorted(ready_scene_scope_keys):
            state = self._scene_tracker.summary_scene_states.get(scene_scope_key)
            if not isinstance(state, dict):
                continue
            scene_id = str(state.get("scene_id") or "")
            route_id = str(state.get("route_id") or "")
            lines_since_push = int(state.get("lines_since_push") or 0)
            is_fallback = (
                scene_scope_key in time_fallback_scope_keys
                or scene_scope_key in merge_fallback_scope_keys
                or scene_scope_key in cross_scene_fallback_scope_keys
                or scene_scope_key == self._pending_merge_primary
                or scene_scope_key == self._pending_cross_scene_primary
            )
            if (
                lines_since_push < self._scene_summary_push_line_interval
                and not is_fallback
            ):
                continue

            merge_scope_keys = (
                self._pending_merge_scene_ids
                if scene_scope_key == self._pending_merge_primary
                else None
            )
            merge_ids = [
                str(merged_state.get("scene_id") or "")
                for merged_scope_key in (merge_scope_keys or [])
                if isinstance(
                    merged_state := self._scene_tracker.summary_scene_states.get(
                        merged_scope_key
                    ),
                    dict,
                )
                and str(merged_state.get("route_id") or "") == route_id
                and str(merged_state.get("scene_id") or "") != scene_id
            ]
            scope_snapshot = dict(snapshot)
            scope_snapshot["scene_id"] = scene_id
            scope_snapshot["route_id"] = route_id
            scope_shared = dict(shared)
            scope_shared["latest_snapshot"] = scope_snapshot
            memory_scene_ids = [scene_id]
            boundary_key = self._scene_capsule_boundary_key(
                shared,
                session_id=session_id,
            )
            boundary_ledger = self._scene_capsule_delivery_ledger.get(
                boundary_key
            ) or {}
            scene_aliases = dict(
                boundary_ledger.get("memory_handoff_scene_aliases") or {}
            )
            aliased_scene_id = str(
                scene_aliases.get(
                    self._scene_tracker.summary_scope_key(scene_id, route_id)
                )
                or ""
            )
            if aliased_scene_id and aliased_scene_id != scene_id:
                memory_scene_ids.append(aliased_scene_id)
            previous_scene_summary = next(
                (
                    str(memory_item.get("summary") or "").strip()
                    for memory_scene_id in memory_scene_ids
                    for memory_item in reversed(self._scene_memory)
                    if isinstance(memory_item, dict)
                    and str(memory_item.get("scene_id") or "") == memory_scene_id
                    and str(memory_item.get("route_id") or "") == route_id
                    and str(memory_item.get("summary") or "").strip()
                ),
                "",
            )
            if previous_scene_summary:
                scope_shared["previous_scene_summary"] = previous_scene_summary
            allowed_scene_routes = {
                (scene_id, route_id),
                *[
                    (
                        str(merged_state.get("scene_id") or ""),
                        str(merged_state.get("route_id") or ""),
                    )
                    for merged_scope_key in (merge_scope_keys or [])
                    if isinstance(
                        merged_state := self._scene_tracker.summary_scene_states.get(
                            merged_scope_key
                        ),
                        dict,
                    )
                    and str(merged_state.get("route_id") or "") == route_id
                ],
            }
            scope_shared["history_lines"] = [
                dict(line)
                for occurrence in archivable_line_occurrences
                if isinstance(line := occurrence.get("line"), dict)
                and (
                    str(line.get("scene_id") or ""),
                    str(line.get("route_id") or ""),
                )
                in allowed_scene_routes
            ]
            scope_shared["history_observed_lines"] = (
                []
                if timeline_boundary_active
                else [
                    dict(line)
                    for line in list(shared.get("history_observed_lines") or [])
                    if isinstance(line, dict)
                    and (
                        str(line.get("scene_id") or ""),
                        str(line.get("route_id") or ""),
                    )
                    in allowed_scene_routes
                ]
            )
            scope_history_choices: list[dict[str, Any]] = []
            scope_choice_identities: set[tuple[str, str, str]] = set()
            raw_history_choices = (
                []
                if timeline_boundary_active
                else list(shared.get("history_choices") or [])
            )
            for raw_choice in raw_history_choices:
                if not isinstance(raw_choice, dict):
                    continue
                if str(raw_choice.get("action") or "").strip().lower() != "selected":
                    continue
                if (
                    str(raw_choice.get("scene_id") or ""),
                    str(raw_choice.get("route_id") or ""),
                ) not in allowed_scene_routes:
                    continue
                choice_record = dict(raw_choice)
                identity = (
                    str(
                        choice_record.get("choice_id")
                        or choice_record.get("option_id")
                        or ""
                    ),
                    str(
                        choice_record.get("text")
                        or choice_record.get("label")
                        or ""
                    ),
                    str(choice_record.get("ts") or ""),
                )
                scope_history_choices.append(choice_record)
                scope_choice_identities.add(identity)
            for occurrence in archivable_choice_occurrences:
                choice = occurrence.get("choice")
                if not isinstance(choice, dict):
                    continue
                if self._normalized_choice_state(choice) != "selected":
                    continue
                if (
                    str(choice.get("scene_id") or ""),
                    str(choice.get("route_id") or ""),
                ) not in allowed_scene_routes:
                    continue
                choice_record = {
                    **dict(choice),
                    "action": "selected",
                    "ts": str(
                        choice.get("ts") or occurrence.get("ts") or ""
                    ),
                }
                identity = (
                    str(
                        choice_record.get("choice_id")
                        or choice_record.get("option_id")
                        or ""
                    ),
                    str(
                        choice_record.get("text")
                        or choice_record.get("label")
                        or ""
                    ),
                    str(choice_record.get("ts") or ""),
                )
                if identity in scope_choice_identities:
                    continue
                scope_history_choices.append(choice_record)
                scope_choice_identities.add(identity)
            scope_shared["history_choices"] = scope_history_choices
            scheduled_line_count = int(state.get("lines_since_push") or 0)
            scheduled_seq = int(state.get("last_line_seq") or max_processed_seq or 0)
            previous_scheduled_seq = int(state.get("last_scheduled_seq") or 0)
            seen_line_key_order = list(state.get("seen_line_key_order") or [])
            scheduled_line_keys = set(
                seen_line_key_order[-scheduled_line_count:]
                if scheduled_line_count > 0
                else []
            )
            has_previous_line_batch = len(seen_line_key_order) > scheduled_line_count
            previous_batch_last_key = (
                str(seen_line_key_order[-scheduled_line_count - 1])
                if has_previous_line_batch and scheduled_line_count > 0
                else ""
            )
            line_occurrences_by_key = {
                str(occurrence.get("event_key") or ""): occurrence
                for occurrence in archivable_line_occurrences
                if str(occurrence.get("event_key") or "")
            }
            line_event_indices = {
                str(occurrence.get("event_key") or ""): int(
                    occurrence.get("history_event_index")
                )
                for occurrence in archivable_line_occurrences
                if str(occurrence.get("event_key") or "")
                and occurrence.get("history_event_index") is not None
            }
            previous_batch_event_index = line_event_indices.get(
                previous_batch_last_key
            )
            scheduled_event_indices = [
                line_event_indices[key]
                for key in scheduled_line_keys
                if key in line_event_indices
            ]
            scheduled_batch_event_index = (
                max(scheduled_event_indices) if scheduled_event_indices else None
            )
            previous_batch_occurrence = line_occurrences_by_key.get(
                previous_batch_last_key
            )
            retained_previous_batch_order = state.get(
                "last_delivered_occurrence_order"
            )
            previous_batch_order = (
                retained_previous_batch_order
                if isinstance(retained_previous_batch_order, tuple)
                else (
                    self._scene_capsule_occurrence_order(previous_batch_occurrence)
                    if previous_batch_occurrence is not None
                    else None
                )
            )
            scheduled_batch_orders = [
                self._scene_capsule_occurrence_order(
                    line_occurrences_by_key[key]
                )
                for key in scheduled_line_keys
                if key in line_occurrences_by_key
            ]
            scheduled_batch_order = (
                max(scheduled_batch_orders) if scheduled_batch_orders else None
            )
            context = build_summarize_context(
                scope_shared,
                scene_id=scene_id,
                merge_from_scene_ids=merge_ids,
                config=self._context_config,
            )
            context["new_stable_lines"] = [
                dict(line)
                for occurrence in archivable_line_occurrences
                if str(occurrence.get("event_key") or "") in scheduled_line_keys
                and isinstance(line := occurrence.get("line"), dict)
                and (
                    str(line.get("scene_id") or ""),
                    str(line.get("route_id") or ""),
                )
                in allowed_scene_routes
            ]
            scheduled_choice_occurrences: list[dict[str, Any]] = []
            if not has_previous_line_batch:
                scheduled_choice_occurrences = [
                    occurrence
                    for occurrence in archivable_choice_occurrences
                    if isinstance(choice := occurrence.get("choice"), dict)
                    and self._normalized_choice_state(choice) == "selected"
                    and (
                        str(choice.get("scene_id") or ""),
                        str(choice.get("route_id") or ""),
                    )
                    in allowed_scene_routes
                ]
            else:
                for occurrence in archivable_choice_occurrences:
                    choice = occurrence.get("choice")
                    if not isinstance(choice, dict):
                        continue
                    if (
                        self._normalized_choice_state(choice) != "selected"
                        or (
                            str(choice.get("scene_id") or ""),
                            str(choice.get("route_id") or ""),
                        )
                        not in allowed_scene_routes
                    ):
                        continue
                    occurrence_order = self._scene_capsule_occurrence_order(
                        occurrence
                    )
                    within_occurrence_bounds = bool(
                        previous_batch_order is not None
                        and scheduled_batch_order is not None
                        and previous_batch_order[0]
                        and scheduled_batch_order[0]
                        and occurrence_order[0]
                        and previous_batch_order
                        < occurrence_order
                        <= scheduled_batch_order
                    )
                    within_event_bounds = bool(
                        previous_batch_event_index is not None
                        and scheduled_batch_event_index is not None
                        and previous_batch_event_index
                        < int(occurrence.get("history_event_index") or -1)
                        <= scheduled_batch_event_index
                    )
                    within_sequence_bounds = bool(
                        previous_scheduled_seq > 0
                        and previous_scheduled_seq
                        < int(occurrence.get("seq") or 0)
                        <= scheduled_seq
                    )
                    if not (
                        within_occurrence_bounds
                        or within_event_bounds
                        or within_sequence_bounds
                    ):
                        continue
                    scheduled_choice_occurrences.append(occurrence)
            context["new_choices"] = [
                dict(choice)
                for occurrence in scheduled_choice_occurrences
                if isinstance(choice := occurrence.get("choice"), dict)
            ]
            covered_choice_keys = [
                str(occurrence.get("event_key") or "")
                for occurrence in scheduled_choice_occurrences
                if str(occurrence.get("event_key") or "")
            ]
            if previous_scene_summary:
                # Keep the prior LLM archive explicit even in rolling context
                # mode, where scene_summary_seed intentionally remains local.
                context["previous_scene_summary"] = previous_scene_summary
            if scene_scope_key == self._pending_merge_primary:
                self._pending_merge_scene_ids = None
                self._pending_merge_primary = ""
            if scene_scope_key == self._pending_cross_scene_primary:
                self._pending_cross_scene_primary = ""
            stable_lines = list(context.get("stable_lines") or [])
            stable_line_count = _context_line_count(stable_lines)
            if not stable_lines:
                self._summary_debug["gate_blocked"] = {
                    "gate": "empty_stable_lines",
                    "scene_id": scene_id,
                    "history_lines_count": len(list(shared.get("history_lines") or [])),
                }
                continue

            stable_line_signatures = {
                json.dumps(
                    dict(line),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for line in stable_lines
                if isinstance(line, dict)
            }
            covered_line_keys = [
                str(occurrence.get("event_key") or "")
                for occurrence in archivable_line_occurrences
                if str(occurrence.get("event_key") or "")
                and isinstance(line := occurrence.get("line"), dict)
                and json.dumps(
                    dict(line),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                in stable_line_signatures
            ]

            last_line_occurrence_key = (
                str(seen_line_key_order[-1]) if seen_line_key_order else ""
            )
            scheduled_batch_has_sequence_less_line = any(
                int(line_occurrences_by_key[key].get("seq") or 0) <= 0
                for key in scheduled_line_keys
                if key in line_occurrences_by_key
            )
            delivery_key = self._summary_delivery_key(
                scene_id=scene_id,
                route_id=route_id,
                scheduled_seq=(
                    0 if scheduled_batch_has_sequence_less_line else scheduled_seq
                ),
                last_line_seq=scheduled_seq,
                stable_line_count=stable_line_count,
                last_line_occurrence_key=last_line_occurrence_key,
            )
            if delivery_key and delivery_key == self._last_delivered_summary_key:
                self._summary_debug["last_skip"] = {
                    "reason": "already_delivered_summary_key",
                    "scene_id": scene_id,
                    "scheduled_from_event_seq": scheduled_seq,
                    "summary_delivery_key": delivery_key,
                }
                continue
            scheduled_owner_token = self._scene_tracker.mark_scene_summary_scheduled(
                scene_id,
                route_id=route_id,
                seq=scheduled_seq,
                covered_line_keys=covered_line_keys,
                covered_choice_keys=covered_choice_keys,
                scheduled_occurrence_order=scheduled_batch_order,
            )
            merged_schedule_restore: list[dict[str, Any]] = []
            for merged_scope_key in merge_scope_keys or []:
                merged_state = self._scene_tracker.summary_scene_states.get(
                    merged_scope_key
                )
                if not isinstance(merged_state, dict):
                    continue
                merged_scene_id = str(merged_state.get("scene_id") or "")
                merged_route_id = str(merged_state.get("route_id") or "")
                if not merged_scene_id or merged_route_id != route_id:
                    continue
                merged_schedule_restore.append(
                    {
                        "scene_id": merged_scene_id,
                        "route_id": merged_route_id,
                        "scheduled_seq": 0,
                        "scheduled_owner_token": 0,
                        "lines_since_push": (
                            self._scene_tracker.current_scene_lines_since_push(
                                merged_scene_id,
                                route_id=merged_route_id,
                            )
                        ),
                    }
                )
                merged_schedule_restore[-1]["scheduled_owner_token"] = (
                    self._scene_tracker.mark_scene_summary_scheduled(
                        merged_scene_id,
                        route_id=merged_route_id,
                        seq=0,
                        covered_line_keys=covered_line_keys,
                        covered_choice_keys=covered_choice_keys,
                        scheduled_occurrence_order=scheduled_batch_order,
                    )
                )
            metadata = {
                "context_type": "galgame_scene_context",
                "trigger": "line_count",
                "line_interval": self._scene_summary_push_line_interval,
                "scheduled_from_event_seq": scheduled_seq,
                "last_line_seq": scheduled_seq,
                "stable_line_count": stable_line_count,
                "last_line_occurrence_key": last_line_occurrence_key,
                "summary_delivery_key": delivery_key,
                "current_scene_id_at_schedule": current_scene_id,
                "memory_scene_alias_ids": memory_scene_ids[1:],
                "merged_schedule_restore": json_copy(merged_schedule_restore),
            }
            if scheduled_line_count >= self._scene_summary_push_line_interval:
                previous = self._summary_debug.get("last_task_restored_schedule")
                if isinstance(previous, dict) and previous.get("scene_id") == scene_id:
                    metadata["retry_reason"] = "threshold_reached_without_delivery"
                    self._summary_debug["last_retry_reason"] = (
                        "threshold_reached_without_delivery"
                    )
            self._schedule_scene_summary_task(
                shared=scope_shared,
                session_id=session_id,
                scene_id=scene_id,
                route_id=route_id,
                snapshot=scope_snapshot,
                context=context,
                trigger="line_count",
                metadata=metadata,
                scheduled_owner_token=scheduled_owner_token,
                scheduled_line_count=scheduled_line_count,
                merged_schedule_restore=merged_schedule_restore,
            )
            scheduled.append(
                {
                    "scene_id": scene_id,
                    "trigger": "line_count",
                    "scheduled_from_event_seq": scheduled_seq,
                    "summary_delivery_key": delivery_key,
                    "current_scene_id_at_schedule": current_scene_id,
                    "stable_line_count": stable_line_count,
                }
            )

        self._scene_tracker.sync_current_scene_summary_mirror(
            current_scene_id,
            route_id=current_route_id,
        )
        self._summary_debug["last_processed_event_seq"] = max_processed_seq
        self._summary_debug["scene_states"] = (
            self._scene_tracker.summary_scene_statuses(
                current_scene_id=current_scene_id,
                current_route_id=current_route_id,
            )
        )
        if scheduled:
            self._summary_debug["last_scheduled"] = scheduled[-1]
            self._logger.info(
                "galgame scene_summary scheduled: count=%d scenes=%s",
                len(scheduled),
                [s["scene_id"] for s in scheduled],
            )
