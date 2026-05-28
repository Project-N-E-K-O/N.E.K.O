from __future__ import annotations

from ._common import *  # noqa: F401, F403





class _CheckinEntriesMixin:



    @plugin_entry(
        id="study_checkin_status",
        name="Study Check-In Status",
        description="Return current check-in status and streak.",
        input_schema={
            "type": "object",
            "properties": {"date": {"type": "string", "default": ""}},
        },
        llm_result_fields=["checked_in", "streak_days"],
    )
    async def study_checkin_status(self, date: str = "", **_):
        try:
            _, manager, _, _ = self._require_habit_components()
            target_date = str(date or self._today())[:10]
            return Ok(
                await asyncio.to_thread(
                    manager.checkin_status,
                    date=target_date,
                    today=self._today(),
                )
            )
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_checkin_manual",
        name="Manual Study Check-In",
        description="Record a manual study check-in or makeup check-in.",
        input_schema={
            "type": "object",
            "properties": {
                "date": {"type": "string", "default": ""},
                "note": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["checkin"],
    )
    async def study_checkin_manual(self, date: str = "", note: str = "", **_):
        try:
            _, manager, _, _ = self._require_habit_components()
            checkin = await asyncio.to_thread(
                manager.manual_checkin,
                date=str(date or self._today())[:10],
                today=self._today(),
                note=note,
            )
            return Ok({"checkin": checkin})
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_session_summary",
        name="Study Habit Session Summary",
        description="Return the daily habit summary for focus minutes and goal completion.",
        input_schema={
            "type": "object",
            "properties": {"date": {"type": "string", "default": ""}},
        },
        llm_result_fields=[
            "total_focus_minutes",
            "completed_goal_count",
            "incomplete_goal_count",
        ],
    )
    async def study_session_summary(self, date: str = "", **_):
        try:
            _, manager, _, _ = self._require_habit_components()
            target_date = str(date or self._today())[:10]
            summary = await asyncio.to_thread(
                manager.daily_summary,
                date=target_date,
            )
            if self._memory_habit_bridge is not None:
                summary["memory_summary"] = await asyncio.to_thread(
                    self._memory_habit_bridge.memory_summary,
                    date=target_date,
                )
            return Ok(summary)
        except Exception as exc:
            return Err(SdkError(str(exc)))
