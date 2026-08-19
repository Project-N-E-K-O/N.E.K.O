from __future__ import annotations

from .entry_common import (
    Any,
    asyncio,
    StudyEvent,
    StudyEventBus,
)
from .fsrs_bridge import REVIEW_IS_DUE_AFTER_KEY, REVIEW_WAS_DUE_BEFORE_KEY


def _consume_review_due_transition(payload: Any) -> tuple[bool, bool]:
    if not isinstance(payload, dict):
        return False, False
    marker_payload = payload
    nested_review = payload.get("review")
    if isinstance(nested_review, dict) and REVIEW_WAS_DUE_BEFORE_KEY in nested_review:
        marker_payload = nested_review
    was_due_before = bool(marker_payload.pop(REVIEW_WAS_DUE_BEFORE_KEY, False))
    is_due_after = bool(marker_payload.pop(REVIEW_IS_DUE_AFTER_KEY, False))
    return was_due_before, is_due_after


class _CommunicationReviewEventsMixin:
    def _memory_review_transition_lock(self) -> asyncio.Lock:
        lock = getattr(self, "_memory_review_completion_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._memory_review_completion_lock = lock
        return lock

    async def _run_serialized_review_transition(
        self, operation, /, *args, **kwargs
    ) -> tuple[Any, bool]:
        async with self._memory_review_transition_lock():
            payload = await asyncio.to_thread(operation, *args, **kwargs)
            was_due_before, is_due_after = _consume_review_due_transition(payload)
            completed = False
            if was_due_before and not is_due_after:
                due_after = await asyncio.to_thread(self._count_total_due_reviews)
                completed = due_after == 0
        return payload, completed

    def _resolve_study_target_lanlan(
        self, kwargs: dict[str, Any] | None = None
    ) -> str | None:
        if isinstance(kwargs, dict) and "_ctx" in kwargs:
            ctx_payload = kwargs.get("_ctx")
            if isinstance(ctx_payload, dict):
                lanlan_name = str(ctx_payload.get("lanlan_name") or "").strip()
                return lanlan_name or None
            return None
        ctx = getattr(self, "ctx", None)
        lanlan_name = str(getattr(ctx, "_current_lanlan", "") or "").strip()
        return lanlan_name or None

    def _count_total_due_reviews(self) -> int:
        memory_due_count = int(self._memory_deck_store.count_due_reviews() or 0)
        topic_due_count = int(self._knowledge_tracker.count_due_reviews() or 0)
        return memory_due_count + topic_due_count

    def _require_event_bus(self) -> StudyEventBus:
        if self._event_bus is None:
            raise RuntimeError(
                "Neko communication is not enabled (communication.enabled=false)"
            )
        return self._event_bus

    async def _emit_review_due_if_needed(self) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            loop = asyncio.get_running_loop()
            payload_future = loop.run_in_executor(None, self._build_review_due_payload)
            self._review_due_payload_future = payload_future
            try:
                payload = await asyncio.shield(payload_future)
            finally:
                if payload_future.done() and self._review_due_payload_future is payload_future:
                    self._review_due_payload_future = None
            if not payload:
                return
            payload = {
                **payload,
                "target_lanlan": self._resolve_study_target_lanlan(),
            }
            bus.schedule_emit(StudyEvent(name="review_due", payload=payload))
        except Exception as exc:
            self.logger.warning("study review due event emit failed: {}", exc)

    async def _emit_review_session_completed_event(
        self,
        *,
        reviewed_count: int,
        deck_name: str = "",
        target_lanlan: str | None = None,
    ) -> bool:
        bus = self._event_bus
        if bus is None:
            return False
        try:
            await bus.emit(
                StudyEvent(
                    name="review_session_completed",
                    payload={
                        "reviewed_count": max(1, int(reviewed_count or 1)),
                        "deck_name": str(deck_name or "").strip(),
                        "target_lanlan": str(target_lanlan or "").strip() or None,
                    },
                )
            )
        except Exception:
            self.logger.warning("review completion event delivery failed")
            return False
        return True

    def _build_review_due_payload(self) -> dict[str, Any]:
        due_count = self._count_total_due_reviews()
        if due_count <= 0:
            return {}
        memory_reviews = self._memory_deck_store.due_reviews(limit=50)
        topic_reviews = self._knowledge_tracker.get_review_queue(limit=50)
        urgent_count = self._count_urgent_due(memory_reviews) + self._count_urgent_due(
            topic_reviews
        )
        topics = self._get_due_topics(memory_reviews, topic_reviews)
        return {
            "due_count": due_count,
            "urgent_count": urgent_count,
            "topics": topics,
            "suggestion": (
                f"Suggested review time: "
                f"{max(5, due_count * 2)} minutes for "
                f"{due_count} card(s)."
            ),
        }

    @staticmethod
    def _count_urgent_due(reviews: list[dict[str, Any]]) -> int:
        return sum(1 for item in reviews if float(item.get("overdue_days") or 0.0) > 0)

    def _get_due_topics(
        self,
        memory_reviews: list[dict[str, Any]] | None = None,
        topic_reviews: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        topics: list[str] = []
        memory_items = (
            memory_reviews
            if memory_reviews is not None
            else self._memory_deck_store.due_reviews(limit=50)
        )
        for item in memory_items:
            deck = item.get("deck") or {}
            topic = str(deck.get("name") or item.get("topic_id") or "").strip()
            if topic and topic not in topics:
                topics.append(topic)
            if len(topics) >= 5:
                return topics
        topic_items = (
            topic_reviews
            if topic_reviews is not None
            else self._knowledge_tracker.get_review_queue(limit=50)
        )
        for item in topic_items:
            topic_payload = (
                item.get("topic") if isinstance(item.get("topic"), dict) else {}
            )
            topic = str(
                topic_payload.get("name")
                or topic_payload.get("id")
                or item.get("topic_id")
                or ""
            ).strip()
            if topic and topic not in topics:
                topics.append(topic)
            if len(topics) >= 5:
                return topics
        return topics
