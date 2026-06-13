from __future__ import annotations

from .entry_common import (
    Any,
    asyncio,
    StudyEvent,
    StudyEventBus,
)


class _CommunicationReviewEventsMixin:
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
            bus.schedule_emit(StudyEvent(name="review_due", payload=payload))
        except Exception as exc:
            self.logger.warning("study review due event emit failed: {}", exc)

    def _build_review_due_payload(self) -> dict[str, Any]:
        memory_due_count = int(self._memory_deck_store.count_due_reviews() or 0)
        topic_due_count = int(self._knowledge_tracker.count_due_reviews() or 0)
        due_count = memory_due_count + topic_due_count
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
