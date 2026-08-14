from __future__ import annotations

import hashlib

from .agent_shared import *  # noqa: F401,F403
from .agent_prompt import _context_line_count


class AgentSummaryMixin:
    @property
    def _summary_seen_line_keys(self) -> set[str]:
        return self._scene_tracker.summary_seen_line_keys

    @_summary_seen_line_keys.setter
    def _summary_seen_line_keys(self, value: set[str]) -> None:
        self._scene_tracker.summary_seen_line_keys = value
        self._scene_tracker._summary_seen_line_key_order = list(value or set())
        scene_id = self._scene_tracker.summary_scene_id
        if scene_id:
            state = self._scene_tracker.state_for_scene(scene_id)
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
            state = self._scene_tracker.state_for_scene(scene_id)
            state["lines_since_push"] = normalized

    @property
    def _summary_scene_id(self) -> str:
        return self._scene_tracker.summary_scene_id

    @_summary_scene_id.setter
    def _summary_scene_id(self, value: str) -> None:
        self._scene_tracker.sync_current_scene_summary_mirror(str(value or ""))

    @staticmethod
    def _summary_delivery_key(
        *,
        scene_id: str,
        scheduled_seq: int = 0,
        last_line_seq: int = 0,
        stable_line_count: int = 0,
    ) -> str:
        normalized_scene_id = str(scene_id or "").strip()
        if not normalized_scene_id:
            return ""
        normalized_seq = int(scheduled_seq or 0)
        if normalized_seq > 0:
            return f"{normalized_scene_id}:{normalized_seq}"
        return (
            f"{normalized_scene_id}:{int(last_line_seq or 0)}:"
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
        fingerprint = self._session_fingerprint(shared)
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
                identity = (
                    self._trusted_history_token(shared)
                    or str(session_id or "").strip()
                )
        if not identity:
            return ""
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _scene_capsule_coalesce_key(boundary_key: str) -> str:
        return f"galgame:scene_delta:{boundary_key}" if boundary_key else ""

    @staticmethod
    def _scene_capsule_event_key(*parts: Any) -> str:
        raw = "|".join(str(part or "") for part in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _scene_capsule_fallback_occurrence_ids(
        self,
        *,
        source_key: str,
        signatures: list[str],
    ) -> list[int]:
        state = self._scene_capsule_fallback_occurrences.setdefault(
            source_key,
            {"signatures": [], "occurrence_ids": [], "next_id": 1},
        )
        previous_signatures = [
            str(item) for item in list(state.get("signatures") or [])
        ]
        previous_ids = [int(item) for item in list(state.get("occurrence_ids") or [])]
        overlap_count = 0
        for candidate in range(
            min(len(previous_signatures), len(signatures)),
            0,
            -1,
        ):
            if previous_signatures[-candidate:] == signatures[:candidate]:
                overlap_count = candidate
                break
        occurrence_ids = (
            previous_ids[-overlap_count:] if overlap_count else []
        )
        next_id = max(1, int(state.get("next_id") or 1))
        for _ in signatures[overlap_count:]:
            occurrence_ids.append(next_id)
            next_id += 1
        state["signatures"] = list(signatures)
        state["occurrence_ids"] = list(occurrence_ids)
        state["next_id"] = next_id
        return occurrence_ids

    def _scene_capsule_line_occurrences(
        self,
        shared: dict[str, Any],
        *,
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        data_source = self._current_input_source(shared)
        session_id = str(shared.get("active_session_id") or "")
        event_occurrences: list[dict[str, Any]] = []
        event_signature_counts: dict[str, int] = {}
        for event in list(shared.get("history_events") or []):
            if not isinstance(event, dict) or str(event.get("type") or "") != "line_changed":
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
            line = {
                "line_id": str(payload.get("line_id") or event.get("line_id") or ""),
                "speaker": str(payload.get("speaker") or ""),
                "text": text,
                "scene_id": str(
                    payload.get("scene_id")
                    or event.get("scene_id")
                    or snapshot.get("scene_id")
                    or ""
                ),
                "route_id": str(payload.get("route_id") or event.get("route_id") or ""),
                "ts": str(event.get("ts") or payload.get("ts") or ""),
                "stability": "stable",
            }
            signature = json.dumps(
                {
                    "line_id": line["line_id"],
                    "speaker": line["speaker"],
                    "text": line["text"],
                    "scene_id": line["scene_id"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            event_signature_counts[signature] = event_signature_counts.get(signature, 0) + 1
            try:
                seq = int(event.get("seq") or 0)
            except (TypeError, ValueError):
                seq = 0
            event_session = str(event.get("session_id") or session_id)
            fallback_event_identity = hashlib.sha256(
                (
                    signature
                    + "|"
                    + str(event.get("ts") or payload.get("ts") or "")
                ).encode("utf-8")
            ).hexdigest()[:16]
            event_key = self._scene_capsule_event_key(
                data_source,
                event_session,
                "line_changed",
                seq if seq > 0 else f"event:{fallback_event_identity}",
            )
            event_occurrences.append(
                {"event_key": event_key, "seq": seq, "line": line, "signature": signature}
            )

        fallback_occurrences: list[dict[str, Any]] = []
        fallback_pending: list[tuple[dict[str, Any], str]] = []
        consumed_event_signatures: dict[str, int] = {}
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
            line.setdefault("scene_id", str(snapshot.get("scene_id") or ""))
            line.setdefault("stability", "stable")
            signature = json.dumps(
                {
                    "line_id": str(line.get("line_id") or ""),
                    "speaker": str(line.get("speaker") or ""),
                    "text": text,
                    "scene_id": str(line.get("scene_id") or ""),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            consumed = consumed_event_signatures.get(signature, 0)
            if consumed < event_signature_counts.get(signature, 0):
                consumed_event_signatures[signature] = consumed + 1
                continue
            fallback_pending.append((line, signature))
        fallback_ids = self._scene_capsule_fallback_occurrence_ids(
            source_key=f"{data_source}|{session_id}|history_line",
            signatures=[signature for _line, signature in fallback_pending],
        )
        for (line, signature), occurrence_id in zip(
            fallback_pending,
            fallback_ids,
            strict=False,
        ):
            fallback_occurrences.append(
                {
                    "event_key": self._scene_capsule_event_key(
                        data_source,
                        session_id,
                        "history_line",
                        str(line.get("scene_id") or ""),
                        occurrence_id,
                    ),
                    "seq": 0,
                    "line": line,
                    "signature": signature,
                }
            )
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
        for event in list(shared.get("history_events") or []):
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")
            if event_type not in {"choices_shown", "choice_selected"}:
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            raw_choices = payload.get("choices") if event_type == "choices_shown" else None
            if not isinstance(raw_choices, list):
                selected = payload.get("choice")
                raw_choices = [selected if isinstance(selected, dict) else payload]
            try:
                seq = int(event.get("seq") or 0)
            except (TypeError, ValueError):
                seq = 0
            for choice_index, item in enumerate(raw_choices):
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or item.get("label") or "").strip()
                if not text:
                    continue
                choice = dict(item)
                choice["text"] = text
                choice["choice_state"] = (
                    "visible" if event_type == "choices_shown" else "selected"
                )
                choice.setdefault("scene_id", str(payload.get("scene_id") or scene_id))
                fallback_event_identity = hashlib.sha256(
                    json.dumps(
                        {
                            "type": event_type,
                            "ts": str(event.get("ts") or payload.get("ts") or ""),
                            "choice_id": str(
                                choice.get("choice_id")
                                or choice.get("option_id")
                                or ""
                            ),
                            "text": text,
                            "index": choice_index,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()[:16]
                occurrences.append(
                    {
                        "event_key": self._scene_capsule_event_key(
                            data_source,
                            str(event.get("session_id") or session_id),
                            event_type,
                            seq if seq > 0 else f"event:{fallback_event_identity}",
                            choice_index,
                        ),
                        "seq": seq,
                        "ts": str(event.get("ts") or payload.get("ts") or ""),
                        "choice": choice,
                    }
                )

        if occurrences:
            return occurrences
        for choice_state, items in (
            ("selected", list(shared.get("history_choices") or [])),
            ("visible", list(snapshot.get("choices") or [])),
        ):
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
                semantic = json.dumps(
                    {
                        "choice_id": str(choice.get("choice_id") or choice.get("option_id") or ""),
                        "text": text,
                        "state": choice_state,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                fallback_choices.append((choice, choice_scene_id, semantic))
            fallback_ids = self._scene_capsule_fallback_occurrence_ids(
                source_key=(
                    f"{data_source}|{session_id}|history_choice:{choice_state}"
                ),
                signatures=[semantic for _choice, _scene, semantic in fallback_choices],
            )
            for (choice, choice_scene_id, _semantic), occurrence_id in zip(
                fallback_choices,
                fallback_ids,
                strict=False,
            ):
                occurrences.append(
                    {
                        "event_key": self._scene_capsule_event_key(
                            data_source,
                            session_id,
                            f"history_choice:{choice_state}",
                            choice_scene_id,
                            occurrence_id,
                        ),
                        "seq": 0,
                        "ts": str(
                            choice.get("ts")
                            or (snapshot.get("ts") if choice_state == "visible" else "")
                            or ""
                        ),
                        "choice": choice,
                    }
                )
        return occurrences

    def _prune_scene_summary_repeat_deliveries(self, now: float) -> None:
        window = self._scene_summary_duplicate_window_seconds
        if window <= 0:
            self._scene_summary_repeat_deliveries.clear()
            return
        expired = [
            fingerprint
            for fingerprint, record in self._scene_summary_repeat_deliveries.items()
            if now - float(record.get("delivered_at") or 0.0) > window
        ]
        for fingerprint in expired:
            self._scene_summary_repeat_deliveries.pop(fingerprint, None)

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
        scheduled_seq: int,
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
                seq=scheduled_seq,
                lines_since_push=scheduled_line_count,
            )
        for item in merged_schedule_restore:
            merged_scene_id = str(item.get("scene_id") or "")
            merged_line_count = int(item.get("lines_since_push") or 0)
            if not merged_scene_id or merged_line_count <= 0:
                continue
            merged_seq = int(item.get("scheduled_seq") or 0)
            self._scene_tracker.restore_scene_summary_schedule(
                merged_scene_id,
                seq=merged_seq,
                lines_since_push=merged_line_count,
            )
            restored_merged.append(
                {
                    "scene_id": merged_scene_id,
                    "scheduled_seq": merged_seq,
                    "scheduled_line_count": merged_line_count,
                }
            )
        self._record_summary_task_event(
            "restored_schedule",
            {
                "reason": reason,
                "scene_id": scene_id,
                "scheduled_seq": scheduled_seq,
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
        scheduled_seq: int = 0,
        scheduled_line_count: int = 0,
        merged_schedule_restore: list[dict[str, Any]] | None = None,
        repeat_reservation_key: str = "",
        meta: dict[str, Any] | None = None,
    ) -> None:
        self._summary_tasks.add(task)
        task_meta = dict(meta or {})
        self._summary_task_meta[task] = task_meta
        self._record_summary_task_event("scheduled", task_meta)

        def _finish(done: asyncio.Task[bool]) -> None:
            self._summary_tasks.discard(done)
            done_meta = self._summary_task_meta.pop(done, None) or task_meta
            if repeat_reservation_key:
                self._scene_summary_repeat_reservations.discard(
                    repeat_reservation_key
                )
            delivery_key = str(done_meta.get("summary_delivery_key") or "")
            if done.cancelled():
                self._restore_failed_summary_schedule(
                    scene_id=scene_id,
                    scheduled_seq=scheduled_seq,
                    scheduled_line_count=scheduled_line_count,
                    reason="task_cancelled",
                    delivery_key=delivery_key,
                    merged_schedule_restore=merged_schedule_restore,
                )
                self._record_summary_task_event("cancelled", done_meta)
                return
            try:
                delivered = bool(done.result())
            except Exception as exc:
                self._restore_failed_summary_schedule(
                    scene_id=scene_id,
                    scheduled_seq=scheduled_seq,
                    scheduled_line_count=scheduled_line_count,
                    reason="task_exception",
                    delivery_key=delivery_key,
                    merged_schedule_restore=merged_schedule_restore,
                )
                self._record_summary_task_event(
                    "exception",
                    {**done_meta, "error": str(exc)},
                )
                self._logger.warning("galgame scene summary task failed: {}", exc)
                return
            if not delivered:
                self._restore_failed_summary_schedule(
                    scene_id=scene_id,
                    scheduled_seq=scheduled_seq,
                    scheduled_line_count=scheduled_line_count,
                    reason="task_returned_false",
                    delivery_key=delivery_key,
                    merged_schedule_restore=merged_schedule_restore,
                )
                self._record_summary_task_event("returned_false", done_meta)
                return
            self._record_summary_task_event("finished", {**done_meta, "delivered": True})

        task.add_done_callback(_finish)

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
                    event_key in set(
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
        generation: int,
        order: int,
        scene_id: str,
        route_id: str,
    ) -> bool:
        if generation != self._summary_generation:
            return False
        if order != self._scene_summary_latest_observed_order:
            return False
        if scene_id != self._observed_scene_id:
            return False
        if route_id and self._observed_route_id and route_id != self._observed_route_id:
            return False
        return True

    async def _run_scene_capsule_task(
        self,
        *,
        generation: int,
        order: int,
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
        target_line_count: int,
        target_choice_count: int,
    ) -> bool:
        freshness_check = lambda: self._scene_capsule_is_fresh(
            generation=generation,
            order=order,
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
        if not submitted or not freshness_check():
            return False
        ledger = self._scene_capsule_delivery_ledger.setdefault(
            boundary_key,
            {
                "committed_event_keys": [],
                "stable_tail": [],
                "source_identity": "",
                "data_source": "",
                "scene_id": "",
            },
        )
        committed = list(ledger.get("committed_event_keys") or [])
        committed.extend(event_keys)
        ledger["committed_event_keys"] = list(dict.fromkeys(committed))[-512:]
        if stable_tail:
            ledger["stable_tail"] = list(stable_tail[-4:])
        ledger["source_identity"] = source_identity
        ledger["data_source"] = data_source
        ledger["scene_id"] = scene_id
        ledger["last_submitted_order"] = order
        self._scene_summary_latest_submitted_order = order
        self._summary_debug["last_capsule_submitted"] = {
            "scene_id": scene_id,
            "order": order,
            "new_stable_line_count": target_line_count,
            "new_choice_count": target_choice_count,
            "ts": self._utc_now_iso(),
        }
        return True

    def _maybe_schedule_scene_capsule(
        self,
        shared: dict[str, Any],
        *,
        snapshot: dict[str, Any],
        line_occurrences: list[dict[str, Any]],
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
        source_identity = f"{data_source}|{session_id}"
        self._scene_capsule_source_aliases[source_identity] = boundary_key
        ledger = self._scene_capsule_delivery_ledger.setdefault(
            boundary_key,
            {
                "committed_event_keys": [],
                "stable_tail": [],
                "source_identity": "",
                "data_source": "",
                "scene_id": "",
            },
        )
        committed = set(ledger.get("committed_event_keys") or [])
        current_lines = [
            item
            for item in line_occurrences
            if str((item.get("line") or {}).get("scene_id") or "") == scene_id
        ]

        previous_source_identity = str(ledger.get("source_identity") or "")
        if (
            self._scene_summary_repeat_guard_enabled
            and previous_source_identity
            and previous_source_identity != source_identity
        ):
            previous_tail = [str(item) for item in list(ledger.get("stable_tail") or [])]
            current_texts = [
                self._normalize_scene_summary_fingerprint_text(
                    (item.get("line") or {}).get("text")
                )
                for item in current_lines
            ]
            overlap_count = 0
            for candidate in range(min(len(previous_tail), len(current_texts)), 0, -1):
                if previous_tail[-candidate:] == current_texts[:candidate]:
                    overlap_count = candidate
                    break
            if overlap_count:
                committed.update(
                    str(item.get("event_key") or "")
                    for item in current_lines[:overlap_count]
                    if str(item.get("event_key") or "")
                )
                ledger["committed_event_keys"] = list(committed)[-512:]
            ledger["source_identity"] = source_identity
            ledger["data_source"] = data_source

        choice_occurrences = [
            item
            for item in self._scene_capsule_choice_occurrences(shared, snapshot=snapshot)
            if str((item.get("choice") or {}).get("scene_id") or scene_id) == scene_id
        ]
        candidates: list[tuple[int, int, str, int, str, dict[str, Any]]] = []
        for index, item in enumerate(current_lines):
            event_key = str(item.get("event_key") or "")
            if not event_key or event_key in self._scene_capsule_reservations:
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

        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        _has_seq, _seq, _ts, _index, target_kind, target = candidates[-1]
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
            new_choices = [dict(target.get("choice") or {})]
        content = self._format_scene_delta_for_cat(
            new_stable_lines=new_stable_lines,
            new_choices=new_choices,
            continuity_lines=continuity_lines,
        )
        if not content:
            return

        self._scene_summary_schedule_order_counter += 1
        order = self._scene_summary_schedule_order_counter
        self._scene_summary_latest_observed_order = order
        superseded_event_keys: list[str] = []
        for pending in list(self._scene_capsule_tasks):
            pending_meta = self._scene_capsule_task_meta.get(pending) or {}
            if int(pending_meta.get("order") or 0) < order and not pending.done():
                superseded_event_keys.extend(
                    str(item)
                    for item in list(pending_meta.get("event_keys") or [])
                    if str(item)
                )
                pending.cancel()
        consumed_event_keys = tuple(
            dict.fromkeys([*superseded_event_keys, *candidate_event_keys])
        )
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
        task = asyncio.create_task(
            self._run_scene_capsule_task(
                generation=self._summary_generation,
                order=order,
                shared=json_copy(shared),
                session_id=session_id,
                scene_id=scene_id,
                route_id=route_id,
                boundary_key=boundary_key,
                data_source=data_source,
                source_identity=source_identity,
                content=content,
                event_keys=consumed_event_keys,
                stable_tail=normalized_tail,
                target_line_count=len(new_stable_lines),
                target_choice_count=len(new_choices),
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
                "event_count": len(consumed_event_keys),
                "event_keys": list(consumed_event_keys),
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
        self._scene_tracker.replace_scene_summary(
            scene_id=scene_id,
            route_id=route_id,
            summary=summary,
        )
        if not scene_id or not summary:
            return
        if any(
            str(item.get("scene_id") or "") == scene_id
            for item in self._scene_memory
            if isinstance(item, dict)
        ):
            return
        self._append_bounded(
            self._scene_memory,
            {
                "scene_id": scene_id,
                "route_id": route_id,
                "summary": summary,
                "ts": self._utc_now_iso(),
            },
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
        update_scene_memory: bool,
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
        self._scene_summary_latest_memory_order_by_scene[scene_id] = memory_order
        metadata_payload["_memory_schedule_order"] = memory_order
        last_line_seq = int(metadata_payload.get("last_line_seq") or scheduled_seq or 0)
        delivery_key = str(metadata_payload.get("summary_delivery_key") or "")
        if not delivery_key:
            delivery_key = self._summary_delivery_key(
                scene_id=scene_id,
                scheduled_seq=scheduled_seq,
                last_line_seq=last_line_seq,
                stable_line_count=stable_line_count,
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
                update_scene_memory=True,
            )
        )
        self._track_summary_task(
            task,
            scene_id=scene_id,
            scheduled_seq=scheduled_seq,
            scheduled_line_count=scheduled_line_count,
            merged_schedule_restore=merged_schedule_restore,
            meta={
                "scene_id": scene_id,
                "scheduled_seq": scheduled_seq,
                "scheduled_line_count": scheduled_line_count,
                "merged_schedule_restore": json_copy(merged_schedule_restore or []),
                "stable_line_count": stable_line_count,
                "summary_delivery_key": delivery_key,
                "session_id_at_schedule": session_id,
                "data_source_at_schedule": current_data_source,
                "trusted_history_token": self._trusted_history_token(shared),
                "memory_order": memory_order,
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
        del data_source_at_schedule, trusted_history_token
        scheduled_seq = int(metadata.get("scheduled_from_event_seq") or 0)
        delivery_key = str(metadata.get("summary_delivery_key") or "")
        memory_order = int(metadata.get("_memory_schedule_order") or 0)
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
            if session_id != self._observed_session_id:
                return False
            latest_memory_order = int(
                self._scene_summary_latest_memory_order_by_scene.get(scene_id) or 0
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
                self._scene_tracker.mark_scene_summary_delivered(
                    scene_id,
                    seq=scheduled_seq,
                )
                return True

            # Claim the order before either memory sink is called.  A partial
            # sink failure must not permit an older result to overwrite it.
            self._scene_summary_latest_memory_order_by_scene[scene_id] = memory_order
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
            self._scene_tracker.mark_scene_summary_delivered(scene_id, seq=scheduled_seq)
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
        if not self._should_push_scene(shared):
            self._summary_debug["gate_blocked"] = {
                "gate": "should_push_scene",
                "push_notifications": bool(shared.get("push_notifications")),
                "mode": str(shared.get("mode") or ""),
            }
            self._logger.info("galgame scene_summary gate: push_notifications=%s mode=%s",
                             bool(shared.get("push_notifications")),
                             str(shared.get("mode") or ""))
            return
        session_id = str(shared.get("active_session_id") or "")
        if not session_id:
            self._summary_debug["gate_blocked"] = {"gate": "missing_session_id"}
            return
        current_scene_id = str(snapshot.get("scene_id") or "")
        if current_scene_id != self._summary_scene_id:
            self._scene_tracker.sync_current_scene_summary_mirror(current_scene_id)

        current_data_source = self._current_input_source(shared)
        if (
            self._scene_summary_repeat_data_source
            and current_data_source != self._scene_summary_repeat_data_source
        ):
            self._cancel_summary_tasks()
        self._scene_summary_repeat_data_source = current_data_source

        line_occurrences = self._scene_capsule_line_occurrences(
            shared,
            snapshot=snapshot,
        )
        self._maybe_schedule_scene_capsule(
            shared,
            snapshot=snapshot,
            line_occurrences=line_occurrences,
        )

        max_processed_seq = self._scene_tracker.summary_last_processed_event_seq
        changed_scene_ids: set[str] = set()
        for occurrence in line_occurrences:
            line = occurrence.get("line")
            if not isinstance(line, dict):
                continue
            scene_id = str(line.get("scene_id") or "").strip()
            if not scene_id:
                continue
            key = str(occurrence.get("event_key") or "")
            if not key:
                continue
            seq = int(occurrence.get("seq") or 0)
            max_processed_seq = max(max_processed_seq, seq)
            if self._scene_tracker.remember_scene_line(
                scene_id,
                key,
                seq=seq,
                ts=str(line.get("ts") or ""),
            ):
                changed_scene_ids.add(scene_id)
        self._scene_tracker.summary_last_processed_event_seq = max_processed_seq

        ready_scene_ids = set(changed_scene_ids)
        for scene_id, state in self._scene_tracker.summary_scene_states.items():
            if int(state.get("lines_since_push") or 0) >= self._scene_summary_push_line_interval:
                ready_scene_ids.add(scene_id)

        # D: 时间回退
        time_fallback_ids: set[str] = set()
        now_ts = time.monotonic()
        if self._last_push_ts > 0 and (
            now_ts - self._last_push_ts
        ) > self._scene_push_time_fallback_seconds:
            for sid, st in self._scene_tracker.summary_scene_states.items():
                if not isinstance(st, dict):
                    continue
                lsp = int(st.get("lines_since_push") or 0)
                if lsp >= self._scene_push_half_threshold:
                    ready_scene_ids.add(sid)
                    time_fallback_ids.add(sid)

        # C: 合并回退
        if not ready_scene_ids:
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
                        if isinstance(s, dict) and int(s.get("lines_since_push") or 0) > 0
                    ),
                    key=lambda kv: str(kv[1].get("last_line_ts") or ""),
                    reverse=True,
                )
                if sorted_scenes:
                    self._pending_merge_primary = sorted_scenes[0][0]
                    self._pending_merge_scene_ids = [
                        sid for sid, _ in sorted_scenes[1:]
                    ]
                    ready_scene_ids.add(self._pending_merge_primary)

        # E: 跨 scene 累计回退
        if not ready_scene_ids:
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
                        if isinstance(s, dict) and int(s.get("lines_since_push") or 0) > 0
                    ),
                    key=lambda kv: str(kv[1].get("last_line_ts") or ""),
                    reverse=True,
                )
                if sorted_scenes:
                    self._pending_cross_scene_primary = sorted_scenes[0][0]
                    ready_scene_ids.add(self._pending_cross_scene_primary)

        if not ready_scene_ids:
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

        scheduled: list[dict[str, Any]] = []
        for scene_id in sorted(ready_scene_ids):
            state = self._scene_tracker.state_for_scene(scene_id)
            lines_since_push = int(state.get("lines_since_push") or 0)
            is_fallback = (
                scene_id in time_fallback_ids
                or scene_id == self._pending_merge_primary
                or scene_id == self._pending_cross_scene_primary
            )
            if lines_since_push < self._scene_summary_push_line_interval and not is_fallback:
                continue

            merge_ids = (
                self._pending_merge_scene_ids
                if scene_id == self._pending_merge_primary
                else None
            )
            context = build_summarize_context(
                shared,
                scene_id=scene_id,
                merge_from_scene_ids=merge_ids,
                config=self._context_config,
            )
            if scene_id == self._pending_merge_primary:
                self._pending_merge_scene_ids = None
                self._pending_merge_primary = ""
            if scene_id == self._pending_cross_scene_primary:
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

            last_line = stable_lines[-1] if isinstance(stable_lines[-1], dict) else {}
            route_id = str(
                context.get("route_id")
                or (last_line.get("route_id") if isinstance(last_line, dict) else "")
                or snapshot.get("route_id")
                or ""
            )
            scheduled_line_count = int(state.get("lines_since_push") or 0)
            scheduled_seq = int(state.get("last_line_seq") or max_processed_seq or 0)
            delivery_key = self._summary_delivery_key(
                scene_id=scene_id,
                scheduled_seq=scheduled_seq,
                last_line_seq=scheduled_seq,
                stable_line_count=stable_line_count,
            )
            if delivery_key and delivery_key == self._last_delivered_summary_key:
                self._summary_debug["last_skip"] = {
                    "reason": "already_delivered_summary_key",
                    "scene_id": scene_id,
                    "scheduled_from_event_seq": scheduled_seq,
                    "summary_delivery_key": delivery_key,
                }
                self._scene_tracker.mark_scene_summary_delivered(
                    scene_id,
                    seq=scheduled_seq,
                )
                continue
            self._scene_tracker.mark_scene_summary_scheduled(scene_id, seq=scheduled_seq)
            merged_schedule_restore: list[dict[str, Any]] = []
            for merged_sid in (merge_ids or []):
                merged_scene_id = str(merged_sid or "")
                if not merged_scene_id:
                    continue
                merged_schedule_restore.append(
                    {
                        "scene_id": merged_scene_id,
                        "scheduled_seq": 0,
                        "lines_since_push": (
                            self._scene_tracker.current_scene_lines_since_push(
                                merged_scene_id
                            )
                        ),
                    }
                )
                self._scene_tracker.mark_scene_summary_scheduled(merged_sid, seq=0)
            metadata = {
                "context_type": "galgame_scene_context",
                "trigger": "line_count",
                "line_interval": self._scene_summary_push_line_interval,
                "scheduled_from_event_seq": scheduled_seq,
                "last_line_seq": scheduled_seq,
                "stable_line_count": stable_line_count,
                "summary_delivery_key": delivery_key,
                "current_scene_id_at_schedule": current_scene_id,
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
                shared=shared,
                session_id=session_id,
                scene_id=scene_id,
                route_id=route_id,
                snapshot=snapshot,
                context=context,
                trigger="line_count",
                metadata=metadata,
                update_scene_memory=False,
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

        self._scene_tracker.sync_current_scene_summary_mirror(current_scene_id)
        self._summary_debug["last_processed_event_seq"] = max_processed_seq
        self._summary_debug["scene_states"] = self._scene_tracker.summary_scene_statuses(
            current_scene_id=current_scene_id
        )
        if scheduled:
            self._summary_debug["last_scheduled"] = scheduled[-1]
            self._logger.info(
                "galgame scene_summary scheduled: count=%d scenes=%s",
                len(scheduled),
                [s["scene_id"] for s in scheduled],
            )
