from __future__ import annotations

from .entry_common import (
    asyncio,
    Err,
    Ok,
    SdkError,
    _entry_exception_error,
    plugin_entry,
    tr,
    ui,
)


class _GoalEntriesMixin:
    @ui.action()
    @plugin_entry(
        id="study_goals",
        name="Study Daily Goals",
        description="Return daily study habit goals for a date.",
        input_schema={
            "type": "object",
            "properties": {"date": {"type": "string", "default": ""}},
        },
        llm_result_fields=["goals"],
    )
    async def study_goals(self, date: str = "", **_):
        try:
            habits, _, _, _ = self._require_habit_components()
            target_date = str(date or self._today())[:10]
            return Ok(
                {
                    "date": target_date,
                    "goals": await asyncio.to_thread(
                        habits.list_goals, date=target_date
                    ),
                }
            )
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_goals")

    @ui.action()
    @plugin_entry(
        id="study_goal_create",
        name="Create Study Daily Goal",
        description="Create a local daily study goal.",
        input_schema={
            "type": "object",
            "properties": {
                "date": {"type": "string", "default": ""},
                "target_type": {"type": "string", "default": "custom"},
                "target_id": {"type": "string", "default": ""},
                "subject": {"type": "string", "default": ""},
                "target_amount": {"type": "number", "default": 1},
                "unit": {"type": "string", "default": "task"},
            },
        },
        llm_result_fields=["goal"],
    )
    async def study_goal_create(
        self,
        date: str = "",
        target_type: str = "custom",
        target_id: str = "",
        subject: str = "",
        target_amount: float = 1,
        unit: str = "task",
        **_,
    ):
        try:
            _, manager, _, _ = self._require_habit_components()
            goal = await asyncio.to_thread(
                manager.create_goal,
                date=str(date or self._today())[:10],
                target_type=target_type,
                target_id=target_id,
                subject=subject,
                target_amount=target_amount,
                unit=unit,
            )
            return Ok({"goal": goal})
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_goal_create")

    @plugin_entry(
        id="study_goal_update",
        name="Update Study Daily Goal",
        description="Update a local daily study goal.",
        input_schema={
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "target_amount": {"type": "number"},
                "progress_amount": {"type": "number"},
                "status": {"type": "string"},
            },
            "required": ["goal_id"],
        },
        llm_result_fields=["goal"],
    )
    async def study_goal_update(
        self,
        goal_id: str,
        target_amount: float | None = None,
        progress_amount: float | None = None,
        status: str | None = None,
        **_,
    ):
        try:
            _, manager, _, _ = self._require_habit_components()
            goal = await asyncio.to_thread(
                manager.update_goal,
                goal_id,
                target_amount=target_amount,
                progress_amount=progress_amount,
                status=status,
            )
            return Ok({"goal": goal})
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_goal_update")

    @ui.action()
    @plugin_entry(
        id="study_goal_delete",
        name="Delete Study Daily Goal",
        description="Delete a local daily study goal and associated focus sessions.",
        input_schema={
            "type": "object",
            "properties": {"goal_id": {"type": "string"}},
            "required": ["goal_id"],
        },
        llm_result_fields=["deleted"],
    )
    async def study_goal_delete(self, goal_id: str, **_):
        try:
            _, manager, _, _ = self._require_habit_components()
            deleted = await asyncio.to_thread(manager.delete_goal, goal_id)
            return Ok({"deleted": bool(deleted), "goal_id": goal_id})
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_goal_delete")

    @ui.action()
    @plugin_entry(
        id="study_memory_set_deck_goal",
        name=tr("entries.memory_set_deck_goal.name", default="Set Memory Deck Goal"),
        description=tr(
            "entries.memory_set_deck_goal.description",
            default="Create or update today's daily goal for a memory deck.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "deck_id": {"type": "string"},
                "date": {"type": "string", "default": ""},
                "target_amount": {"type": "number", "default": 10},
                "unit": {
                    "type": "string",
                    "enum": ["cards", "minutes", "attempts"],
                    "default": "cards",
                },
            },
            "required": ["deck_id"],
        },
        llm_result_fields=["goal", "deck", "created"],
    )
    async def study_memory_set_deck_goal(
        self,
        deck_id: str,
        date: str = "",
        target_amount: float = 10,
        unit: str = "cards",
        **_,
    ):
        try:
            self._require_habit_components()
            bridge = self._require_memory_habit_bridge()
            payload = await asyncio.to_thread(
                bridge.create_deck_goal,
                date=str(date or self._today())[:10],
                deck_id=deck_id,
                target_amount=target_amount,
                unit=unit,
            )
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_memory_set_deck_goal")
