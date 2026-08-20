from __future__ import annotations

from .entry_common import (
    Any,
    asyncio,
    StudyEvent,
    _event_ratio,
    _event_nonnegative_float,
)
from ._general_narration import prepare_general_narration_content


class _CommunicationTutorEventsMixin:
    async def _emit_general_response_completed_event(
        self,
        *,
        response_mode: str,
        content: str,
        target_lanlan: str | None = None,
    ) -> bool:
        normalized_mode = str(response_mode or "").strip()
        if normalized_mode not in {
            "general_explanation",
            "general_discussion",
            "general_fallback",
        }:
            return False
        prepared_content = prepare_general_narration_content(content)
        if not prepared_content:
            return False
        bus = self._event_bus
        if bus is None:
            return False
        payload = {"response_mode": normalized_mode, "content": prepared_content}
        if normalized_target := str(target_lanlan or "").strip():
            payload["target_lanlan"] = normalized_target
        event = StudyEvent(name="general_response_completed", payload=payload)
        schedule_emit = getattr(bus, "schedule_emit", None)
        if callable(schedule_emit):
            try:
                return schedule_emit(event) is not None
            except Exception:
                self.logger.warning("general narration event delivery failed")
                return False
        try:
            await bus.emit(event)
        except Exception:
            self.logger.warning("general narration event delivery failed")
            return False
        return True

    async def _emit_solution_completed_event(
        self, sections: dict[str, str], *, target_lanlan: str | None = None
    ) -> bool:
        bus = self._event_bus
        if bus is None:
            return False
        payload = {
            "analysis": str(sections.get("analysis") or "").strip(),
            "answer": str(sections.get("answer") or "").strip(),
            "transfer": str(sections.get("transfer") or "").strip(),
        }
        if any(not payload[key] for key in ("analysis", "answer", "transfer")):
            return False
        if normalized_target := str(target_lanlan or "").strip():
            payload["target_lanlan"] = normalized_target
        try:
            scheduled = bus.schedule_emit(
                StudyEvent(name="solution_completed", payload=payload)
            )
        except Exception:
            self.logger.warning("solution narration event delivery failed")
            return False
        return scheduled is not None

    async def _emit_answer_evaluated_event(
        self,
        *,
        verdict: str,
        score: Any,
        question_summary: str,
        user_answer_summary: str,
        correction_hint: str = "",
        topic: str = "",
        mastery_before: float = -1.0,
        mastery_after: float = -1.0,
        target_lanlan: str | None = None,
    ) -> None:
        bus = self._event_bus
        if bus is None:
            return
        bus.schedule_emit(
            StudyEvent(
                name="answer_evaluated",
                payload={
                    "verdict": str(verdict or "").strip(),
                    "score": _event_ratio(score),
                    "question_summary": str(question_summary or "").strip()[:200],
                    "user_answer_summary": str(user_answer_summary or "").strip()[:200],
                    "correction_hint": str(correction_hint or "").strip()[:200],
                    "topic": str(topic or "").strip(),
                    "mastery_before": mastery_before,
                    "mastery_after": mastery_after,
                    "target_lanlan": str(target_lanlan or "").strip() or None,
                },
            )
        )

    async def _emit_memory_review_answer_event(
        self, payload: dict[str, Any], *, target_lanlan: str | None = None
    ) -> None:
        item = payload.get("item") or {}
        review = payload.get("review_record") or {}
        deck = (
            await asyncio.to_thread(
                self._memory_deck_store.get_deck, str(item.get("deck_id") or "")
            )
            or {}
        )
        correct = bool(review.get("correct"))
        rating = payload.get("rating") or review.get("rating") or ""
        await self._emit_answer_evaluated_event(
            verdict="correct" if correct else "incorrect",
            score=1.0 if correct else 0.0,
            question_summary=str(item.get("prompt") or item.get("front") or ""),
            user_answer_summary=f"rating={rating}",
            correction_hint=str(review.get("error_type") or ""),
            topic=str(deck.get("subject") or deck.get("name") or ""),
            target_lanlan=target_lanlan,
        )

    async def _emit_recitation_answer_event(
        self, payload: dict[str, Any], *, target_lanlan: str | None = None
    ) -> None:
        diff_data = payload.get("diff") or {}
        review = payload.get("review") or {}
        item = review.get("item") or {}
        deck = (
            await asyncio.to_thread(
                self._memory_deck_store.get_deck, str(item.get("deck_id") or "")
            )
            or {}
        )
        score = _event_ratio(diff_data.get("score"))
        if score >= 0.8:
            verdict = "correct"
        elif score >= 0.5:
            verdict = "partial"
        else:
            verdict = "incorrect"
        attempt = payload.get("attempt") or {}
        await self._emit_answer_evaluated_event(
            verdict=verdict,
            score=score,
            question_summary=str(item.get("prompt") or item.get("front") or ""),
            user_answer_summary=str(attempt.get("user_input_text") or ""),
            correction_hint=(
                f"Missing: {diff_data.get('missing_count', 0)}, "
                f"extra: {diff_data.get('extra_count', 0)}"
            ),
            topic=str(deck.get("subject") or deck.get("name") or ""),
            target_lanlan=target_lanlan,
        )

    async def _emit_session_summarized_event(self, payload: dict[str, Any]) -> None:
        bus = self._event_bus
        if bus is None:
            return
        async with self._lock:
            seed = dict(self._state.session_summary_seed)
        answer_count = int(seed.get("answer_count") or 0)
        verdict_counts = dict(seed.get("verdict_counts") or {})
        correct = int(verdict_counts.get("correct") or 0)
        fallback_correct_rate = (correct / answer_count) if answer_count > 0 else 0.0
        bus.schedule_emit(
            StudyEvent(
                name="session_summarized",
                payload={
                    "duration_minutes": _event_nonnegative_float(
                        payload.get("duration_minutes"), 0.0
                    ),
                    "questions_attempted": int(
                        _event_nonnegative_float(
                            payload.get("questions_attempted"), float(answer_count)
                        )
                    ),
                    "correct_rate": _event_ratio(
                        payload.get("correct_rate", fallback_correct_rate)
                    ),
                    "topics_studied": [seed.get("last_topic")]
                    if seed.get("last_topic")
                    else [],
                    "key_insight": str(
                        payload.get("key_insight")
                        or payload.get("summary")
                        or payload.get("reply")
                        or ""
                    ).strip()[:240],
                },
            )
        )
