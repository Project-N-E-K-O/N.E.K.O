from __future__ import annotations

from dataclasses import asdict
from typing import Any

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


def _note_dict(note: Any) -> dict[str, Any]:
    return asdict(note) if hasattr(note, "__dataclass_fields__") else dict(note or {})


def _notebook_dict(notebook: Any) -> dict[str, Any]:
    return (
        asdict(notebook)
        if hasattr(notebook, "__dataclass_fields__")
        else dict(notebook or {})
    )


class _NotebookEntriesMixin:
    @ui.action()
    @plugin_entry(
        id="study_notebook_create",
        name=tr("entries.notebook_create.name", default="Create Study Notebook"),
        description=tr(
            "entries.notebook_create.description",
            default="Create a folder-like notebook for study notes.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string", "default": ""},
                "sort_order": {"type": "integer", "default": 0},
            },
            "required": ["name"],
        },
        llm_result_fields=["notebook"],
    )
    async def study_notebook_create(
        self, name: str, description: str = "", sort_order: int = 0, **_
    ):
        try:
            notebook = await asyncio.to_thread(
                self._notebook_store.create_notebook,
                name=name,
                description=description,
                sort_order=sort_order,
            )
            return Ok({"notebook": _notebook_dict(notebook)})
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_notebook_create")

    @ui.action()
    @plugin_entry(
        id="study_notebook_list",
        name=tr("entries.notebook_list.name", default="List Study Notebooks"),
        description=tr(
            "entries.notebook_list.description",
            default="List study notebooks with note counts.",
        ),
        input_schema={
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 100}},
        },
        llm_result_fields=["notebooks"],
    )
    async def study_notebook_list(self, limit: int = 100, **_):
        try:
            notebooks = await asyncio.to_thread(
                self._notebook_store.list_notebooks, limit=limit
            )
            return Ok({"notebooks": [_notebook_dict(item) for item in notebooks]})
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_notebook_list")

    @plugin_entry(
        id="study_notebook_update",
        name=tr("entries.notebook_update.name", default="Update Study Notebook"),
        description=tr(
            "entries.notebook_update.description",
            default="Rename or reorder a study notebook.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "notebook_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "sort_order": {"type": "integer"},
            },
            "required": ["notebook_id"],
        },
        llm_result_fields=["notebook"],
    )
    async def study_notebook_update(
        self,
        notebook_id: str,
        name: str | None = None,
        description: str | None = None,
        sort_order: int | None = None,
        **_,
    ):
        try:
            notebook = await asyncio.to_thread(
                self._notebook_store.update_notebook,
                notebook_id,
                name=name,
                description=description,
                sort_order=sort_order,
            )
            return Ok({"notebook": _notebook_dict(notebook)})
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_notebook_update")

    @plugin_entry(
        id="study_notebook_delete",
        name=tr("entries.notebook_delete.name", default="Delete Study Notebook"),
        description=tr(
            "entries.notebook_delete.description",
            default="Delete a study notebook and keep its notes unfiled.",
        ),
        input_schema={
            "type": "object",
            "properties": {"notebook_id": {"type": "string"}},
            "required": ["notebook_id"],
        },
        llm_result_fields=["deleted", "notes_unlinked"],
    )
    async def study_notebook_delete(self, notebook_id: str, **_):
        try:
            payload = await asyncio.to_thread(
                self._notebook_store.delete_notebook, notebook_id
            )
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_notebook_delete")

    @ui.action()
    @plugin_entry(
        id="study_note_upsert",
        name=tr("entries.note_upsert.name", default="Save Study Note"),
        description=tr(
            "entries.note_upsert.description",
            default="Create or update a Markdown study note.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "note_id": {"type": "string", "default": ""},
                # No defaults on update fields: an omitted field (partial edit)
                # is left unchanged by upsert_note, instead of a generated form's
                # "" / [] / false silently overwriting title/content/topics/tags.
                # An explicit "" notebook_id is still an intentional unfile.
                "notebook_id": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "topic_ids": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "is_ai_generated": {"type": "boolean"},
                "source_type": {"type": "string", "default": "manual"},
                "source_ref": {"type": "string", "default": ""},
            },
        },
        timeout=20.0,
        llm_result_fields=["note"],
    )
    async def study_note_upsert(
        self,
        note_id: str = "",
        notebook_id: str | None = None,
        title: str | None = None,
        content: str | None = None,
        topic_ids: list[str] | None = None,
        tags: list[str] | None = None,
        is_ai_generated: bool | None = None,
        source_type: str | None = None,
        source_ref: str | None = None,
        **_,
    ):
        try:
            note_key = str(note_id or "").strip()
            update_kwargs = {"note_id": note_key or None}
            # None = field omitted (partial edit) → leave filing unchanged;
            # "" = explicit unfile (e.g. the editor's cleared Notebook field) →
            # pass None to upsert_note; a real id files into that notebook.
            if notebook_id is not None:
                update_kwargs["notebook_id"] = notebook_id or None
            if title is not None:
                update_kwargs["title"] = title
            if content is not None:
                update_kwargs["content"] = content
            if topic_ids is not None:
                update_kwargs["topic_ids"] = topic_ids
            if tags is not None:
                update_kwargs["tags"] = tags
            if is_ai_generated is not None:
                update_kwargs["is_ai_generated"] = is_ai_generated
            if not note_key:
                update_kwargs["source_type"] = source_type or "manual"
                update_kwargs["source_ref"] = source_ref or ""
            elif (
                source_type is not None
                and str(source_type or "").strip() not in {"", "manual"}
            ) or (source_ref is not None and str(source_ref or "").strip()):
                update_kwargs["source_type"] = source_type
                update_kwargs["source_ref"] = source_ref
            note = await asyncio.to_thread(
                self._notebook_store.upsert_note,
                **update_kwargs,
            )
            return Ok({"note": _note_dict(note)})
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_note_upsert")

    @ui.action()
    @plugin_entry(
        id="study_note_get",
        name=tr("entries.note_get.name", default="Get Study Note"),
        description=tr(
            "entries.note_get.description",
            default="Return a single study note by id.",
        ),
        input_schema={
            "type": "object",
            "properties": {"note_id": {"type": "string"}},
            "required": ["note_id"],
        },
        llm_result_fields=["note"],
    )
    async def study_note_get(self, note_id: str, **_):
        try:
            note = await asyncio.to_thread(self._notebook_store.get_note, note_id)
            if note is None:
                return Err(SdkError("study note not found", code="NOTE_NOT_FOUND"))
            return Ok({"note": _note_dict(note)})
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_note_get")

    @ui.action()
    @plugin_entry(
        id="study_note_delete",
        name=tr("entries.note_delete.name", default="Delete Study Note"),
        description=tr(
            "entries.note_delete.description",
            default="Delete a study note.",
        ),
        input_schema={
            "type": "object",
            "properties": {"note_id": {"type": "string"}},
            "required": ["note_id"],
        },
        llm_result_fields=["deleted"],
    )
    async def study_note_delete(self, note_id: str, **_):
        try:
            payload = await asyncio.to_thread(self._notebook_store.delete_note, note_id)
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_note_delete")

    @ui.action()
    @plugin_entry(
        id="study_note_list",
        name=tr("entries.note_list.name", default="List Study Notes"),
        description=tr(
            "entries.note_list.description",
            default="List study notes by notebook, topic, tag, or query.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "notebook_id": {"type": "string", "default": ""},
                "notebook_filter": {
                    "type": "string",
                    "enum": ["all", "unfiled", "specific"],
                    "default": "all",
                },
                "topic_id": {"type": "string", "default": ""},
                "tag": {"type": "string", "default": ""},
                "search_query": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 50},
            },
        },
        llm_result_fields=["notes"],
    )
    async def study_note_list(
        self,
        notebook_id: str = "",
        notebook_filter: str = "all",
        topic_id: str = "",
        tag: str = "",
        search_query: str = "",
        limit: int = 50,
        **_,
    ):
        try:
            kwargs: dict[str, Any] = {
                "notebook_filter": notebook_filter,
                "topic_id": topic_id or None,
                "tag": tag or None,
                "search_query": search_query,
                "limit": limit,
            }
            if notebook_id:
                kwargs["notebook_id"] = notebook_id
            notes = await asyncio.to_thread(self._notebook_store.list_notes, **kwargs)
            return Ok({"notes": [_note_dict(note) for note in notes]})
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_note_list")

    @ui.action()
    @plugin_entry(
        id="study_note_search_all",
        name=tr("entries.note_search_all.name", default="Search Study Notes"),
        description=tr(
            "entries.note_search_all.description",
            default="Search notes, topics, sessions, and wrong questions.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
        llm_result_fields=["notes", "topics", "sessions", "wrong_questions"],
    )
    async def study_note_search_all(self, query: str, limit: int = 20, **_):
        try:
            payload = await asyncio.to_thread(
                self._notebook_store.search_all, query, limit=limit
            )
            return Ok(dict(payload))
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_note_search_all")

    @ui.action()
    @plugin_entry(
        id="study_note_ai_expand",
        name=tr("entries.note_ai_expand.name", default="Expand Study Note"),
        description=tr(
            "entries.note_ai_expand.description",
            default="Use the tutor model to expand a note without saving the result.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "note_id": {"type": "string", "default": ""},
                "content": {"type": "string", "default": ""},
                "topic_context": {"type": "string", "default": ""},
                "expand_scope": {"type": "string", "default": "details"},
            },
        },
        timeout=75.0,
        llm_result_fields=["content", "markdown", "degraded", "diagnostic"],
    )
    async def study_note_ai_expand(
        self,
        note_id: str = "",
        content: str = "",
        topic_context: str = "",
        expand_scope: str = "details",
        **_,
    ):
        try:
            if self._agent is None:
                return Err(SdkError("study tutor agent is not initialized"))
            source_content = str(content or "")
            if note_id and not source_content.strip():
                note = await asyncio.to_thread(self._notebook_store.get_note, note_id)
                if note is None:
                    return Err(SdkError("study note not found", code="NOTE_NOT_FOUND"))
                source_content = note.content
                if not topic_context and note.topic_ids:
                    topic_context = ", ".join(note.topic_ids)
            if not source_content.strip():
                return Err(SdkError("study note content is required", code="MISSING_TEXT"))
            reply = await self._agent.expand_note(
                source_content,
                topic_context=topic_context,
                expand_scope=expand_scope,
            )
            return Ok(
                {
                    "content": reply.reply,
                    "markdown": reply.reply,
                    "degraded": reply.degraded,
                    "diagnostic": reply.diagnostic,
                }
            )
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_note_ai_expand")

    @plugin_entry(
        id="study_note_ai_generate",
        name=tr("entries.note_ai_generate.name", default="Generate Study Note"),
        description=tr(
            "entries.note_ai_generate.description",
            default="Summarize a session, topic, OCR text, or supplied text into a note draft without saving it.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source_type": {"type": "string", "default": "manual"},
                "source_ref": {"type": "string", "default": ""},
                "source_text": {"type": "string", "default": ""},
                "topic_ids": {"type": "array", "items": {"type": "string"}, "default": []},
            },
        },
        timeout=75.0,
        llm_result_fields=["title", "content", "source_type", "source_ref", "topic_ids"],
    )
    async def study_note_ai_generate(
        self,
        source_type: str = "manual",
        source_ref: str = "",
        source_text: str = "",
        topic_ids: list[str] | None = None,
        **_,
    ):
        try:
            if self._agent is None:
                return Err(SdkError("study tutor agent is not initialized"))
            normalized_source_type = str(source_type or "manual").strip() or "manual"
            normalized_source_ref = str(source_ref or "").strip()
            text = str(source_text or "").strip()
            if not text and normalized_source_type == "session":
                text = await asyncio.to_thread(
                    self._notebook_store.source_text_for_session, normalized_source_ref
                )
            elif not text and normalized_source_type == "topic":
                text = await asyncio.to_thread(
                    self._notebook_store.source_text_for_topic, normalized_source_ref
                )
                if not topic_ids and normalized_source_ref:
                    topic_ids = [normalized_source_ref]
            elif not text and normalized_source_type == "ocr":
                async with self._lock:
                    text = self._state.last_ocr_text
            if not text.strip():
                return Err(SdkError("study note source text is required", code="MISSING_TEXT"))
            reply = await self._agent.summarize_to_note(
                text,
                source_type=normalized_source_type,
                source_ref=normalized_source_ref,
            )
            return Ok(
                {
                    "title": str(reply.payload.get("title") or "Study Note"),
                    "content": reply.reply,
                    "source_type": normalized_source_type,
                    "source_ref": normalized_source_ref,
                    "topic_ids": list(topic_ids or []),
                    "is_ai_generated": True,
                    "degraded": reply.degraded,
                    "diagnostic": reply.diagnostic,
                }
            )
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_note_ai_generate")

    @plugin_entry(
        id="study_note_highlight_action",
        name=tr("entries.note_highlight_action.name", default="Study Note Highlight Action"),
        description=tr(
            "entries.note_highlight_action.description",
            default="Run an action from selected note text: generate a question, create a memory card, or view linked topics.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["generate_question", "create_memory_card", "view_topic"]},
                "selected_text": {"type": "string", "default": ""},
                "note_id": {"type": "string", "default": ""},
                "topic_id": {"type": "string", "default": ""},
            },
            "required": ["action"],
        },
        timeout=60.0,
        llm_result_fields=["action", "question", "card", "topic_id", "notes"],
    )
    async def study_note_highlight_action(
        self,
        action: str,
        selected_text: str = "",
        note_id: str = "",
        topic_id: str = "",
        **_,
    ):
        try:
            normalized_action = str(action or "").strip()
            text = str(selected_text or "").strip()
            if normalized_action == "generate_question":
                if self._agent is None:
                    return Err(SdkError("study tutor agent is not initialized"))
                if not text:
                    return Err(SdkError("selected_text is required", code="MISSING_TEXT"))
                async with self._lock:
                    active_mode = self._state.active_mode
                reply = await self._agent.question_generate(
                    text,
                    mode=active_mode,
                    context={"source": "notebook_highlight", "source_text": text},
                )
                return Ok(
                    {
                        "action": normalized_action,
                        "question": reply.payload,
                        "degraded": reply.degraded,
                        "diagnostic": reply.diagnostic,
                    }
                )
            if normalized_action == "create_memory_card":
                if not text:
                    return Err(SdkError("selected_text is required", code="MISSING_TEXT"))
                deck = await asyncio.to_thread(
                    self._memory_deck_store.get_or_create_default_deck,
                    deck_type="custom",
                )
                result = await asyncio.to_thread(
                    self._memory_deck_store.upsert_item,
                    deck_id=deck["id"],
                    item_type="custom",
                    prompt=text[:160],
                    answer=text,
                    metadata={
                        "source": "study_notebook",
                        "note_id": str(note_id or ""),
                    },
                )
                item = result.get("item") if isinstance(result, dict) else {}
                return Ok(
                    {
                        "action": normalized_action,
                        "created": bool(result.get("created"))
                        if isinstance(result, dict)
                        else False,
                        "card": self._memory_deck_store.compat_card_payload(item or {}),
                    }
                )
            if normalized_action == "view_topic":
                resolved_topic = str(topic_id or "").strip()
                if not resolved_topic and note_id:
                    note = await asyncio.to_thread(self._notebook_store.get_note, note_id)
                    if note is not None and note.topic_ids:
                        resolved_topic = note.topic_ids[0]
                notes = (
                    await asyncio.to_thread(
                        self._notebook_store.get_notes_by_topic,
                        resolved_topic,
                        limit=20,
                    )
                    if resolved_topic
                    else []
                )
                return Ok(
                    {
                        "action": normalized_action,
                        "topic_id": resolved_topic,
                        "notes": [_note_dict(note) for note in notes],
                    }
                )
            return Err(SdkError("unsupported highlight action", code="INVALID_ACTION"))
        except Exception as exc:
            return _entry_exception_error(
                self, exc, operation="study_note_highlight_action"
            )
