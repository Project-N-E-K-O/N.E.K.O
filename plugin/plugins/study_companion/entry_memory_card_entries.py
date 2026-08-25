from __future__ import annotations

from .entry_common import (
    asyncio,
    Err,
    Ok,
    SdkError,
    _entry_exception_error,
    plugin_entry,
    tr,
    MemoryItemNotFoundError,
)


class _MemoryCardEntriesMixin:
    @plugin_entry(
        id="study_memory_card_upsert",
        name=tr("entries.memory_card_upsert.name", default="Upsert Study Memory Card"),
        description=tr(
            "entries.memory_card_upsert.description",
            default="Create or update a spaced-repetition memory card in the study deck.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "front": {"type": "string", "default": ""},
                "back": {"type": "string", "default": ""},
                "topic_id": {"type": "string", "default": ""},
                "subject": {"type": "string", "default": "memory"},
                "chapter": {"type": "string", "default": "memory_deck"},
                "difficulty": {"type": "number", "default": 0.5},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "source": {"type": "string", "default": "manual"},
                "deck_id": {"type": "string", "default": ""},
                "item_type": {
                    "type": "string",
                    "enum": ["word", "sentence", "paragraph", "cloze", "custom"],
                    "default": "custom",
                },
            },
            "required": ["front", "back"],
        },
        llm_result_fields=["created", "card"],
    )
    async def study_memory_card_upsert(
        self,
        front: str = "",
        back: str = "",
        topic_id: str = "",
        subject: str = "memory",
        chapter: str = "memory_deck",
        difficulty: float = 0.5,
        tags: list[str] | None = None,
        source: str = "manual",
        deck_id: str = "",
        item_type: str = "custom",
        **_,
    ):
        try:
            topic_key = str(topic_id or "").strip()
            deck_key = str(deck_id or "").strip()
            normalized_item_type = str(item_type or "").strip().lower()
            if normalized_item_type not in {
                "word",
                "sentence",
                "paragraph",
                "cloze",
                "custom",
            }:
                raise ValueError("unsupported memory item type")
            if deck_key:
                deck = await asyncio.to_thread(
                    self._memory_deck_store.get_deck, deck_key
                )
                if deck is None:
                    raise ValueError("deck not found")
            else:
                deck = await asyncio.to_thread(
                    self._memory_deck_store.get_or_create_default_deck,
                    deck_type="custom",
                )
            result = await asyncio.to_thread(
                self._memory_deck_store.upsert_item,
                deck_id=str(deck.get("id") or ""),
                item_type=normalized_item_type,
                prompt=front,
                answer=back,
                dedupe_metadata_key=("topic_id", "legacy_topic_id")
                if topic_key
                else "",
                dedupe_metadata_value=topic_key,
                metadata={
                    "topic_id": topic_key,
                    "legacy_topic_id": topic_key,
                    "subject": str(subject or "memory"),
                    "chapter": str(chapter or "memory_deck"),
                    "difficulty": 0.5 if difficulty is None else float(difficulty),
                    "tags": tags if isinstance(tags, list) else [],
                    "source": str(source or "manual"),
                },
            )
            item = result.get("item") if isinstance(result, dict) else {}
            return Ok(
                {
                    "created": bool(result.get("created"))
                    if isinstance(result, dict)
                    else False,
                    "card": self._memory_deck_store.compat_card_payload(item),
                    "deck": deck,
                }
            )
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_memory_card_upsert")

    @plugin_entry(
        id="study_memory_card_review",
        name=tr("entries.memory_card_review.name", default="Review Study Memory Card"),
        description=tr(
            "entries.memory_card_review.description",
            default="Grade a study memory card with FSRS ratings: again, hard, good, or easy.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "topic_id": {"type": "string", "default": ""},
                "rating": {
                    "type": "string",
                    "enum": ["again", "hard", "good", "easy"],
                    "default": "good",
                },
                "answer": {"type": "string", "default": ""},
            },
            "required": ["topic_id", "rating"],
        },
        llm_result_fields=["topic_id", "rating", "schedule", "card"],
    )
    async def study_memory_card_review(
        self,
        topic_id: str = "",
        rating: str = "good",
        answer: str = "",
        **kwargs,
    ):
        try:
            target_lanlan = self._resolve_study_target_lanlan(kwargs)
            topic_key = str(topic_id or "").strip()
            exact_item = await asyncio.to_thread(
                self._memory_deck_store.get_item, topic_key
            )
            if exact_item is not None:
                payload, review_completed = await self._run_serialized_review_transition(
                    self._memory_deck_store.review_item,
                    item_id=topic_key,
                    rating=rating,
                )
            else:
                default_deck = await asyncio.to_thread(
                    self._memory_deck_store.get_or_create_default_deck,
                    deck_type="custom",
                )
                try:
                    payload, review_completed = (
                        await self._run_serialized_review_transition(
                            self._memory_deck_store.review_item,
                            item_id=topic_key,
                            rating=rating,
                            deck_id=str(default_deck.get("id") or ""),
                        )
                    )
                except MemoryItemNotFoundError:
                    # Not a memory/custom item: a knowledge-graph topic card surfaced
                    # via study_memory_deck(include_topic_cards=True) is reviewed through
                    # the topic FSRS backend instead. Metadata fallback is intentionally
                    # limited to the default deck; arbitrary custom decks use the stable
                    # item_id contract exposed by study_memory_review_item.
                    knowledge_payload, review_completed = (
                        await self._run_serialized_review_transition(
                            self._knowledge_tracker.review_memory_card,
                            topic_id=topic_key,
                            rating=rating,
                            answer=answer,
                        )
                    )
                    try:
                        if review_completed:
                            card = knowledge_payload.get("card") or {}
                            topic = card.get("topic") or {}
                            await self._emit_review_session_completed_event(
                                reviewed_count=1,
                                deck_name=str(topic.get("name") or ""),
                                target_lanlan=target_lanlan,
                            )
                    except Exception as emit_exc:
                        self.logger.warning(
                            "knowledge card review event emission degraded: {}",
                            emit_exc,
                        )
                    return Ok(knowledge_payload)
            item = payload.get("item") if isinstance(payload, dict) else {}
            try:
                await self._emit_memory_review_answer_event(
                    payload, target_lanlan=target_lanlan
                )
                if review_completed:
                    reviewed_deck = await asyncio.to_thread(
                        self._memory_deck_store.get_deck,
                        str(item.get("deck_id") or ""),
                    )
                    await self._emit_review_session_completed_event(
                        reviewed_count=1,
                        deck_name=str((reviewed_deck or {}).get("name") or ""),
                        target_lanlan=target_lanlan,
                    )
            except Exception as emit_exc:
                self.logger.warning(
                    "memory card review event emission degraded: {}", emit_exc
                )
            return Ok(
                {
                    "topic_id": topic_key,
                    "rating": int(payload.get("rating") or 0)
                    if isinstance(payload, dict)
                    else 0,
                    "answer": str(answer or ""),
                    "schedule": payload.get("schedule")
                    if isinstance(payload, dict)
                    else {},
                    "card": self._memory_deck_store.compat_card_payload(item),
                }
            )
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_memory_card_review")
