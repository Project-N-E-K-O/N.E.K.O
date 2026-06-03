from __future__ import annotations

from .entry_common import (
    asyncio,
    Ok,
    _entry_exception_error,
    plugin_entry,
    tr,
)


class _MemoryReviewEntriesMixin:
    @plugin_entry(
        id="study_memory_due_reviews",
        name=tr("entries.memory_due_reviews.name", default="Study Memory Due Reviews"),
        description=tr(
            "entries.memory_due_reviews.description",
            default="Return due memory reviews sorted by deck and retrievability.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "deck_id": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 50},
                "item_type": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["due_reviews"],
    )
    async def study_memory_due_reviews(
        self, deck_id: str = "", limit: int = 50, item_type: str = "", **_
    ):
        try:
            reviews = await asyncio.to_thread(
                self._memory_deck_store.due_reviews,
                deck_id=deck_id,
                limit=max(1, min(500, int(limit or 50))),
                item_type=item_type,
            )
            return Ok({"due_reviews": reviews})
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_memory_due_reviews")

    @plugin_entry(
        id="study_memory_review_item",
        name=tr("entries.memory_review_item.name", default="Review Study Memory Item"),
        description=tr(
            "entries.memory_review_item.description",
            default="Record a memory item review and update its dedicated FSRS card.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "default": ""},
                "rating": {
                    "type": "string",
                    "enum": ["again", "hard", "good", "easy"],
                },
                "correct": {"type": "boolean"},
                "error_type": {"type": "string", "default": ""},
                "elapsed_ms": {"type": "integer", "default": 0},
                "session_id": {"type": "string", "default": ""},
            },
            "required": ["item_id"],
        },
        llm_result_fields=["item", "rating", "schedule", "review_record"],
    )
    async def study_memory_review_item(
        self,
        item_id: str = "",
        rating: str | None = None,
        correct: bool | None = None,
        error_type: str = "",
        elapsed_ms: int = 0,
        session_id: str = "",
        **_,
    ):
        try:
            payload = await asyncio.to_thread(
                self._memory_deck_store.review_item,
                item_id=item_id,
                rating=rating,
                correct=correct if isinstance(correct, bool) else None,
                error_type=error_type,
                elapsed_ms=int(elapsed_ms or 0) or None,
                session_id=session_id,
            )
            if self._memory_habit_bridge is not None:
                try:
                    payload["habit_progress"] = await asyncio.to_thread(
                        self._memory_habit_bridge.apply_review_progress,
                        payload,
                        date=self._today(),
                    )
                except Exception as bridge_exc:
                    self.logger.warning(
                        f"memory habit review progress degraded: {bridge_exc}"
                    )
                    payload["habit_progress"] = {
                        "applied": 0,
                        "error": str(bridge_exc),
                    }
            try:
                await self._emit_memory_review_answer_event(payload)
            except Exception as emit_exc:
                self.logger.warning(
                    "memory review event emission degraded: {}", emit_exc
                )
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_memory_review_item")

    @plugin_entry(
        id="study_memory_recitation_attempt",
        name=tr(
            "entries.memory_recitation_attempt.name",
            default="Submit Study Memory Recitation",
        ),
        description=tr(
            "entries.memory_recitation_attempt.description",
            default="Diff a passage recitation attempt and record the resulting FSRS review.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "default": ""},
                "user_input_text": {"type": "string", "default": ""},
                "hint_count": {"type": "integer", "default": 0},
                "elapsed_ms": {"type": "integer", "default": 0},
                "session_id": {"type": "string", "default": ""},
            },
            "required": ["item_id", "user_input_text"],
        },
        llm_result_fields=["attempt", "diff", "review"],
    )
    async def study_memory_recitation_attempt(
        self,
        item_id: str = "",
        user_input_text: str = "",
        hint_count: int = 0,
        elapsed_ms: int = 0,
        session_id: str = "",
        **_,
    ):
        try:
            payload = await asyncio.to_thread(
                self._memory_deck_store.add_recitation_attempt,
                item_id=item_id,
                user_input_text=user_input_text,
                hint_count=max(0, int(hint_count or 0)),
                elapsed_ms=int(elapsed_ms or 0) or None,
                session_id=session_id,
            )
            if self._memory_habit_bridge is not None:
                try:
                    payload["habit_progress"] = await asyncio.to_thread(
                        self._memory_habit_bridge.apply_recitation_progress,
                        payload,
                        date=self._today(),
                    )
                except Exception as bridge_exc:
                    self.logger.warning(
                        f"memory habit recitation progress degraded: {bridge_exc}"
                    )
                    payload["habit_progress"] = {
                        "applied": 0,
                        "error": str(bridge_exc),
                    }
            try:
                await self._emit_recitation_answer_event(payload)
            except Exception as emit_exc:
                self.logger.warning(
                    "memory recitation event emission degraded: {}", emit_exc
                )
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_memory_recitation_attempt")
