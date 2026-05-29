from __future__ import annotations

from .entry_common import (
    Any,
    asyncio,
    Err,
    Ok,
    SdkError,
    _entry_exception_error,
    plugin_entry,
    build_pomodoro_status_payload,
    _validated_pomodoro_focus_minutes,
)


class _PomodoroEntriesMixin:
    @plugin_entry(
        id="study_pomodoro_status",
        name="Study Pomodoro Status",
        description="Return the current Study Companion pomodoro timer status.",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["state", "mode", "remaining_seconds", "session_count"],
    )
    async def study_pomodoro_status(self, **_):
        try:
            _, _, timer, supervision = self._require_habit_components()
            before_status = await asyncio.to_thread(timer.status)
            before_state = str(before_status.get("state") or "")
            status = await asyncio.to_thread(timer.tick)
            after_state = str(status.get("state") or "")
            reminder: dict[str, Any] = {}
            if before_state == "focusing" and after_state in {
                "short_break",
                "long_break",
                "completed",
            }:
                supervision.on_focus_end()
            elif after_state == "focusing":
                reminder = supervision.due_reminder()
            payload = build_pomodoro_status_payload(status)
            if reminder:
                payload["supervision_reminder"] = reminder
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_pomodoro_status")

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
        **_,
    ):
        try:
            habits, _, timer, supervision = self._require_habit_components()
            planned_focus_minutes = _validated_pomodoro_focus_minutes(
                self._cfg, focus_minutes
            )
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
            return Ok(build_pomodoro_status_payload(status))
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_pomodoro_start")

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
            status = await asyncio.to_thread(timer.pause)
            return Ok(build_pomodoro_status_payload(status))
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_pomodoro_pause")

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
            status = await asyncio.to_thread(timer.resume)
            return Ok(build_pomodoro_status_payload(status))
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_pomodoro_resume")

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
            status = await asyncio.to_thread(timer.stop)
            supervision.on_focus_end()
            return Ok(build_pomodoro_status_payload(status))
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_pomodoro_stop")

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
            status = await asyncio.to_thread(timer.skip_break)
            return Ok(build_pomodoro_status_payload(status))
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_pomodoro_skip_break")
