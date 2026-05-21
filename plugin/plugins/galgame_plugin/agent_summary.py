from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403
from .agent_prompt import _context_line_count


class AgentSummaryMixin:
    @property
    def _summary_seen_line_keys(self) -> set[str]:
        return self._scene_tracker.summary_seen_line_keys

    @_summary_seen_line_keys.setter
    def _summary_seen_line_keys(self, value: set[str]) -> None:
        self._scene_tracker.summary_seen_line_keys = value
        scene_id = self._scene_tracker.summary_scene_id
        if scene_id:
            state = self._scene_tracker.state_for_scene(scene_id)
            state["seen_line_keys"] = set(value or set())

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
    ) -> None:
        if scheduled_line_count <= 0:
            return
        self._scene_tracker.restore_scene_summary_schedule(
            scene_id,
            seq=scheduled_seq,
            lines_since_push=scheduled_line_count,
        )
        self._record_summary_task_event(
            "restored_schedule",
            {
                "reason": reason,
                "scene_id": scene_id,
                "scheduled_seq": scheduled_seq,
                "scheduled_line_count": scheduled_line_count,
                "summary_delivery_key": delivery_key,
            },
        )

    def _track_summary_task(
        self,
        task: asyncio.Task[bool],
        *,
        scene_id: str = "",
        scheduled_seq: int = 0,
        scheduled_line_count: int = 0,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self._summary_tasks.add(task)
        task_meta = dict(meta or {})
        self._summary_task_meta[task] = task_meta
        self._record_summary_task_event("scheduled", task_meta)

        def _finish(done: asyncio.Task[bool]) -> None:
            self._summary_tasks.discard(done)
            done_meta = self._summary_task_meta.pop(done, None) or task_meta
            delivery_key = str(done_meta.get("summary_delivery_key") or "")
            if done.cancelled():
                self._restore_failed_summary_schedule(
                    scene_id=scene_id,
                    scheduled_seq=scheduled_seq,
                    scheduled_line_count=scheduled_line_count,
                    reason="task_cancelled",
                    delivery_key=delivery_key,
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
                )
                self._record_summary_task_event("returned_false", done_meta)
                return
            self._record_summary_task_event("finished", {**done_meta, "delivered": True})

        task.add_done_callback(_finish)

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
    ) -> None:
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
        scheduled_seq = int(metadata_payload.get("scheduled_from_event_seq") or 0)
        stable_line_count = _context_line_count(context_payload.get("stable_lines"))
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
                data_source_at_schedule=self._current_input_source(shared),
                trusted_history_token=self._trusted_history_token(shared),
                scene_id=scene_id,
                route_id=route_id,
                shared=shared_payload,
                snapshot=snapshot_payload,
                context=context_payload,
                trigger=trigger,
                metadata=metadata_payload,
                update_scene_memory=update_scene_memory,
            )
        )
        self._track_summary_task(
            task,
            scene_id=scene_id,
            scheduled_seq=scheduled_seq,
            scheduled_line_count=scheduled_line_count,
            meta={
                "scene_id": scene_id,
                "scheduled_seq": scheduled_seq,
                "scheduled_line_count": scheduled_line_count,
                "stable_line_count": stable_line_count,
                "summary_delivery_key": delivery_key,
                "session_id_at_schedule": session_id,
                "data_source_at_schedule": self._current_input_source(shared),
                "trusted_history_token": self._trusted_history_token(shared),
            },
        )

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
        scheduled_seq = int(metadata.get("scheduled_from_event_seq") or 0)
        delivery_key = str(metadata.get("summary_delivery_key") or "")
        self._record_summary_task_event(
            "started",
            {
                "scene_id": scene_id,
                "trigger": trigger,
                "scheduled_seq": scheduled_seq,
                "summary_delivery_key": delivery_key,
                "generation": generation,
            },
        )
        try:
            summary, summary_meta = await self._summarize_scene_context_for_cat(
                context,
                scene_id=scene_id,
                route_id=route_id,
                snapshot=snapshot,
            )
        except Exception as exc:
            plain_summary = self._build_scene_context_fallback(
                scene_id=scene_id,
                route_id=route_id,
                lines=list(context.get("stable_lines") or []),
                selected_choices=list(context.get("recent_choices") or []),
                snapshot=snapshot,
            )
            summary = self._format_scene_context_for_cat(
                summary=plain_summary,
                key_points=[],
                context=context,
                snapshot=snapshot,
            )
            summary_meta = {
                "scene_summary": plain_summary,
                "key_points": [],
                "summary_source": "local_context",
                "summary_degraded": True,
                "summary_diagnostic": str(exc),
            }

        lock = summary_lock
        if lock is None:
            self._summary_debug["last_drop"] = {
                "reason": "missing_summary_lock",
                "scene_id": scene_id,
                "trigger": trigger,
                "summary_delivery_key": delivery_key,
            }
            self._logger.warning("galgame scene_summary drop: missing_summary_lock scene=%s", scene_id)
            return False
        async with lock:
            if generation != self._summary_generation:
                self._summary_debug["last_drop"] = {
                    "reason": "generation_mismatch",
                    "scene_id": scene_id,
                    "trigger": trigger,
                    "generation": generation,
                    "current_generation": self._summary_generation,
                    "summary_delivery_key": delivery_key,
                }
                self._logger.info(
                    "galgame scene_summary drop: generation_mismatch scene=%s gen=%d current=%d",
                    scene_id, generation, self._summary_generation,
                )
                return False
            if session_id != self._observed_session_id:
                current_token = self._trusted_history_token_from_fingerprint(
                    self._observed_session_fingerprint
                )
                allow_transient_delivery = (
                    self._last_session_transition_type == "ocr_transient_session_reset"
                    and data_source_at_schedule == DATA_SOURCE_OCR_READER
                    and trusted_history_token
                    and trusted_history_token == current_token
                    and scene_id in self._scene_tracker.summary_scene_states
                )
                if not allow_transient_delivery:
                    self._summary_debug["last_drop"] = {
                        "reason": "session_mismatch",
                        "scene_id": scene_id,
                        "trigger": trigger,
                        "session_id": session_id,
                        "current_session_id": self._observed_session_id,
                        "transition_type": self._last_session_transition_type,
                        "data_source_at_schedule": data_source_at_schedule,
                        "summary_delivery_key": delivery_key,
                    }
                    self._logger.info(
                        "galgame scene_summary drop: session_mismatch scene=%s session=%s current=%s",
                        scene_id, session_id, self._observed_session_id,
                    )
                    return False
            current_scene_id = self._observed_scene_id
            scene_no_longer_current = scene_id != current_scene_id
            if scene_no_longer_current and trigger != "line_count":
                self._summary_debug["last_drop"] = {
                    "reason": "scene_mismatch",
                    "scene_id": scene_id,
                    "trigger": trigger,
                    "current_scene_id": current_scene_id,
                    "summary_delivery_key": delivery_key,
                }
                self._logger.info(
                    "galgame scene_summary drop: scene_mismatch scene=%s current=%s trigger=%s",
                    scene_id, current_scene_id, trigger,
                )
                return False
            if delivery_key and delivery_key == self._last_delivered_summary_key:
                self._summary_debug["last_skip"] = {
                    "reason": "already_delivered_summary_key",
                    "scene_id": scene_id,
                    "trigger": trigger,
                    "summary_delivery_key": delivery_key,
                }
                self._scene_tracker.mark_scene_summary_delivered(
                    scene_id,
                    seq=scheduled_seq,
                )
                return True
            if update_scene_memory:
                self._replace_scene_memory_summary(
                    scene_id=scene_id,
                    route_id=route_id,
                    summary=str(summary_meta.get("scene_summary") or summary),
                )
            push_metadata = dict(metadata)
            push_metadata.update(summary_meta)
            if trigger:
                push_metadata.setdefault("trigger", trigger)
            if scene_no_longer_current:
                push_metadata.setdefault("delivered_after_scene_change", True)
                push_metadata.setdefault("current_scene_id", current_scene_id)
                if trigger == "line_count":
                    push_metadata.setdefault("scene_changed_while_summarizing", True)
            self._record_summary_task_event(
                "before_push",
                {
                    "scene_id": scene_id,
                    "trigger": trigger,
                    "scheduled_seq": scheduled_seq,
                    "summary_delivery_key": delivery_key,
                },
            )
            delivered = await self._push_agent_message(
                shared,
                kind="scene_summary",
                content=(
                    "======[游戏上下文提示]\n"
                    "以下内容来自 galgame 插件对当前游戏画面和近期台词的理解。"
                    "这不是后台任务，也不是任务完成通知。回复时不要说“后台任务完成”、"
                    "“任务跑完了”、“插件完成了”。请直接以当前角色人格自然评论剧情、"
                    "回应角色处境，或给出简短陪伴式反应。\n"
                    + str(summary or "")
                    + "\n======"
                ),
                scene_id=scene_id,
                route_id=route_id,
                metadata=push_metadata,
            )
            if not delivered:
                self._summary_debug["last_drop"] = {
                    "reason": "push_not_delivered",
                    "scene_id": scene_id,
                    "trigger": trigger,
                    "summary_delivery_key": delivery_key,
                    "last_outbound_status": "not_delivered",
                }
                self._logger.warning(
                    "galgame scene_summary drop: push_not_delivered scene=%s status=%s",
                    scene_id,
                    "not_delivered",
                )
                return False
            self._logger.info(
                "galgame scene_summary delivered: scene=%s key=%s trigger=%s",
                scene_id, delivery_key, trigger,
            )
            self._last_delivered_summary_key = delivery_key
            self._last_delivered_summary_seq = scheduled_seq
            self._last_delivered_summary_scene_id = scene_id
            self._scene_tracker.mark_scene_summary_delivered(scene_id, seq=scheduled_seq)
            story_recorder = getattr(
                self._plugin,
                "_record_story_progress_from_scene_summary",
                None,
            )
            story_recorded = True
            if callable(story_recorder):
                try:
                    story_recorder(
                        scene_id=scene_id,
                        route_id=route_id,
                        summary=str(summary_meta.get("scene_summary") or summary),
                        push_seq=scheduled_seq,
                    )
                except Exception:
                    self._logger.warning(
                        "galgame story_so_far update failed",
                        exc_info=True,
                    )
                    story_recorded = False
            if story_recorded:
                self._last_push_ts = time.monotonic()
            self._record_summary_task_event(
                "after_push",
                {
                    "scene_id": scene_id,
                    "trigger": trigger,
                    "scheduled_seq": scheduled_seq,
                    "summary_delivery_key": delivery_key,
                    "delivered": True,
                },
            )
            return True

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

        event_seq_by_key: dict[str, int] = {}
        event_ts_by_key: dict[str, str] = {}
        max_processed_seq = self._scene_tracker.summary_last_processed_event_seq
        history_events = shared.get("history_events")
        if isinstance(history_events, list):
            for event in history_events:
                if not isinstance(event, dict):
                    continue
                try:
                    seq = int(event.get("seq") or 0)
                except (TypeError, ValueError):
                    seq = 0
                if seq > max_processed_seq:
                    max_processed_seq = seq
                if str(event.get("type") or "") != "line_changed":
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                line = {
                    "line_id": str(payload.get("line_id") or ""),
                    "speaker": str(payload.get("speaker") or ""),
                    "text": str(payload.get("text") or "").strip(),
                    "scene_id": str(payload.get("scene_id") or "").strip(),
                    "route_id": str(payload.get("route_id") or ""),
                    "ts": str(event.get("ts") or ""),
                }
                key = self._line_summary_key(line)
                if not key:
                    continue
                event_seq_by_key[key] = max(seq, int(event_seq_by_key.get(key) or 0))
                event_ts_by_key[key] = str(event.get("ts") or "")
        self._scene_tracker.summary_last_processed_event_seq = max_processed_seq

        changed_scene_ids: set[str] = set()
        history_lines = shared.get("history_lines")
        if not isinstance(history_lines, list):
            history_lines = []
        for line in history_lines:
            if not isinstance(line, dict) or not str(line.get("text") or "").strip():
                continue
            scene_id = str(line.get("scene_id") or "").strip()
            if not scene_id:
                continue
            key = self._line_summary_key(line)
            if not key:
                continue
            if self._scene_tracker.remember_scene_line(
                scene_id,
                key,
                seq=int(event_seq_by_key.get(key) or 0),
                ts=str(event_ts_by_key.get(key) or line.get("ts") or ""),
            ):
                changed_scene_ids.add(scene_id)

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
            for merged_sid in (merge_ids or []):
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
