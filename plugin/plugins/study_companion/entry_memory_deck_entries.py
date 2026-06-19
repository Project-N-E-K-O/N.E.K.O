from __future__ import annotations

from .entry_common import (
    asyncio,
    Ok,
    _entry_exception_error,
    plugin_entry,
    tr,
    ui,
)


class _MemoryDeckEntriesMixin:
    @plugin_entry(
        id="study_memory_deck",
        name=tr("entries.memory_deck.name", default="Study Memory Deck"),
        description=tr(
            "entries.memory_deck.description",
            default="Return memory cards and due spaced-repetition cards for the study deck.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "due_only": {"type": "boolean", "default": False},
                "include_topic_cards": {"type": "boolean", "default": False},
            },
        },
        llm_result_fields=["card_count", "due_count", "cards"],
    )
    async def study_memory_deck(
        self,
        limit: int = 20,
        due_only: bool = False,
        include_topic_cards: bool = False,
        **_,
    ):
        try:
            safe_limit = max(1, min(200, int(limit or 20)))
            if bool(include_topic_cards):
                topic_cards = await asyncio.to_thread(
                    self._knowledge_tracker.list_memory_cards,
                    limit=safe_limit,
                    due_only=bool(due_only),
                    include_topic_cards=True,
                )
                if bool(due_only):
                    payload = await asyncio.to_thread(
                        self._memory_deck_store.status_summary, limit=safe_limit
                    )
                    due_reviews = (
                        payload.get("due_reviews") if isinstance(payload, dict) else []
                    )
                    due_cards = [
                        self._memory_deck_store.compat_card_payload(
                            item.get("item") or {}
                        )
                        for item in due_reviews
                        if isinstance(item, dict)
                    ]
                    cards = (due_cards + topic_cards)[:safe_limit]
                    topic_due_count = await asyncio.to_thread(
                        self._knowledge_tracker.count_due_reviews
                    )
                    merged = (
                        {k: v for k, v in payload.items() if k != "due_reviews"}
                        if isinstance(payload, dict)
                        else {}
                    )
                    return Ok(
                        {
                            **merged,
                            "card_count": len(cards),
                            "due_count": int(payload.get("due_count") or 0)
                            + int(topic_due_count or 0),
                            "cards": cards,
                            "due_cards": cards,
                        }
                    )
                items = await asyncio.to_thread(
                    self._memory_deck_store.list_items,
                    limit=safe_limit,
                    include_archived=False,
                )
                cards = [
                    self._memory_deck_store.compat_card_payload(item) for item in items
                ] + topic_cards
                due_cards = [item for item in cards if item.get("is_due")]
                cards = cards[:safe_limit]
                return Ok(
                    {
                        "card_count": len(cards),
                        "due_count": len(due_cards),
                        "cards": due_cards if bool(due_only) else cards,
                        "due_cards": due_cards,
                    }
                )
            payload = await asyncio.to_thread(
                self._memory_deck_store.status_summary, limit=safe_limit
            )
            all_items = await asyncio.to_thread(
                self._memory_deck_store.list_items,
                limit=safe_limit,
                include_archived=False,
            )
            due_reviews = (
                payload.get("due_reviews") if isinstance(payload, dict) else []
            )
            due_cards = [
                self._memory_deck_store.compat_card_payload(item.get("item") or {})
                for item in due_reviews
                if isinstance(item, dict)
            ]
            cards = [
                self._memory_deck_store.compat_card_payload(item) for item in all_items
            ]
            payload = {**payload, "cards": cards, "due_cards": due_cards}
            if bool(due_only):
                payload = {**payload, "cards": payload.get("due_cards") or []}
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_memory_deck")

    @ui.action()
    @plugin_entry(
        id="study_memory_create_deck",
        name=tr("entries.memory_create_deck.name", default="Create Study Memory Deck"),
        description=tr(
            "entries.memory_create_deck.description",
            default="Create a word, passage, formula, or custom memory deck.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "default": ""},
                "deck_type": {
                    "type": "string",
                    "enum": ["word", "passage", "formula", "custom"],
                    "default": "custom",
                },
                "subject": {"type": "string", "default": ""},
                "language": {"type": "string", "default": ""},
                "source": {"type": "string", "default": "manual"},
            },
            "required": ["name"],
        },
        llm_result_fields=["id", "name", "deck_type"],
    )
    async def study_memory_create_deck(
        self,
        name: str = "",
        deck_type: str = "custom",
        subject: str = "",
        language: str = "",
        source: str = "manual",
        **_,
    ):
        try:
            deck = await asyncio.to_thread(
                self._memory_deck_store.create_deck,
                name=name,
                deck_type=deck_type,
                subject=subject,
                language=language,
                source=source,
            )
            return Ok(deck)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_memory_create_deck")

    @ui.action()
    @plugin_entry(
        id="study_memory_list_decks",
        name=tr("entries.memory_list_decks.name", default="List Study Memory Decks"),
        description=tr(
            "entries.memory_list_decks.description",
            default="List local memory decks and item counts.",
        ),
        input_schema={
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 100}},
        },
        llm_result_fields=["decks"],
    )
    async def study_memory_list_decks(self, limit: int = 100, **_):
        try:
            decks = await asyncio.to_thread(
                self._memory_deck_store.list_decks,
                limit=max(1, min(500, int(limit or 100))),
            )
            return Ok({"decks": decks})
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_memory_list_decks")

    @ui.action()
    @plugin_entry(
        id="study_memory_delete_deck",
        name=tr("entries.memory_delete_deck.name", default="Delete Study Memory Deck"),
        description=tr(
            "entries.memory_delete_deck.description",
            default="Delete a memory deck and cascade its memory items and review data.",
        ),
        input_schema={
            "type": "object",
            "properties": {"deck_id": {"type": "string", "default": ""}},
            "required": ["deck_id"],
        },
        llm_result_fields=["deleted", "cascade"],
    )
    async def study_memory_delete_deck(self, deck_id: str = "", **_):
        try:
            payload = await asyncio.to_thread(
                self._memory_deck_store.delete_deck, deck_id
            )
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_memory_delete_deck")
