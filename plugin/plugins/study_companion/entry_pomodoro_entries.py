from __future__ import annotations

from .entry_common import (
    Any,
    asyncio,
    Err,
    Ok,
    SdkError,
    _entry_exception_error,
    plugin_entry,
    ui,
    build_pomodoro_status_payload,
    _validated_pomodoro_focus_minutes,
)


class _PomodoroEntriesMixin:
    @ui.action()
    @plugin_entry(
        id="study_pomodoro_status",
        name="Study Pomodoro Status",
        description="Return the current Study Companion pomodoro timer status.",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["state", "mode", "remaining_seconds", "session_count"],
    )
    async def study_pomodoro_status(self, **_):
        try:
            _, _, _, supervision = self._require_habit_components()
            async with self._pomodoro_runtime_lock():
                status, transition = await self._tick_pomodoro_timer_locked()
            await self._emit_pomodoro_transition(transition)
            reminder: dict[str, Any] = {}
            if str(status.get("state") or "") == "focusing":
                reminder = supervision.due_reminder()
            payload = build_pomodoro_status_payload(status)
            if reminder:
                payload["supervision_reminder"] = reminder
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_pomodoro_status")

    @ui.action()
    @plugin_entry(
        id="study_pomodoro_start",
        name="Start Study Pomodoro",
        description=(
            "Start a focus pomodoro. goal_id is used as-is when provided; "
            "deck_id resolves a memory deck minutes goal only when goal_id is empty."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "focus_minutes": {
                    "type": "integer",
                    "description": "Focus duration in minutes.",
                },
                "goal_id": {
                    "type": "string",
                    "default": "",
                    "description": "Existing daily goal id. Takes precedence over deck_id.",
                },
                "deck_id": {
                    "type": "string",
                    "default": "",
                    "description": (
                        "Memory deck id used to create or reuse a minutes goal when "
                        "goal_id is empty."
                    ),
                },
            },
        },
        llm_result_fields=["state", "remaining_seconds", "goal_id"],
    )
    async def study_pomodoro_start(
        self,
        focus_minutes: int | None = None,
        goal_id: str = "",
        deck_id: str = "",
        **kwargs,
    ):
        try:
            habits, _, timer, supervision = self._require_habit_components()
            planned_focus_minutes = _validated_pomodoro_focus_minutes(
                self._cfg, focus_minutes
            )
            async with self._pomodoro_runtime_lock():
                before_status = await asyncio.to_thread(timer.status)
                before_session_id = str(
                    before_status.get("current_focus_session", {}).get("id") or ""
                )
                before_state = str(before_status.get("state") or "")
                if (
                    deck_id
                    and not goal_id
                    and before_state
                    not in {"focusing", "paused", "short_break", "long_break"}
                ):
                    bridge = self._require_memory_habit_bridge()
                    goal_payload = await asyncio.to_thread(
                        bridge.resolve_focus_goal,
                        date=self._today(),
                        deck_id=deck_id,
                        focus_minutes=float(planned_focus_minutes),
                    )
                    goal_id = str((goal_payload.get("goal") or {}).get("id") or "")
                status = await asyncio.to_thread(
                    timer.start, goal_id=goal_id, focus_minutes=planned_focus_minutes
                )
                after_session_id = str(
                    status.get("current_focus_session", {}).get("id") or ""
                )
                if (
                    str(status.get("state") or "") == "focusing"
                    and after_session_id
                    and after_session_id != before_session_id
                ):
                    self._pomodoro_session_id = after_session_id
                    self._pomodoro_target_lanlan = self._resolve_pomodoro_target_lanlan(
                        kwargs
                    )
                    goal = (
                        await asyncio.to_thread(habits.get_goal, str(goal_id or ""))
                        if goal_id
                        else {}
                    )
                    status_config = status.get("config")
                    status_focus_minutes = (
                        status_config.get("focus_minutes")
                        if isinstance(status_config, dict)
                        else None
                    )
                    supervision.on_focus_start(
                        goal=goal or {},
                        planned_minutes=float(
                            status_focus_minutes
                            if status_focus_minutes is not None
                            else (
                                focus_minutes
                                if focus_minutes is not None
                                else planned_focus_minutes
                            )
                        ),
                    )
            self._wake_pomodoro_watcher()
            return Ok(build_pomodoro_status_payload(status))
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_pomodoro_start")

    @ui.action()
    @plugin_entry(
        id="study_pomodoro_pause",
        name="Pause Study Pomodoro",
        description="Pause the active focus pomodoro.",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["state", "remaining_seconds"],
    )
    async def study_pomodoro_pause(self, **_):
        try:
            _, _, timer, _ = self._require_habit_components()
            transition: dict[str, Any] | None = None
            async with self._pomodoro_runtime_lock():
                status, transition = await self._tick_pomodoro_timer_locked()
                if str(status.get("state") or "") == "focusing":
                    status = await asyncio.to_thread(timer.pause)
            await self._emit_pomodoro_transition(transition)
            self._wake_pomodoro_watcher()
            return Ok(build_pomodoro_status_payload(status))
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_pomodoro_pause")

    @ui.action()
    @plugin_entry(
        id="study_pomodoro_resume",
        name="Resume Study Pomodoro",
        description="Resume a paused focus pomodoro.",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["state", "remaining_seconds"],
    )
    async def study_pomodoro_resume(self, **_):
        try:
            _, _, timer, _ = self._require_habit_components()
            async with self._pomodoro_runtime_lock():
                status = await asyncio.to_thread(timer.resume)
            self._wake_pomodoro_watcher()
            return Ok(build_pomodoro_status_payload(status))
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_pomodoro_resume")

    @ui.action()
    @plugin_entry(
        id="study_pomodoro_stop",
        name="Stop Study Pomodoro",
        description="Stop the active focus or break timer.",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["state", "current_focus_session"],
    )
    async def study_pomodoro_stop(self, **_):
        try:
            _, _, timer, supervision = self._require_habit_components()
            transition: dict[str, Any] | None = None
            async with self._pomodoro_runtime_lock():
                before_status = await asyncio.to_thread(timer.status)
                before_state = str(before_status.get("state") or "")
                status = await asyncio.to_thread(timer.stop)
                after_state = str(status.get("state") or "")
                if before_state == "focusing" and after_state in {
                    "short_break",
                    "long_break",
                }:
                    transition = {
                        "name": "pomodoro_focus_completed",
                        "payload": {
                            "session_id": str(
                                status.get("current_focus_session", {}).get("id")
                                or getattr(self, "_pomodoro_session_id", "")
                                or ""
                            ),
                            "break_type": after_state,
                            "target_lanlan": getattr(
                                self, "_pomodoro_target_lanlan", None
                            ),
                        },
                    }
                else:
                    self._pomodoro_session_id = ""
                    self._pomodoro_target_lanlan = None
            supervision.on_focus_end()
            await self._emit_pomodoro_transition(transition)
            self._wake_pomodoro_watcher()
            return Ok(build_pomodoro_status_payload(status))
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_pomodoro_stop")

    @ui.action()
    @plugin_entry(
        id="study_pomodoro_skip_break",
        name="Skip Study Pomodoro Break",
        description="Skip the current short or long break when allowed.",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["state", "remaining_seconds"],
    )
    async def study_pomodoro_skip_break(self, **_):
        try:
            _, _, timer, _ = self._require_habit_components()
            async with self._pomodoro_runtime_lock():
                status = await asyncio.to_thread(timer.skip_break)
                if str(status.get("state") or "") == "completed":
                    self._pomodoro_session_id = ""
                    self._pomodoro_target_lanlan = None
            self._wake_pomodoro_watcher()
            return Ok(build_pomodoro_status_payload(status))
        except Exception as exc:
            return _entry_exception_error(
                self, exc, operation="study_pomodoro_skip_break"
            )
