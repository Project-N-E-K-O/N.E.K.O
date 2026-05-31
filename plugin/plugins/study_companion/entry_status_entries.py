from __future__ import annotations

from .entry_common import (
    asyncio,
    Ok,
    _entry_exception_error,
    plugin_entry,
    tr,
    build_open_ui_payload,
)


class _StatusEntriesMixin:
    @plugin_entry(
        id="study_open_ui",
        name=tr("entries.open_ui.name", default="Open Study Companion UI"),
        description=tr(
            "entries.open_ui.description",
            default="Return the static UI path for study_companion.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["available", "path", "message_key"],
    )
    async def study_open_ui(self, **_):
        return Ok(
            build_open_ui_payload(
                plugin_id=self.plugin_id,
                available=self.get_static_ui_config() is not None,
            )
        )

    @plugin_entry(
        id="study_status",
        name=tr("entries.status.name", default="Study Companion Status"),
        description=tr(
            "entries.status.description",
            default="Return runtime status, dependencies, and recent study interactions.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=[
            "status",
            "active_mode",
            "screen_classification",
            "current_question",
            "last_answer_evaluation",
        ],
    )
    async def study_status(self, **_):
        try:
            payload = await asyncio.to_thread(self._status_payload)
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_status")

    @plugin_entry(
        id="study_neko_communication_status",
        name=tr(
            "entries.neko_communication_status.name",
            default="Neko Communication Status",
        ),
        description=tr(
            "entries.neko_communication_status.description",
            default="Return whether real-time neko communication is active.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["available", "events_emitted", "events_blocked"],
    )
    async def study_neko_communication_status(self, **_):
        bus = self._event_bus
        return Ok(
            {
                "available": bus is not None,
                "events_emitted": bus.emit_count if bus is not None else 0,
                "events_blocked": bus.block_count if bus is not None else 0,
            }
        )

    @plugin_entry(
        id="study_memory_habit_status",
        name=tr(
            "entries.memory_habit_status.name", default="Memory Habit Bridge Status"
        ),
        description=tr(
            "entries.memory_habit_status.description",
            default="Return whether memory deck habit integration is available.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["available", "supports_deck_goals", "supports_deck_focus"],
    )
    async def study_memory_habit_status(self, **_):
        try:
            self._require_habit_components()
            return Ok(self._require_memory_habit_bridge().status())
        except Exception as exc:
            return Ok({"available": False, "error": str(exc)})
