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


class _MemoryImportEntriesMixin:
    @ui.action()
    @plugin_entry(
        id="study_memory_import_words",
        name=tr(
            "entries.memory_import_words.name", default="Import Study Memory Words"
        ),
        description=tr(
            "entries.memory_import_words.description",
            default="Import word cards into a memory deck from CSV or JSON.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "deck_id": {"type": "string", "default": ""},
                "content": {"type": "string", "default": ""},
                "fmt": {"type": "string", "enum": ["csv", "json"], "default": "csv"},
            },
            "required": ["deck_id", "content"],
        },
        llm_result_fields=[
            "imported_count",
            "updated_count",
            "skipped_rows",
            "preview",
        ],
    )
    async def study_memory_import_words(
        self, deck_id: str = "", content: str = "", fmt: str = "csv", **_
    ):
        try:
            payload = await asyncio.to_thread(
                self._memory_deck_store.import_words,
                deck_id=deck_id,
                content=content,
                fmt=fmt,
            )
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_memory_import_words")

    @plugin_entry(
        id="study_memory_import_passage",
        name=tr(
            "entries.memory_import_passage.name", default="Import Study Memory Passage"
        ),
        description=tr(
            "entries.memory_import_passage.description",
            default="Split passage text into paragraph memory items and FSRS cards.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "deck_id": {"type": "string", "default": ""},
                "text": {"type": "string", "default": ""},
                "title": {"type": "string", "default": ""},
            },
            "required": ["deck_id", "text"],
        },
        llm_result_fields=["imported_count", "items"],
    )
    async def study_memory_import_passage(
        self, deck_id: str = "", text: str = "", title: str = "", **_
    ):
        try:
            payload = await asyncio.to_thread(
                self._memory_deck_store.import_passage,
                deck_id=deck_id,
                text=text,
                title=title,
            )
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_memory_import_passage")

    @plugin_entry(
        id="study_memory_generate_draft",
        name=tr(
            "entries.memory_generate_draft.name", default="Generate Study Memory Draft"
        ),
        description=tr(
            "entries.memory_generate_draft.description",
            default="Generate a candidate memory draft without saving it to a deck.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "draft_type": {
                    "type": "string",
                    "enum": ["word_example", "sentence_cloze", "recitation_error"],
                    "default": "word_example",
                },
                "word": {"type": "string", "default": ""},
                "meaning": {"type": "string", "default": ""},
                "sentence": {"type": "string", "default": ""},
                "expected": {"type": "string", "default": ""},
                "actual": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["id", "payload", "status"],
    )
    async def study_memory_generate_draft(
        self,
        draft_type: str = "word_example",
        word: str = "",
        meaning: str = "",
        sentence: str = "",
        expected: str = "",
        actual: str = "",
        **_,
    ):
        try:
            normalized = str(draft_type or "word_example")
            if normalized == "sentence_cloze":
                candidate = await asyncio.to_thread(
                    self._memory_deck_store.create_cloze_draft,
                    sentence=sentence,
                )
            elif normalized == "recitation_error":
                candidate = await asyncio.to_thread(
                    self._memory_deck_store.create_recitation_error_draft,
                    expected=expected,
                    actual=actual,
                )
            else:
                candidate = await asyncio.to_thread(
                    self._memory_deck_store.create_word_draft,
                    word=word,
                    meaning=meaning,
                )
            return Ok(candidate)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_memory_generate_draft")
